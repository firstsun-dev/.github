# Cloudflare Worker CI/CD v2

Opt-in reusable workflow `.github/workflows/_cf-worker-v2.yml`. v1 (`_cf-worker-template.yml`, `@v1`, `@v1.5.x`) is unchanged.
Spec: `openspec/changes/redesign-cloudflare-worker-cicd-v2` (PR #44), tracking #43.

## Contract

| Event | production-preview | hybrid | production-only |
|---|---|---|---|
| push, non-main | tests → **branch Native Preview** | same; never touches persistent Dev | tests only |
| pull_request | tests; attach existing Preview URL (never deploys) | same | tests |
| fork PR | tests, no secrets | same | same |
| delete (branch) | delete that Preview + owned resources | same | – |
| PR closed/merged | Preview kept | same | – |
| workflow_dispatch `target=persistent-dev` on `persistent_dev_ref` | – | protected Dev deploy | – |
| push main | Production | Production | Production |

Identity: `br-<slug≤24>-<sha256(repo\nworker\nfull-ref)[:10]>`, deterministic, collision-proof, scoped per Worker.
Preview URL comes only from `wrangler preview --json` → `.preview.urls[0]`.

## Caller (must be on the default branch for `delete` to work)

```yaml
name: CI
on:
  push:                # no `paths:` filter — every branch push must yield a URL
  pull_request:
  delete:
  workflow_dispatch:
    inputs:
      target: { type: choice, options: [none, persistent-dev], default: none }
permissions:
  contents: read
  deployments: write
  pull-requests: write
jobs:
  cf:
    uses: firstsun-dev/.github/.github/workflows/_cf-worker-v2.yml@<SHA-or-v2.x.y>
    with:
      tools_ref: <same SHA-or-v2.x.y>
      app_name: www
      build_cmd: pnpm build
      deployment_strategy: production-preview
      app_url_prod: https://example.com
    secrets:
      CLOUDFLARE_PREVIEW_API_TOKEN: ${{ secrets.CLOUDFLARE_PREVIEW_API_TOKEN }}
      CLOUDFLARE_PREVIEW_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_PREVIEW_ACCOUNT_ID }}
      CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
      CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
```

Monorepos: one caller job per Worker (`app_name`, `app_path`), each guarded by a changed-path
filter (`_detect-changes.yml`); the `delete` event must run **all** caller jobs (cleanup is idempotent).

## Wrangler config requirements

* Top-level `previews` block (may be `{}`), Wrangler ≥ 4.135.0 installed project-locally.
* Every Production storage/queue/service binding needs a Preview counterpart on a different resource (enforced by `preflight`).
* `services`, `hyperdrive`, `secrets_store_secrets` in `previews` require an explicit `preview_binding_allow` entry (e.g. `services:ID`) — Preview service bindings hit the target's **Production** deployment.
* No `routes`, `triggers`, queue consumers in `previews`; `assets`/`compatibility_*` stay top-level.

## Failure semantics

* Test/preflight/provision/build failure → `wrangler preview` is never invoked; prior Preview unchanged.
* Smoke failure after publish → Deployment `failure` ("unverified"), then **recovery**: redeploy the newest earlier GitHub Deployment with state `success` (checkout that SHA, rebuild, `wrangler preview`, probe). The run stays red. No Native Preview rollback command is assumed. Caveat: branch D1 schema is not rolled back; migrations must be forward-compatible.
* No prior good deployment → explicit manual-recovery summary (push a fix or delete the branch).
* Production: `production_rollback_policy` = automatic | manual | disabled; a rollback never reverts D1.

## Security

* Preview jobs only receive `CLOUDFLARE_PREVIEW_*`; Production credentials exist only in `production*` jobs; Dev credentials only in `persistent-dev` (protected `<app>/dev` environment).
* Fork PRs get no deployment jobs and no secrets.
* `production-only` never creates a Preview (use for `anas-mcp`: its auth depends on Cloudflare Access at the custom domain).

## State ledger

GitHub Deployments, environment `<app_name>/preview/<preview-name>`, payload `{branch_ref, preview_name, worker}`. Used for last-good recovery, PR URL lookup and orphan reconciliation. Per-branch resources (D1 etc.) created by `preview_provision_cmd` must be named from `PREVIEW_NAME`, carry ownership metadata, and be removed by `preview_cleanup_cmd`.

## Not yet implemented / needs real Cloudflare validation

* `wrangler preview --json` full output shape and `delete` not-found message are asserted from docs only; the pilot must confirm them.
* Orphan reconciliation scheduled job and quota ceilings (see openspec task Phase 5).
