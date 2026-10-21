# Tasks: firstsun MCP hosting convention

## Phase 0 — organization OpenSpec (this documentation-only PR)

- [x] Verify `firstsun-dev/.github` explicitly supports coordinated cross-repository changes.
- [x] Inspect existing ANAS Worker handler, hostname Custom Domain, Access app and the `/mcp` dependency.
- [x] Record the owner decision: single `mcp.firstsun.org` hostname; `/{service}` canonical URLs; first migration to `/anas`; no `/mcp` compatibility.
- [x] Separate Cloudflare ingress/Access ownership (`infra-config`) from protocol ownership (`anas-mcp`) and shared CI/CD (`.github`).
- [x] Define security gates, path-collision tests, OAuth discovery requirements, cutover order, and future-service onboarding.
- [ ] Review/approve this specification before implementation. No deploy, credentials, or Terraform changes in this phase.

## Phase 1 — ANAS application preparation (separate `anas-mcp` PR)

- [ ] Add an ANAS-specific OpenSpec implementation change referencing this cross-repository contract; reconcile `openspec/project.md` and the active dual-auth decision without rewriting old history.
- [ ] Change `createMcpHandler` route from `/mcp` to **only** `/anas`; ensure old/unknown paths cannot execute MCP tools or expose a working alias.
- [ ] Preserve origin-side JWT issuer/JWKS/AUD checks, Host/Origin handling, read-only tool constraints and production-only ingress.
- [ ] Update `openapi.yaml`, `docs/mcp-authentication.md`, `docs/cloudflare-access.md`, `docs/api.md`, `docs/cicd.md`, README, smoke scripts, tests and client examples with exact canonical URLs/metadata expectations.
- [ ] Preserve the existing minimal public `GET /health` behavior intentionally; do not accidentally make it a general-purpose service listing.
- [ ] Add path collision, unauthenticated/incorrect-AUD, OAuth discovery, and Service Token tests; CI stays production-only for non-main branches.

## Phase 2 — Cloudflare Terraform preparation (separate `infra-config` PR)

- [ ] Record an infra-specific OpenSpec implementation change referencing this convention and review current `anas_mcp_access.tf` state.
- [ ] Plan modification of the existing Access application's domain to `mcp.firstsun.org/anas`; verify whether the provider updates in place, whether AUD changes, and how client grants/service tokens are affected.
- [ ] Retain `mcp.firstsun.org` Custom Domain origin unless a reviewed edge constraint requires revisiting the design; do not create speculative `/infra` or `/knowledge` routes.
- [ ] Document reusable Terraform registration for future named service route + separate Access app/policies/AUD; maintain a single owner for route resources.
- [ ] Validate exact path matching, safe unknown-path behavior, OAuth metadata interception and DNS/route precedence. Review `terraform plan` for unintended replacement, overlap or public exposure.

## Phase 3 — controlled ANAS production cutover (explicit later approval)

- [ ] Agree the exact ordered Worker/Access deployment procedure and a bounded manual recovery plan. Keep ingress fail-closed at every intermediate state.
- [ ] Activate `/anas` and remove usable `/mcp`. No alias, redirect or dual-served old MCP endpoint.
- [ ] Verify real ChatGPT Managed OAuth flow, headless Service Token flow, protected-resource metadata, invalid-AUD rejection, hostname restriction, absent preview/public host bypass, and minimal health.
- [ ] Update the owner's ChatGPT connection URL to `https://mcp.firstsun.org/anas` only after verification.
- [ ] Capture results in the app/infra implementation PRs and close migration issues only when deployment acceptance passes.

## Phase 4 — future MCP onboarding (not part of this change)

- [ ] Add a new service only after its dedicated Worker, owner, route pattern and Access application are defined in `infra-config` and its own repository.
- [ ] Verify the existing ANAS Custom Domain origin and the new service's exact Workers Route do not shadow each other; reject prefix collisions and unknown paths.
- [ ] Test two Access apps on the shared hostname for independent audience validation, discovery and policy enforcement.
- [ ] If that routing or OAuth model cannot meet the gates, stop and obtain a new approved OpenSpec instead of silently introducing a Gateway.

## Commit / PR boundary

This planning change SHALL be delivered as **one focused commit in one `.github` PR**. All later ANAS and infrastructure implementation changes are separate PRs. The documentation PR must not run Terraform apply or alter production.

