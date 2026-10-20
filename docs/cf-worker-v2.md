# Cloudflare Worker CI/CD v2

Opt-in reusable workflow `.github/workflows/_cf-worker-v2.yml`. v1 (`_cf-worker-template.yml`, `@v1`, `@v1.5.x`) is unchanged.
Spec: `openspec/changes/redesign-cloudflare-worker-cicd-v2` (PR #44), tracking #43.

## Contract

| Event | production-preview | hybrid | production-only |
|---|---|---|---|
| push, non-main | tests → **branch Native Preview** | same; never touches persistent Dev | tests only |
| pull_request | tests; attach existing Preview URL (never deploys) | same | tests |
| fork PR | tests, no secrets | same | same |
| delete (branch) | **Preview is kept** (stable URL stays); GitHub ledger retired | same | – |
| PR closed/merged | Preview kept | same | – |
| workflow_dispatch `target=persistent-dev` on `persistent_dev_ref` | – | protected Dev deploy | – |
| push main | Production | Production | Production |

Identity = the normalised branch name (`feat/Solar_Term` → `feat-solar-term`): no prefix, no hash, no commit SHA.
URL: `https://<slug>-<worker>.<subdomain>.workers.dev` (e.g. `feat-solar-term-blog-preview…`), **one stable URL per branch**.
Slug is truncated so `<slug>-<host worker>` fits a 63-char DNS label; an 8-hex slug gets a `b-` prefix so it never
resembles a per-deployment URL. Normalisation is lossy (`Feat/A` and `feat-a` → `feat-a`): the GitHub Deployment
ledger records which full branch ref owns a name and the run **fails closed in Plan, before tests**, if another
branch already owns it (rename the branch, or delete that Preview + ledger).
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

## Test inputs (v2.1.0+)

`test_unit_cmd`, `test_integration_cmd`, `test_e2e_cmd` (+ `test_e2e_setup_cmd`, `test_e2e_env`,
`test_e2e_variants` JSON matrix, `use_mailpit`) run as parallel jobs behind a single `Tests` gate.
`preview_smoke_env` (KEY=VALUE lines) is exported before `preview_smoke_cmd`, which receives
`TARGET_URL` and `EXPECTED_SHA`. Browsers path defaults to `/opt/playwright-browsers`.

## Where to find the Preview URL

* **On the CI job itself:** the `Branch Preview` job declares `environment: <app_name>/preview` with the Preview URL,
  so the job shows a **"View deployment"** link in the Actions graph and on the run page
* **Run annotation** "Preview URL" and the run summary
* **Deployments** tab: per-branch ledger `<app>/preview/<name>` (`environment_url`), and the PR comment when a PR is open
* Reusable-workflow output `preview_url` for callers

## Hosting Previews under another Worker (`preview_worker_name`)

Native Preview URLs need the **Worker-level** `previews_enabled` setting (verified: a Worker with
`{enabled:false, previews_enabled:false}` gets `preview.urls: []`; `{enabled:false, previews_enabled:true}`
gets a URL). If the production Worker deliberately keeps workers.dev/Preview URLs off (public URLs bound to
production resources), set `preview_worker_name` to an existing dev Worker that has `previews_enabled`;
`wrangler preview --worker-name` is used for publish and recovery (manual deletion: `wrangler preview delete --name <n> --worker-name <w>`).

## Wrangler config requirements

* Top-level `previews` block (may be `{}`), Wrangler ≥ 4.135.0 installed project-locally.
* Every Production storage/queue/service binding needs a Preview counterpart on a different resource (enforced by `preflight`).
* `services`, `hyperdrive`, `secrets_store_secrets` in `previews` require an explicit `preview_binding_allow` entry (e.g. `services:ID`) — Preview service bindings hit the target's **Production** deployment.
* No `routes`, `triggers`, queue consumers in `previews`; `assets`/`compatibility_*` stay top-level.

## Branch deletion keeps the Preview (owner decision)

Deleting a branch does **not** delete its Native Preview or resources. Cloudflare cannot remove only the
stable URL (Preview `urls` is read-only; `wrangler preview delete` removes the Preview with all deployments),
so the options were "keep everything" or "delete everything"; the owner chose to keep. The `delete` run only
marks the GitHub Deployments `inactive` and prints the manual delete command. There is no automatic reclaim:
Previews accumulate until removed manually (or by a future scheduled sweep driven by the ledger).

## Smoke semantics

The built-in probe (`actions/cf-preview/probe.sh`) requires an explicit `2xx` from `<preview-url>/`;
plain redirects and 4xx/5xx fail. A redirect to `https://*.cloudflareaccess.com/` is accepted as
**access-protected** (the Preview exists and is not public) and is surfaced in the summary, but served
content / SHA are then unverified; `preview_smoke_cmd` receives `PREVIEW_ACCESS_PROTECTED=true` so it can
skip content checks or use Access service-token headers.

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

GitHub Deployments, environment `<app_name>/preview/<preview-name>`, payload `{branch_ref, preview_name, worker}`. Used for last-good recovery, PR URL lookup and (future) orphan reconciliation. On branch deletion the entries are marked `inactive`, never deleted. `preview_provision_cmd`/`preview_cleanup_cmd` are legacy hooks (`preview_cleanup_cmd` is ignored): the supported model is shared durable Preview resources.

## Not yet implemented / needs real Cloudflare validation

* `wrangler preview --json` full output shape and `delete` not-found message are asserted from docs only; the pilot must confirm them.
* Orphan reconciliation scheduled job and quota ceilings (see openspec task Phase 5).
