"""Trades: what a deal does for both teams over the rest of the season, and a finder
for deals that help both.

Measure. Hold's (`waivers.py`), for each side: the change in expected categories
won per week against a league-average team over the remaining weeks, with each
week's expected totals from the daily lineup cap over the NBA schedule (an injured
player has no games before his expected return, so the bench covers them). Also the
change in P(win the week), from the per-category odds with the categories treated as
independent (`week_odds`). The league average is recomputed with both rosters after
the deal.

Replacement floor. A player is never worth less to a team than a replacement-level
player, one it could find on waivers: the average of the top free agents by G-score,
one per team, as a single player with their combined schedule (`replacement_player`).
A rostered player below that G-score who'd also add less to his team's categories, to
first order, counts as a replacement-level player, before and after the deal alike, and
so does such a player received, judged for the team that gets him. The G-score test
keeps a star from counting down on a team far ahead in his categories, where the first-
order value of anything it already has plenty of is near zero. A roster spot the deal opens
holds a replacement-level player. So shedding a player below replacement gains a team
nothing and taking one on costs it nothing; without the floor, negative-G throw-ins
made lopsided deals look good for both teams. No named free agent is assumed picked up.

Rosters stay legal. A side pushed over the roster limit drops its lowest-priority
players, ones it just got included.

Value. How the other manager would likely see a deal: each player's G-score above
replacement level, and 0 below it, summed over the players a side gets minus those it
sends. A negative-G throw-in adds nothing, as a plain sum would have it subtract. A
deal is flagged when the partner gives up more than `LOPSIDED` of it.

Finder. Every 1-for-1 and 2-for-1 deal, both ways, with every other team is
screened by a first-order estimate of each side's change in categories won per
week: a player's expected weekly stats (rate x games, no lineup cap) times the
slope of the team's P(win) in each category at its current totals, and never less
than replacement level's. The slope is steepest where a team is close to the league
average, so help in a category it punts or has locked counts for little. With each
partner, the deals of each shape whose smaller estimated gain is largest are
evaluated in full. Per partner, so every team gets a look; per shape, because
ignoring the lineup cap overrates 2-for-1s and would crowd out the 1-for-1s; by the
smaller gain, because the estimate tracks the full score but not closely enough to
trust a deal that only looks good for one side. A deal is kept when it gains me at
least `MIN_GAIN` categories a week (a smaller gain is too small to count on) and
costs the partner nothing, and each set of players I'd get keeps only its best
package. A deal in which a side gets a player below replacement, or would drop one it
gets, is left out: it's the smaller deal plus a throw-in, and that one is screened on
its own.
"""

from dataclasses import dataclass, replace
from itertools import combinations

import numpy as np

from fantasy_helper.analytics.matchup import SimPlayer
from fantasy_helper.analytics.waivers import (
    cat_slopes,
    day_totals,
    recount_from,
    weekly_cat_p_win,
    weekly_stats,
    weekly_totals,
)
from fantasy_helper.analytics.zscores import pct
from fantasy_helper.categories import Category
from fantasy_helper.domain import STAT_KEYS

PER_SHAPE = 15  # deals per partner and shape (1-for-1, 2-for-1, 1-for-2) evaluated in full
MIN_GAIN = 0.05  # categories won per week a deal must gain me to be listed
PARTNER_MIN_GAIN = 0.0  # ...and the partner's: it mustn't hurt them
LOPSIDED = 1.0  # value (G above replacement level) the partner may give up before a deal is flagged
REPLACEMENT_ID = -1  # the replacement-level player; his stand-ins on a roster get ids below -1
_OPEN_SPOT = -(10**12)  # stand-ins for roster spots a deal opens: _OPEN_SPOT - i
_COL = {k: i for i, k in enumerate(STAT_KEYS)}


def week_odds(cat_p_win: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """P(win) and P(tie) of each week's matchup from its per-category P(win), treating the
    categories as independent and each one as won or lost."""
    probs = list(cat_p_win.values())
    n = len(probs)
    dist = np.zeros((len(probs[0]) if probs else 0, n + 1))
    dist[:, 0] = 1.0
    for p in probs:  # dist[w, j]: P(j categories won so far in week w)
        nxt = dist * (1 - p)[:, None]
        nxt[:, 1:] += dist[:, :-1] * p[:, None]
        dist = nxt
    won = 2 * np.arange(n + 1)
    return dist[:, won > n].sum(axis=1), dist[:, won == n].sum(axis=1)


def replacement_player(free_agents: list[SimPlayer], n: int) -> SimPlayer | None:
    """The average of the top `n` free agents by G-score (`priority`) as one player: their mean
    per-game rates and G-score, and on each day the share of them who play, so his expected
    line that day is theirs averaged. None if there are no free agents."""
    pool = sorted(free_agents, key=lambda p: -p.priority)[:n]
    if not pool:
        return None
    rates = {k: float(np.mean([p.per_game[k] for p in pool])) for k in STAT_KEYS}
    games = {}
    for day in sorted({d for p in pool for d in p.games}):
        line = {
            k: sum(p.per_game[k] * p.games[day].get(k, 1.0) for p in pool if day in p.games) / len(pool)
            for k in STAT_KEYS
        }
        games[day] = {k: line[k] / rates[k] if rates[k] else 0.0 for k in STAT_KEYS}
    slots = sorted({s for p in pool for s in p.eligible_slots})
    return SimPlayer(REPLACEMENT_ID, slots, rates, games, 1.0, float(np.mean([p.priority for p in pool])))


def _stand_in(s: "Season", player_id: int) -> SimPlayer:
    """A replacement-level player in a roster spot: for a player counted at replacement level
    (id -player_id), or for an open spot (ids from `_OPEN_SPOT`)."""
    return replace(s.replacement, player_id=-player_id)


@dataclass
class Season:
    """Every team's expected weeks over the rest of the season, before any trade, as rostered
    and with players below replacement level counted at it (`floored`)."""

    categories: list[Category]
    weeks: list[list[int]]  # the scoring periods in each remaining week
    slot_counts: dict[str, int]
    roster_spots: int  # IR excluded
    dispersion: dict[str, float]
    rosters: dict[int, list[SimPlayer]]
    weekly: dict[int, np.ndarray]  # team -> weeks x STAT_KEYS, as rostered
    average: np.ndarray  # the league-average team's weeks, as rostered
    cat_p_win: dict[int, dict[str, np.ndarray]]  # team -> category -> P(win) each week vs the average
    values: dict[int, dict[int, float]]  # team -> player -> first-order categories won per week
    replacement: SimPlayer | None  # the replacement-level player; None without free agents
    by_id: dict[int, SimPlayer]  # rostered players
    below: dict[int, set[int]]  # team -> its players counted at replacement level
    floored: dict[int, list[SimPlayer]]  # team -> roster with those players' stand-ins
    floored_days: dict[int, dict[int, np.ndarray]]  # team -> day -> expected starters' totals, floored
    floored_weekly: dict[int, np.ndarray]
    floored_average: np.ndarray
    floored_cat_p_win: dict[int, dict[str, np.ndarray]]

    def player(self, player_id: int) -> SimPlayer:
        return self.by_id[player_id]

    def below_replacement(self, team_id: int, player_id: int) -> bool:
        """Whether a player counts only as replacement level for this team: his G-score is below
        it, and he'd add less to the team's categories than a replacement-level player, to first
        order (`values`). Both, so a team far ahead in a star's categories doesn't count him down,
        and one that needs a specialist with a low G-score doesn't count him down either."""
        r = self.replacement
        if r is None or self.player(player_id).priority >= r.priority:
            return False
        v = self.values[team_id]
        return v[player_id] < v[REPLACEMENT_ID]

    def value(self, player_ids: list[int]) -> float:
        """How a manager would likely value these players: each one's G-score above replacement
        level, 0 for one below it (he could be dropped for nothing)."""
        floor = self.replacement.priority if self.replacement else min(p.priority for p in self.by_id.values())
        return sum(max(self.player(pid).priority - floor, 0.0) for pid in player_ids)


def season(
    categories: list[Category],
    rosters: dict[int, list[SimPlayer]],
    weeks: list[list[int]],
    slot_counts: dict[str, int],
    roster_spots: int,
    free_agents: list[SimPlayer],
    dispersion: dict[str, float] | None = None,
) -> Season:
    """`rosters` and `free_agents` carry their games over `weeks`; the top free agents, one
    per team, set replacement level."""
    dispersion = dispersion or {}
    days = sorted({d for w in weeks for d in w})

    def weeks_of(players: list[SimPlayer]) -> tuple[dict[int, np.ndarray], np.ndarray]:
        by_day = {d: day_totals(players, d, slot_counts) for d in days}
        return by_day, weekly_totals(by_day, weeks)

    weekly = {t: weeks_of(players)[1] for t, players in rosters.items()}
    average = np.mean(list(weekly.values()), axis=0)
    repl = replacement_player(free_agents, len(rosters))
    everyone = [*(p for r in rosters.values() for p in r), *([repl] if repl else [])]
    stats = {p.player_id: weekly_stats(p, weeks) for p in everyone}
    n = max(len(weeks), 1)
    values = {}
    for t in rosters:
        slopes = cat_slopes(weekly[t], average, categories, dispersion)
        values[t] = {pid: float((slopes * x).sum()) / n for pid, x in stats.items()}
    s = Season(
        categories,
        weeks,
        slot_counts,
        roster_spots,
        dispersion,
        rosters,
        weekly,
        average,
        {t: weekly_cat_p_win(weekly[t], average, categories, dispersion) for t in rosters},
        values,
        repl,
        {p.player_id: p for r in rosters.values() for p in r},
        {},
        {},
        {},
        {},
        average,
        {},
    )
    for t, players in rosters.items():
        # A player in the IR slot holds no roster spot now, so he isn't counted down.
        s.below[t] = {p.player_id for p in players if p.ir_back is None and s.below_replacement(t, p.player_id)}
        s.floored[t] = [_stand_in(s, p.player_id) if p.player_id in s.below[t] else p for p in players]
        s.floored_days[t], s.floored_weekly[t] = weeks_of(s.floored[t])
    s.floored_average = np.mean(list(s.floored_weekly.values()), axis=0)
    s.floored_cat_p_win = {
        t: weekly_cat_p_win(s.floored_weekly[t], s.floored_average, categories, dispersion) for t in rosters
    }
    return s


def _new_weekly(s: Season, team_id: int, out_ids: set[int], incoming: list[SimPlayer]) -> np.ndarray:
    """A team's floored weeks with `out_ids` gone and `incoming` added; only days they play
    are recomputed."""
    base_roster, base = s.floored[team_id], s.floored_days[team_id]
    leaving = [p for p in base_roster if p.player_id in out_ids]
    roster = [p for p in base_roster if p.player_id not in out_ids] + incoming
    changed = {d for p in [*leaving, *incoming] for d in p.games}
    if (first := recount_from(roster)) is not None:
        changed |= {d for d in base if d >= first}
    return weekly_totals(
        {d: day_totals(roster, d, s.slot_counts) if d in changed else t for d, t in base.items()}, s.weeks
    )


def roster_moves(s: Season, team_id: int, receives: list[int], sends: set[int]) -> tuple[list[SimPlayer], int]:
    """Who a side drops to stay within the roster limit after getting `receives` for `sends`
    (its lowest-priority players, ones it just got included), and how many roster spots the
    deal opens. A player in the IR slot holds no spot now and can't be dropped; when he's
    back, `waivers.on_roster` makes room."""
    kept = [p for p in s.rosters[team_id] if p.player_id not in sends and p.ir_back is None]
    over = len(kept) + len(receives) - s.roster_spots
    drops = sorted([*kept, *(s.player(pid) for pid in receives)], key=lambda p: p.priority)[: max(over, 0)]
    opened = min(len(sends) - len(receives), s.roster_spots - len(kept) - len(receives))
    return drops, max(opened, 0) if s.replacement else 0


def _after(s: Season, team_id: int, sends: list[int], receives: list[int]) -> tuple[np.ndarray, list[int], int]:
    """A side's floored weeks after the deal, who it drops and how many spots it opens. Its own
    players below replacement are already stand-ins; a player it gets who'd be below
    replacement for it comes in as one, and so does each spot the deal opens."""
    drops, opened = roster_moves(s, team_id, receives, set(sends))
    dropped = {p.player_id for p in drops}
    own = s.below[team_id]
    out_ids = {-pid if pid in own else pid for pid in [*sends, *(dropped - set(receives))]}
    incoming = [
        _stand_in(s, pid) if s.below_replacement(team_id, pid) else s.player(pid)
        for pid in receives
        if pid not in dropped
    ]
    incoming += [_stand_in(s, -(_OPEN_SPOT - i)) for i in range(opened)]
    return _new_weekly(s, team_id, out_ids, incoming), [p.player_id for p in drops], opened


@dataclass
class TradeSide:
    team_id: int
    receives: list[int]
    sends: list[int]
    drops: list[int]  # dropped to make room
    opened: int  # roster spots the deal opens, each holding a replacement-level player
    below_replacement: list[int]  # players received who count only as replacement level here
    cats_per_week: float  # before the deal, as rostered, vs the league average
    d_cats_per_week: float  # with players below replacement counted at it, before and after
    p_week: float  # mean P(win a week) vs the league average, before the deal, as rostered
    d_p_week: float
    cat_p_win: dict[str, float]  # mean P(win) per category per week, before the deal, as rostered
    cat_d_p_win: dict[str, float]
    cat_d_value: dict[str, float]  # change in the expected weekly total; percentages: in the percentage
    d_value: float  # value (`Season.value`) received minus sent


@dataclass
class TradeResult:
    me: TradeSide
    partner: TradeSide

    @property
    def lopsided(self) -> bool:
        """The partner gives up clearly more value than they get, so a manager going by player
        values would likely say no."""
        return self.partner.d_value < -LOPSIDED

    @property
    def throw_in(self) -> bool:
        """A side gets a player below replacement, or drops one it just got: the same deal
        without him, plus a throw-in."""
        sides = (self.me, self.partner)
        return any(s.below_replacement or set(s.drops) & set(s.receives) for s in sides)


def _value_change(categories: list[Category], before: np.ndarray, after: np.ndarray) -> dict[str, float]:
    out = {}
    for cat in categories:
        if cat.is_percentage:
            m, a = _COL[cat.makes], _COL[cat.attempts]
            out[cat.key] = pct(after[:, m].sum(), after[:, a].sum()) - pct(before[:, m].sum(), before[:, a].sum())
        else:
            out[cat.key] = float((after[:, _COL[cat.key]] - before[:, _COL[cat.key]]).mean())
    return out


def evaluate_trade(s: Season, team_id: int, partner_id: int, give: list[int], get: list[int]) -> TradeResult:
    """`give`: my players going to the partner; `get`: the partner's players coming to me.
    The change is measured with players below replacement counted at it (`Season.floored`)."""
    plan = {team_id: (give, get), partner_id: (get, give)}  # team -> (sends, receives)
    after = {t: _after(s, t, list(sends), list(receives)) for t, (sends, receives) in plan.items()}
    avg = s.floored_average + sum(after[t][0] - s.floored_weekly[t] for t in plan) / len(s.weekly)

    def side(t: int) -> TradeSide:
        sends, receives = plan[t]
        weekly, drops, opened = after[t]
        base, now = s.floored_cat_p_win[t], weekly_cat_p_win(weekly, avg, s.categories, s.dispersion)
        actual = s.cat_p_win[t]
        return TradeSide(
            team_id=t,
            receives=list(receives),
            sends=list(sends),
            drops=drops,
            opened=opened,
            below_replacement=[pid for pid in receives if pid not in drops and s.below_replacement(t, pid)],
            cats_per_week=float(sum(actual.values()).mean()),
            d_cats_per_week=float((sum(now.values()) - sum(base.values())).mean()),
            p_week=float(week_odds(actual)[0].mean()),
            d_p_week=float((week_odds(now)[0] - week_odds(base)[0]).mean()),
            cat_p_win={k: float(v.mean()) for k, v in actual.items()},
            cat_d_p_win={k: float((now[k] - base[k]).mean()) for k in base},
            cat_d_value=_value_change(s.categories, s.floored_weekly[t], weekly),
            d_value=s.value(list(receives)) - s.value(list(sends)),
        )

    return TradeResult(side(team_id), side(partner_id))


def first_order(s: Season, team_id: int, sends: tuple[int, ...], receives: tuple[int, ...]) -> float:
    """The screen's estimate of a side's change in categories won per week, roster moves included.
    No player counts for less than a replacement-level player, and an opened spot holds one."""
    drops, opened = roster_moves(s, team_id, list(receives), set(sends))
    v = s.values[team_id]
    floor = v[REPLACEMENT_ID] if s.replacement else -np.inf

    def worth(pid: int) -> float:
        return max(v[pid], floor)

    gained = sum(worth(pid) for pid in receives) + (opened * floor if opened else 0.0)
    return gained - sum(worth(pid) for pid in sends) - sum(worth(p.player_id) for p in drops)


def _throw_in(s: Season, team_id: int, sends: tuple[int, ...], receives: tuple[int, ...]) -> bool:
    """A side gets a player below replacement for it, or one it would drop to make room."""
    if any(s.below_replacement(team_id, pid) for pid in receives):
        return True
    drops, _ = roster_moves(s, team_id, list(receives), set(sends))
    return any(p.player_id in receives for p in drops)


@dataclass
class TradeSearch:
    deals: list[TradeResult]  # good for me, not bad for the partner; best for me first, one per set I'd get
    candidates: int  # deals screened
    evaluated: int  # deals evaluated in full


def deal_shapes(mine: list[int], theirs: list[int]) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    """(give, get) for every 1-for-1 and 2-for-1 deal, both ways."""
    return (
        [((a,), (b,)) for a in mine for b in theirs]
        + [(pair, (b,)) for pair in combinations(mine, 2) for b in theirs]
        + [((a,), pair) for a in mine for pair in combinations(theirs, 2)]
    )


def find_trades(
    s: Season,
    team_id: int,
    tradable: set[int],
    per_shape: int = PER_SHAPE,
    min_gain: float = MIN_GAIN,
    partner_min_gain: float = PARTNER_MIN_GAIN,
) -> TradeSearch:
    """Deals that help me and don't hurt the partner. Only players in `tradable` are offered or
    asked for."""
    mine = [p.player_id for p in s.rosters[team_id] if p.player_id in tradable]
    results, candidates = [], 0
    for partner_id, roster in s.rosters.items():
        if partner_id == team_id:
            continue
        theirs = [p.player_id for p in roster if p.player_id in tradable]
        by_shape: dict[tuple[int, int], list] = {}
        for give, get in deal_shapes(mine, theirs):
            candidates += 1
            if _throw_in(s, team_id, give, get) or _throw_in(s, partner_id, get, give):
                continue
            gains = first_order(s, team_id, give, get), first_order(s, partner_id, get, give)
            if min(gains) > 0:
                by_shape.setdefault((len(give), len(get)), []).append((min(gains), give, get))
        for screened in by_shape.values():
            screened.sort(key=lambda x: x[0], reverse=True)
            results += [evaluate_trade(s, team_id, partner_id, list(g), list(r)) for _, g, r in screened[:per_shape]]
    results.sort(key=lambda r: r.me.d_cats_per_week, reverse=True)
    deals, targets = [], set()
    for r in results:
        target = frozenset(r.me.receives)
        good = r.me.d_cats_per_week >= min_gain and r.partner.d_cats_per_week >= partner_min_gain
        if good and not r.throw_in and target not in targets:
            targets.add(target)
            deals.append(r)
    return TradeSearch(deals, candidates, len(results))
