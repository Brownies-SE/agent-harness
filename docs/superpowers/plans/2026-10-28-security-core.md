# Security Core Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan task-by-task.

**Goal:** Build and publish the deny-by-default Agent Harness core.
**Architecture:** Immutable host policy and one validating executor; trusted built-ins
and reviewed MCP adapters sit behind it. Both agent runners use the same executor.
**Tech Stack:** Python 3.11+, jsonschema, PyYAML, optional LangGraph; pytest and Ruff.
**Spec:** ../specs/2026-10-28-security-design.md

## Global Constraints
Model requests never grant permissions. local_only=true and telemetry=false by default.
No unrestricted shell. Public repository, main and dev. Do not claim OS containment.

## Review Focus
- Symlinks/hardlinks and special files must fail at the actual file operation.
- Public IPs hidden behind DNS, redirects, IPv6 or local-only allowlists remain blocked.
- Approval reuse must not authorize different arguments or change deny/resource rules.
- MCP metadata cannot grant capabilities; hidden MCP activity requires OS containment.
- LangGraph and malformed model responses cannot bypass the shared executor.

### Task 1: Shared security boundary
Files: src/agent_harness/{policy,executor,filesystem,network,config}.py; tests/test_security.py.
Interfaces: Policy, Tool, Executor.execute(request), SecurityError; policy capability,
path and URL checks; schema-validated immutable tool registrations.
- [x] Write adversarial policy/executor/filesystem tests; run and observe failure.
- [x] Implement strict config, capability/resource checks, approval and audit.
- [x] Implement bounded no-follow filesystem tools and pinned network transport.
- [x] Run security tests and resolve failures.

### Task 2: Agents, model and MCP
Files: src/agent_harness/{agents,model,mcp,cli}.py; tests/test_agents.py.
Interfaces: NativeAgent.run(prompt), LangGraphAgent.run(prompt), LocalModel,
MCPAdapter.register(discovered, trusted mapping); one Executor for all paths.
- [x] Write runner parity, malicious inference and MCP mapping tests; observe failure.
- [x] Implement bounded runners, local JSON model adapter and transport-neutral MCP adapter.
- [x] Add CLI validate/run, example policy, README and trust-boundary documentation.
- [x] Run complete suite, lint and dependency audit.

### Task 3: Publication and hardening
Files: .github/workflows/ci.yml, .github/dependabot.yml, SECURITY.md.
- [x] Pin resolved dependencies and CI action commits; CI must test actual LangGraph.
- [ ] Review full source and security tests; resolve material issues with regressions.
- [ ] Commit, create public Brownies-SE/agent-harness and push main/dev.
- [ ] Enable repository controls and branch protection; verify CI and remote settings.

## Execution ledger
User authorized building and publication. New isolated repository; no existing code touched.


## Verification ledger
- Security boundary tests initially failed with missing core, then 40 passed.
- Runner/MCP/config tests initially failed with missing modules, then 81 passed.
- Regression tests reproduced approval reuse and MCP tool-name collision; fixes: 84 passed.
- Independent review found schema CPU exhaustion and partial MCP registration. Both
  reproduced in tests, fixed via restricted/bounded schemas and atomic registry rollback
  with failed-client cleanup; 88 tests passed. Reviewer confirmed fixes with 5 focused tests.
- Ruff checks/format, CLI policy validation, wheel/sdist build and dependency audit passed.
- Removed unused vulnerable bootstrap setuptools and synchronized uv.lock; no known
  vulnerabilities in the tested environment. This is not an independent penetration test.
