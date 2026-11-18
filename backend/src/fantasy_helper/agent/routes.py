"""`POST /api/agent`: one chat turn, streamed as server-sent events.

The browser keeps the conversation and sends it whole each turn (just the text: the model
calls its tools again rather than seeing earlier results). Only the latest turns go to the model
(`recent`), so a long chat costs no more per turn than a short one. Every HTTP status is decided before
the stream starts, since a streamed response can't change it later: 422 for a malformed body,
400 for a conversation that doesn't end with a question or a `team_id` not in the league, 503
when the agent isn't configured, 404 when no league is stored, 429 over the spending cap
(guard.py), which reserves the turn's worst-case cost here and settles it when the turn ends.
Once streaming, problems arrive as an `error` event, and `done` always comes last. See loop.py
for the events.
"""

import json
import logging
from collections.abc import Callable, Iterator
from typing import Any, Literal

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from fantasy_helper.agent.guard import (
    Refused,
    header_shape,
    reserve,
    settle,
    visitor_ip,
    visitor_key,
    worst_step_usd,
)
from fantasy_helper.agent.llm import ChatModel, OpenAIModel
from fantasy_helper.agent.loop import Message, run_turn
from fantasy_helper.api.app import Ctx
from fantasy_helper.config import Settings
from fantasy_helper.db import queries as q

log = logging.getLogger("fantasy_helper.agent")

MAX_QUESTION_CHARS = 2000  # a question the visitor types
MAX_ANSWER_CHARS = 8000  # an earlier answer sent back: longer, but the visitor can edit it too
MAX_MESSAGES_IN = 200  # refused above this
RECENT_MESSAGES = 20  # sent to the model
RECENT_CHARS = 16_000
STREAM_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}

router = APIRouter(prefix="/api")


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Only these two: a visitor can't add system or developer instructions.
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)

    @model_validator(mode="after")
    def _question_length(self) -> "MessageIn":
        if self.role == "user" and len(self.content) > MAX_QUESTION_CHARS:
            raise ValueError(f"A question can be at most {MAX_QUESTION_CHARS} characters")
        return self


class AgentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[MessageIn] = Field(min_length=1, max_length=MAX_MESSAGES_IN)
    # The team the questions are about ("me", "my team"), picked in the page; the site owner's
    # (settings.my_team_id) when it's not sent.
    team_id: int | None = Field(default=None, gt=0)


def recent(messages: list[MessageIn]) -> list[MessageIn]:
    """The latest messages within RECENT_MESSAGES and RECENT_CHARS, starting with a question;
    the last question always goes, whatever its length."""
    kept, chars = [messages[-1]], len(messages[-1].content)
    for m in reversed(messages[:-1]):
        if len(kept) == RECENT_MESSAGES or chars + len(m.content) > RECENT_CHARS:
            break
        kept.append(m)
        chars += len(m.content)
    kept.reverse()
    while kept[0].role != "user":
        kept.pop(0)
    return kept


def openai_model(settings: Settings) -> ChatModel:
    return OpenAIModel(
        api_key=settings.openai_api_key or "",
        model=settings.agent_model or "",
        max_output_tokens=settings.agent_max_output_tokens,
        reasoning_effort=settings.agent_reasoning_effort,
    )


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'), default=str)}\n\n"


def public(event: dict[str, Any]) -> dict[str, Any]:
    """What the browser gets: tool results stay on the server."""
    return {k: v for k, v in event.items() if k != "result"}


@router.post("/agent")
def agent(body: AgentIn, request: Request):
    state = request.app.state
    settings: Settings = state.settings
    # Before any refusal, so it's logged while the chat is still off: what the visitor rule can rely on.
    log.info("agent request headers: %s", header_shape(request.headers))
    if not settings.agent_configured:
        return JSONResponse(status_code=503, content={"detail": "The chat isn't set up yet."})
    if body.messages[-1].role != "user":
        return JSONResponse(status_code=400, content={"detail": "The conversation must end with a question."})
    with state.sessionmaker() as session:
        Ctx(session, settings, state.cache)  # raises LookupError (404) with no league stored
        if body.team_id is not None:
            if body.team_id not in {t.team_id for t in q.load_teams(session, settings.season)}:
                return JSONResponse(status_code=400, content={"detail": "That team isn't in the league."})
            # The turn's "me": the prompt, tool labels and team lookups all read my_team_id.
            settings = settings.model_copy(update={"my_team_id": body.team_id})
    ip = visitor_ip(request.headers, request.client.host if request.client else None)
    salt = settings.agent_visitor_salt or settings.openai_api_key or ""
    try:
        row_id = reserve(state.sessionmaker, settings, visitor_key(ip, salt))
    except Refused as exc:
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})
    messages = [Message(m.role, m.content) for m in recent(body.messages)]
    factory: Callable[[Settings], ChatModel] = state.agent_model_factory
    return TurnResponse(
        _events(state, settings, factory, messages, row_id), media_type="text/event-stream", headers=STREAM_HEADERS
    )


class TurnResponse(StreamingResponse):
    """A streamed turn that is closed when the response ends, however it ends. Starlette reads a
    plain generator in a thread and, when the visitor disconnects, stops reading without closing it,
    so the turn would never settle. Closing it runs the turn's own cleanup: the model response is
    closed and the turn's usage recorded."""

    def __init__(self, content: Iterator[str], **kwargs: Any):
        super().__init__(content, **kwargs)
        self._content = content

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):  # it may be finishing because it was cancelled
                await anyio.to_thread.run_sync(_close, self._content)


def _close(content: Iterator[str]) -> None:
    close = getattr(content, "close", None)
    if close is not None:
        try:
            close()
        except Exception:
            log.exception("agent stream didn't close cleanly")


def _events(
    state: Any, settings: Settings, factory: Callable[[Settings], ChatModel], messages: list[Message], row_id: int
) -> Iterator[str]:
    # Once the turn has produced an event it settles itself (run_turn's on_finish). Before that
    # nothing has been spent: the model is only called inside the turn, and the stream can only be
    # closed at one of these yields. So a stream that ends sooner settles at no cost.
    turn_started = False
    status = "closed"
    try:
        yield sse({"type": "start"})  # at once, so the page knows it's working
        model = factory(settings)
        with state.sessionmaker() as session:  # its own: the request's is gone once streaming starts
            c = Ctx(session, settings, state.cache)
            step_usd = worst_step_usd(settings)
            turn = run_turn(
                c, model, messages, on_finish=lambda summary: settle(state.sessionmaker, row_id, summary, step_usd)
            )
            try:
                for event in turn:
                    turn_started = True
                    yield sse(public(event))
            finally:
                turn.close()  # settles the turn now, if the visitor left, while the session is open
    except Exception:
        log.exception("agent stream failed")
        status = "error"
        yield sse({"type": "error", "message": "Something went wrong on our side."})
        yield sse({"type": "done"})
    finally:
        if not turn_started:
            settle(state.sessionmaker, row_id, {"status": status, "cost_usd": 0.0}, 0.0)
