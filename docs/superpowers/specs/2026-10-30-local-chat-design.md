# Local chat backend

Build a single-owner local HTTP chat backend for a later UI. Keep the existing
OpenAI-compatible local inference adapter and LangGraph/executor security boundary.
The user clarified that Claude/cloud services are out of scope and requested strict
Pydantic models and separate routes, models, and services.

Use FastAPI routes, strict Pydantic request/response and built-in tool argument
models, and services for PostgreSQL persistence and chat orchestration. External MCP
schemas remain bounded JSON Schema, never generated Python or permission grants.
This builds on the existing core rather than replacing it or adding a cloud SDK.

Persist users, conversations, and messages in a dedicated local database and an
application schema with versioned packaged SQL migrations. One local owner is
configured by the host; clients cannot select owners, policies, handlers, or models.
Serve on loopback, require a host-provided bearer token, reject unexpected Host
headers, and leave CORS disabled until the UI origin is known. Postgres connections
must use numeric loopback addresses with bounded connection/statement waits.

API: health; create/list/get/rename/delete conversations; list messages with bounded
pagination; send a text message and return the stored user/assistant exchange.
Use response models, reject unknown request fields and coercion, bound request bodies,
and return fixed errors without prompts, credentials, SQL, or exception strings.

Serialize sends per conversation with a nonblocking database row lock. Commit each
successful exchange atomically; failures leave no partial exchange. Retain all saved
messages while sending only the existing bounded recent context to the model.
Each turn constructs its own agent/executor. Tool calls continue through
Executor.execute. No API approval mechanism yet: ask tools fail closed.

Alternatives: schema-only would not provide a usable backend; replacing SQLite
audit history would change its privacy contract. Keep metadata-only audit separate
and add content-bearing chat storage explicitly. Defer the original schema's MCP
credentials/catalog, per-user tool permissions, files, vectors, memory extraction,
compaction, streaming, and multi-user login until those features are needed.

Local application checks are not an OS sandbox. Database administrators and local
processes holding credentials/tokens are trusted. Saving chat content is intentional;
tool observations remain untrusted and are not persisted for replay. No automatic
retry/resumption: failed persistence cannot undo tool side effects.

Validation: bypass regressions first, real PostgreSQL integration tests, actual
LangGraph tests, full suite, Ruff checks/format, dependency audit, PR and required CI.
Work on a feature branch and preserve unrelated workspace files. The user's directive
to make the backend happen supplies execution authorization; UI work is deferred.
