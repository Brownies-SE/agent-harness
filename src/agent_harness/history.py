"""Private local run metadata. Prompts, arguments, results and answers are never stored."""

import json
import os
import re
import sqlite3
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from agent_harness.core.errors import SecurityError, safe_reason
from agent_harness.core.runtime import RunLimits

EVENTS = frozenset(
    {
        "run.started",
        "run.finished",
        "model.started",
        "model.finished",
        "tool.finished",
        "security.tool_allowed",
        "security.tool_denied",
        "security.approval_requested",
        "security.approval_granted",
        "security.approval_denied",
        "security.network_blocked",
        "security.path_blocked",
        "security.mcp_cleanup_failed",
    }
)
STATUSES = frozenset(
    {"running", "completed", "failed", "denied", "stopped", "cancelled", "timed_out", "limited"}
)


def safe_event(data):
    event = data.get("event")
    result = {"event": event if isinstance(event, str) and event in EVENTS else "run.event"}
    for field in ("elapsed_ms", "duration_ms"):
        value = data.get(field)
        if type(value) is int and 0 <= value <= 1000000000:
            result[field] = value
    tool = data.get("tool")
    if isinstance(tool, str):
        result["tool"] = (
            tool if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,95}", tool) else "<unknown>"
        )
    if "reason" in data:
        reason = data["reason"]
        result["reason"] = safe_reason(reason)
    if isinstance(data.get("status"), str) and data["status"] in STATUSES:
        result["status"] = data["status"]
    request_id = data.get("request_id")
    if isinstance(request_id, str) and re.fullmatch(r"[a-f0-9]{32}", request_id):
        result["request_id"] = request_id
    return result


class RunHistory:
    def __init__(self, directory=None, *, roots=()):
        directory = Path(directory or Path.home() / ".local/state/agent-harness").expanduser()
        directory = directory.resolve()
        self.database = directory / "runs.sqlite3"
        self.check_roots(roots)
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(self.database, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise SecurityError("history_unavailable")
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
            with self.connection() as connection:
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY, runner TEXT NOT NULL, started_at TEXT NOT NULL,
                        status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                        limits_json TEXT NOT NULL, elapsed_ms INTEGER NOT NULL DEFAULT 0,
                        model_calls INTEGER NOT NULL DEFAULT 0,
                        tool_calls INTEGER NOT NULL DEFAULT 0,
                        denied_tools INTEGER NOT NULL DEFAULT 0,
                        input_tokens INTEGER NOT NULL DEFAULT 0,
                        output_tokens INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE IF NOT EXISTS events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL REFERENCES runs(run_id), metadata TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS events_run ON events(run_id, sequence);
                """)
        except OSError as exc:
            raise SecurityError("history_unavailable") from exc

    def check_roots(self, roots):
        directory = self.database.parent.resolve()
        if any(directory.is_relative_to(Path(root).resolve()) for root in roots):
            raise ValueError("history storage must be outside tool filesystem roots")

    @contextmanager
    def connection(self):
        connection = None
        try:
            connection = sqlite3.connect(self.database, timeout=1)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        except sqlite3.Error as exc:
            raise SecurityError("history_unavailable") from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def check_id(run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("invalid run ID")

    def begin(self, run_id, limits):
        self.check_id(run_id)
        RunLimits(**limits)
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO runs(run_id, runner, started_at, status, limits_json) "
                "VALUES (?, ?, ?, 'running', ?)",
                (run_id, "langgraph", datetime.now(timezone.utc).isoformat(), json.dumps(limits)),
            )

    def append(self, run_id, event):
        self.check_id(run_id)
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO events(run_id, metadata) VALUES (?, ?)",
                (run_id, json.dumps(safe_event(event))),
            )

    def finish(
        self,
        run_id,
        *,
        status,
        reason,
        elapsed_ms,
        model_calls,
        tool_calls,
        denied_tools,
        input_tokens,
        output_tokens,
    ):
        self.check_id(run_id)
        if status not in STATUSES:
            raise ValueError("invalid run status")
        reason = safe_reason(reason)
        with self.connection() as connection:
            connection.execute(
                "UPDATE runs SET status=?, reason=?, elapsed_ms=?, model_calls=?, "
                "tool_calls=?, denied_tools=?, input_tokens=?, output_tokens=? "
                "WHERE run_id=?",
                (
                    status,
                    reason,
                    elapsed_ms,
                    model_calls,
                    tool_calls,
                    denied_tools,
                    input_tokens,
                    output_tokens,
                    run_id,
                ),
            )

    def list_runs(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("history limit must be between 1 and 1000")
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["limits"] = json.loads(item.pop("limits_json"))
            result.append(item)
        return result

    def events(self, run_id):
        self.check_id(run_id)
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT metadata FROM events WHERE run_id=? ORDER BY sequence", (run_id,)
            ).fetchall()
        return [json.loads(row["metadata"]) for row in rows]
