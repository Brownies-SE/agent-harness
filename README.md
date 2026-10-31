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
CLI denies tools requiring approval; the HTTP backend stores them for explicit API
approval. Trusted library hosts can supply their own approval callback. Audit events go to stderr for CLI runs; redirect them to a local
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
`users`, `local_account`, `auth_sessions`, `conversations`, `messages`,
`message_variants`, `mcp_servers`, `mcp_connections`, `mcp_tools`,
`tool_permissions`, `tool_calls`, `files`, `memories`, and migration tracking there in
DBeaver. Migration 0002 upgrades the initial chat schema without deleting messages.
It adds the compaction/memory markers, cache-read token column, and `VECTOR(1024)`
embeddings. File/memory conversation references enforce owner scope; compaction
markers must reference a message in the same conversation. Migration 0005 adds the
single local account and session hashes; migration 0006 adds saved reply variants
and their ratings without deleting existing conversations. Migration 0007 adds the
nullable conversation deletion timestamp.
Use `--migrate-only` to apply migrations without starting HTTP; use `--port` to
change the default port 8000. Start your configured local inference server separately.
The server requires `local_only: true` and `telemetry: false`. FastAPI tracing,
metrics, logs and automatic telemetry configuration are explicitly disabled.

Open [ReDoc](http://127.0.0.1:8000/redoc) for the reference or
[Swagger](http://127.0.0.1:8000/docs) to try requests. These documentation shells and
their pinned JS/CSS assets are served locally without authentication; they contain
no user data. Paste `HARNESS_API_TOKEN` from your private environment file into the
page to load the schema. Tokens stay in page memory, never URLs or browser storage.
The host token remains the machine and documentation credential; keep it server-only
in private environment configuration. Lucid users sign in with email/password and do
not enter an API key. On first launch, the user chooses their own email and password
in Lucid; the agent does not choose or store a plaintext password. Setup establishes
one local owner account, preserving the existing owner's UUID and conversations.
There is no public registration, password-reset endpoint or multi-user account system.

Lucid's server forwards the host bearer only for account status, setup and login.
Successful setup/login returns an opaque session, which Lucid stores in an HttpOnly,
SameSite Strict cookie with an eight-hour lifetime. Resource requests forward that
session as a bearer; logout revokes it and clears the cookie. The loopback proxy
checks write-request Origin against the request origin and rejects cross-origin
writes. Cookies use local HTTP for this loopback deployment; HTTPS and Secure cookies
are required before any future remote exposure.

Passwords are Argon2id hashes (64 MiB memory, three iterations, parallelism one).
Setup accepts 12–1024 characters; login accepts 1–1024. Email addresses are validated
and normalized. Setup/login attempts share a bounded ten-per-minute, process-local
limiter; restarting the backend resets it. PostgreSQL stores only SHA-256 session
token hashes. Sessions expire after eight hours, support revocation, and authenticate
only the configured local owner. Setup, login and status require the exact host
bearer even when a valid session is supplied. Other resource endpoints accept either
the host bearer or an unexpired, unrevoked owner session. Logout requires a session.
Authentication and validation errors do not echo credentials.

OpenAPI declares HTTP bearer security for every API operation, so generated clients
can supply the appropriate credential. The server binds only `127.0.0.1`, rejects
other Host headers, disables proxy-header trust and access logs, and returns
`Cache-Control: no-store`. CORS is disabled; Lucid connects through its same-origin
loopback proxy. The host bearer retains the full local owner's authority, including
account setup; protect it alongside database and host configuration.

| Method | Route | Body / result |
| --- | --- | --- |
| GET | `/api/auth/status` | Host bearer only; returns `{"setup_required":true}` or `{"setup_required":false}`. |
| POST | `/api/auth/setup` | Host bearer only; `{"email":"owner@example.com","password":"your chosen password"}`; initializes the existing owner once. |
| POST | `/api/auth/login` | Host bearer only; email/password; returns `session_token`, `expires_at`, and `user` (`id`, `email`, `created_at`), as does setup. |
| POST | `/api/auth/logout` | Session bearer; revokes that session, HTTP 204. |
| GET | `/api/health` | Database availability and configured model name; does not probe inference. |
| POST | `/api/conversations` | `{"title":"My chat"}` or `{}`; returns conversation, HTTP 201. |
| `/api/conversations/search` | Search saved titles and message text with owner-scoped literal keyword matching; empty query returns recent chats. |
| GET | `/api/conversations` | Conversation list; `limit` 1–100 (default 50), `offset` ≥ 0. |
| GET | `/api/conversations/{id}` | Conversation metadata. |
| PATCH | `/api/conversations/{id}` | `{"title":"Renamed"}`. |
| DELETE | `/api/conversations/{id}` | Soft deletes the conversation; retains messages and audit; HTTP 204. |
| GET | `/api/conversations/{id}/messages` | Messages by ascending `seq`; `after_seq` ≥ 0, `limit` 1–100. |
| POST | `/api/conversations/{id}/messages` | `{"content":"Hello"}`; returns `user`, `assistant`, `run_id`. |
| POST | `/api/conversations/{id}/messages/stream` | Same request; authenticated SSE answer deltas followed by a committed exchange or a sanitized error. |

Assistant replies support persistent feedback and alternatives:

| Method | Route | Body / result |
| --- | --- | --- |
| PATCH | `/api/conversations/{cid}/messages/{mid}/rating` | `{"variant_id":"UUID","rating":"up"}`; rating is `"up"`, `"down"` or `null`; returns the message. |
| POST | `/api/conversations/{cid}/messages/{mid}/regenerate` | No body; returns the message with its saved alternatives and new selection. |
| PATCH | `/api/conversations/{cid}/messages/{mid}/selection` | `{"variant_id":"UUID"}`; selects the saved answer used in canonical history; returns the message. |

Ratings belong to individual variants of owned assistant replies and survive reloads.
Regeneration runs the existing local agent through `Executor.execute`, saves and
selects an alternative, and preserves the original turns, variants and tool audit.
It uses the original user prompt and earlier turns, excluding later turns, recalled
summaries and memories. It does not replay saved tool executions; a fresh run can
propose new tool calls under the current policy and approval checks. Selecting an
alternative changes canonical history and clears the stale compaction summary;
subsequent normal sends use the selected answer. Later saved turns remain in place;
selection does not create a branching conversation tree.

Chat message content is an ordered JSONB array. User messages contain one text block,
`[{"type":"text","text":"Hello"}]`; assistant replies may interleave validated text
and chart blocks. Send only the new user text, not client-supplied
history or roles. Request bodies reject extra fields and type coercion, accept up
to 8000 characters per message / 200 per title, and have a 64 KiB byte limit.
Validation errors use a fixed `detail` code without echoing request content.

Each successful exchange is committed atomically. Model or storage failures roll
back both messages; a concurrent send, rename or delete on that conversation returns
HTTP 409. Reopening a conversation loads its saved history; inference receives at
most eight recent exchanges within an 8000-character serialized context budget.
Older messages stay in the database. Usage counts are provider-reported per turn.

Streaming uses POST and the same authentication, strict request models, owner checks,
conversation lock, LangGraph validation and Executor as normal chat. `text/event-stream`
frames contain JSON in `data:`: `{"type":"delta","text":"..."}` and optional
`{"type":"chart","chart":{"type":"chart","spec":{...}}}` in display order, then
`{"type":"complete","exchange":{...}}` after storage commits, or
`{"type":"error","detail":"fixed_reason"}`. Heartbeats are SSE comments. Swagger
and ReDoc include the typed event schemas. Raw tool proposals/results, provider reasoning fields and connection credentials
are not forwarded as stream events. Answer text may quote data a model has seen. Text and chart events are provisional display data until completion; malformed or
interrupted model decisions are not saved. On error/disconnect, reconcile against saved
history rather than retaining or replaying provisional blocks.

One stream may generate at a time per local service; another stream returns HTTP 409.
The queue holds at most 32 typed events with a five-second backpressure deadline.
Provider input is capped at 1 MiB, its accumulated decision at 256 KiB, and an SSE
provider event at 128 KiB. Use `GET /api/conversations/{id}/messages?settled=1`
to read a settled snapshot: HTTP 409 means the generation transaction still holds
the conversation lock; a successful read follows its commit or rollback. Existing run/step/token/time limits still apply. Streaming
uses the checked, pinned network transport with no redirects or proxies. Disconnect
cancels the run and interrupts its inference socket; an unfinished exchange rolls
back. The stream has no resume/replay behavior. A completion may commit just before
Stop/disconnect; reload saved history to resolve that race. Completed tool side
effects survive cancellation or rollback. Other in-process handlers stop cooperatively,
not through forced termination. Regeneration currently retains its normal JSON endpoint.

Deleting a conversation soft deletes it: its deletion and update timestamps are set,
and its messages, saved variants and tool audit remain in PostgreSQL. Deleted chats
are absent from chat lists/search and reject direct history access, sends, renames,
ratings, selection, regeneration, extraction and compaction. They cannot receive new
file/memory associations, tool requests or pending tool approvals. Files and extracted
memories remain independent owned resources, available through their own APIs; delete
those separately when desired. This is retention, not secure erasure, and there is no
restore or purge endpoint. Conversation locks serialize deletion with chat work and
new tool claims; work already claimed as running may finish after deletion.

### Weather and chart tools

`GET /api/tools` advertises strict input schemas for `weather_tool` and `chart_tool`.
They are usable through normal/streaming chat and the existing tool-call API; no tool
request bypasses Executor. Local model inference, embeddings and chart rendering stay
local. Weather is a separate, explicitly enabled external service.

`weather_tool` uses Open-Meteo's public noncommercial API without an account or key.
Its arguments are a city/location string, optional two-letter country code, forecast
days (1–7, default 3), and `imperial` or `metric` units. It returns the provider's first
geocoding match with name/country/coordinates/timezone, current conditions, dated daily
forecasts, units, source and retrieval time. Include a country code for ambiguous city
names and check the resolved location. See [Open-Meteo's terms](https://open-meteo.com/en/terms)
and [limits](https://open-meteo.com/en/pricing); the free service is noncommercial.
Host-generated attribution is retained in successful weather replies even if the model
omits it. Location search text and the server's IP reach Open-Meteo; full chat history,
session credentials and tool registry are not sent to that provider by the handler.

The default `harness.yaml` denies `weather.read`. To enable weather in trusted host
configuration, grant `weather.read: allow` under `permissions`; `ask` uses the existing
approval flow. This permission is a narrowly scoped exception to generic local-only
networking: the handler can call only HTTPS/443 at fixed paths on
`geocoding-api.open-meteo.com` and `api.open-meteo.com`. It checks all resolved addresses
are public and connects to a pinned address without redirects/proxies. Responses are
limited to 64 KiB per call and ten seconds. `network: deny` and `local_only: true` stay
in effect for generic network tools/inference; enabling weather does not grant generic
HTTP access. User tool preferences can restrict this host grant, never expand it.

`chart_tool` accepts a closed `ChartSpec`: `kind` (`line`, `bar`, `scatter`, `pie`,
`mermaid`), optional title/axis labels, category labels, named numeric series, or Mermaid
source. Charts accept at most 200 points per series, six series, and finite numbers
bounded to ±1e12. Pie charts require one nonnegative series with a positive total;
scatter x labels must be numeric. Mermaid accepts at most 12,000 characters/200 lines
in supported flowchart, sequence, class, state, ER, Gantt, mindmap, timeline or pie
syntax. It rejects node metadata, configuration directives, HTML, links, image/icon
sources, callbacks, source CSS and external resource references. This is a data
validation tool with no filesystem/network/subprocess capability; per-tool deny/ask
policies still apply.

Only successful authorized chart-tool results can appear in a reply. Each result includes
a host-generated `chart_id`; the model places `[[chart:<chart_id>]]` in its final string
where that current-run chart belongs. Host code buffers split references, resolves only
that run's approved results, and emits typed text/chart blocks. Unknown, cross-run,
malformed, unterminated or repeated references fail closed. Marker fragments are not
displayed. Unreferenced approved charts keep the legacy append fallback, so existing
models and old text-then-chart history remain compatible. The reference conveys no
permission to run another tool or access saved artifacts from other turns.

Chart-bearing replies use position-neutral visual references instead of unsupported
claims such as "the chart above". A bounded text normalizer handles common deictic
chart/diagram phrases consistently across stream chunks and saved text; code and numeric
comparisons remain untouched. Existing saved replies receive the same presentation
correction when read, including their variants, without modifying their stored rows.


For chart-related requests, host code checks common claims of a displayed visual
against successful chart-tool results. Missing results trigger at most two formatting
corrections within the existing run limits; corrections grant no permissions. Unverified
text is held until a chart succeeds or an honest text-only answer is accepted. This
presentation check does not prove all model prose truthful. Historical chart data is
included as untrusted text for follow-up requests, without reusable chart references.

Saved assistant JSONB content is ordered, with at most nine blocks (four charts and
five text blocks). A chart can appear first, between paragraphs or on its own. User
messages remain one text block. The same order is retained by normal JSON responses,
stream events, reloads, ratings, regeneration and version selection, without a migration.
Search, memory, compaction and conversation context join all text blocks, including
text after charts. Chart specs are not interpreted as instructions. Direct tool-call
responses return data; attachment to a reply happens when its chat agent runs the tool.

Lucid renders blocks within one assistant response. Numeric charts have rounded cards;
Mermaid diagrams stay unboxed. Numerical charts use local SVG with focus/hover details
and series toggles.
Mermaid is bundled locally, runs with strict host-controlled configuration, and its SVG
is sanitized before insertion with scripts, images, links, CSS and external references
removed. Diagrams support local zoom. These application checks are not an OS sandbox
or a general guarantee of silence from third-party libraries. Visual review is left to
the user.

All model and direct tool requests pass through `Executor.execute`. Calls requiring
approval are stored as `awaiting_approval`; approving through the API rechecks the
current host policy and resources, then executes the exact saved request once.
Chat text cannot approve a tool. Additional per-tool `allow`/`ask`/`deny` preferences
can restrict the host policy but cannot expand it. Declared deny/resource restrictions
always win. Tool execution logs survive failed chat turns and link to the assistant
message after successful persistence. Connection lifecycle logs have no conversation.

The API provides working operations for the remaining resources:

| API | Operations |
| --- | --- |
| `/api/me` | Inspect the host-selected local owner. |
| `/api/models` | Installed/loaded chat and embedding model sizes, local policy and free disk bytes. |
| `/api/embeddings` | Generate real local 1024-dimensional vectors from `texts`; optional `query` prefix. |
| `/api/memories` | Create/list/get/update/delete facts with vectors generated on writes. |
| `/api/memories/search` | Owner-scoped cosine search using `query`, `limit` and optional `min_score`. |
| `/api/conversations/{id}/memories/extract` | Extract new user facts with the local model; repeated calls are incremental. |
| `/api/conversations/{id}/compact` | Generate and store a local summary; keep the original messages. |
| `/api/files` | Upload base64 content, list/get metadata, download `{id}/content`, and delete. |
| `/api/tools`, `/api/tools/search` | Inspect/search the host-registered tool catalog. |
| `/api/tool-permissions` | List/PUT/DELETE additional per-tool restrictions. |
| `/api/tool-calls` | Execute a request for a conversation, list/get execution records. |
| `/api/tool-calls/{id}/approval` | Submit `{"decision":"approve"}` or `{"decision":"deny"}`. |
| `/api/mcp/servers` | Create/list/get/update/delete local HTTP MCP catalog entries and inspect cached tools. |
| `/api/mcp/connections` | Create/list/get/update/delete owned encrypted credentials; `{id}/check` and `{id}/refresh` perform real discovery. |

Install the local embedding model explicitly with `ollama pull mxbai-embed-large`
(approximately 670 MB). It emits 1024 dimensions and has a 512-token input window;
oversized embedding inputs are rejected rather than silently truncated. Configure:

```yaml
embeddings:
  endpoint: http://127.0.0.1:11434
  model: mxbai-embed-large
```

Memory search uses the configured model's vector space; old vectors from another
model are excluded until updated/re-embedded. Chat recalls up to three matching
memories using the first 400 characters of the query while sending the complete
user message to chat inference. Summaries and memories enter chat as bounded,
explicitly untrusted observations, never system messages or permission grants.
Extraction and compaction are explicit API actions, not automatic background jobs.
Cache-read usage is recorded when the provider reports `prompt_tokens_details`.

File blobs live in a host-owned private `0700` directory outside the tool workspace;
set `HARNESS_FILES_DIR` to override the default local state directory. File names are
display names; clients cannot choose storage paths. Credentials use Fernet encryption
with `HARNESS_CREDENTIAL_KEY`; generate a key after installation with:

```bash
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Save that key privately in `.env.local` and retain it across restarts. The API never
returns token plaintext or ciphertext. Losing the key requires reconnecting those
servers. OAuth connections accept separately obtained tokens and track expiration;
automatic external OAuth login is not implemented.

For MCP, provide an actual local **streamable HTTP** server, allow its exact host
and port in trusted YAML, and review each tool's capabilities/resources. Pin its
catalog name to the exact endpoint as well:

```yaml
mcp_endpoints:
  reviewed_server: http://127.0.0.1:9000/mcp
mcp_permissions:
  reviewed_server:
    reviewed_pure_tool:
      capabilities: []
      resources: {}
```

The server connection itself also needs the configured `network` startup grant;
connection approval never authorizes a tool. Catalog metadata never supplies these
mappings. Changing server identity clears its credentials and requires reconnection.
The HTTP API rejects stdio commands and legacy SSE connection setup; trusted library
hosts can still provide separately reviewed clients to `MCPAdapter`. It does not
launch arbitrary commands or invent missing integration credentials. Streamable HTTP
JSON and event-stream responses are supported with bounded parsing and session checks.

There is no separate cancellation endpoint, automatic action retry or interrupted-run
resumption. Normal JSON requests can leave a bounded turn running after disconnect;
streaming requests propagate disconnect cancellation.
Completed tool side effects cannot be undone by database rollback: inspect tool-call
history before retrying a request whose result was lost. PostgreSQL intentionally
stores text, memories and tool arguments/results. Database administrators, host
configuration and credential-holding local processes are trusted; loopback checks are
not an OS sandbox. Secure and back up the database, blobs and encryption key yourself.

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
The noninteractive CLI installs no approval callback; tools requiring `ask` deny.
The HTTP backend records pending requests for explicit API approval.
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
The HTTP backend supports explicit local compaction and stored chat reopening.
Interrupted-run resumption is not implemented.

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
