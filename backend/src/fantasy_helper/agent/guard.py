"""The chat's spending cap, checked before each turn starts.

Two limits over the last 24 hours: the dollars spent (`agent_daily_budget_usd`), and the messages
one visitor sent (`agent_visitor_daily_messages`). The budget is the real protection: a visitor
is told apart only by IP address, which a determined one can change.

Each turn reserves its worst-case cost in `agent_usage` before it starts and is settled to its
real cost when it ends (`settle`). A turn that never reports back (killed at the Lambda timeout)
keeps its reservation; one cut off mid-step is charged one model call's worst case for each step
whose usage never came. Either way the cap never undercounts. Two turns
checked at the same moment could each pass; on Postgres a transaction-level advisory lock
makes the check and the reservation one step.
"""

import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from fantasy_helper.config import Settings
from fantasy_helper.db import models as m

log = logging.getLogger("fantasy_helper.agent")

WINDOW = timedelta(hours=24)
LOCK_KEY = 0x46480002  # the ingest's is ...01
UNKNOWN_VISITOR = "unknown"


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def worst_step_usd(settings: Settings) -> float:
    """The most one model call can cost: the input ceiling and the output limit."""
    tokens_usd = settings.agent_reserve_input_tokens * (settings.agent_input_price or 0.0)
    tokens_usd += settings.agent_max_output_tokens * (settings.agent_output_price or 0.0)
    return tokens_usd / 1_000_000


def worst_case_usd(settings: Settings) -> float:
    """The most one turn can cost: every step at its worst."""
    return settings.agent_max_steps * worst_step_usd(settings)


# --- Who's asking ----------------------------------------------------------------------------


def visitor_ip(headers: Mapping[str, str], client_host: str | None) -> str:
    """The visitor's IP address, from the headers a proxy adds.

    Checked on the deployed API on 2026-11-18 (`header_shape`'s log lines):
      - Through the site, Vercel's proxy makes the request, so the connecting address is
        Vercel's. Vercel sends the visitor's address in x-vercel-forwarded-for, x-real-ip and
        X-Forwarded-For (one entry), overwriting any value the visitor sent.
      - Straight to the function URL, X-Forwarded-For is the caller's own header passed through
        unchanged (AWS appends nothing), or the connecting address when there is none.
    So Vercel's headers come first. Someone calling the function URL directly can send any of
    these headers and get past the per-visitor limit, but not the budget."""
    lower = {k.lower(): v for k, v in headers.items()}
    for name in ("x-vercel-forwarded-for", "x-real-ip"):
        if value := lower.get(name, "").split(",")[0].strip():
            return value
    if first := lower.get("x-forwarded-for", "").split(",")[0].strip():
        return first
    return client_host or UNKNOWN_VISITOR


def visitor_key(ip: str, salt: str) -> str:
    """A salted hash: no address is stored, and an unsalted IPv4 hash could be reversed."""
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()


def header_shape(headers: Mapping[str, str]) -> dict[str, Any]:
    """Which forwarding headers a request carried and how many X-Forwarded-For entries, never
    their values: logged so the visitor rule can be checked against real traffic."""
    lower = {k.lower(): v for k, v in headers.items()}
    names = ("x-forwarded-for", "x-vercel-forwarded-for", "x-real-ip", "x-amzn-request-context", "forwarded")
    shape: dict[str, Any] = {name: name in lower for name in names}
    shape["xff_entries"] = len([x for x in lower.get("x-forwarded-for", "").split(",") if x.strip()])
    if context := lower.get("x-amzn-request-context"):
        try:
            source = json.loads(context).get("http", {}).get("sourceIp")
        except ValueError:
            source = None
        xff = [x.strip() for x in lower.get("x-forwarded-for", "").split(",") if x.strip()]
        shape["source_ip_is_last_xff"] = bool(source and xff and xff[-1] == source)
    return shape


# --- The cap ---------------------------------------------------------------------------------


def _since(now: datetime) -> datetime:
    return now - WINDOW


def spent_usd(session: Session, now: datetime) -> float:
    """Settled turns at their cost, running ones at their reservation."""
    total = session.scalar(
        select(func.coalesce(func.sum(m.AgentUsage.cost_usd), 0.0)).where(m.AgentUsage.created_at >= _since(now))
    )
    return float(total or 0.0)


def visitor_messages(session: Session, visitor: str, now: datetime) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(m.AgentUsage)
            .where(m.AgentUsage.visitor == visitor, m.AgentUsage.created_at >= _since(now))
        )
        or 0
    )


def reserve(Sessions: sessionmaker, settings: Settings, visitor: str, now: datetime | None = None) -> int:
    """Check both limits and reserve this turn's worst case; the new row's id. Raises Refused:
    429 over a limit, 503 when one turn could cost more than the whole budget. Uses a session of
    its own, committed before the turn starts."""
    now = now or datetime.now(UTC)
    worst = worst_case_usd(settings)
    budget = settings.agent_daily_budget_usd
    if worst > budget:
        log.error(
            "agent off: one turn could cost $%.4f, over the $%.2f daily budget; raise FH_AGENT_DAILY_BUDGET_USD "
            "or lower FH_AGENT_MAX_STEPS, FH_AGENT_MAX_OUTPUT_TOKENS or FH_AGENT_RESERVE_INPUT_TOKENS",
            worst,
            budget,
        )
        raise Refused(503, "The chat isn't set up yet.")
    with Sessions.begin() as session:
        if session.get_bind().dialect.name == "postgresql":  # held until this transaction ends
            session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})
        spent = spent_usd(session, now)
        if spent + worst > budget:
            log.warning("agent refused: $%.4f spent in 24 h, budget $%.2f", spent, budget)
            raise Refused(429, "The chat has reached its daily spending limit. Please try again tomorrow.")
        if visitor_messages(session, visitor, now) >= settings.agent_visitor_daily_messages:
            raise Refused(
                429,
                f"You've sent {settings.agent_visitor_daily_messages} messages in the last 24 hours, the most "
                "allowed. Please try again tomorrow.",
            )
        row = m.AgentUsage(
            created_at=now,
            visitor=visitor,
            model=settings.agent_model or "",
            status="running",
            reserved_usd=worst,
            cost_usd=worst,
            usage_known=False,
            steps=0,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
        )
        session.add(row)
        session.flush()
        return row.id


def settle(Sessions: sessionmaker, row_id: int, summary: dict[str, Any], step_usd: float) -> None:
    """Record a finished turn from its usage event, in a session of its own. The cost is what its
    reported tokens cost, plus `step_usd` (one model call's worst case) for each step whose usage
    never came, such as one cut off when the visitor left; never more than the reservation. With
    no cost at all, the reservation stands."""
    with Sessions.begin() as session:
        row = session.get(m.AgentUsage, row_id)
        if row is None:
            return
        reported, unreported = summary.get("cost_usd"), int(summary.get("unreported_steps", 0))
        row.status = summary.get("status", "error")
        row.usage_known = reported is not None and unreported == 0
        if reported is None:
            row.cost_usd = row.reserved_usd
        else:
            row.cost_usd = min(row.reserved_usd, float(reported) + unreported * step_usd)
        row.steps = int(summary.get("steps", 0))
        row.tools = list(summary.get("tools", []))
        row.input_tokens = int(summary.get("input_tokens", 0))
        row.cached_input_tokens = int(summary.get("cached_input_tokens", 0))
        row.output_tokens = int(summary.get("output_tokens", 0))
        row.reasoning_tokens = int(summary.get("reasoning_tokens", 0))
        row.seconds = summary.get("seconds")
