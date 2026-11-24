# Handoff C — Shared CI/CD (firstsun-dev/.github)

## Summary
Additive, backward-compatible MCP post-deploy stage; `_cf-worker-v2.yml` and its inputs/outputs/check names are untouched.
`pipeline` (existing: CI, build/test, deploy, engine smoke, rollback) -> **`_mcp-post-deploy.yml`**: upstream smoke -> [per-portal lock] capability sync -> portal smoke -> dynamic portal smoke.
Production push to main only, verified, no rollback (guard enforced on every job by `check-workflows.py`); never a PR Preview. Consumer wiring: `docs/mcp-post-deploy.md`.

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
- Dynamic mode tool names: Cloudflare's reference table says `portal_query_tools`/`portal_execute`, its changelog/prose says `query`/`execute` -> configurable `portal_dynamic_tools`. `portal_execute`/`portal_query_tools` argument keys are mapped heuristically from `inputSchema` (fails loudly if unmappable). Portal tool-name prefixing is unknown -> use `re:`. All unverified against a live portal.
- Sync only refreshes a server that already exists; registration is infra-config's. `waiting` after the bounded retries fails (by design).
- `queue: max` is a new GitHub concurrency option (already used by the engine); `needs`-skipped upstream smoke (no `mcp_url`) does not block the sync.
- Diet Manager is OAuth-user: an upstream service-token smoke is likely impossible (initialize needs auth), so leave `mcp_url` empty for it, rely on portal-level coverage, and keep its OAuth-user tests a separate check.

## Rollback
Revert the PR (new files only). Consumers that have not added the `mcp` job are unaffected; a consumer that has can remove the job; nothing in Cloudflare is created or changed by this repo (a sync only refreshes a server's tool/prompt list).
