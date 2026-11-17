"""Minimal stand-ins for espn_api objects, shaped like the real ones."""

from types import SimpleNamespace


def fake_player(player_id=1, name="Test Player", pro_team="BOS", stats=None, schedule=None, **kw):
    return SimpleNamespace(
        playerId=player_id,
        name=name,
        proTeam=pro_team,
        position=kw.get("position", "SF"),
        eligibleSlots=kw.get("eligibleSlots", ["SF", "F", "UT", "BE", "IR"]),
        injuryStatus=kw.get("injuryStatus", "ACTIVE"),
        lineupSlot=kw.get("lineupSlot", "SF"),
        acquisitionType=kw.get("acquisitionType", "DRAFT"),
        stats=stats or {},
        schedule=schedule or {},
    )


def stat_block(gp=10, **per_game):
    avg = {"GP": gp, "MPG": 30.0, **per_game}
    return {"avg": avg, "total": {"GP": gp}, "applied_total": 0, "applied_avg": 0}


class ScriptedModel:
    """A stand-in for the chat model: each call to `stream` plays the next scripted Step (or
    raises it, if it's an exception), and records the request it was given."""

    name = "scripted"

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def stream(self, instructions, input, tools, *, tool_choice, timeout):
        import copy

        from fantasy_helper.agent.llm import TextDelta

        self.requests.append(
            {
                "instructions": instructions,
                "input": copy.deepcopy(input),
                "tools": [t["name"] for t in tools],
                "tool_choice": tool_choice,
                "timeout": timeout,
            }
        )
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        for word in step.text.split(" ") if step.text else []:
            yield TextDelta(word + " ")
        yield step


def answer(text, input_tokens=1000, output_tokens=100, cached=0, status="completed", reason=None):
    from fantasy_helper.agent.llm import Step, Usage

    usage = Usage(input_tokens, cached, 0, output_tokens)
    items = [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]
    return Step(status, text, [], items, usage, reason)


def calls(*tool_calls, status="completed", input_tokens=1000, output_tokens=50):
    """A step calling tools: each a (name, arguments) pair, arguments a dict or a raw string;
    a third element False marks a call cut off mid-way."""
    import json

    from fantasy_helper.agent.llm import Step, ToolCall, Usage

    made = []
    for i, (name, arguments, *complete) in enumerate(tool_calls):
        text = arguments if isinstance(arguments, str) else json.dumps(arguments)
        made.append(ToolCall(f"call_{name}_{i}", name, text, complete[0] if complete else True))
    items = [{"type": "function_call", "call_id": c.call_id, "name": c.name, "arguments": c.arguments} for c in made]
    return Step(status, "", made, items, Usage(input_tokens, 0, 0, output_tokens))
