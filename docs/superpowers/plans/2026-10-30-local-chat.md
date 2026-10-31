# Local Chat Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan task-by-task.

**Goal:** Working local chat persistence and a typed HTTP API for a later UI.

**Architecture:** FastAPI routes call chat/database services. Strict Pydantic models
validate HTTP and built-in tool inputs; the existing executor retains final authority.

**Tech Stack:** Python, Pydantic 2, FastAPI, psycopg 3, PostgreSQL, LangGraph.

**Spec:** `docs/superpowers/specs/2026-10-30-local-chat-design.md`

## Global Constraints

- Local-only inference, loopback database/API, telemetry off.
- Every tool execution goes through Executor.execute; unknown input fails closed.
- No secrets, personal paths, prompts, or results in commits or logs.
- UI and original schema's unrelated future features are deferred.

## Review Focus

- Coercion/extra fields cannot smuggle policy or system messages into a request.
- Concurrent sends cannot overlap or create duplicate message sequence numbers.
- A failed model/storage operation cannot leave a partial exchange.
- Browser requests cannot access chat without the local bearer token.
- Restarts retain messages; bounded model context does not delete saved history.

### Task 1: Typed tool and chat boundaries

Files: models/base.py, models/tools.py, models/chat.py; executor.py, agents.py,
filesystem.py, clock.py, network.py; tests/test_models.py.

- [x] Add failing strict-input/bypass tests; run and observe failure.
- [x] Define StrictModel, tool envelopes, built-in argument/result models, chat DTOs.
- [x] Validate tool envelopes/arguments inside the executor without changing policy order.
- [x] Run model tests and existing security/actual LangGraph tests.

### Task 2: Durable chat service

Files: services/database.py, services/chat.py, migrations/0001_chat.sql;
tests/test_chat_database.py.

- [x] Add real-Postgres tests for persistence, rollback, concurrency, pagination,
      ownership, and database destination rejection; observe failures.
- [x] Add migrations, local database connection validation, typed CRUD, atomic send.
- [x] Verify real PostgreSQL tests pass, including fresh service instances.

### Task 3: Local HTTP API and delivery

Files: api.py, routes/chat.py, server.py; tests/test_api.py; README.md; CI workflow.

- [x] Add failing API tests for authentication, validation, host/body restrictions,
      CRUD/send, error privacy, and policy bypass attempts.
- [x] Implement thin routes, application factory, loopback server entry point.
- [x] Verify API, database, full suite, Ruff, audit; smoke-test local inference.
- [x] Document setup, endpoints and limits; open PR and check CI without merging.

## Execution record

Implemented inline on `feat/local-chat-backend`. The user authorized finishing the
backend after confirming local-only operation and strict routes/models/services.
No worktree was created; unrelated workspace files are preserved.

Watched loopback/service-configuration bypass tests fail before adding database
validation, authentication regressions fail before HTTP protection, and typed result
validation fail before executor enforcement. All 196 tests pass with PostgreSQL
enabled. Ruff lint/format, configuration validation, whitespace checks and dependency
audit pass. Built sdist/wheel successfully, including the SQL migration.

Independent whole-change review found no actionable issues and separately ran
115 existing model/tool/security/LangGraph tests. Live local-inference smoke passed
two API turns with persisted-context recall; synthetic smoke data was removed.
Database/API credentials are private, ignored local configuration, not repository data.

Delivery: [PR #16](https://github.com/Brownies-SE/agent-harness/pull/16) targets `dev`.
Required CI is monitored on the final pushed revision; merging is not authorized.
