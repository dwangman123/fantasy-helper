from datetime import UTC, date, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from fantasy_helper.categories import KNOWN_CATEGORIES
from fantasy_helper.db import models as m
from fantasy_helper.domain import LeagueSnapshot, Matchup, Player, ProGame, RosterEntry, StatLine, Team
from fantasy_helper.ingest.store import store_snapshot
from fantasy_helper.predict import p_play, predict_current_week, store_predictions

SLOTS = {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1, "UT": 3, "BE": 3, "IR": 1}


def snapshot():
    per_game = {k: 2.0 for k in m.STAT_COLUMNS}
    return LeagueSnapshot(
        league_id=1,
        season=2027,
        name="Test League",
        categories=list(KNOWN_CATEGORIES.values()),
        current_matchup_period=1,
        current_scoring_period=4,  # week 1 is scoring periods 1-6
        teams=[Team(4, "Mine", "ME", 0, 0, 0), Team(7, "Them", "TH", 0, 0, 0)],
        players=[Player(10, "A", "BOS", "SF", ["SF", "UT"], "ACTIVE"), Player(11, "B", "LAL", "C", ["C"], "OUT")],
        rosters=[RosterEntry(4, 10, "SF", "DRAFT"), RosterEntry(7, 11, "C", "DRAFT")],
        stat_lines=[StatLine(10, "projected", 70, 36, per_game), StatLine(11, "projected", 60, 30, per_game)],
        pro_games=[ProGame("BOS", sp, "DET", date(2026, 10, 19 + sp)) for sp in (2, 4, 5)]
        + [ProGame("LAL", 4, "GSW", date(2026, 10, 23))],
        matchups=[Matchup(1, 1, 4, 7, "UNDECIDED", "NONE", {"PTS": 10.0}, {"PTS": 30.0})],
        matchup_ids={},
        fetched_at=datetime.now(UTC),
        lineup_slot_counts=SLOTS,
        scoring_type="H2H_MOST_CATEGORIES",
    )


def test_predicts_the_current_week_from_stored_data_and_appends_rows():
    engine = create_engine("sqlite://")
    m.Base.metadata.create_all(engine)
    with Session(engine) as s, s.begin():
        store_snapshot(s, snapshot())
    with Session(engine) as s, s.begin():
        week = predict_current_week(s, 2027, n_sims=500)
        assert week.days_left == [4, 5, 6]
        [(mu, pred)] = week.results
        # BOS plays days 4 and 5 (day 2 is past); the LAL player is OUT.
        assert pred.expected_games == {"home": 2.0, "away": 0.0}
        assert pred.projected_totals["away"]["PTS"] == 30.0
        assert store_predictions(s, week, ingest_run_id=None, warnings=["w"]) == 1
    with Session(engine) as s, s.begin():
        store_predictions(s, predict_current_week(s, 2027, n_sims=100), ingest_run_id=None)
        assert s.scalar(select(func.count()).select_from(m.MatchupPrediction)) == 2  # appended, not replaced


def test_injury_status_sets_chance_of_playing():
    assert [p_play(s) for s in ("ACTIVE", None, "DAY_TO_DAY", "OUT", "SUSPENSION")] == [1.0, 1.0, 0.5, 0.0, 0.0]
