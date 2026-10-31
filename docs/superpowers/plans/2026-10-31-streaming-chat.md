# Streaming Chat Implementation Plan

**Goal:** Real optional streaming through the existing local authentication, model, executor and persistence boundaries.

**Architecture:** Keep POST messages JSON; add POST messages/stream with typed SSE events (delta, complete, error). Stream only the JSON final-answer string provisionally, validate the complete decision before persisting, and never dispatch tools outside Executor. Use the existing pinned network transport with bounded incremental reads. A bounded worker queue propagates disconnect cancellation into RunControl. Normal and streaming share ChatService.send.

**Constraints:** Local-only, telemetry off, strict Pydantic events, no credentials/tool data in events, no automatic replay. Preserve normal API. Owner/deletion checks before stream and in transaction. Cancellation rolls back an unfinished turn; already completed tool side effects cannot be undone. Stream completion follows commit. One active stream per service, bounded queue/bytes/deadline. UI user verifies visuals.

## Tasks
- [x] Add failing security/lifecycle tests: streaming deny/unknown proposals never execute, malformed events/JSON fail closed, owner/deleted guards, cancellation rolls back, completion persists once.
- [x] Implement pinned transport chunk callbacks, provider SSE decoding and final-answer projection; test live incremental arrival and cancellation with a local HTTP test server.
- [x] Add typed API stream session and document OpenAPI events, error/commit/disconnect limits. Reuse ChatService.send and LangGraph.
- [x] Add failing proxy/parser tests, then bounded incremental forwarding through existing cookie and origin checks with cancellation.
- [x] Wire Settings streaming default, Stop and frame-paced soft text reveal with reduced-motion support; reconcile saved history on stop/error. Keep safe Markdown renderer.
- [x] Run complete Postgres/LangGraph suite, Ruff checks/format, dependency audit; frontend unit/type/build checks. Smoke actual local model stream through API without browser. Update docs, commit backend feature branch and push PR; wait required CI.


## Verification

- Failing security regression observed before streaming agent path was implemented.
- Real local provider socket test proves output is consumed before completion and cancellation interrupts a stalled read.
- Service tests cover cancellation during SQL writes, settled history, disconnect/slot cleanup and resolvable OpenAPI event schemas.
- Actual installed local model streamed through the API against the test database; deltas matched the two persisted messages. Fixture conversation was removed.
- Peer review identified rendered-height following and stop/save reconciliation; both fixed and re-reviewed without remaining blockers.
- Visual verification intentionally left to the user. Regeneration keeps its existing JSON endpoint.
