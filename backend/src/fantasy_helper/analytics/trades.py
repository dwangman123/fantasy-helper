"""Trades: what a deal does for both teams over the rest of the season, and a finder
for deals that help both.

Measure. Hold's (`waivers.py`), for each side: the change in expected categories
won per week against a league-average team over the remaining weeks, everyone
healthy, with each week's expected totals from the daily lineup cap over the NBA
schedule. Also the change in P(win the week), from the per-category odds with the
categories treated as independent (`week_odds`). The league average is
recomputed with both rosters after the deal.

Rosters stay legal. A side pushed over the roster limit drops its lowest-priority
players outside the deal; a side that sends more players than it gets fills the
spots it opened with its best free agents for an open spot (found once per team,
not per deal).

Finder. Every 1-for-1 and 2-for-1 deal, both ways, with every other team is
screened by a first-order estimate of each side's change in categories won per
week: a player's expected weekly stats (rate x games, no lineup cap) times the
slope of the team's P(win) in each category at its current totals. The slope is
steepest where a team is close to the league average, so help in a category it
punts or has locked counts for little. With each partner, the deals of each shape
whose smaller estimated gain is largest are evaluated in full. Per partner, so
every team gets a look; per shape, because ignoring the lineup cap overrates
2-for-1s and would crowd out the 1-for-1s; by the smaller gain, because the
estimate tracks the full score (correlation about 0.95 on a sample of the real
league's deals, Oct 2026) but not closely enough to trust a deal that only looks
good for one side. A deal is kept when both sides gain at least `MIN_GAIN`
categories a week (a smaller gain is too small to count on), and each set of
players I'd get keeps only its best package.
"""

from dataclasses import dataclass
from itertools import combinations

import numpy as np

from fantasy_helper.analytics.matchup import SimPlayer
from fantasy_helper.analytics.waivers import cat_slopes, day_totals, weekly_cat_p_win, weekly_stats, weekly_totals
from fantasy_helper.analytics.zscores import pct
from fantasy_helper.categories import Category
from fantasy_helper.domain import STAT_KEYS

PER_SHAPE = 15  # deals per partner and shape (1-for-1, 2-for-1, 1-for-2) evaluated in full
MIN_GAIN = 0.05  # categories won per week each side must gain for a deal to be listed
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
class Season:
    """Every team's expected weeks over the rest of the season, before any trade."""

    categories: list[Category]
    weeks: list[list[int]]  # the scoring periods in each remaining week
    slot_counts: dict[str, int]
    roster_spots: int  # IR excluded
    dispersion: dict[str, float]
    rosters: dict[int, list[SimPlayer]]
    day_totals: dict[int, dict[int, np.ndarray]]  # team -> day -> expected starters' totals
    weekly: dict[int, np.ndarray]  # team -> weeks x STAT_KEYS
    average: np.ndarray  # the league-average team's weeks
    cat_p_win: dict[int, dict[str, np.ndarray]]  # team -> category -> P(win) each week vs the average
    values: dict[int, dict[int, float]]  # team -> player -> first-order categories won per week
    fills: dict[int, list[SimPlayer]]  # team -> free agents for an open spot, best first

    def player(self, player_id: int) -> SimPlayer:
        return next(p for r in self.rosters.values() for p in r if p.player_id == player_id)


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
    )
    for t in rosters:
        best = sorted(free_agents, key=lambda p: -values[t][p.player_id])[:FILL_CHOICES]
        gain = {p.player_id: _cats_per_week(s, _new_weekly(s, t, set(), [p]), average) for p in best}
        s.fills[t] = sorted(best, key=lambda p: -gain[p.player_id])
    return s


def _new_weekly(s: Season, team_id: int, out_ids: set[int], incoming: list[SimPlayer]) -> np.ndarray:
    """A team's weeks with `out_ids` gone and `incoming` added; only days they play are recomputed."""
    leaving = [p for p in s.rosters[team_id] if p.player_id in out_ids]
    roster = [p for p in s.rosters[team_id] if p.player_id not in out_ids] + incoming
    changed = {d for p in [*leaving, *incoming] for d in p.games}
    base = s.day_totals[team_id]
    return weekly_totals(
        {d: day_totals(roster, d, s.slot_counts) if d in changed else t for d, t in base.items()}, s.weeks
    )


def _cats_per_week(s: Season, weekly: np.ndarray, average: np.ndarray) -> float:
    return float(sum(weekly_cat_p_win(weekly, average, s.categories, s.dispersion).values()).mean())


def roster_moves(s: Season, team_id: int, receives: int, sends: set[int]) -> tuple[list[SimPlayer], list[SimPlayer]]:
    """Who a side drops to stay within the roster limit after getting `receives` players
    for `sends`, and which free agents fill the spots the deal opens."""
    kept = [p for p in s.rosters[team_id] if p.player_id not in sends]
    over = len(kept) + receives - s.roster_spots
    drops = sorted(kept, key=lambda p: p.priority)[: max(over, 0)]
    opened = min(len(sends) - receives, s.roster_spots - len(kept) - receives)
    return drops, s.fills.get(team_id, [])[: max(opened, 0)]


@dataclass
class TradeSide:
    team_id: int
    receives: list[int]
    sends: list[int]
    drops: list[int]  # dropped to make room
    adds: list[int]  # free agents picked up for the spots the deal opens
    cats_per_week: float  # before the deal, vs the league average
    d_cats_per_week: float
    p_week: float  # mean P(win a week) vs the league average, before the deal
    d_p_week: float
    cat_p_win: dict[str, float]  # mean P(win) per category per week, before the deal
    cat_d_p_win: dict[str, float]
    cat_d_value: dict[str, float]  # change in the expected weekly total; percentages: in the percentage


@dataclass
class TradeResult:
    me: TradeSide
    partner: TradeSide


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
    """`give`: my players going to the partner; `get`: the partner's players coming to me."""
    plan = {team_id: (give, get), partner_id: (get, give)}  # team -> (sends, receives)
    moves, new = {}, {}
    for t, (sends, receives) in plan.items():
        drops, adds = roster_moves(s, t, len(receives), set(sends))
        moves[t] = drops, adds
        incoming = [s.player(pid) for pid in receives] + adds
        new[t] = _new_weekly(s, t, set(sends) | {p.player_id for p in drops}, incoming)
    after = s.average + sum(new[t] - s.weekly[t] for t in plan) / len(s.weekly)

    def side(t: int) -> TradeSide:
        sends, receives = plan[t]
        drops, adds = moves[t]
        base, now = s.cat_p_win[t], weekly_cat_p_win(new[t], after, s.categories, s.dispersion)
        base_week, now_week = week_odds(base)[0], week_odds(now)[0]
        return TradeSide(
            team_id=t,
            receives=list(receives),
            sends=list(sends),
            drops=[p.player_id for p in drops],
            adds=[p.player_id for p in adds],
            cats_per_week=float(sum(base.values()).mean()),
            d_cats_per_week=float((sum(now.values()) - sum(base.values())).mean()),
            p_week=float(base_week.mean()),
            d_p_week=float((now_week - base_week).mean()),
            cat_p_win={k: float(v.mean()) for k, v in base.items()},
            cat_d_p_win={k: float((now[k] - base[k]).mean()) for k in base},
            cat_d_value=_value_change(s.categories, s.weekly[t], new[t]),
        )

    return TradeResult(side(team_id), side(partner_id))


def first_order(s: Season, team_id: int, sends: tuple[int, ...], receives: tuple[int, ...]) -> float:
    """The screen's estimate of a side's change in categories won per week, roster moves included."""
    drops, adds = roster_moves(s, team_id, len(receives), set(sends))
    v = s.values[team_id]
    gained = sum(v[pid] for pid in receives) + sum(v[p.player_id] for p in adds)
    return gained - sum(v[pid] for pid in sends) - sum(v[p.player_id] for p in drops)


@dataclass
class TradeSearch:
    deals: list[TradeResult]  # both sides gain; best for me first, one per set of players I'd get
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
    s: Season, team_id: int, tradable: set[int], per_shape: int = PER_SHAPE, min_gain: float = MIN_GAIN
) -> TradeSearch:
    """Deals that help both sides. Only players in `tradable` are offered or asked for."""
    mine = [p.player_id for p in s.rosters[team_id] if p.player_id in tradable]
    results, candidates = [], 0
    for partner_id, roster in s.rosters.items():
        if partner_id == team_id:
            continue
        theirs = [p.player_id for p in roster if p.player_id in tradable]
        by_shape: dict[tuple[int, int], list] = {}
        for give, get in deal_shapes(mine, theirs):
            candidates += 1
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
        if min(r.me.d_cats_per_week, r.partner.d_cats_per_week) >= min_gain and target not in targets:
            targets.add(target)
            deals.append(r)
    return TradeSearch(deals, candidates, len(results))
