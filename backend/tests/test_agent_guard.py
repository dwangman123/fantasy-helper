"""The chat's spending cap: reservations, the two limits, settling, and who a visitor is."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fantasy_helper.agent.guard import (
    Refused,
    header_shape,
    reserve,
    settle,
    spent_usd,
    visitor_ip,
    visitor_key,
    worst_case_usd,
)
from fantasy_helper.agent.loop import Message
from fantasy_helper.agent.routes import _events
from fantasy_helper.api.app import create_app
from fantasy_helper.config import Settings
from fantasy_helper.db import models as m
from fantasy_helper.ingest.store import store_snapshot
from tests.fakes import ScriptedModel, answer, calls
from tests.league_fixture import MY_TEAM, SEASON, league_snapshot

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def cap_settings(**kw):
    """Prices of $1 in and $5 out per million tokens: a turn's worst case is
    2 steps × (10,000 × $1 + 1,000 × $5) / 1M = $0.03."""
    defaults = {
        "season": SEASON,
        "my_team_id": MY_TEAM,
        "database_url": "sqlite://",
        "openai_api_key": "test-key",
        "agent_model": "scripted",
        "agent_input_price": 1.0,
        "agent_cached_input_price": 0.1,
        "agent_output_price": 5.0,
        "agent_max_steps": 2,
        "agent_reserve_input_tokens": 10_000,
        "agent_max_output_tokens": 1000,
        "agent_daily_budget_usd": 0.10,
        "agent_visitor_daily_messages": 3,
    }
    return Settings(**(defaults | kw))


@pytest.fixture
def Sessions():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    m.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def rows(Sessions):
    with Sessions() as s:
        return list(s.scalars(select(m.AgentUsage).order_by(m.AgentUsage.id)))


# --- The worst case --------------------------------------------------------------------------


def test_a_turns_worst_case_is_every_step_at_the_ceilings():
    assert worst_case_usd(cap_settings()) == pytest.approx(0.03)
    assert worst_case_usd(cap_settings(agent_max_steps=6)) == pytest.approx(0.09)


def test_a_turn_dearer_than_the_budget_turns_the_chat_off(Sessions, caplog):
    with pytest.raises(Refused) as err:
        reserve(Sessions, cap_settings(agent_daily_budget_usd=0.02), "v", NOW)
    assert err.value.status == 503 and "over the $0.02 daily budget" in caplog.text
    assert rows(Sessions) == []


# --- Reserving and the limits ----------------------------------------------------------------


def test_a_turn_reserves_its_worst_case(Sessions):
    row_id = reserve(Sessions, cap_settings(), "v", NOW)
    (row,) = rows(Sessions)
    assert row.id == row_id and row.status == "running" and row.visitor == "v"
    assert row.reserved_usd == row.cost_usd == pytest.approx(0.03) and not row.usage_known


def test_running_turns_count_against_the_budget(Sessions):
    settings = cap_settings()
    for visitor in ("a", "b", "c"):  # $0.09 reserved, none settled
        reserve(Sessions, settings, visitor, NOW)
    with pytest.raises(Refused) as err:
        reserve(Sessions, settings, "d", NOW)  # $0.09 + $0.03 > $0.10
    assert err.value.status == 429 and "daily spending limit" in err.value.message


def test_settled_turns_count_at_their_real_cost(Sessions):
    settings = cap_settings()
    for visitor in ("a", "b", "c"):
        row_id = reserve(Sessions, settings, visitor, NOW)
        settle(Sessions, row_id, {"status": "ok", "known": True, "cost_usd": 0.001, "steps": 2})
    reserve(Sessions, settings, "d", NOW)  # $0.003 spent, so there's room
    with Sessions() as s:
        assert spent_usd(s, NOW) == pytest.approx(0.003 + 0.03)


def test_spending_older_than_a_day_drops_out(Sessions):
    settings = cap_settings()
    for visitor in ("a", "b", "c"):
        reserve(Sessions, settings, visitor, NOW - timedelta(hours=25))
    reserve(Sessions, settings, "d", NOW)
    with Sessions() as s:
        assert spent_usd(s, NOW) == pytest.approx(0.03)


def test_one_visitor_is_limited_to_their_messages(Sessions):
    settings = cap_settings(agent_daily_budget_usd=10.0)
    for _ in range(3):
        reserve(Sessions, settings, "a", NOW)
    with pytest.raises(Refused) as err:
        reserve(Sessions, settings, "a", NOW)
    assert err.value.status == 429 and "3 messages in the last 24 hours" in err.value.message
    reserve(Sessions, settings, "b", NOW)  # someone else can still ask
    reserve(Sessions, settings, "a", NOW + timedelta(hours=24, seconds=1))  # and they can the next day


# --- Settling --------------------------------------------------------------------------------


def test_known_usage_replaces_the_reservation(Sessions):
    row_id = reserve(Sessions, cap_settings(), "v", NOW)
    summary = {
        "status": "ok",
        "known": True,
        "cost_usd": 0.0006,
        "steps": 2,
        "tools": ["rank_waivers"],
        "input_tokens": 5670,
        "cached_input_tokens": 1465,
        "output_tokens": 296,
        "reasoning_tokens": 0,
        "seconds": 16.6,
    }
    settle(Sessions, row_id, summary)
    (row,) = rows(Sessions)
    assert (row.status, row.cost_usd, row.reserved_usd, row.usage_known) == ("ok", 0.0006, pytest.approx(0.03), True)
    assert row.tools == ["rank_waivers"] and row.input_tokens == 5670 and row.seconds == 16.6


@pytest.mark.parametrize("summary", [{"status": "timeout", "known": False, "cost_usd": None}, {"status": "closed"}])
def test_unknown_usage_keeps_the_reservation(Sessions, summary):
    row_id = reserve(Sessions, cap_settings(), "v", NOW)
    settle(Sessions, row_id, summary)
    (row,) = rows(Sessions)
    assert row.status == summary["status"] and row.cost_usd == pytest.approx(0.03) and not row.usage_known


# --- Who's asking ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "client", "ip"),
    [
        (
            {"x-vercel-forwarded-for": "203.0.113.7", "x-forwarded-for": "1.1.1.1, 76.76.21.1"},
            "127.0.0.1",
            "203.0.113.7",
        ),
        ({"X-Real-IP": "203.0.113.8", "X-Forwarded-For": "1.1.1.1"}, "127.0.0.1", "203.0.113.8"),
        ({"x-forwarded-for": "203.0.113.9, 76.76.21.1"}, "127.0.0.1", "203.0.113.9"),
        ({"x-forwarded-for": " "}, "127.0.0.1", "127.0.0.1"),
        ({}, None, "unknown"),
    ],
)
def test_visitor_ip(headers, client, ip):
    assert visitor_ip(headers, client) == ip


def test_visitor_keys_are_salted_hashes():
    key = visitor_key("203.0.113.7", "salt")
    assert len(key) == 64 and "203" not in key
    assert key == visitor_key("203.0.113.7", "salt") != visitor_key("203.0.113.7", "other")


def test_header_shape_never_holds_an_address():
    context = json.dumps({"http": {"sourceIp": "76.76.21.1"}})
    headers = {"x-forwarded-for": "203.0.113.7, 76.76.21.1", "x-amzn-request-context": context}
    shape = header_shape(headers)
    assert shape["x-forwarded-for"] and not shape["x-vercel-forwarded-for"] and shape["xff_entries"] == 2
    assert shape["source_ip_is_last_xff"] is True
    assert "203.0.113.7" not in str(shape) and "76.76.21.1" not in str(shape)


# --- Through the endpoint --------------------------------------------------------------------


@pytest.fixture
def league(Sessions):
    with Sessions.begin() as s:
        store_snapshot(s, league_snapshot())
    return Sessions


def app(Sessions, model, **settings):
    return TestClient(create_app(Sessions, cap_settings(**settings), agent_model=lambda s: model))


def ask(client, ip="203.0.113.7"):
    return client.post(
        "/api/agent", json={"messages": [{"role": "user", "content": "hi"}]}, headers={"x-forwarded-for": ip}
    )


def test_a_turn_is_settled_at_its_real_cost(league):
    model = ScriptedModel(calls(("get_power_rankings", {})), answer("Team 3."))
    assert ask(app(league, model)).status_code == 200
    (row,) = rows(league)
    assert row.status == "ok" and row.usage_known and row.tools == ["get_power_rankings"]
    # 2,000 in at $1 and 150 out at $5, per million.
    assert row.cost_usd == pytest.approx((2000 * 1 + 150 * 5) / 1e6) and row.reserved_usd == pytest.approx(0.03)


def test_over_the_limit_is_a_429_before_the_model_is_asked(league):
    model = ScriptedModel(*[answer("ok") for _ in range(4)])
    client = app(league, model)
    assert [ask(client).status_code for _ in range(4)] == [200, 200, 200, 429]
    assert "3 messages" in ask(client).json()["detail"] and len(model.requests) == 3
    assert ask(client, ip="198.51.100.2").status_code == 200  # another visitor isn't blocked by this one
    assert len(model.requests) == 4


def test_an_exhausted_budget_refuses_everyone(league):
    for visitor in ("a", "b", "c"):
        reserve(league, cap_settings(), visitor, datetime.now(UTC))  # $0.09 reserved, never settled
    model = ScriptedModel(answer("never"))
    response = ask(app(league, model), ip="198.51.100.3")
    assert response.status_code == 429 and "daily spending limit" in response.json()["detail"]
    assert model.requests == []


def test_a_turn_closed_by_the_visitor_keeps_its_reservation(league):
    settings = cap_settings()
    row_id = reserve(league, settings, "v", datetime.now(UTC))
    state = app(league, ScriptedModel(answer("one two three four"))).app.state
    stream = _events(state, state.agent_model_factory, [Message("user", "hi")], row_id)
    assert '"start"' in next(stream) and '"text"' in next(stream)
    stream.close()  # what Starlette does when the visitor leaves
    (row,) = rows(league)
    assert row.status == "closed" and not row.usage_known and row.cost_usd == pytest.approx(0.03)


def test_a_model_that_cant_be_made_costs_nothing(league):
    def broken(settings):
        raise RuntimeError("no network")

    client = TestClient(create_app(league, cap_settings(), agent_model=broken))
    assert ask(client).status_code == 200
    (row,) = rows(league)
    assert row.status == "error" and row.cost_usd == 0.0
