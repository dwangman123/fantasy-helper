"""Trade evaluation and the trade finder for one team, from the stored snapshot.

A deal is scored the way Hold scores a pickup (see `analytics/trades.py`): the change in
expected categories won per week against a league-average team, for both sides, over
the weeks after this one with everyone healthy. Each report carries the assumptions
behind it so the UI can state them.
"""

from dataclasses import dataclass, field

from fantasy_helper.analytics.trades import (
    MIN_GAIN,
    PER_SHAPE,
    Season,
    TradeResult,
    TradeSearch,
    evaluate_trade,
    find_trades,
    season,
)
from fantasy_helper.context import OUT_STATUSES, LeagueContext

MAX_PER_SIDE = 3
_STATUS = {"OUT": "out", "SUSPENSION": "suspended", "INJURY_RESERVE": "on injured reserve"}


def season_outlook(ctx: LeagueContext) -> Season:
    """Every team's remaining weeks as rostered now, and the healthy free agents that can
    fill a spot a deal opens."""
    weeks = [list(w.scoring_periods) for w in ctx.later_weeks]
    if not weeks:
        raise LookupError(f"No matchup weeks left after week {ctx.matchup_period}")
    days = [d for w in weeks for d in w]
    teams = sorted({r.team_id for r in ctx.rosters})
    pool = [ctx.sim_player(pid, days, healthy=True) for pid in ctx.free_agent_ids() if not _out(ctx, pid)]
    return season(
        ctx.categories,
        {t: ctx.team_sim_players(t, days, healthy=True) for t in teams},
        weeks,
        ctx.slot_counts,
        ctx.roster_spots,
        pool,
        ctx.dispersion,
    )


def _out(ctx: LeagueContext, player_id: int) -> bool:
    return ctx.players[player_id].injury_status in OUT_STATUSES


def _assumptions(ctx: LeagueContext) -> list[str]:
    later = ctx.later_weeks
    return [
        f"Scored over weeks {later[0].matchup_period}-{later[-1].matchup_period} (playoffs included) against a "
        "league-average team, with everyone healthy. This week is left out: when ESPN processes a trade in this "
        "league isn't verified.",
        "A team that gets more players than it sends drops its lowest-value players outside the deal; a team that "
        "sends more picks up its best free agents for the open spots.",
        "The league average is recomputed with both rosters after the deal.",
        "Win a week is the chance to win more categories than a league-average team, treating the nine categories "
        "as independent.",
        "The other manager may value players differently; a deal that helps both teams here can still be turned down.",
    ]


@dataclass
class TradeReport:
    team_id: int
    partner_id: int
    result: TradeResult
    warnings: list[str]
    assumptions: list[str] = field(default_factory=list)


def trade_report(
    ctx: LeagueContext, outlook: Season, team_id: int, partner_id: int, give: list[int], get: list[int]
) -> TradeReport:
    """`give`: my players going to the partner; `get`: the partner's players coming back."""
    if partner_id == team_id or partner_id not in outlook.rosters or team_id not in outlook.rosters:
        raise ValueError("A trade needs two different teams in the league")
    for t, ids in ((team_id, give), (partner_id, get)):
        roster = {p.player_id for p in outlook.rosters[t]}
        if not ids or len(ids) > MAX_PER_SIDE or len(set(ids)) < len(ids):
            raise ValueError(f"Each side sends 1 to {MAX_PER_SIDE} different players")
        if not set(ids) <= roster:
            raise ValueError(f"Players sent by team {t} must be on its roster, outside the IR slot")
    warnings = [
        f"{ctx.players[pid].name} is {_STATUS[ctx.players[pid].injury_status]}; the score assumes he plays every game."
        for pid in [*give, *get]
        if _out(ctx, pid)
    ]
    result = evaluate_trade(outlook, team_id, partner_id, give, get)
    return TradeReport(team_id, partner_id, result, warnings, _assumptions(ctx))


@dataclass
class TradeIdeas:
    team_id: int
    search: TradeSearch
    assumptions: list[str] = field(default_factory=list)


def trade_ideas(ctx: LeagueContext, outlook: Season, team_id: int, per_shape: int = PER_SHAPE) -> TradeIdeas:
    if team_id not in outlook.rosters:
        raise LookupError(f"No team {team_id} in season {ctx.season}")
    tradable = {p.player_id for r in outlook.rosters.values() for p in r if not _out(ctx, p.player_id)}
    search = find_trades(outlook, team_id, tradable, per_shape=per_shape)
    notes = [
        f"Searches every 1-for-1 and 2-for-1 deal, both ways, with every other team ({search.candidates:,} deals). "
        f"A quick estimate screens them and the {search.evaluated} most promising are scored in full, so a good "
        "deal can be missed. Players who are out, suspended or on IR are left out.",
        f"Listed deals help both teams by at least {MIN_GAIN:g} categories a week, best for you first; each "
        "player or pair you'd get shows its best package.",
    ]
    return TradeIdeas(team_id, search, notes + _assumptions(ctx))
