# MCP post-deploy verification (upstream smoke -> portal sync -> portal smoke)

OpenSpec: `openspec/changes/add-mcp-portal-post-deploy`. Optional and additive: `_cf-worker-v2.yml` is unchanged.

```
Detect Changes -> pipeline (_cf-worker-v2.yml: CI, build/test, deploy, engine smoke, rollback)
                    -> mcp (_mcp-post-deploy.yml): upstream smoke -> [portal lock] sync -> portal smoke -> dynamic portal smoke
```

Every job runs only for a **verified Production push to main without rollback**; PR Previews / Dev / unverified or rolled-back deliveries / a skipped (unaffected) pipeline skip everything, so a Preview can never be registered in or smoked through a production portal.

## Test layers (rev 2: portal -> Worker runtime auth is service-token headers)
| Layer | What | Where |
|---|---|---|
| L1 direct Worker | smoke token OK; anonymous AND an invalid token refused (no Bypass); `initialize`, `tools/list`, expected tools, optional safe read-only call. A wrong-audience request is not testable from CI (needs a token of another app) and is covered by the Worker's own unit tests | `upstream-smoke` (`mcp_url`) |
| L2 portal | smoke token OK; anonymous / invalid refused; expected machine-visible tools; `portal_expect_absent_tools` (no cross-portal exposure; per-user-OAuth servers such as Diet Manager are excluded from machine sessions by design); dynamic mode (`?optimize_context=search_and_execute`: `portal_query_tools`, `portal_execute`, confirmed live; the legacy `?codemode=` tools `portal_codemode_*` are a different mode and not used) with a safe dynamic execute | `portal` job (`portal_url`) |
| L3 portal -> Worker runtime auth | sync returns server **ready with tools > 0** (`portal_sync_min_tools`, default 1), then `portal_read_calls` performs an actual tool call THROUGH the portal that reaches the Worker. A direct-Worker success alone proves nothing about the portal's registered credential | `portal` job (sync + `portal_read_calls`) |
| L4 human OAuth | Diet Manager per-user OAuth / ChatGPT: manual or a separate consumer check; never part of this stage | not here |

Credentials: CI uses ONLY the existing org secrets `CF_ACCESS_CLIENT_ID/SECRET` (token `firstsun-mcp-smoke`). It never receives the portal runtime credential (`firstsun-mcp-portal`, held by the portal's bearer registration) nor any backend token. Servers registered as bearer/custom-header are refreshed by the explicit sync API (the documented ~2 h background sync is described for DCR servers only), so the explicit POST is the minimal mechanism kept here.

## Consumer change (innovation-apps, per MCP workflow)

```yaml
  mcp:
    name: ANAS MCP post-deploy
    needs: [pipeline]
    if: ${{ !cancelled() && needs.pipeline.result == 'success' }}
    permissions:
      contents: read
    uses: firstsun-dev/.github/.github/workflows/_mcp-post-deploy.yml@v2   # same ref the pipeline uses
    with:
      app_name: anas-mcp
      deploy_target: ${{ needs.pipeline.outputs.deploy_target }}
      deploy_verified: ${{ needs.pipeline.outputs.deploy_verified }}
      rollback_result: ${{ needs.pipeline.outputs.rollback_result }}
      mcp_url: https://mcp.firstsun.org/anas/mcp
      expect_tools: health
      read_calls: '[{"tool":"health","arguments":{}}]'
      portal_server_ids: anas-mcp                        # server id == name (handoff-A)
      portal_url: https://mcp.firstsun.org/mcp           # yao portal: https://yao-mcp.firstsun.org/mcp, key yao-mcp
      portal_key: firstsun-mcp                           # same value for every app in this portal
      portal_expect_tools: re:health$                    # L2; upstream tool names may be server-prefixed - confirm live
      portal_expect_absent_tools: re:^(get_nutrition_context)$   # L2: no per-user-OAuth / other-portal tools
      portal_read_calls: '[{"tool":"health","arguments":{}}]'   # L3: a real call through the portal
    secrets:
      CF_ACCESS_CLIENT_ID: ${{ secrets.CF_ACCESS_CLIENT_ID }}
      CF_ACCESS_CLIENT_SECRET: ${{ secrets.CF_ACCESS_CLIENT_SECRET }}
      CLOUDFLARE_API_TOKEN: ${{ secrets.CF_MCP_PORTAL_API_TOKEN }}   # `MCP Portals Write` only (name per handoff-A)
      CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
```

Also: add `mcp` to the app's `gate` job `needs` (and treat `skipped` like the pipeline's), and drop the app-local MCP checks of `scripts/ci/access-smoke.mjs` (`smoke_cmd`) once the shared smoke is live — the engine's own smoke (`probe_path`, anonymous ingress script) stays. Before the first run `@v2` must point at a commit containing these files (or pin the commit).

An app outside any portal (e.g. a Worker not yet registered) sets only `mcp_url`; a portal-only call sets only `portal_*`.

## Inputs
| Input | Default | Meaning |
|---|---|---|
| `app_name`, `deploy_target`, `deploy_verified` | required | labels; pipeline outputs |
| `rollback_result` | `''` | pipeline output; non-empty skips all |
| `mcp_url`, `expect_tools`, `expect_server_name`, `read_calls`, `require_anon_reject` | `''`, `''`, `''`, `''`, `true` | upstream smoke (empty `mcp_url` = skipped) |
| `portal_server_ids`, `portal_sync_min_tools` | `''`, `1` | server ids to sync (empty = no sync); min tools the synced server must report |
| `portal_url`, `portal_expect_tools`, `portal_expect_absent_tools`, `portal_read_calls`, `portal_dynamic_smoke` (`true`), `portal_dynamic_tools` | | portal smoke (empty `portal_url` = skipped) |
| `portal_key` | `app_name` | concurrency identity; apps sharing a portal must share it |
| `runner` | `linux` | |

Secrets (all optional): `CF_ACCESS_CLIENT_ID/SECRET` (Access service token admitted by `allow-firstsun-mcp-smoke`), `CLOUDFLARE_API_TOKEN` (+`CLOUDFLARE_ACCOUNT_ID`, sync only; permission **MCP Portals Write**). `expect_tools`/`read_calls` accept `re:<regex>`. Dynamic mode maps `portal_execute` arguments from its `inputSchema` (live: `portal_query_tools(pattern, limit)`, `portal_execute(tool_name, arguments)`). The sync API's answer must carry `result.tools` (as infra-config's `sync-mcp-servers.sh` reads it); an answer without it fails the min-tools check - set `portal_sync_min_tools: 0` only if live behaviour proves otherwise.

## OAuth-user tests are separate
This stage is service-token only (its job summaries say so). Diet Manager (oauth, `on_behalf=true`) is invisible to service-token sessions: assert its tools ABSENT in the machine session (`portal_expect_absent_tools`). Its OAuth-user tests must stay a separate consumer job/check; never fold them into this result.

## Verified against Cloudflare docs (2026-11-24)
- Sync: `POST /accounts/{account_id}/access/ai-controls/mcp/servers/{id}/sync`, permission `MCP Portals Write`; returns `{success, result:{status: waiting|ready|stale|error, error, error_details}}`.
- Portal service-token sessions: `CF-Access-Client-Id/-Secret` at the portal URL; each linked server needs `on_behalf: false` and a Service Auth policy for the same token.
- Dynamic mode `?optimize_context=search_and_execute`: live-confirmed by Session D: `portal_query_tools` + `portal_execute` (default `portal_dynamic_tools`). Upstream tool-name prefixing through a portal is still unobserved (no machine-visible server before rev 2) - use `re:` until Session D re-runs with `on_behalf=false` servers.
