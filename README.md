# Agent Harness

**The model proposes. The harness decides.**

A working Python security core for running untrusted local models with explicitly
scoped tools. Native and LangGraph agents use the **same policy and executor**.
A tool request is a proposal, never a permission grant.

```text
Native Agent ────┐
                ├── Tool request → Validation → Policy → Approval → Executor
LangGraph Agent ┘                                             ├── Native tool
                                                              └── MCP tool
```

This initial release includes a CLI, an OpenAI-compatible local inference adapter,
bounded text file tools, an explicitly gated HTTP GET tool, a transport-neutral MCP
adapter, and adversarial tests. It does not ship shell, Git, Docker or process-control
tools. Their capability names are reserved for explicitly reviewed future tools.

## Quick start

Python 3.11+ on Linux or macOS is required for filesystem tools. Install uv separately,
then install the locked dependencies. Installation downloads packages and is separate
from running in local-only mode.

```bash
uv sync --locked --extra dev --extra langgraph
uv run --no-sync agent-harness validate --config harness.yaml
```

Start your own local inference server supporting `POST /v1/chat/completions`, install
a model there, and set `model.name` in [harness.yaml](harness.yaml) to that model's name.
The example endpoint is `http://127.0.0.1:11434`; configure your actual endpoint explicitly.
No model weights are downloaded or launched by this project.

```bash
uv run --no-sync agent-harness run --prompt 'Read README.md and summarize it.'
uv run --no-sync agent-harness run --agent langgraph --prompt 'Read README.md.'
```

Use `--no-sync` after installation to avoid dependency synchronization during a run.
The example permits reads only inside `./project`, asks before writes there, and denies
all tool networking and subprocess execution. An empty `Policy()` denies every
capability and filesystem root. CLI results and security events are JSON-escaped to
avoid terminal control injection. Audit events go to stderr; redirect them to a local
file if persistence is needed. Prompts passed with `--prompt` can appear in your OS
process list and shell history; avoid putting secrets in CLI arguments.

## What local-only means

`security.local_only: true` is the default. `security.telemetry: false` is also the default.

| Boundary | Enforcement and limits |
| --- | --- |
| **Model inference** | Only the user-configured localhost, loopback, RFC1918 private-network or IPv6 ULA endpoint. Every resolved address must pass local-only checks before a prompt is sent. Cloud provider names, public addresses, link-local/metadata addresses and redirects are rejected. The endpoint operator can still see prompts/results; a LAN server is a separate machine. |
| **Network tools** | Denied by default. Require an allowed/approved `network` capability **and** an exact hostname and port allowlist. Local-only additionally rejects public destinations, even if allowlisted. Connections use a validated pinned IP with HTTPS hostname verification and TLS 1.2 minimum, without proxies or redirects. |
| **MCP servers** | Connection startup is gated independently from tool execution. Remote transports require network permission and URL policy; stdio requires subprocess permission. Discovered tools need explicit host-side mappings. Server metadata never grants permission. A server can perform hidden filesystem/network operations under its own OS privileges. |
| **Dependencies/integrations** | The harness does not intentionally export prompts, tool results, source code or traces by default. LangGraph runs explicitly disable LangSmith tracing when telemetry is false, including environment-enabled tracing. Arbitrary third-party Python packages, plugins, inference engines and MCP processes cannot be guaranteed never to make network requests. Installation, CI, vulnerability auditing and dependency updates intentionally use external services. |

Local-only is an application policy, **not an OS sandbox or an air-gap guarantee**.
For stronger containment run the harness and each MCP server in separate restricted
processes/containers with an OS firewall, read-only/limited mounts and no ambient
credentials. Keep an inference server isolated too. Local model weights receive
serialized messages and tool schemas, not Python objects or tool implementations.
A malicious inference engine or installed plugin is executable trusted code outside
what model-output validation can contain.

There is no automatic telemetry integration in the native runner. Explicitly enabling
`security.telemetry` permits LangGraph's LangSmith tracing according to the host's
LangSmith configuration; it can export user data. Keep it false for local data confinement.

## Permissions and resource policy

The vocabulary is `filesystem.read`, `filesystem.write`, `network`, `subprocess`,
`process.read`, `process.write`, `docker.read`, `docker.write`, `git.read`, `git.write`.
Each is `allow`, `deny` or `ask`; omitted/unknown capabilities deny. Unknown config
fields, duplicate YAML keys, invalid types and unsupported schema references are rejected.
The only supported network default is `deny`.

```yaml
security:
  local_only: true
  telemetry: false
filesystem:
  roots: [./project]
permissions:
  filesystem.read: allow
  filesystem.write: ask
  network: ask
network:
  default: deny
  allow: [localhost, 127.0.0.1]
  ports: [8080]
```

Inference has a separate user-configured endpoint grant, so denying network tools does
not disable local inference. Private networks are not implicitly trusted for tools:
add each destination and port explicitly. Hostname and port lists form a cross-product;
use separate harness instances for policies that need different host/port combinations.
`local_only: false` removes the local-address restriction but retains destination,
capability and argument checks for tools; this release still has no cloud provider SDK.

Paths are constrained to configured roots, with `..`, symlinks, hardlinks, special files,
secret-like filenames and common credential/browser directories denied. Existing paths
are opened descriptor-relatively using POSIX no-follow flags; file size and output size
are bounded. Writes cannot create parent directories. Exact `filesystem.sensitive_allow`
paths allow intentional exceptions but never escape roots. This filename list is a
heuristic, not a secret detector: avoid mounting unrelated data or credentials at all.
Concurrent renames by another OS process and malicious native code require OS isolation.

Approvals are host-side terminal interactions: `y` once, `a` this exact request for the
run, `n` deny. Noninteractive sessions and missing/failing approval callbacks deny.
A run approval is scoped to tool + canonical arguments + capabilities, resets between
agent runs, and never bypasses deny rules, paths or network restrictions. One executor
belongs to one sequential run at a time; do not share it across concurrent runs.

## Registering a native tool

Only trusted application code registers handlers. No model-generated code is executed.

```python
from agent_harness import Executor, Policy, Tool

executor = Executor(Policy())
executor.register(
    Tool(
        name="double",
        schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        capabilities=(),  # Pure computation; no OS capabilities.
        handler=lambda value: value * 2,
    )
)
assert executor.execute({"tool": "double", "arguments": {"value": 4}}) == 8
```

Filesystem and network tools must declare a required string resource argument mapped
as `resources={"path": "path"}` or `resources={"url": "url"}`. Registering code is
responsible for complete capability declarations and actually using checked resources.
The built-in filesystem/HTTP tools enforce these checks at use time; an arbitrary
extension cannot be made safe merely by declaring metadata. Schemas use a bounded subset of JSON Schema: basic types, closed objects, arrays,
required fields, enum/const and size/numeric bounds. Regex, patternProperties, schema
references, combinators, uniqueItems and unknown keywords are rejected; deeply nested
or oversized schemas are rejected before validation. MCP discovery registers a whole
batch atomically, so failed schema validation exposes no partial tool list.
Schema validation, capability
checks and approvals happen before the handler. Arguments, requests and outputs are
size-bounded JSON. A handler failure becomes a fixed reason code, not raw exception text.
The loop is bounded to eight steps by default. LangGraph has no alternative ToolNode path.

## MCP integration

`MCPAdapter` is a working synchronous, transport-neutral integration boundary. It does
not bundle an MCP SDK or automatically launch arbitrary servers. Supply a trusted client
with `list_tools() -> list[dict]` and `call_tool(name, arguments) -> JSON` methods.
A client wrapping an async SDK must provide its own synchronous/lifecycle bridge.

```python
from agent_harness import Executor, Policy
from agent_harness.mcp import MCPAdapter, MCPPermission

# Host-approved stdio startup: default Policy() would deny before calling the factory.
executor = Executor(Policy(rules={"subprocess": "ask"}), approve=host_approval)
adapter = MCPAdapter(executor, "reviewed_server", reviewed_stdio_client_factory)
adapter.connect(
    {
        "add": MCPPermission(capabilities=()),  # Reviewed pure-computation tool.
    }
)
```

For HTTP supply `endpoint=` and a factory receiving the validated `Target`; the factory
must use its pinned address and preserve HTTPS verification. A reviewed stdio factory
must use fixed argument arrays, `shell=False`, command policies, timeouts, bounded output
and minimal environment/credentials. **This package supplies neither an unrestricted
shell nor an automatic subprocess runner.** Factories and SDKs are separate trusted code.

Connection approval never grants tool approval. Discovery inspects schemas; only tools
listed in the host permission map are registered. Schema resource mappings are validated
before use. Server `readOnlyHint`/other annotations and model requests cannot elevate
permissions. Capabilities do not intercept covert I/O inside a server; use OS containment.
Caller-owned clients must bound execution/discovery and close resources. Failed
discovery invokes client.close() if provided. Arbitrary Python
handlers and MCP calls cannot be safely preempted by this synchronous in-process core.

## Prompt injection and audit

System/user instructions and tool data are separate. Tool observations carry
`trust: untrusted_data`; they never configure policy or the registry. The model may
still follow injected instructions, so security is enforced outside the model.
The tests deliberately make the model follow an injected instruction and verify denial.

Audit event names include `security.tool_allowed`, `security.tool_denied`,
`security.approval_requested`, `security.approval_granted`, `security.approval_denied`,
`security.network_blocked`, `security.path_blocked`. Events contain request IDs, registered
tool names and fixed reason codes, excluding prompts, raw paths/URLs, arguments and
results. The host approval callback needs actual arguments to make a decision; do not
send that callback to an external service. Audit sink failures prevent tool execution.
The default library sink is a no-op; provide a durable local sink for audit retention.
Events are not a tamper-proof forensic log. External inference checks fail closed but
are not currently emitted as tool audit events.

## Development

```bash
uv sync --locked --extra dev --extra langgraph
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pip-audit --skip-editable
```

CI tests Linux/macOS and actual compiled LangGraph execution, checks formatting, audits
locked dependencies and runs CodeQL. Action references are pinned to commit IDs and
workflow permissions are read-only except the CodeQL security-results upload job.
`main` is the default release branch; `dev` is the integration branch. Use feature
branches and pull requests. See [the design](docs/superpowers/specs/2026-10-28-security-design.md),
[implementation plan](docs/superpowers/plans/2026-10-28-security-core.md) and
[security policy](SECURITY.md). This initial core has adversarial regression coverage;
it has not received an independent penetration test or sandbox certification.
