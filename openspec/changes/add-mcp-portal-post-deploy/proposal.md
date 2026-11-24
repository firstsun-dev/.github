# Proposal: MCP post-deploy verification and portal capability sync

## Why
Firstsun is consolidating its MCP servers behind two Cloudflare MCP portals. Each MCP app already ships through the v2 engine (`_cf-worker-v2.yml`), but (1) every app re-implements its own Access-authenticated MCP smoke (`innovation-apps/scripts/ci/access-smoke.mjs`), (2) nothing keeps a portal's registered server capabilities in step with a deploy, and (3) nothing smokes the portal itself (static and `optimize_context=search_and_execute`).

## What
Additive, optional building blocks; the engine and its inputs are untouched:
- `actions/mcp-smoke` — ONE Access-authenticated MCP smoke for an upstream endpoint, a portal, or a dynamic portal (centralized, masked service-token header injection).
- `actions/mcp-portal-sync` — `POST /accounts/{id}/access/ai-controls/mcp/servers/{server}/sync`, fail-loud.
- `.github/workflows/_mcp-post-deploy.yml` — reusable stage a consumer calls after its `pipeline` job: upstream smoke -> (per-portal serialized) capability sync -> portal smoke -> dynamic portal smoke, PRODUCTION push-to-main only.

## Compatibility
New files only plus test/lint wiring. No existing input, output, job or check name changes. A consumer that does not call `_mcp-post-deploy.yml` is unaffected. All inputs are optional with safe defaults (an empty stage is skipped).

## Ownership
`.github` owns the workflow/actions. Cloudflare resources (portals, servers, policies, token) belong to infra-config; app code and caller wiring belong to innovation-apps. See `docs/mcp-post-deploy.md` for the exact consumer change.
