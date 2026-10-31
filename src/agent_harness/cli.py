import argparse
import json
import signal
import sys
import threading
from contextlib import contextmanager
from dataclasses import replace

from agent_harness.core.agents import LangGraphAgent
from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Executor
from agent_harness.core.runtime import RunStopped
from agent_harness.tools import builtin_tools

from .config import load_config
from .history import RunHistory, safe_event
from .model import LocalModel


@contextmanager
def cancellation_signals(cancel):
    previous = {}
    received = []

    def stop(signum, frame):
        received.append(signum)
        cancel.set()

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, stop)
        yield received
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def print_event(event):
    print(json.dumps({"run_id": event["run_id"], **safe_event(event)}), file=sys.stderr)


def build_agent(config, limits, history, *, on_event=None):
    executor = Executor(config.policy)
    for tool in builtin_tools(config.policy):
        executor.register(tool)
    model = LocalModel(
        config.policy,
        endpoint=config.endpoint,
        model=config.model,
        provider=config.provider,
    )
    return LangGraphAgent(model, executor, limits=limits, history=history, on_event=on_event)


def main():
    parser = argparse.ArgumentParser(description="The model proposes. The harness decides.")
    parser.add_argument("command", nargs="?", choices=["validate", "run", "history", "eval"])
    parser.add_argument("--config", default="harness.yaml")
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
    if args.command is None:
        parser.print_help()
        return 0
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
        if args.command == "run" and args.prompt is None:
            parser.error("run requires --prompt")
        history = (
            None if args.no_history else RunHistory(args.history_dir, roots=config.policy.roots)
        )
        cancel = threading.Event()
        with cancellation_signals(cancel):
            if args.command == "eval":
                from .evaluations import evaluate

                result = evaluate(
                    config,
                    repeats=args.repeat,
                    limits=limits,
                    history=history,
                    cancel=cancel,
                )
                print(json.dumps(result))
                return 0 if result["passed"] == result["total"] else 1
            agent = build_agent(config, limits, history, on_event=print_event)
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
                else "invalid configuration or missing dependency"
            ),
            file=sys.stderr,
        )
        return 1
