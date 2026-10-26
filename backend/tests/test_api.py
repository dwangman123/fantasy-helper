from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fantasy_helper.api.app import SnapshotCache, create_app
from fantasy_helper.config import Settings
from fantasy_helper.db import models as m
from fantasy_helper.ingest.store import store_snapshot
from fantasy_helper.predict import predict_current_week, store_predictions
from tests.league_fixture import MY_TEAM, SEASON, league_snapshot


@pytest.fixture(scope="module")
def client():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    m.Base.metadata.create_all(engine)
    with Session(engine) as s, s.begin():
        store_snapshot(s, league_snapshot())
    with Session(engine) as s, s.begin():
        store_predictions(s, predict_current_week(s, SEASON, n_sims=200), ingest_run_id=None)
    settings = Settings(season=SEASON, my_team_id=MY_TEAM, database_url="sqlite://")
    with TestClient(create_app(sessionmaker(bind=engine), settings)) as c:
        yield c


def test_league_describes_the_current_week(client):
    body = client.get("/api/league").json()
    assert body["my_team_id"] == MY_TEAM and body["week"]["matchup_period"] == 3
    assert [c["key"] for c in body["categories"]][:2] == ["FG%", "FT%"]
    assert len(body["teams"]) == 10 and body["last_ingest"] is None


def test_matchups_cover_every_team_this_week(client):
    body = client.get("/api/matchups").json()
    assert len(body) == 5
    assert all(abs(x["p_home"] + x["p_tie"] + x["p_away"] - 1) < 1e-9 for x in body)


def test_matchup_is_from_my_side_with_history(client):
    body = client.get("/api/matchup").json()
    assert body["team_id"] == MY_TEAM and body["opponent_id"] == 7
    assert len(body["categories"]) == 9 and len(body["history"]) == 1
    assert {c["label"] for c in body["categories"]} <= {"locked", "swing", "lost"}
    other = client.get("/api/matchup", params={"team_id": 7}).json()
    assert other["p_win"] == body["p_loss"] and other["opponent_id"] == MY_TEAM


def test_timestamps_are_sent_as_utc(client):
    # SQLite returns stored timestamps naive; the API must still send their UTC offset.
    created = client.get("/api/matchup").json()["history"][0]["created_at"]
    assert datetime.fromisoformat(created).utcoffset() == timedelta(0)


def test_waivers_rank_stream_and_hold(client):
    body = client.get("/api/waivers").json()
    assert body["stream"] and body["hold"]["rows"] and body["assumptions"]
    ranks = [r["d_p_win"] for r in body["stream"]]
    assert ranks == sorted(ranks, reverse=True)
    assert body["first_day"] > client.get("/api/league").json()["today"]
    same_day = client.get("/api/waivers", params={"same_day": True}).json()
    assert same_day["first_day"] == client.get("/api/league").json()["today"]


def test_lineup_counts_started_games_only(client):
    body = client.get("/api/lineup").json()
    assert body["team_id"] == MY_TEAM and len(body["days"]) == 7
    assert [d["played"] for d in body["days"]] == [True] * 3 + [False] * 4
    assert len(body["players"]) == 14  # 13 roster spots plus IR
    for p in body["players"]:
        for cell in p["cells"]:
            assert not cell["counts"] or (cell["slot"] not in ("BE", "IR") and cell["opponent"])
    pts = sum(d["totals"]["PTS"] for d in body["days"])
    assert pts > 0 and abs(pts - body["totals"]["PTS"]) < 1e-6
    assert client.get("/api/lineup", params={"team_id": 99}).status_code == 404


def test_unknown_team_is_404(client):
    assert client.get("/api/matchup", params={"team_id": 99}).status_code == 404


def refresh_client(monkeypatch, fetch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    m.Base.metadata.create_all(engine)
    monkeypatch.setattr("fantasy_helper.ingest.run.fetch_snapshot", fetch)
    settings = Settings(season=SEASON, my_team_id=MY_TEAM, database_url="sqlite://")
    return TestClient(create_app(sessionmaker(bind=engine, expire_on_commit=False), settings))


def test_refresh_runs_the_ingest_one_at_a_time(monkeypatch):
    with refresh_client(monkeypatch, lambda settings, days: league_snapshot()) as c:
        run = c.post("/api/refresh").json()
        assert run["status"] == "success"
        assert c.get("/api/league").json()["last_ingest"]["id"] == run["id"]
        lock = c.app.state.refresh_lock
        with lock:
            assert c.post("/api/refresh").status_code == 409


def test_failed_refresh_reports_why_and_keeps_the_data(monkeypatch):
    def fail(settings, days):
        raise ConnectionError("ESPN unreachable")

    with refresh_client(monkeypatch, lambda settings, days: league_snapshot()) as c:
        c.post("/api/refresh")
        before = c.get("/api/league").json()
        monkeypatch.setattr("fantasy_helper.ingest.run.fetch_snapshot", fail)
        r = c.post("/api/refresh")
        assert r.status_code == 502 and "ESPN unreachable" in r.json()["detail"]
        after = c.get("/api/league").json()
        assert after["last_ingest"]["status"] == "failed" and after["data_as_of"] == before["data_as_of"]


def test_cache_evicts_the_oldest_entry():
    cache, calls = SnapshotCache(size=2), []
    for key in ["a", "b", "a", "c", "b"]:
        cache.get(key, lambda k=key: calls.append(k) or k)
    assert calls == ["a", "b", "c", "b"]  # "a" was reused, then "b" evicted by "c"
