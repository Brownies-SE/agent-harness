# Security policy

The model proposes. The harness decides. Never give an untrusted model direct OS,
filesystem, network or MCP access. Policy is trusted host configuration.

## Reporting
Use GitHub's private vulnerability reporting feature on this repository when available
(Security -> Report a vulnerability). Do not post secrets or exploitable details in a
public issue. There is no guaranteed response SLA. Only the latest main branch is
currently maintained.

## Trust boundary
This core validates model output and gates registered tools; it does not sandbox Python,
third-party handlers, native dependencies, inference servers or MCP processes. Review
extensions before registration and isolate executable components at the OS level.
Local-only defaults constrain intentional harness traffic; they cannot guarantee arbitrary
third-party code never makes requests. See README for the four network boundaries.

## Repository controls
Both main and dev should require PRs, passing CI, resolved review conversations and an
independent review; reject force pushes/deletion and apply rules to admins. GitHub admins
can still change repository settings. Workflows use minimal permissions, pinned actions,
locked packages and no pull_request_target execution. Never put credentials in examples,
workflow files or committed model transcripts. Feature work belongs on separate branches.
