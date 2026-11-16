"""Trades: what a deal does for both teams over the rest of the season, and a finder
for deals that help both.

Measure. Hold's (`waivers.py`), for each side: the change in expected categories
won per week against a league-average team over the remaining weeks, with each
week's expected totals from the daily lineup cap over the NBA schedule (an injured
player has no games before his expected return, so the bench covers them). Also the
change in P(win the week), from the per-category odds with the categories treated as
independent (`week_odds`). The league average is recomputed with both rosters after
the deal.

Against the best no-trade option. A roster spot is worth the best free agent for it,
not the worst player in it, so a trade isn't credited with a pickup a team could make
without it. Before and after the deal, each side may swap its lowest-priority player
for its best free agent not already added, when that raises its categories won, as
many times as the deal opens spots (at least once). Without this, a side that sends
2 for 1 scored the free agent it picks up for the open spot, and a side that gets 2
for 1 was charged only for cutting a player it could have swapped for that free agent
anyway: on the real league's rosters (Nov 2026) most listed deals won on that alone.

Rosters stay legal. A side pushed over the roster limit drops its lowest-priority
players, ones it just got included; a side that sends more players than it gets
fills the spots it opened with its best free agents for an open spot (found once per
team, not per deal).

Value. How the other manager would likely see a deal: each player's G-score above
the best free agent a team would pick up, and 0 below him (he could be dropped for
nothing), summed over the players a side gets minus those it sends. A negative-G
throw-in adds nothing, as a plain sum would have it subtract. A deal is flagged
when the partner gives up more than `LOPSIDED` of it.

Finder. Every 1-for-1 and 2-for-1 deal, both ways, with every other team is
screened by a first-order estimate of each side's change in categories won per
week: a player's expected weekly stats (rate x games, no lineup cap) times the
slope of the team's P(win) in each category at its current totals, and never less
than the team's best free agent's. The slope is steepest where a team is close to
the league average, so help in a category it punts or has locked counts for little.
With each partner, the deals of each shape whose smaller estimated gain is largest
are evaluated in full. Per partner, so every team gets a look; per shape, because
ignoring the lineup cap overrates 2-for-1s and would crowd out the 1-for-1s; by the
smaller gain, because the estimate tracks the full score but not closely enough to
trust a deal that only looks good for one side. A deal is kept when it gains me at
least `MIN_GAIN` categories a week (a smaller gain is too small to count on) and
costs the partner nothing, and each set of players I'd get keeps only its best
package. A deal in which a side would drop a player it gets is left out: it's the
smaller deal plus a throw-in, and that one is screened on its own.
"""

from dataclasses import dataclass, field
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
LOPSIDED = 1.0  # value (G above the best free agent) the partner may give up before a deal is flagged
FILL_CHOICES = 3  # free agents per team tried in full for an open spot
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


@dataclass
class Roster:
    """A team's roster after some moves, against its current one."""

    out_ids: frozenset[int]  # current players gone
    incoming: tuple[SimPlayer, ...]  # players added
    weekly: np.ndarray  # its expected weeks (weeks x STAT_KEYS)
    drops: tuple[int, ...] = ()  # players dropped, for room or for a free agent
    adds: tuple[int, ...] = ()  # free agents picked up


@dataclass
class Season:
    """Every team's expected weeks over the rest of the season, before any trade."""

    categories: list[Category]
    weeks: list[list[int]]  # the scoring periods in each remaining week
    slot_counts: dict[str, int]
    roster_spots: int  # IR excluded
    dispersion: dict[str, float]
    rosters: dict[int, list[SimPlayer]]
    day_totals: dict[int, dict[int, np.ndarray]]  # team -> day -> expected starters' totals
    weekly: dict[int, np.ndarray]  # team -> weeks x STAT_KEYS, as rostered now
    average: np.ndarray  # the league-average team's weeks
    cat_p_win: dict[int, dict[str, np.ndarray]]  # team -> category -> P(win) each week vs the average
    values: dict[int, dict[int, float]]  # team -> player -> first-order categories won per week
    fills: dict[int, list[SimPlayer]]  # team -> free agents for an open spot, best first
    replacement: SimPlayer | None = None  # the best free agent any team would pick up, by G-score
    baselines: dict[tuple[int, int], Roster] = field(default_factory=dict)  # (team, pickups) -> no-trade best
    by_id: dict[int, SimPlayer] = field(default_factory=dict)  # rostered players

    def player(self, player_id: int) -> SimPlayer:
        return self.by_id[player_id]

    def value(self, player_ids: list[int]) -> float:
        """How a manager would likely value these players: each one's G-score above the best free
        agent, 0 for one below him (he could be dropped for nothing)."""
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
    """`rosters` and `free_agents` carry their games over `weeks`; `free_agents` are the
    pickups that may fill a spot a deal opens."""
    dispersion = dispersion or {}
    days = sorted({d for w in weeks for d in w})
    by_day = {t: {d: day_totals(players, d, slot_counts) for d in days} for t, players in rosters.items()}
    weekly = {t: weekly_totals(by_day[t], weeks) for t in rosters}
    average = np.mean(list(weekly.values()), axis=0)
    stats = {p.player_id: weekly_stats(p, weeks) for p in [*free_agents, *(p for r in rosters.values() for p in r)]}
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
        by_day,
        weekly,
        average,
        {t: weekly_cat_p_win(weekly[t], average, categories, dispersion) for t in rosters},
        values,
        {},
        by_id={p.player_id: p for r in rosters.values() for p in r},
    )
    for t in rosters:
        best = sorted(free_agents, key=lambda p: -values[t][p.player_id])[:FILL_CHOICES]
        gain = {p.player_id: _cats_per_week(s, _new_weekly(s, t, set(), [p]), average) for p in best}
        s.fills[t] = sorted(best, key=lambda p: -gain[p.player_id])
    s.replacement = max((p for f in s.fills.values() for p in f), key=lambda p: p.priority, default=None)
    for t in rosters:
        baseline(s, t, 1)
    return s


def _new_weekly(s: Season, team_id: int, out_ids: set[int], incoming: list[SimPlayer]) -> np.ndarray:
    """A team's weeks with `out_ids` gone and `incoming` added; only days they play are recomputed."""
    leaving = [p for p in s.rosters[team_id] if p.player_id in out_ids]
    roster = [p for p in s.rosters[team_id] if p.player_id not in out_ids] + incoming
    changed = {d for p in [*leaving, *incoming] for d in p.games}
    base = s.day_totals[team_id]
    if (first := recount_from(roster)) is not None:
        changed |= {d for d in base if d >= first}
    return weekly_totals(
        {d: day_totals(roster, d, s.slot_counts) if d in changed else t for d, t in base.items()}, s.weeks
    )


def _cats_per_week(s: Season, weekly: np.ndarray, average: np.ndarray) -> float:
    return float(sum(weekly_cat_p_win(weekly, average, s.categories, s.dispersion).values()).mean())


def _pickup(s: Season, team_id: int, r: Roster) -> Roster | None:
    """One free move: the team's lowest-priority player (outside the IR slot) for its best free
    agent not already added, when that raises its categories won per week. None if it doesn't."""
    added = {p.player_id for p in r.incoming}
    fill = next((p for p in s.fills.get(team_id, []) if p.player_id not in added), None)
    roster = [p for p in s.rosters[team_id] if p.player_id not in r.out_ids and p.ir_back is None]
    roster += [p for p in r.incoming if p.ir_back is None]
    if fill is None or not roster:
        return None
    worst = min(roster, key=lambda p: p.priority).player_id
    if worst in added:  # one he just got
        out_ids, incoming = r.out_ids, tuple(p for p in r.incoming if p.player_id != worst) + (fill,)
    else:
        out_ids, incoming = r.out_ids | {worst}, (*r.incoming, fill)
    weekly = _new_weekly(s, team_id, set(out_ids), list(incoming))
    if _cats_per_week(s, weekly, s.average) <= _cats_per_week(s, r.weekly, s.average):
        return None
    return Roster(out_ids, incoming, weekly, (*r.drops, worst), (*r.adds, fill.player_id))


def _pickups(s: Season, team_id: int, r: Roster, n: int) -> Roster:
    """Up to `n` free moves (`_pickup`), stopping at the first that doesn't help."""
    for _ in range(n):
        if (nxt := _pickup(s, team_id, r)) is None:
            break
        r = nxt
    return r


def baseline(s: Season, team_id: int, pickups: int) -> Roster:
    """A team's best roster without a trade: up to `pickups` free moves from its current one."""
    key = (team_id, pickups)
    if key not in s.baselines:
        start = Roster(frozenset(), (), s.weekly[team_id]) if pickups <= 1 else baseline(s, team_id, pickups - 1)
        s.baselines[key] = _pickups(s, team_id, start, 1 if pickups >= 1 else 0)
    return s.baselines[key]


def roster_moves(
    s: Season, team_id: int, receives: list[int], sends: set[int]
) -> tuple[list[SimPlayer], list[SimPlayer]]:
    """Who a side drops to stay within the roster limit after getting `receives` for `sends`
    (its lowest-priority players, ones it just got included), and which free agents fill the
    spots the deal opens. A player in the IR slot holds no spot now and can't be dropped; when
    he's back, `waivers.on_roster` makes room."""
    kept = [p for p in s.rosters[team_id] if p.player_id not in sends and p.ir_back is None]
    over = len(kept) + len(receives) - s.roster_spots
    drops = sorted([*kept, *(s.player(pid) for pid in receives)], key=lambda p: p.priority)[: max(over, 0)]
    opened = min(len(sends) - len(receives), s.roster_spots - len(kept) - len(receives))
    return drops, s.fills.get(team_id, [])[: max(opened, 0)]


def _after(s: Season, team_id: int, sends: list[int], receives: list[int]) -> tuple[Roster, int]:
    """A side's roster after the deal and its free moves, and how many free agents the
    no-trade option it's compared with may use: as many as the deal opens spots, at least one."""
    drops, adds = roster_moves(s, team_id, receives, set(sends))
    dropped = {p.player_id for p in drops}
    out_ids = frozenset(sends) | (dropped - set(receives))
    incoming = tuple(s.player(pid) for pid in receives if pid not in dropped) + tuple(adds)
    deal = Roster(
        out_ids,
        incoming,
        _new_weekly(s, team_id, set(out_ids), list(incoming)),
        tuple(p.player_id for p in drops),
        tuple(p.player_id for p in adds),
    )
    pickups = max(len(adds), 1)
    return _pickups(s, team_id, deal, pickups - len(adds)), pickups


@dataclass
class TradeSide:
    team_id: int
    receives: list[int]
    sends: list[int]
    drops: list[int]  # dropped to make room, or for a free agent
    adds: list[int]  # free agents picked up, for the spots the deal opens or for a drop
    pickup_drops: list[int]  # the no-trade option the deal is compared with: these dropped...
    pickup_adds: list[int]  # ...for these free agents
    cats_per_week: float  # with no trade (after the pickups), vs the league average
    d_cats_per_week: float
    p_week: float  # mean P(win a week) vs the league average, with no trade
    d_p_week: float
    cat_p_win: dict[str, float]  # mean P(win) per category per week, with no trade
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
        """A side drops a player it just got: the same deal without him, plus a throw-in."""
        return any(pid in side.receives for side in (self.me, self.partner) for pid in side.drops)


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
    Each side is compared with its best no-trade option (`baseline`)."""
    plan = {team_id: (give, get), partner_id: (get, give)}  # team -> (sends, receives)
    after, before = {}, {}
    for t, (sends, receives) in plan.items():
        after[t], pickups = _after(s, t, list(sends), list(receives))
        before[t] = baseline(s, t, pickups)
    n = len(s.weekly)
    avg_before = s.average + sum(before[t].weekly - s.weekly[t] for t in plan) / n
    avg_after = s.average + sum(after[t].weekly - s.weekly[t] for t in plan) / n

    def side(t: int) -> TradeSide:
        sends, receives = plan[t]
        base = weekly_cat_p_win(before[t].weekly, avg_before, s.categories, s.dispersion)
        now = weekly_cat_p_win(after[t].weekly, avg_after, s.categories, s.dispersion)
        base_week, now_week = week_odds(base)[0], week_odds(now)[0]
        return TradeSide(
            team_id=t,
            receives=list(receives),
            sends=list(sends),
            drops=list(after[t].drops),
            adds=list(after[t].adds),
            pickup_drops=list(before[t].drops),
            pickup_adds=list(before[t].adds),
            cats_per_week=float(sum(base.values()).mean()),
            d_cats_per_week=float((sum(now.values()) - sum(base.values())).mean()),
            p_week=float(base_week.mean()),
            d_p_week=float((now_week - base_week).mean()),
            cat_p_win={k: float(v.mean()) for k, v in base.items()},
            cat_d_p_win={k: float((now[k] - base[k]).mean()) for k in base},
            cat_d_value=_value_change(s.categories, before[t].weekly, after[t].weekly),
            d_value=s.value(list(receives)) - s.value(list(sends)),
        )

    return TradeResult(side(team_id), side(partner_id))


def first_order(s: Season, team_id: int, sends: tuple[int, ...], receives: tuple[int, ...]) -> float:
    """The screen's estimate of a side's change in categories won per week, roster moves included.
    No player counts for less than the team's best free agent, who could take his spot anyway."""
    drops, adds = roster_moves(s, team_id, list(receives), set(sends))
    v = s.values[team_id]
    fills = s.fills.get(team_id)
    floor = v[fills[0].player_id] if fills else -np.inf

    def worth(pid: int) -> float:
        return max(v[pid], floor)

    gained = sum(worth(pid) for pid in receives) + sum(v[p.player_id] for p in adds)
    return gained - sum(worth(pid) for pid in sends) - sum(worth(p.player_id) for p in drops)


def _cuts_a_player_it_gets(s: Season, team_id: int, sends: tuple[int, ...], receives: tuple[int, ...]) -> bool:
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
            if _cuts_a_player_it_gets(s, team_id, give, get) or _cuts_a_player_it_gets(s, partner_id, get, give):
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
