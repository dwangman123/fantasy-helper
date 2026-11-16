"""Trade evaluation and the trade finder for one team, from the stored snapshot.

A deal is scored the way Hold scores a pickup (see `analytics/trades.py`): the change in
expected categories won per week against a league-average team, for both sides, over
the weeks after this one, with injured players counted from ESPN's expected return
(`LeagueContext.return_period`). No player counts for less than a replacement-level
player, before or after the deal, and the deal's value balance shows how the other
manager would likely see it. Each report carries the assumptions behind it so the UI can state them.
"""

from dataclasses import dataclass, field

from fantasy_helper.analytics.matchup import SimPlayer
from fantasy_helper.analytics.trades import (
    LOPSIDED,
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


def _replacement(outlook: Season) -> str:
    r = outlook.replacement
    n = len(outlook.rosters)
    return f"the average of the top {n} free agents, G-score {r.priority:.1f}" if r else "no free agents listed"


def _assumptions(ctx: LeagueContext, outlook: Season) -> list[str]:
    later = ctx.later_weeks
    return [
        f"Scored over weeks {later[0].matchup_period}-{later[-1].matchup_period} (playoffs included) against a "
        "league-average team. This week is left out: when ESPN processes a trade in this league isn't verified.",
        injury_assumption(ctx),
        f"No player counts for less than a replacement-level player, one a team could find on waivers "
        f"({_replacement(outlook)}). A player below that G-score who'd also add less to his team's categories "
        "counts as replacement level, before and after the deal, and so does such a player received, judged for "
        "the team that gets him; a roster spot the deal opens holds a replacement-level player. So dumping a "
        "player a team could drop anyway gains it nothing, and taking one on costs it nothing. The changes are "
        "measured that way; 'now' is each team as rostered.",
        "A team that gets more players than it sends drops its lowest-value players, ones it just got included.",
        "The league average is recomputed with both rosters after the deal.",
        "Win a week is the chance to win more categories than a league-average team, treating the nine categories "
        "as independent.",
        "Value is how a manager going by player values would likely see a deal: each player's G-score above "
        f"replacement level ({_replacement(outlook)}), and 0 for a player below it, who could be dropped for "
        "nothing. A negative-G throw-in adds nothing to a deal.",
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
    for who, whom, side in (("You", "you", result.me), ("They", "them", result.partner)):
        for pid in side.receives:
            name = ctx.players[pid].name
            if pid in side.drops:
                warnings.append(f"{who}'d drop {name} to make room, so it's the same as the deal without him.")
            elif pid in side.below_replacement:
                warnings.append(
                    f"{name} is below replacement level for {whom}, so he counts as a replacement-level player: "
                    "the deal is worth the same without him."
                )
    if result.lopsided:
        warnings.append(
            f"They give up {-result.partner.d_value:.1f} more value than they get (G-score above replacement level, "
            f"{_replacement(outlook)}); a manager going by player values would likely say no."
        )
    return TradeReport(team_id, partner_id, result, warnings, _assumptions(ctx, outlook))


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
        f"Listed deals help you by at least {MIN_GAIN:g} categories a week and don't hurt the other team, best for "
        "you first; each player or pair you'd get shows its best package. A deal in which a team would get a player "
        "below replacement level, or drop one it just got, is left out: it's the same as the deal without him.",
        f"⚠ marks a deal in which the other team gives up over {LOPSIDED:.1f} more value than it gets; the model "
        "says it doesn't hurt them, but a manager going by player values would likely say no.",
    ]
    return TradeIdeas(team_id, search, notes + _assumptions(ctx, outlook))
