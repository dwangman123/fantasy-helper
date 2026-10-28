"""Waiver rankings for one team from the stored snapshot; `fh-waivers` prints them.

Stream ranks free agents by the change in this week's win odds against the actual
opponent; hold ranks them by the change in expected categories won per week against
a league-average opponent. The method is in `analytics/waivers.py`. Each report
carries the assumptions behind it so the UI can state them.
"""

import argparse
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from fantasy_helper.analytics.waivers import HoldRanking, StreamRanking, rank_hold, rank_stream
from fantasy_helper.config import get_settings
from fantasy_helper.context import LeagueContext, load_context
from fantasy_helper.db import queries as q
from fantasy_helper.db.session import get_sessionmaker
from fantasy_helper.domain import Matchup
from fantasy_helper.predict import N_SIMS

N_DROPS = 3  # my lowest-priority players considered as drops
MAX_ADDS = 25  # free agents fully evaluated per ranking, after the screen


@dataclass
class WaiverReport:
    season: int
    matchup_period: int
    team_id: int
    opponent_id: int
    side: str  # "home" / "away": my side of the matchup
    days_left: list[int]
    first_day: int  # first scoring period a pickup can play
    drop_ids: list[int | None]  # None: an open roster spot
    free_agents: int  # size of the stored free-agent pool
    stream: StreamRanking
    hold: HoldRanking
    hold_periods: list[int]  # matchup periods the hold ranking covers
    games_this_week: dict[int, int]  # free agent -> games he can play this week
    hold_games: dict[int, int]  # free agent -> games over the hold weeks
    n_sims: int
    assumptions: list[str] = field(default_factory=list)


def current_matchup(session: Session, ctx: LeagueContext, team_id: int) -> Matchup:
    for mu in q.load_matchups(session, ctx.season, ctx.matchup_period):
        if team_id in (mu.home_team_id, mu.away_team_id) and mu.away_team_id is not None:
            return mu
    raise LookupError(f"Team {team_id} has no opponent in matchup period {ctx.matchup_period}")


def default_drops(ctx: LeagueContext, team_id: int, n: int = N_DROPS) -> list[int | None]:
    """My n lowest-priority players, plus None (add without dropping) if a roster spot is open."""
    entries = ctx.team_entries(team_id)
    ranked = sorted((r.player_id for r in entries if r.player_id in ctx.z), key=lambda pid: ctx.z[pid].total)
    return ([None] if len(entries) < ctx.roster_spots else []) + ranked[:n]


def waiver_report(
    session: Session,
    season: int,
    team_id: int,
    same_day: bool = False,
    drop_ids: list[int | None] | None = None,
    max_adds: int = MAX_ADDS,
    n_sims: int = N_SIMS,
    seed: int = 0,
    ctx: LeagueContext | None = None,
) -> WaiverReport:
    """`same_day`: a pickup can play today rather than from tomorrow. `drop_ids` overrides
    the default drops; each must be one of my players outside the IR slot."""
    ctx = ctx or load_context(session, season)
    mu = current_matchup(session, ctx, team_id)
    side = "home" if mu.home_team_id == team_id else "away"
    opponent_id = mu.away_team_id if side == "home" else mu.home_team_id
    drops = default_drops(ctx, team_id) if drop_ids is None else drop_ids
    mine = {r.player_id for r in ctx.team_entries(team_id) if r.player_id in ctx.rates}
    if not drops or any(d is not None and d not in mine for d in drops):
        raise ValueError(f"Drops must be players on team {team_id} outside the IR slot")

    days = ctx.days_left
    first_day = ctx.scoring_period if same_day else ctx.scoring_period + 1
    fa_ids = ctx.free_agent_ids()
    z = {pid: pz.by_cat for pid, pz in ctx.z.items()}
    stream = rank_stream(
        ctx.categories,
        {"home": ctx.actual(mu.home_team_id), "away": ctx.actual(mu.away_team_id)},
        {"home": ctx.team_sim_players(mu.home_team_id, days), "away": ctx.team_sim_players(mu.away_team_id, days)},
        side,
        days,
        first_day,
        ctx.slot_counts,
        [ctx.sim_player(pid, days) for pid in fa_ids],
        drops,
        z,
        limit=max_adds,
        n_sims=n_sims,
        seed=seed + mu.espn_id,  # the same random numbers as this matchup's prediction
        dispersion=ctx.dispersion,
    )

    later = ctx.later_weeks
    hold_days = [d for w in later for d in w.scoring_periods]
    hold = rank_hold(
        ctx.categories,
        ctx.team_sim_players(team_id, hold_days, healthy=True),
        [ctx.team_sim_players(t, hold_days, healthy=True) for t in sorted({r.team_id for r in ctx.rosters})],
        [list(w.scoring_periods) for w in later],
        ctx.slot_counts,
        [ctx.sim_player(pid, hold_days, healthy=True) for pid in fa_ids],
        drops,
        z,
        limit=max_adds,
        dispersion=ctx.dispersion,
    )

    playable = [d for d in days if d >= first_day]
    return WaiverReport(
        season=season,
        matchup_period=ctx.matchup_period,
        team_id=team_id,
        opponent_id=opponent_id,
        side=side,
        days_left=days,
        first_day=first_day,
        drop_ids=drops,
        free_agents=len(fa_ids),
        stream=stream,
        hold=hold,
        hold_periods=[w.matchup_period for w in later],
        games_this_week={pid: len(ctx.sim_player(pid, playable).games) for pid in fa_ids},
        hold_games={pid: len(ctx.sim_player(pid, hold_days).games) for pid in fa_ids},
        n_sims=n_sims,
        assumptions=_assumptions(ctx, first_day, same_day),
    )


def _day(ctx: LeagueContext, scoring_period: int) -> str:
    d = ctx.calendar.date_for(scoring_period)
    return f"{d:%a %b} {d.day}"


def _assumptions(ctx: LeagueContext, first_day: int, same_day: bool) -> list[str]:
    when = "today" if same_day else f"{_day(ctx, first_day)}, the day after the move"
    return [
        f"A pickup plays from {when}; the dropped player keeps any games before then. "
        "When ESPN lets a pickup play in this league isn't verified yet.",
        "The opponent's roster stays as it is (they may stream too).",
        "Each line is one add/drop, not a chain of moves through the week.",
        "The league's add limit isn't checked yet (its setting is unclear), so make sure you have an add left.",
        "The free-agent pool may include players still on waivers, who can't be added until they clear.",
        "Hold compares against a league-average team over the remaining weeks, with everyone healthy.",
    ]


def _name(players: dict, pid: int | None) -> str:
    if pid is None:
        return "(open spot)"
    p = players[pid]
    injury = f" {p.injury_status}" if p.injury_status not in (None, "ACTIVE") else ""
    return f"{p.name} ({p.pro_team} {p.position}{injury})"


def _format(report: WaiverReport, ctx: LeagueContext, teams: dict, top: int) -> str:
    base, opp = report.stream.baseline, teams[report.opponent_id]
    lines = [
        f"Week {report.matchup_period} vs {opp.name} ({opp.abbrev.strip()}): win {base.p_win:.1%} | "
        f"tie {base.p_tie:.1%} | loss {base.p_loss:.1%}, expected {base.expected_cats:.1f} categories",
        f"{len(report.days_left)} days left; pickups play from {_day(ctx, report.first_day)}",
        "Board: " + " | ".join(f"{k} {label} {base.cat_p_win[k]:.0%}" for k, label in report.stream.board.items()),
        "",
        f"Stream (this week; {report.stream.screened} of {report.free_agents} free agents simulated)",
        f"  {'':<4}{'add':<36}{'drop':<36}{'games':>6}{'+starts':>8}{'P(win)':>8}{'cats':>7}  flips",
    ]
    for i, o in enumerate(report.stream.options[:top], 1):
        flips = " ".join([f"+{k}" for k in o.gained] + [f"-{k}" for k in o.lost])
        lines.append(
            f"  {i:>2}. {_name(ctx.players, o.add_id):<36}{_name(ctx.players, o.drop_id):<36}"
            f"{report.games_this_week[o.add_id]:>6}{o.extra_starts:>+8.1f}{o.d_p_win:>+8.1%}{o.d_expected_cats:>+7.2f}"
            f"  {flips}"
        )
    hold = report.hold
    lines += [
        "",
        f"Hold (next {hold.weeks} weeks vs a league-average team; baseline {hold.cats_per_week:.2f} categories/week)",
    ]
    for i, o in enumerate(hold.options[:top], 1):
        lines.append(
            f"  {i:>2}. {_name(ctx.players, o.add_id):<36}{_name(ctx.players, o.drop_id):<36}"
            f"{o.d_cats_per_week:>+7.2f} categories/week"
        )
    lines += ["", "Assumptions:", *(f"  - {a}" for a in report.assumptions)]
    return "\n".join(lines)


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Rank free agents for this week (stream) and the season (hold).")
    parser.add_argument("--team", type=int, default=settings.my_team_id)
    parser.add_argument("--today", action="store_true", help="pickups can play today, not from tomorrow")
    parser.add_argument("--sims", type=int, default=N_SIMS)
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()
    with get_sessionmaker()() as session:
        ctx = load_context(session, settings.season)
        report = waiver_report(session, settings.season, args.team, same_day=args.today, n_sims=args.sims, ctx=ctx)
        teams = {t.team_id: t for t in q.load_teams(session, settings.season)}
    print(_format(report, ctx, teams, args.top))


if __name__ == "__main__":
    main()
