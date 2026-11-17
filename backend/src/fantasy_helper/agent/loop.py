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
from dataclasses import asdict, dataclass
from typing import Any

from fantasy_helper.agent.llm import ChatModel, ModelError, Step, TextDelta, Usage
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
    """Cached tokens are part of input_tokens and billed at the cached rate; reasoning tokens
    are part of output_tokens. None when usage or a price isn't known."""
    prices = (settings.agent_input_price, settings.agent_cached_input_price, settings.agent_output_price)
    if not usage.known or None in prices:
        return None
    full, cached, out = prices
    uncached = usage.input_tokens - usage.cached_input_tokens
    return (uncached * full + usage.cached_input_tokens * cached + usage.output_tokens * out) / 1_000_000


def run_turn(
    c: Ctx,
    model: ChatModel,
    messages: list[Message],
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[dict[str, Any]]:
    settings = c.settings
    start = clock()
    deadline = start + settings.agent_deadline_seconds
    usage, tools_called, steps = Usage(), [], 0
    status = "error"
    try:
        system = instructions(c)
        specs = tool_specs()
        input: list[dict[str, Any]] = [{"role": m.role, "content": m.content} for m in messages]
        for steps in range(1, settings.agent_max_steps + 1):
            remaining = deadline - clock()
            if remaining <= 1:
                yield {"type": "error", "message": "This took too long, so it stopped. Try a narrower question."}
                break
            last = steps == settings.agent_max_steps
            step = None
            for event in model.stream(system, input, specs, tool_choice="none" if last else "auto", timeout=remaining):
                if isinstance(event, TextDelta):
                    yield {"type": "text", "delta": event.text}
                else:
                    step = event
            assert isinstance(step, Step), "a model stream ends with one Step"
            usage.add(step.usage)
            if step.status == "failed":
                log.warning("agent step %d failed: %s", steps, step.reason)
                yield {"type": "error", "message": "The model couldn't answer. Please try again."}
                break
            input += step.items
            calls = [x for x in step.calls if x.complete]
            if step.status == "incomplete" and not calls:
                yield {"type": "notice", "message": _cut_short(step.reason)}
                status = "incomplete"
                break
            if not calls:
                status = "ok"
                break
            for call in calls:
                tools_called.append(call.name)
                yield from _call(c, call.call_id, call.name, call.arguments, input)
            if step.status == "incomplete":  # its finished calls ran; the rest of it is lost
                yield {"type": "notice", "message": _cut_short(step.reason)}
                status = "incomplete"
                break
        else:  # the last step called tools anyway
            yield {"type": "error", "message": "This needed too many steps, so it stopped. Try a narrower question."}
    except ModelError as exc:
        log.warning("agent model error: %s", exc)
        yield {"type": "error", "message": "The model couldn't be reached. Please try again."}
    except Exception:
        log.exception("agent turn failed")
        yield {"type": "error", "message": "Something went wrong on our side."}
    cost = cost_usd(usage, settings)
    latency = clock() - start
    log.info(
        "agent turn %s: %d steps, tools %s, %d in (%d cached) / %d out tokens, $%s, %.1f s",
        status,
        steps,
        tools_called,
        usage.input_tokens,
        usage.cached_input_tokens,
        usage.output_tokens,
        "?" if cost is None else f"{cost:.4f}",
        latency,
    )
    yield {
        "type": "usage",
        "model": model.name,
        "status": status,
        "steps": steps,
        "tools": tools_called,
        **asdict(usage),
        "cost_usd": cost,
        "seconds": round(latency, 2),
    }
    yield {"type": "done"}


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
