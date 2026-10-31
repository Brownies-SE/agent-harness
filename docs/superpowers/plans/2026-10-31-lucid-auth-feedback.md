# Local login, feedback and original Lucid controls

User correction: preserve Lucid's design, remove the added chat header, restore love/dislike/regeneration, and use real email/password login instead of asking for the harness token.

- [x] Backend local account setup/login/logout and expiring/revocable sessions, Argon2id password hashes, first owner UUID preserved. Setup/login protected by host token; Lucid server alone supplies it. Resource requests use scoped session bearer tokens.
- [x] Persist ratings per owned assistant reply and add safe answer regeneration with variant selection. Variants and ratings survive refresh; regeneration invokes the existing local agent/Executor, preserves original turns and tool audit, and does not replay stored tool executions.
- [x] Lucid uses HttpOnly SameSite Strict local session cookies and email/password setup/login. Host token stays server-only in ignored environment configuration. Same-origin writes and loopback proxy restrictions remain enforced.
- [x] Restore original message buttons and remove chat header. Put Harness/chat management in existing sidebar menu. Keep design and local-only defaults.
- [x] Add bypass regressions before security edits, run complete Postgres/LangGraph suite, Ruff, audit, frontend tests/check/build/audit, browser tests and independent review. Update PR and wait required checks; no merge.

Single local owner account, preserving existing data. First-run setup establishes that owner's email/password; this is not public multi-user registration. Passwords never enter chat, logs, source files or prompts. Argon2id and HttpOnly session guidance: https://argon2-cffi.readthedocs.io/en/stable/api.html and https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html . HTTP cookies are loopback-only; use HTTPS before any future remote exposure.

Execution evidence:
- Complete local PostgreSQL/pgvector + actual LangGraph suite: 305 passed.
- Frontend proxy/content suite: 16 passed; TypeScript, production build and formatting passed.
- Backend and frontend dependency audits report no known vulnerabilities.
- Live Chrome verified first-owner setup, session restoration, password rejection,
  logout/revocation/failure handling, persisted per-version ratings, regeneration,
  version selection, memory search, authenticated files, MCP/tools and explicit
  tool approval. Desktop/mobile checks and delayed-load regressions passed with
  no external runtime requests or page errors. Synthetic test accounts used the
  separate test database and were removed; the real owner remains unconfigured.
- Independent review's future-memory recall and failed-logout findings were
  reproduced before fixes, corrected and verified.
- Backend wheel/sdist build passed, including six incremental SQL migrations.
- Normal backend/UI restarted on ports 8000/3000. User chooses real credentials
  on first-run setup. Lucid remains separate and has no Git checkout; backend
  changes go through PR #16 and required CI. No merge authorized.
