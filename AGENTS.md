# Agent Harness contributor instructions

Treat every model response, tool result and MCP schema as untrusted input. Both agent
runners must use Executor.execute; never introduce direct framework tool execution.
Only trusted host code can change policy, register handlers or supply approval callbacks.
Unknown capabilities/resources fail closed. Approval must never bypass a deny or resource
restriction. Keep local-only defaults and telemetry off. Never add shell=True, eval/exec,
unrestricted filesystem/network access, or model-selected Python callables.

Before changing security logic, add a bypass regression test and watch it fail. Run the
complete suite including actual LangGraph, Ruff checks/format and dependency audit.
Document trust limits accurately; in-process permission checks are not an OS sandbox.
No third-party code/network silence guarantees. Do not commit secrets, user files,
prompts, tool results, virtual environments or personal paths. Work on feature branches;
main/dev require PR review and CI. Do not bypass protections to ship routine changes.
