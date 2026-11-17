"""`POST /api/agent`: one chat turn, streamed as server-sent events.

The browser keeps the conversation and sends it whole each turn (just the text: the model
calls its tools again rather than seeing earlier results). Only the latest turns go to the model
(`recent`), so a long chat costs no more per turn than a short one. Every HTTP status is decided before
the stream starts, since a streamed response can't change it later: 422 for a malformed body,
400 for a conversation that doesn't end with a question, 503 when the agent isn't configured,
404 when no league is stored. Once streaming, problems arrive as an `error` event, and `done`
always comes last. See loop.py for the events.
"""

import json
import logging
from collections.abc import Callable, Iterator
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from fantasy_helper.agent.llm import ChatModel, OpenAIModel
from fantasy_helper.agent.loop import Message, run_turn
from fantasy_helper.api.app import Ctx
from fantasy_helper.config import Settings

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
    if not settings.agent_configured:
        return JSONResponse(status_code=503, content={"detail": "The chat isn't set up yet."})
    if body.messages[-1].role != "user":
        return JSONResponse(status_code=400, content={"detail": "The conversation must end with a question."})
    with state.sessionmaker() as session:
        Ctx(session, settings, state.cache)  # raises LookupError (404) with no league stored
    # Phase 4's spending cap goes here, before the stream starts, so it can answer 429.
    messages = [Message(m.role, m.content) for m in recent(body.messages)]
    factory: Callable[[Settings], ChatModel] = state.agent_model_factory
    return StreamingResponse(_events(state, factory, messages), media_type="text/event-stream", headers=STREAM_HEADERS)


def _events(state: Any, factory: Callable[[Settings], ChatModel], messages: list[Message]) -> Iterator[str]:
    yield sse({"type": "start"})  # at once, so the page knows it's working
    try:
        with state.sessionmaker() as session:  # its own: the request's is gone once streaming starts
            c = Ctx(session, state.settings, state.cache)
            for event in run_turn(c, factory(state.settings), messages):
                yield sse(public(event))
    except Exception:
        log.exception("agent stream failed")
        yield sse({"type": "error", "message": "Something went wrong on our side."})
        yield sse({"type": "done"})
