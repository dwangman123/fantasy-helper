"""One team's matchup week, player by player and day by day, following the lineup set
on ESPN rather than the simulation's best lineup.

Days already played are box scores; a game counts only if the player was in a
starting slot. From today on, a player in a starting slot with a game is projected
at his per-game rate times that game's opponent factor and his chance of playing;
a game on the bench is projected too but counts for nothing. Today is live: once a
game tips, a cell shows the player's line so far, and its projected final adds his
rate over the part of the game still to play (the SimPlayer's factors for today
are already scaled down to that part). `missed_starts` is how many more games the
day's best lineup would start (`lineup.assign_starters` over the players able to
play), i.e. games the set lineup leaves on the bench.

Stats are the counting stats plus makes and attempts (STAT_KEYS); percentages are
derived from those.
"""

from dataclasses import dataclass

from fantasy_helper.analytics.lineup import NON_STARTING_SLOTS, assign_starters
from fantasy_helper.analytics.matchup import SimPlayer
from fantasy_helper.domain import STAT_KEYS, LiveGame, PlayerGameStat

# ESPN's lineup order, used to list players: starters first, then bench and IR.
SLOT_ORDER = ["PG", "SG", "SF", "PF", "C", "G", "F", "UT", "BE", "IR"]


@dataclass
class DayCell:
    scoring_period: int
    slot: str | None  # None: not on the team that day, or a past day without a box score
    opponent: str | None  # his NBA team's opponent; None without a game
    counts: bool  # a started game, so it adds to the team's totals
    actual: bool  # a box score (a completed day's, or today's so far) rather than a projection
    stats: dict[str, float]  # what to show: the box score, or the projection before tip; keyed by STAT_KEYS
    projected: dict[str, float]  # the game's projected final: equals `stats` except during a live game
    game: LiveGame | None = None  # today's game state; None on other days
    start: float = 0.0  # games this cell starts: 1 once played, else his chance of playing; 0 if it doesn't count


@dataclass
class PlayerWeek:
    player_id: int
    cells: list[DayCell]
    totals: dict[str, float]  # counted games' projected finals


@dataclass
class TeamWeek:
    days: list[int]
    players: list[PlayerWeek]
    day_actual: list[dict[str, float]]  # counted box scores per day (today's so far); zeros ahead
    day_projected: list[dict[str, float]]  # counted projected finals per day
    totals: dict[str, float]  # the week's projected final
    starts: list[float]  # started games per day; ahead of a game, weighted by his chance of playing
    missed_starts: list[int | None]  # extra games the best lineup would start; None for days played


def _zeros() -> dict[str, float]:
    return dict.fromkeys(STAT_KEYS, 0.0)


def _add(total: dict[str, float], stats: dict[str, float]) -> None:
    for k in STAT_KEYS:
        total[k] += stats.get(k, 0.0)


def _sum(*parts: dict[str, float]) -> dict[str, float]:
    total = _zeros()
    for part in parts:
        _add(total, part)
    return total


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
    live: dict[int, PlayerGameStat] | None = None,
    games_today: dict[int, LiveGame] | None = None,
) -> TeamWeek:
    """`days` are the week's scoring periods and `today` the first one not yet played.
    `slots` is (player_id, day) -> set slot from today on; `played` is the team's box
    scores on earlier days. `players` holds what a projection needs from today on
    (rates, games with opponent factors, P(play), eligible slots); a player without a
    stat line is missing and shown without one. `opponents` is player_id -> {day: NBA
    opponent} over the whole week. `live` is player_id -> his line so far today and
    `games_today` player_id -> his NBA team's game today."""
    live, games_today = live or {}, games_today or {}
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
                stats = dict(g.stats) if g else _zeros()
                cell = DayCell(
                    day,
                    g.lineup_slot if g else None,
                    opponents.get(pid, {}).get(day) if g else None,
                    counts=g is not None and _starts(g.lineup_slot),
                    actual=True,
                    stats=stats,
                    projected=stats,
                    start=1.0 if g is not None and _starts(g.lineup_slot) else 0.0,
                )
            else:
                slot = slots.get((pid, day))
                opponent = opponents.get(pid, {}).get(day) if slot is not None else None
                game = games_today.get(pid) if day == today and opponent is not None else None
                rest = projection(pid, day) if opponent is not None else _zeros()
                p_play = players[pid].p_play if pid in players else 0.0
                if game is not None and game.state != "pre":
                    # Tipped: his line so far, plus the rest of the game (gone once it's final).
                    so_far = dict(live[pid].stats) if pid in live else _zeros()
                    stats, projected, actual = so_far, _sum(so_far, rest), True
                    start = 1.0 if pid in live else 0.0 if game.state == "final" else p_play
                else:
                    stats, projected, actual, start = rest, rest, False, p_play
                counts = opponent is not None and _starts(slot)
                cell = DayCell(
                    day,
                    slot,
                    opponent,
                    counts=counts,
                    actual=actual,
                    stats=stats,
                    projected=projected,
                    game=game,
                    start=start if counts else 0.0,
                )
            if cell.counts:
                _add(totals, cell.projected)
            cells.append(cell)
        rows.append(PlayerWeek(pid, cells, totals))

    def order(row: PlayerWeek) -> tuple[int, float]:
        upcoming = [c.slot for c in row.cells if c.scoring_period >= today and c.slot is not None]
        rank = SLOT_ORDER.index(upcoming[0]) if upcoming and upcoming[0] in SLOT_ORDER else len(SLOT_ORDER)
        p = players.get(row.player_id)
        return rank, -(p.priority if p else 0.0)

    rows.sort(key=order)

    day_actual, day_projected, starts, missed = [], [], [], []
    for i, day in enumerate(days):
        counted = [row.cells[i] for row in rows if row.cells[i].counts]
        day_actual.append(_sum(*(c.stats for c in counted if c.actual)))
        day_projected.append(_sum(*(c.projected for c in counted)))
        starts.append(sum(c.start for c in counted))
        if day < today:
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
        missed.append(max(len(best) - len(set_starters), 0))

    return TeamWeek(days, rows, day_actual, day_projected, _sum(*day_projected), starts, missed)
