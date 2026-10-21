# Design: path-based Firstsun MCP hosting

## Baseline (verified on 2026-10-21)

- `infra-config/terraform/cloudflare/firstsun/anas_mcp_access.tf` provisions `cloudflare_workers_custom_domain` for `mcp.firstsun.org` pointing directly at Worker `anas-mcp`. Its Access application currently protects `mcp.firstsun.org/mcp` and supports both Managed OAuth and Service Token.
- `anas-mcp/src/index.ts` fixes `MCP_PUBLIC_HOSTNAME` to `mcp.firstsun.org` and sets `createMcpHandler(..., { route: "/mcp" })`. The Worker verifies `Cf-Access-Jwt-Assertion` before invoking the MCP handler; `GET /health` is the existing minimal public exception.
- `anas-mcp/wrangler.jsonc` disables `workers_dev` and `preview_urls`; its `ACCESS_TEAM_DOMAIN` and `ACCESS_AUD` correspond to the path-scoped Access application.
- Cloudflare Custom Domains are **hostname-wide** (not path-wide). Workers Routes can take precedence over a Custom Domain on the same hostname, provided route matching and underlying proxied DNS are valid. They must be verified as actual edge behavior, not assumed from hostname semantics.

## Canonical public contract

`https://mcp.firstsun.org/{service}` is a *complete client-configured MCP endpoint*, not an HTML landing page or a mandatory URL prefix for `/{service}/mcp`.

A service slug must be registered once, be lowercase ASCII, and match `^[a-z][a-z0-9-]{1,31}$`. Reserved names include `mcp`, `health`, `cdn-cgi` and `.well-known`. Slugs are organization-level service identities, not necessarily the Worker or repo name. A registration maps one service slug to one owning repository, Worker, protected endpoint and Access application.

The MCP handler accepts protocol-supported request methods at its **exact** registered path (Streamable HTTP POST; further methods only where supported by the pinned protocol/SDK version). No additional `/mcp` suffix is imposed. A trailing slash, suffix path or URL normalization must be explicitly tested; clients should use the exact canonical URL.

## Cloudflare ingress and routing

### Initial ANAS cutover

Retain the current `mcp.firstsun.org` Custom Domain origin attached to `anas-mcp` unless a Terraform plan or edge verification identifies a blocking condition. Since the Custom Domain matches the hostname, the same Worker can serve `/anas` directly after its handler change; **it does not require a second DNS name or a Gateway**.

Update the existing ANAS Access application to protect `mcp.firstsun.org/anas`, coordinated with the Worker route change. Existing `/mcp` SHALL stop exposing a working MCP handler; it need not redirect or authenticate legacy clients.

The current public `GET /health` is an explicit minimal operational exception. It is not the canonical MCP endpoint, not an organization-wide health aggregator and must not reveal service data. Do not change it only to make the endpoint migration appear cleaner; handle any future per-service health design in its own scope.

### Additional services

When `/infra` or another service is actually implemented, `infra-config` shall add a dedicated Cloudflare Workers Route to its Worker on `mcp.firstsun.org` and a separately scoped Access application. The more-specific path Worker Route runs in preference to the hostname Custom Domain's ANAS origin. Do not move ANAS code into that Worker, proxy requests through ANAS, or copy generic Cloudflare route Terraform into each consumer repo.

Use an **exact route** for the canonical endpoint, for example `mcp.firstsun.org/infra`. If a service requires subpaths, register the separately bounded `mcp.firstsun.org/infra/*` pattern and assert that `/infra-other` is excluded. Never approximate a service prefix with `mcp.firstsun.org/infra*`: Cloudflare documents that `/path*` can match `/path2`.

A Worker serving one registered service must reject unrelated paths, even if requests arrive at that Worker through the hostname's Custom Domain fallback. Unknown routes, old `/mcp`, and typo paths shall not invoke MCP tools. Distinguish a harmless 404/410 from authenticated/unauthenticated behavior where appropriate; **any uncertainty about protection fails closed**. Do not depend on a separate reverse-proxy Worker for this phase.

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

Path-scoped applications share the hostname but **not** their Access application identity. The protected-resource URL advertised in `WWW-Authenticate` must resolve to metadata for the requested service `/anas` or `/infra` and point to the expected Cloudflare Access authorization server. Do not hard-code an inferred `.well-known` URL from a generic MCP tutorial.

ANAS currently uses the Cloudflare-specific protected-resource metadata URL `/.well-known/cloudflare-access-protected-resource/mcp` and the authorization server `https://firstsun.cloudflareaccess.com`. The new location must be derived and verified for `/anas`, including cross-service behavior after a second Access app is added.

Do **not** treat a Worker HTTP 200 smoke probe as proof of OAuth. Test actual OAuth code+PKCE discovery/consent/token/authorized MCP initialization, independently of Service Token authentication. Test wrong-AUD rejection and that a token authorized for `/anas` does not authorize `/infra` unless separately and explicitly granted.

If Cloudflare Access cannot advertise distinct, client-compatible discovery metadata on the shared host, this design is blocked: propose an amended ingress/auth design rather than weakening either application's protection.

## Staged implementation and cutover

1. **Specification only (this PR)**: establish ownership, URL naming, security invariants, validation matrix and non-goals. No operational changes.
2. **Application preparation (ANAS PR)**: replace handler `/mcp` with `/anas`, fail closed before Access cutover, update protocol/OpenAPI specs, route tests, smoke scripts, docs and any exact-path references. Keep `workers_dev=false` / `preview_urls=false`. Do not expose both MCP paths for compatibility.
3. **Infrastructure preparation (infra-config PR)**: inspect current Custom Domain and Access app Terraform state, plan the path change, review Access application identity/AUD consequences and the implementation order. Explicitly block an unintended delete/recreate or temporary unauthenticated endpoint. No immediate need to provision routes for imaginary services.
4. **Coordinated production cutover (later approval)**: deploy/apply in a reviewed order preserving the Worker-origin JWT gate at all times; run unauthenticated denial checks, path discovery, real human OAuth, machine Service Token and wrong-path checks. Record any bounded single-user interruption. Reconfigure the operator's ChatGPT MCP URL only after success.
5. **Subsequent services**: separately implement/approve each service, register exact paths and Access app in Terraform, and run two-service conflict/discovery validation before declaring it live.

Rollbacks require operator review of the current Access AUD and Worker version. Do not automatically roll back to a pre-Access Worker version or reopen `/mcp` as a permanent compatibility endpoint.

## Verification matrix

| Scenario | Expected |
| --- | --- |
| `POST /anas` without Access credentials | Cloudflare challenge/denial; no MCP execution |
| `POST /anas` with approved Managed OAuth session | MCP initialize succeeds; tools retain existing read-only permissions |
| `POST /anas` with approved headless Service Token | Authenticated MCP initialize succeeds |
| `POST /anas` with invalid/missing origin JWT | 401/503 fail-closed as documented; no tool/database work |
| `POST /mcp` after cutover | No usable ANAS MCP endpoint, alias or redirect |
| `/anas2`, `/infra2` and unregistered slugs | No prefix collision, no dispatch to the wrong MCP server |
| `/infra` when only ANAS is installed | No working foreign MCP endpoint |
| `/infra` after separately approved registration | Only Infra Worker executes; ANAS handler is not invoked |
| Access JWT issued for another service's AUD | Origin rejects before MCP initialization |
| OAuth `WWW-Authenticate` and protected-resource discovery | Metadata identifies the requested registered resource, not the other MCP |
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

