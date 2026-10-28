# CF Worker v2 — in-place refactor: interface, concurrency design, migration guide

`.github/workflows/_cf-worker-v2.yml` was refactored **in place** (breaking, no `v3`). Spec: `openspec/changes/refactor-cloudflare-worker-cicd-v2` (PR #57). Shipped v2.x behavior stays documented by `docs/cf-worker-v2.md` / PR #56 as the historical record; **the interface below replaces it**. The floating `@v2` tag has not moved and no consumer has been changed.

## Job DAG and architecture (thin workflow, one delivery engine)

```
Detect Changes (monorepo only — the CALLER runs _detect-changes.yml and gates its calls)
        │
 ┌──────┬───────────┬──────┬─────────────┬──────────────────┬─────┐
 Lint  Typecheck  Unit Tests  Integration Tests  Build Validation  E2E     six independent jobs, no `needs` between them
 └──────┴───────────┴──────┴─────────────┴──────────────────┴─────┘        (actions/cf-ci-check; E2E has its own body)
        │                                           │
     Deploy                                   Shared Dev  (optional; `needs: Deploy` for ORDERING only)
 target = prod | preview                         target = dev
        │                                           │
        └──────────── actions/cf-deploy ────────────┘      ONE delivery engine (engine.sh) for every target:
   init → stale → setup → prepare → BUILD + audit → exact D1 target check → stale → MIGRATE (step) → stale
        → PUBLISH the audited artifact → smoke → mark verified | recover
        │
    Release                optional (release_cmd), main only, after a VERIFIED Production deploy

 outside the core DAG:  lifecycle (same-repo PR comment / branch-delete ledger retire)
                        config-guard (production-only + Shared Dev is rejected)
                        persistent-dev (Hybrid, protected workflow_dispatch on the approved ref; the same engine, mode=manual)
```

There is **no Prepare, Plan, Test-Gate or Migration job**. Migration is one explicit step of the engine, executed only after the deployable was built and the effective Wrangler config was audited, and before the audited artifact is published.

### Responsibilities (who owns what)

| Layer | Owns | Files |
|---|---|---|
| Workflow YAML (`_cf-worker-v2.yml`, ~800 lines of which ~300 are the public interface) | event triggers and target selection, validation jobs and gating, GitHub Environments, **which Cloudflare credentials exist in a job**, permissions, concurrency, conditional channels, Release, lifecycle | `.github/workflows/_cf-worker-v2.yml` |
| Composite actions | sequencing only: one step per engine phase, failure/recovery wiring | `actions/cf-deploy/action.yml` (delivery engine), `actions/cf-ci-check/action.yml` (one validation job body) |
| Scripts | everything with logic: Wrangler execution (`build.sh`, `publish.sh`, `migrate.sh`), config parsing / audits / migration-target resolution / D1 consistency / artifact digest (`cli.mjs`, `lib.mjs`, `dev-policy.mjs`), env import (`parse-env.mjs`, `envfile.sh`), GitHub Deployment ledger (`gh.sh`), stale-SHA detection (`guard.sh`), smoke (`smoke.sh`, `probe.sh`), phase dispatcher (`engine.sh`) | `actions/cf-preview/` |

`engine.sh` reads every workflow input from one JSON blob (`toJSON(inputs)`, passed through `env`, never interpolated into a script) and writes validated state under `$RUNNER_TEMP/cfv2` (mode 0700). Production, Native Preview and Shared Dev differ only in the explicit, validated per-target table below; there is one Build / Audit / Migrate / Publish / Smoke / Recovery implementation.

| Concern | prod | preview | dev |
|---|---|---|---|
| Trigger | main push / dispatch | non-main push (`preview_on_branch_push`) | non-main push (`shared_dev_on_branch_push`); manual: protected dispatch |
| Credentials in the job env | `CLOUDFLARE_API_TOKEN/ACCOUNT_ID` | `CLOUDFLARE_PREVIEW_*` (+ `PREVIEW_SECRETS`) | `CLOUDFLARE_API_TOKEN/ACCOUNT_ID` (same account token as Production) |
| Environment | `<app>/prod` | `<app>/preview` | `<app>/dev` |
| Build env / vars | `build_env_prod` | `build_env_preview` | `build_env_dev`, `dev_build_cmd`, `CLOUDFLARE_ENV=dev_wrangler_env` |
| D1 (same project SQL) | top-level `d1_databases` | `previews.d1_databases` | `env.<dev_wrangler_env>.d1_databases` |
| Config audit on the published config | Preview-resource ban | Preview binding / auth isolation | Dev Worker identity + resource policy |
| Publish | `versions upload` + `versions deploy @100` | `wrangler preview --name <slug>` | `wrangler deploy --config <audited> --env <dev env>` or the caller's `dev_deploy_cmd` |
| Smoke URL | `app_url_prod` | discovered Preview URL | `app_url_dev` |
| Recovery | policy-driven rollback to the previous version | redeploy last-good of THIS branch + host Worker | redeploy last verified Dev SHA (any branch) |

### Local composite actions in a cross-repository reusable workflow

A reusable workflow called from another repository resolves `uses: ./...` against the **caller's** workspace, and `uses:` cannot take an expression, so `uses: firstsun-dev/.github/actions/x@${{ inputs.tools_ref }}` is impossible and a pinned `@v2` would not contain the new actions until the tag moves. Each job therefore (1) checks out the app, (2) checks out `firstsun-dev/.github` at `inputs.tools_ref` into `.cf-tools` (sparse: only the action it uses plus `actions/cf-preview`), and (3) references the action by the **static** path `./.cf-tools/actions/cf-deploy` / `cf-ci-check`. `tools_ref` must equal the ref the caller pins the workflow at (required input, unchanged). `test-workflow-contract.py` enforces: static `./.cf-tools/actions/<name>` paths only, the tooling checkout precedes every such reference and includes the action directory, the action exists in this repository. Only the pre-existing `setup` / `mailpit` `@v1` shared actions are referenced remotely.

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
* **Deploy** needs the six validation jobs (a CONFIGURED one that is skipped, failed or cancelled blocks it). There is no Migration job: when `d1_migrations` is true the engine's **Migration step** runs inside Deploy (and Shared Dev) after the build, the effective-config audit, the exact-D1-target check and a stale check, and before a second stale check and the publish. A failed migration fails the job and nothing is published.
* **Deploy** (engine): target re-resolved from the event and bound once, credentials present (fail closed), stale-SHA guard, exact-SHA checkout, setup, target build env, **build**, source and **effective (adapter-generated)** Wrangler config audit, exact D1 target check, stale check, **migrate**, stale check, publish **of the audited config only**, real URL, smoke (Access-aware), Deployment status, recovery.
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
| Deploy | `build_env_prod`, `build_env_preview`, `smoke_cmd`, `smoke_env`, `probe_path`, `probe_expect_status`, `sha_path`, `app_url_prod`, `access_protected_targets`, `production_rollback_policy`, `preview_binding_allow`, `preview_deny_resources` |
| Migration | `d1_migrations`, `d1_binding`, `d1_lock_scope` (names the shared-D1 concurrency group; no command override) |
| Release | `release_cmd` (+ secret `RELEASE_TOKEN`) |
| Hybrid | `persistent_dev_ref`, `dev_deploy_cmd`, `dev_smoke_cmd`, `dispatch_target` |
| Shared Dev | `shared_dev_on_branch_push`, `app_url_dev`, `dev_deploy_cmd`, `dev_smoke_cmd`, `build_env_dev`, `dev_build_cmd`, `dev_wrangler_env` (default `dev`), `dev_worker_name`, `dev_binding_allow`, `dev_deny_resources`; reuses `d1_migrations`, `d1_binding`, `sha_path`, `smoke_env`, `access_protected_targets`, `preview_deny_resources` |

Outputs: `deploy_url`, `deploy_target`, `version_id` (prod only), `dev_url` (Shared Dev). The internal `deployed_and_verified` Deploy output is what gates Release. Secrets: `CLOUDFLARE_API_TOKEN/ACCOUNT_ID` (prod), `CLOUDFLARE_PREVIEW_API_TOKEN/ACCOUNT_ID`, `PREVIEW_SECRETS`, `SMOKE_USERNAME/PASSWORD`, `RELEASE_TOKEN`.

`*_env` inputs are `KEY=VALUE` lines imported by `actions/cf-preview/parse-env.mjs` **from an environment variable** (never interpolated into a script). It rejects malformed lines and refuses `CLOUDFLARE_*`, `GITHUB_*`, `RUNNER_*`, `PATH`, `NODE_OPTIONS`, `LD_*`, `BASH_ENV`, `GIT_*`, `SMOKE_*` credentials, etc.; all-or-nothing. Application variable names remain consumer-owned. Command inputs are passed through `env:` and run with `bash -c "$CMD"`.

### Removed (old v2 → refactored)

| Removed | Replacement |
|---|---|
| jobs `plan`, `test-unit`, `test-integration`, `test-e2e`, `test` (gate), `preview`, `production`, `production-verify`, `production-recovery`, `pr-link`, `cleanup`, and (PR #64 refactor) the standalone `migrate` job | `lint`, `typecheck`, `unit`, `integration`, `build`, `e2e`, `deploy` (incl. the Migration step), `dev-deploy`, `release`, `lifecycle` |
| `preview_build_cmd` | one `build_cmd`; targets differ via `build_env_prod` / `build_env_preview` |
| `preview_provision_cmd`, `preview_cleanup_cmd` (already ignored) | removed: shared durable Preview resources with explicit `previews` bindings |
| `preview_migrate_cmd`, `migrate_cmd_prod` | `d1_migrations: true` (the engine's Migration step). **No command override**: a custom command could skip the target-D1 check, so the built-in `wrangler d1 migrations` path is the only one |
| `preview_smoke_cmd`, `preview_smoke_env`, `verify_cmd` | `smoke_cmd`, `smoke_env` (both targets; `TARGET_URL`, `EXPECTED_SHA`, `DEPLOY_TARGET`, `TEST_ENV`) |
| `preview_sha_path` | `sha_path` |
| `test_e2e_setup_cmd` doing the build | `test_e2e_setup_cmd` + `test_e2e_build_cmd` |
| output `preview_url` | `deploy_url` (+ `deploy_target`) |
| `actions/cf-preview/deploy.sh` (this PR's earlier revision) | `build.sh` (build + audit) and `publish.sh` (publish only), sequenced by `engine.sh` via `actions/cf-deploy` — one implementation for prod, preview and dev |

## Non-main channels: Feature Preview and Shared Dev

Two independent switches decide what a non-main push deploys. Neither requires the other; main always follows the Production path.

| `preview_on_branch_push` | `shared_dev_on_branch_push` | Non-main push |
|---|---|---|
| true | true | Native Preview **and** Shared Dev |
| true | false | Native Preview only (default for existing callers) |
| false | true | Shared Dev only |
| false | false | validation only |

* **Feature Native Preview** — the `Deploy` job with target `preview`: one stable URL per branch.
* **Shared Dev** — job `dev-deploy` ("Shared Dev"). Every branch targets the **same** Dev Worker and `app_url_dev`; the last verified deployment wins and there is no per-branch Dev. Use `shared_dev_on_branch_push` for this; the manual `persistent-dev` dispatch (Hybrid, approved ref, Cron/Queue checks) is a separate trigger of the **same engine** (`mode: manual`, no automatic recovery) when `dev_build_cmd` is set; without it the legacy two-command contract runs `dev_deploy_cmd` alone.
* `config-guard` fails the run when `deployment_strategy: production-only` is combined with `shared_dev_on_branch_push: true`. `production-only` still silently ignores `preview_on_branch_push` (its default is `true`; failing on it would break existing callers) — open question for review.

### Shared Dev job order

`stale-SHA guard → Dev build (dev_build_cmd or build_cmd, build_env_dev, CLOUDFLARE_ENV=dev_wrangler_env) → audit of the effective config (dev_worker_name, resource policy) → exact D1 check (deployed D1 == migration D1) → shared-D1 guard → stale-SHA guard → Dev D1 migration → stale-SHA guard → publish the audited config → smoke(app_url_dev, dev_smoke_cmd)`. It is the same engine as Production and Preview.

* A failed build, audit or stale run never changes the running Dev Worker or its D1. Production, Preview and Dev all build **before** they migrate.
* Migration reuses `migrate.sh` with the engine-bound target `dev`: D1 = `env.<dev_wrangler_env>.d1_databases`, never top-level (Production); no fallback. The Preview D1 and the Dev D1 **may** be the same physical database (Blog); Production's may not. `migrations apply` is pending-only. Shared Dev has `needs: deploy` purely for ordering, so the two channels never migrate one shared D1 concurrently, plus the shared concurrency group below.
* `dev_worker_name` is **required** and the audit runs on every Shared Dev run (automatic and manual): the adapter-generated effective config (`.wrangler/deploy/config.json`), or if none was generated the source `env.<dev_wrangler_env>` block, must have `name == dev_worker_name` and pass the resource policy below.
* **Same artifact.** `build.sh` records the path, SHA-256 and a content fingerprint (`artifact-digest`: config + Worker entry directory + assets directory) of the audited config. `publish.sh` refuses to run without that record, refuses a config that changed in between, always passes `--config <audited config>` (and `--env <dev env>` only for an un-flattened source config) and clears `CLOUDFLARE_ENV`. A caller-supplied `dev_deploy_cmd` is **publish-only**: it runs with `CF_AUDITED_CONFIG` exported and must `wrangler deploy --config "$CF_AUDITED_CONFIG"`; a changed config or deployable afterwards fails the run and the deployment is reported unverified. (A command that ignores the variable and deploys something else cannot be proven wrong by static means; keep `dev_deploy_cmd` empty to use the default.)
* **Same D1.** `cli.mjs d1-consistency` requires the D1 binding of the config that will be published (effective config, or the source for an un-flattened config) to have the same physical `database_id` as the validated migration target. A mismatch fails before the migration and the publish: DB-A is never migrated while a Worker bound to DB-B is deployed.
* With `d1_migrations`, `dev_build_cmd` is **required**, so the Dev build always succeeds before the Dev D1 is migrated.
* **Shared-D1 guard:** if the Preview and Dev D1 are the same physical database and the Preview Deploy job reported its migration `failed` (or was cancelled), Shared Dev stops before touching anything (D1 state unknown). With distinct D1s, Preview off, or a Preview failure after a successful migration (e.g. a failed smoke), Dev still deploys; a Preview failure never blocks Dev otherwise.
* Credentials: Dev reuses `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` in the `<app>/dev` Environment. Preview credentials and `PREVIEW_SECRETS` are not read in Dev. Missing account credentials fail closed; Dev Worker, environment and resource audits remain separate.
* Smoke fails the job. Recovery: the job records a ledger entry `<app>/dev/shared`; after a failed smoke it redeploys the last verified SHA (from any branch) with the same build, Worker-identity/resource audit and full smoke (probe, `sha_path` for that SHA, `dev_smoke_cmd`), or states **manual recovery required** when none exists. Recovery is skipped when a newer push already owns the branch. The run stays red either way, and a successful recovery never marks the failed SHA verified.
* Concurrency: workflow-level group (below) queues all branches; the job also uses `cfv2-dev-<repo>-<d1_lock_scope or app_name>`, the same group as `persistent-dev`, so automatic and manual Dev deployments never overlap. It differs from the workflow-level groups, so there is no caller/callee deadlock.
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

Engine contract (new consumers; opt in by setting `dev_build_cmd`): `dev_build_cmd` builds only, `dev_deploy_cmd` (optional) publishes only, `dev_smoke_cmd` verifies only. Automatic Shared Dev and the manual `persistent-dev` dispatch then both run build → config audit → D1 check → Dev D1 migration (`d1_migrations`) → publish → smoke through the same engine; the manual run also requires `dev_worker_name` and `app_url_dev`, keeps the approved-ref gate and protected `<app>/dev` Environment, and cannot skip the build. A manual run is recorded in the last-good ledger **only after its smoke passes** (failures are recorded as failed); it has no automatic recovery.

Legacy contract (`dev_build_cmd` empty, existing hybrid callers): the manual dispatch runs `dev_deploy_cmd` (which must build itself) and the optional `dev_smoke_cmd` exactly as before and is **never** written to the ledger, so automatic recovery can never mistake it for a known-good release. Migration plan: add `dev_build_cmd`, `dev_worker_name`, `app_url_dev` (and an audit-clean Dev config); no caller is forced to.

### Recovery limits

After a failed Shared Dev smoke the job redeploys the last verified SHA (from the Dev ledger, any branch). It rebinds the commit identity: occurrences of the failed run's SHA (full and 7-character) in `build_env_dev` and `app_version` are replaced with the last-good SHA; `app_version` values not derived from the commit SHA are kept and flagged with a warning. It then repeats build, Worker-identity/resource audit, publish and the full smoke. Only with `sha_path` set is the restored **commit identity** verified; without it the summary says "commit identity NOT verified". It never rolls back D1 (a schema newer than the restored code is possible), a caller whose build parameters embed the commit in ways other than the exact SHA cannot be rebound, and a failed recovery is reported as such; the run is always red. Manual runs have no recovery.

### Required follow-up in Blog #448 (after this PR is approved)

Not changed by this PR. Replace `dev_binding_allow: r2_buckets:SHINYI_MUYU_VOICE_CACHE` with `r2_buckets:SHINYI_MUYU_VOICE_CACHE=firstsun-blog-assets`; set `dev_deny_resources` if Dev must never touch other Production resources (it must **not** list `firstsun-blog-assets`); keep `preview_deny_resources` for Native Preview unchanged; set `sha_path` so recovery verifies the commit; keep the Voice prefixes `cache/shinyi-muyu/tts/prod/` and `.../preview/`; pin to the merged SHA/tag; and use the existing account token for Dev deployments.

## Post-incident hardening (Blog run 39708495349)

The first live Blog run published a Native Preview with **no URL**, then ran smoke against nothing, then "recovered" a different Worker's deployment, and left a Shared Dev deployment unverified (Dev was not noindexed). Fixes in this contract:

* **Empty Preview URL (root cause):** the former `deploy.sh` (now `publish.sh`) did `echo "url=$(node cli.mjs extract-url ... | sed ...)"`. The pipeline status is `sed`'s and `echo`'s is always 0, and the extractor's `::error::` line was filtered out by `sed`, so a failed extraction printed `url=` and exited 0. Now the extractor runs on its own with its status preserved; the URL must be a non-empty `https://` URL; a missing/malformed payload, a missing or **empty `preview.urls`**, a non-string or non-https entry all fail the Publish step with a sanitized, actionable message (no payload dump). `preview_mutated=true` is printed right after `wrangler preview` succeeds so the workflow can say the Preview *may* have changed and is unverified; no recovery runs (there is nothing to verify against).
* **Host readiness:** before the build, `build.sh` calls the read-only Workers subdomain API (`GET /accounts/{id}/workers/scripts/{worker}/subdomain`) for the host Worker and refuses when `previews_enabled` is `false`, explaining how to enable Preview URLs. It never changes the Worker. An unreadable answer (token scope, 404, network) only warns and the strict post-publish validation applies. Custom-domain Preview hosting cannot be derived from this flag: set `preview_host_check: false` there.
* **Ledger identity:** Preview ledger records now carry `repo` and `host_worker`. `gh.sh last-good-preview` only returns a deployment whose repository, application, branch ref and **host Worker** match, whose latest status is `success` and whose recorded URL is a valid https URL; legacy records without `host_worker` are never eligible (a Preview hosted by `blog-preview` is not a recovery source for one hosted by `blog`). History is kept.
* **Preview recovery:** rebuilds with the Preview build environment restored from `build_env_preview` (failed SHA rebound to the last-good SHA, same for `app_version`), checks the host Worker, requires the redeployed URL to equal the recorded last-good URL, runs the configured `smoke_cmd` (with `sha_path` against the last-good SHA) and records the outcome: content-level verified, Access-liveness only (reported as **not** verified), or failed. D1 is never rolled back; the run is always red.
* **Smoke env isolation:** `smoke_env` is staged in `SMOKE_ENV_FILE` and loaded only inside `smoke.sh`; it is never merged into the job environment, so Production-oriented smoke values can no longer overwrite the target build environment (e.g. `SITE_NOINDEX`) for a later rebuild.

## Migration, concurrency, release and secrets

### Migration safety rules
* **Target D1.** `migrate.sh` always derives the target from the *source* Wrangler config: Production = top-level `d1_databases`, Preview = `previews.d1_databases` (never `env.dev`). It requires explicit `database_id`/`database_name`, one unambiguous binding (`d1_binding` when several), Preview ≠ Production database, and a migrations directory containing `.sql` files, then writes a minimal config naming only that D1 (all `migrations_*` settings such as `migrations_table`/`migrations_pattern` are carried over; `migrations_dir` is made absolute). There is no way to bypass this with a custom command.
* **Pending-only, no-op safe.** `wrangler d1 migrations list` must exit 0 (state readable, target reachable) and `apply` is itself pending-only. The "No migrations to apply" text only skips a no-op `apply`; correctness does not depend on parsing it.
* **Order inside the job (migration is a step, not a job):** `init` (target bound, credentials) → stale check → `prepare` (Wrangler version, Preview identity and **ledger ownership**, source binding policy) → **build + audit of the effective config** → exact D1 target check (read-only; the deployed D1 must be the migrated D1) → stale check → **`migrate.sh`** → stale check → publish. Schema is never changed before a deployable build and a valid target configuration exist; a deleted/superseded branch applies nothing, and a push that lands *during* a long migration is detected by the final stale check so old code is not published over a newer deployment.
* Failed migration ⇒ the job fails and nothing is published or verified (so Release cannot run); PRs never reach it; Worker recovery never rolls D1 back. Production, Preview and Dev apply the same project-owned SQL to their own D1.
* **The target cannot be redirected by application env.** `migrate.sh` is invoked by the engine with the target bound from the composite `target` input and the state written at `init`; `build_env_*`, `smoke_env` and `PREVIEW_SECRETS` are parsed with `--reserved`, which rejects every deployment-control name (`TARGET`, `DEV_ENV`, `GH_TOKEN`, `EXPECTED_SHA`, `RUN_SHA`, `D1_BINDING`, `STATE_DIR`, `BUILD_CMD`, `PUBLISH_CMD`, `INPUTS_JSON`, `CF_AUDITED_CONFIG`, `VITE_APP_ENV`/`VERSION`, ... see `parse-env.mjs` `RESERVED`) in addition to the credential/loader names; application env is loaded only inside the process that needs it and is never written to `GITHUB_ENV`. A build command that itself appends `TARGET=prod` to `GITHUB_ENV` still cannot move the migration (tested).
* **Not implemented (do not assume):** SQL checksum/drift detection or rejection of incompatible histories when several branches share the Preview D1. A branch that edits an already-applied migration, or two branches adding conflicting schema, is not detected; forward-compatible schema changes remain a convention.

### Concurrency (final)
The earlier custom Git-ref lock (`lock.sh`, `refs/cfv2-locks/*`, `contents: write`) was **removed**: GitHub API errors, non-atomic takeover and unverified ruleset behaviour made it unsafe to rely on.

The shared workflow now sets **workflow-level** `concurrency` with `queue: max` (GitHub changelog 2026-05-07; FIFO, up to 100 pending runs per group, extra runs are cancelled, `queue: max` cannot be combined with `cancel-in-progress: true`). Because a group cannot span two jobs, the unit is the whole run, so the Build → Migration → Publish interval is protected as one; CI and E2E remain parallel inside a run. **The group names the shared resource, not the app:** `d1_lock_scope` replaces `app_name`, so two apps in this repository that share one physical D1 get the same group even when their `app_name` differs (the earlier key included `app_name` and therefore did not serialize them).

| Run | Group |
|---|---|
| main push / dispatch | `cfv2-prod-<d1_lock_scope or app>` |
| branch, `d1_migrations: true` | `cfv2-nonprod-d1-<d1_lock_scope or app>` — **one group for the shared Preview AND Shared Dev D1**, not per branch; never equal to the Production group |
| branch, no D1, `shared_dev_on_branch_push: true` | `cfv2-shared-dev-<app>` — **one group for the one Dev Worker**, all branches queue |
| branch, no D1, Shared Dev off | `cfv2-<app>-preview-<branch>` |
| PR, delete, tag, persistent-dev | private `cfv2-run-<run_id>` (never queues behind a deploy) |
| Shared Dev job and `persistent-dev` job (job level) | `cfv2-dev-<repo>-<d1_lock_scope or app>` — automatic and manual Dev never overlap |

* Prod and Preview-D1 never block each other; apps with different physical D1 do not block each other (apps that share one non-Production D1 must set the same `d1_lock_scope`). **Limit:** GitHub `concurrency` is scoped to one repository. It can never serialize migrations from a *different* repository (or a human running `wrangler d1 migrations apply`) against the same physical D1; such sharing is unprotected and must be avoided or coordinated outside GitHub.
* A queued run that is no longer the branch tip applies no migration and publishes nothing (tip re-checked at start, before the migration and again before the publish).
* The `cfv2-` prefix is static on purpose and `github.workflow` is not used: **never put the same group (or `${{ github.workflow }}`-derived group at the calling job) in a caller**, GitHub documents that caller and called workflow with the same group cancel/deadlock each other. Callers' own groups (e.g. heaven-www `CI/CD-<ref>`) are different strings and are fine; they still apply to the caller run (default `queue: single` there is acceptable because a superseded push is skipped anyway).
* Trade-offs: the whole run, including CI/E2E, queues behind earlier runs of the same group; a run waiting for an Environment approval holds the group; when 100 are pending extra runs are cancelled (fail-closed, nothing deployed); a repo-local group does **not** protect a D1 shared with another repository or persistent Dev.
* `actionlint` 1.7.12 does not know the `queue` key; `.github/actionlint.yaml` ignores exactly that message for exactly this file. Syntax validity of `queue` is therefore **not** proven by actionlint.

**Verified in a scratch repository (2026-10-21, private repo `ClaudiaFang/cfv2-concurrency-scratch`, real GitHub Actions, no Cloudflare):** a called reusable workflow with top-level `concurrency: { group: …${{ inputs.key }}, queue: max }` and a caller with no concurrency of its own. Four `workflow_dispatch` runs with the same key (A1–A4, each `one` 25 s → `two`) ran strictly one after another in trigger order, none was cancelled, and A2's first job started only after A1's *second* job had finished (so the gap between two jobs of one run is covered); a run with a different key ran concurrently and was not blocked. Control without `queue` (B1–B4): B2 and B3 were cancelled and only B1 and B4 ran (the old single-pending behaviour this design avoids). GitHub accepted `queue: max` in that position.
**Still not verified:** the called workflow was in the *same* repository (`./.github/workflows/…`), not a cross-repository `uses: firstsun-dev/.github/...@ref` as consumers use; queue-full (100) behaviour; Environment-approval interaction (a waiting run is expected to hold the group); caller/callee same-group deadlock was not reproduced; `workflow_dispatch` only (push events not exercised). Re-run the cross-repository variant before moving the floating `@v2`; if it is not honoured, the fallback is requiring every caller to set the same group on the calling job.

### Release correctness
`Deploy` exposes `deployed_and_verified` (default false). It becomes true only in the step that runs after **this run published its own SHA and smoke passed**. A stale run, a skipped publish, a failed smoke (with or without rollback), or a missing URL leaves it false. `Release` requires `deploy.result == success`, `deployed_and_verified == true` and `target == prod`. A configured E2E that did not run (skipped / empty or invalid matrix) now blocks Migration and Deploy; only `test_e2e_cmd: ''` allows a skip.

### Smoke
Fail-closed (`smoke.sh`): no URL ⇒ fail; the probe requests `probe_path` (default `/`, must start with `/`) and only 2xx counts — or exactly `probe_expect_status` when set (e.g. `405` for a POST-only API route; a single 3-digit code, no ranges) (401/403/3xx-elsewhere/5xx/no response fail); a `sha_path` that is missing or serves another commit fails; a redirect to Cloudflare Access is **liveness only** and passes only for targets listed in `access_protected_targets` (default `preview`; prod must opt in explicitly) — it is *published but not verified* (see "Verification levels"). Production rollback runs through `publish.sh ROLLBACK_TO`, i.e. the same audited config as the publish, then the run stays red and Release stays closed. A failed automatic rollback is reported as failed, never as rolled back.

### Preview secrets
`PREVIEW_SECRETS` is staged by `parse-env.mjs` (validated names, all-or-nothing, `::add-mask::`, owner-only file, removed at the end of the job) and exported into the Preview build/smoke processes through quoted variables (`envfile.sh`), never `source`d. Values with `$()`, backticks, quotes, `;`, `=` and spaces arrive literally; names such as `CLOUDFLARE_*`, `GITHUB_*`, `PATH`, `NODE_OPTIONS` are rejected; multi-line values are not supported (a newline starts a new `NAME=` line and a non-conforming line is an error). Only the Deploy publish/smoke steps see them; CI, E2E and Migration never do.

## Security review (summary)

* Credentials: each Cloudflare credential in the Deploy job is `(main && prod-secret) || (not main && preview-secret) || ''`, in Shared Dev and persistent-Dev the same account credential pair is used as Production; Preview credentials are never read there and missing credentials never fall back. The composite engine action contains no credentials. CI, E2E and lifecycle contain no secrets. All jobs except Release are read-only on repository contents; the lock's `contents: write` is gone.
* Authorization: Deploy / Shared Dev run only for push/dispatch on a branch ref; the engine re-resolves the target (`cli.mjs resolve`) and fails on mismatch; Production uses the protected `<app>/prod` Environment (one approval: Migration is a step of the same job).
* Fork PRs: CI/E2E only, no secrets, no migrate/deploy/lifecycle.
* Injection: env inputs are data (`parse-env.mjs`, reserved names rejected for the engine); commands come from `env:` via `bash -c "$CMD"`; no `${{ }}` inside any `run:` of the workflow or the composite actions (contract-tested); deploy checkouts use `persist-credentials: false`.
* Bindings: Preview source preflight before the first remote command; **effective generated config audited** after the build for both targets.
* Artifacts: none cross jobs; the only upload is the E2E report on failure, retention 3 days.
* Residual: callers' commands are trusted code; Preview migration can still break other branches' code.

## Verification levels

`smoke.sh` reports one level; `engine.sh verified` turns it into the public outcome.

| Level | Meaning | `deployed_and_verified` | Ledger status | Release / known-good |
| --- | --- | --- | --- | --- |
| *(no smoke / smoke failed)* | published, unverified | `false` | `failure` / `error` | no |
| `liveness` | only the Cloudflare Access login redirect answered (target listed in `access_protected_targets`) | `false`, `published_unverified=true` | `inactive` (description says unverified) | **no** |
| `content` | the application itself answered 2xx (directly, or through an authenticated probe: service-token headers via `smoke_env` `PROBE_HEADERS`) | `true` | `success … (content)` | yes |
| `content-sha` | content verified **and** `sha_path` served the expected commit | `true` | `success … (content-sha)` | yes |

* Under an Access redirect `smoke_cmd`/`sha_path` cannot raise the level: an unauthenticated command may be reading the login page.
* **SHA comparison is exact:** the `sha_path` body is split into words; a 40-hex value must equal `EXPECTED_SHA`, an abbreviated (7–39 hex) value is accepted only as an exact prefix and only when no full SHA is served. No substring matching.
* **Authenticated smoke (`auth.sh`):** set `CF_ACCESS_CLIENT_ID` + `CF_ACCESS_CLIENT_SECRET` (standard Access service token; both or neither — a single one fails with an actionable error, never anonymous) and/or the legacy `PROBE_HEADERS`. The same headers go to the initial probe and the `sha_path` request, as a 0600 `curl -H @file` (never argv), values masked. A token that is still redirected to Access is a failure (invalid/expired token), not liveness. Put them in `PREVIEW_SECRETS` (Preview) or `smoke_env` (any target) — they are exported only inside the smoke process.
* Outputs (additive; `url`/`deploy_url`/`dev_url` keep their meaning "published URL"): `published_url`, `verified_url` (only at content level), `published_unverified`; workflow outputs `deploy_verified_url`, `deploy_published_unverified`, `dev_verified_url`, `dev_published_unverified`. Gate consumers on the `*_verified_url` outputs.
* The ledger scan (`last-good-preview`, `last-good-dev`) only accepts `success` records whose description carries `(content)` or `(content-sha)`; liveness-only and legacy records are never recovery targets. Recovery verifies against the **last-good** SHA.

## Deployment guard, lifecycle and concurrency (review fixes)

* `deployment_strategy: production-only` + `shared_dev_on_branch_push: true`: `config-guard` reports it **and** the Deploy job's `if:` plus `engine.sh init` refuse it, so nothing is published or migrated. `production-only` + the default `preview_on_branch_push: true` stays accepted.
* PR lifecycle comments only a Preview returned by the host-bound `last-good-preview` lookup (repo, app, branch ref, host Worker, https URL, content-verified); otherwise the comment is left unchanged. Branch deletion still keeps the Native Preview.
* Run concurrency: the shared non-production D1 group applies only when a non-main channel (Preview and/or Shared Dev, strategy ≠ `production-only`) can run **and** `d1_migrations` is true; CI-only runs use the per-branch group. Production keeps its own group.

## Verified vs not verified

**Verified (mocked, except the same-repo concurrency experiment above; `.github/workflows/cf-worker-v2-ci.yml`):** actionlint 1.7.12 on the workflows; `shellcheck -S warning` on the scripts **and on the `run:` scripts of the composite actions** (`shellcheck-actions.py`); Node unit tests (identity, event/target, migration target, **deployed-D1 consistency, artifact digest, reserved control variables**, Production / Dev resource policy including Queues, Services, KV, Hyperdrive and Durable Objects); `test-workflow-contract.py`, which *evaluates* every job `if:`, credential `env:` and the concurrency group over an event matrix (the four Preview × Shared Dev combinations, main/feature/PR/delete/tag/dispatch routing, validation skip rules, Release gate, credential isolation, the shared-D1 group across different `app_name`) and checks the **composite action structure** (engine step order, stale checks around the migration, recovery wiring, static cross-repository `./.cf-tools/actions/...` references with the tooling checkout first, no interpolation in scripts); `test-jobs.py`, which **executes the real `run:` steps** of the Deploy, Shared Dev and persistent-Dev jobs *including the composite engine* against stub `gh`/`pnpm`/`curl` (jobrunner expands local composite actions with GitHub step semantics): migration after build and before publish, failed migration publishes nothing, stale before / during the migration, post-migration publish refusal, effective-vs-migrated D1 mismatch, protected env names (`TARGET`, `DEV_ENV`, `GH_TOKEN`, `EXPECTED_SHA`, ...) in `build_env_*` / `smoke_env`, a build writing `TARGET=prod` into `GITHUB_ENV`, Production Queue / Service / D1 in the Dev config, a publish command that modifies the audited config, Preview + Dev on one D1, smoke failure and recovery (success, failed, URL mismatch, superseded), failed rollback visibility; behaviour tests for `build.sh` / `publish.sh` (same-artifact contract), `migrate.sh`, `smoke.sh`, `parse-env.mjs`, probe and ledger. Mutation spot-checks (engine-reserved env, post-migration stale guard, D1 consistency, artifact digest, target binding) make the suites fail as intended.

**Shared Dev (mocked/static only):** the same suites cover the Shared Dev channel. **No live Shared Dev or Native Preview pilot has run**; live behaviour (Dev environment protection, the `queue: max` group across Preview and Dev, real recovery, `uses: ./.cf-tools/actions/...` resolution on a real runner in a cross-repository call) is unverified.

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
