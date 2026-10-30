# Grafana MCP Onboarding — `mcp-service-hosting` capability delta

This is an **additional proposed** cross-repository contract built on `openspec/changes/standardize-mcp-path-hosting/`. It does not claim to describe a currently deployed Grafana MCP service.

## ADDED Requirements

### Requirement: Grafana SHALL use the canonical shared-hostname path with independent ownership

The organization SHALL register `grafana` as the service slug, `https://mcp.firstsun.org/grafana/mcp` as the only remote MCP endpoint, `firstsun-dev/grafana-mcp` as the Proxy Worker repository, and a dedicated Worker + Cloudflare Access application/AUD. `infra-config` SHALL own its edge Terraform, NAS service and VPC Service.

#### Scenario: Approved client requests Grafana MCP

- **WHEN** an approved client uses `https://mcp.firstsun.org/grafana/mcp`
- **THEN** its authenticated request SHALL reach only the Grafana MCP proxy and NAS-hosted official Grafana MCP server
- **AND** no ANAS MCP tools SHALL execute

#### Scenario: A lookalike or unknown path is requested

- **WHEN** a caller requests `/grafana`, `/grafana/mcp/`, `/grafana/mcp2`, `/grafana-other/mcp`, a non-canonical encoded variant or an unregistered service path
- **THEN** no Grafana or ANAS MCP tool SHALL initialize through that request

### Requirement: Adding Grafana SHALL preserve ANAS Custom Domain, identity, and routing

`infra-config` SHALL add an exact, path-specific Workers Route for Grafana ahead of the existing `mcp.firstsun.org` Custom Domain, without replacing or migrating the ANAS Worker, its DNS, authentication application, Service Tokens or AUD.

#### Scenario: Grafana Route is activated

- **WHEN** `mcp.firstsun.org/grafana/mcp` is registered
- **THEN** the Grafana Worker SHALL receive only requests matched to its exact path
- **AND** existing `/anas/mcp` Managed OAuth and headless clients SHALL continue to work unmodified
- **AND** the Grafana Worker SHALL NOT use a public `fetch(request)` fallback to the ANAS Custom Domain

### Requirement: The Grafana origin SHALL use the existing NAS Tunnel without public exposure

The Grafana Worker SHALL access a private, single-host/single-port HTTP Workers VPC Service bound to the existing `firstsun-nas` Tunnel. A new Tunnel, publicly routable NAS port, new DNS/public origin hostname or broad network binding SHALL NOT be introduced by this change.

#### Scenario: Grafana MCP is reachable through VPC

- **WHEN** an authorized Worker requests the registered Grafana VPC Service
- **THEN** traffic SHALL reach only the designated NAS-local Grafana MCP port over the existing Tunnel
- **AND** the origin SHALL remain inaccessible through an unauthenticated public address

#### Scenario: Tunnel is incompatible with the private transport

- **WHEN** QUIC, routing, host/port reachability or Streamable HTTP over VPC cannot be verified
- **THEN** production onboarding SHALL stop for design review
- **AND** no public-access fallback or destructive Tunnel modification SHALL be applied

### Requirement: Grafana authentication SHALL be isolated and verified at both boundaries

Grafana SHALL have a separately scoped Cloudflare Access application offering Managed OAuth and explicitly approved headless Service Tokens. Before VPC forwarding, the Worker SHALL validate the signed Grafana Access assertion's issuer, signature, AUD and time claims. It SHALL not forward caller identity/authentication material to the NAS; the upstream SHALL require an independent origin token.

#### Scenario: Grafana OAuth client completes authorization

- **WHEN** an approved OAuth client follows an unauthenticated `/grafana/mcp` challenge, resource metadata, PKCE authorization and token exchange
- **THEN** the advertised protected resource SHALL identify Grafana rather than ANAS
- **AND** an authorized MCP initialize SHALL succeed without bypassing Access

#### Scenario: Cross-service or spoofed authorization

- **WHEN** an ANAS-only JWT, expired/tampered assertion, invalid Service Token or caller-supplied identity header reaches Grafana
- **THEN** the proxy SHALL reject it before an upstream request or tool invocation
- **AND** neither another service's AUD nor its secret SHALL be accepted

#### Scenario: Client attempts a Grafana credential override

- **WHEN** a client supplies an Authorization bearer, `X-Grafana-URL`, `X-Grafana-Service-Account-Token`, `X-Grafana-API-Key` or another unapproved origin-control header
- **THEN** the proxy SHALL strip or reject the override
- **AND** the only upstream authentication SHALL be the proxy's own secret and the official MCP server's dedicated read-only Grafana account

### Requirement: The official NAS-hosted Grafana MCP SHALL be read-only and protocol-correct

The NAS SHALL run an explicitly pinned official Grafana MCP server with Streamable HTTP at `/grafana/mcp`, globally disabled write tools, least-privilege Grafana API credentials, restrictive Host/Origin validation, and no publicly exposed MCP port. The Proxy Worker SHALL preserve the upstream's protocol status, headers and stream semantics.

#### Scenario: Approved client reads monitoring data

- **WHEN** an authorized client initializes MCP and requests supported metrics, dashboards or alert information
- **THEN** the call SHALL use the existing Grafana datasource and return data under an approved read-only Grafana service account
- **AND** the original event-stream and MCP session headers SHALL remain valid across the Worker, VPC and Tunnel hops

#### Scenario: Client requests a mutation

- **WHEN** a client attempts to create, change or delete Grafana resources through MCP
- **THEN** the operation SHALL be unavailable or denied
- **AND** Grafana resources SHALL remain unchanged

#### Scenario: Backend is unhealthy or overloaded

- **WHEN** the origin, VPC Service or Grafana datasource times out or fails
- **THEN** the proxy SHALL return a bounded fail-closed error without credential leakage or a public alternate origin
- **AND** existing NAS monitoring and ANAS MCP availability SHALL not be intentionally disrupted

### Requirement: Grafana rollout SHALL be gated and reversible without collateral changes

A specification-only commit SHALL NOT create production resources. Any implementation SHALL use independently reviewed cross-repository changes, an operator-approved full refreshed Terraform plan and live two-service acceptance evidence. No existing Tunnel, Custom Domain, DNS, ANAS Access or ANAS Worker resource may be replaced or destroyed as part of Grafana onboarding.

#### Scenario: A Terraform plan includes unrelated drift

- **WHEN** a full refreshed plan proposes changes outside the approved Grafana resource set, or any replacement/destruction of existing resources
- **THEN** application SHALL stop and require an explicit new operator decision
- **AND** the Grafana change SHALL NOT silently include or fix that drift

#### Scenario: Grafana cutover or rollback

- **WHEN** the new service passes OAuth, Service Token, wrong-AUD, exact-route, streaming, read-only and ANAS regression tests
- **THEN** its external route MAY be enabled with explicit operator approval
- **AND** any rollback SHALL affect only Grafana-specific Worker, Access/Route/VPC registrations and NAS container, leaving ANAS, shared Custom Domain and NAS Tunnel intact
