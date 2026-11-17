"""`fh-agent "question"`: one chat turn in the terminal, against the configured database and model."""

import argparse
import json
import logging
import sys

from fantasy_helper.agent.loop import Message, run_turn
from fantasy_helper.agent.routes import openai_model
from fantasy_helper.api.app import Ctx, SnapshotCache
from fantasy_helper.config import get_settings
from fantasy_helper.db.session import get_sessionmaker

NEEDED = {
    "FH_OPENAI_API_KEY": "openai_api_key",
    "FH_AGENT_MODEL": "agent_model",
    "FH_AGENT_INPUT_PRICE": "agent_input_price",
    "FH_AGENT_CACHED_INPUT_PRICE": "agent_cached_input_price",
    "FH_AGENT_OUTPUT_PRICE": "agent_output_price",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the Fantasy Helper agent one question.")
    parser.add_argument("question")
    parser.add_argument("--results", action="store_true", help="print each tool's full result")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    settings = get_settings()
    if not settings.agent_configured:
        missing = [env for env, name in NEEDED.items() if getattr(settings, name) in (None, "")]
        sys.exit(f"The agent isn't configured; set {', '.join(missing)} (see backend/.env.example).")

    failed = False
    with get_sessionmaker()() as session:
        c = Ctx(session, settings, SnapshotCache())
        for event in run_turn(c, openai_model(settings), [Message("user", args.question)]):
            kind = event["type"]
            if kind == "text":
                print(event["delta"], end="", flush=True)
            elif kind == "tool_call":
                print(f"\n→ {event['name']}({json.dumps(event['arguments'])})", file=sys.stderr)
            elif kind == "tool_result":
                if not event["ok"]:
                    print(f"  ✗ {event['error']}", file=sys.stderr)
                elif args.results:
                    print(f"  {json.dumps(event['result'], indent=1)}", file=sys.stderr)
            elif kind in ("notice", "error"):
                failed = failed or kind == "error"
                print(f"\n[{kind}] {event['message']}", file=sys.stderr)
            elif kind == "usage":
                cost = "?" if event["cost_usd"] is None else f"${event['cost_usd']:.4f}"
                print(
                    f"\n\n[{event['model']}: {event['steps']} steps, {event['input_tokens']:,} in "
                    f"({event['cached_input_tokens']:,} cached) / {event['output_tokens']:,} out tokens, {cost}, "
                    f"{event['seconds']} s]",
                    file=sys.stderr,
                )
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
