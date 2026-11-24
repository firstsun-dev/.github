# Tasks

## .github
- [x] `actions/mcp-smoke` (+ mock-server tests: pass, wrong token, redirect, Bypass, missing tool, read-only call, SSE, timeout, dynamic portal)
- [x] `actions/mcp-portal-sync` (+ mock Cloudflare API tests: ready, waiting, retryable, stale/error, 403, malformed, config)
- [x] `.github/workflows/_mcp-post-deploy.yml`; actionlint + `check-workflows.py` production-guard / serialization rules
- [x] `docs/mcp-post-deploy.md`, `docs/mcp-consolidation/handoff-C.md`

## innovation-apps (separate PR, owner: Session B) — see docs/mcp-post-deploy.md
- [ ] Add the `mcp` job to each MCP workflow, switch to the shared smoke, retire `scripts/ci/access-smoke.mjs` for MCP apps
## infra-config (Session A)
- [ ] Portal/server ids and policies; token with `MCP Portals Write` -> secret
## Validation (Session D)
- [ ] Live dynamic-mode tool names and portal tool-name prefixing; end-to-end run
