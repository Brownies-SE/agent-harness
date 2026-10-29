# Run controls, local history and real evaluations

> **For agentic workers:** Use executing-plans to implement task-by-task.

**Goal:** Implement the first three improvements authorized by the user: bounded runs,
local metadata-only history, and repeatable evaluations against the configured local model.
**Architecture:** One RunControl context shared by native/LangGraph and their executor.
SQLite stores only whitelisted metadata outside model-accessible filesystem roots. Local
model evaluations run synthetic tasks in temporary workspaces through the real executor.
**Tech Stack:** Existing Python 3.11+, SQLite/stdlib, pytest, optional existing LangGraph.
**Spec:** User-approved priorities 1–3 in this conversation; security design remains binding.

## Global constraints
No model-granted authority, retries of side effects, new cloud traffic, prompt/result logs,
new dependencies, protected-branch bypasses, or unrelated machine-wide changes.
Defaults: max_steps=8, timeout_seconds=120, max_output_tokens=512 per model request.
Cancellation/deadlines are cooperative around arbitrary Python/MCP calls and DNS. Built-in
network I/O gets an absolute timeout/cancellation monitor. No claim of an OS sandbox.

## Review focus
- Cancellation/deadline reached after approval must prevent dispatch and approval reuse.
- Network trickle responses must not keep resetting the total I/O deadline.
- Tool/model errors must never enter durable metadata as raw exception strings.
- History must not be writable through approved model filesystem roots.
- Evaluations must score actual tool use/outcomes and report failures, not just completion.

### Task 1 — Shared run controls
Files: runtime.py; agents.py, executor.py, model.py, network.py, config.py; tests/test_runtime.py.
Interfaces: frozen RunLimits; RunControl.activate/check/cancel; current_run(); RunStopped.
- [x] Write failing tests for bounds, both-runner cancellation/deadlines and no post-approval dispatch.
- [x] Implement shared lifecycle, configurable limits, model request token ceiling and bounded network I/O.
- [x] Run new tests and full security suite.

### Task 2 — Metadata-only history and CLI
Files: history.py, cli.py; tests/test_history.py, tests/test_cli.py.
Interfaces: RunHistory.begin/append/finish/list_runs/events; CLI run/history flags.
- [x] Write failing tests for persistence, secret exclusion, failed/cancelled runs and unsafe storage roots.
- [x] Implement private SQLite storage, run IDs, timings, status and metadata inspection.
- [x] Wire SIGINT/SIGTERM cancellation and approval polling; run tests and formatting.

### Task 3 — Task evaluations and publication
Files: evaluations.py; tests/test_evaluations.py; README.md, harness.yaml.
Interfaces: evaluate(config, runner, repeats, limits, history, model_factory) -> JSON metrics.
- [x] Write failing evaluation tests checking synthetic reads, two-file reasoning, approved writes and denials.
- [x] Implement randomized fixtures, outcome/tool-use scoring and local eval CLI.
- [x] Run actual Ollama evals through native and LangGraph; report observed reliability honestly.
- [x] Run suite, lint, dependency audit, independent review; fix material issues with regressions.
- [ ] Push feature branch/create PR targeting dev and verify CI without bypassing required human review.

## Ledger
Ruling: use the existing clean checkout on a dedicated feature branch; preserve local Ollama
configuration and virtual environment. Do not change global Python links in this task.
Ruling: arbitrary synchronous third-party calls/DNS cannot be safely killed in-process;
cooperative checks and bounded built-in I/O are explicit limits, rather than unsafe thread killing.


## Verification ledger
Baseline 91 tests passed. Run-control tests failed before implementation, then 107 passed.
History tests failed before implementation, then 119 passed. CLI tests failed before
implementation, then 122 passed. Evaluator tests failed before implementation, then
129 passed. Two history-boundary regressions reproduced, then fixed; suite 131 passed.
Initial live Qwen3 4B Instruct evaluation: 5/10 tasks passed in ~14.5s across Native and
LangGraph. Failures are preserved, not converted to success or suppressed.
Independent review found wrong-target denial scoring, non-object provider JSON escaping
normalization, and late cancellation being marked completed. Added 8 failing regression
cases; parent also added 2 failing cases for actionable redacted failure reasons. All
fixed in one pass. Final fix-pass suite: 141 tests passed; Ruff check/format passed; dependency audit found
no known vulnerabilities. Repeated live evaluation: 11/20 (55%) in ~24.5s. All report cases
failed report_missing, all .env cases failed invalid_model_response, one lookup answer
mismatched; these failures remain visible. A normal real-model CLI run completed a file
read with default durable history and per-run limits. GitHub CI still to be verified.

Final: fixed wrong-target scoring — test_denial_tasks_require_the_exact_target_once
RED→GREEN; exact target/count tracked as ephemeral boolean counters, no raw args persisted.
Final: fixed non-object provider JSON — test_non_object_provider_json_is_a_safe_model_error
RED→GREEN for native and LangGraph.
Final: fixed late cancellation/deadline classification —
test_backend_unwinding_cannot_turn_a_stopped_run_into_success RED→GREEN for both backends.
Final: fixed failure labels — tests preserve fixed error codes and classify missing reads.
No material review findings deferred. No automatic retries, checkpointing or context
compression introduced; they belong to the later priorities outside this authorization.

Network verification also covers a peer that accepts TCP but never sends HTTP headers.
Connection establishment polls nonblocking sockets for cancellation/deadlines, so it does
not depend on cross-thread shutdown behavior for pending TCP handshakes. A full-backlog
probe was discarded because the OS can legitimately reset it immediately, not stall.

Final local verification after network hardening: 142 tests passed; Ruff, audit and wheel/sdist build passed.

Initial GitHub Linux/macOS tests and audit passed. CodeQL flagged URL-substring task
selectors in the scripted test model as incomplete URL sanitization. Replaced selectors
with explicit fixture prompt prefixes; production URL checks and evaluation target
validation are unchanged. No scanner suppression/exclusion was introduced.
