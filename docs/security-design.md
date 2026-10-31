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

The HTTP backend and CLI have no interactive approval UI or approval callback;
tools requiring `ask` fail closed. Only a trusted library host may install a callback.
OpenAPI declares bearer authentication, and the HTTP boundary validates its token
before reading a request body. Internal execution/security modules live in `core/`;
tool implementations live in `tools/`.

The PostgreSQL schema includes the full requested catalog, connection, permission,
tool-call, file and memory tables. These are storage contracts, not execution grants.
No service automatically registers tools, changes host policy, decrypts credentials,
replays tool calls or loads memories from those rows. MCP schemas and any future
recalled content remain untrusted. File/memory references and compaction markers are
scoped at the database boundary; this does not replace authorization in future routes.
Credential-writing services must encrypt token bytes with trusted host-managed keys.

Run limits, cancellation and metadata-only local history apply to LangGraph. Arbitrary
Python/MCP handlers and DNS remain cooperative; stronger containment requires OS-level
isolation. Third-party dependencies and inference engines are separate trust boundaries.
No guarantee is made that arbitrary executable dependencies never perform network I/O.

Only Brownies-SE may change main/dev. PRs, trusted CI/CodeQL and secret push protection
remain required. Force pushes/deletion are blocked. Independent human approval is optional
for this solo project; authorized agents use the owner's account after checks pass.
