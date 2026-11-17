"""The language model behind the agent, behind a small interface so tests can script it.

`OpenAIModel` calls OpenAI's Responses API with streaming. It is three pieces, so the part that
can only be checked against the live API stays thin:
  - `build_request`: the request's arguments, a pure function
  - `read_stream`: SDK stream events -> text deltas and one finished `Step`, a pure function
  - `OpenAIModel.stream`: the network call between them

Nothing is stored at OpenAI (`store=False`): visitors' messages aren't kept anywhere. So each
request carries the whole turn so far, and a step's output items, reasoning included (as
encrypted content), are replayed into the next request by `replay_items`.

The `openai` package is imported only when a request is made, so the API's cold start doesn't
pay for it.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol


class ModelError(Exception):
    """The model couldn't answer: a network or API error, or a failed response."""


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: str  # JSON, as the model wrote it
    complete: bool = True  # False when the response was cut off mid-call


@dataclass
class Usage:
    input_tokens: int = 0  # cached tokens included
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0  # reasoning tokens included
    reasoning_tokens: int = 0
    known: bool = True  # False when a response ended without reporting usage

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.cached_input_tokens += other.cached_input_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.output_tokens += other.output_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.known = self.known and other.known


@dataclass
class Step:
    """One model response: its text, the tools it called, and what to replay next time."""

    status: str  # completed / incomplete / failed
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)  # output items, for the next request
    usage: Usage = field(default_factory=lambda: Usage(known=False))
    reason: str | None = None  # why it's incomplete or failed


@dataclass
class TextDelta:
    text: str


class ChatModel(Protocol):
    name: str

    def stream(
        self,
        instructions: str,
        input: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str,
        timeout: float,
    ) -> Iterator[TextDelta | Step]:
        """Text deltas as they're written, then exactly one Step. Raises ModelError."""
        ...


def build_request(
    model: str,
    instructions: str,
    input: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    tool_choice: str,
    max_output_tokens: int,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": input,
        "tools": tools,
        "tool_choice": tool_choice,
        "parallel_tool_calls": True,
        "max_output_tokens": max_output_tokens,
        "store": False,
        "include": ["reasoning.encrypted_content"],
        "stream": True,
    }
    if reasoning_effort:  # left out unless set: some models don't take it
        request["reasoning"] = {"effort": reasoning_effort}
    return request


def replay_items(output: Iterable[Any]) -> list[dict[str, Any]]:
    """A response's output items as input items for the next request."""
    return [item.model_dump(exclude_none=True, mode="json") for item in output]


def _usage(usage: Any) -> Usage:
    if usage is None:
        return Usage(known=False)
    inp, out = getattr(usage, "input_tokens_details", None), getattr(usage, "output_tokens_details", None)
    return Usage(
        input_tokens=usage.input_tokens or 0,
        cached_input_tokens=(getattr(inp, "cached_tokens", 0) or 0) if inp else 0,
        cache_write_tokens=(getattr(inp, "cache_write_tokens", 0) or 0) if inp else 0,
        output_tokens=usage.output_tokens or 0,
        reasoning_tokens=(getattr(out, "reasoning_tokens", 0) or 0) if out else 0,
    )


def _step(response: Any, status: str) -> Step:
    text, calls = [], []
    for item in response.output or []:
        if item.type == "message":
            text += [part.text for part in item.content or [] if getattr(part, "type", "") == "output_text"]
        elif item.type == "function_call":
            complete = getattr(item, "status", None) in (None, "completed")
            calls.append(ToolCall(item.call_id, item.name, item.arguments or "", complete))
    reason = None
    if status == "incomplete" and response.incomplete_details is not None:
        reason = response.incomplete_details.reason
    elif status == "failed" and response.error is not None:
        reason = response.error.message
    return Step(status, "".join(text), calls, replay_items(response.output or []), _usage(response.usage), reason)


TERMINAL = {"response.completed": "completed", "response.incomplete": "incomplete", "response.failed": "failed"}


def read_stream(events: Iterable[Any]) -> Iterator[TextDelta | Step]:
    """The events of one streamed response, as text deltas and then its Step. A stream that ends
    without a terminal event is a failed step whose usage isn't known."""
    for event in events:
        kind = getattr(event, "type", "")
        if kind == "response.output_text.delta":
            yield TextDelta(event.delta)
        elif kind in TERMINAL:
            yield _step(event.response, TERMINAL[kind])
            return
        elif kind == "error":
            raise ModelError(f"OpenAI error: {event.message}")
    yield Step("failed", reason="The response ended early")


class OpenAIModel:
    def __init__(
        self,
        api_key: str,
        model: str,
        max_output_tokens: int,
        reasoning_effort: str | None = None,
        max_retries: int = 1,
    ):
        import openai  # here, not at the top: see the module docstring

        self._openai = openai
        self._client = openai.OpenAI(api_key=api_key, max_retries=max_retries)
        self.name = model
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort

    def stream(
        self,
        instructions: str,
        input: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str,
        timeout: float,
    ) -> Iterator[TextDelta | Step]:
        request = build_request(
            self.name,
            instructions,
            input,
            tools,
            tool_choice=tool_choice,
            max_output_tokens=self.max_output_tokens,
            reasoning_effort=self.reasoning_effort,
        )
        try:
            yield from read_stream(self._client.responses.create(**request, timeout=timeout))
        except self._openai.APIError as exc:
            raise ModelError(f"OpenAI error: {exc}") from exc
