"""Loopback-only entry point. All configuration comes from the trusted local host."""

import argparse
import os

import uvicorn

from agent_harness.core.agents import LangGraphAgent
from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Executor
from agent_harness.tools import builtin_tools

from .api import create_app, validate_token
from .config import load_config
from .model import LocalModel
from .services.chat import ChatService
from .services.database import ChatError, Database


def build_service(config, database):
    if not config.policy.local_only or config.policy.telemetry:
        raise ValueError("chat server requires local_only=true and telemetry=false")
    # Validate the inference destination before creating storage or accepting requests.
    LocalModel(config.policy, endpoint=config.endpoint, model=config.model)
    database.migrate()
    owner = database.owner("local@localhost")

    def agent():
        executor = Executor(config.policy)
        for tool in builtin_tools(config.policy):
            executor.register(tool)
        model = LocalModel(config.policy, endpoint=config.endpoint, model=config.model)
        return LangGraphAgent(model, executor, limits=config.limits)

    return ChatService(database, owner, agent)


def main():
    parser = argparse.ArgumentParser(description="Local chat backend (loopback only)")
    parser.add_argument("--config", default="harness.local.yaml")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--migrate-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        database = Database(os.environ.get("HARNESS_DATABASE_URL", ""))
        if args.migrate_only:
            database.migrate()
            print("Local chat schema ready.")
            return 0
        token = os.environ.get("HARNESS_API_TOKEN", "")
        # Validate authentication before connecting to the database.
        validate_token(token)
        config = load_config(args.config)
        service = build_service(config, database)
        app = create_app(service, token=token, model_name=config.model)
    except (ValueError, ChatError, SecurityError):
        # Never print configuration values, connection strings or underlying errors.
        parser.exit(1, "Local chat configuration or storage unavailable.\n")
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
