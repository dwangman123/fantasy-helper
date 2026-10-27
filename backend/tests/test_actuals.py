from fantasy_helper.analytics.actuals import category_values, espn_mismatches, week_actuals
from fantasy_helper.categories import KNOWN_CATEGORIES
from fantasy_helper.domain import STAT_KEYS, Matchup, PlayerGameStat

CATS = list(KNOWN_CATEGORIES.values())


def line(pid, day, team_id, slot, **stats):
    return PlayerGameStat(pid, day, "BOS", 30, dict.fromkeys(STAT_KEYS, 0.0) | stats, team_id, slot)


def test_only_started_games_count_toward_a_teams_totals():
    games = [
        line(1, 1, 4, "PG", PTS=20, FGM=8, FGA=16),
        line(2, 1, 4, "BE", PTS=30),  # benched
        line(3, 1, 4, "IR", PTS=10),
        line(4, 1, None, None, PTS=40),  # a free agent
        line(1, 2, 4, "UT", PTS=5, FGM=2, FGA=4),
    ]
    totals = week_actuals(games)
    assert set(totals) == {4} and totals[4]["PTS"] == 25 and (totals[4]["FGM"], totals[4]["FGA"]) == (10, 20)


def test_percentages_come_from_makes_and_attempts():
    values = category_values({"FGM": 10, "FGA": 20, "PTS": 25}, CATS)
    assert values["FG%"] == 0.5 and values["PTS"] == 25
    assert values["FT%"] is None  # no attempts yet
    assert values["STL"] == 0


def test_espn_totals_are_checked_against_box_scores():
    completed = [line(1, 1, 4, "PG", PTS=20), line(2, 1, 7, "C", PTS=10)]
    live = [line(1, 2, 4, "PG", PTS=6)]
    agree = Matchup(1, 1, 4, 7, "UNDECIDED", "NONE", {"PTS": 26.0}, {"PTS": 10.0})  # ESPN counts today's so far
    assert espn_mismatches([agree], completed, live) == []
    corrected = Matchup(1, 1, 4, 7, "UNDECIDED", "NONE", {"PTS": 20.0}, {"PTS": 11.0})
    [warning] = espn_mismatches([corrected], completed, live)
    assert warning.startswith("Team 7") and "'PTS': 1.0" in warning
    first_day = Matchup(1, 1, 4, 7, "UNDECIDED", "NONE", {"PTS": 0.0}, {"PTS": 0.0})
    assert espn_mismatches([first_day], completed, live) == []
