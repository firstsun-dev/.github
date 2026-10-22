# CF Worker v2 — in-place refactor: interface, concurrency design, migration guide

`.github/workflows/_cf-worker-v2.yml` was refactored **in place** (breaking, no `v3`). Spec: `openspec/changes/refactor-cloudflare-worker-cicd-v2` (PR #57). Shipped v2.x behavior stays documented by `docs/cf-worker-v2.md` / PR #56 as the historical record; **the interface below replaces it**. The floating `@v2` tag has not moved and no consumer has been changed.

## Job DAG

```
Detect Changes (monorepo only — the CALLER runs _detect-changes.yml and gates its calls)
        │
 ┌──────┬───────────┬──────┬─────────────┬──────────────────┬─────┐
 Lint  Typecheck  Unit Tests  Integration Tests  Build Validation  E2E     six independent jobs, no `needs` between them
 └──────┴───────────┴──────┴─────────────┴──────────────────┴─────┘
        │
    Migration              dedicated job, only when d1_migrations: true; needs all six
        │
     Deploy                ONE job, target = prod (main) | preview (any other eligible branch)
        │
    Release                optional (release_cmd), main only

 outside the core DAG:  lifecycle (same-repo PR comment / branch-delete ledger retire)
                        persistent-dev (Hybrid, protected workflow_dispatch on the approved ref)
```

**Revision (issue #61):** the former single `CI` job (Lint → Typecheck → Unit → Integration → Build as steps) is split into independent jobs for failure isolation, per-check duration/conclusion and single-job reruns. Same v2 name and public inputs; no v3.

| Job (check name) | Runs when | Command input | Notes |
|---|---|---|---|
| `Lint` | `lint_cmd != ''` | `lint_cmd` | target build env, then `ci_env` |
| `Typecheck` | `typecheck_cmd != ''` | `typecheck_cmd` | |
| `Unit Tests` | `test_unit_cmd != ''` | `test_unit_cmd` | Mailpit if `use_mailpit` |
| `Integration Tests` | `test_integration_cmd != ''` | `test_integration_cmd` | Mailpit if `use_mailpit` |
| `Build Validation` | always (`build_cmd` is required) | `build_cmd` | validation only, nothing uploaded |
| `E2E` | `test_e2e_cmd != ''` | setup / build / cmd, variants matrix | unchanged |

* Every validation job: own checkout, `setup` action (existing dependency cache), env import, logs, a short Step Summary, no Cloudflare credentials, no shared workspace, no artifacts (only the E2E failure report, 3 days). Browsers are installed only by a consumer's E2E commands; Mailpit starts only in Unit, Integration and E2E.
* **Skip rules.** An optional job is `skipped` on purpose only when its command input is empty. Migration and Deploy accept `skipped` for an optional job *only* in that case; a configured job that ends `skipped`, `failure` or `cancelled` blocks them, and `Build Validation` must be `success`. No Test Gate job and no `continue-on-error`.
* **Migration**: after all six validation jobs. A failure/cancel blocks Deploy. **Deploy** needs the same six plus Migration (`skipped` accepted only when `d1_migrations` is false).
* **Deploy**: checks the target (fail-closed re-check of the job `if`), stale-SHA guard, exact-SHA checkout, setup, target build env, independent build, source **and effective (adapter-generated)** Wrangler binding audit, publish (`wrangler preview` or `versions upload`+`deploy @100`), real URL, smoke (Access-aware), Deployment status, recovery.
* Same-repo PRs do not re-run validation (the push run of the same SHA covers them); fork PRs run it credential-free. No job other than `lifecycle` ever runs on a PR with write capabilities.
* **Runner overhead.** One run now needs up to 5 validation jobs + the E2E matrix concurrently (e.g. 5 + 4 = 9 runners for Blog) instead of 1 + 4, and each repeats checkout and dependency install (cache hits make this cheap, never share a mutable workspace). Peak memory per runner is unchanged; peak *runner count* roughly doubles on a self-hosted pool. Mitigations: leave unused command inputs empty (the job is skipped, not a no-op runner), keep the pool large enough for the largest consumer's fan-out, and do not serialize checks. `queue: max` still serializes whole runs per resource group, so fan-out only multiplies within a run.
* **Consumer guidance.** No input changed: callers that already pass `lint_cmd`/`typecheck_cmd`/`test_unit_cmd`/`test_integration_cmd` get distinct jobs automatically after pinning a SHA containing this change. A caller without unit/integration tests (e.g. heaven-www) leaves them empty and keeps passing. A caller's content-only fast path may keep passing an empty unit/integration/E2E command; Lint/Typecheck/Build stay required.

## Interface (inputs)

| Group | Inputs |
|---|---|
| Identity | `tools_ref` (must equal the ref the caller pins), `app_name`, `app_path`, `app_version`, `runner`, `wrangler_config` |
| Strategy | `deployment_strategy` (`production-preview` \| `hybrid` \| `production-only`), `preview_on_branch_push`, `shared_dev_on_branch_push` (default `false`), `preview_worker_name` |
| Validation | `lint_cmd`, `typecheck_cmd`, `test_unit_cmd`, `test_integration_cmd`, `build_cmd` (required), `ci_env`, `use_mailpit` |
| E2E | `test_e2e_cmd`, `test_e2e_setup_cmd`, `test_e2e_build_cmd`, `test_e2e_env`, `test_e2e_variants` |
| Deploy | `build_env_prod`, `build_env_preview`, `smoke_cmd`, `smoke_env`, `sha_path`, `app_url_prod`, `access_protected_targets`, `production_rollback_policy`, `preview_binding_allow`, `preview_deny_resources` |
| Migration | `d1_migrations`, `d1_binding`, `d1_lock_scope` (no command override) |
| Release | `release_cmd` (+ secret `RELEASE_TOKEN`) |
| Hybrid | `persistent_dev_ref`, `dev_deploy_cmd`, `dev_smoke_cmd`, `dispatch_target` |
| Shared Dev | `shared_dev_on_branch_push`, `app_url_dev`, `dev_deploy_cmd`, `dev_smoke_cmd`, `build_env_dev`, `dev_build_cmd`, `dev_wrangler_env` (default `dev`), `dev_worker_name`, `dev_binding_allow`, `dev_deny_resources`; reuses `d1_migrations`, `d1_binding`, `sha_path`, `smoke_env`, `access_protected_targets`, `preview_deny_resources` |

Outputs: `deploy_url`, `deploy_target`, `version_id` (prod only), `dev_url` (Shared Dev). The internal `deployed_and_verified` Deploy output is what gates Release. Secrets: `CLOUDFLARE_API_TOKEN/ACCOUNT_ID` (prod), `CLOUDFLARE_PREVIEW_API_TOKEN/ACCOUNT_ID`, `CLOUDFLARE_DEV_*`, `PREVIEW_SECRETS`, `SMOKE_USERNAME/PASSWORD`, `RELEASE_TOKEN`.

`*_env` inputs are `KEY=VALUE` lines imported by `actions/cf-preview/parse-env.mjs` **from an environment variable** (never interpolated into a script). It rejects malformed lines and refuses `CLOUDFLARE_*`, `GITHUB_*`, `RUNNER_*`, `PATH`, `NODE_OPTIONS`, `LD_*`, `BASH_ENV`, `GIT_*`, `SMOKE_*` credentials, etc.; all-or-nothing. Application variable names remain consumer-owned. Command inputs are passed through `env:` and run with `bash -c "$CMD"`.

### Removed (old v2 → refactored)

| Removed | Replacement |
|---|---|
| jobs `plan`, `test-unit`, `test-integration`, `test-e2e`, `test` (gate), `preview`, `production`, `production-verify`, `production-recovery`, `pr-link`, `cleanup` | `lint`, `typecheck`, `unit`, `integration`, `build`, `e2e`, `migrate`, `deploy`, `release`, `lifecycle` |
| `preview_build_cmd` | one `build_cmd`; targets differ via `build_env_prod` / `build_env_preview` |
| `preview_provision_cmd`, `preview_cleanup_cmd` (already ignored) | removed: shared durable Preview resources with explicit `previews` bindings |
| `preview_migrate_cmd`, `migrate_cmd_prod` | `d1_migrations: true` in the dedicated Migration job. **No command override**: a custom command could skip the target-D1 check, so the built-in `wrangler d1 migrations` path is the only one |
| `preview_smoke_cmd`, `preview_smoke_env`, `verify_cmd` | `smoke_cmd`, `smoke_env` (both targets; `TARGET_URL`, `EXPECTED_SHA`, `DEPLOY_TARGET`, `TEST_ENV`) |
| `preview_sha_path` | `sha_path` |
| `test_e2e_setup_cmd` doing the build | `test_e2e_setup_cmd` + `test_e2e_build_cmd` |
| output `preview_url` | `deploy_url` (+ `deploy_target`) |
| `actions/cf-preview/publish.sh` | `deploy.sh` (both targets) |

## Non-main channels: Feature Preview and Shared Dev

Two independent switches decide what a non-main push deploys. Neither requires the other; main always follows the Production path.

| `preview_on_branch_push` | `shared_dev_on_branch_push` | Non-main push |
|---|---|---|
| true | true | Native Preview **and** Shared Dev |
| true | false | Native Preview only (default for existing callers) |
| false | true | Shared Dev only |
| false | false | validation only |

* **Feature Native Preview** — unchanged: `Migration` → `Deploy`, one stable URL per branch.
* **Shared Dev** — job `dev-deploy` ("Shared Dev"). Every branch targets the **same** Dev Worker and `app_url_dev`; the last verified deployment wins and there is no per-branch Dev. Use `shared_dev_on_branch_push` for this; the manual `persistent-dev` dispatch (Hybrid, approved ref, Cron/Queue checks) is a separate path that still runs `dev_deploy_cmd` alone.
* `config-guard` fails the run when `deployment_strategy: production-only` is combined with `shared_dev_on_branch_push: true`. `production-only` still silently ignores `preview_on_branch_push` (its default is `true`; failing on it would break existing callers) — open question for review.

### Shared Dev job order

`stale-SHA guard → Dev build (dev_build_cmd, build_env_dev, CLOUDFLARE_ENV=dev_wrangler_env) → effective-config audit (dev_worker_name, no Production resource) → stale-SHA guard → Dev D1 migration → dev_deploy_cmd → smoke(app_url_dev, dev_smoke_cmd)`.

* A failed build, audit or stale run never changes the running Dev Worker or its D1 (Preview migrates before it builds; Dev builds first because Dev has no separate Migration job).
* Migration reuses `migrate.sh` with `TARGET=dev`: D1 = `env.<dev_wrangler_env>.d1_databases`, never top-level (Production); no fallback. The Preview D1 and the Dev D1 **may** be the same physical database (Blog); Production's may not. `migrations apply` is pending-only, and the job `needs` Migration/Deploy purely for ordering (their results are ignored), plus the shared concurrency group, so two channels never migrate one D1 at the same time.
* `dev_worker_name` is **required**, and the audit runs on every Shared Dev run (automatic and manual split-contract): the adapter-generated effective config (`.wrangler/deploy/config.json`), or if none was generated the source `env.<dev_wrangler_env>` block, must have `name == dev_worker_name` and pass the resource policy below.
* With `d1_migrations`, `dev_build_cmd` is **required**, so the Dev build always succeeds before the Dev D1 is migrated.
* **Shared-D1 guard:** if the Preview and Dev D1 are the same physical database and the Preview Migration failed or was cancelled, Shared Dev stops before building or migrating (D1 state unknown). With distinct D1s or Preview off, Dev still deploys.
* Credentials: `CLOUDFLARE_DEV_API_TOKEN` / `CLOUDFLARE_DEV_ACCOUNT_ID` only, in the `<app>/dev` Environment. No Production/Preview token or `PREVIEW_SECRETS` is referenced; a missing Dev token fails closed.
* Smoke fails the job. Recovery: the job records a ledger entry `<app>/dev/shared`; after a failed smoke it redeploys the last verified SHA (from any branch) with the same build, Worker-identity/resource audit and full smoke (probe, `sha_path` for that SHA, `dev_smoke_cmd`), or states **manual recovery required** when none exists. The run stays red either way.
* Concurrency: workflow-level group (above) queues all branches; the job also uses `cfv2-dev-<repo>-<app>`, the same group as `persistent-dev`, so automatic and manual Dev deployments never overlap. This differs from the workflow-level `cfv2-<app>-…` groups, so there is no caller/callee deadlock.
* Retention: branch deletion and disabling a switch deploy or delete nothing; no job deletes a Worker.

Consumer sketch (Blog): `preview_on_branch_push: true`, `shared_dev_on_branch_push: true`, `app_url_dev: https://blog-preview.firstsun.org`, `dev_wrangler_env: dev`, `dev_worker_name: blog-preview`, `d1_migrations: true`, a required `dev_build_cmd` that builds with `CLOUDFLARE_ENV=dev` and a `dev_deploy_cmd` that only runs `wrangler deploy --config <effective config>`. Heaven ID: `preview_on_branch_push: false`, `shared_dev_on_branch_push: true`, `app_url_dev: https://id-dev.heavenfortress.com`.

### Shared Dev resource policy (identity-aware, fail-closed)

Production, Feature Preview and Shared Dev may share a resource **only when the sharing is explicit, exact and verified**. The Dev audit (`actions/cf-preview/dev-policy.mjs`) compares the Dev Worker's effective config with the Production (source top-level) config. It uses **only** `dev_binding_allow`, `dev_deny_resources` and the Production identities it discovers; `preview_binding_allow` / `preview_deny_resources` keep governing Native Preview and are never consulted for Dev (so Preview can block the Production bucket while Dev deliberately shares it).

Precedence per binding: **1.** `dev_deny_resources` (hard deny, always fails, even if allowlisted) → **2.** allow-entry identity mismatch fails → **3.** a Production collision fails unless an exact, supported allow entry matches. An allow entry never skips another check (Worker identity, queues, services, routes, ...).

`dev_binding_allow` syntax, whitespace/newline separated, `kind:BINDING=exact-identity`:

```yaml
dev_binding_allow: |
  r2_buckets:SHINYI_MUYU_VOICE_CACHE=firstsun-blog-assets
  kv_namespaces:CACHE=<namespace id>
  services:AUTH=heaven-id-dev          # or worker#environment
```

| Kind | Identity compared | Production collision | Allowlist |
|---|---|---|---|
| `r2_buckets` | `bucket_name` | fail | exact `=bucket_name` |
| `kv_namespaces` | `id` | fail | exact `=id` |
| `vectorize` | `index_name` | fail | exact `=index_name` |
| `analytics_engine_datasets` | `dataset` | fail | exact `=dataset` |
| `d1_databases` | `database_id` **and** `database_name` | fail, **never approvable** | rejected |
| `hyperdrive` (`id`), `secrets_store_secrets` (`store_id/secret_name`, full), `pipelines` (`pipeline`), `ratelimits` (`namespace_id`) | exact identity | fail | **not supported** (collision detection only) |
| `queues` producers / consumers / `dead_letter_queue` | queue name | fail, no allowlist | **not supported** |
| `services` | `service` (+ `#environment`) | Production Worker/service, or the Production Worker itself: fail, not approvable | default **deny**: each binding needs `services:BINDING=target` and must not be a Production target |
| `routes` / `route` | pattern / host | a Production route or the `app_url_prod` host: fail | none |
| `durable_objects` with another Worker's `script_name`; `workflows`, `dispatch_namespaces`, `mtls_certificates`, `containers`, `tail_consumers`, `unsafe` | — | **unsupported**: fail closed (cannot be verified generically) | none |

Rules: an unknown kind, a malformed entry, a duplicate entry, an entry that matches no Dev binding, a binding declared twice, a binding with a missing or incomplete identity (e.g. a secret without `store_id`) and an identity mismatch (same binding, different bucket/id) all fail. A matching local binding **name** is never evidence that a remote service or resource is safe.

* **Why D1 is always isolated:** a Production D1 holds Production data and is the only target of Production migrations; Preview and Dev may share one approved non-Production D1 (migration targeting stays explicit and fail-closed), but there is no bypass.
* **Why Queues and Service Bindings are stricter:** a consumer or dead-letter queue *consumes or writes* Production work (Wrangler creates a missing dead-letter queue automatically), and a Service Binding invokes the other Worker's code and data. A shared R2 bucket with different object-key prefixes provides *application-level* isolation only; it does **not** make the bucket read-only for Dev, and the Dev Worker's token can still touch Production objects. Queue sharing, if ever needed, requires a separate high-risk policy with exact resource and direction; none exists.
* Schema reference: the Wrangler configuration docs (https://developers.cloudflare.com/workers/wrangler/configuration/); kinds not listed above are not audited and any binding kind we cannot verify is rejected rather than assumed safe.

### Manual vs automatic Dev contract

Split contract (new consumers; opt in by setting `dev_build_cmd`): `dev_build_cmd` builds only, `dev_deploy_cmd` publishes only, `dev_smoke_cmd` verifies only. Automatic Shared Dev and the manual `persistent-dev` dispatch then both run build → config audit → Dev D1 migration (`d1_migrations`) → publish → smoke; the manual run also requires `dev_worker_name` and `app_url_dev`, keeps the approved-ref gate and protected `<app>/dev` Environment, and cannot skip the build. A manual run is recorded in the last-good ledger **only after its smoke passes** (failures are recorded as failed); it has no automatic recovery.

Legacy contract (`dev_build_cmd` empty, existing hybrid callers): the manual dispatch runs `dev_deploy_cmd` (which must build itself) and the optional `dev_smoke_cmd` exactly as before and is **never** written to the ledger, so automatic recovery can never mistake it for a known-good release. Migration plan: add `dev_build_cmd`, `dev_worker_name`, `app_url_dev` (and an audit-clean Dev config); no caller is forced to.

### Recovery limits

After a failed Shared Dev smoke the job redeploys the last verified SHA (from the Dev ledger, any branch). It rebinds the commit identity: occurrences of the failed run's SHA (full and 7-character) in `build_env_dev` and `app_version` are replaced with the last-good SHA; `app_version` values not derived from the commit SHA are kept and flagged with a warning. It then repeats build, Worker-identity/resource audit, publish and the full smoke. Only with `sha_path` set is the restored **commit identity** verified; without it the summary says "commit identity NOT verified". It never rolls back D1 (a schema newer than the restored code is possible), a caller whose build parameters embed the commit in ways other than the exact SHA cannot be rebound, and a failed recovery is reported as such; the run is always red. Manual runs have no recovery.

### Required follow-up in Blog #448 (after this PR is approved)

Not changed by this PR. Replace `dev_binding_allow: r2_buckets:SHINYI_MUYU_VOICE_CACHE` with `r2_buckets:SHINYI_MUYU_VOICE_CACHE=firstsun-blog-assets`; set `dev_deny_resources` if Dev must never touch other Production resources (it must **not** list `firstsun-blog-assets`); keep `preview_deny_resources` for Native Preview unchanged; set `sha_path` so recovery verifies the commit; keep the Voice prefixes `cache/shinyi-muyu/tts/prod/` and `.../preview/`; pin to the merged SHA/tag; and provision the `CLOUDFLARE_DEV_*` secrets before any pilot.

## Post-incident hardening (Blog run 39708495349)

The first live Blog run published a Native Preview with **no URL**, then ran smoke against nothing, then "recovered" a different Worker's deployment, and left a Shared Dev deployment unverified (Dev was not noindexed). Fixes in this contract:

* **Empty Preview URL (root cause):** `deploy.sh` did `echo "url=$(node cli.mjs extract-url ... | sed ...)"`. The pipeline status is `sed`'s and `echo`'s is always 0, and the extractor's `::error::` line was filtered out by `sed`, so a failed extraction printed `url=` and exited 0. Now the extractor runs on its own with its status preserved; the URL must be a non-empty `https://` URL; a missing/malformed payload, a missing or **empty `preview.urls`**, a non-string or non-https entry all fail the Publish step with a sanitized, actionable message (no payload dump). `preview_mutated=true` is printed right after `wrangler preview` succeeds so the workflow can say the Preview *may* have changed and is unverified; no recovery runs (there is nothing to verify against).
* **Host readiness:** before the build, `deploy.sh` calls the read-only Workers subdomain API (`GET /accounts/{id}/workers/scripts/{worker}/subdomain`) for the host Worker and refuses when `previews_enabled` is `false`, explaining how to enable Preview URLs. It never changes the Worker. An unreadable answer (token scope, 404, network) only warns and the strict post-publish validation applies. Custom-domain Preview hosting cannot be derived from this flag: set `preview_host_check: false` there.
* **Ledger identity:** Preview ledger records now carry `repo` and `host_worker`. `gh.sh last-good-preview` only returns a deployment whose repository, application, branch ref and **host Worker** match, whose latest status is `success` and whose recorded URL is a valid https URL; legacy records without `host_worker` are never eligible (a Preview hosted by `blog-preview` is not a recovery source for one hosted by `blog`). History is kept.
* **Preview recovery:** rebuilds with the Preview build environment restored from `build_env_preview` (failed SHA rebound to the last-good SHA, same for `app_version`), checks the host Worker, requires the redeployed URL to equal the recorded last-good URL, runs the configured `smoke_cmd` (with `sha_path` against the last-good SHA) and records the outcome: content-level verified, Access-liveness only (reported as **not** verified), or failed. D1 is never rolled back; the run is always red.
* **Smoke env isolation:** `smoke_env` is staged in `SMOKE_ENV_FILE` and loaded only inside `smoke.sh`; it is never merged into the job environment, so Production-oriented smoke values can no longer overwrite the target build environment (e.g. `SITE_NOINDEX`) for a later rebuild.

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
| branch, no D1, `shared_dev_on_branch_push: true` | `cfv2-<app>-shared-dev` — **one group for the one Dev Worker**, all branches queue |
| branch, no D1, Shared Dev off | `cfv2-<app>-preview-<branch>` |
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

**Shared Dev (mocked/static only):** the four-switch matrix, main/PR/delete/tag/persistent-dev exclusion, production-only rejection, validation and skip semantics, concurrency groups, Dev-only credential selection, step order, stale handling, Dev migration target (`env.<name>`, no Production fallback, Preview==Dev D1 accepted), effective-config audit and recovery step wiring are covered by `test-workflow-contract.py`, `lib.test.mjs` and `test-migrate.sh`. **No live Shared Dev or Native Preview pilot has run**; live behaviour (Dev environment protection, the `queue: max` group across Preview and Dev, real recovery) is unverified.

**Not verified (needs real GitHub/Cloudflare; no Cloudflare operation was performed):** top-level `concurrency` + `queue: max` queuing for a *cross-repository* called workflow (same-repo case verified, see above); behaviour when the queue is full; Environment approval prompts and their interaction with the group; the format/exit codes of `wrangler d1 migrations list/apply` on the Wrangler each consumer pins (including non-interactive confirmation); real Native Preview/URL stability, Access-protected smoke, last-good recovery, fork PRs, force-push ordering, quota; Astro-generated config in a real build; stub `gh` fidelity (e.g. paging of the Deployments API); cross-repository D1 sharing (**unprotected**).

## Consumers

Inventory (org `firstsun-dev`, workflow files scanned, no consumer references `@main`):

| Repo / file | Reference | Impact of the refactor |
|---|---|---|
| `heaven-www` `.github/workflows/cicd.yml` | `_cf-worker-v2.yml@v2` (floating) + `tools_ref: v2` | **Breaks at cutover** (the only live v2 consumer): see checklist; no change until `@v2` moves |
| `blog` draft PR #448 (`feat/cf-worker-v2-previews`) | `@v2`, uses `preview_migrate_cmd`, `build_env_*`, `test_e2e_*`, `verify_cmd` | Pilot; rewrite per checklist (not merged) |
| `heaven-monorepo`, `innovation-apps`, `blog` (main), `anas-mcp` | `_cf-worker-template.yml` `@v1` / `@v1.5.0` | **Unaffected** (v1 untouched) |

Checklist per v2 consumer: (1) pin the candidate SHA in **both** `uses:` and `tools_ref`; (2) drop the caller-side `lint`/`build-check`/`prepare` jobs into `lint_cmd`/`typecheck_cmd`/`build_cmd` (or keep them if intentional) and drop duplicate unit/E2E; (3) rename inputs per the table (`migrate_cmd_*` no longer exist: use `d1_migrations: true`, explicit `previews.d1_databases`, and wrangler migrations; set `access_protected_targets` explicitly if Production is behind Access); (4) set `d1_migrations: true` where a D1 exists and make sure `previews.d1_databases` has an explicit DB distinct from production; (5) add `test_e2e_build_cmd` / move the test build out of `test_e2e_setup_cmd`; (6) move `verify_cmd`/`preview_smoke_cmd` to `smoke_cmd` (+ `smoke_env`, `app_url_prod`); (7) keep `on: push (all branches), pull_request, delete, workflow_dispatch`; (8) re-check Environments `<app>/prod` and `<app>/preview` (approvals apply to Migration and Deploy); (9) the caller's job `permissions` must allow `deployments: write` and `pull-requests: write` (and `contents: read`) — **and `contents: write`**, because the shared `release` job declares it and a called workflow cannot exceed its caller's permissions (a caller granting less fails at startup with `startup_failure`, observed on heaven-www); do **not** reuse a `cfv2-*` or `${{ github.workflow }}` group at the calling job; (10) pass the new `RELEASE_TOKEN` only if `release_cmd` is used; (11) delete the caller `cleanup` job and the `if: github.event_name != 'delete'` gate on the pipeline call — `delete` is handled by `lifecycle` through the same call.

`heaven-www` specifically: remove `preview_smoke_cmd` → `smoke_cmd`; no D1 so `d1_migrations` stays false; keep `preview_binding_allow`/Preview credentials as-is; the separate `cleanup` caller job and the `!= 'delete'` gate on `pipeline` are removed; its `lint`/`build-check` jobs may move into `lint_cmd`/`typecheck_cmd`/`build_cmd`.

**Recommended order:** (1) merge this implementation PR (floating `@v2` untouched); (2) pilot **Blog** (shared `firstsun-blog-db-preview`, `blog-preview` host, Astro adapter, voice R2) pinned to the candidate SHA; (3) pilot **Heaven ID** only after the `heaven-id-db-dev` sharing decision and OAuth/Cron checks; (4) migrate `heaven-www`; (5) publish an immutable `v2.x.y` and move floating `@v2` only after every `@v2` consumer is ready; (6) monorepo apps and innovation-apps.
