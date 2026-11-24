# MCP Post-Deploy Verification — Capability Delta

## ADDED Requirements

### Requirement: One reusable Access-authenticated MCP smoke
`actions/mcp-smoke` SHALL perform MCP `initialize` and `tools/list` against an upstream, portal or dynamic-portal URL, assert expected tools and optional safe read-only `tools/call`s, and inject `CF-Access-Client-Id/-Secret` from optional inputs, masked, without ever printing them.

#### Scenario: Valid service token
- **WHEN** the token is admitted and the expected tools are exposed
- **THEN** the smoke passes and the job summary labels it service-token

#### Scenario: Rejected token, missing tool or Bypass
- **WHEN** the token is refused, an expected tool is missing, or an anonymous request is not refused
- **THEN** the smoke fails with a credential-free diagnostic

#### Scenario: Dynamic portal
- **WHEN** the portal URL carries `optimize_context=search_and_execute`
- **THEN** `portal_query_tools` and `portal_execute` SHALL be exposed, upstream tools SHALL be discoverable through the query tool and callable through the execute tool

### Requirement: Fail-loud, serialized capability sync after a verified production deploy
`_mcp-post-deploy.yml` SHALL sync the registered portal servers only for a verified Production push to main without rollback, SHALL serialize per portal without dropping pending syncs, and SHALL fail on any status other than `ready`.

#### Scenario: PR Preview or unverified delivery
- **WHEN** the delivery is not Production, not verified, rolled back or not a push to main
- **THEN** no sync and no portal smoke run

#### Scenario: Simultaneous MCP deployments to one portal
- **WHEN** two apps deploy to the same `portal_key`
- **THEN** their sync + portal smoke run one after the other, both execute

### Requirement: Backward compatibility
The change SHALL NOT alter `_cf-worker-v2.yml` inputs, outputs, jobs or check names; non-MCP consumers are unaffected.
