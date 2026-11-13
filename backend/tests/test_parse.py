from datetime import UTC, date, datetime
from types import SimpleNamespace

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


def test_owner_names_capitalize_and_split_shared_first_names():
    def team(team_id, first=None, last=None, name="Team"):
        owners = [{"firstName": first, "lastName": last, "displayName": "ESPNFAN123"}] if first else []
        return SimpleNamespace(team_id=team_id, team_name=f" {name} ", owners=owners)

    teams = [team(1, "den", "wang"), team(2, "Sam", "Lee"), team(3, "sam", "Park"), team(4, name="Jalen Brunson")]
    assert parse.owner_names(teams) == {1: "Den", 2: "Sam L.", 3: "Sam P.", 4: "Jalen Brunson"}


def test_pro_games_use_scoring_period_dates():
    cal = MatchupCalendar.load(2027)
    p = fake_player(schedule={"1": {"team": "DET", "date": datetime(2026, 10, 20, 16, 0)}})
    [g] = parse.parse_pro_games(p, cal)
    assert (g.pro_team, g.opponent, g.scoring_period, g.game_date) == ("BOS", "DET", 1, date(2026, 10, 20))
    assert parse.parse_pro_games(fake_player(pro_team="FA"), cal) == []


def test_daily_lineups_name_each_slot():
    entries = [{"playerId": 11, "lineupSlotId": 0}, {"playerId": 12, "lineupSlotId": 12}]
    teams = [{"id": 4, "roster": {"entries": entries}}]
    slots = parse.parse_daily_lineups(teams, 9)
    assert [(d.team_id, d.player_id, d.scoring_period, d.lineup_slot) for d in slots] == [
        (4, 11, 9, "PG"),
        (4, 12, 9, "BE"),
    ]


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


def test_tips_are_each_teams_game_start_on_that_day():
    early = fake_player(pro_team="NYK", schedule={"4": {"team": "BOS", "date": datetime(2026, 10, 23, 16, 0)}})
    late = fake_player(pro_team="LAL", schedule={"4": {"team": "GSW", "date": datetime(2026, 10, 23, 19, 30)}})
    other_day = fake_player(pro_team="MIA", schedule={"5": {"team": "ORL", "date": datetime(2026, 10, 24, 9, 0)}})
    tips = parse.tips([late, early, other_day], 4)
    assert tips == {
        "NYK": datetime(2026, 10, 23, 16, 0).astimezone(UTC),
        "LAL": datetime(2026, 10, 23, 19, 30).astimezone(UTC),
    }


def scoreboard_event(state, period=0, clock=0.0, display="0.0", teams=(("2", "BOS"), ("4", "CHI"))):
    return {
        "date": "2026-10-26T23:30Z",
        "status": {"clock": clock, "displayClock": display, "period": period, "type": {"state": state}},
        "competitions": [{"competitors": [{"team": {"id": i, "abbreviation": a}} for i, a in teams]}],
    }


def test_live_games_read_state_and_share_left_from_the_scoreboard():
    board = {
        "events": [
            scoreboard_event("pre"),
            scoreboard_event("in", period=3, clock=360.0, display="6:00", teams=(("27", "WSH"), ("21", "PHX"))),
            scoreboard_event("in", period=5, clock=100.0, display="1:40", teams=(("9", "GS"), ("13", "LAL"))),
            scoreboard_event("post", period=4, teams=(("5", "CLE"), ("16", "MIN"))),
        ]
    }
    games = {g.pro_team: g for g in parse.parse_live_games(board)}
    assert set(games) == {"BOS", "CHI", "WAS", "PHO", "GSW", "LAL", "CLE", "MIN"}  # fantasy codes, by team id
    assert (games["BOS"].state, games["BOS"].remaining) == ("pre", 1.0)
    assert games["BOS"].tip_at == datetime(2026, 10, 26, 23, 30, tzinfo=UTC)
    assert (games["WAS"].state, games["WAS"].period, games["WAS"].clock) == ("live", 3, "6:00")
    assert games["WAS"].remaining == 0.375  # 30 of 48 minutes played
    assert (games["LAL"].state, games["LAL"].remaining) == ("live", 0.0)  # overtime: nothing projected
    assert (games["MIN"].state, games["MIN"].remaining) == ("final", 0.0)


def test_live_games_estimated_from_tips_when_the_scoreboard_is_down():
    tip = datetime(2026, 10, 26, 23, 0, tzinfo=UTC)
    games = {
        g.pro_team: g
        for g in parse.estimate_live_games({"BOS": tip, "LAL": tip + parse.GAME_LENGTH}, tip + parse.GAME_LENGTH / 2)
    }
    assert (games["BOS"].state, games["BOS"].remaining) == ("live", 0.5)
    assert (games["LAL"].state, games["LAL"].remaining) == ("pre", 1.0)
    [final] = parse.estimate_live_games({"BOS": tip}, tip + parse.GAME_LENGTH)
    assert (final.state, final.remaining) == ("final", 0.0)


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


# Trimmed mTransactions2 rows as this public league returns them.
TRANSACTIONS = [
    {
        "id": "3100bccf",
        "type": "FREEAGENT",
        "status": "EXECUTED",
        "scoringPeriodId": 7,
        "proposedDate": 1793055751757,
        "processDate": None,
        "items": [
            {"type": "ADD", "playerId": 4278562, "fromTeamId": 0, "toTeamId": 6},
            {"type": "DROP", "playerId": 5041955, "fromTeamId": 6, "toTeamId": 0},
        ],
    },
    {
        "id": "w1",
        "type": "WAIVER",
        "status": "CANCELED",
        "scoringPeriodId": 7,
        "items": [{"type": "ADD", "playerId": 1}],
    },
    {"id": "ce5bf78c", "type": "TRADE_ACCEPT", "scoringPeriodId": 1, "items": []},  # no status at all
    {"id": "d1", "type": "DRAFT", "status": "EXECUTED", "items": [{"type": "DRAFT", "playerId": 2, "toTeamId": 4}]},
    {"id": "r1", "type": "ROSTER", "status": "EXECUTED", "items": [{"type": "LINEUP", "playerId": 3}]},
]


def test_moves_are_executed_adds_and_drops_with_names():
    moves = parse.parse_moves(TRANSACTIONS, {4278562: "New Guy"})
    assert [(x.type, x.player_id, x.team_id, x.kind) for x in moves] == [
        ("ADD", 4278562, 6, "FREEAGENT"),
        ("DROP", 5041955, 6, "FREEAGENT"),
    ]
    assert moves[0].player_name == "New Guy" and moves[1].player_name is None
    assert moves[0].at == datetime.fromtimestamp(1793055751.757, UTC)  # proposedDate when nothing later is set


def test_acquisition_dates_come_from_roster_entries():
    teams = [{"id": 4, "roster": {"entries": [{"playerId": 7, "acquisitionDate": 1792540741520}, {"playerId": 8}]}}]
    assert parse.parse_acquired_at(teams) == {7: datetime.fromtimestamp(1792540741.52, UTC)}


def _injury(href, **details):
    return {"athlete": {"links": [{"href": href}]}, "details": details}


def test_injuries_read_the_player_id_return_date_and_season_flag():
    data = {
        "injuries": [
            {
                "injuries": [
                    _injury(
                        "https://www.espn.com/nba/player/_/id/3975/stephen-curry",
                        type="Abdomen",
                        returnDate="2026-12-12",
                        fantasyStatus={"abbreviation": "OUT"},
                    ),
                    _injury(
                        "https://www.espn.com/nba/player/_/id/4433255/x",
                        type="Knee",
                        detail="Surgery",
                        returnDate="2027-07-01",
                        fantasyStatus={"abbreviation": "OFS"},
                    ),
                    _injury("https://www.espn.com/nba/player/_/id/77/y", type="Hamstring", detail="Not Specified"),
                    _injury("https://www.espn.com/nba/team/_/name/gs"),  # no player id: skipped
                ]
            }
        ]
    }
    curry, season, undated = parse.parse_injuries(data)
    assert (curry.player_id, curry.return_date, curry.out_for_season, curry.note) == (
        3975,
        date(2026, 12, 12),
        False,
        "Abdomen",
    )
    assert (season.player_id, season.out_for_season, season.note) == (4433255, True, "Knee surgery")
    assert (undated.player_id, undated.return_date, undated.note) == (77, None, "Hamstring")
