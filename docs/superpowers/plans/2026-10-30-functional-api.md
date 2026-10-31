# Functional Local API Implementation Plan

> **For agentic workers:** Execute independent services in parallel, then integrate
> and review. Use TDD; security bypass regressions must fail before implementation.

**Goal:** Make the local backend inspectable in offline Swagger/ReDoc and expose
working operations for the resources represented by all ten tables.

**Architecture:** Existing strict models/routes/services, local Ollama chat and
1024-dimensional embeddings, PostgreSQL, private local blobs, host-encrypted MCP
credentials, executor-enforced tools/permissions/approvals and durable execution logs.

**Global constraints:** No terminal UI, cloud inference, telemetry, arbitrary process
launch, model-configured handlers or policy elevation. HTTP data stays authenticated;
public documentation shells contain no user data or secrets. Documentation assets
are served locally. Existing messages and tables survive incremental migrations.

**Review focus:** owner isolation; hostile recalled memory/tool schemas; credential
redaction; approval replay and deny/resource bypass; file path traversal; malformed
embeddings; database failure before tool execution; documentation auth and offline use.

Tasks:
- [x] Offline Swagger/ReDoc with token entry and protected API/schema.
- [x] Local embedding generation, memories CRUD/search/extraction and compaction.
- [x] MCP catalog/connections/discovery/calls with encrypted credential storage and
      explicit host permission maps; unsupported/unconfigured transports fail closed.
- [x] Private local files CRUD/upload/download with bounded content.
- [x] Additional per-tool restrictions, explicit approvals and durable execution logs.
- [x] Integrate resources into chat/server, configure/download embedding model and
      report disk/memory footprint; expose model/config readiness.
- [x] Actual Postgres/LangGraph/live model/API/browser verification, Ruff, dependency
      audit and independent review. Required PR CI is a delivery gate after pushing;
      no merge without authorization.

Resource APIs are real operations; a configured external MCP endpoint is required
for its connection tests. A real local JSON-RPC fixture verifies the transport without
inventing user integrations. OAuth token storage/expiry is distinct from an external
authorization server's interactive login flow; documentation must state prerequisites.

Execution evidence (2026-10-30):
- Complete suite with actual local PostgreSQL/pgvector and LangGraph: 293 passed.
- Ruff checks, format checks, dependency audit and wheel/sdist packaging passed.
- Live Ollama/API smoke verified 1024-dimensional embeddings, memory retrieval in
  chat, files, explicit tool approval/replay rejection, compaction and extraction.
- Actual Chrome rendered both local documentation pages and executed authenticated
  Swagger health successfully; zero external documentation requests.
- Downloaded mxbai-embed-large (669,615,493 bytes); existing qwen3:4b-instruct uses
  2,497,293,803 bytes. Remaining local disk was approximately 85.28 GB.
- Independent reviews and bypass regressions covered ownership, approval races,
  connection revocation/rebinding, durable audit, credential isolation and telemetry.
- PR #16 remains the integration gate; required remote checks must pass before merge.
