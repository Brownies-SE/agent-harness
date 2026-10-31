# Soft deletion and inline chat names

Conversation deletion now records deleted_at instead of deleting rows. Messages,
answer versions, file associations, memories and tool audit remain stored. Deleted
conversations disappear from active lists/search and reject direct chat access,
new uploads/tool requests, compaction/extraction and pending approvals. Started
tool work may finish; soft deletion is not cancellation. No restore/purge UI was
added.

Lucid edits the existing chat-name input in both search results and sidebar.
Enter saves; Escape or clicking away cancels. No chat-name browser prompt is used.
The sidebar expands before editing a name if it was collapsed.

Validation: security regressions failed before backend changes. Complete actual
PostgreSQL/pgvector and LangGraph suite:314 passed. Ruff, format, dependency audit,
frontend17tests, TypeScript and production build passed. Independent source review
confirmed active guards/lock order; collapsed-sidebar rename finding was fixed.
No visual/UI verification ran, per user preference. Backend schema migrates
incrementally to version7. Services restarted with matching local configuration;
private config remains ignored and uncommitted. PR16/required CI is the integration
gate; no merge authorized.
