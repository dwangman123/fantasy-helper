import pytest

from fantasy_helper.analytics.team_profile import team_profiles
from fantasy_helper.categories import KNOWN_CATEGORIES
from fantasy_helper.domain import STAT_KEYS, RosterEntry, StatLine

PTS, TO, FG = KNOWN_CATEGORIES[0], KNOWN_CATEGORIES[11], KNOWN_CATEGORIES[19]


def line(player_id, **per_game):
    return StatLine(player_id, "projected", gp=10, mpg=30, per_game={k: 0.0 for k in STAT_KEYS} | per_game)


def roster(team_id, *player_ids, slot="UT"):
    return [RosterEntry(team_id, p, slot, "DRAFT") for p in player_ids]


def test_team_fg_pct_is_total_makes_over_attempts():
    # 1/2 (50%) and 9/10 (90%): team FG% is 10/12, not the 70% average of percentages
    profiles = team_profiles(roster(1, 10, 11), [line(10, FGM=1, FGA=2), line(11, FGM=9, FGA=10)], [FG])
    assert profiles[1].totals["FG%"] == pytest.approx(10 / 12)


def test_ir_is_excluded_and_bench_counts():
    rosters = roster(1, 10) + roster(1, 11, slot="BE") + roster(1, 12, slot="IR")
    profiles = team_profiles(rosters, [line(10, PTS=20), line(11, PTS=10), line(12, PTS=30)], [PTS])
    assert profiles[1].totals["PTS"] == 30


def test_rostered_player_without_a_line_is_reported_not_zeroed_silently():
    profiles = team_profiles(roster(1, 10, 11), [line(10, PTS=20)], [PTS])
    assert profiles[1].totals["PTS"] == 20
    assert profiles[1].missing_player_ids == [11]


def test_ranks_handle_ties_and_reversed_cats():
    rosters = roster(1, 10) + roster(2, 20) + roster(3, 30)
    lines = [line(10, PTS=20, TO=1), line(20, PTS=20, TO=3), line(30, PTS=10, TO=2)]
    profiles = team_profiles(rosters, lines, [PTS, TO])
    assert [profiles[t].ranks["PTS"] for t in (1, 2, 3)] == [1, 1, 3]
    assert [profiles[t].ranks["TO"] for t in (1, 2, 3)] == [1, 3, 2]  # fewest turnovers ranks first


def test_clear_punt_is_flagged_and_middling_team_is_not():
    # Five teams at 20 PTS, one at 19, one at 2: only the 2 is far below the league.
    rosters = [e for t in range(1, 8) for e in roster(t, t * 10)]
    points = [20, 20, 20, 20, 20, 19, 2]
    profiles = team_profiles(rosters, [line(t * 10, PTS=p) for t, p in zip(range(1, 8), points, strict=True)], [PTS])
    assert profiles[7].punts == ["PTS"]
    assert profiles[6].punts == [] and profiles[6].ranks["PTS"] == 6


def test_punt_threshold_is_configurable():
    rosters = roster(1, 10) + roster(2, 20)
    lines = [line(10, PTS=20), line(20, PTS=10)]  # league z is exactly +1 / -1
    assert team_profiles(rosters, lines, [PTS], punt_z=-0.5)[2].punts == ["PTS"]
    assert team_profiles(rosters, lines, [PTS], punt_z=-1.5)[2].punts == []
