# Security design

The model proposes. The harness decides.

LangGraph is the only agent backend. Model decisions pass through strict JSON/schema
validation and Executor.execute before any trusted tool handler runs. No ToolNode or
framework callback may bypass policy, resource validation, approvals or audit events.

Local-only inference uses a user-configured local endpoint. Tool networking separately
defaults to deny and requires an exact host/port grant. Requests use checked pinned IPs,
HTTPS verification and bounded network I/O. Telemetry is disabled by default.

Filesystem tools require explicit roots. Descriptor-relative no-follow opens block
symlink traversal; sensitive paths, hardlinks and special files are restricted. Directory
listing is bounded and filters blocked entries. Writes require the configured permission
or host approval. The clock declares no privileged capability and cannot grant others.

MCP tools require trusted host capability/resource mappings. Discovery and server hints
never grant authority. Approvals satisfy ask rules only and cannot override denials or
resource limits. Tool results and schemas remain untrusted data.

The CLI has no interactive approval UI; missing callbacks deny `ask` tools.
The HTTP backend installs a trusted callback that records pending requests without
executing them. An authenticated explicit approval rechecks current policy/resources
and can execute only the saved request once. Stored tool preferences add restrictions;
an `allow` row cannot override host denials. Each approval claim is atomic, and stale
completion/failure callbacks cannot overwrite a separately claimed execution.
OpenAPI declares bearer authentication, and the HTTP boundary validates the host
bearer or an eligible owner session before reading a request body. Internal
execution/security modules live in `core/`; tool implementations live in `tools/`.

Lucid provides email/password authentication for one local owner. The user chooses
the account email and password on first run; the agent does not choose a password.
Setup preserves the existing owner's UUID and data. Account status, setup and login
require the exact host bearer, supplied by Lucid's server from private environment
configuration. A valid session cannot authorize setup or account changes. Setup is
serialized and atomic; subsequent setup attempts fail. There is no public
registration, password reset or multi-user account management. The host bearer
continues to authorize machine clients and documentation with full owner authority;
it must stay server-only in Lucid and outside source control.

Passwords are stored as Argon2id hashes with 64 MiB memory, three iterations and
parallelism one. Credential models reject extra fields and coercion, validate and
normalize emails, and bound setup passwords to 12–1024 characters and login passwords
to 1–1024. Login failures use a generic error. A bounded, process-local limiter allows
ten setup/login attempts per minute and resets on process restart; it is not a
distributed abuse-control system.

Sessions use 32 random bytes in an opaque bearer, with only a SHA-256 token hash
persisted in PostgreSQL. Resource authentication requires an unexpired, unrevoked
session matching the fixed configured owner and the local account. Sessions expire
after eight hours; logout revokes the supplied session. Synchronous session database
checks run in a worker thread at the HTTP boundary, and authentication failures are
sanitized. The host bearer remains an alternative resource credential; session
bearers cannot satisfy host-only account endpoints.

Lucid keeps sessions in HttpOnly, SameSite Strict cookies and sends them to the
backend through its same-origin loopback proxy. Writes require a matching Origin;
backend destinations remain loopback-restricted. The host bearer is not sent to the
browser. Local HTTP cookies lack Secure for this loopback deployment; HTTPS and
Secure cookies are required before any future remote exposure. Cookie, host and
in-process permission checks are not an OS sandbox and do not protect against a
compromised trusted local host or database administrator.

The PostgreSQL-backed resource services enforce owner scope and strict request models.
Tool call records are written before execution, including separate host connection
lifecycle records, and storage failures prevent execution. MCP tools register only
from host-reviewed capability/resource mappings pinned to exact local endpoints.
API catalog edits cannot transfer grants or existing credentials to a new identity;
identity changes clear encrypted credentials and require reconnection. Connection
tokens are encrypted with a private host-managed Fernet key and are never returned.
Server identity/credential reads and catalog edits use transaction locks to avoid
credential rebinding races. stdio commands are not accepted by the HTTP API.

Assistant feedback and saved variants enforce conversation ownership and require an
assistant reply and a variant belonging to that reply. Ratings are per variant and
accept only up, down or null. Regeneration uses the existing agent/Executor and
current policy; it does not replay stored tool executions or grant new permissions.
Its context contains only the original prompt and earlier turns, excluding later
turns and recalled memories/summaries. Original turns, alternatives and tool audit
remain stored. Variant selection changes canonical message content and clears stale
compaction summaries; later saved turns are retained rather than forming a branch.
Fresh regeneration can produce new tool side effects under normal checks, and a
storage rollback cannot undo completed tool operations.

Memories use bounded local embeddings, owner-scoped vector search and model-space
identity. Recalled text and summaries are untrusted observations, never authoritative
instructions. Extraction/compaction make tool-free local inference calls and validate
structured outputs. File blobs use private host-owned directories outside tool roots,
random keys and descriptor-relative no-follow I/O; clients cannot select storage paths.
File/memory references and compaction markers are scoped at the database boundary.

Swagger and ReDoc shells/assets are public only at fixed paths after Host validation;
the schema and API data remain bearer-protected. Documentation serves pinned assets
locally, does not persist tokens, and only adds authorization to same-origin requests.

Run limits, cancellation and metadata-only local history apply to LangGraph. Arbitrary
Python/MCP handlers and DNS remain cooperative; stronger containment requires OS-level
isolation. Third-party dependencies and inference engines are separate trust boundaries.
No guarantee is made that arbitrary executable dependencies never perform network I/O.

Only Brownies-SE may change main/dev. PRs, trusted CI/CodeQL and secret push protection
remain required. Force pushes/deletion are blocked. Independent human approval is optional
for this solo project; authorized agents use the owner's account after checks pass.
