from datetime import UTC, date, datetime

from fantasy_helper.calendar import MatchupCalendar
from fantasy_helper.categories import KNOWN_CATEGORIES
from fantasy_helper.espn import parse
from tests.fakes import fake_player, stat_block


def test_stat_lines_keep_components_and_skip_empty_windows():
    p = fake_player(
        stats={
            "2027_projected": stat_block(gp=70, PTS=26.7, FGM=9.1, FGA=20.1, TO=2.8, **{"3PM": 3.4, "FG%": 0.454}),
            "2027_total": {"applied_total": 0, "applied_avg": 0},  # no games yet
            "2026_total": stat_block(gp=50, PTS=99),  # other season: ignored
        }
    )
    lines = parse.parse_stat_lines(p, 2027)
    assert [s.window for s in lines] == ["projected"]
    line = lines[0]
    assert line.gp == 70 and line.per_game["PTS"] == 26.7 and line.per_game["3PM"] == 3.4
    assert "FG%" not in line.per_game  # percentages are derived, never stored
    assert line.per_game["FGM"] == 9.1 and line.per_game["REB"] == 0.0


def test_pro_games_use_scoring_period_dates():
    cal = MatchupCalendar.load(2027)
    p = fake_player(schedule={"1": {"team": "DET", "date": datetime(2026, 10, 20, 16, 0)}})
    [g] = parse.parse_pro_games(p, cal)
    assert (g.pro_team, g.opponent, g.scoring_period, g.game_date) == ("BOS", "DET", 1, date(2026, 10, 20))
    assert parse.parse_pro_games(fake_player(pro_team="FA"), cal) == []


def test_player_drops_bench_and_ir_slots():
    assert parse.parse_player(fake_player()).eligible_slots == ["SF", "F", "UT"]


def test_matchups_parse_category_scores_and_byes():
    cats = [KNOWN_CATEGORIES[0], KNOWN_CATEGORIES[19]]
    schedule = [
        {
            "id": 1,
            "matchupPeriodId": 1,
            "winner": "UNDECIDED",
            "home": {"teamId": 4, "cumulativeScore": {"scoreByStat": {"0": {"score": 210.0}, "19": {"score": 0.48}}}},
            "away": {"teamId": 7, "cumulativeScore": {"scoreByStat": None}},
        },
        {"id": 2, "matchupPeriodId": 20, "playoffTierType": "WINNERS_BRACKET", "home": {"teamId": 1}},
    ]
    played, bye = parse.parse_matchups(schedule, cats)
    assert played.home_cats == {"PTS": 210.0, "FG%": 0.48} and played.away_cats == {}
    assert bye.away_team_id is None and bye.playoff_tier == "WINNERS_BRACKET"


def test_matchup_scores_keep_makes_and_attempts():
    cats = [KNOWN_CATEGORIES[19]]
    by_stat = {"19": {"score": 0.4545}, "13": {"score": 80.0}, "14": {"score": 176.0}, "15": {"score": 44.0}}
    schedule = [{"id": 1, "matchupPeriodId": 1, "home": {"teamId": 4, "cumulativeScore": {"scoreByStat": by_stat}}}]
    [m] = parse.parse_matchups(schedule, cats)
    assert m.home_cats == {"FG%": 0.4545, "FGM": 80.0, "FGA": 176.0, "FTM": 44.0}


def test_lineup_slot_counts_use_slot_names_and_drop_unused():
    # Exactly this league's rosterSettings.lineupSlotCounts (captured 2026-10-23).
    raw = {"0": 1, "1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1, "7": 0, "8": 0, "9": 0, "10": 0}
    raw |= {"11": 3, "12": 3, "13": 1, "14": 0}
    counts = parse.parse_lineup_slot_counts({"lineupSlotCounts": raw})
    assert counts == {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1, "UT": 3, "BE": 3, "IR": 1}


def test_first_tip_is_the_earliest_game_on_that_day():
    early = fake_player(schedule={"4": {"team": "NYK", "date": datetime(2026, 10, 23, 16, 0)}})
    late = fake_player(schedule={"4": {"team": "LAL", "date": datetime(2026, 10, 23, 19, 30)}})
    other_day = fake_player(schedule={"5": {"team": "MIA", "date": datetime(2026, 10, 24, 9, 0)}})
    assert parse.first_tip([late, early, other_day], 4) == datetime(2026, 10, 23, 16, 0).astimezone(UTC)
    assert parse.first_tip([other_day], 4) is None


def raw_player(player_id=7, sp=2, source=0, split=5, **stats):
    by_id = {"0": "PTS", "1": "BLK", "2": "STL", "3": "AST", "6": "REB", "11": "TO", "13": "FGM", "14": "FGA"}
    by_id |= {"15": "FTM", "16": "FTA", "17": "3PM", "40": "MIN"}
    ids = {name: sid for sid, name in by_id.items()}
    entry = {"statSourceId": source, "statSplitTypeId": split, "scoringPeriodId": sp, "proTeamId": 2}
    entry["stats"] = {ids[k]: v for k, v in stats.items()}
    return {"id": player_id, "stats": [entry]}


def test_game_stat_reads_one_day_box_score():
    p = raw_player(PTS=18.0, FGM=4.0, FGA=13.0, MIN=36.0, **{"3PM": 1.0})
    g = parse.parse_game_stat(p, 2, team_id=4, lineup_slot_id=12)
    assert (g.player_id, g.scoring_period, g.pro_team, g.minutes) == (7, 2, "BOS", 36.0)
    assert g.stats["PTS"] == 18.0 and g.stats["3PM"] == 1.0 and g.stats["REB"] == 0.0
    assert (g.team_id, g.lineup_slot) == (4, "BE")


def test_game_stat_skips_other_days_projections_and_did_not_play():
    assert parse.parse_game_stat(raw_player(sp=3, PTS=10.0, MIN=20.0), 2) is None
    assert parse.parse_game_stat(raw_player(source=1, PTS=10.0, MIN=20.0), 2) is None
    assert parse.parse_game_stat(raw_player(PTS=None, MIN=None), 2) is None


def test_opponent_stats_read_the_opponent_blocks_and_map_team_codes():
    names = ["avgPoints", "avgFieldGoalsMade", "avgFieldGoalsAttempted", "avgThreePointFieldGoalsMade"]
    names += ["avgTurnovers", "avgAssists", "avgFreeThrowsMade", "avgFreeThrowsAttempted"]
    data = {
        "categories": [
            {"name": "general", "names": ["avgRebounds", "gamesPlayed"]},
            {"name": "defensive", "names": ["avgSteals", "avgBlocks"]},
            {"name": "offensive", "names": names},
        ],
        "teams": [
            {
                "team": {"abbreviation": "GS"},
                "categories": [
                    {"name": "general", "displayName": "Own General", "values": [39.0, 1.0]},
                    {"name": "general", "displayName": "Opponent General", "values": [49.0, 1.0]},
                    {"name": "defensive", "displayName": "Opponent Defensive", "values": [7.0, 5.0]},
                    {"name": "offensive", "displayName": "Own Offensive", "values": [113.0] * 8},
                    {
                        "name": "offensive",
                        "displayName": "Opponent Offensive",
                        "values": [121, 45, 90, 15, 17, 28, 20, 25],
                    },
                ],
            }
        ],
    }
    [o] = parse.parse_opponent_stats(data)
    assert (o.pro_team, o.gp) == ("GSW", 1.0)
    assert o.per_game["REB"] == 49.0 and o.per_game["PTS"] == 121.0 and o.per_game["STL"] == 7.0
    assert o.per_game["FGA"] == 90.0 and o.per_game["TO"] == 17.0
