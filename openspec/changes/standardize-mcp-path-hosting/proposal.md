# Proposal: Firstsun MCP hosting on shared path-based endpoints

## Why

Today `firstsun-dev/anas-mcp` is served at `https://mcp.firstsun.org/mcp`. The hostname is a Cloudflare Worker Custom Domain assigned to `anas-mcp`, and a path-scoped Cloudflare Access application protects `/mcp`. As more independently maintained remote MCP services appear, `/mcp` is not a useful service identity. It also leaves the organization without an explicit contract for path ownership, authentication, and routing.

**Approved owner decision (2026-10-21): use one hostname, `mcp.firstsun.org`, and one top-level path per MCP service. The move from `/mcp` to `/anas` is deliberately breaking; there is no old-path compatibility requirement.** Only the owner currently uses the ANAS connection and will reconfigure the client after cutover.

The MCP Streamable HTTP specification requires an HTTP endpoint, but does not require the literal `/mcp` path.

## Proposed contract

| Service identity | Canonical remote MCP URL | Rollout |
| --- | --- | --- |
| `anas` | `https://mcp.firstsun.org/anas` | Migrate existing `anas-mcp` |
| `infra` | `https://mcp.firstsun.org/infra` | Illustrative reservation; no service is deployed by this change |
| `knowledge` | `https://mcp.firstsun.org/knowledge` | Illustrative reservation; no service is deployed by this change |

- Every service owns a **distinct Worker and MCP endpoint** under `/{service}`.
- `firstsun-dev/infra-config` centrally owns Cloudflare hostname, route mapping, and per-service Access application/policies through Terraform; service repositories own protocol handlers, app OpenAPI, smoke tests, and operational documentation.
- Reuse native Cloudflare Workers routing for distinct paths when proven safe; **do not introduce a new application-level reverse proxy or MCP Gateway** for this change.
- Preserve the current `anas-mcp` authorization model: Cloudflare Access Managed OAuth for user-driven MCP clients, Service Tokens for approved headless clients, and origin-side Access JWT issuer/audience validation. New MCP services must be separately authorized, not implicitly trusted because of a common hostname.
- `/mcp` is removed as a usable ANAS MCP endpoint. No alias, redirect, dual-serving period, or permanent compatibility route is required.

## Why the organization `.github` repository is appropriate

`firstsun-dev/.github` is the source of **cross-repository engineering contracts**, while `infra-config` owns Terraform and `anas-mcp` owns runtime semantics. The path convention and onboarding criteria govern multiple future repositories, so this OpenSpec change belongs here. It does **not** transfer cloud infrastructure ownership or application-specific API policy into `.github`.

The new `mcp-service-hosting` capability is intentionally separate from `cloudflare-worker-delivery`. The existing centralized CI/CD workflow is not being redesigned.

## Affected repositories and ownership

| Repository | Responsibility in later implementation |
| --- | --- |
| `firstsun-dev/.github` | Organization naming, ingress/auth contract, migration gate, cross-repo checklist (this OpenSpec PR only) |
| `firstsun-dev/infra-config` | Terraform for existing shared host and ANAS Access path; future per-service Worker Routes, Access applications/AUD/policies; plan/apply and route validation |
| `firstsun-dev/anas-mcp` | Change MCP handler route to `/anas`; retire `/mcp`; keep origin auth fail-closed; update OpenAPI, docs, tests and smoke; update ChatGPT connection after verification |
| Future service repository | Own its handler, tests, Worker config, registration request, and consumer-facing docs |

## Compatibility, security and operational boundaries

- **Breaking endpoint change**: existing clients pointing at `/mcp` must be manually updated to `/anas` after production validation. No compatibility layer will be developed.
- **No implicit authentication migration**: keep approved human identities, service-token policy semantics, and Access JWT verification. If modifying the Access application changes its AUD or OAuth grants, treat that as a coordinated reauthorization, not an unreviewed replacement.
- **No public bypass**: do not enable `workers.dev`/Preview URLs, create a general public fallback, or rely only on Host/path checks in place of Access JWT validation.
- **No shared namespace wildcard**: `/anas-other` must not be treated as `/anas`. Route matching, path ownership and unregistered-path rejection require explicit tests.
- A DNS/Custom Domain/Routes transition must use a reviewed Terraform plan and a bounded cutover; no assumption that an existing Custom Domain can be silently shared by several origin Workers.
- Production-only CI/CD classification for `anas-mcp` remains unchanged.

## Success criteria and gates

1. Approved stable URL for ANAS is exactly `https://mcp.firstsun.org/anas`, with no live `/mcp` MCP alias.
2. The ANAS Worker remains protected by the same two *types* of clients; missing/incorrect Access assertions fail closed before MCP tools or data bindings run.
3. ChatGPT OAuth login completes for `/anas`, including path-correct 401 challenge and protected-resource discovery; Service Token flow is independently verified.
4. Edge routing and Access tests prove no accidental cross-service dispatch, credential sharing, prefix-collision match, or unprotected alternate hostname.
5. Existing production `/health` behavior is explicitly accounted for and does not expose analytics or other protected methods.
6. Future `/infra` and `/knowledge` may be added without changing the ANAS Worker implementation or inheriting ANAS's Access audience.
7. Cross-repo PRs and production rollout happen **only after** this proposal is accepted. This specification change creates no runtime resources and makes no deployment.

## Explicit non-goals

- No actual Infra/Knowledge MCP implementation.
- No MCP Gateway, dynamic tool federation, unified multi-MCP login, or automatic service discovery catalog.
- No edits to shared Cloudflare Worker CI/CD v2/vNext contracts, and no ANAS Preview enablement.
- No legacy `/mcp` alias, redirect, migration shim, or back-compat test suite.
- No Terraform apply, Cloudflare dashboard mutation, secret rotation, DNS change, or ChatGPT client change in this documentation-only PR.

