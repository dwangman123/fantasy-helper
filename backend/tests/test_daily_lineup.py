from fantasy_helper.analytics.daily_lineup import team_week
from fantasy_helper.analytics.matchup import SimPlayer
from fantasy_helper.domain import STAT_KEYS, PlayerGameStat

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
    assert [t["PTS"] for t in w.day_totals] == [25, 20, 20 * 1.1 + 8]
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
