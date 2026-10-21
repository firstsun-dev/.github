# MCP Service Hosting Capability — Path-Based Host Contract

This is a **proposed organization-wide cross-repository capability**, not a description of already deployed behavior. It becomes normative for implementations only when the corresponding OpenSpec change is approved.

## ADDED Requirements

### Requirement: One canonical hostname SHALL expose independently named MCP endpoints

The organization SHALL use `https://mcp.firstsun.org/{service}/mcp` as the canonical remote Streamable HTTP MCP endpoint format. A registered service SHALL expose Streamable HTTP at its exact `/{service}/mcp` path, NOT at bare `/{service}` or an additional nested `/mcp`.

#### Scenario: ANAS is migrated
- **WHEN** the approved ANAS migration is deployed
- **THEN** the canonical endpoint SHALL be `https://mcp.firstsun.org/anas/mcp`
- **AND** a client using that URL SHALL reach the ANAS MCP handler after authentication

#### Scenario: Another MCP service is proposed
- **WHEN** Infra MCP or Knowledge MCP is onboarded
- **THEN** its reviewed endpoint SHALL be `https://mcp.firstsun.org/infra/mcp` or `https://mcp.firstsun.org/knowledge/mcp`, respectively
- **AND** onboarding SHALL NOT require changing the ANAS MCP application implementation

### Requirement: Service paths SHALL be unique, explicit and collision-safe

Every registered `{service}` SHALL have one documented owner and route mapping. Slugs SHALL match `^[a-z][a-z0-9-]{1,31}$` and SHALL NOT use reserved platform names (`mcp`, `health`, `cdn-cgi`, `.well-known`). Routing SHALL NOT conflate a slug or endpoint with longer prefixes, suffixes or trailing-slash variants.

#### Scenario: Ambiguous request path
- **WHEN** a client requests `/anas`, `/anas/mcp2`, `/anas/mcp/` or `/infra2/mcp`
- **THEN** it SHALL NOT be dispatched as `/anas/mcp` or `/infra/mcp`
- **AND** it SHALL NOT invoke another registered service's tools

#### Scenario: Unknown service path
- **WHEN** a client requests a path without a registered service
- **THEN** the request SHALL NOT reach a usable MCP handler or expose protected resources

### Requirement: Cloudflare routing SHALL keep services independently deployed

`infra-config` SHALL own hostname and path-routing resources in Terraform. The organization SHALL prefer native Cloudflare Workers Custom Domain plus path-specific Workers Routes for this phase, without a new application-level MCP Gateway. The existing ANAS Custom Domain MAY remain the unmatched-path origin if the verified routing and security gates pass.

#### Scenario: A future service is added
- **WHEN** `/infra/mcp` is registered and its Worker is deployed
- **THEN** a reviewed path route SHALL select the Infra Worker ahead of the hostname's unmatched-path origin
- **AND** ANAS and Infra SHALL have separately deployable Workers and failure handling
- **AND** each application repository SHALL NOT create competing Terraform routing resources

#### Scenario: Path-based routes are not safe on this hostname
- **WHEN** route precedence, DNS, path exactness or fallback handling cannot be verified as safe
- **THEN** onboarding SHALL stop for architecture review
- **AND** an unapproved Gateway, broad wildcard or public fallback SHALL NOT be introduced

### Requirement: Access policies and JWT audiences SHALL be isolated per MCP service

Each deployed MCP service SHALL have a path-scoped Cloudflare Access application and documented client policies. Its origin SHALL validate the signed Access assertion against its expected issuer and application audience before any MCP tool execution or protected data access.

#### Scenario: ANAS clients authenticate
- **WHEN** an approved interactive client uses Managed OAuth or an approved headless client uses its Service Token for `/anas/mcp`
- **THEN** Access and ANAS origin-side verification SHALL both succeed before MCP initialization
- **AND** the current ANAS dual-mode authentication semantics SHALL remain intact

#### Scenario: Wrong service audience
- **WHEN** a request presents an assertion for a different service or an invalid/missing assertion
- **THEN** the target Worker SHALL reject it before initializing MCP or touching protected resources
- **AND** no service SHALL inherit another service's token policy by virtue of a shared hostname

#### Scenario: Alternate ingress
- **WHEN** someone tries an unapproved `workers.dev` hostname, Preview URL or other alternate ingress for ANAS
- **THEN** it SHALL NOT bypass Cloudflare Access / origin validation

### Requirement: OAuth discovery SHALL identify the exact requested service

Managed OAuth's unauthorized challenge and its advertised protected-resource metadata SHALL identify the intended endpoint and resolve to a client-compatible authorization flow. Multiple Access applications on `mcp.firstsun.org` SHALL NOT produce ambiguous or cross-service authorization behavior.

#### Scenario: OAuth client connects to `/anas`
- **WHEN** an OAuth-capable MCP client starts unauthenticated against `/anas/mcp`
- **THEN** the HTTP challenge and resource metadata SHALL guide it to the ANAS-specific protected resource
- **AND** successful authorization SHALL allow MCP initialization without weakening the Access gate

#### Scenario: Two different Access applications share the hostname
- **WHEN** both ANAS and Infra endpoints are registered
- **THEN** the discovery/authorization test SHALL prove their resource identities and origin audience checks are distinct
- **AND** failure to prove this SHALL block rollout pending a revised design

### Requirement: The `/anas` to `/anas/mcp` move SHALL not retain compatibility

The ANAS cutover SHALL retire bare `/anas` and keep root `/mcp` retired. Neither old path SHALL receive a redirect, alias, proxy shim or dual-serving period.

#### Scenario: Owner reconnects a client
- **WHEN** ANAS production passes the `/anas/mcp` authentication and protocol smoke tests
- **THEN** the operator SHALL manually update existing client settings to `/anas/mcp`
- **AND** neither bare `/anas` nor root `/mcp` SHALL initialize ANAS MCP

### Requirement: Cross-repository ownership and release gates SHALL be explicit

`.github` SHALL own the common hosting convention, `infra-config` SHALL own edge routing and Access Terraform, and each MCP repository SHALL own its handler, HTTP contract, test suite and client-facing integration documentation.

#### Scenario: A specification PR is merged
- **WHEN** the organization-wide OpenSpec is approved
- **THEN** no Terraform apply, Worker deployment, secret change or MCP client reconfiguration SHALL be implied by that merge

#### Scenario: Production migration is attempted
- **WHEN** implementation PRs are ready
- **THEN** a reviewed cross-repo rollout plan and Cloudflare Access/AUD assessment SHALL precede changes
- **AND** negative path/auth tests, actual OAuth initialization, headless token authentication and runtime smoke tests SHALL pass before declaring the cutover complete

