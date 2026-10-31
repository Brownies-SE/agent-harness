# Lucid chat search

User supplied a two-pane chat search reference. Keep Lucid's current design and add a sidebar Search chats entry/Cmd-K dialog: search at top, dated results left, real conversation preview right, open selected chat. Use current theme on desktop and a list/preview flow on mobile. No new chat header, fake media search or private-chat mode.

Search titles and saved canonical message text through an authenticated owner-scoped PostgreSQL query, literal and case insensitive. Empty search returns recent conversations. Bound query to 200 characters, results to 100, validate strict Pydantic request/response. No vector retrieval, schema migration, index engine or new dependencies needed for the local dataset.

- [x] Backend fail-first regression for search, ownership, literal wildcard/injection input and authentication; models/routes/service.
- [x] Lucid modal, sidebar shortcut, bounded proxy route, debounced requests and stale response guards; focus/keyboard/mobile states.
- [x] Full Postgres/LangGraph tests, Ruff/format/audit; frontend tests/types/build/audit; real browser search/title/body/preview/open/keyboard/theme/mobile checks.
- [x] Independent review and backend PR/CI; preserve user data, leave separate frontend local without Git checkout.

Verification: 308 backend tests with actual PostgreSQL/pgvector and LangGraph passed; Ruff check/format, dependency audit and diff check passed. Frontend16tests, TypeScript, build, formatting and audit passed. Real Chrome searched title/body through the API, previewed seq67 in an80message chat and opened savedhistory; recentdategroups, emptyresults, keyboardshortcut, light/dark and mobilepreview/back passed. Synthetic API browser test verified stale query/preview rejection and focus/keyboard behavior. No externalruntime requests or pageerrors observed. Independent review found no actionableissues. Fixtureaccounts/data used the testdatabase and were removed. Normal services restarted at8000/3000; backenddelivery gatedbyPR16CI, frontendremainsseparate withoutGitcheckout.
