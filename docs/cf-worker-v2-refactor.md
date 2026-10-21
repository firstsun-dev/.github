# CF Worker v2 — in-place refactor: interface, concurrency design, migration guide

`.github/workflows/_cf-worker-v2.yml` was refactored **in place** (breaking, no `v3`). Spec: `openspec/changes/refactor-cloudflare-worker-cicd-v2` (PR #57). Shipped v2.x behavior stays documented by `docs/cf-worker-v2.md` / PR #56 as the historical record; **the interface below replaces it**. The floating `@v2` tag has not moved and no consumer has been changed.

## Job DAG

```
Detect Changes (monorepo only — the CALLER runs _detect-changes.yml and gates its calls)
        │
   ┌────┴────┐
   CI       E2E            parallel; no `needs` between them
   └────┬────┘
    Migration              dedicated job, only when d1_migrations: true
        │
     Deploy                ONE job, target = prod (main) | preview (any other eligible branch)
        │
    Release                optional (release_cmd), main only

 outside the core DAG:  lifecycle (same-repo PR comment / branch-delete ledger retire)
                        persistent-dev (Hybrid, protected workflow_dispatch on the approved ref)
```

* **CI** (steps, one job): setup, `lint_cmd`, `typecheck_cmd`, `test_unit_cmd`, `test_integration_cmd`, build **validation** (`build_cmd` under the target's `build_env_*` then `ci_env`; nothing is uploaded).
* **E2E**: own setup, own **test** build (`test_e2e_build_cmd`, `test_e2e_env`), optional variants matrix. No `test_e2e_cmd` ⇒ no job; an intentional skip never blocks Migration/Deploy (the `always()` gating from the heaven-www incident is kept and tested).
* **Migration**: after CI+E2E succeed. A failure/cancel blocks Deploy.
* **Deploy**: checks the target (fail-closed re-check of the job `if`), stale-SHA guard, exact-SHA checkout, setup, target build env, independent build, source **and effective (adapter-generated)** Wrangler binding audit, publish (`wrangler preview` or `versions upload`+`deploy @100`), real URL, smoke (Access-aware), Deployment status, recovery.
* Same-repo PRs do not re-run CI/E2E (the push run of the same SHA covers them); fork PRs run CI/E2E credential-free. No job other than `lifecycle` ever runs on a PR with write capabilities.

## Interface (inputs)

| Group | Inputs |
|---|---|
| Identity | `tools_ref` (must equal the ref the caller pins), `app_name`, `app_path`, `app_version`, `runner`, `wrangler_config` |
| Strategy | `deployment_strategy` (`production-preview` \| `hybrid` \| `production-only`), `preview_on_branch_push`, `preview_worker_name` |
| CI | `lint_cmd`, `typecheck_cmd`, `test_unit_cmd`, `test_integration_cmd`, `build_cmd` (required), `ci_env`, `use_mailpit` |
| E2E | `test_e2e_cmd`, `test_e2e_setup_cmd`, `test_e2e_build_cmd`, `test_e2e_env`, `test_e2e_variants` |
| Deploy | `build_env_prod`, `build_env_preview`, `smoke_cmd`, `smoke_env`, `sha_path`, `app_url_prod`, `access_protected_targets`, `production_rollback_policy`, `preview_binding_allow`, `preview_deny_resources` |
| Migration | `d1_migrations`, `d1_binding`, `d1_lock_scope` (no command override) |
| Release | `release_cmd` (+ secret `RELEASE_TOKEN`) |
| Hybrid | `persistent_dev_ref`, `dev_deploy_cmd`, `dev_smoke_cmd`, `dispatch_target` |

Outputs: `deploy_url`, `deploy_target`, `version_id` (prod only). The internal `deployed_and_verified` Deploy output is what gates Release. Secrets: `CLOUDFLARE_API_TOKEN/ACCOUNT_ID` (prod), `CLOUDFLARE_PREVIEW_API_TOKEN/ACCOUNT_ID`, `CLOUDFLARE_DEV_*`, `PREVIEW_SECRETS`, `SMOKE_USERNAME/PASSWORD`, `RELEASE_TOKEN`.

`*_env` inputs are `KEY=VALUE` lines imported by `actions/cf-preview/parse-env.mjs` **from an environment variable** (never interpolated into a script). It rejects malformed lines and refuses `CLOUDFLARE_*`, `GITHUB_*`, `RUNNER_*`, `PATH`, `NODE_OPTIONS`, `LD_*`, `BASH_ENV`, `GIT_*`, `SMOKE_*` credentials, etc.; all-or-nothing. Application variable names remain consumer-owned. Command inputs are passed through `env:` and run with `bash -c "$CMD"`.

### Removed (old v2 → refactored)

| Removed | Replacement |
|---|---|
| jobs `plan`, `test-unit`, `test-integration`, `test-e2e`, `test` (gate), `preview`, `production`, `production-verify`, `production-recovery`, `pr-link`, `cleanup` | `ci`, `e2e`, `migrate`, `deploy`, `release`, `lifecycle` |
| `preview_build_cmd` | one `build_cmd`; targets differ via `build_env_prod` / `build_env_preview` |
| `preview_provision_cmd`, `preview_cleanup_cmd` (already ignored) | removed: shared durable Preview resources with explicit `previews` bindings |
| `preview_migrate_cmd`, `migrate_cmd_prod` | `d1_migrations: true` in the dedicated Migration job. **No command override**: a custom command could skip the target-D1 check, so the built-in `wrangler d1 migrations` path is the only one |
| `preview_smoke_cmd`, `preview_smoke_env`, `verify_cmd` | `smoke_cmd`, `smoke_env` (both targets; `TARGET_URL`, `EXPECTED_SHA`, `DEPLOY_TARGET`, `TEST_ENV`) |
| `preview_sha_path` | `sha_path` |
| `test_e2e_setup_cmd` doing the build | `test_e2e_setup_cmd` + `test_e2e_build_cmd` |
| output `preview_url` | `deploy_url` (+ `deploy_target`) |
| `actions/cf-preview/publish.sh` | `deploy.sh` (both targets) |

## Migration, concurrency, release and secrets

### Migration safety rules
* **Target D1.** `migrate.sh` always derives the target from the *source* Wrangler config: Production = top-level `d1_databases`, Preview = `previews.d1_databases` (never `env.dev`). It requires explicit `database_id`/`database_name`, one unambiguous binding (`d1_binding` when several), Preview ≠ Production database, and a migrations directory containing `.sql` files, then writes a minimal config naming only that D1 (all `migrations_*` settings such as `migrations_table`/`migrations_pattern` are carried over; `migrations_dir` is made absolute). There is no way to bypass this with a custom command.
* **Pending-only, no-op safe.** `wrangler d1 migrations list` must exit 0 (state readable, target reachable) and `apply` is itself pending-only. The "No migrations to apply" text only skips a no-op `apply`; correctness does not depend on parsing it.
* **Everything that can be checked without mutation happens before the first remote command** (inside the existing Migration job, no Plan job): trusted target + credentials, Preview identity and **ledger ownership** (a branch whose normalised name is owned by another ref fails here), source binding policy (`preflight`), exact migration target, branch tip (a deleted/superseded branch applies nothing). Deploy re-checks ownership and the tip before publishing.
* Failed migration ⇒ Deploy blocked; PRs never reach it; Worker recovery never rolls D1 back.
* **Not implemented (do not assume):** SQL checksum/drift detection or rejection of incompatible histories when several branches share the Preview D1. A branch that edits an already-applied migration, or two branches adding conflicting schema, is not detected; forward-compatible schema changes remain a convention.

### Concurrency (final)
The earlier custom Git-ref lock (`lock.sh`, `refs/cfv2-locks/*`, `contents: write`) was **removed**: GitHub API errors, non-atomic takeover and unverified ruleset behaviour made it unsafe to rely on.

The shared workflow now sets **workflow-level** `concurrency` with `queue: max` (GitHub changelog 2026-05-07; FIFO, up to 100 pending runs per group, extra runs are cancelled, `queue: max` cannot be combined with `cancel-in-progress: true`). Because a group cannot span two jobs, the unit is the whole run, so Migration→Deploy is protected as one interval; CI and E2E remain parallel inside a run.

| Run | Group |
|---|---|
| main push / dispatch | `cfv2-<app>-prod` |
| branch, `d1_migrations: true` | `cfv2-<app>-preview-d1-<d1_lock_scope or app>` — **one group for the shared Preview D1**, not per branch |
| branch, no D1 | `cfv2-<app>-preview-<branch>` |
| PR, delete, tag, persistent-dev | private `cfv2-run-<run_id>` (never queues behind a deploy) |

* Prod and Preview-D1 never block each other; apps with different physical D1 do not block each other (apps that share one Preview D1 must set the same `d1_lock_scope`).
* A queued run that is no longer the branch tip applies no migration and publishes nothing (tip re-checked in Migration, and twice in Deploy).
* The `cfv2-` prefix is static on purpose and `github.workflow` is not used: **never put the same group (or `${{ github.workflow }}`-derived group at the calling job) in a caller**, GitHub documents that caller and called workflow with the same group cancel/deadlock each other. Callers' own groups (e.g. heaven-www `CI/CD-<ref>`) are different strings and are fine; they still apply to the caller run (default `queue: single` there is acceptable because a superseded push is skipped anyway).
* Trade-offs: the whole run, including CI/E2E, queues behind earlier runs of the same group; a run waiting for an Environment approval holds the group; when 100 are pending extra runs are cancelled (fail-closed, nothing deployed); a repo-local group does **not** protect a D1 shared with another repository or persistent Dev.
* `actionlint` 1.7.12 does not know the `queue` key; `.github/actionlint.yaml` ignores exactly that message for exactly this file. Syntax validity of `queue` is therefore **not** proven by actionlint.

**Verified in a scratch repository (2026-10-21, private repo `ClaudiaFang/cfv2-concurrency-scratch`, real GitHub Actions, no Cloudflare):** a called reusable workflow with top-level `concurrency: { group: …${{ inputs.key }}, queue: max }` and a caller with no concurrency of its own. Four `workflow_dispatch` runs with the same key (A1–A4, each `one` 25 s → `two`) ran strictly one after another in trigger order, none was cancelled, and A2's first job started only after A1's *second* job had finished (so the gap between two jobs of one run is covered); a run with a different key ran concurrently and was not blocked. Control without `queue` (B1–B4): B2 and B3 were cancelled and only B1 and B4 ran (the old single-pending behaviour this design avoids). GitHub accepted `queue: max` in that position.
**Still not verified:** the called workflow was in the *same* repository (`./.github/workflows/…`), not a cross-repository `uses: firstsun-dev/.github/...@ref` as consumers use; queue-full (100) behaviour; Environment-approval interaction (a waiting run is expected to hold the group); caller/callee same-group deadlock was not reproduced; `workflow_dispatch` only (push events not exercised). Re-run the cross-repository variant before moving the floating `@v2`; if it is not honoured, the fallback is requiring every caller to set the same group on the calling job.

### Release correctness
`Deploy` exposes `deployed_and_verified` (default false). It becomes true only in the step that runs after **this run published its own SHA and smoke passed**. A stale run, a skipped publish, a failed smoke (with or without rollback), or a missing URL leaves it false. `Release` requires `deploy.result == success`, `deployed_and_verified == true` and `target == prod`. A configured E2E that did not run (skipped / empty or invalid matrix) now blocks Migration and Deploy; only `test_e2e_cmd: ''` allows a skip.

### Smoke
Fail-closed (`smoke.sh`): no URL ⇒ fail; only 2xx counts (401/403/3xx-elsewhere/5xx/no response fail); a `sha_path` that is missing or serves another commit fails; a redirect to Cloudflare Access is **liveness only** and passes only for targets listed in `access_protected_targets` (default `preview`; prod must opt in explicitly). Production rollback runs through `deploy.sh ROLLBACK_TO`, i.e. the same Wrangler config/redirect resolution as the publish, then the run stays red and Release stays closed.

### Preview secrets
`PREVIEW_SECRETS` is staged by `parse-env.mjs` (validated names, all-or-nothing, `::add-mask::`, owner-only file, removed at the end of the job) and exported into the Preview build/smoke processes through quoted variables (`envfile.sh`), never `source`d. Values with `$()`, backticks, quotes, `;`, `=` and spaces arrive literally; names such as `CLOUDFLARE_*`, `GITHUB_*`, `PATH`, `NODE_OPTIONS` are rejected; multi-line values are not supported (a newline starts a new `NAME=` line and a non-conforming line is an error). Only the Deploy publish/smoke steps see them; CI, E2E and Migration never do.

## Security review (summary)

* Credentials: each Cloudflare credential in Migration/Deploy is `(main && prod-secret) || (not main && preview-secret) || ''`; the other target's secret is never read and a missing secret never falls back. CI, E2E and lifecycle contain no secrets. All jobs except Release are read-only on repository contents; the lock's `contents: write` is gone.
* Authorization: Migration/Deploy run only for push/dispatch on a branch ref; the job re-resolves the target (`cli.mjs resolve`) and fails on mismatch; Production uses the protected `<app>/prod` Environment (Migration and Deploy both, so approvals are asked twice).
* Fork PRs: CI/E2E only, no secrets, no migrate/deploy/lifecycle.
* Injection: env inputs are data (`parse-env.mjs`); commands come from `env:` via `bash -c "$CMD"`; no event text in scripts; deploy checkouts use `persist-credentials: false`.
* Bindings: Preview source preflight before the first remote command; **effective generated config audited** after the build for both targets.
* Artifacts: none cross jobs; the only upload is the E2E report on failure, retention 3 days.
* Residual: callers' commands are trusted code; Preview migration can still break other branches' code.

## Verified vs not verified

**Verified (mocked, except the same-repo concurrency experiment above; `.github/workflows/cf-worker-v2-ci.yml`, all green locally):** actionlint (+ shellcheck on inline scripts) and `shellcheck -S warning` on the scripts; Node unit tests (identity, event/target, migration target and settings, production audit, env parsing); a workflow contract test that *evaluates* every job `if:`, credential `env:` and the concurrency group over an event matrix (optional vs configured E2E, Migration failure/cancel blocking Deploy, D1-less deploy, fork/PR/tag/delete, production-only, Hybrid separation, Release gate, group per resource); `test-jobs.py`, which **executes the real `run:` steps** of the Migration and Deploy jobs against stub `gh`/`pnpm`/`curl` with GitHub step semantics and feeds results into the next job's `if:` (stale main run does not release, skipped publish, smoke failure, rollback with the same config, missing URL, Access, 401/403/5xx, SHA mismatch, name collision / deleted / stale branch / preflight failure / ambiguous D1 / missing creds all fail before any `wrangler d1`, secret-injection literal delivery); behaviour tests for `deploy.sh`, `migrate.sh`, `smoke.sh`, `parse-env.mjs`, probe and ledger. Mutation spot-checks (Release gate, collision guard, credential fallback, Migration gate) made the suites fail as intended.

**Not verified (needs real GitHub/Cloudflare; no Cloudflare operation was performed):** top-level `concurrency` + `queue: max` queuing for a *cross-repository* called workflow (same-repo case verified, see above); behaviour when the queue is full; Environment approval prompts and their interaction with the group; the format/exit codes of `wrangler d1 migrations list/apply` on the Wrangler each consumer pins (including non-interactive confirmation); real Native Preview/URL stability, Access-protected smoke, last-good recovery, fork PRs, force-push ordering, quota; Astro-generated config in a real build; stub `gh` fidelity (e.g. paging of the Deployments API); cross-repository D1 sharing (**unprotected**).

## Consumers

Inventory (org `firstsun-dev`, workflow files scanned, no consumer references `@main`):

| Repo / file | Reference | Impact of the refactor |
|---|---|---|
| `heaven-www` `.github/workflows/cicd.yml` | `_cf-worker-v2.yml@v2` (floating) + `tools_ref: v2` | **Breaks at cutover** (the only live v2 consumer): see checklist; no change until `@v2` moves |
| `blog` draft PR #448 (`feat/cf-worker-v2-previews`) | `@v2`, uses `preview_migrate_cmd`, `build_env_*`, `test_e2e_*`, `verify_cmd` | Pilot; rewrite per checklist (not merged) |
| `heaven-monorepo`, `innovation-apps`, `blog` (main), `anas-mcp` | `_cf-worker-template.yml` `@v1` / `@v1.5.0` | **Unaffected** (v1 untouched) |

Checklist per v2 consumer: (1) pin the candidate SHA in **both** `uses:` and `tools_ref`; (2) drop the caller-side `lint`/`build-check`/`prepare` jobs into `lint_cmd`/`typecheck_cmd`/`build_cmd` (or keep them if intentional) and drop duplicate unit/E2E; (3) rename inputs per the table (`migrate_cmd_*` no longer exist: use `d1_migrations: true`, explicit `previews.d1_databases`, and wrangler migrations; set `access_protected_targets` explicitly if Production is behind Access); (4) set `d1_migrations: true` where a D1 exists and make sure `previews.d1_databases` has an explicit DB distinct from production; (5) add `test_e2e_build_cmd` / move the test build out of `test_e2e_setup_cmd`; (6) move `verify_cmd`/`preview_smoke_cmd` to `smoke_cmd` (+ `smoke_env`, `app_url_prod`); (7) keep `on: push (all branches), pull_request, delete, workflow_dispatch`; (8) re-check Environments `<app>/prod` and `<app>/preview` (approvals apply to Migration and Deploy); (9) the caller's job `permissions` must allow `deployments: write` and `pull-requests: write` (and `contents: read`); do **not** reuse a `cfv2-*` or `${{ github.workflow }}` group at the calling job; (10) pass the new `RELEASE_TOKEN` only if `release_cmd` is used; (11) delete the caller `cleanup` job and the `if: github.event_name != 'delete'` gate on the pipeline call — `delete` is handled by `lifecycle` through the same call.

`heaven-www` specifically: remove `preview_smoke_cmd` → `smoke_cmd`; no D1 so `d1_migrations` stays false; keep `preview_binding_allow`/Preview credentials as-is; the separate `cleanup` caller job and the `!= 'delete'` gate on `pipeline` are removed; its `lint`/`build-check` jobs may move into `lint_cmd`/`typecheck_cmd`/`build_cmd`.

**Recommended order:** (1) merge this implementation PR (floating `@v2` untouched); (2) pilot **Blog** (shared `firstsun-blog-db-preview`, `blog-preview` host, Astro adapter, voice R2) pinned to the candidate SHA; (3) pilot **Heaven ID** only after the `heaven-id-db-dev` sharing decision and OAuth/Cron checks; (4) migrate `heaven-www`; (5) publish an immutable `v2.x.y` and move floating `@v2` only after every `@v2` consumer is ready; (6) monorepo apps and innovation-apps.
