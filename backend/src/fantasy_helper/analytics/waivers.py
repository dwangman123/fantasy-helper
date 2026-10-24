"""Waiver rankings: free agents ranked by what they do for my team, never by generic rank.

Category board. Each category is labeled from my P(win) in the current matchup:
locked (> 90%), swing (10-90%) or lost (< 10%).

Stream (this week). Re-run the matchup simulation against my actual opponent with
the free agent swapped in for one of my players from the first day he can play
(the dropped player keeps any games before then). Rank by the change in P(win
matchup), then by the change in expected categories won. Baseline and swaps share
their random numbers (see `matchup.py`), so the deltas measure the swap, not
simulation noise. Because the measure is win odds against this opponent, help in
a swing category counts, help in a locked or lost one barely does, and hurting a
lost category costs nothing, so weekly punts fall out without being coded.

Hold (rest of season). Change in expected categories won per week against a
league-average opponent over the remaining weeks, everyone assumed healthy. A
week's expected totals come from the daily lineup cap over the NBA schedule, and
P(win) per category is a normal approximation: variance phi * mean for counting
stats, p(1 - p) / attempts for percentages.

Keeping it fast. Every add x drop pair costs a full evaluation, so free agents are
screened first by z-score value times games. Stream counts the games he would
start this week and only swing categories; hold counts all remaining games,
with each category weighted by 4p(1 - p) at my baseline P(win) against the
league average, which is largest where I'm closest to even. Drops are limited
to my lowest-priority players (the caller picks them).
"""

from dataclasses import dataclass, replace

import numpy as np
from scipy.special import ndtr

from fantasy_helper.analytics.lineup import assign_starters
from fantasy_helper.analytics.matchup import MatchupPrediction, SimPlayer, score_matchup, simulate_side
from fantasy_helper.categories import Category
from fantasy_helper.domain import STAT_KEYS

LOCKED, SWING, LOST = "locked", "swing", "lost"
LOCK_P = 0.9
_COL = {k: i for i, k in enumerate(STAT_KEYS)}


def category_board(cat_p_win: dict[str, float], lock_p: float = LOCK_P) -> dict[str, str]:
    return {k: LOCKED if p > lock_p else LOST if p < 1 - lock_p else SWING for k, p in cat_p_win.items()}


@dataclass
class SideView:
    """A matchup prediction from one team's side."""

    p_win: float
    p_tie: float
    p_loss: float
    expected_cats: float
    cat_p_win: dict[str, float]
    cat_p_loss: dict[str, float]
    expected_games: float  # started games over the days left


def side_view(pred: MatchupPrediction, side: str) -> SideView:
    other = "away" if side == "home" else "home"
    p = {"home": pred.p_home, "away": pred.p_away}
    return SideView(
        p_win=p[side],
        p_tie=pred.p_tie,
        p_loss=p[other],
        expected_cats=pred.expected_score[side],
        cat_p_win={k: v[side] for k, v in pred.cat_probs.items()},
        cat_p_loss={k: v[other] for k, v in pred.cat_probs.items()},
        expected_games=pred.expected_games[side],
    )


def _games_between(p: SimPlayer, first: int | None = None, before: int | None = None) -> SimPlayer:
    games = {d: f for d, f in p.games.items() if (first is None or d >= first) and (before is None or d < before)}
    return replace(p, games=games)


@dataclass
class StreamOption:
    add_id: int
    drop_id: int | None  # None: an open roster spot, nobody dropped
    result: SideView
    d_p_win: float
    d_p_tie: float
    d_expected_cats: float
    extra_starts: float  # team started games gained after the lineup cap
    cat_d_p_win: dict[str, float]
    gained: list[str]  # categories that turn in my favor (P(win) > P(loss))
    lost: list[str]  # categories that stop being in my favor


@dataclass
class StreamRanking:
    baseline: SideView
    board: dict[str, str]
    options: list[StreamOption]  # best drop per free agent, best first
    screened: int  # free agents evaluated after the screen


def _starts_this_week(add: SimPlayer, mine: list[SimPlayer], slot_counts: dict[str, int]) -> float:
    """Expected games he'd start, assuming everyone else who can play does."""
    starts = 0.0
    for day in add.games:
        playing = sorted([p for p in mine if day in p.games and p.p_play > 0] + [add], key=lambda p: -p.priority)
        if add.player_id in assign_starters([(p.player_id, p.eligible_slots) for p in playing], slot_counts):
            starts += add.p_play
    return starts


def screen_stream(
    adds: list[SimPlayer],
    mine: list[SimPlayer],
    slot_counts: dict[str, int],
    board: dict[str, str],
    z: dict[int, dict[str, float]],
    limit: int,
) -> list[SimPlayer]:
    """Free agents most likely to move a swing category this week. `adds` hold only the
    games they can play."""
    targets = [k for k, label in board.items() if label == SWING] or list(board)
    scored = []
    for add in adds:
        starts = _starts_this_week(add, mine, slot_counts) if add.p_play > 0 else 0.0
        if starts > 0:
            scored.append((starts * sum(z[add.player_id][k] for k in targets), add))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [add for _, add in scored[:limit]]


def _best_per_add(options: list) -> list:
    """Options are sorted best first; keep each free agent's best drop."""
    seen, out = set(), []
    for o in options:
        if o.add_id not in seen:
            seen.add(o.add_id)
            out.append(o)
    return out


def rank_stream(
    categories: list[Category],
    actual: dict[str, dict[str, float]],
    players: dict[str, list[SimPlayer]],
    side: str,
    days: list[int],
    first_day: int,
    slot_counts: dict[str, int],
    adds: list[SimPlayer],
    drop_ids: list[int | None],
    z: dict[int, dict[str, float]],
    limit: int = 25,
    n_sims: int = 5000,
    seed: int = 0,
    dispersion: dict[str, float] | None = None,
) -> StreamRanking:
    """`actual` and `players` are keyed by "home" / "away" as in `simulate_matchup`;
    `side` is mine. `adds` are free agents with their games over `days`; `drop_ids`
    are the players I'd consider dropping, lowest priority first."""
    other = "away" if side == "home" else "home"
    opp = simulate_side(categories, actual[other], players[other], days, slot_counts, other, n_sims, seed, dispersion)

    def run(mine: list[SimPlayer]) -> SideView:
        draws = {other: opp}
        draws[side] = simulate_side(categories, actual[side], mine, days, slot_counts, side, n_sims, seed, dispersion)
        return side_view(score_matchup(categories, draws["home"], draws["away"]), side)

    base = run(players[side])
    board = category_board(base.cat_p_win)
    adds = [_games_between(a, first=first_day) for a in adds]
    weakest = drop_ids[0] if drop_ids else None
    screen_roster = [p for p in players[side] if p.player_id != weakest]
    candidates = screen_stream(adds, screen_roster, slot_counts, board, z, limit)

    favored = {k: base.cat_p_win[k] > base.cat_p_loss[k] for k in base.cat_p_win}
    options = []
    for add in candidates:
        for drop_id in drop_ids:
            mine = [_games_between(p, before=first_day) if p.player_id == drop_id else p for p in players[side]]
            r = run([*mine, add])
            now = {k: r.cat_p_win[k] > r.cat_p_loss[k] for k in r.cat_p_win}
            options.append(
                StreamOption(
                    add_id=add.player_id,
                    drop_id=drop_id,
                    result=r,
                    d_p_win=r.p_win - base.p_win,
                    d_p_tie=r.p_tie - base.p_tie,
                    d_expected_cats=r.expected_cats - base.expected_cats,
                    extra_starts=r.expected_games - base.expected_games,
                    cat_d_p_win={k: r.cat_p_win[k] - base.cat_p_win[k] for k in base.cat_p_win},
                    gained=[k for k in now if now[k] and not favored[k]],
                    lost=[k for k in now if favored[k] and not now[k]],
                )
            )
    options.sort(key=lambda o: (o.d_p_win, o.d_expected_cats), reverse=True)
    return StreamRanking(base, board, _best_per_add(options), len(candidates))


@dataclass
class HoldOption:
    add_id: int
    drop_id: int | None
    d_cats_per_week: float
    cat_d_p_win: dict[str, float]  # mean change per week in P(win) for each category


@dataclass
class HoldRanking:
    weeks: int
    cats_per_week: float  # baseline expected categories won per week vs the league average
    cat_p_win: dict[str, float]  # baseline mean P(win) per category per week
    options: list[HoldOption]  # best drop per free agent, best first
    screened: int


def _day_totals(players: list[SimPlayer], day: int, slot_counts: dict[str, int]) -> np.ndarray:
    """Expected stats from the players who'd start that day (everyone healthy)."""
    playing = sorted((p for p in players if day in p.games), key=lambda p: -p.priority)
    total = np.zeros(len(STAT_KEYS))
    if not playing:
        return total
    starters = assign_starters([(p.player_id, p.eligible_slots) for p in playing], slot_counts)
    for p in playing:
        if p.player_id in starters:
            total += [p.per_game[k] * p.games[day].get(k, 1.0) for k in STAT_KEYS]
    return total


def _weekly(day_totals: dict[int, np.ndarray], weeks: list[list[int]]) -> np.ndarray:
    return np.array([sum((day_totals[d] for d in w), np.zeros(len(STAT_KEYS))) for w in weeks])


def _ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    return np.divide(num, den, out=np.zeros_like(num), where=den > 0)


def weekly_cat_p_win(
    me: np.ndarray, opp: np.ndarray, categories: list[Category], dispersion: dict[str, float]
) -> dict[str, np.ndarray]:
    """P(win) per category for each week, from expected weekly totals (weeks x STAT_KEYS)."""
    out = {}
    for cat in categories:
        if cat.is_percentage:
            att_m, att_o = me[:, _COL[cat.attempts]], opp[:, _COL[cat.attempts]]
            pm, po = _ratio(me[:, _COL[cat.makes]], att_m), _ratio(opp[:, _COL[cat.makes]], att_o)
            diff, var = pm - po, _ratio(pm * (1 - pm), att_m) + _ratio(po * (1 - po), att_o)
        else:
            m, o = me[:, _COL[cat.key]], opp[:, _COL[cat.key]]
            diff, var = (o - m if cat.reverse else m - o), dispersion.get(cat.key, 1.0) * (m + o)
        z = np.divide(diff, np.sqrt(var), out=np.zeros_like(diff), where=var > 0)
        out[cat.key] = np.where(var > 0, ndtr(z), 0.5 + 0.5 * np.sign(diff))
    return out


def screen_hold(
    adds: list[SimPlayer], cat_p_win: dict[str, float], z: dict[int, dict[str, float]], limit: int
) -> list[SimPlayer]:
    weights = {k: 4 * p * (1 - p) for k, p in cat_p_win.items()}
    scored = [(len(a.games) * sum(w * z[a.player_id][k] for k, w in weights.items()), a) for a in adds if a.games]
    scored.sort(key=lambda s: s[0], reverse=True)
    return [add for _, add in scored[:limit]]


def rank_hold(
    categories: list[Category],
    mine: list[SimPlayer],
    league: list[list[SimPlayer]],
    weeks: list[list[int]],
    slot_counts: dict[str, int],
    adds: list[SimPlayer],
    drop_ids: list[int | None],
    z: dict[int, dict[str, float]],
    limit: int = 25,
    dispersion: dict[str, float] | None = None,
) -> HoldRanking:
    """`mine`, every roster in `league` (mine included) and `adds` carry their games over
    `weeks`, a list of the scoring periods in each remaining week."""
    dispersion = dispersion or {}
    days = sorted({d for w in weeks for d in w})
    if not days:
        return HoldRanking(0, 0.0, {}, [], 0)
    average = np.mean([_weekly({d: _day_totals(t, d, slot_counts) for d in days}, weeks) for t in league], axis=0)
    base_days = {d: _day_totals(mine, d, slot_counts) for d in days}
    base_p = weekly_cat_p_win(_weekly(base_days, weeks), average, categories, dispersion)
    base_cats = sum(base_p.values())
    base_mean = {k: float(v.mean()) for k, v in base_p.items()}

    candidates = screen_hold(adds, base_mean, z, limit)
    by_id = {p.player_id: p for p in mine}
    options = []
    for add in candidates:
        for drop_id in drop_ids:
            roster = [p for p in mine if p.player_id != drop_id] + [add]
            changed = set(add.games) | (set(by_id[drop_id].games) if drop_id is not None else set())
            totals = {d: _day_totals(roster, d, slot_counts) if d in changed else base_days[d] for d in days}
            p = weekly_cat_p_win(_weekly(totals, weeks), average, categories, dispersion)
            options.append(
                HoldOption(
                    add_id=add.player_id,
                    drop_id=drop_id,
                    d_cats_per_week=float((sum(p.values()) - base_cats).mean()),
                    cat_d_p_win={k: float((p[k] - base_p[k]).mean()) for k in p},
                )
            )
    options.sort(key=lambda o: o.d_cats_per_week, reverse=True)
    return HoldRanking(len(weeks), float(base_cats.mean()), base_mean, _best_per_add(options), len(candidates))
