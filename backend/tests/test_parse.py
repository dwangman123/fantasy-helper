from datetime import date, datetime

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
