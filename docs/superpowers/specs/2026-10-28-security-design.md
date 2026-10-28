# Agent Harness: model-is-untrusted security design

The model proposes. The harness decides.

## Intent and scope
Build a Python working security core for Native and optional LangGraph agents. A model
is an untrusted source of decisions and JSON tool requests, never a holder of tool
implementations or credentials. The user supplied the security requirements and
authorized implementation and public GitHub publication with main and dev branches.

## Approach
A shared in-process policy/executor is the smallest useful implementation. Both runners
use that exact executor. Framework-specific middleware would duplicate enforcement;
an OS sandbox would offer stronger containment but is a separate deployment boundary.
These are implementation choices, not a claim of OS isolation.

## Pipeline
Model -> request envelope validation -> registered schema validation -> resource
validation -> immutable policy -> optional host-side approval -> trusted tool handler.
Unknown tools, capabilities, schema/resource mappings and malformed policies fail closed.
Policy cannot be changed by a model message, tool output, MCP metadata or approval.
An approval only satisfies ask rules; it never bypasses deny rules or resource limits.

## Defaults
local_only=true; telemetry=false; no filesystem roots; network default deny;
all capabilities deny unless configured; no shell/subprocess tool. Inference has a
separate user-configured local endpoint, checked before sending any prompt. Loopback
and explicitly configured RFC1918/IPv6 ULA inference endpoints are allowed; public,
link-local and metadata destinations are rejected. Tool networking requires network
capability AND an exact destination/port allowlist. Local-only rejects public targets
even when present in an allowlist. No redirects, proxies or DNS re-resolution between
validation and connection. HTTPS retains hostname verification using a pinned address.

## Filesystem
Explicit user roots; reject traversal, symlinks in path components, special files and
hardlinks. POSIX descriptor-relative no-follow opens enforce reads and writes at use
time. Sensitive filenames/directories are denied even inside roots, unless explicitly
listed in trusted configuration. Paths cannot escape roots via approvals. Concurrent
renames of directories by another OS process remain an OS isolation concern.

## MCP
Discovery is schema inspection, not a permission grant. A trusted administrator maps
each server/tool to capabilities and resource fields. Unknown tools remain unavailable.
Unreviewed tools default to denial. Connecting remote servers requires network policy
and is rejected in local-only mode. Local stdio servers require explicit subprocess
permission before startup; this version provides a transport-neutral adapter for a
host-managed client and does not automatically launch servers. An MCP process can use
its own OS privileges: apply OS sandboxing/firewall rules to contain it.

## Approvals and audit
Allow/deny/ask per capability. Host callback returns once/run/deny; absent callback
means deny. Run grants are scoped to tool, exact canonical arguments and capabilities,
not blanket privileges. Audit events contain generated IDs, tool names and reason
codes, never raw prompts, arguments, URLs, file contents or tool results. Host audit
sink failures prevent execution. Tool outputs are labeled untrusted data.

## Native and LangGraph
A bounded native loop and a real optional StateGraph share request execution. No
ToolNode, eval, exec or model-selected arbitrary Python callable. Structured model
responses have strict envelope and tool argument validation. Tool errors and denials
become bounded observations without returning exception strings or secrets.

## Boundaries and non-goals
The core is not a sandbox against malicious Python extensions, inference servers,
dependencies or MCP binaries. Local inference is distinct from tool, MCP and dependency
networking. No unsolicited telemetry integrations. Do not guarantee third-party silence.
No unrestricted shell, browser, Docker or Git tools in this initial release. Capability
names reserve policy vocabulary; registering a tool requires a trusted code review.
No cloud adapter in this first local-first core.

## Verification and repository controls
Adversarial parity tests exercise native and compiled LangGraph execution; filesystem,
network, schema, repeated denial, approvals, MCP, prompt injection, and inference
configuration tests are required. GitHub CI has read-only permissions and pinned actions.
Protect main/dev with PR and CI requirements, prevent force pushes and deletion, enable
secret scanning/push protection and dependency alerts where supported.
