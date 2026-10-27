from fantasy_helper.analytics.daily_lineup import team_week
from fantasy_helper.analytics.matchup import SimPlayer
from fantasy_helper.domain import STAT_KEYS, LiveGame, PlayerGameStat

SLOTS = {"PG": 1, "UT": 1, "BE": 2, "IR": 1}


def rates(pts: float) -> dict[str, float]:
    return dict.fromkeys(STAT_KEYS, 0.0) | {"PTS": pts}


def week():
    """Day 1 is played; days 2-3 follow the set lineup. A starts at PG both days; B sits
    on the bench (day-to-day) and C at UT, which wastes day 2, when C has no game."""
    players = {
        1: SimPlayer(1, ["PG", "UT"], rates(20), games={2: {}, 3: {"PTS": 1.1}}, priority=3),
        2: SimPlayer(2, ["UT"], rates(10), games={2: {}}, p_play=0.5, priority=2),
        3: SimPlayer(3, ["UT"], rates(8), games={3: {}}, priority=1),
    }
    slots = {(1, 2): "PG", (1, 3): "PG", (2, 2): "BE", (2, 3): "BE", (3, 2): "UT", (3, 3): "UT"}
    played = [
        PlayerGameStat(1, 1, "BOS", 34, rates(25), team_id=4, lineup_slot="PG"),
        PlayerGameStat(4, 1, "PHO", 20, rates(12), team_id=4, lineup_slot="BE"),  # since dropped
    ]
    opponents = {1: {1: "LAL", 2: "BOS", 3: "NYK"}, 2: {2: "MIA"}, 3: {3: "DEN"}, 4: {1: "DAL"}}
    return team_week([1, 2, 3], 2, SLOTS, slots, played, players, opponents)


def test_only_started_games_count():
    w = week()
    assert [t["PTS"] for t in w.day_projected] == [25, 20, 20 * 1.1 + 8]
    assert [t["PTS"] for t in w.day_actual] == [25, 0, 0]
    assert w.totals["PTS"] == 25 + 20 + 22 + 8
    by_id = {p.player_id: p for p in w.players}
    benched = by_id[2].cells[1]
    assert benched.slot == "BE" and not benched.counts and benched.stats["PTS"] == 5.0  # 10 x P(play) 0.5
    assert not by_id[4].cells[0].counts and by_id[4].cells[0].actual
    assert by_id[3].cells[1].opponent is None and not by_id[3].cells[1].counts


def test_flags_games_left_on_the_bench():
    w = week()
    assert w.missed_starts == [None, 1, 0]  # day 2: B could start at UT instead of C
    assert w.starts == [1.0, 1.0, 2.0]


def test_players_listed_in_lineup_order():
    assert [p.player_id for p in week().players] == [1, 3, 2, 4]


def test_today_shows_the_line_so_far_and_projects_the_rest():
    """Day 2 is today. A's game is live with a quarter left; C's game is over."""
    a = SimPlayer(1, ["PG", "UT"], rates(20), games={2: dict.fromkeys(STAT_KEYS, 0.25)}, priority=3)
    c = SimPlayer(3, ["UT"], rates(8), games={}, priority=1)  # final: no game left to project
    slots = {(1, 2): "PG", (3, 2): "UT"}
    live = {
        1: PlayerGameStat(1, 2, "BOS", 30, rates(15), 4, "PG"),
        3: PlayerGameStat(3, 2, "DEN", 25, rates(9), 4, "UT"),
    }
    games = {1: LiveGame("BOS", None, "live", 4, "6:00", 0.25), 3: LiveGame("DEN", None, "final", 4, "0.0", 0.0)}
    opponents = {1: {2: "LAL"}, 3: {2: "MIA"}}
    w = team_week([1, 2], 2, SLOTS, slots, [], {1: a, 3: c}, opponents, live, games)
    by_id = {p.player_id: p for p in w.players}
    a_cell, c_cell = by_id[1].cells[1], by_id[3].cells[1]
    assert a_cell.actual and a_cell.stats["PTS"] == 15 and a_cell.projected["PTS"] == 15 + 20 * 0.25
    assert c_cell.actual and c_cell.stats["PTS"] == c_cell.projected["PTS"] == 9
    assert a_cell.game.state == "live" and c_cell.game.state == "final"
    assert w.day_actual[1]["PTS"] == 24 and w.day_projected[1]["PTS"] == 24 + 5
