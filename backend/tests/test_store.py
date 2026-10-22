from datetime import UTC, date, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from fantasy_helper.categories import KNOWN_CATEGORIES
from fantasy_helper.db import models as m
from fantasy_helper.domain import LeagueSnapshot, Player, ProGame, RosterEntry, StatLine, Team
from fantasy_helper.ingest.store import store_snapshot


def snapshot(roster_team=4):
    per_game = {k: 1.0 for k in m.STAT_COLUMNS}
    return LeagueSnapshot(
        league_id=1,
        season=2027,
        name="Test League",
        categories=list(KNOWN_CATEGORIES.values()),
        current_matchup_period=1,
        current_scoring_period=1,
        teams=[Team(4, "Mine", "ME", 0, 0, 0), Team(7, "Them", "TH", 0, 0, 0)],
        players=[Player(10, "A", "BOS", "SF", ["SF"], "ACTIVE"), Player(11, "B", "LAL", "C", ["C"], None)],
        rosters=[RosterEntry(roster_team, 10, "SF", "DRAFT")],
        stat_lines=[StatLine(10, "projected", 70, 36, per_game), StatLine(11, "projected", 60, 20, per_game)],
        pro_games=[ProGame("BOS", 1, "DET", date(2026, 10, 20))],
        matchups=[],
        matchup_ids={},
        fetched_at=datetime.now(UTC),
    )


def test_store_is_idempotent_and_replaces_snapshot():
    engine = create_engine("sqlite://")
    m.Base.metadata.create_all(engine)
    with Session(engine) as s, s.begin():
        store_snapshot(s, snapshot())
    with Session(engine) as s, s.begin():
        counts = store_snapshot(s, snapshot(roster_team=7))  # player 10 traded to team 7
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(m.StatLine)) == 2
        assert s.scalar(select(func.count()).select_from(m.Player)) == 2
        assert s.scalars(select(m.RosterEntry.team_id)).all() == [7]
        assert s.get(m.StatLine, (2027, 10, "projected")).tpm == 1.0
    assert counts["rostered"] == 1
