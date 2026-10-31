# Backend Review Fixes Implementation Plan

> **For agentic workers:** Use executing-plans inline with test-driven-development.

**Goal:** Resolve the three approved review findings and organize the backend.

**Architecture:** Preserve the local HTTP chat and executor boundary. Add a second
schema migration, remove interactive terminal code, declare bearer authentication,
and group shared implementation modules without adding compatibility wrappers.

**Tech Stack:** Existing Python, Pydantic, FastAPI, psycopg, PostgreSQL and pgvector.

**Spec:** The user's original ten-table SQL and approval of review findings 1–3
supersede the earlier decision to defer parts of that schema.

## Global Constraints

- Preserve existing conversations and messages through an incremental migration.
- All ten requested tables, four missing chat columns and VECTOR(1024) must exist.
- Stored policy/schema metadata cannot grant permissions or register handlers.
- No interactive terminal chat or approval UI; retain noninteractive admin commands.
- Keep local-only defaults, telemetry off, and executor-only tool execution.
- API authentication remains enforced before request-body handling.
- UI and runtime MCP/auth/files/memory workflows are separate from schema setup.

## Review Focus

- Migration preserves v1 rows, applies once, and rejects unsupported versions.
- Compaction markers cannot reference another conversation's message.
- Unknown/denied tool proposals remain denied after imports and CLI changes.
- OpenAPI declares authentication; malformed/missing/wrong tokens still fail closed.
- Installed packages include both migrations and no obsolete terminal module.

### Task 1: Complete the schema

Files: migrations/0002_resources.sql; services/database.py, services/chat.py,
models/chat.py; tests/test_chat_database.py.

- [x] Write and observe failing full-schema and upgrade-preservation tests.
- [x] Add remaining tables, columns, pgvector and scoped relationship constraints.
- [x] Add ordered migration runner and update existing strict chat contracts.
- [x] Enable pgvector with the local admin in dedicated app/test databases; verify.

### Task 2: Remove terminal UI and organize modules

Files: cli.py, core/, tools/, routes/boundary.py, dependent imports and docs.

- [x] Write regressions for command help/no interactive default and denied ask tools.
- [x] Remove terminal chat/approval/formatting and their obsolete tests; retain core
      context/security tests outside removed UI tests.
- [x] Move agent, executor, policy, schema, runtime and errors to core/; built-in
      filesystem/network/clock and MCP code to tools/; HTTP boundary to routes/.
- [x] Update all callers, tests and examples; preserve top-level public exports.

### Task 3: Declare authentication and deliver

Files: routes/chat.py; tests/test_api.py; README.md; CI workflow.

- [x] Observe OpenAPI authentication regression fail; declare HTTP bearer security.
- [x] Run actual PostgreSQL and LangGraph suite, Ruff, audit and packaged build.
- [x] Restart local backend, verify upgraded schema and live local chat.
- [x] Complete independent review and prepare the PR #16 update.

Delivery gate: push the final revision and verify every required CI check in GitHub.
Do not merge. CI status is tracked on PR #16 rather than copied into this document.


## Execution record

The five new regressions failed against the prior implementation: missing schema,
missing OpenAPI authentication, interactive default, retained chat command, and
installed terminal approval callback. Fixed those behaviors and grouped core/tool
modules. Shared context validation and configured-model tests were retained when
obsolete terminal tests were removed.

192 tests pass with actual PostgreSQL and LangGraph. This includes v1 SQL upgrade
preservation, same-conversation compaction markers, owner-scoped file/memory links,
delete behavior, idempotent migrations, and denial despite stored allow policies.
Ruff lint/format, whitespace checks, configuration validation, dependency audit,
sdist/wheel build and wheel import/resource/tool execution checks pass. Independent
review found no actionable issues.

The dedicated app and test databases have pgvector enabled with administrator
credentials; application login privileges were not expanded. The app database was
upgraded with existing chat row counts preserved. Live local-model API smoke passed
two persisted turns after restart, including context recall and new response fields.
All synthetic smoke data was removed. Credentials remain ignored local configuration.
