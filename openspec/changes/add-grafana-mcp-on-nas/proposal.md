# Proposal: Onboard Grafana MCP over the existing NAS Cloudflare Tunnel

## Status and scope

**Proposed, documentation only.** This OpenSpec coordinates the first NAS-hosted MCP service on the existing organization MCP hostname. Merging the spec neither deploys infrastructure nor authorizes production `terraform apply`.

This change extends the proposed `mcp-service-hosting` organization contract in `openspec/changes/standardize-mcp-path-hosting/`. It does not rewrite that change or its ANAS cutover. Application behavior and Terraform are implemented in their owning repositories.

## Why

The organization already serves ANAS MCP through the `mcp.firstsun.org` Worker Custom Domain with path-specific Cloudflare Access Managed OAuth and headless Service Token support. The `firstsun-nas` Tunnel already serves the NAS-hosted Grafana and is available to carry private traffic. The operator wants read-only Grafana/Prometheus investigation from ChatGPT, Claude Code and Codex without deploying another Tunnel, publishing a second hostname, changing ANAS, or reimplementing Grafana MCP.

## Decision

- Register service slug `grafana` and canonical endpoint **`https://mcp.firstsun.org/grafana/mcp`**. Bare `/grafana`, trailing-slash and lookalike paths are not endpoints.
- Retain the current `mcp.firstsun.org` Custom Domain backed by `anas-mcp`. Add only an **exact** Workers Route to a dedicated `grafana-mcp-proxy` Worker. The Route takes precedence; unmatched traffic continues to the existing origin and must never execute Grafana tools.
- Reuse the **existing `firstsun-nas` Tunnel** through one narrowly scoped HTTP **Workers VPC Service** bound to a single NAS-local origin and port. Do not create a second Tunnel, public Tunnel ingress hostname, DNS record, or broad VPC Network binding.
- Run the official upstream `grafana/mcp-grafana` image on NAS as a separate, pinned, read-only Streamable HTTP service. Do not fork or recreate the Grafana MCP tools. Use the existing Grafana API and configured Prometheus datasource.
- Protect `/grafana/mcp` with its own Cloudflare Access application, Managed OAuth (interactive clients), and explicit Service Token authorization (approved headless clients). Give the service its **own Access AUD**. The proxy validates the Access JWT before forwarding and authenticates separately to the NAS origin.
- Preserve application-specific GitHub ownership: one MCP service identity, one repository, one Worker, one Access application, one AUD. No general MCP Gateway, OAuth server, or multi-service proxy.

## Repositories and boundaries

| Owner | Future implementation | Excluded from this spec commit |
| --- | --- | --- |
| `firstsun-dev/.github` | Cross-repository registration, security, rollout and acceptance contract | Runtime code or Cloudflare apply |
| `firstsun-dev/grafana-mcp` (new repository) | Proxy Worker, JWT verification, VPC binding, CI tests, protocol-preserving forwarding, operation guide | Grafana MCP tool implementation |
| `firstsun-dev/infra-config` | Exact Worker Route, independent Access application/policies/token, HTTP VPC Service, NAS Ansible/Compose, secret injection, plan/cutover runbook | Replacing the shared hostname or NAS Tunnel |
| `firstsun-dev/anas-mcp` | No change | Route, auth, code and AUD migration |
| `firstsun-dev/.github` shared CI/CD | Consume the existing supported reusable Worker workflow from the new repository | Shared workflow redesign |

The organization-hosted `grafana-mcp` application OpenSpec MAY live in its new repository once created; this change governs the cross-repository contract and shall not pretend to be the application's detailed implementation spec.

## Compatibility and risk

- Existing `/anas/mcp` OAuth, machine clients, Custom Domain, public minimal health behavior, DNS and routing must keep working.
- The Grafana service is **read-only by default and at every layer**: `--disable-write`, a least-privilege Grafana service account, selected tool families, and no Grafana API credentials exposed to clients.
- The Cloudflare Access assertion is verified for the Grafana **issuer, signature, AUD, expiry and not-before** before a single upstream call. No trust based on a path, identity header, hostname or TLS alone.
- Client credentials and Access assertions must not be forwarded to NAS. A separate origin caller token is provided only by the proxy; the Grafana service account token remains only on the NAS.
- The proxy must preserve MCP Streamable HTTP semantics and streaming behavior. Query-cost / timeout guardrails and safe observability must prevent an AI client from significantly degrading the monitoring stack.
- The actual Tunnel is healthy per the initial inventory, but Terraform `config_src` and API `remote_config` observations disagree; preflight must reconcile this drift **without changing the Tunnel**.
- Workers VPC is beta. Confirm QUIC connectivity and HTTP streaming end to end before production rollout; if unsupported, stop for design review rather than adding a public bypass.

## Acceptance gates

1. Only `https://mcp.firstsun.org/grafana/mcp` initializes Grafana MCP, via a verified exact Worker Route and a distinct Access application.
2. Real ChatGPT-compatible Managed OAuth discovery, PKCE and MCP `initialize` / `tools/list` succeed; an approved Service Token works independently.
3. ANAS and Grafana audiences are mutually rejected. Missing/bad JWT, unsupported paths, alternate Worker URLs and unauthorized callers execute no tools.
4. Prometheus query, dashboard search and alert listing succeed through the Grafana datasource using a read-only service account; writes are unavailable.
5. Authenticated streaming POST/GET/DELETE, protocol headers, response status and event-stream handling work through Worker → VPC Service → existing NAS Tunnel → official container.
6. Terraform's **refreshed full plan** has only approved additions and no unrelated changes, destroys, replacements, route/DNS/tunnel mutations, or Access policy regressions. Resource-specific plans may be used only after inspecting the complete plan and state.
7. `/anas/mcp` still passes pre- and post-cutover OAuth and Service Token regression tests. Rollback removes only the Grafana registration and workload.
8. No implementation/deployment is implied by committing this OpenSpec. Implementation, credentials and production changes require their own reviewed changes and explicit operator approval.

## Non-goals

- No standalone Prometheus MCP; Grafana uses its existing datasource.
- No Grafana write tools, alert rule mutation, automatic remediation, or generic Grafana administration.
- No new Cloudflare account, new NAS Tunnel, new domain, public origin, Cloudflare-managed Grafana replacement, R2/D1 database, or Windmill job.
- No changes to `anas-mcp`, the shared Worker CI/CD engine or other MCP service audiences.
- No secret values, customer data, private network credentials, direct production apply, DNS repoint or client reconnect in this documentation-only commit.

## Baseline references

- `firstsun-dev/.github/openspec/changes/standardize-mcp-path-hosting/`
- `firstsun-dev/infra-config/docs/mcp-service-registration.md`
- `firstsun-dev/infra-config/terraform/cloudflare/firstsun/anas_mcp_access.tf`
- `firstsun-dev/infra-config/terraform/cloudflare/firstsun/tunnels.tf`
- `firstsun-dev/infra-config/ansible/roles/nas_monitor/templates/docker-compose.yml.j2`
- https://github.com/grafana/mcp-grafana
- https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/
- https://developers.cloudflare.com/workers/configuration/routing/routes/
