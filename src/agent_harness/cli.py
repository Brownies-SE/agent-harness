import argparse
import json
import select
import signal
import sys
import threading
from contextlib import contextmanager
from dataclasses import replace

from .agents import LangGraphAgent, NativeAgent
from .config import load_config
from .errors import SecurityError
from .executor import Executor
from .filesystem import filesystem_tools
from .history import RunHistory, safe_event
from .model import LocalModel
from .network import http_tool
from .runtime import RunStopped, check_run


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
    while True:
        check_run()
        if select.select([sys.stdin], [], [], 0.1)[0]:
            return {"y": "once", "a": "run"}.get(sys.stdin.readline().strip().lower(), "deny")


@contextmanager
def cancellation_signals(cancel):
    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda signum, frame: cancel.set())
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def print_event(event):
    print(json.dumps({"run_id": event["run_id"], **safe_event(event)}), file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="The model proposes. The harness decides.")
    parser.add_argument("command", choices=["validate", "run", "history", "eval"])
    parser.add_argument("--config", default="harness.yaml")
    parser.add_argument("--agent", choices=["native", "langgraph", "both"], default="native")
    parser.add_argument("--prompt")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--timeout-seconds", type=float)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--history-dir")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    try:
        if args.command == "history":
            history = RunHistory(args.history_dir)
            result = (
                {"run_id": args.run_id, "events": history.events(args.run_id)}
                if args.run_id
                else history.list_runs(args.limit)
            )
            print(json.dumps(result))
            return 0
        config = load_config(args.config)
        overrides = {
            name: getattr(args, name)
            for name in ("max_steps", "timeout_seconds", "max_output_tokens")
            if getattr(args, name) is not None
        }
        limits = replace(config.limits, **overrides)
        if args.command == "validate":
            print("Configuration valid; capabilities remain subject to policy checks.")
            return 0
        if args.command == "run" and (args.prompt is None or args.agent == "both"):
            parser.error("run requires --prompt and a single --agent")
        history = (
            None if args.no_history else RunHistory(args.history_dir, roots=config.policy.roots)
        )
        cancel = threading.Event()
        with cancellation_signals(cancel):
            if args.command == "eval":
                from .evaluations import evaluate

                result = evaluate(
                    config,
                    runner=args.agent,
                    repeats=args.repeat,
                    limits=limits,
                    history=history,
                    cancel=cancel,
                )
                print(json.dumps(result))
                return 0 if result["passed"] == result["total"] else 1
            executor = Executor(config.policy, approve=terminal_approval)
            for tool in filesystem_tools(config.policy):
                executor.register(tool)
            executor.register(http_tool(config.policy))
            model = LocalModel(
                config.policy,
                endpoint=config.endpoint,
                model=config.model,
                provider=config.provider,
            )
            runner = NativeAgent if args.agent == "native" else LangGraphAgent
            agent = runner(model, executor, limits=limits, history=history, on_event=print_event)
            answer = agent.run(args.prompt, cancel=cancel)
            print(json.dumps({"run_id": agent.last_run.id, "answer": answer}, ensure_ascii=True))
            return 0
    except RunStopped as exc:
        print("Harness stopped: " + str(exc), file=sys.stderr)
        return {"run_cancelled": 130, "run_timeout": 124, "step_limit": 2}[str(exc)]
    except KeyboardInterrupt:
        print("Harness stopped: run_cancelled", file=sys.stderr)
        return 130
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
