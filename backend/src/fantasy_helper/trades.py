"""Trade evaluation and the trade finder for one team, from the stored snapshot.

A deal is scored the way Hold scores a pickup (see `analytics/trades.py`): the change in
expected categories won per week against a league-average team, for both sides, over
the weeks after this one, with injured players counted from ESPN's expected return
(`LeagueContext.return_period`). Each report carries the assumptions behind it so the UI
can state them.
"""

from dataclasses import dataclass, field

from fantasy_helper.analytics.matchup import SimPlayer
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
from fantasy_helper.context import OUT_STATUSES, LeagueContext, injury_assumption

MAX_PER_SIDE = 3
_STATUS = {"OUT": "out", "SUSPENSION": "suspended", "INJURY_RESERVE": "on injured reserve"}


def season_outlook(ctx: LeagueContext) -> Season:
    """Every team's remaining weeks as rostered now, and the free agents that can fill a spot
    a deal opens (not those out for the season or hurt with no listed return)."""
    weeks = [list(w.scoring_periods) for w in ctx.later_weeks]
    if not weeks:
        raise LookupError(f"No matchup weeks left after week {ctx.matchup_period}")
    days = [d for w in weeks for d in w]
    teams = sorted({r.team_id for r in ctx.rosters})
    free_agents = (ctx.sim_player(pid, days, rest_of_season=True) for pid in ctx.free_agent_ids())
    pool = [p for p in free_agents if not _unvalued(ctx, p)]
    return season(
        ctx.categories,
        {t: ctx.team_sim_players(t, days, rest_of_season=True) for t in teams},
        weeks,
        ctx.slot_counts,
        ctx.roster_spots,
        pool,
        ctx.dispersion,
    )


def _out(ctx: LeagueContext, player_id: int) -> bool:
    return ctx.players[player_id].injury_status in OUT_STATUSES


def _unvalued(ctx: LeagueContext, p: SimPlayer) -> bool:
    """Not worth getting in a deal or a pickup: no games left (out for the season), or out
    with no listed return, so his value can't be told."""
    return not p.games or bool(ctx.unknown_returns([p.player_id]))


def _warning(ctx: LeagueContext, player_id: int) -> str:
    p = ctx.players[player_id]
    back, injury = ctx.return_period(player_id), ctx.injury(player_id)
    if back is None:
        status = _STATUS[p.injury_status]
        return f"{p.name} is {status} with no return date listed; the score counts every game after this week."
    if injury is not None and injury.return_date is not None and back <= ctx.calendar.weeks[-1].last_scoring_period:
        d = injury.return_date
        return f"{p.name} is out until {d:%b} {d.day} (ESPN's estimate); the score counts his games from then."
    return f"{p.name} is out for the season; the score counts no games for him."


def _assumptions(ctx: LeagueContext) -> list[str]:
    later = ctx.later_weeks
    return [
        f"Scored over weeks {later[0].matchup_period}-{later[-1].matchup_period} (playoffs included) against a "
        "league-average team. This week is left out: when ESPN processes a trade in this league isn't verified.",
        injury_assumption(ctx),
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
        roster = {p.player_id for p in outlook.rosters[t] if p.ir_back is None}
        if not ids or len(ids) > MAX_PER_SIDE or len(set(ids)) < len(ids):
            raise ValueError(f"Each side sends 1 to {MAX_PER_SIDE} different players")
        if not set(ids) <= roster:
            raise ValueError(f"Players sent by team {t} must be on its roster, outside the IR slot")
    warnings = [_warning(ctx, pid) for pid in [*give, *get] if _out(ctx, pid)]
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
    tradable = {p.player_id for r in outlook.rosters.values() for p in r if p.ir_back is None and not _unvalued(ctx, p)}
    search = find_trades(outlook, team_id, tradable, per_shape=per_shape)
    notes = [
        f"Searches every 1-for-1 and 2-for-1 deal, both ways, with every other team ({search.candidates:,} deals). "
        f"A quick estimate screens them and the {search.evaluated} most promising are scored in full, so a good "
        "deal can be missed. Players in the IR slot, out for the season or hurt with no return date listed are "
        "left out.",
        f"Listed deals help both teams by at least {MIN_GAIN:g} categories a week, best for you first; each "
        "player or pair you'd get shows its best package.",
    ]
    return TradeIdeas(team_id, search, notes + _assumptions(ctx))
