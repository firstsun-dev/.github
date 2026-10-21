# Design: path-based Firstsun MCP hosting

## Baseline (verified on 2026-10-21)

- `infra-config/terraform/cloudflare/firstsun/anas_mcp_access.tf` provisions `cloudflare_workers_custom_domain` for `mcp.firstsun.org` pointing directly at Worker `anas-mcp`. Its Access application source currently targets `mcp.firstsun.org/anas` and supports both Managed OAuth and Service Token.
- `anas-mcp/src/index.ts` fixes `MCP_PUBLIC_HOSTNAME` to `mcp.firstsun.org` and sets `createMcpHandler(..., { route: "/anas" })`. The Worker verifies `Cf-Access-Jwt-Assertion` before invoking the MCP handler; `GET /health` is the existing minimal public exception.
- `anas-mcp/wrangler.jsonc` disables `workers_dev` and `preview_urls`; its `ACCESS_TEAM_DOMAIN` and `ACCESS_AUD` correspond to the path-scoped Access application.
- Cloudflare Custom Domains are **hostname-wide** (not path-wide). Workers Routes can take precedence over a Custom Domain on the same hostname, provided route matching and underlying proxied DNS are valid. They must be verified as actual edge behavior, not assumed from hostname semantics.

## Canonical public contract

`https://mcp.firstsun.org/{service}/mcp` is the *complete client-configured MCP Streamable HTTP endpoint*. Bare `/{service}` is only a namespace and SHALL NOT initialize MCP. No second `/mcp` suffix follows `/{service}/mcp`.

A service slug must be registered once, be lowercase ASCII, and match `^[a-z][a-z0-9-]{1,31}$`. Reserved names include `mcp`, `health`, `cdn-cgi` and `.well-known`. Slugs are organization-level service identities, not necessarily the Worker or repo name. A registration maps one service slug to one owning repository, Worker, protected endpoint and Access application.

The MCP handler accepts supported Streamable HTTP methods at its **exact** `/{service}/mcp` path. Bare `/{service}`, trailing slashes, longer prefixes and extra suffixes SHALL NOT initialize MCP. A trailing slash, suffix path or URL normalization must be explicitly tested; clients should use the exact canonical URL.

## Cloudflare ingress and routing

### Initial ANAS cutover

Retain the current `mcp.firstsun.org` Custom Domain origin attached to `anas-mcp` unless a Terraform plan or edge verification identifies a blocking condition. Since the Custom Domain matches the hostname, the same Worker can serve `/anas/mcp` directly after its handler change; **it does not require a second DNS name or a Gateway**.

Update the existing ANAS Access application from `mcp.firstsun.org/anas` to exactly `mcp.firstsun.org/anas/mcp`, coordinated with the Worker route change. Bare `/anas` SHALL stop serving MCP and retired root `/mcp` SHALL remain unusable; do not redirect either old endpoint or leave an auth bypass.

The current public `GET /health` is an explicit minimal operational exception. It is not the canonical MCP endpoint, not an organization-wide health aggregator and must not reveal service data. Do not change it only to make the endpoint migration appear cleaner; handle any future per-service health design in its own scope.

### Additional services

When `/infra/mcp` or another service endpoint is actually implemented, `infra-config` shall add a dedicated Cloudflare Workers Route to its Worker on `mcp.firstsun.org` and a separately scoped Access application. The more-specific path Worker Route runs in preference to the hostname Custom Domain's ANAS origin. Do not move ANAS code into that Worker, proxy requests through ANAS, or copy generic Cloudflare route Terraform into each consumer repo.

Use an **exact route** for the canonical endpoint, for example `mcp.firstsun.org/infra/mcp`. If a service requires subpaths, register the separately bounded `mcp.firstsun.org/infra/mcp/*` pattern and assert that `/infra/mcp2` and `/infra-other/mcp` are excluded. Never approximate a service prefix with `mcp.firstsun.org/infra/mcp*`: Cloudflare documents that `/path*` can match `/path2`.

A Worker serving one registered service must reject unrelated paths, even if requests arrive at that Worker through the hostname's Custom Domain fallback. Unknown routes, bare `/anas`, retired root `/mcp`, and typo paths shall not invoke MCP tools. Distinguish a harmless 404/410 from authenticated/unauthenticated behavior where appropriate; **any uncertainty about protection fails closed**. Do not depend on a separate reverse-proxy Worker for this phase.

**Known trade-off:** the ANAS Worker is still the hostname's Custom Domain origin for unmatched requests. Edge path routing isolates other registered Worker handlers, but is not a guarantee of complete origin failure-domain isolation. Before onboarding a second service, verify no route shadowing, accidental ANAS fallback, namespace collision, or Access policy leakage. If Cloudflare cannot safely satisfy these conditions, stop and submit a revised design for explicit approval rather than silently adding a Gateway.

### Single configuration owner

Terraform in `infra-config` remains authoritative for the hostname, Worker route patterns, Access applications, service-token policies, and non-secret AUD outputs. Runtime Worker repos do not redeclare those Cloudflare Route resources in Wrangler. Onboarding docs record the mapping and the approved integration-owned Service Token(s). Avoid Terraform resource replacement, orphaned route state and ambiguous ownership. Review a refreshed plan before apply.

## Per-service Cloudflare Access and origin validation

Each service gets a separately scoped Access application on its canonical path, with explicit identity and machine-client policies appropriate to that service. For ANAS, modify/reconcile the existing application rather than automatically creating a new application or rotating tokens.

- Interactive clients use Managed OAuth. Headless clients use individually approved Access Service Tokens. Do not fall back to an unprotected public endpoint when OAuth fails.
- An origin must validate `Cf-Access-Jwt-Assertion` (signature/JWKS, expected team issuer, **that service's** application audience, time constraints) **before** creating its MCP server, accessing databases, or executing tools.
- Never accept another service's Access JWT audience, a caller-supplied unsigned identity, GitHub deployment credentials or an ANAS Service Token policy by default.
- Host/Origin validation stays explicit. Do not loosen the existing ANAS host allowlist just to facilitate path routing. `workers.dev` and Worker Preview URL exposure remain disabled for ANAS.
- New services should follow the same protected-ingress principle, but their approved auth policies can be different; they must be documented by their own OpenSpec before deployment.
- Sensitive tokens, JWTs, assertions and claims are never placed in repository text, CI logs, query strings or user-visible error bodies.

## OAuth discovery on a shared hostname: mandatory live acceptance test

Path-scoped applications share the hostname but **not** their Access application identity. The protected-resource URL advertised in `WWW-Authenticate` must resolve to metadata for the requested endpoint `/anas/mcp` or `/infra/mcp` and point to the expected Cloudflare Access authorization server. Do not hard-code an inferred `.well-known` URL from a generic MCP tutorial.

The historical Cloudflare discovery URL `/.well-known/cloudflare-access-protected-resource/mcp` predates this new naming choice and SHALL NOT be carried forward by assumption. Verify actual metadata, resource ID, `WWW-Authenticate`, PKCE and authorization flow for `/anas/mcp`, including distinct second-service behavior.

Do **not** treat a Worker HTTP 200 smoke probe as proof of OAuth. Test actual OAuth code+PKCE discovery/consent/token/authorized MCP initialization, independently of Service Token authentication. Test wrong-AUD rejection and that a token authorized for `/anas` does not authorize `/infra` unless separately and explicitly granted.

If Cloudflare Access cannot advertise distinct, client-compatible discovery metadata on the shared host, this design is blocked: propose an amended ingress/auth design rather than weakening either application's protection.

## Staged implementation and cutover

1. **Specification only (this PR)**: establish ownership, URL naming, security invariants, validation matrix and non-goals. No operational changes.
2. **Application preparation (ANAS PR)**: replace handler `/anas` with `/anas/mcp`, fail closed before Access cutover, update protocol/OpenAPI specs, route tests, smoke scripts, docs and any exact-path references. Keep `workers_dev=false` / `preview_urls=false`. Do not expose both MCP paths for compatibility.
3. **Infrastructure preparation (infra-config PR)**: inspect current Custom Domain and Access app Terraform state, plan the path change, review Access application identity/AUD consequences and the implementation order. Explicitly block an unintended delete/recreate or temporary unauthenticated endpoint. No immediate need to provision routes for imaginary services.
4. **Coordinated production cutover (later approval)**: deploy/apply in a reviewed order preserving the Worker-origin JWT gate at all times; run unauthenticated denial checks, path discovery, real human OAuth, machine Service Token and wrong-path checks. Record any bounded single-user interruption. Reconfigure the operator's ChatGPT MCP URL only after success.
5. **Subsequent services**: separately implement/approve each service, register exact paths and Access app in Terraform, and run two-service conflict/discovery validation before declaring it live.

Rollbacks require operator review of the current Access AUD and Worker version. Do not automatically roll back to a pre-Access Worker version or reopen bare `/anas` or root `/mcp` as permanent compatibility endpoints.

## Verification matrix

| Scenario | Expected |
| --- | --- |
| `POST /anas/mcp` without Access credentials | Cloudflare challenge/denial; no MCP execution |
| `POST /anas/mcp` with approved Managed OAuth session | MCP initialize succeeds; tools retain existing read-only permissions |
| `POST /anas/mcp` with approved headless Service Token | Authenticated MCP initialize succeeds |
| `POST /anas/mcp` with invalid/missing origin JWT | 401/503 fail-closed as documented; no tool/database work |
| `POST /anas` and `POST /mcp` after cutover | Neither old endpoint initializes MCP or redirects |
| `/anas/mcp/`, `/anas/mcp2`, `/anas-other/mcp`, `/infra/mcp2` and unregistered slugs | No prefix collision, no dispatch to the wrong MCP server |
| `/infra/mcp` when only ANAS is installed | No working foreign MCP endpoint |
| `/infra/mcp` after separately approved registration | Only Infra Worker executes; ANAS handler is not invoked |
| Access JWT issued for another service's AUD | Origin rejects before MCP initialization |
| OAuth `WWW-Authenticate` and `/anas/mcp` protected-resource discovery | Metadata identifies the requested registered resource, not the other MCP |
| Public `GET /health` | Existing minimal status only; no analytics or client credentials |
| Direct `workers.dev` / preview host requests for ANAS | Not accessible as an alternate authentication bypass |
| Terraform plan and post-cutover validation | No unapproved Access replacement, route collision, stale policy or unexpected hostname migration |

## Reference documentation

- [Cloudflare Workers Routes](https://developers.cloudflare.com/workers/configuration/routing/routes/)
- [Cloudflare Workers Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/)
- [Cloudflare Access application paths](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/)
- [Cloudflare Access Managed OAuth](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/managed-oauth/)
- [MCP Streamable HTTP transport](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/streamable-http.mdx)
- [Current ANAS origin code](https://github.com/firstsun-dev/anas-mcp/blob/main/src/index.ts)
- [Current ANAS Terraform Access and Custom Domain](https://github.com/firstsun-dev/infra-config/blob/main/terraform/cloudflare/firstsun/anas_mcp_access.tf)

