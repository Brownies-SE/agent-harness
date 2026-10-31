# Agent Harness

**The model proposes. The harness decides.**

A working Python security core for running untrusted local models with explicitly
scoped tools. LangGraph is the only agent backend. Every tool call uses the harness policy and executor.
A tool request is a proposal, never a permission grant.

```text
Model → LangGraph → Tool request → Validation → Policy → Approval → Executor
                                                               ├── Built-in tool
                                                               └── MCP tool
```

This release includes a local HTTP chat backend, noninteractive administration commands,
an OpenAI-compatible local inference adapter,
bounded text file/directory tools, a local clock, an explicitly gated HTTP GET tool,
a transport-neutral MCP
adapter, and adversarial tests. It does not ship shell, Git, Docker or process-control
tools. Their capability names are reserved for explicitly reviewed future tools.

## Quick start

Python 3.11+ on Linux or macOS is required for filesystem tools. Install uv separately,
then install the locked dependencies. Installation downloads packages and is separate
from running in local-only mode.

```bash
uv sync --locked --extra dev
uv run --no-sync agent-harness validate --config harness.yaml
```

Start your own local inference server supporting `POST /v1/chat/completions`, install
a model there, and set `model.name` in [harness.yaml](harness.yaml) to that model's name.
The example endpoint is `http://127.0.0.1:11434`; configure your actual endpoint explicitly.
No model weights are downloaded or launched by this project.

```bash
uv run --no-sync agent-harness run --no-history --prompt 'Hello'
```

Use `--no-sync` after installation to avoid dependency synchronization during a run.
The example permits reads only inside `./project`, marks writes as requiring approval, and denies
all tool networking and subprocess execution. An empty `Policy()` denies every
capability and filesystem root. CLI results and security events escape terminal controls to
avoid terminal control injection. There is no interactive terminal chat or approval UI.
Running `agent-harness` without arguments prints administration command help. The
CLI and HTTP backend deny tools requiring approval; trusted library hosts can supply
their own approval callback. Audit events go to stderr for CLI runs; redirect them to a local
file if persistence is needed. Prompts passed with `--prompt` can appear in your OS
process list and shell history; avoid putting secrets in CLI arguments.

## Local chat backend

The HTTP backend adds durable PostgreSQL conversations for a local UI. It uses the
same local inference adapter, LangGraph and executor as the CLI. No Claude API or
cloud inference service is used. Chat conversations are served through HTTP.

Code is separated into `routes/` (HTTP), `models/` (strict Pydantic contracts), and
`services/` (storage and chat), `core/` (agent execution and security), and `tools/`
(built-in tools and the MCP adapter). Migrations live in `migrations/`; startup and
administration entry points stay at the package root. Top-level `Executor`, `Policy`,
`SecurityError` and `Tool` imports remain available. Built-in tools have typed input/result models and
generated input schemas. MCP tools retain bounded JSON Schema validation; untrusted
server schemas never choose Python types, handlers or permissions.

Create dedicated local databases and a login using DBeaver or your PostgreSQL admin
connection. The login needs ownership of these databases to apply migrations; it
does not need superuser or role-creation privileges after pgvector is enabled:

```sql
CREATE ROLE agent_harness_local LOGIN PASSWORD 'choose-a-local-password';
CREATE DATABASE agent_harness OWNER agent_harness_local;
CREATE DATABASE agent_harness_test OWNER agent_harness_local;
```

The PostgreSQL installation must include [pgvector](https://github.com/pgvector/pgvector#installation).
Connect as your local PostgreSQL administrator to **each** new database and run:

```sql
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
```

Use the dedicated application login afterwards. Do not grant it superuser privileges.


Copy `.env.example` to `.env.local`, set the matching database password, and generate
`HARNESS_API_TOKEN` with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`.
Environment files are ignored by Git; keep `.env.local` private (`chmod 600 .env.local`).
The database URL requires an explicit `127.0.0.1` or `::1` host, database, username
and password. Service files, extra connection parameters and hostname resolution
are rejected. Unset `PGSERVICE` if you use it for another project.

```bash
uv sync --locked --extra dev
set -a
source .env.local
set +a
uv run --no-sync agent-harness-server --config harness.local.yaml
```

Startup applies versioned migrations to the `agent_chat` schema. You can inspect
`users`, `conversations`, `messages`, `mcp_servers`, `mcp_connections`, `mcp_tools`,
`tool_permissions`, `tool_calls`, `files`, `memories`, and migration tracking there in
DBeaver. Migration 0002 upgrades the initial chat schema without deleting messages.
It adds the compaction/memory markers, cache-read token column, and `VECTOR(1024)`
embeddings. File/memory conversation references enforce owner scope; compaction
markers must reference a message in the same conversation.
Use `--migrate-only` to apply migrations without starting HTTP; use `--port` to
change the default port 8000. Start your configured local inference server separately.
The server requires `local_only: true` and `telemetry: false`.

Every HTTP request requires `Authorization: Bearer <HARNESS_API_TOKEN>`, including
health and `/openapi.json`. OpenAPI declares HTTP bearer security for every API
operation, so generated clients can supply the token. The server binds only `127.0.0.1`, rejects other Host
headers, disables proxy-header trust and access logs, and returns `Cache-Control:
no-store`. CORS is disabled; connect the future UI through a same-origin proxy or
add its exact local origin when integrating it. Anyone holding this token acts as
the single local owner; this is not multi-user authentication.

| Method | Route | Body / result |
| --- | --- | --- |
| GET | `/api/health` | Database availability and configured model name; does not probe inference. |
| POST | `/api/conversations` | `{"title":"My chat"}` or `{}`; returns conversation, HTTP 201. |
| GET | `/api/conversations` | Conversation list; `limit` 1–100 (default 50), `offset` ≥ 0. |
| GET | `/api/conversations/{id}` | Conversation metadata. |
| PATCH | `/api/conversations/{id}` | `{"title":"Renamed"}`. |
| DELETE | `/api/conversations/{id}` | Deletes the conversation and messages; HTTP 204. |
| GET | `/api/conversations/{id}/messages` | Messages by ascending `seq`; `after_seq` ≥ 0, `limit` 1–100. |
| POST | `/api/conversations/{id}/messages` | `{"content":"Hello"}`; returns `user`, `assistant`, `run_id`. |

Chat message content is a JSONB array of text blocks,
`[{"type":"text","text":"Hello"}]`. Send only the new message, not client-supplied
history or roles. Request bodies reject extra fields and type coercion, accept up
to 8000 characters per message / 200 per title, and have a 64 KiB byte limit.
Validation errors use a fixed `detail` code without echoing request content.

Each successful exchange is committed atomically. Model or storage failures roll
back both messages; a concurrent send, rename or delete on that conversation returns
HTTP 409. Reopening a conversation loads its saved history; inference receives at
most eight recent exchanges within an 8000-character serialized context budget.
Older messages stay in the database. Usage counts are provider-reported per turn.

All tool calls still pass through `Executor.execute`. The HTTP backend does not
implement approval callbacks yet, so tools requiring `ask` are denied. It cannot
accept policy, model, handler or owner overrides from HTTP or saved messages.
There is no streaming, cancellation endpoint, automatic retry, or interrupted-run
resumption. A disconnected client can leave its bounded turn running. Completed
tool side effects cannot be undone by transaction rollback: inspect history before
retrying a request whose result was lost.

PostgreSQL intentionally stores user and assistant text, unlike metadata-only CLI
run history. API tool arguments/results and durable tool audit events are not stored
in these chat tables. PostgreSQL administrators, host configuration and local
processes with credentials are trusted; loopback checks are not an OS sandbox.
Secure and back up the database yourself. All ten requested application tables are
present. The additional tables provide storage contracts, not automatic feature
activation: HTTP routes for MCP connections, file/blob operations, memory retrieval,
compaction and tool-call persistence are not implemented. Credential fields are
reserved for encrypted bytes; a trusted credential-writing service and host-managed
keys must be implemented before using them. Cached MCP schemas and stored
`tool_permissions` rows cannot authorize tools, change policy or register handlers.
Cache-read counts currently default to zero; cache accounting is not collected.

Run database/API integration tests against the separate test database:

```bash
uv run --no-sync pytest -q
```

Set `HARNESS_TEST_DATABASE_URL` first (the environment loading above does this).
Without it PostgreSQL tests skip; CI has a dedicated PostgreSQL job running the
full suite with these tests enabled. Tests use unique synthetic owners and delete
their own rows; do not point them at a production database.

## What local-only means

`security.local_only: true` is the default. `security.telemetry: false` is also the default.

| Boundary | Enforcement and limits |
| --- | --- |
| **Model inference** | Only the user-configured localhost, loopback, RFC1918 private-network or IPv6 ULA endpoint. Every resolved address must pass local-only checks before a prompt is sent. Cloud provider names, public addresses, link-local/metadata addresses and redirects are rejected. The endpoint operator can still see prompts/results; a LAN server is a separate machine. |
| **Network tools** | Denied by default. Require an allowed/approved `network` capability **and** an exact hostname and port allowlist. Local-only additionally rejects public destinations, even if allowlisted. Connections use a validated pinned IP with HTTPS hostname verification and TLS 1.2 minimum, without proxies or redirects. |
| **MCP servers** | Connection startup is gated independently from tool execution. Remote transports require network permission and URL policy; stdio requires subprocess permission. Discovered tools need explicit host-side mappings. Server metadata never grants permission. A server can perform hidden filesystem/network operations under its own OS privileges. |
| **Dependencies/integrations** | The harness does not intentionally export prompts, tool results, source code or traces by default. LangGraph runs explicitly disable LangSmith tracing when telemetry is false, including environment-enabled tracing. Arbitrary third-party Python packages, plugins, inference engines and MCP processes cannot be guaranteed never to make network requests. Installation, CI, vulnerability auditing and dependency updates intentionally use external services. |

Local-only is an application policy, **not an OS sandbox or an air-gap guarantee**.
For stronger containment run the harness and each MCP server in separate restricted
processes/containers with an OS firewall, read-only/limited mounts and no ambient
credentials. Keep an inference server isolated too. Local model weights receive
serialized messages and tool schemas, not Python objects or tool implementations.
A malicious inference engine or installed plugin is executable trusted code outside
what model-output validation can contain.

Telemetry is disabled by default. Explicitly enabling
`security.telemetry` permits LangGraph's LangSmith tracing according to the host's
LangSmith configuration; it can export user data. Keep it false for local data confinement.

## Permissions and resource policy

The vocabulary is `filesystem.read`, `filesystem.write`, `network`, `subprocess`,
`process.read`, `process.write`, `docker.read`, `docker.write`, `git.read`, `git.write`.
Each is `allow`, `deny` or `ask`; omitted/unknown capabilities deny. Unknown config
fields, duplicate YAML keys, invalid types and unsupported schema references are rejected.
The only supported network default is `deny`.

```yaml
security:
  local_only: true
  telemetry: false
filesystem:
  roots: [./project]
permissions:
  filesystem.read: allow
  filesystem.write: ask
  network: ask
network:
  default: deny
  allow: [localhost, 127.0.0.1]
  ports: [8080]
```

Inference has a separate user-configured endpoint grant, so denying network tools does
not disable local inference. Private networks are not implicitly trusted for tools:
add each destination and port explicitly. Hostname and port lists form a cross-product;
use separate harness instances for policies that need different host/port combinations.
`local_only: false` removes the local-address restriction but retains destination,
capability and argument checks for tools; this release still has no cloud provider SDK.

Paths are constrained to configured roots, with `..`, symlinks, hardlinks, special files,
secret-like filenames and common credential/browser directories denied. Existing paths
are opened descriptor-relatively using POSIX no-follow flags; file size and output size
are bounded. Writes cannot create parent directories. Exact `filesystem.sensitive_allow`
paths allow intentional exceptions but never escape roots. This filename list is a
heuristic, not a secret detector: avoid mounting unrelated data or credentials at all.
Concurrent renames by another OS process and malicious native code require OS isolation.

Approvals are trusted host callbacks returning `once`, `run`, or `deny`.
The CLI and HTTP backend install no approval callback; tools requiring `ask` deny.
Missing or failing callbacks also deny.
A run approval is scoped to tool + canonical arguments + capabilities, resets between
agent runs, and never bypasses deny rules, paths or network restrictions. One executor
belongs to one sequential run at a time; do not share it across concurrent runs.

## Built-in tools

| Tool | Arguments | Permission / behavior |
| --- | --- | --- |
| `read_file` | `path` | `filesystem.read`; UTF-8 text inside approved roots, up to 128 KiB. |
| `write_file` | `path`, `content` | `filesystem.write`; the example policy asks before writes. Parent directories must exist. |
| `list_directory` | `path` (use `.` for the workspace root) | `filesystem.read`; immediate allowed file/folder names and types, no recursion. |
| `get_current_time` | none (`{}`) | Local system clock; returns matching `utc` and `local` ISO8601 timestamps. Declares no privileged capabilities. |
| `http_get` | `url` | Network capability plus host/port policy; denied in the example configuration. |

Directory listing returns `{"entries": [{"name": "README.md", "type": "file"}],
"truncated": false}`. Results are sorted, contain at most 200 entries, and examine at most
1000 immediate children. `truncated: true` means the listing is incomplete; it is not a
recursive file search or a paginated index. Sensitive names, symlinks, hardlinked files
and special files are excluded unless an exact sensitive-path override applies (links
and special files remain excluded). Directory opens use the same descriptor-based,
no-follow traversal as file operations. Read permissions and approval requirements apply
before listing executes. File tools still reject directory roots as file arguments.

Tool schemas and handlers are used by LangGraph and the CLI/evaluation registry.
Clock reads do not enable filesystem, network or subprocess access. Tool results, including
filenames and timestamps, remain untrusted data rather than new instructions.

## Registering a tool

Only trusted application code registers handlers. No model-generated code is executed.

```python
from agent_harness import Executor, Policy, Tool

executor = Executor(Policy())
executor.register(
    Tool(
        name="double",
        schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        capabilities=(),  # Pure computation; no OS capabilities.
        handler=lambda value: value * 2,
    )
)
assert executor.execute({"tool": "double", "arguments": {"value": 4}}) == 8
```

Filesystem and network tools must declare a required string resource argument mapped
as `resources={"path": "path"}` (file), `resources={"path": "directory"}` (directory),
or `resources={"url": "url"}`. Registering code is
responsible for complete capability declarations and actually using checked resources.
The built-in filesystem/HTTP tools enforce these checks at use time; an arbitrary
extension cannot be made safe merely by declaring metadata. Schemas use a bounded subset of JSON Schema: basic types, closed objects, arrays,
required fields, enum/const and size/numeric bounds. Regex, patternProperties, schema
references, combinators, uniqueItems and unknown keywords are rejected; deeply nested
or oversized schemas are rejected before validation. MCP discovery registers a whole
batch atomically, so failed schema validation exposes no partial tool list.
Schema validation, capability
checks and approvals happen before the handler. Arguments, requests and outputs are
size-bounded JSON. A handler failure becomes a fixed reason code, not raw exception text.
The loop is bounded to eight steps by default. LangGraph has no alternative ToolNode path.

## MCP integration

`MCPAdapter` is a working synchronous, transport-neutral integration boundary. It does
not bundle an MCP SDK or automatically launch arbitrary servers. Supply a trusted client
with `list_tools() -> list[dict]` and `call_tool(name, arguments) -> JSON` methods.
A client wrapping an async SDK must provide its own synchronous/lifecycle bridge.

```python
from agent_harness import Executor, Policy
from agent_harness.tools.mcp import MCPAdapter, MCPPermission

# Host-approved stdio startup: default Policy() would deny before calling the factory.
executor = Executor(Policy(rules={"subprocess": "ask"}), approve=host_approval)
adapter = MCPAdapter(executor, "reviewed_server", reviewed_stdio_client_factory)
adapter.connect(
    {
        "add": MCPPermission(capabilities=()),  # Reviewed pure-computation tool.
    }
)
```

For HTTP supply `endpoint=` and a factory receiving the validated `Target`; the factory
must use its pinned address and preserve HTTPS verification. A reviewed stdio factory
must use fixed argument arrays, `shell=False`, command policies, timeouts, bounded output
and minimal environment/credentials. **This package supplies neither an unrestricted
shell nor an automatic subprocess runner.** Factories and SDKs are separate trusted code.

Connection approval never grants tool approval. Discovery inspects schemas; only tools
listed in the host permission map are registered. Schema resource mappings are validated
before use. Server `readOnlyHint`/other annotations and model requests cannot elevate
permissions. Capabilities do not intercept covert I/O inside a server; use OS containment.
Caller-owned clients must bound execution/discovery and close resources. Failed
discovery invokes client.close() if provided. Arbitrary Python
handlers and MCP calls cannot be safely preempted by this synchronous in-process core.

## Prompt injection and audit

System/user instructions and tool data are separate. Tool observations carry
`trust: untrusted_data`; they never configure policy or the registry. The model may
still follow injected instructions, so security is enforced outside the model.
The tests deliberately make the model follow an injected instruction and verify denial.

Audit event names include `security.tool_allowed`, `security.tool_denied`,
`security.approval_requested`, `security.approval_granted`, `security.approval_denied`,
`security.network_blocked`, `security.path_blocked`. Events contain request IDs, registered
tool names and fixed reason codes, excluding prompts, raw paths/URLs, arguments and
results. The host approval callback needs actual arguments to make a decision; do not
send that callback to an external service. Audit sink failures prevent tool execution.
The default library sink is a no-op; provide a durable local sink for audit retention.
Events are not a tamper-proof forensic log. External inference checks fail closed but
are not currently emitted as tool audit events.

## Run limits and cancellation

LangGraph uses the host-owned run lifecycle. Configure defaults in YAML or
override them per CLI run:

```yaml
run:
  max_steps: 8
  timeout_seconds: 120
  max_output_tokens: 512
```

```bash
uv run --no-sync agent-harness run --config harness.local.yaml \
  --max-steps 6 --timeout-seconds 45 --max-output-tokens 256 \
  --prompt 'Read README.md and summarize it.'
```

`max_steps` counts model calls, including the final answer. `max_output_tokens` is the
requested generation ceiling per call, not a measured total-token or monetary budget.
The provider must honor it; independent JSON/response byte limits still apply. Usage
counts in history are provider-reported; zero can mean no usage was reported.

Ctrl-C or SIGTERM requests cancellation. Checks run before/after inference, before
execution, after approvals, and before reporting completion. Built-in network I/O has
an absolute timeout monitor, so trickling bytes cannot indefinitely extend a request.
The noninteractive CLI exits with 130 for cancellation,
124 for the run deadline, 2 for the step limit, 1 for other errors, and 0 for completion.

**Limits are cooperative across arbitrary Python handlers, MCP calls, DNS resolution,
and local history I/O.** A blocking third-party call must return before the harness can
observe cancellation; use transport timeouts or process isolation for guaranteed
termination. Cancellation cannot undo a write that already happened. No action is
automatically retried or replayed. This release does not add checkpoint/resume.

## Local run history

CLI runs save whitelisted metadata in `~/.local/state/agent-harness/runs.sqlite3` by default.
The database is mode 0600, and newly created application directories are mode 0700.
Library runs persist only when given a `RunHistory` instance. Storage must be outside
the tool filesystem roots; that check also applies when constructing library agents.

```bash
uv run --no-sync agent-harness history
uv run --no-sync agent-harness history --run-id YOUR_RUN_ID
uv run --no-sync agent-harness run --config harness.local.yaml --no-history --prompt 'Hello'
```

Use `--history-dir DIRECTORY` to choose another local store, including when inspecting
history. Each run has an ID, UTC start time, configured limits, status, fixed failure
reason, elapsed time, model/tool call counts, denial count and reported token usage.
The event timeline records model/tool durations, approvals and security decisions.
Prompts, request arguments, file paths, URLs, results, final answers and raw exceptions
are excluded. Registered tool names are recorded. CLI security/progress events also
carry the run ID and use the same metadata filter. Final answers still go to stdout.

A `completed` run means the model returned a final answer; task correctness is measured
separately by evaluations. SIGKILL, a crash or power loss can leave a row marked `running`;
it is not evidence of completion. History is an inspectable local record, not resumable
state or a tamper-proof log. Records are retained until you remove/archive the local
store; there is no automatic retention pruning in this release.

## Real local-model evaluations

```bash
uv run --no-sync agent-harness eval --config harness.local.yaml --repeat 2
```

The suite runs five tasks per repeat: randomized value lookup, combining two
files, a narrowly approved report write, rejection of an external URL, and rejection
of `.env`. It creates disposable synthetic workspaces and a fixture policy: reads
allowed, only the exact report write approved, networking denied, telemetry off.
It tests the configured inference endpoint with those tools, rather than auditing your
entire production configuration or an MCP server.

Scoring checks actual tool use, file outcomes and answers; saying "done" is insufficient.
Denial tasks require one attempt at the specified target, a real harness denial, and
an appropriate final response. A model refusing without proposing the test request is
a task failure, not a security bypass. Reports contain pass rate, task failure codes,
run IDs, call counts and timings, never generated fixture contents or model answers.
Exit status is nonzero if any task fails. CI tests the evaluator with controlled model
responses; live Ollama evaluation is explicit and never downloads models automatically.

Run this against your configured local model to measure its task reliability. The suite
reports failures honestly; a final answer alone does not count as task success.
Context compaction and interrupted-run resumption are not implemented. The HTTP
backend can reopen stored chat exchanges.

## Development

```bash
uv sync --locked --extra dev
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pip-audit --skip-editable
```

CI tests Linux/macOS and actual compiled LangGraph execution, checks formatting, audits
locked dependencies and runs CodeQL. Action references are pinned to commit IDs and
workflow permissions are read-only except the CodeQL security-results upload job.
`main` is the default release branch; `dev` is the integration branch. Use feature
branches and pull requests. Only Brownies-SE may update main/dev; the owner can merge
passing PRs without another reviewer. Force pushes and branch deletion remain blocked.
See [the security design](docs/security-design.md) and
[security policy](SECURITY.md). This initial core has adversarial regression coverage;
it has not received an independent penetration test or sandbox certification.
