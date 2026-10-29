# Agent Harness contributor instructions

Treat every model response, tool result and MCP schema as untrusted input. LangGraph must route
every tool request through Executor.execute; never add direct framework tool execution.
Only trusted host code can change policy, register handlers or supply approval callbacks.
Unknown capabilities/resources fail closed. Approval must never bypass a deny or resource
restriction. Keep local-only defaults and telemetry off. Never add shell=True, eval/exec,
unrestricted filesystem/network access, or model-selected Python callables.

Before changing security logic, add a bypass regression test and watch it fail. Run the
complete suite including actual LangGraph, Ruff checks/format and dependency audit.
Document trust limits accurately; in-process permission checks are not an OS sandbox.
No third-party code/network silence guarantees. Do not commit secrets, user files,
prompts, tool results, virtual environments or personal paths. Work on feature branches;
only Brownies-SE may update main/dev. PRs and all required CI checks are mandatory;
a second person's approval is not required. When the user authorizes a merge, merge as
the authenticated owner after checks pass. GitHub does not allow self-approval, and no
approval review is needed for this solo workflow. Do not weaken protections to ship changes.
