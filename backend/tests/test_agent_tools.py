"""The agent's tools on the synthetic league: the same numbers as the API, small results,
strict-mode schemas, and errors the model can act on."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fantasy_helper.agent.tools import (
    MAX_RESULT_CHARS,
    TOOLS,
    TOP_ROWS,
    cats,
    one,
    pct,
    result_text,
    run_tool,
)
from fantasy_helper.api.app import Ctx, SnapshotCache, create_app
from fantasy_helper.config import Settings
from fantasy_helper.db import models as m
from fantasy_helper.ingest.store import store_snapshot
from fantasy_helper.predict import predict_current_week, store_predictions
from tests.league_fixture import MY_TEAM, SEASON, league_snapshot

OPPONENT = 7  # my opponent this week in the fixture


@pytest.fixture(scope="module")
def engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    m.Base.metadata.create_all(engine)
    with Session(engine) as s, s.begin():
        store_snapshot(s, league_snapshot())
    with Session(engine) as s, s.begin():
        store_predictions(s, predict_current_week(s, SEASON, n_sims=200), ingest_run_id=None)
    return engine


@pytest.fixture(scope="module")
def settings():
    return Settings(season=SEASON, my_team_id=MY_TEAM, database_url="sqlite://")


@pytest.fixture(scope="module")
def api(engine, settings):
    with TestClient(create_app(sessionmaker(bind=engine), settings)) as c:
        yield c


@pytest.fixture(scope="module")
def ctx(engine, settings):
    """A context of its own, with its own cache: the tools recompute rather than reuse the API's results."""
    with Session(engine) as s:
        yield Ctx(s, settings, SnapshotCache())


def call(ctx, name, **arguments):
    return run_tool(ctx, name, arguments)


def roster(ctx, team_id):
    """A team's players outside the IR slot, best first."""
    league = ctx.context()
    ids = [r.player_id for r in league.rosters if r.team_id == team_id and r.lineup_slot != "IR"]
    return sorted(ids, key=lambda pid: -league.z[pid].value)


# --- Schemas and the registry ----------------------------------------------------------------


def _strict_problems(schema, path="parameters"):
    """Where a schema breaks OpenAI's strict mode."""
    problems = []
    kinds = schema.get("type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    for key in ("default", "title", "minItems", "maxItems", "oneOf"):
        if key in schema:
            problems.append(f"{path}: has {key}")
    if "enum" in schema and "null" in kinds and None not in schema["enum"]:
        problems.append(f"{path}: nullable enum without null")
    if "object" in kinds:
        if schema.get("additionalProperties") is not False:
            problems.append(f"{path}: additionalProperties isn't false")
        if sorted(schema.get("required", [])) != sorted(schema.get("properties", {})):
            problems.append(f"{path}: not every property is required")
        for name, sub in schema.get("properties", {}).items():
            problems += _strict_problems(sub, f"{path}.{name}")
    if "array" in kinds:
        problems += _strict_problems(schema["items"], f"{path}[]")
    return problems


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
def test_every_schema_is_strict(tool):
    assert tool.parameters["type"] == "object"
    assert _strict_problems(tool.parameters) == []
    assert tool.name.replace("_", "").isalpha() and tool.description


def test_bad_names_and_arguments_come_back_as_errors(ctx):
    assert "No tool" in call(ctx, "get_everything")["error"]
    assert "Unknown arguments for get_team: colour" in call(ctx, "get_team", team=None, colour="red")["error"]
    assert "No team called 'Lakers'" in call(ctx, "get_team", team="Lakers", window=None)["error"]
    assert "Unknown window" in call(ctx, "get_team", team=None, window="forever")["error"]
    assert "Unknown category" in call(ctx, "rank_waivers", focus_category="dunks")["error"]
    assert "isn't played yet" in call(ctx, "get_matchup", week=9)["error"]


def test_every_team_label_a_tool_writes_resolves_to_that_team(ctx):
    rows = call(ctx, "get_power_rankings")["rows"]
    assert any(r["team"].endswith(" (you)") for r in rows)
    for row in rows:
        assert call(ctx, "get_team", team=row["team"], window=None)["team"] == row["team"]


def test_a_player_label_with_its_team_resolves(ctx):
    pid = roster(ctx, MY_TEAM)[0]
    got = call(ctx, "player_lookup", names=[f"Player {pid} (Team {MY_TEAM} (you))"])["players"][0]
    assert got["name"] == f"Player {pid}" and got["fantasy_team"] == f"Team {MY_TEAM} (you)"


def test_missing_optional_arguments_mean_null(ctx):
    assert call(ctx, "get_team")["team"] == f"Team {MY_TEAM} (you)"


# --- Same numbers as the API -----------------------------------------------------------------


def test_matchup_is_the_matchup_page(ctx, api):
    page = api.get("/api/matchup").json()
    got = call(ctx, "get_matchup", team=None, week=None)
    assert (got["team"], got["opponent"]) == (f"Team {MY_TEAM} (you)", f"Team {OPPONENT}")
    assert got["p_win_pct"] == pct(page["p_win"]) and got["expected_categories"]["me"] == cats(page["expected"]["me"])
    assert [x["category"] for x in got["categories"]] == [x["key"] for x in page["categories"]]
    assert [x["p_win_pct"] for x in got["categories"]] == [pct(x["p_win"]) for x in page["categories"]]
    assert {x["label"] for x in got["categories"]} <= {"locked", "swing", "lost"}
    assert next(x for x in got["categories"] if x["category"] == "TO")["lower_is_better"]


def test_a_finished_week_is_its_result(ctx, api):
    page = api.get("/api/matchup", params={"team_id": OPPONENT, "period": 2}).json()
    got = call(ctx, "get_matchup", team=f"Team {OPPONENT}", week=2)
    assert got["final"] and got["score"]["won"] == page["expected"]["me"]
    assert [x["result"] for x in got["categories"]] == [x["result"] for x in page["categories"]]
    fg = next(x for x in got["categories"] if x["category"] == "FG%")
    assert fg["final"]["me"] == pct(page["categories"][0]["now_me"])  # a percentage, in percent


def test_team_is_the_team_page(ctx, api):
    page = api.get("/api/team", params={"team_id": OPPONENT, "window": "season"}).json()
    got = call(ctx, "get_team", team=f"Team {OPPONENT}", window="season")
    assert [p["name"] for p in got["players"]] == [p["player"]["name"] for p in page["players"]]
    assert [p["value"] for p in got["players"]] == [one(p["value"]) for p in page["players"]]
    assert [x["rank"] for x in got["categories"]] == [x["rank"] for x in page["profile"]]
    assert got["punts"] == page["punts"] and got["window"] == "season"
    assert all("z" not in p for p in got["players"])  # per-category z tables are left out


def test_category_profiles_rank_every_team(ctx, api):
    page = api.get("/api/profiles").json()
    got = call(ctx, "get_category_profiles", window=None)
    assert len(got["teams"]) == len(page["teams"]) == 10
    for mine, theirs in zip(got["teams"], page["teams"], strict=True):
        assert mine["rank"] == {x["key"]: x["rank"] for x in theirs["profile"]}


def test_power_rankings_keep_the_page_order(ctx, api):
    page = api.get("/api/power").json()
    got = call(ctx, "get_power_rankings")
    assert [r["team"].removesuffix(" (you)") for r in got["rows"]] == [r["team"]["owner"] for r in page["rows"]]
    assert [r["strength_pct"] for r in got["rows"]] == [pct(r["strength"]) for r in page["rows"]]


def test_waivers_are_the_waivers_page_trimmed(ctx, api):
    page = api.get("/api/waivers").json()
    got = call(ctx, "rank_waivers", team=None, pickups_play_today=None, focus_category=None)
    assert got["opponent"] == f"Team {OPPONENT}"
    assert len(got["stream_this_week"]) == min(TOP_ROWS, len(page["stream"]))
    first, theirs = got["stream_this_week"][0], page["stream"][0]
    assert first["add"]["name"] == theirs["add"]["name"] and first["change_pp"] == pct(theirs["d_p_win"])
    assert got["hold_rest_of_season"]["rows"][0]["add"]["name"] == page["hold"]["rows"][0]["add"]["name"]
    assert got["now"]["p_win_pct"] == pct(page["baseline"]["p_win"])


def test_waivers_can_be_sorted_by_one_category(ctx):
    got = call(ctx, "rank_waivers", team=None, pickups_play_today=None, focus_category="steals")
    assert got["sorted_by"].startswith("change in P(win STL)")
    stl = [row["category_change_pp"].get("STL", 0.0) for row in got["stream_this_week"]]
    assert stl == sorted(stl, reverse=True)


def test_player_lookup_says_where_each_player_is(ctx):
    league = ctx.context()
    mine, free_agent = roster(ctx, MY_TEAM)[0], league.free_agent_ids()[0]
    got = call(ctx, "player_lookup", names=[f"Player {mine}", f"player {free_agent}", "LeBron James"])["players"]
    assert got[0]["fantasy_team"] == f"Team {MY_TEAM} (you)" and got[0]["value"] == one(league.z[mine].value)
    assert got[1]["fantasy_team"] == "free agent" and "slot" not in got[1]
    assert set(got[0]["per_game"]) >= {"PTS", "FG%", "FT%"} and 0 < got[0]["per_game"]["FG%"] < 100
    assert "No player called 'LeBron James'" in got[2]["error"]
    assert "1 to 5" in call(ctx, "player_lookup", names=[f"Player {mine}"] * 6)["error"]


def test_an_injured_player_shows_his_status(ctx):
    league = ctx.context()
    hurt = next(r.player_id for r in league.rosters if r.lineup_slot == "IR")
    got = call(ctx, "player_lookup", names=[f"Player {hurt}"])["players"][0]
    assert got["status"] == "OUT" and got["slot"] == "IR"


def test_a_trade_is_the_trade_page(ctx, api):
    give, get = roster(ctx, MY_TEAM)[-1], roster(ctx, OPPONENT)[-1]
    page = api.get("/api/trade", params={"partner_id": OPPONENT, "give": [give], "get": [get]}).json()
    got = call(ctx, "evaluate_trade", partner=f"Team {OPPONENT}", give=[f"Player {give}"], get=[f"Player {get}"])
    assert got["me"]["gets"][0]["name"] == f"Player {get}" and got["partner"]["team"] == f"Team {OPPONENT}"
    assert got["me"]["change_categories_per_week"] == cats(page["me"]["d_cats_per_week"])
    assert got["partner"]["value_balance"] == one(page["partner"]["d_value"])
    assert got["assumptions"] == page["assumptions"]


def test_a_trade_needs_each_player_on_the_right_team(ctx):
    theirs = roster(ctx, OPPONENT)[0]
    got = call(ctx, "evaluate_trade", partner=f"Team {OPPONENT}", give=[f"Player {theirs}"], get=[f"Player {theirs}"])
    assert "No player called" in got["error"]  # he isn't on my roster


def test_found_trades_are_the_trades_page(ctx, api):
    page = api.get("/api/trades").json()
    got = call(ctx, "find_trades", team=None)
    assert got["deals_found"] == len(page["deals"])
    assert len(got["deals"]) == min(TOP_ROWS, len(page["deals"]))
    if page["deals"]:
        assert got["deals"][0]["my_change_categories_per_week"] == cats(page["deals"][0]["me"]["d_cats_per_week"])


# --- Size ------------------------------------------------------------------------------------


CALLS = [
    ("get_matchup", {}),
    ("get_matchup", {"week": 1}),
    ("get_team", {"window": "last_30"}),
    ("get_category_profiles", {}),
    ("get_power_rankings", {}),
    ("rank_waivers", {}),
    ("find_trades", {}),
]


@pytest.mark.parametrize(("name", "arguments"), CALLS, ids=[f"{n}-{i}" for i, (n, _) in enumerate(CALLS)])
def test_every_result_is_small_json(ctx, name, arguments):
    result = call(ctx, name, **arguments)
    assert "error" not in result
    text = result_text(result)
    assert len(text) < MAX_RESULT_CHARS
    assert json.loads(text) == json.loads(json.dumps(result, default=str))
