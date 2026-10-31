"""Loopback-only entry point. All configuration comes from the trusted local host."""

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from agent_harness.core.agents import LangGraphAgent
from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Executor
from agent_harness.tools import builtin_tools

from .api import create_app, validate_token
from .config import load_config
from .model import LocalModel
from .models.settings import EmbeddingConfig
from .services.auth import AuthService
from .services.chat import ChatService
from .services.database import ChatError, Database
from .services.embeddings import LocalEmbeddings
from .services.execution import ExecutionService
from .services.files import FileService
from .services.mcp import MCPService
from .services.memory import MemoryService
from .services.status import StatusService
from .services.summarization import LocalSummarizer


@dataclass
class Services:
    chat: ChatService
    memory: MemoryService
    files: FileService
    mcp: MCPService
    execution: ExecutionService
    status: StatusService
    auth: AuthService | None = None


def build_service(config, database, *, encryption_key=None, files_root=None):
    if not config.policy.local_only or config.policy.telemetry:
        raise ValueError("chat server requires local_only=true and telemetry=false")
    # Validate the inference destination before creating storage or accepting requests.
    LocalModel(config.policy, endpoint=config.endpoint, model=config.model)
    database.migrate()
    owner = database.local_owner()
    embedding_config = config.embeddings or EmbeddingConfig(endpoint=config.endpoint)
    embedder = LocalEmbeddings(config.policy, embedding_config.endpoint, embedding_config.model)
    mcp = MCPService(
        database,
        owner,
        config.policy,
        encryption_key,
        permissions=config.mcp_permissions,
        endpoints=config.mcp_endpoints,
    )

    def executor_factory(*, tool_rules=None, approve=None, observe=None):
        lifecycle_observe, _ = execution.observer(None)
        executor = Executor(config.policy, tool_rules=tool_rules, observe=lifecycle_observe)
        for tool in builtin_tools(config.policy):
            executor.register(tool)
        mcp.register(executor)
        executor._approve = approve
        executor._observe = observe
        return executor

    execution = ExecutionService(database, owner, executor_factory)

    def discovery_executor():
        lifecycle_observe, _ = execution.observer(None)
        return Executor(config.policy, tool_rules=execution.rules(), observe=lifecycle_observe)

    mcp.discovery_factory = discovery_executor

    def agent():
        executor = executor_factory(tool_rules=execution.rules())
        model = LocalModel(config.policy, endpoint=config.endpoint, model=config.model)
        return LangGraphAgent(model, executor, limits=config.limits)

    summarize = LocalSummarizer(config.policy, config.endpoint, config.model, config.limits)
    memory = MemoryService(database, owner, embedder, summarize)
    files = FileService(
        database,
        owner,
        files_root or Path.home() / ".local/state/agent-harness/blobs",
        tool_roots=config.policy.roots,
    )
    chat = ChatService(database, owner, agent, memory_service=memory, execution_service=execution)
    return Services(
        chat,
        memory,
        files,
        mcp,
        execution,
        StatusService(config, embedding_config),
        AuthService(database, owner),
    )


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
        services = build_service(
            config,
            database,
            encryption_key=os.environ.get("HARNESS_CREDENTIAL_KEY"),
            files_root=os.environ.get("HARNESS_FILES_DIR"),
        )
        app = create_app(
            services.chat,
            token=token,
            model_name=config.model,
            memory_service=services.memory,
            file_service=services.files,
            mcp_service=services.mcp,
            execution_service=services.execution,
            status_service=services.status,
            auth_service=services.auth,
        )
    except (ValueError, ChatError, SecurityError):
        # Never print configuration values, connection strings or underlying errors.
        parser.exit(1, "Local chat configuration or storage unavailable.\n")
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
