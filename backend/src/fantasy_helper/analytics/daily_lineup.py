"""One team's matchup week, player by player and day by day, following the lineup set
on ESPN rather than the simulation's best lineup.

Days already played are box scores; a game counts only if the player was in a
starting slot. From today on, a player in a starting slot with a game is projected
at his per-game rate times that game's opponent factor and his chance of playing;
a game on the bench is projected too but counts for nothing. `missed_starts` is how
many more games the day's best lineup would start (`lineup.assign_starters` over
the players able to play), i.e. games the set lineup leaves on the bench.

Stats are the counting stats plus makes and attempts (STAT_KEYS); percentages are
derived from those.
"""

from dataclasses import dataclass

from fantasy_helper.analytics.lineup import NON_STARTING_SLOTS, assign_starters
from fantasy_helper.analytics.matchup import SimPlayer
from fantasy_helper.domain import STAT_KEYS, PlayerGameStat

# ESPN's lineup order, used to list players: starters first, then bench and IR.
SLOT_ORDER = ["PG", "SG", "SF", "PF", "C", "G", "F", "UT", "BE", "IR"]


@dataclass
class DayCell:
    scoring_period: int
    slot: str | None  # None: not on the team that day, or a past day without a box score
    opponent: str | None  # his NBA team's opponent; None without a game
    counts: bool  # a started game, so it adds to the team's totals
    actual: bool  # a completed day's box score rather than a projection
    stats: dict[str, float]  # keyed by STAT_KEYS; zeros without a game or a projection


@dataclass
class PlayerWeek:
    player_id: int
    cells: list[DayCell]
    totals: dict[str, float]  # counted games only


@dataclass
class TeamWeek:
    days: list[int]
    players: list[PlayerWeek]
    day_totals: list[dict[str, float]]  # counted games only
    totals: dict[str, float]
    starts: list[float]  # started games per day; from today, weighted by the chance of playing
    missed_starts: list[int | None]  # extra games the best lineup would start; None for days played


def _zeros() -> dict[str, float]:
    return dict.fromkeys(STAT_KEYS, 0.0)


def _add(total: dict[str, float], stats: dict[str, float]) -> None:
    for k in STAT_KEYS:
        total[k] += stats.get(k, 0.0)


def _starts(slot: str | None) -> bool:
    return slot is not None and slot not in NON_STARTING_SLOTS


def team_week(
    days: list[int],
    today: int,
    slot_counts: dict[str, int],
    slots: dict[tuple[int, int], str],
    played: list[PlayerGameStat],
    players: dict[int, SimPlayer],
    opponents: dict[int, dict[int, str]],
) -> TeamWeek:
    """`days` are the week's scoring periods and `today` the first one not yet played.
    `slots` is (player_id, day) -> set slot from today on; `played` is the team's box
    scores on earlier days. `players` holds what a projection needs from today on
    (rates, games with opponent factors, P(play), eligible slots); a player without a
    stat line is missing and shown without one. `opponents` is player_id -> {day: NBA
    opponent} over the whole week."""
    box = {(g.player_id, g.scoring_period): g for g in played}
    ids = {pid for pid, _ in slots} | {pid for pid, _ in box}

    def projection(pid: int, day: int) -> dict[str, float]:
        p = players.get(pid)
        if p is None or day not in p.games:
            return _zeros()
        return {k: p.per_game[k] * p.games[day].get(k, 1.0) * p.p_play for k in STAT_KEYS}

    rows = []
    for pid in ids:
        cells, totals = [], _zeros()
        for day in days:
            if day < today:
                g = box.get((pid, day))
                cell = DayCell(
                    day,
                    g.lineup_slot if g else None,
                    opponents.get(pid, {}).get(day) if g else None,
                    counts=g is not None and _starts(g.lineup_slot),
                    actual=True,
                    stats=dict(g.stats) if g else _zeros(),
                )
            else:
                slot = slots.get((pid, day))
                opponent = opponents.get(pid, {}).get(day) if slot is not None else None
                cell = DayCell(
                    day,
                    slot,
                    opponent,
                    counts=opponent is not None and _starts(slot),
                    actual=False,
                    stats=projection(pid, day) if opponent is not None else _zeros(),
                )
            if cell.counts:
                _add(totals, cell.stats)
            cells.append(cell)
        rows.append(PlayerWeek(pid, cells, totals))

    def order(row: PlayerWeek) -> tuple[int, float]:
        upcoming = [c.slot for c in row.cells if c.scoring_period >= today and c.slot is not None]
        rank = SLOT_ORDER.index(upcoming[0]) if upcoming and upcoming[0] in SLOT_ORDER else len(SLOT_ORDER)
        p = players.get(row.player_id)
        return rank, -(p.priority if p else 0.0)

    rows.sort(key=order)

    day_totals, starts, missed = [], [], []
    for i, day in enumerate(days):
        total = _zeros()
        for row in rows:
            if row.cells[i].counts:
                _add(total, row.cells[i].stats)
        day_totals.append(total)
        if day < today:
            starts.append(float(sum(row.cells[i].counts for row in rows)))
            missed.append(None)
            continue
        # Able to play: on the roster outside IR, with a game, and not ruled out.
        able = [
            row.player_id
            for row in rows
            if row.cells[i].opponent is not None
            and row.cells[i].slot not in (None, "IR")
            and (p := players.get(row.player_id)) is not None
            and p.p_play > 0
        ]
        set_starters = [row.player_id for row in rows if row.player_id in able and row.cells[i].counts]
        best = assign_starters([(pid, players[pid].eligible_slots) for pid in able], slot_counts)
        starts.append(sum(players[pid].p_play for pid in set_starters))
        missed.append(max(len(best) - len(set_starters), 0))

    week_totals = _zeros()
    for t in day_totals:
        _add(week_totals, t)
    return TeamWeek(days, rows, day_totals, week_totals, starts, missed)
