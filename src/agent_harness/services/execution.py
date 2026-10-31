from contextvars import ContextVar
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from agent_harness.core.errors import SecurityError, safe_reason
from agent_harness.core.executor import json_copy
from agent_harness.core.runtime import current_run
from agent_harness.models.execution import ToolCall, ToolDescription, ToolPermission
from agent_harness.services.database import ChatError


class ExecutionService:
    def __init__(self, database, owner, executor_factory):
        self.database = database
        self.owner = owner
        self.executor_factory = executor_factory

    @staticmethod
    def _record(row):
        return ToolCall(**{key: row[key] for key in ToolCall.model_fields})

    def list_tools(self):
        executor = self.executor_factory(tool_rules=self.rules())
        return [ToolDescription(**description) for description in executor.descriptions()]

    def rules(self):
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT tool_key,policy FROM agent_chat.tool_permissions WHERE user_id=%s",
                (self.owner,),
            ).fetchall()
        return {row["tool_key"]: row["policy"] for row in rows}

    def permissions(self):
        return [
            ToolPermission(tool_key=key, policy=value)
            for key, value in sorted(self.rules().items())
        ]

    def set_permission(self, tool_key, policy):
        if policy not in {"allow", "ask", "deny"}:
            raise ChatError("invalid_tool_policy", 422)
        if tool_key not in {tool.name for tool in self.list_tools()}:
            raise ChatError("unknown_tool", 404)
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO agent_chat.tool_permissions(user_id,tool_key,policy) "
                "VALUES (%s,%s,%s) ON CONFLICT(user_id,tool_key) "
                "DO UPDATE SET policy=EXCLUDED.policy",
                (self.owner, tool_key, policy),
            )
        return ToolPermission(tool_key=tool_key, policy=policy)

    def delete_permission(self, tool_key):
        with self.database.connect() as db:
            if not db.execute(
                "DELETE FROM agent_chat.tool_permissions "
                "WHERE user_id=%s AND tool_key=%s RETURNING tool_key",
                (self.owner, tool_key),
            ).fetchone():
                raise ChatError("tool_permission_not_found", 404)

    def get(self, call_id):
        with self.database.connect() as db:
            row = db.execute(
                "SELECT * FROM agent_chat.tool_calls WHERE id=%s AND user_id=%s",
                (call_id, self.owner),
            ).fetchone()
        if row is None:
            raise ChatError("tool_call_not_found", 404)
        return self._record(row)

    def list(self, *, conversation_id=None, limit=50, offset=0):
        if not 1 <= limit <= 100 or not 0 <= offset <= 2147483647:
            raise ValueError("invalid pagination")
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT * FROM agent_chat.tool_calls WHERE user_id=%s "
                "AND (%s::uuid IS NULL OR conversation_id=%s) "
                "ORDER BY started_at DESC NULLS LAST,id DESC LIMIT %s OFFSET %s",
                (self.owner, conversation_id, conversation_id, limit, offset),
            ).fetchall()
        return [self._record(row) for row in rows]

    def _insert(self, conversation_id, request, run_id=None):
        request = json_copy(request)
        tool = request.get("tool", "<invalid>") if isinstance(request, dict) else "<invalid>"
        arguments = request.get("arguments", {}) if isinstance(request, dict) else {}
        if not isinstance(tool, str) or len(tool) > 96:
            tool = "<invalid>"
        if not isinstance(arguments, dict):
            arguments = {}
        with self.database.connect() as db:
            if (
                conversation_id is not None
                and not db.execute(
                    "SELECT id FROM agent_chat.conversations WHERE id=%s AND user_id=%s",
                    (conversation_id, self.owner),
                ).fetchone()
            ):
                raise ChatError("conversation_not_found", 404)
            row = db.execute(
                "INSERT INTO agent_chat.tool_calls "
                "(user_id,conversation_id,run_id,tool_use_id,tool_key,input,status) "
                "VALUES (%s,%s,%s,%s,%s,%s,'pending') RETURNING id",
                (self.owner, conversation_id, run_id, uuid4().hex, tool, Jsonb(arguments)),
            ).fetchone()
        return row["id"]

    def _callbacks(self, conversation_id, *, existing=None, explicit=False, run_id=None):
        active = ContextVar("execution_calls", default=())
        delegated = ContextVar("execution_delegated_approvals", default=frozenset())

        def observe(event):
            if event["phase"] == "started":
                control = current_run()
                runtime_id = UUID(control.id) if control else run_id
                call_id = existing or self._insert(conversation_id, event["request"], runtime_id)
                with self.database.connect() as db:
                    if not db.execute(
                        "UPDATE agent_chat.tool_calls SET status='running',connection_id=%s,"
                        "started_at=clock_timestamp() WHERE id=%s AND user_id=%s "
                        "AND status IN ('pending','running') RETURNING id",
                        (event.get("connection_id"), call_id, self.owner),
                    ).fetchone():
                        raise ChatError("tool_call_not_runnable", 409)
                active.set((*active.get(), call_id))
                return
            stack = active.get()
            if not stack:
                return
            call_id = stack[-1]
            active.set(stack[:-1])
            if call_id in delegated.get():
                delegated.set(delegated.get() - {call_id})
                return
            reason = safe_reason(event.get("reason", ""))
            status = {"completed": "succeeded", "denied": "denied"}.get(event["status"], "failed")
            if reason in {"tool_failed", "audit_unavailable", "operation_failed"}:
                status = "failed"
            with self.database.connect() as db:
                db.execute(
                    "UPDATE agent_chat.tool_calls SET status=%s,output=%s,error=%s,"
                    "finished_at=clock_timestamp() WHERE id=%s AND user_id=%s "
                    "AND status<>'awaiting_approval'",
                    (status, Jsonb(event.get("result")), reason or None, call_id, self.owner),
                )

        def approve(details):
            stack = active.get()
            if not stack:
                return "deny"
            call_id = stack[-1]
            if explicit:
                saved = self.get(call_id)
                if saved.tool_key == details["tool"] and saved.input == details["arguments"]:
                    return "once"
                return "deny"
            with self.database.connect() as db:
                if not db.execute(
                    "UPDATE agent_chat.tool_calls SET status='awaiting_approval',"
                    "input=%s,error=NULL WHERE id=%s AND user_id=%s "
                    "AND status='running' RETURNING id",
                    (Jsonb(json_copy(details["arguments"])), call_id, self.owner),
                ).fetchone():
                    return "deny"
                # Commit the delegation before the original executor's denial completion.
                delegated.set(delegated.get() | {call_id})
            return "deny"

        return observe, approve

    def observer(self, conversation_id, run_id=None):
        """Callbacks belong to one trusted executor; call stacks are context-local."""
        return self._callbacks(conversation_id, run_id=run_id)

    def _execute(self, call_id, *, explicit=False):
        saved = self.get(call_id)
        observe, approve = self._callbacks(
            saved.conversation_id, existing=call_id, explicit=explicit
        )
        try:
            executor = self.executor_factory(
                tool_rules=self.rules(), approve=approve, observe=observe
            )
            executor.execute({"tool": saved.tool_key, "arguments": saved.input})
        except SecurityError as exc:
            # Startup can fail before an executor observer exists.
            with self.database.connect() as db:
                db.execute(
                    "UPDATE agent_chat.tool_calls SET status='failed',error=%s,"
                    "finished_at=clock_timestamp() WHERE id=%s AND user_id=%s "
                    "AND status IN ('pending','running') AND (%s OR approved_at IS NULL)",
                    (safe_reason(str(exc)), call_id, self.owner, explicit),
                )
        except Exception:
            with self.database.connect() as db:
                db.execute(
                    "UPDATE agent_chat.tool_calls SET status='failed',error='operation_failed',"
                    "finished_at=clock_timestamp() WHERE id=%s AND user_id=%s "
                    "AND status IN ('pending','running') AND (%s OR approved_at IS NULL)",
                    (call_id, self.owner, explicit),
                )
        return self.get(call_id)

    def call(self, body):
        call_id = self._insert(
            body.conversation_id, {"tool": body.tool, "arguments": body.arguments}
        )
        return self._execute(call_id)

    def approve(self, call_id, body):
        with self.database.connect() as db:
            row = db.execute(
                "SELECT id,status FROM agent_chat.tool_calls WHERE id=%s AND user_id=%s FOR UPDATE",
                (call_id, self.owner),
            ).fetchone()
            if row is None:
                raise ChatError("tool_call_not_found", 404)
            if row["status"] != "awaiting_approval":
                raise ChatError("tool_call_not_awaiting_approval", 409)
            if body.decision == "deny":
                db.execute(
                    "UPDATE agent_chat.tool_calls SET status='denied',error='approval_denied',"
                    "finished_at=clock_timestamp() WHERE id=%s AND user_id=%s",
                    (call_id, self.owner),
                )
            else:
                db.execute(
                    "UPDATE agent_chat.tool_calls SET status='running',"
                    "approved_at=clock_timestamp() WHERE id=%s AND user_id=%s",
                    (call_id, self.owner),
                )
        if body.decision == "deny":
            return self.get(call_id)
        return self._execute(call_id, explicit=True)

    def link_run(self, run_id, message_id):
        with self.database.connect() as db:
            if not db.execute(
                "SELECT m.id FROM agent_chat.messages m "
                "JOIN agent_chat.conversations c ON c.id=m.conversation_id "
                "WHERE m.id=%s AND c.user_id=%s",
                (message_id, self.owner),
            ).fetchone():
                raise ChatError("message_not_found", 404)
            db.execute(
                "UPDATE agent_chat.tool_calls t SET message_id=%s FROM agent_chat.messages m "
                "WHERE t.user_id=%s AND t.run_id=%s AND m.id=%s "
                "AND t.conversation_id=m.conversation_id",
                (message_id, self.owner, UUID(str(run_id)), message_id),
            )
