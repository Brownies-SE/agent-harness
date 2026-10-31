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
OpenAPI declares bearer authentication, and the HTTP boundary validates its token
before reading a request body. Internal execution/security modules live in `core/`;
tool implementations live in `tools/`.

The PostgreSQL-backed resource services enforce owner scope and strict request models.
Tool call records are written before execution, including separate host connection
lifecycle records, and storage failures prevent execution. MCP tools register only
from host-reviewed capability/resource mappings pinned to exact local endpoints.
API catalog edits cannot transfer grants or existing credentials to a new identity;
identity changes clear encrypted credentials and require reconnection. Connection
tokens are encrypted with a private host-managed Fernet key and are never returned.
Server identity/credential reads and catalog edits use transaction locks to avoid
credential rebinding races. stdio commands are not accepted by the HTTP API.

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
