"""Matchup predictions for the current week, built from the stored snapshot.

The ingest runs this after each snapshot and appends the results to
`matchup_predictions`; `fh-predict` runs it on demand and prints without storing.

Per player: blended per-game rates (`rates.blend_rates`), games on the days left
in the week (`pro_games`), each adjusted for the NBA opponent
(`opponent.opponent_factors`), a chance of playing from ESPN's injury status, and a
start priority from blended z-scores. Players in the IR slot are left out.
Game-to-game spread comes from the box-score history (`variance.fit_dispersion`),
falling back to Poisson until there are enough logged games.
"""

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from fantasy_helper.analytics.matchup import MatchupPrediction, SimPlayer, simulate_matchup
from fantasy_helper.analytics.opponent import opponent_factors
from fantasy_helper.analytics.rates import blend_rates
from fantasy_helper.analytics.variance import fit_dispersion
from fantasy_helper.analytics.zscores import player_zscores
from fantasy_helper.calendar import load_calendar
from fantasy_helper.config import get_settings
from fantasy_helper.db import models as m
from fantasy_helper.db import queries as q
from fantasy_helper.db.session import get_sessionmaker
from fantasy_helper.domain import STAT_KEYS, Matchup

MODEL_VERSION = "1"
N_SIMS = 5000
OUT_STATUSES = {"OUT", "SUSPENSION", "INJURY_RESERVE"}
DAY_TO_DAY_P_PLAY = 0.5


def p_play(injury_status: str | None) -> float:
    if injury_status in OUT_STATUSES:
        return 0.0
    if injury_status == "DAY_TO_DAY":
        return DAY_TO_DAY_P_PLAY
    return 1.0


@dataclass
class WeekPredictions:
    season: int
    matchup_period: int
    days_left: list[int]  # scoring periods still to play
    week_days: list[int]
    results: list[tuple[Matchup, MatchupPrediction]]


def predict_current_week(session: Session, season: int, n_sims: int = N_SIMS, seed: int = 0) -> WeekPredictions:
    league = session.get(m.League, season)
    if league is None or not league.lineup_slot_counts:
        raise LookupError(f"No league settings stored for season {season}; run fh-ingest first")
    categories = q.load_categories(session, season)
    week = load_calendar(season).week(league.current_matchup_period)
    days = [sp for sp in week.scoring_periods if sp >= league.current_scoring_period]

    rosters = q.load_rosters(session, season)
    rostered_ids = {r.player_id for r in rosters}
    players = q.load_players(session, rostered_ids)
    lines = blend_rates(q.load_stat_lines(session, season, "season"), q.load_stat_lines(session, season, "projected"))
    rates = {s.player_id: s.per_game for s in lines}
    z = player_zscores(lines, categories, baseline_ids=rostered_ids)
    schedule = q.load_schedule(session, season, days)
    factors = opponent_factors(q.load_opponent_stats(session, season))
    dispersion = fit_dispersion(q.load_game_stats(session, season), STAT_KEYS)

    def sim_players(team_id: int) -> list[SimPlayer]:
        out = []
        for r in rosters:
            if r.team_id != team_id or r.lineup_slot == "IR" or r.player_id not in rates:
                continue
            p = players[r.player_id]
            out.append(
                SimPlayer(
                    player_id=r.player_id,
                    eligible_slots=p.eligible_slots,
                    per_game=rates[r.player_id],
                    games={d: factors.get(opp, {}) for d, opp in schedule.get(p.pro_team, {}).items()},
                    p_play=p_play(p.injury_status),
                    priority=z[r.player_id].total,
                )
            )
        return out

    results = []
    for mu in q.load_matchups(session, season, league.current_matchup_period):
        if mu.away_team_id is None:  # playoff bye
            continue
        prediction = simulate_matchup(
            categories,
            {"home": mu.home_cats, "away": mu.away_cats},
            {"home": sim_players(mu.home_team_id), "away": sim_players(mu.away_team_id)},
            days,
            league.lineup_slot_counts,
            n_sims=n_sims,
            seed=seed + mu.espn_id,
            dispersion=dispersion,
        )
        results.append((mu, prediction))
    return WeekPredictions(season, league.current_matchup_period, days, list(week.scoring_periods), results)


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
            now_h, now_a = mu.home_cats.get(cat.key), mu.away_cats.get(cat.key)
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
