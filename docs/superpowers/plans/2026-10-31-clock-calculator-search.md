# Date/time, calculator and web search implementation plan

> **For agentic workers:** Use executing-plans to implement this plan in the current feature branch.

**Goal:** Give local chat reliable date/time, bounded arithmetic and web search through Executor.

**Architecture:** Extend the existing clock tool without removing its UTC/local fields. Add strict Pydantic calculator and search contracts and register them with the existing built-in registry. Reuse the existing execution routes, permission/audit services and checked network transport.

**Tech stack:** Python standard library, Pydantic, existing FastAPI/LangGraph/Postgres stack; local SearXNG JSON API preferred for account-free search.

## Constraints and review focus

- All model requests go through Executor; host-only policy and provider configuration.
- No eval/exec, Python callable selection, arbitrary URL fetch, redirects or automatic result fetching.
- Arithmetic has bounded expression length, AST complexity, depth, exponents and decimal magnitude; reject non-finite values and unsupported syntax.
- Time conversion requires explicit offsets; reject ambiguous naive timestamps and invalid zones.
- Search defaults denied, uses a fixed loopback service path and bounded results; queries and snippets are untrusted. Search service contacts external engines and is not covered by in-process sandbox claims.
- No UI changes or browser verification. Existing model streaming consumes ordinary tool results.

## Tasks

- [x] Add failing clock conversion/difference/invalid-input tests, calculator arithmetic/precision/unsafe-syntax/budget/deny tests. Implement strict models and tools and run focused tests.
- [x] Add failing search capability/resource/input/provider/redirect/permission tests before extending security logic. Implement a host-configured loopback SearXNG adapter, install/configure the local search service, and verify real results.
- [x] Verify tools via existing API, actual local model and streaming; document setup and trust limits. Run full PostgreSQL/LangGraph suite, Ruff format/check and dependency audit. Obtain independent code review, push existing feature PR and wait for required CI. Restart local backend. Do not merge.

Provider selection confirmed: local SearXNG, no API key. Live installed local model invoked all three tools successfully with durable audit and matching streamed/saved responses. Final PR checks are tracked on the feature PR.
