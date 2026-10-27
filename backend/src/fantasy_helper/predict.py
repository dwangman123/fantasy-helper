"""Matchup predictions for the current week, built from the stored snapshot.

The ingest runs this after each snapshot and appends the results to
`matchup_predictions`; `fh-predict` runs it on demand and prints without storing.
Inputs per player (rates, games, opponent factors, chance of playing, start
priority) come from `context.load_context`.
"""

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from fantasy_helper.analytics.actuals import category_values
from fantasy_helper.analytics.matchup import MatchupPrediction, simulate_matchup
from fantasy_helper.config import get_settings
from fantasy_helper.context import LeagueContext, load_context
from fantasy_helper.db import models as m
from fantasy_helper.db import queries as q
from fantasy_helper.db.session import get_sessionmaker
from fantasy_helper.domain import Matchup

MODEL_VERSION = "2"  # 2: common random numbers (inverse-CDF team totals, per player-game availability)
N_SIMS = 5000


@dataclass
class WeekPredictions:
    season: int
    matchup_period: int
    days_left: list[int]  # scoring periods still to play
    week_days: list[int]
    results: list[tuple[Matchup, MatchupPrediction]]
    actuals: dict[int, dict[str, float]]  # fantasy team -> started totals so far, today included


def predict_matchup(ctx: LeagueContext, mu: Matchup, n_sims: int = N_SIMS, seed: int = 0) -> MatchupPrediction:
    """The seed is offset by the matchup id, so each matchup has its own random numbers
    and waiver swaps can reuse them."""
    days = ctx.days_left
    return simulate_matchup(
        ctx.categories,
        {"home": ctx.actual(mu.home_team_id), "away": ctx.actual(mu.away_team_id)},
        {"home": ctx.team_sim_players(mu.home_team_id, days), "away": ctx.team_sim_players(mu.away_team_id, days)},
        days,
        ctx.slot_counts,
        n_sims=n_sims,
        seed=seed + mu.espn_id,
        dispersion=ctx.dispersion,
    )


def predict_current_week(
    session: Session, season: int, n_sims: int = N_SIMS, seed: int = 0, ctx: LeagueContext | None = None
) -> WeekPredictions:
    ctx = ctx or load_context(session, season)
    results = [
        (mu, predict_matchup(ctx, mu, n_sims, seed))
        for mu in q.load_matchups(session, season, ctx.matchup_period)
        if mu.away_team_id is not None  # playoff bye
    ]
    return WeekPredictions(
        season, ctx.matchup_period, ctx.days_left, list(ctx.week.scoring_periods), results, ctx.actuals
    )


def store_predictions(
    session: Session, week: WeekPredictions, ingest_run_id: int | None, warnings: list[str] | None = None
) -> int:
    now = datetime.now(UTC)
    for mu, p in week.results:
        session.add(
            m.MatchupPrediction(
                ingest_run_id=ingest_run_id,
                created_at=now,
                season=week.season,
                espn_id=mu.espn_id,
                matchup_period=week.matchup_period,
                scoring_period=week.days_left[0] if week.days_left else week.week_days[-1] + 1,
                home_team_id=mu.home_team_id,
                away_team_id=mu.away_team_id,
                p_home=p.p_home,
                p_tie=p.p_tie,
                p_away=p.p_away,
                cat_probs=p.cat_probs,
                expected_score=p.expected_score,
                most_likely_score=list(p.most_likely_score),
                projected_totals=p.projected_totals,
                expected_games=p.expected_games,
                n_sims=p.n_sims,
                model_version=MODEL_VERSION,
                warnings=warnings or [],
            )
        )
    return len(week.results)


def _format(week: WeekPredictions, teams: dict, categories: list, focus_team: int) -> str:
    done = len(week.week_days) - len(week.days_left)
    lines = [f"Week {week.matchup_period}: {done} of {len(week.week_days)} days played, {len(week.days_left)} left", ""]
    for mu, p in week.results:
        home, away = teams[mu.home_team_id].abbrev.strip(), teams[mu.away_team_id].abbrev.strip()
        e = p.expected_score
        lines.append(
            f"  {home:>5} {p.p_home:6.1%}  tie {p.p_tie:5.1%}  {p.p_away:6.1%} {away:<5}"
            f"   expected {e['home']:.1f}-{e['away']:.1f}-{e['ties']:.1f}"
        )
    for mu, p in week.results:
        if focus_team not in (mu.home_team_id, mu.away_team_id):
            continue
        home, away = teams[mu.home_team_id], teams[mu.away_team_id]
        ha, aa = home.abbrev.strip(), away.abbrev.strip()
        hw, aw, t = p.most_likely_score
        now = {
            side: category_values(week.actuals.get(team_id, {}), categories)
            for side, team_id in (("home", mu.home_team_id), ("away", mu.away_team_id))
        }
        lines += [
            "",
            f"{home.name} ({ha}) vs {away.name} ({aa})",
            f"  Win: {ha} {p.p_home:.1%} | tie {p.p_tie:.1%} | {aa} {p.p_away:.1%}",
            f"  Expected score {p.expected_score['home']:.1f}-{p.expected_score['away']:.1f}"
            f" ({p.expected_score['ties']:.1f} tied); most likely {hw}-{aw}-{t}",
            f"  Starts left: {p.expected_games['home']:.1f} vs {p.expected_games['away']:.1f}",
            "",
            f"  {'cat':<5}{'now':>9}{'final':>9} {'P(win)':>7}{'tie':>6}{'P(win)':>8}{'final':>9}{'now':>9}",
        ]
        for cat in categories:
            fmt = "{:9.3f}" if cat.is_percentage else "{:9.0f}"
            now_h, now_a = now["home"][cat.key], now["away"][cat.key]
            cp = p.cat_probs[cat.key]
            lines.append(
                f"  {cat.key:<5}"
                + (fmt.format(now_h) if now_h is not None else f"{'-':>9}")
                + fmt.format(p.projected_totals["home"][cat.key])
                + f" {cp['home']:7.0%}{cp['tie']:6.0%}{cp['away']:8.0%}"
                + fmt.format(p.projected_totals["away"][cat.key])
                + (fmt.format(now_a) if now_a is not None else f"{'-':>9}")
            )
    return "\n".join(lines)


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Predict this week's matchups from the stored snapshot.")
    parser.add_argument("--team", type=int, default=settings.my_team_id, help="team id to show in detail")
    parser.add_argument("--sims", type=int, default=N_SIMS)
    args = parser.parse_args()
    with get_sessionmaker()() as session:
        week = predict_current_week(session, settings.season, n_sims=args.sims)
        teams = {t.team_id: t for t in q.load_teams(session, settings.season)}
        categories = q.load_categories(session, settings.season)
        last_run = session.scalars(select(m.IngestRun).order_by(m.IngestRun.id.desc()).limit(1)).first()
    print(_format(week, teams, categories, args.team))
    for w in (last_run.warnings or []) if last_run else []:
        print(f"\nWarning from ingest {last_run.id}: {w}")


if __name__ == "__main__":
    main()
