# Handoff C — Shared CI/CD (firstsun-dev/.github)

## Summary
Additive, backward-compatible MCP post-deploy stage; `_cf-worker-v2.yml` and its inputs/outputs/check names are untouched.
`pipeline` (existing: CI, build/test, deploy, engine smoke, rollback) -> **`_mcp-post-deploy.yml`**: upstream smoke -> [per-portal lock] capability sync -> portal smoke -> dynamic portal smoke.
Production push to main only, verified, no rollback (guard enforced on every job by `check-workflows.py`); never a PR Preview. Consumer wiring: `docs/mcp-post-deploy.md`.

## Rev 2 update (service-token portal -> Worker auth)
- Four layers (L1 direct Worker, L2 portal, L3 portal->Worker runtime auth, L4 human OAuth = out of scope) are mapped to inputs in `docs/mcp-post-deploy.md`.
- New: invalid-service-token refusal check (L1/L2, default on), `portal_expect_absent_tools` (no cross-portal / no per-user-OAuth exposure, also through `portal_query_tools` in dynamic mode), sync requires `ready` AND `result.tools >= portal_sync_min_tools` (default 1; L3), dynamic names default `portal_query_tools`/`portal_execute` (live-confirmed by D; mock now uses `tool_name`).
- Sync stays the explicit API call (bearer/custom-header servers are not covered by the documented DCR background sync); per-portal `queue: max`, fail loud, production only. CI credential = org `CF_ACCESS_CLIENT_ID/SECRET` (smoke token) only; never the portal runtime token or backend tokens.
- DAG unchanged (upstream-smoke -> portal). 20 mock tests pass.
- Assumption to verify live (D): the sync answer carries `result.tools` (infra-config's sync script reads it). Wrong-audience rejection is not testable from CI (left to Worker unit tests). Tool-name prefixing through a portal still unobserved.

## Changed files
- New: `.github/workflows/_mcp-post-deploy.yml`, `actions/mcp-smoke/{action.yml,mcp-smoke.mjs}`, `actions/mcp-portal-sync/{action.yml,sync.mjs}`, `scripts/mcp/{mcp-smoke.test.mjs,mock-mcp-server.mjs}`, `docs/mcp-post-deploy.md`, `openspec/changes/add-mcp-portal-post-deploy/*`
- Edited (test/lint wiring only): `scripts/cf-preview/{test.sh,check-workflows.py,shellcheck-actions.py}`, `.github/actionlint.yaml`

## Test results
`bash scripts/cf-preview/test.sh` (full; docker-wrapped shellcheck + actionlint latest): **PASSED** — static (workflow structure, shellcheck scripts/actions, actionlint incl. the new workflow), unit (16 new mock-server tests + existing), safety (existing engine suites). New tests: pass; header injection + masking + no secret in output; wrong token; Access redirect; Bypass detected; missing tool; regex tool; failing read-only call; SSE; partial creds / bad URL / bad mode; hang -> timeout; dynamic portal (pass, missing execute, wrong names, undiscoverable); sync (ready, waiting retry, retryable retry, non-retryable no retry, stale, 403 hint, malformed, one failure still syncs the rest, invalid config makes no request).
NOT run: any real GitHub Actions run, real Cloudflare API, real portal (none exists yet).

## Inputs / secrets / vars required (by the consumer; none are values here)
- Secrets (all optional): `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` (existing org secrets; service token must be admitted by `allow-firstsun-mcp-smoke` on each app AND portal Access app); `CLOUDFLARE_API_TOKEN` with **MCP Portals Write** + `CLOUDFLARE_ACCOUNT_ID` (sync only; recommend a dedicated narrowly-scoped token mapped from a new org secret, name to be set in handoff-A).
- Inputs: see `docs/mcp-post-deploy.md` (server ids, portal URL, `portal_key`, expected tools, read calls). No repository `vars` needed.

## Dependencies
- **A (infra-config):** portal URLs/ids, per-server Cloudflare MCP server ids, `on_behalf: false` on linked servers, service-token Service Auth policy on portal + servers, the sync-capable API token, resolution of the `mcp.firstsun.org` ANAS hostname collision.
- **B (innovation-apps):** add the `mcp` job per MCP workflow + gate `needs`, retire the duplicated `access-smoke.mjs` MCP checks, keep Diet Manager OAuth-user tests a separate check; `@v2` (or a pinned commit) must contain these files first (merge order: C first).
- **D:** confirm live behaviour (below).

## Limitations / open verification
- Dynamic mode names/args confirmed live by D; portal tool-name prefixing unknown -> use `re:`. Argument keys are still mapped from `inputSchema`.
- Sync only refreshes a server that already exists; registration is infra-config's. `waiting` after the bounded retries fails (by design).
- `queue: max` is a new GitHub concurrency option (already used by the engine); `needs`-skipped upstream smoke (no `mcp_url`) does not block the sync.
- Diet Manager is OAuth-user: an upstream service-token smoke is likely impossible (initialize needs auth), so leave `mcp_url` empty for it, rely on portal-level coverage, and keep its OAuth-user tests a separate check.

## Rollback
Revert the PR (new files only). Consumers that have not added the `mcp` job are unaffected; a consumer that has can remove the job; nothing in Cloudflare is created or changed by this repo (a sync only refreshes a server's tool/prompt list).
