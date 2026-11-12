from datetime import datetime, timedelta
from itertools import pairwise

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fantasy_helper.analytics.actuals import week_actuals
from fantasy_helper.analytics.trades import MIN_GAIN
from fantasy_helper.api.app import SnapshotCache, create_app
from fantasy_helper.calendar import load_calendar
from fantasy_helper.config import Settings
from fantasy_helper.db import models as m
from fantasy_helper.ingest.run import IngestBusy
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


def test_matchup_now_is_started_box_scores_plus_todays_lines(client):
    snap = league_snapshot()
    week = load_calendar(SEASON).week(snap.current_matchup_period)
    this_week = [g for g in snap.game_stats if g.scoring_period in week.scoring_periods]
    totals = week_actuals([*this_week, *snap.live_stats])[MY_TEAM]
    completed = week_actuals(this_week)[MY_TEAM]
    cats = {c["key"]: c for c in client.get("/api/matchup").json()["categories"]}
    assert cats["PTS"]["now_me"] == totals["PTS"] > completed["PTS"]  # today's lines so far are in
    assert cats["FG%"]["now_me"] == pytest.approx(totals["FGM"] / totals["FGA"])
    assert cats["FT%"]["now_opp"] is not None


def test_league_lists_the_weeks_so_far(client):
    weeks = client.get("/api/league").json()["weeks"]
    assert [w["matchup_period"] for w in weeks] == [1, 2, 3]
    assert all(a["end"] < b["start"] for a, b in pairwise(weeks))


def test_a_finished_week_is_espn_totals_and_each_categorys_result(client):
    mu = next(x for x in league_snapshot().matchups if x.matchup_period == 2 and x.home_team_id == MY_TEAM)
    body = client.get("/api/matchup", params={"period": 2}).json()
    assert body["final"] and body["matchup_period"] == 2 and body["opponent_id"] == mu.away_team_id
    assert body["n_sims"] == 0 and body["days_left"] == [] and len(body["week_days"]) == 7
    cats = {c["key"]: c for c in body["categories"]}
    assert cats["PTS"]["now_me"] == cats["PTS"]["proj_me"] == mu.home_cats["PTS"]
    assert cats["FG%"]["now_opp"] == pytest.approx(mu.away_cats["FG%"])
    assert all(c["label"] is None for c in cats.values())
    fewer_turnovers = cats["TO"]["now_me"] < cats["TO"]["now_opp"]
    assert cats["TO"]["result"] == ("win" if fewer_turnovers else "loss")  # reversed: fewer wins
    results = [c["result"] for c in cats.values()]
    assert (body["expected"]["me"], body["expected"]["opp"]) == (results.count("win"), results.count("loss"))
    assert body["p_win"] == (1.0 if mu.winner == "HOME" else 0.0)
    other = client.get("/api/matchup", params={"team_id": mu.away_team_id, "period": 2}).json()
    assert other["p_win"] == body["p_loss"] and other["expected"]["me"] == body["expected"]["opp"]


def test_finished_weeks_list_every_matchups_result(client):
    body = client.get("/api/matchups", params={"period": 1}).json()
    assert len(body) == 5 and all(x["final"] for x in body)
    assert all(x["p_home"] + x["p_tie"] + x["p_away"] == 1 for x in body)
    assert all(x["expected_home"] + x["expected_away"] <= 9 for x in body)


def test_a_finished_weeks_lineup_is_its_box_scores(client):
    week = load_calendar(SEASON).week(2)
    games = [g for g in league_snapshot().game_stats if g.scoring_period in week.scoring_periods]
    body = client.get("/api/lineup", params={"period": 2}).json()
    assert body["matchup_period"] == 2 and len(body["days"]) == 7 and all(d["played"] for d in body["days"])
    started = week_actuals(games)[MY_TEAM]
    assert sum(d["actual"]["PTS"] for d in body["days"]) == pytest.approx(started["PTS"])
    for p in body["players"]:
        for cell in p["cells"]:
            assert cell["actual"] and cell["game"] is None and (not cell["slot"] or cell["opponent"])


def test_weeks_not_played_yet_are_rejected(client):
    for path in ("/api/matchups", "/api/matchup", "/api/lineup"):
        assert client.get(path, params={"period": 4}).status_code == 400
        assert client.get(path, params={"period": 0}).status_code == 400
    assert client.get("/api/matchup", params={"period": 3}).json()["final"] is False  # this week is live


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
    pts = sum(d["projected"]["PTS"] for d in body["days"])
    assert pts > 0 and abs(pts - body["totals"]["PTS"]) < 1e-6
    assert client.get("/api/lineup", params={"team_id": 99}).status_code == 404


def test_lineup_shows_todays_games_live(client):
    body = client.get("/api/lineup").json()
    today = [d["played"] for d in body["days"]].index(False)
    cells = [p["cells"][today] for p in body["players"] if p["cells"][today]["game"]]
    states = {c["game"]["state"] for c in cells}
    assert states <= {"pre", "live", "final"} and cells
    for c in cells:
        game = c["game"]
        if game["state"] == "pre":
            assert not c["actual"] and c["stats"] == c["projected"] and game["status"] == ""
        else:
            assert c["actual"] and game["status"] in ("Final", "Q3 6:00")
            # Final: nothing left to add. Live: the rest of the game on top of the line so far.
            grows = c["projected"]["PTS"] >= c["stats"]["PTS"]
            assert c["projected"] == c["stats"] if game["state"] == "final" else grows
    day = body["days"][today]
    started_live = [c for c in cells if c["counts"] and c["actual"]]
    assert abs(day["actual"]["PTS"] - sum(c["stats"]["PTS"] for c in started_live)) < 1e-6


def test_lineup_values_each_game_on_the_single_game_scale(client):
    body = client.get("/api/lineup").json()
    assert body["value_scale"]["sd"] > 0
    for i, day in enumerate(body["days"]):
        cells = [p["cells"][i] for p in body["players"]]
        for c in cells:
            has_game = c["slot"] is not None and c["opponent"] is not None
            assert (c["value"] is not None) == has_game
            if has_game:
                assert c["value"] == pytest.approx(sum(c["value_by_cat"].values()))
        counted = [c for c in cells if c["counts"]]
        assert day["actual_value"] == pytest.approx(sum(c["value"] for c in counted if c["actual"]))
        assert day["projected_value"] == pytest.approx(sum(c["projected_value"] for c in counted))
    assert all(p["usual"] is not None for p in body["players"])


def test_team_page_profiles_the_roster_and_lists_moves(client):
    body = client.get("/api/team").json()
    assert body["team"]["team_id"] == MY_TEAM and body["window"] == "model"
    profile = {p["key"]: p for p in body["profile"]}
    assert len(profile) == 9 and all(1 <= p["rank"] <= 10 for p in profile.values())
    assert set(body["punts"]) == {k for k, p in profile.items() if p["punt"]}
    assert len(body["players"]) == 14 and body["players"][-1]["slot"] == "IR"
    for p in body["players"]:
        assert all(p["z"][k] >= 1.0 for k in p["specialties"]) and len(p["specialties"]) <= 2
    assert [m["transaction_id"] is None for m in body["moves"]] == [False, False, True]  # trades have no id
    assert body["sources"] and body["notes"][0].startswith("Value is a G-score")
    assert [(m["type"], m["kind"]) for m in body["moves"]] == [
        ("ADD", "FREEAGENT"),
        ("DROP", "FREEAGENT"),
        ("TRADE", "TRADE"),
    ]
    season = client.get("/api/team", params={"window": "season"}).json()
    assert season["window"] == "season" and season["players"][0]["per_game"] != body["players"][0]["per_game"]
    assert client.get("/api/team", params={"window": "career"}).status_code == 400
    assert client.get("/api/team", params={"team_id": 99}).status_code == 404


def test_unknown_team_is_404(client):
    assert client.get("/api/matchup", params={"team_id": 99}).status_code == 404


def refresh_client(monkeypatch, fetch, **settings_kw):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    m.Base.metadata.create_all(engine)
    monkeypatch.setattr("fantasy_helper.ingest.run.fetch_snapshot", fetch)
    settings = Settings(season=SEASON, my_team_id=MY_TEAM, database_url="sqlite://", **settings_kw)
    return TestClient(create_app(sessionmaker(bind=engine, expire_on_commit=False), settings))


def test_refresh_runs_the_ingest_one_at_a_time(monkeypatch):
    with refresh_client(monkeypatch, lambda settings, *args: league_snapshot()) as c:
        run = c.post("/api/refresh").json()
        assert run["status"] == "success"
        assert c.get("/api/league").json()["last_ingest"]["id"] == run["id"]
        lock = c.app.state.refresh_lock
        with lock:
            assert c.post("/api/refresh").status_code == 409


def test_refresh_returns_the_last_run_within_the_cooldown(monkeypatch):
    fetches = []
    with refresh_client(
        monkeypatch, lambda settings, *args: fetches.append(1) or league_snapshot(), refresh_cooldown_seconds=60
    ) as c:
        first = c.post("/api/refresh").json()
        assert c.post("/api/refresh").json()["id"] == first["id"] and len(fetches) == 1
        with Session(c.app.state.sessionmaker.kw["bind"]) as s, s.begin():  # an hour later
            s.get(m.IngestRun, first["id"]).finished_at -= timedelta(hours=1)
        assert c.post("/api/refresh").json()["id"] != first["id"] and len(fetches) == 2


def test_refresh_is_busy_while_another_process_ingests(monkeypatch):
    def busy(*args):
        raise IngestBusy("Another ingest is already running")

    with refresh_client(monkeypatch, lambda settings, *args: league_snapshot()) as c:
        monkeypatch.setattr("fantasy_helper.api.app.run_ingest", busy)
        assert c.post("/api/refresh").status_code == 409


def test_failed_refresh_reports_why_and_keeps_the_data(monkeypatch):
    def fail(settings, *args):
        raise ConnectionError("ESPN unreachable")

    with refresh_client(monkeypatch, lambda settings, *args: league_snapshot()) as c:
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


def test_profiles_cover_every_team(client):
    body = client.get("/api/profiles").json()
    assert body["window"] == "model" and len(body["teams"]) == 10
    mine = next(t for t in body["teams"] if t["team"]["team_id"] == MY_TEAM)
    assert mine["profile"] == client.get("/api/team").json()["profile"]  # the Team page's numbers
    for key in (p["key"] for p in mine["profile"]):
        ranks = [next(p["rank"] for p in t["profile"] if p["key"] == key) for t in body["teams"]]
        assert min(ranks) == 1 and max(ranks) <= 10
    assert client.get("/api/profiles", params={"window": "last_7"}).json()["window"] == "last_7"
    assert client.get("/api/profiles", params={"window": "career"}).status_code == 400


def test_power_ranks_every_team_with_the_all_play_record(client):
    body = client.get("/api/power").json()
    rows = body["rows"]
    assert [r["rank"] for r in rows] == list(range(1, 11)) and body["completed_weeks"] == 2
    assert [r["strength"] for r in rows] == sorted((r["strength"] for r in rows), reverse=True)
    assert sum(r["strength"] for r in rows) == pytest.approx(5, abs=0.01)  # every pairing is split between two teams
    for r in rows:  # two completed weeks, nine opponents each
        assert r["all_play"]["wins"] + r["all_play"]["losses"] + r["all_play"]["ties"] == 18
    assert body["first_period"] == 4 and body["notes"]
    for r in rows:  # the record so far plus every regular-season week still to play (this one included)
        record, proj = r["team"], r["projected"]
        assert proj["wins"] >= record["wins"] and proj["losses"] >= record["losses"]
        played = record["wins"] + record["losses"] + record["ties"]
        assert sum(proj.values()) > played


def test_trade_scores_both_sides(client):
    me = [p["player"]["player_id"] for p in client.get("/api/team").json()["players"] if p["slot"] != "IR"]
    them = [p["player"]["player_id"] for p in client.get("/api/team", params={"team_id": 7}).json()["players"]]
    body = client.get("/api/trade", params={"partner_id": 7, "give": me[:2], "get": them[:1]}).json()
    assert [p["player_id"] for p in body["me"]["sends"]] == me[:2] and len(body["me"]["adds"]) == 1
    assert [p["player_id"] for p in body["partner"]["sends"]] == them[:1] and len(body["partner"]["drops"]) == 1
    assert set(body["me"]["cat_d_p_win"]) == {c["key"] for c in client.get("/api/league").json()["categories"]}
    assert body["first_period"] == 4 and body["assumptions"]
    bad = client.get("/api/trade", params={"partner_id": 7, "give": them[:1], "get": me[:1]})
    assert bad.status_code == 400 and "roster" in bad.json()["detail"]


def test_trades_lists_deals_good_for_both_teams(client):
    body = client.get("/api/trades").json()
    assert body["team_id"] == MY_TEAM and body["candidates"] >= body["evaluated"] > 0 and body["assumptions"]
    assert body["deals"]
    for d in body["deals"]:
        assert d["me"]["team_id"] == MY_TEAM and d["partner"]["team_id"] != MY_TEAM
        assert min(d["me"]["d_cats_per_week"], d["partner"]["d_cats_per_week"]) >= MIN_GAIN
    assert client.get("/api/trades", params={"team_id": 99}).status_code == 404
