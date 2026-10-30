# Design: Grafana MCP behind the existing NAS Tunnel

## Current and target topology

The shared `mcp.firstsun.org` Custom Domain currently selects `anas-mcp` for unmatched paths. A more-specific exact Worker Route may select another Worker. The NAS-hosted Grafana already listens behind the `firstsun-nas` Tunnel and already has a Prometheus datasource. None of those existing origins must be moved.

~~~text
ChatGPT / Claude Code / Codex
  |
  | HTTPS https://mcp.firstsun.org/grafana/mcp
  v
Cloudflare Access: grafana-mcp (Managed OAuth or approved Service Token)
  | signed Cf-Access-Jwt-Assertion
  v
Workers Route: mcp.firstsun.org/grafana/mcp (exact)
  v
grafana-mcp-proxy Worker
  | verify Grafana Access issuer/JWKS/AUD/exp/nbf; reject unwanted paths
  | discard client auth, use distinct private upstream bearer secret
  | Streamable HTTP via bound HTTP VPC Service
  v
existing firstsun-nas Cloudflare Tunnel (private Workers VPC traffic)
  v
NAS-local listener, illustrative 127.0.0.1:18080
  v
official grafana/mcp-grafana container (:8000)
  | GRAFANA_URL + read-only Grafana service account
  v
existing Grafana API (:3000)
  v
existing Prometheus datasource / dashboards / alerts

Unaffected: mcp.firstsun.org/anas/mcp -> existing anas-mcp Custom Domain origin
~~~

The NAS-local host port is illustrative and must be confirmed free, accessible from the existing `cloudflared` process, and not published on any LAN/public interface. Avoid coupling an unrelated `nas_monitor` Compose restart to the new MCP service; a standalone `nas_grafana_mcp` role/project is preferred.

## Ownership and source of truth

- `.github`: organization ingress and onboarding invariants only.
- `infra-config`: Terraform owns the existing Custom Domain, new exact Worker Route and path-scoped Access application, explicit Service Token, non-secret AUD outputs, scoped HTTP Workers VPC Service and NAS Ansible/Compose.
- `grafana-mcp`: independent Worker, request validation, protocol-aware proxy, VPC binding declaration, CI/test/doc and Grafana-specific application OpenSpec.
- The Grafana project remains the official upstream container, pinned to a vetted release/digest. Do not mirror upstream tools or ship Grafana credentials inside the Worker.
- Consumer Wrangler configuration must **not** create a competing Route or Custom Domain; production `workers_dev=false` and `preview_urls=false` (or equivalent audited disablement) are mandatory.

## Public ingress and Cloudflare Access

The only public MCP endpoint is exactly `https://mcp.firstsun.org/grafana/mcp`. An exact, non-wildcard `cloudflare_workers_route` on that path selects `grafana-mcp-proxy` ahead of the existing ANAS Custom Domain. The existing `cloudflare_workers_custom_domain.mcp_firstsun_org`, DNS and ANAS Access application remain unchanged.

Create a **new** path-scoped `grafana-mcp` self-hosted Access application:
- Managed OAuth enabled with compatible dynamic client registration, authorization code and PKCE for approved interactive clients.
- Explicit approved identity allowlist and separate, approved headless Service Token policy; never `any_valid_service_token`, blanket bypass or reuse an ANAS Service Token.
- New application AUD; do not reuse ANAS AUD or copy a production assertion between services.
- The Worker validates `Cf-Access-Jwt-Assertion` signature using team JWKS, issuer, its Grafana AUD, expiration, not-before and approved algorithm before any upstream connection. Return fail-closed 401 for invalid caller assertion or 503 for unavailable verification dependencies; never trust unverified header contents.
- Preserve Cloudflare-owned `WWW-Authenticate` and RFC 9728/8414 protected-resource discovery for the Grafana path. Verify real OAuth for Grafana and ANAS from external clients.
- Reject incorrect Host, non-MCP Origin, prefix collisions, malformed/encoded paths and all other URL paths before tool execution. No open proxy or fetch-to-ANAS fallback.

## Proxy and protocol behavior

The Worker is an **authentication-terminating, fixed-destination MCP transport adapter**, not a generic HTTP forward proxy.

- Restrict upstream destination to one configured VPC Service and one fixed path; the external `/grafana/mcp` path stays `/grafana/mcp` internally. Prefer official `mcp-grafana` `--base-path /grafana` plus default `--endpoint-path /mcp` instead of an unreviewed path rewrite.
- Support the Streamable HTTP operations actually exposed by the chosen upstream version (initialize/tool POST, optional response-stream GET and session DELETE). Preserve `Accept`, `Content-Type`, `MCP-Protocol-Version`, `Mcp-Session-Id`, `Last-Event-ID` and necessary response/session headers with correct case-insensitive handling.
- Do not buffer an event stream into a JSON response or alter JSON-RPC envelopes. Preserve meaningful upstream status codes and content types, handle cancellations, body size and timeouts, prohibit caching, and maintain streaming where supported. Validate behavior end to end; fail rollout if Workers VPC cannot support it.
- Reconstruct a safe upstream request rather than forwarding all client headers. Remove `Authorization`, `Cookie`, `Cf-Access-*`, `CF-Access-*`, `X-Grafana-*` overrides, forwarding/proxy headers and unrelated auth/session data. Inject only the **origin-only** `Authorization: Bearer <server-token>` from a Worker secret when calling the official upstream.
- Grafana upstream `--allowed-hosts` must allow only the verified internal Host values; `--allowed-origins` remains restrictive. Never configure wildcard `*` or permit browser-Origin bypass merely to make proxies work.
- Do not expose a method/path which would allow clients to choose Grafana URL, account credentials, organization, upstream address, or another datasource through crafted headers.

## NAS and Grafana MCP

Provision a standalone NAS container of the official `grafana/mcp-grafana` image:
- Pin a tested release or immutable digest; do not use an unpinned `latest` tag in production.
- Run `-t streamable-http`, listen on container port 8000, use `--base-path /grafana`, `--disable-write`, and configure an explicit minimal tool-category allowlist (Prometheus query, dashboard search/read, datasource discovery and alert read).
- Set `GRAFANA_URL` to the existing private Grafana API (NAS host or explicitly joined private Docker network), not the public `grafana.firstsun.org` URL. Verify the exact reachable Docker networking rather than assuming `localhost` inside a container refers to the NAS host.
- Authenticate **upstream callers** with `MCP_GRAFANA_SERVER_TOKEN`. The proxy holds the matching secret separately; neither OAuth credentials nor Access JWTs are accepted by the upstream.
- Authenticate the MCP **to Grafana** with `GRAFANA_SERVICE_ACCOUNT_TOKEN` (or the supported token-file variant). Create a dedicated least-privilege read-only service account and restrict datasource query permissions where the installed Grafana edition supports it.
- Bind the NAS host-facing port to loopback only (illustrative `127.0.0.1:18080:8000`), and confirm the Cloudflare Tunnel process can reach it. `/healthz` and `/metrics` remain internal only. Set modest CPU/memory and restart policies; failed MCP workloads must not restart/reconfigure the monitoring stack.
- Store token values in the authorized secret store; NAS/Ansible/Worker logs, repositories, CI output and Terraform state outputs must not contain the token values.

## Existing Tunnel and Workers VPC

Use **one dedicated HTTP VPC Service** whose `host.network.tunnel_id` is the **existing** `cloudflare_zero_trust_tunnel_cloudflared.firstsun_nas`. Pin the origin to the explicitly verified NAS-local interface and port (do not expose a broad VPC Network). Call its `fetch()` binding from the Worker.

This private Worker → VPC Service → Tunnel path is independent of the Tunnel's existing public hostname ingress entries. Do not add a public `mcp.firstsun.org` or `grafana-mcp.firstsun.org` tunnel hostname, modify DNS, change existing ingress or create a second Tunnel.

Preflight must compare live Tunnel configuration (`remote_config`) with Terraform (`config_src`), verify origin reachability, QUIC transport and firewall egress requirements. Because this might be a real drift, **do not apply** any unexpected Tunnel lifecycle or configuration change as part of Grafana onboarding.

## Runtime limits and observability

- Export non-sensitive up/down, request outcomes, latency, VPC errors and origin connectivity metrics, if supported by the chosen deployment. Do not log tokens, JWTs, prompts, PromQL payloads, private metrics or Authorization headers.
- Choose bounded body size, concurrency, query timeout and default query time range where implementation permits; report limits transparently. A read-only query may still exhaust Prometheus/Grafana resources.
- A separate internal health check should prove MCP process readiness; actual authorized `initialize` and one read-only query are required to prove Grafana API/data-source functionality.
- Use minimal secrets and origin privileges, reject unknown Host/Origin, and keep externally reachable endpoints restricted to the canonical MCP path (plus Cloudflare-owned OAuth discovery).

## Rollout, verification, and rollback

1. Approve this documentation-only contract; do not provision infrastructure yet.
2. Verify current ANAS OAuth/Service Token endpoint, Custom Domain/Route behavior, Tunnel config drift, protocol feasibility and the current Grafana API. Pin versions and choose an unused NAS-local port.
3. Create the new `grafana-mcp` repository with its own application OpenSpec, tests and Worker. Implement JWT and transport tests before enabling public routing.
4. Add NAS official container through Ansible, create origin caller and Grafana read-only service tokens, validate local end-to-end behavior. Do not expose a public origin.
5. Add Cloudflare Terraform resources in `infra-config`: HTTP VPC Service, distinct Access app + policies + Service Token, exact Worker Route. Review **full refreshed plan** and apply only after approval with staged cutover.
6. Deploy the Proxy Worker with its VPC Service binding but without a routable alternate public endpoint. Enable the exact public route only when the authenticated local and private backend gates succeed.
7. Test live OAuth PKCE/discovery, Service Token, Streamable HTTP `initialize`, `tools/list`, Prometheus query, dashboard read, alert read, and negative authorization/path cases for both Grafana and ANAS.
8. On failure, remove/disable **Grafana-only** Route and Access registration, restore the prior Grafana Proxy Worker version if needed, and roll back its container only; leave ANAS, Custom Domain, DNS and Tunnel untouched.

## Negative test matrix

| Input/failure | Required outcome |
| --- | --- |
| Anonymous `/grafana/mcp` | Access 401 challenge (not MCP tools) with correct resource metadata |
| Valid Grafana OAuth, wrong Grafana origin token | Fail closed; no tool calls |
| Valid ANAS Access JWT at Grafana | Reject before VPC fetch |
| Valid Grafana JWT at ANAS | Reject with ANAS's existing audience gate |
| `/grafana`, `/grafana/mcp/`, `/grafana/mcp2`, `/grafana-other/mcp`, encoded or doubled path | No Grafana MCP initialization and no ANAS fallback tools |
| Unregistered or alternate `workers.dev` / Preview route | No unauthorized API access |
| Spoofed `Cf-Access-Jwt-Assertion`, client `Authorization`, `X-Grafana-URL` | Reject or strip; never bypass Access or control private origin |
| Grafana MCP write attempt | Not registered/allowed; no state mutation |
| Bad VPC connectivity / Tunnel outage / Grafana outage | Bounded 5xx; no unprotected fallback, ANAS unaffected |
| Event-stream/long poll and session headers | Intact protocol semantics across all hops |
| Full Terraform plan includes any unrelated replace/destroy/change | Stop; seek explicit operator decision |

## External references

- https://github.com/grafana/mcp-grafana/blob/main/docs/sources/configure/transports-and-addresses.md
- https://github.com/grafana/mcp-grafana/blob/main/docs/sources/configure/command-line-flags.md
- https://github.com/grafana/mcp-grafana/blob/main/docs/sources/configure/authentication.md
- https://developers.cloudflare.com/workers/configuration/routing/routes/
- https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/managed-oauth/
- https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/
- https://developers.cloudflare.com/workers-vpc/reference/troubleshooting/
