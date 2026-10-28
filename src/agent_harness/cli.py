import argparse
import json
import sys

from .agents import LangGraphAgent, NativeAgent
from .config import load_config
from .errors import SecurityError
from .executor import Executor
from .filesystem import filesystem_tools
from .model import LocalModel
from .network import http_tool


def terminal_approval(details):
    # JSON encoding escapes terminal controls in model-provided values.
    print(json.dumps(details, ensure_ascii=True), file=sys.stderr)
    if not sys.stdin.isatty():
        return "deny"
    print(
        "Allow? [y] once / [a] exact request for this run / [n] deny: ",
        end="",
        file=sys.stderr,
        flush=True,
    )
    return {"y": "once", "a": "run"}.get(input().strip().lower(), "deny")


def main():
    parser = argparse.ArgumentParser(description="The model proposes. The harness decides.")
    parser.add_argument("command", choices=["validate", "run"])
    parser.add_argument("--config", default="harness.yaml")
    parser.add_argument("--agent", choices=["native", "langgraph"], default="native")
    parser.add_argument("--prompt")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.command == "validate":
            print("Configuration valid; capabilities remain subject to policy checks.")
            return 0
        if args.prompt is None:
            parser.error("run requires --prompt")
        executor = Executor(
            config.policy,
            approve=terminal_approval,
            audit=lambda event: print(json.dumps(event), file=sys.stderr),
        )
        for tool in filesystem_tools(config.policy):
            executor.register(tool)
        executor.register(http_tool(config.policy))
        model = LocalModel(
            config.policy, endpoint=config.endpoint, model=config.model, provider=config.provider
        )
        runner = NativeAgent if args.agent == "native" else LangGraphAgent
        # JSON prevents terminal control escape injection by the final model answer.
        print(json.dumps({"answer": runner(model, executor).run(args.prompt)}, ensure_ascii=True))
        return 0
    except (ValueError, SecurityError, ImportError) as exc:
        print(
            "Harness stopped: "
            + (
                str(exc)
                if isinstance(exc, SecurityError)
                else "invalid configuration or missing optional dependency"
            ),
            file=sys.stderr,
        )
        return 1
