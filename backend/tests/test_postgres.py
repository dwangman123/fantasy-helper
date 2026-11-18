"""What only a real Postgres checks (foreign keys, column limits, JSON, timestamps with time
zones, sequences, advisory locks). Runs when FH_TEST_POSTGRES_URL names a scratch database,
as in CI; its tables are dropped and recreated."""

import os
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fantasy_helper.api.app import create_app
from fantasy_helper.config import Settings
from fantasy_helper.db import models as m
from fantasy_helper.db.copy import copy_database
from fantasy_helper.ingest.run import IngestBusy, exclusive, run
from fantasy_helper.ingest.store import store_snapshot
from fantasy_helper.predict import predict_current_week, store_predictions
from tests.league_fixture import MY_TEAM, SEASON, league_snapshot, stored_league

URL = os.environ.get("FH_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="FH_TEST_POSTGRES_URL is not set")


@pytest.fixture
def pg():
    engine = create_engine(Settings(database_url=URL).database_url)
    m.Base.metadata.drop_all(engine)
    m.Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def settings() -> Settings:
    return Settings(season=SEASON, my_team_id=MY_TEAM, database_url=URL)


def test_snapshot_replaces_and_predictions_append(pg):
    for seed in (0, 1):  # the second store replaces the first under enforced foreign keys
        with Session(pg) as s, s.begin():
            store_snapshot(s, league_snapshot(seed=seed))
    with Session(pg) as s, s.begin():
        assert store_predictions(s, predict_current_week(s, SEASON, n_sims=200), ingest_run_id=None) > 0


def test_api_reads_from_postgres(pg):
    with Session(pg) as s, s.begin():
        store_snapshot(s, league_snapshot())
    with TestClient(create_app(sessionmaker(bind=pg, expire_on_commit=False), settings())) as c:
        for path in ("/api/league", "/api/matchups", "/api/matchup", "/api/lineup", "/api/team", "/api/power"):
            assert c.get(path).status_code == 200, path


def test_copied_ids_continue_after_the_copy(pg):
    source = stored_league()
    with Session(source) as s, s.begin():
        s.add(m.IngestRun(id=41, season=SEASON, started_at=datetime.now(UTC), status="success"))
    copy_database(source, pg)
    with Session(pg) as s, s.begin():
        run_row = m.IngestRun(season=SEASON, started_at=datetime.now(UTC), status="running")
        s.add(run_row)
        s.flush()
        assert run_row.id == 42


def test_ingest_is_busy_while_another_holds_the_lock(pg, monkeypatch):
    monkeypatch.setattr("fantasy_helper.ingest.run.fetch_snapshot", lambda settings, *args: league_snapshot())
    Session = sessionmaker(bind=pg, expire_on_commit=False)
    with exclusive(Session):
        with pytest.raises(IngestBusy):
            run(settings(), Session)
    assert run(settings(), Session).status == "success"  # released


def test_the_spending_cap_on_postgres(pg):
    """The 24-hour window with time-zone-aware timestamps, the JSON tools column, and the
    advisory lock around the check and the reservation."""
    from datetime import timedelta

    from fantasy_helper.agent.guard import Refused, reserve, settle, spent_usd

    Sessions = sessionmaker(bind=pg, expire_on_commit=False)
    capped = settings().model_copy(
        update={
            "openai_api_key": "k",
            "agent_model": "m",
            "agent_input_price": 1.0,
            "agent_cached_input_price": 0.1,
            "agent_output_price": 5.0,
            "agent_max_steps": 2,
            "agent_reserve_input_tokens": 10_000,
            "agent_max_output_tokens": 1000,
            "agent_daily_budget_usd": 0.10,
        }
    )
    now = datetime.now(UTC)
    reserve(Sessions, capped, "old", now - timedelta(hours=25))  # outside the window
    first = reserve(Sessions, capped, "a", now)
    settle(Sessions, first, {"status": "ok", "cost_usd": 0.002, "tools": ["get_matchup"]}, 0.015)
    reserve(Sessions, capped, "b", now)
    reserve(Sessions, capped, "c", now)
    with Sessions() as s:
        assert spent_usd(s, now) == pytest.approx(0.002 + 0.03 + 0.03)
        row = s.get(m.AgentUsage, first)
        assert row.tools == ["get_matchup"] and row.created_at.tzinfo is not None
    with pytest.raises(Refused):
        reserve(Sessions, capped, "d", now)  # $0.062 + $0.03 is under $0.10, but a 4th would not be
        reserve(Sessions, capped, "e", now)
