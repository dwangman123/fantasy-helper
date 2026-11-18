"""One chat turn: the model answers, calling the app's tools as it goes.

`run_turn` yields events as they happen, so the endpoint can stream them:
  {"type": "tool_call", "id", "name", "arguments"}   a tool is about to run
  {"type": "tool_result", "id", "name", "ok", "error"?, "result"}   it ran ("result" stays server-side)
  {"type": "text", "delta"}                          part of the answer
  {"type": "notice", "message"}                      the answer was cut short
  {"type": "error", "message"}                       the turn couldn't finish
  {"type": "usage", ...}                             tokens, cost and tools, always sent
  {"type": "done"}                                   always last

Limits: `agent_max_steps` model calls (the last may not call tools, so it has to answer), and
`agent_deadline_seconds` for the whole turn: a streamed Lambda response keeps running, and
billing, even when the visitor closes the page.
"""

import json
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

from fantasy_helper.agent.llm import ChatModel, ModelError, TextDelta, Usage
from fantasy_helper.agent.prompt import instructions
from fantasy_helper.agent.tools import TOOLS, result_text, run_tool
from fantasy_helper.api.app import Ctx
from fantasy_helper.config import Settings

log = logging.getLogger("fantasy_helper.agent")

TOOL_FAILED = "The tool failed unexpectedly (a bug in the app, not in your request); don't retry it."


@dataclass
class Message:
    role: str  # user / assistant
    content: str


def tool_specs() -> list[dict[str, Any]]:
    return [
        {"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters, "strict": True}
        for t in TOOLS
    ]


def cost_usd(usage: Usage, settings: Settings) -> float | None:
    """The reported tokens' cost: cached tokens are part of input_tokens and billed at the cached
    rate, reasoning tokens part of output_tokens. A step whose usage never came isn't in it (the
    usage event's `unreported_steps`). None when a price isn't set."""
    prices = (settings.agent_input_price, settings.agent_cached_input_price, settings.agent_output_price)
    if None in prices:
        return None
    full, cached, out = prices
    uncached = usage.input_tokens - usage.cached_input_tokens
    return (uncached * full + usage.cached_input_tokens * cached + usage.output_tokens * out) / 1_000_000


def run_turn(
    c: Ctx,
    model: ChatModel,
    messages: list[Message],
    clock: Callable[[], float] = time.monotonic,
    on_finish: Callable[[dict[str, Any]], None] | None = None,
) -> Iterator[dict[str, Any]]:
    """`on_finish` gets the turn's usage event however the turn ends, a closed one too (it's
    where the spending cap settles the turn); an error in it is logged, not raised."""
    settings = c.settings
    start = clock()
    deadline = start + settings.agent_deadline_seconds
    turn = _Turn(usage=Usage())
    try:
        system = instructions(c)
        specs = tool_specs()
        input: list[dict[str, Any]] = [{"role": m.role, "content": m.content} for m in messages]
        for turn.steps in range(1, settings.agent_max_steps + 1):
            remaining = deadline - clock()
            if remaining <= 1:
                yield {"type": "error", "message": TOO_LONG}
                break
            choice = "none" if turn.steps == settings.agent_max_steps else "auto"
            step, turn.streaming = None, True
            stream = model.stream(system, input, specs, tool_choice=choice, timeout=remaining)
            try:
                for event in stream:
                    if clock() > deadline:  # a timeout only catches silence, not a model still writing
                        break
                    if isinstance(event, TextDelta):
                        yield {"type": "text", "delta": event.text}
                    else:
                        step = event
            finally:
                stream.close()  # stops OpenAI generating when we stop reading
            turn.streaming = False
            if step is None:
                turn.unreported += 1  # billed, but never reported
                turn.usage.known = False
                yield {"type": "notice", "message": TOO_LONG}
                turn.status = "timeout"
                break
            turn.usage.add(step.usage)
            turn.unreported += not step.usage.known
            if step.status == "failed":
                log.warning("agent step %d failed: %s", turn.steps, step.reason)
                yield {"type": "error", "message": "The model couldn't answer. Please try again."}
                break
            input += step.items
            calls = [x for x in step.calls if x.complete]
            if step.status == "incomplete" and not calls:
                yield {"type": "notice", "message": _cut_short(step.reason)}
                turn.status = "incomplete"
                break
            if not calls:
                turn.status = "ok"
                break
            for call in calls:
                turn.tools.append(call.name)
                yield from _call(c, call.call_id, call.name, call.arguments, input)
            if step.status == "incomplete":  # its finished calls ran; the rest of it is lost
                yield {"type": "notice", "message": _cut_short(step.reason)}
                turn.status = "incomplete"
                break
        else:  # the last step called tools anyway
            yield {"type": "error", "message": "This needed too many steps, so it stopped. Try a narrower question."}
    except ModelError as exc:
        log.warning("agent model error: %s", exc)
        yield {"type": "error", "message": "The model couldn't be reached. Please try again."}
    except GeneratorExit:  # the visitor left and the stream was closed
        turn.status = "closed"
        raise
    except Exception:
        log.exception("agent turn failed")
        yield {"type": "error", "message": "Something went wrong on our side."}
    finally:
        # Every turn is accounted for, a closed one too; nothing can be yielded here.
        if turn.streaming:  # stopped inside a model response: billed, but its usage never came
            turn.unreported += 1
            turn.usage.known = False
        summary = _summary(turn, model.name, cost_usd(turn.usage, settings), clock() - start)
        if on_finish is not None:
            try:
                on_finish(summary)
            except Exception:
                log.exception("agent turn's on_finish failed")
    yield summary
    yield {"type": "done"}


TOO_LONG = "This took too long, so it stopped. Try a narrower question."


@dataclass
class _Turn:
    usage: Usage
    steps: int = 0
    tools: list[str] = field(default_factory=list)
    status: str = "error"  # ok / incomplete / timeout / error / closed (the visitor left)
    streaming: bool = False  # a model response is being read
    unreported: int = 0  # steps billed whose usage never came


def _summary(turn: _Turn, model: str, cost: float | None, seconds: float) -> dict[str, Any]:
    """The turn's usage event, logged as it's made."""
    u = turn.usage
    log.info(
        "agent turn %s: %d steps, tools %s, %d in (%d cached) / %d out tokens%s, $%s, %.1f s",
        turn.status,
        turn.steps,
        turn.tools,
        u.input_tokens,
        u.cached_input_tokens,
        u.output_tokens,
        f" (+{turn.unreported} steps unreported)" if turn.unreported else "",
        "?" if cost is None else f"{cost:.4f}",
        seconds,
    )
    return {
        "type": "usage",
        "model": model,
        "status": turn.status,
        "steps": turn.steps,
        "tools": turn.tools,
        **asdict(u),
        "cost_usd": cost,  # of the reported tokens
        "unreported_steps": turn.unreported,
        "seconds": round(seconds, 2),
    }


def _call(c: Ctx, call_id: str, name: str, arguments: str, input: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Run one tool call, and add its result to the next request's input."""
    try:
        args = json.loads(arguments or "{}")
    except ValueError:
        args = None
    yield {"type": "tool_call", "id": call_id, "name": name, "arguments": args if args is not None else arguments}
    if not isinstance(args, dict):
        result = {"error": "The arguments weren't a JSON object."}
    else:
        try:
            result = run_tool(c, name, args)
        except Exception:
            log.exception("agent tool %s failed with %s", name, args)
            result = {"error": TOOL_FAILED}
    event = {"type": "tool_result", "id": call_id, "name": name, "ok": "error" not in result, "result": result}
    if "error" in result:
        event["error"] = result["error"]
    yield event
    input.append({"type": "function_call_output", "call_id": call_id, "output": result_text(result)})


def _cut_short(reason: str | None) -> str:
    if reason == "max_output_tokens":
        return "The answer hit its length limit and was cut short."
    return "The answer was cut short" + (f" ({reason})." if reason else ".")
