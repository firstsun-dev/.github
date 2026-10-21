# Cloudflare Worker CI/CD v2

> **Lifecycle — supported v2, maintenance-only (2026-10-21).** This page documents the **currently shipped** `@v2` behavior and remains valid for existing v2 consumers. New CI/CD refactor work and unfinished consumer migrations move to [vNext OpenSpec (PR #57)](https://github.com/firstsun-dev/.github/pull/57); see the [v2 OpenSpec handoff](../openspec/changes/redesign-cloudflare-worker-cicd-v2/tasks.md). **Do not assume vNext is implemented or released.** v1/v2 contracts stay supported until an explicitly reviewed migration.


Opt-in reusable workflow `.github/workflows/_cf-worker-v2.yml` plus `actions/cf-preview` tooling.
v1 (`_cf-worker-template.yml`, `@v1`, `@v1.5.x`) is unchanged and independent.
Spec: `openspec/changes/redesign-cloudflare-worker-cicd-v2`; tracking issue #43.

## What it does

* Every push to a non-`main` branch publishes (or updates) **that branch's own Native Preview** with a stable URL — no PR needed.
* `main` pushes go through the Production path (migrate → upload/deploy → verify → recovery policy).
* A Preview is **never** deployed to a shared Dev Worker, and Production credentials never reach Preview jobs.
* Strategies: `production-preview` (default), `hybrid` (adds a protected persistent Dev for Cron/Queue), `production-only` (CI only, no Preview — e.g. `anas-mcp`).

## Versions and pinning

| Ref | Meaning |
|---|---|
| `@v2` | floating major tag (same convention as `@v1`); always the latest compatible 2.x |
| `@v2.x.y` | immutable release |

`uses:` and the `tools_ref:` input **must be the same ref** (the workflow checks out the tooling at `tools_ref`).

| Release | Change |
|---|---|
| v2.0.0 | first release (pilot: heaven-www) |
| v2.1.0 | E2E matrix, Mailpit, `build_env_*`, `preview_smoke_env`; split test jobs behind one `Tests` gate |
| v2.2.0 | `preview_worker_name` |
| v2.3.0 | status-aware smoke probe (requires explicit 2xx; recognises Access-protected Previews) |
| v2.5.0 | Preview name = plain branch slug; deleting a branch keeps its Preview |
| v2.5.1 | **fix:** Preview/Production were silently skipped when optional test jobs were skipped (use ≥ 2.5.1) |
| v2.6.0 | the CI job shows the Preview URL (`environment.url`) |

## Contract

| Event | production-preview | hybrid | production-only |
|---|---|---|---|
| push, non-main | tests → **branch Native Preview** | same; never touches persistent Dev | tests only |
| pull_request | attach the existing Preview URL (never deploys) | same | tests |
| fork PR | no secrets, no deploy | same | same |
| delete (branch) | **Preview is kept**; GitHub ledger retired | same | – |
| PR closed / merged | Preview kept | same | – |
| `workflow_dispatch` `target=persistent-dev` on `persistent_dev_ref` | – | protected Dev deploy | – |
| push main | Production | Production | Production |

### Preview identity and URL

* Name = the normalised branch name: `feat/Solar_Term` → `feat-solar-term`. No prefix, no hash, no commit SHA.
* URL = `https://<slug>-<worker>.<subdomain>.workers.dev`, e.g. `https://feat-solar-term-blog-preview.firstsun.workers.dev`. One stable URL per branch for the life of the branch; pushes never change it. (Cloudflare also creates a per-deployment URL `<id>-<worker>…`; it is never advertised.)
* The slug is truncated so `<slug>-<host worker>` fits a 63-character DNS label. An 8-hex slug gets a `b-` prefix so it never resembles a per-deployment URL.
* Normalisation is lossy (`Feat/A` and `feat-a` → `feat-a`). The GitHub Deployment ledger records which **full branch ref** owns a name; if another ref already owns it, the run **fails in Plan, before any tests**, naming the owner. Rename the branch, or delete that Preview and its ledger.
* The URL is read only from `wrangler preview --json` → `.preview.urls[0]`; it is never constructed.

### Where to find the URL

* On the **CI job**: `Branch Preview` declares `environment: <app_name>/preview` with the URL → *View deployment* in the Actions graph and run page.
* Run annotation "Preview URL" and the run summary.
* Deployments tab, per-branch ledger `<app>/preview/<slug>`; PR comment when a PR is open.
* Reusable-workflow output `preview_url`.

## Caller

The workflow file that handles `delete` must be on the **default branch**. Branches that still carry an older workflow file keep running that older file on push.

```yaml
name: CI/CD
on:
  push:                 # no `paths:` filter — every branch push must produce a URL
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
    uses: firstsun-dev/.github/.github/workflows/_cf-worker-v2.yml@v2
    with:
      tools_ref: v2
      app_name: www
      build_cmd: pnpm run build
      deployment_strategy: production-preview
      app_url_prod: https://example.com
    secrets:
      CLOUDFLARE_PREVIEW_API_TOKEN: ${{ secrets.CLOUDFLARE_PREVIEW_API_TOKEN }}
      CLOUDFLARE_PREVIEW_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_PREVIEW_ACCOUNT_ID }}
      CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
      CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
```

Notes

* A repo may map the Preview secrets to its existing deploy credentials (owner decision; acceptable for stateless Workers). A dedicated least-privilege Preview token is recommended for Workers with stateful bindings.
* Monorepos: one caller job per Worker (`app_name`, `app_path`), each guarded by a changed-path filter; the `delete` event must run **all** caller jobs (retiring the ledger is idempotent).
* Run `lint`/typecheck before the shared pipeline in the caller, as usual, and make the `pipeline` job depend on them.

## Inputs worth knowing

| Input | Purpose |
|---|---|
| `test_unit_cmd`, `test_integration_cmd`, `test_e2e_cmd` (+ `test_e2e_setup_cmd`, `test_e2e_env`, `test_e2e_variants`, `use_mailpit`) | run as parallel jobs behind one `Tests` gate; leave empty to skip |
| `build_env_preview`, `build_env_prod` | build-time `KEY=VALUE` lines (`VITE_APP_ENV=preview` / `prod` are set automatically) |
| `preview_smoke_cmd`, `preview_smoke_env`, `preview_sha_path` | smoke on the **exact** Preview URL (`TARGET_URL`, `EXPECTED_SHA`, `PREVIEW_ACCESS_PROTECTED`) |
| `preview_binding_allow`, `preview_deny_resources` | explicit approvals / forbidden Production resources for the binding audit |
| `preview_worker_name` | host Previews under another existing Worker (see below) |
| `production_rollback_policy` | `automatic` \| `manual` \| `disabled` |
| `preview_provision_cmd`, `preview_migrate_cmd` | optional hooks run before publish (resources must be Preview-only) |
| `preview_cleanup_cmd` | **deprecated and ignored** |

## Hosting Previews under another Worker (`preview_worker_name`)

Native Preview URLs need the **Worker-level** `previews_enabled` setting. Verified: a Worker with
`{enabled:false, previews_enabled:false}` returns `preview.urls: []`; `{enabled:false, previews_enabled:true}` gets a URL.
If the production Worker keeps workers.dev / Preview URLs off on purpose (enabling them would expose every uploaded
Production Version, bound to Production resources), set `preview_worker_name` to an existing dev Worker that has
`previews_enabled` (Blog uses `blog-preview`). `wrangler preview --worker-name` is used for publish and recovery.

## Preview configuration rules

* Top-level `previews` block (may be `{}`) and a project-local Wrangler ≥ 4.135.0 (the workflow refuses to fall back to Dev/Production).
* Every Production storage / queue / service binding needs a Preview counterpart on a **different, non-production** resource.
* **Every Preview binding needs an explicit resource id.** Native Previews do not auto-provision; Cloudflare rejects an id-less binding with API error 10021.
  Adapters can add bindings at build time (the Astro Cloudflare adapter adds a `SESSION` KV), so preflight audits both the source config and the build-generated (redirected) config.
* `services`, `hyperdrive`, `secrets_store_secrets` in `previews` need an explicit `preview_binding_allow` entry (e.g. `services:ID`): Preview service bindings reach the target's **Production** deployment.
* No `routes`, `triggers` or queue consumers in `previews`; `assets` and `compatibility_*` stay top-level.
* Preview secrets are **not** inherited from Production: `wrangler preview base-config secret put <NAME>`.

## Shared Preview resources (accepted trade-off)

Preview resources are durable, non-production resources **shared by all branch Previews** (no per-branch D1/R2).

> **v2 implementation note:** the currently released v2 may run migration hooks from different branches; it has not proven cross-branch schema concurrency safety. vNext instead requires an **independent Migration job** plus whole Migration-to-Deploy coordination. This is a future requirement, not a present v2 guarantee.
Consequences accepted by the owner: branches share state; migrations that the caller runs against the shared Preview
database (Blog runs `db:migrate:dev` before publishing) run from any branch and can race or disturb another branch's Preview.
Per-branch resources (`preview_provision_cmd`) remain possible but are not the supported default.

## Smoke semantics

The built-in probe (`actions/cf-preview/probe.sh`) requires an explicit `2xx` from `<preview-url>/`; plain redirects
and 4xx/5xx fail (`curl -f` would accept any 3xx). A redirect to `https://*.cloudflareaccess.com/` is accepted as
**access-protected**: the Preview exists and is not public, but content and SHA are unverified. This is reported in the run
summary and exported as `PREVIEW_ACCESS_PROTECTED=true` to `preview_smoke_cmd`, which can then assert the protection
(Blog) or use Access service-token headers (`CF-Access-Client-Id` / `CF-Access-Client-Secret`) for content checks.

## Failure semantics

* Test / preflight / hook / build failure → `wrangler preview` is never invoked; the prior Preview is unchanged.
* Smoke failure after publish → Deployment marked `failure` ("unverified"), then **recovery**: redeploy the newest earlier
  GitHub Deployment in state `success` (checkout that SHA, rebuild with the same Preview env, publish, probe). The run stays red.
  No Native Preview rollback command is assumed. Shared-database schema changes are not rolled back.
* No earlier good deployment → explicit manual-recovery summary (push a fix, or delete the Preview).
* Name collision → fails in Plan (see *Preview identity*).
* Production: `production_rollback_policy`; a Worker rollback never reverts D1 migrations or external side effects.

## Branch deletion keeps the Preview (owner decision)

Deleting a branch does **not** delete its Native Preview. Cloudflare cannot remove only the stable URL: Preview `urls`
is read-only (a `PATCH` is accepted but ignored) and `wrangler preview delete` removes the Preview with all deployments.
The `delete` run only marks the GitHub Deployments `inactive` (never deletes them) and prints the manual command:

```bash
wrangler preview delete --name <slug> --worker-name <worker> --skip-confirmation
```

There is no automatic reclaim; Previews accumulate until removed manually. A repeated delete returns
`Preview not found [code: 10025]` (already gone). With several Cloudflare accounts, set `CLOUDFLARE_ACCOUNT_ID`.

## Security

* Preview jobs receive only `CLOUDFLARE_PREVIEW_*`; Production credentials exist only in the production job; Dev credentials only in `persistent-dev` (protected `<app>/dev` environment).
* Fork PRs get no deployment job and no secrets.
* `production-only` never creates a Preview (use for `anas-mcp`: its authentication depends on Cloudflare Access at the custom domain).
* Native Preview hostnames may sit behind Cloudflare Access (Blog's do): anonymous requests see only the Access login.

## State ledger

GitHub Deployments, environment `<app_name>/preview/<slug>`, payload `{branch_ref, preview_name, worker}`. Used for name
ownership, last-good recovery and PR URL lookup. Retired (`inactive`), never deleted, on branch deletion. It is separate from the
job environment `<app_name>/preview`, which only exists to show the URL on the CI job.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Wrangler X < 4.135.0` | project-local Wrangler too old | upgrade `wrangler` (latest); remove stale pnpm overrides such as a pinned `miniflare` |
| `preview.urls: []` | Worker `previews_enabled` is false | enable it, or set `preview_worker_name` to a Worker that has it |
| API `10021 … must have a namespace_id` | Preview binding without an id (often an adapter-added KV `SESSION`) | give it a Preview resource id, or disable the binding for Preview builds |
| Plan fails: "Preview name … already used by …" | two branches normalise to the same slug | rename one, or delete the old Preview + ledger |
| Run green but no Preview / no Production deploy | optional test jobs skipped on an old release | use `>= v2.5.1` |
| Smoke says `access-protected` | URL is behind Cloudflare Access | expected; use a service token for content checks |
| Branch pushes show the old Dev flow | that branch still has the old workflow file | rebase / merge `main` |
| `delete` did nothing | caller workflow not on the default branch | merge it to `main` |

## Verified vs not yet verified

Verified on real Cloudflare (heaven-www, Blog): first/second push keep one URL; independent branches; PR attach without a second deployment;
PR close keeps the Preview; branch delete keeps the Preview and retires the ledger; name-collision fail-closed; Production
versions upload/deploy; smoke-failure recovery; Access-protected smoke; `preview_worker_name`.

Not yet verified: concurrent pushes and force-push ordering, fork-PR credential isolation, Hybrid persistent Dev, stateful
bindings beyond Blog's shared D1/R2, quota limits, and an automatic sweep of old Previews (not implemented).
