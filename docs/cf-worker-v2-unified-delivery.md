# CF Worker v2 — unified target-driven delivery (shared-engine contract)

Spec: `openspec/changes/unify-cf-worker-v2-delivery-targets`. This document is the **frozen Phase 1 contract** consumers build against.
It supersedes the job topology in `cf-worker-v2-refactor.md` ("Job DAG", "Shared Dev job order"); the engine, security rules, ledger
and recovery rules described there still apply. `_cf-worker-v2.yml` remains the only public entrypoint; `_cf-worker-delivery.yml` is an
**internal** reusable workflow called only by it. v1 / v1.5 and the floating `@v2` tag are untouched by this change.

> **Revised again (PR #89 Phase 1 simplification):** validation lives in `_cf-worker-ci.yml`, the Preview lifecycle and manual persistent Dev in their own workflows, every shared action/workflow is referenced with `$/` — see [cf-worker-v2-refactor.md](cf-worker-v2-refactor.md#phase-1-simplification-pr-89-refactor--current-structure).
>
> **Revised (PR #89 simplification).** No separate Preflight stage. **One Migration job with integrated safety validation; reusable target-driven Delivery.**
> The earlier Preflight-per-kind / Migration-per-D1-owner topology, and the real-runner tables further down that exercised it, describe the superseded design;
> the sections "Topology", "Behaviour" and "Single Migration job" are current.

## Topology (final DAG)

```
CI call (_cf-worker-ci.yml): Lint ‖ Typecheck ‖ Unit ‖ Integration ‖ Build Validation ‖ E2E     (independent, parallel)
                         │
                 Validation Gate        every CONFIGURED job succeeds, every unconfigured one is skipped, else fail closed; output validation=passed
                         │
                  Plan                  enabled kinds + smoke modes + ONE physical D1 (pure data, no credentials)
                         │                outputs: on_<kind>, descriptors, migration {owner kind, Environment, d1_id, kinds}
                         │
              Migrate                   ONE job, Environment of the owner (<app>/prod on main)
                │   1. each enabled target that is not the owner: build + effective-config audit + exact D1 binding (nothing remote)
                │   2. the owner: build + audit + D1 binding + stale-SHA guard, then the ONE migration
                         │                skipped when d1_migrations is false / verify phase; never rolls schema back
 delivery_production | delivery_native_branch | delivery_native_dev | delivery_persistent_dev      one CALL per enabled kind, mutually independent
      └─ deploy ──► smoke ──► (conditional) rollback          jobs of the SAME internal workflow, scoped to that one target
                         │
                  Release (Production, main only; reads ONLY delivery_production)
```

Kinds: `production`, `native-branch` (per-branch Native Preview), `native-dev` (the stable Native Preview named `dev`),
`persistent-dev` (the separate `[env.dev]` Dev Worker; Notification Hub). Only enabled kinds exist as calls (a disabled call is skipped by the
plan flag `on_<kind>`), so no phantom delivery paths exist. **A delivery call waits only for the plan (which itself requires a passed CI) and the
single Migration job (success, or skipped when there is nothing to migrate); it never waits for, and is never affected by, another kind's Deploy / Smoke / Rollback.** There is no
matrix anywhere in the delivery graph: every `needs.<call>.outputs.*` belongs to exactly one target.

| Event | Kinds |
| --- | --- |
| trusted push to `main` | `production` |
| trusted push to another branch | `native-branch` if `preview_on_branch_push`; `native-dev` if `shared_dev_native_preview_on_branch_push`; `persistent-dev` if `shared_dev_on_branch_push` |
| protected `workflow_dispatch` persistent-dev | the existing manual `persistent-dev` job (unchanged) |
| PR / delete / tag | none (lifecycle handler only) |

Rejected (plan fails closed, `config-guard` also reports): `production-only` with any Dev channel; `shared_dev_on_branch_push` together with
`shared_dev_native_preview_on_branch_push`; unknown `smoke_mode` / `smoke_mode_overrides`; an unknown kind in a `phase_state` hand-off;
a Preview/Dev kind whose D1 is the Production D1; enabled kinds that name **two or more distinct physical D1 databases** while `d1_migrations` is on (single-DB rule).

### P1 fix: a stale-skipped migration never authorizes a publish (PR #89 review of `f3325f5`)

**Root cause.** `resolveMigrationUsed()` (the Migration Gate every Delivery runs before anything is built or published) skipped the result, owner and physical-D1 checks when the migration result carried `stale=true`.
A Migration job superseded by a newer push succeeds *as a skip* (nothing applied), so with `needs.migration.result == success` the gate accepted it. The Delivery's own branch-tip check then decided alone: if the branch was
force-pushed back to the original commit in between (A → B → A) the tip looked fresh and the Publish proceeded **without the required migration**. Found by code review; a potential race, not a confirmed incident.

**Fix** (`actions/cf-preview/delivery.mjs`, no other production file): the gate now fails closed, in this order — `d1_migrations=false` → no migration needed; no planned owner; Migration job result ≠ `success` (failed, cancelled, skipped, missing); result missing / invalid JSON / not an object;
**`stale` is `true` or `"true"` → rejected, whatever else the result contains**; `migration` ≠ `applied`; owner kind ≠ planned owner; D1 id ≠ planned physical D1. The branch-tip check stays as an additional guard but is no longer relied on.

**Authorization invariant.** *No completed migration (applied, by the planned owner, on exactly this call's physical D1) = no Publish authorization.* Nothing infers "none" from a skip, nothing re-runs a stale migration, and the gate (engine step `Migration gate`) runs before the stale guard, set-up, prepare, build and publish of the publish stage.

**Behaviour change to know.** A genuinely superseded run (branch moved and stayed moved) used to end green with nothing published; with `d1_migrations: true` its Delivery now **fails at the Migration Gate** (red, nothing built, published, smoked or rolled back). `migration_status` is unchanged (`none` for a stale-skipped migration). `d1_migrations: false` and `pipeline_phase: verify` (which never runs the gate and never migrates) are unaffected.

**Tests.** `delivery.test.mjs` (the old "stale is accepted" test is replaced by rejection of `stale: true` / `"true"`, with and without valid metadata or owner/D1, plus missing / invalid / non-object JSON, `none`, wrong owner, wrong D1, failed / cancelled / skipped job, migration disabled);
`test-workflow-contract.py` (the real `cli.mjs migration-gate` executed for each case; gate precedes every other publish-stage step); `test-jobs.py` chain (stale branch and "superseded after the build" now fail at the gate; **A → B → A**: Migration superseded, Delivery sees a fresh tip, gate rejects before the build, nothing published, smoked, rolled back or released; deferred verification unchanged).

### Reusable-workflow limits honoured

| Constraint | How |
| --- | --- |
| Nesting depth | caller → `_cf-worker-v2.yml` → `_cf-worker-delivery.yml` / `-ci` / `-lifecycle` / `-persistent-dev` = 3 levels (limit 10); none of them calls a further workflow |
| Permission ceiling | each `uses:` job declares the permissions the called jobs need (`contents: read`; `deployments: read` for the Migration job, `deployments: write` + `pull-requests: write` for Delivery); the caller of the public workflow already had to grant these |
| Static `uses:` | `$/.github/workflows/_cf-worker-delivery.yml` per call (no expression); actions are `uses: $/actions/<name>` (this repository at the running commit) |
| `tools_ref` | deprecated and ignored: `$/` makes the engine always come from the commit of the running workflow |
| Secrets | passed explicitly per call (smoke credentials only to Delivery); never `secrets: inherit` |
| Environments | job property inside the called workflow (`<app>/prod`, `/preview`, `/native-dev`, `/dev`); the Migration job runs in the owner's Environment; Smoke has none |
| Concurrency | the run-level group stays on the public workflow; the delivery workflow has **only job-level** groups (`cfv2-leg-<run>-<kind>[-rollback]`, persistent Dev keeps `cfv2-dev-<repo>-<scope>`) so nothing deadlocks |

## Inputs

All pre-existing inputs are unchanged (names, types, defaults). New, all optional and default-off:

| Input | Default | Meaning |
| --- | --- | --- |
| `shared_dev_native_preview_on_branch_push` | `false` | Native Dev: every eligible branch push updates Native Preview `dev` (host = `preview_worker_name` or `app_name`, `[previews]` bindings, **not** `[env.dev]`). |
| `build_env_shared_dev_native_preview` | `""` | Build-time `KEY=VALUE` env of the Native Dev build. Never falls back to another kind's env. |
| `app_url_shared_dev_native_preview` | `""` | Optional declared Native Dev URL. Must equal the URL derived from Cloudflare (below) or the run fails **before** the build. |
| `preview_url_suffix` | `""` | Optional host suffix of Preview URLs (`-<worker>.<subdomain>.workers.dev` or `.<custom domain>`). Normally empty: Cloudflare reports it. Required for custom-domain Preview hosting (`preview_host_check: false`). |
| `smoke_mode` | `full` | `full` = built-in probe + `smoke_cmd` (current behaviour); `basic` = content-level HTTP/health only (no `smoke_cmd`); `skip` = no request. |
| `smoke_mode_overrides` | `{}` | JSON keyed by kind, e.g. `{"native-dev":"basic"}`. |

Engine-owned build variables (reserved, cannot be set from caller env): `CFV2_DELIVERY_KIND`, `CFV2_TARGET_URL`; `VITE_APP_ENV` is
`prod` / `preview` / `dev` (`dev` for **both** `native-dev` and `persistent-dev`).

## Outputs (public contract)

Pre-existing outputs keep their meaning: `deploy_*`, `version_id`, `previous_version_id`, `migration_status`, `rollback_result`
describe the Production / per-branch Preview delivery; `dev_*` describe `persistent-dev`; `deploy_gate_blocked`; `phase_state`.
Added: `native_dev_url`, `native_dev_verified_url`, `native_dev_published_unverified`, `native_dev_verified`, `native_dev_smoke_level`.
`deploy_smoke_level` / `native_dev_smoke_level` may now be `skipped`. `phase_state` is opaque: pass it unchanged to the verify call.

**Output determinism.** Every per-kind output SELECTS its source call from the plan (`jobs.plan.outputs.on_<kind> == 'true'`) and then reads that call's own result - never "whichever string is non-empty". `migration_status` is the single Migration job's always-run status step: `applied`, `none` (nothing pending, superseded, `d1_migrations` false or a verify call) or `failed`. `deploy_*` read Production, else
Native Branch (mutually exclusive by event: `main` vs. other branches); `dev_*` read `persistent-dev`; `native_dev_*` read `native-dev`;
`rollback_result` is the first rolled-back kind in the order production, native-branch, native-dev, persistent-dev.

## Behaviour

* **Per-target builds and URL pre-resolution (P1-B).** Every Deploy / Preflight checks out the validated SHA, imports its own build env, resolves
  **its canonical URL before building**, builds, audits its generated Wrangler config and publishes that artifact.
  * Native Preview URL (Cloudflare Workers "Previews" documentation): `workers.dev` → `<preview-name>-<worker-name>.<subdomain>.workers.dev`;
    custom domain → `<preview-name>.<domain>`. The Workers subdomain API (`GET /accounts/{id}/workers/scripts/{worker}/subdomain`, the same
    read-only call as `preview-host-check`) reports `previews_enabled` and `preview_url_suffix`; the URL is `https://<preview-name><suffix>`.
    The identity is the real one: `<branch slug>` (Native Branch, via the collision-guarded identity) or the reserved name `dev`.
  * Nothing is guessed: if neither Cloudflare nor `preview_url_suffix` provides a suffix and no URL is declared the run fails before the build;
    a declared URL (`app_url_shared_dev_native_preview`) that contradicts the derived one fails before the build; two suffix sources must agree.
  * The URL is exported to the build as `CFV2_TARGET_URL`; the published URL must equal it (also in recovery rebuilds), else the publish is rejected.
  * Build-env checks before the build: another target's public URL is rejected; this application's own callback / redirect / app-origin / site
    variables (`*CALLBACK*`, `*REDIRECT_URI/URL*`, `*APP_ORIGIN*`, `*APP_URL*`, `*SITE_URL*`) must be **same-origin with the target URL**.
    An IdP issuer / `IDP_URL` names another service and is not verifiable, so it is only checked against this app's other targets.
* **Single Migration job (replaces Preflight → Migration, P2-B).** The plan groups the enabled kinds by `account_id/database_id` of the source config and admits
  **one** group; identical D1s are deduplicated (Native Branch + Native Dev on one preview D1), distinct ones fail the plan before any build. The `migration`
  job then validates every enabled target that is not the owner (engine stage `preflight`: non-publishing build, effective-config audit, built config binds
  **exactly** the plan's D1) and finally runs the owner's `migrate` stage (the same checks for the owner, a stale-SHA guard, then the one `d1 migrations apply`).
  Step order is the guarantee: no mutation can precede a failed validation. A Preview/Dev kind mapped to the Production D1 fails the plan. Each Delivery call's
  publish stage re-audits the built config against the migrated D1 id and validates that the migration result has the planned owner kind and D1 id. A failed
  migration starts no Delivery. Stale-SHA checks run before migration and again before publish; D1 is never rolled back. Same-repository serialization is
  the workflow-level concurrency group (`cfv2-nonprod-d1-<scope>` / `cfv2-shared-dev-<app>` also cover Native Dev); a D1 shared with **another
  repository** is not locked (GitHub limitation, see limits).
* **Result routing (P1-A).** Each Delivery call receives only its own hand-off and THE migration result (validated by the planned owner kind and the call's D1 id inside the call). The Smoke and Rollback jobs refuse a hand-off whose `kind`, `published_sha` or URL belong
  to anything else (fail closed on missing / ambiguous / stale / mismatched data).
* **Native Dev.** Fixed name `dev`, own ledger environment `<app>/native-dev/dev`, own GitHub Environment `<app>/native-dev`, never
  advertised on PRs. The name `dev` is reserved: with Native Dev on, a branch named `dev` gets Preview `b-dev`; Preview GC never collects `dev`.
* **Smoke / recovery.** `skip`: no request, `deployed_and_verified=false`, `verified_url` empty, `smoke_level=skipped`, ledger `inactive`
  (never known-good), no rollback, no Release. `basic`: Access-redirect = liveness only = Published/Unverified. Recovery is target-owned and runs
  only in the failing kind's call: `native-dev` / `persistent-dev` redeploy the last known-good deployment of the same ledger unless a newer one
  exists; Production keeps its fail-closed policy; a blocked cross-service gate never rolls back.
* **Release** requires the Production Smoke to be content-verified and no Rollback to have run; it reads only `delivery_production`.
* **`pipeline_phase`.** `deploy` runs the Migration job and each call's Deploy only; the `phase_state` output carries each kind's own Deploy
  result, its migration result and D1 id, the kinds, the inputs and the concurrency group. `verify` (second call) runs only each kind's Smoke and
  Rollback from that data (`handoff_json`, `migration_json_in`, `d1_expected_id` are that kind's own entries) and `upstream_gate`; Heaven's
  ID ↔ Test Client ↔ Check-in dependencies are expressed by the caller's `needs` between the deploy and verify calls, unchanged.

## Verification evidence

Local (mocked Cloudflare/GitHub) — suites: `test-workflow-contract.py` (job graph, no matrix / no cross-kind `needs`, gating, credential isolation,
permission ceiling, concurrency, interface compatibility, `pipeline_phase`), `test-jobs.py` (executes the real jobs and composite engine per call
against stubs: all four kinds, simultaneous Branch + Native Dev, reversed completion order, one-target failure isolation, shared-D1 dedupe, URL
pre-resolution, smoke full/basic/skip, rollback isolation and stale-deployment protection, Release gate, deferred `pipeline_phase`),
`delivery.test.mjs`, `lib.test.mjs`, `test-*.sh`, `gc.test.mjs`, shellcheck, actionlint 1.7.12. Local gate at `e72ae91`+: contract 623, test-jobs 379, node (lib+delivery+gc) 114, test-deploy 84, migrate 26, smoke 49, parse-env 27, probe 20, gh 24 — all
passing; actionlint 1.7.12 and shellcheck clean.

**Real-runner evidence** — disposable private caller `firstsun-dev/cf-v2-scratch-caller`, `runner: ubuntu-latest`, **no Cloudflare secrets**, calling
`firstsun-dev/.github/.github/workflows/_cf-worker-v2.yml` at the pinned SHA `88d9777` (`tools_ref` = same SHA):

| Run | What it proves | Observed |
| --- | --- | --- |
| [41035153116](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41035153116) / [41035205869](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41035205869) | **Defect found by the real runner**: `startup_failure` at `c6423d0`. | GitHub validates the permission ceiling for **every** job of a called workflow, including jobs skipped by `if:`. The Preflight / Migration calls granted only `deployments: read` while the same workflow's Deploy/Smoke/Rollback jobs request `deployments: write` + `pull-requests: write`. Fixed in `88d9777` (all per-kind calls grant the full ceiling; each called job still requests only what it needs); the contract test now checks the ceiling against every called job. Bisected by copying the workflow into the scratch repo (`pf1`/`mg1` startup_failure, `dl1`/`nocalls` start). |
| [41035901102](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41035901102) | Cross-repo call of the public workflow at `88d9777` **starts**; the nested `uses: ./.github/workflows/_cf-worker-delivery.yml` resolves; all 20 public outputs are readable by a read-back job. | `call` = failure by design at *Build Validation*: `pnpm: command not found` (see limitations). `readback` = success: `CALL_RESULT=failure`, `DEPLOY_GATE_BLOCKED=false`, every other output empty, `phase_state` rendered as JSON. 22 downstream jobs skipped (fail closed behind the red Gate). |
| [41036120312](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41036120312) | `pipeline_phase: verify` with a hand-built `phase_state` (kinds native-branch + native-dev): plan from the hand-off, **one independent delivery call per kind**. | success: `Delivery plan`, `Delivery (native-branch) / Smoke Tests`, `Delivery (native-dev) / Smoke Tests` ran (nothing was published, so nothing is verified); 29 other jobs skipped; read-back: `DEPLOY_TARGET=preview` (selected by the plan flag), `DEPLOY_URL`, `DEPLOY_VERIFIED`, `NATIVE_DEV_URL`, `NATIVE_DEV_VERIFIED`, `MIGRATION_STATUS`, `ROLLBACK_RESULT` empty, `DEPLOY_GATE_BLOCKED=false`. |
| [41036125410](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41036125410) (ref `main`) | Engine event/kind guard on a real runner. | Preflight(native-dev) failed closed: `target mismatch (enabled kinds 'production', engine 'native-dev'); refusing to deploy`; Deploy / Migration / Smoke / Rollback skipped. |
| [41036258062](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41036258062) (ref `feat/x`) | Preflight engine stage on a real runner, branch event. | Passed init and the kind guard, then failed closed: `preview credentials not provided (set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID)`; the other four jobs skipped. |

Not demonstrable on GitHub-hosted runners without Cloudflare credentials (and so **not** proven by a real run): URL resolution against the Workers
subdomain API, the build, effective-config audit and D1 binding checks, migration, publish, smoke against a live Worker, rollback. Those are proven
only by the local suites above.

## Test profiles and measured performance (PR #89 refactor)

`scripts/cf-preview/test.sh fast|chain|full` is the single entrypoint for CI and agents (logs go to `$LOGDIR`, only summaries print).
`fast` = static workflow contract, unit tests (`lib.test.mjs`, `delivery.test.mjs`), stub shell tests, GC unit tests (~10 s).
`chain` = the real-shell Migration → Deploy → Smoke → Rollback scenarios of `test-jobs.py`, which is now shared fixtures plus **eight explicit
`group_*` functions** (`test-jobs.py --list | --group ID`). `run-job-groups.py` is a thin launcher (bounded parallelism: `CF_V2_TEST_JOBS` or
min(4, CPUs)) that checks a manifest of expected groups with minimum check counts: a missing, unexpected, empty, shrunken or skipped group, or a
missing perf report, fails the run. `full` = both; it is the only profile behind the required `contract` check. `JOBRUNNER_PERF=<file>` writes a JSON
report (subprocesses by command/engine phase, workspaces, jobs).

| Metric (404 chain checks; 379 before + 25 added in this work, none removed) | Before (3842a45 / a9fd174) | After (8ad9f5f) |
| --- | --- | --- |
| Chain wall time, local (bounded parallelism 4) | ~6 min serial equivalent | ~101 s (`run-job-groups.py`) |
| Fast profile, local | not separated | ~11.5 s |
| Full (Fast then Chain, local) | not separated | 1m53s |
| Whole CI job (GitHub-hosted: setup, tests, shellcheck, actionlint) | 2m29s – 4m13s | 2m19s – 3m17s |
| Engine `init` average cost | 213 ms (697 calls, 36 % of subprocess CPU) | 58 ms (702 calls, 40.9 s CPU) |
| Recorded step-subprocess CPU | ~398 s | ~306 s |
| Node processes in one Production chain (system-wide `execve`) | 17 | 9 |
| All `execve` in one Production chain | 567 | 549 (-3 %; the rest is the harness's own bash stubs) |
| Step-shell subprocesses counted by the harness | 7180 | 7640 (+6 %: new `migration-gate`/`init-checks` steps; not a reduction) |
| Workspaces created | 1408 | 1437 (new tests) |

Honest reading: wall time improved through parallel groups, a lighter `init` and the Fast/Full split. The harness's step-shell count did **not** fall; the
work removed is *inside* the steps (Node/`jq` processes per engine call). Replacing repeated full-chain scenarios with focused tests was **not** done in
this round: every end-to-end scenario is still run, new focused unit tests were added alongside them. Remaining top costs: `engine:build` 14 %,
`prepare` 7 %, `hydrate` 5 %.

### Structure (static, before → after)

| | 3842a45 | a9fd174 | 8ad9f5f |
| --- | --- | --- | --- |
| `_cf-worker-v2.yml` lines | 1318 | 1259 | 1249 (+2: the scheduling-lock expression now covers verify) |
| `_cf-worker-delivery.yml` lines | 494 | 450 | 446 |
| `engine.sh` lines | 593 | 606 | 524 |
| `with:` field lines across the 12 per-kind calls | 196 | 144 | 132 |
| Inline `jq` policy steps in the delivery workflow | 10 | 3 | 3 |
| Node spawn sites inside `engine.sh init` | 24 | 17 | 3 |
| Build / publish implementation paths in the engine | 2 / 2 | 1 / 1 | 1 / 1 |
| Independent implementations of "which kinds / D1 / owner / smoke mode" | plan YAML + engine shell + workflow expressions | descriptor (JS) | descriptor (JS), engine re-validates it |

Per call, `target`, `environment`, `d1_expected_id`, `d1_owner_kind`, `deploy_group`, `rollback_group` and `kind` are one `descriptor`. Unavoidable
GitHub Actions duplication: the 12 static `uses:` calls (one job per kind keeps outputs unambiguous), each call's `permissions` ceiling and `secrets`
block, and the two-step tooling checkout in every delivery job — `uses:` cannot take an expression, so a composite that fetches the tools at
`tools_ref` cannot itself be referenced from `tools_ref`; each job runs on its own runner anyway.

Pure decisions now unit-tested in `delivery.mjs` instead of inline shell/jq: target descriptor (incl. deferred-verification restore), migration peer gate,
migration-used resolution, engine initialisation (`validateInit`), verification outcome, Production rollback guard, rollback verdict.

### Deferred verification keeps the deploy call's decisions

`phase_state` carries the deploy call's validated descriptor map (`descriptors`). The verify call's plan restores it (`restoreDescriptors`), checks it
against the rest of the hand-off (kinds, application, repository, D1 ids, migration owner; phase must be `deploy`) and only moves `phase` to `verify`.
A legacy hand-off without `descriptors` is rebuilt from the deploy call's own `phase_state.inputs`, never from the verify call's inputs; missing identity
fails closed. For leg kinds the legacy rebuild uses the verify run's id in the (uncontended) rollback group name — the only documented difference.

### Lock identity across Deploy → Verify → Rollback

* **Job-level locks.** Persistent Dev's Deploy and Rollback groups are `cfv2-dev-<repo>-<d1_lock_scope or app>`. `restoreDescriptors` requires the handed-over
  descriptor to carry exactly that, rebuilt from the current repository, the deploy call's application and the deploy call's `d1_lock_scope` — a modified
  deploy/rollback group, another repository, another application or a changed scope is rejected. A verify call has no input that can pick another Dev lock.
* **Run-level (top-level `concurrency`) lock.** GitHub evaluates it *before any job*, so no Plan check can repair it. For a verify call the expression is therefore the
  deploy expression applied to `phase_state.inputs` (never `phase_state.group`); `delivery.mjs runLockGroup` is its tested twin (512 scenarios, deploy and verify
  mode, rendered from the real YAML). The Plan job then requires `phase_state.group` to equal that value (a `cfv2-run-<id>` fallback group only has to be numeric).
* **Trust boundary (explicit).** `phase_state.inputs` is caller-supplied data. It is the *source* of the scheduling-time lock, so a caller that edits it chooses a lock
  exactly as it could by calling the workflow with a different `d1_lock_scope` directly; GitHub concurrency is cooperative serialisation, not authorisation. What is
  guaranteed: a stale, malformed or inconsistent hand-off (group, descriptor, D1, owner, repository, application) cannot silently join or create a different lock and
  fails the Plan closed, and verifying from another ref/event than the deploy ran on is refused (its derived lock would differ from `phase_state.group`).
  A group string that merely *claims* another lock is ignored for scheduling (see the queue evidence below).

### CLI contract

`cli.mjs` writes workflow annotations (`::error::`, `::warning::`, `::notice::`, `::add-mask::`) to **stderr**; **stdout carries only `key=value` records** and is what the
workflow pipes into `$GITHUB_OUTPUT`. A rejected input therefore exits non-zero with the original message and writes nothing to the output file (previously GitHub added
`Unable to process file command 'output'`). The job harness now enforces GitHub's output-file format, so any regression fails the chain tests. `parse-env.mjs` keeps its
documented stdout contract (its stdout never feeds an output file). Legacy `phase_state` (no `descriptors`): migration ownership is reconstructed per physical D1 only
when every member kind's migration result proves the same owner on the same D1; missing, conflicting or foreign evidence fails closed; hand-offs without D1 migrations
stay valid. `test.sh full` does not start Chain when Fast failed.

### Real-runner evidence pinned to `8ad9f5f7b2ab83ea40200c7a4ccf625bbcb03d9b` (final implementation SHA; later commits are docs only)

Disposable `firstsun-dev/cf-v2-scratch-caller`; public workflow and `tools_ref` both the SHA above; jobs on the org's provisioned `linux` runners (`heavenweb-runner-*`, pnpm
baked in). No Cloudflare credentials existed, so nothing remote was touched.

| Run | What it proves | Observed |
| --- | --- | --- |
| [41062254408](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41062254408) | Valid deferred verify handoff; nested reusable workflows; public outputs; deploy `smoke_mode=skip` kept although the verify call defaults to `full` | success; `deploy_smoke_level=skipped`, `deploy_verified=false`, `deploy_published_unverified=true`, `deploy_url` present |
| [41062261540](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41062261540) | Forged `phase_state.group` | Plan fails with `phase_state.group 'attacker-group' is not the lock identity the deploy inputs imply ('cfv2-prod-scratch')`; no `Unable to process file command`; delivery skipped |
| [41062268866](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41062268866) | Tampered D1 metadata | Plan fails with `descriptor for production binds D1 'FOREIGN' but the hand-off recorded 'REAL-DB'`; no output-file error; delivery skipped |
| the three runs above, dispatched back-to-back | Scheduling-time lock | They ran strictly one after another (valid run first; the forged-group run stayed `pending`/`queued` until it finished, then failed; then the tampered run): the forged group string did **not** create a separate lock — all three shared the lock derived from `phase_state.inputs` (`cfv2-prod-scratch`) |
| [41062278092](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41062278092) | Real `deploy` → `verify` chain, persistent Dev, `d1_lock_scope: shared-db` | Build Validation, Gate, Plan succeed; Deploy fails closed (`dev credentials not provided`); the real `phase_state` has `group: cfv2-shared-dev-scratch` and persistent Dev `deploy_group = rollback_group = cfv2-dev-firstsun-dev/cf-v2-scratch-caller-shared-db`; the verify call (no scope input) restores it; its Plan and Smoke succeed |

Local suites on the same code: 404 chain checks, 131 Node tests, 626 workflow-contract checks (`test.sh full` = 1m53s). CI on the SHA: [run 41061646039](https://github.com/firstsun-dev/.github/actions/runs/41061646039)
(3m17s; actionlint and shellcheck green).

**Not demonstrable here and therefore NOT claimed:** live D1 migration, URL resolution against the Workers subdomain API, publish, smoke against a live Worker, rollback and
recovery — they need approved non-production Cloudflare credentials and are covered only by the local suites. The queue timeline shows GitHub's behaviour for the scheduling
lock on `workflow_dispatch` callers; it was not exercised for every event type.

## Single Migration job (PR #89 simplification) - protections, removed complexity, measurements

**Decision.** No separate Preflight stage. One Migration job with integrated safety validation. Reusable target-driven Delivery. (The earlier requirement of a
Preflight matrix is superseded.) No new reusable-workflow layer, no matrix, no generated YAML.

**Protections that remain** (all enforced by the engine / `delivery.mjs`, none re-implemented in YAML): physical D1 identity from the plan; effective (adapter-generated)
Wrangler config audit per target; exact D1 binding per enabled target before any mutation; Preview/Dev never on the Production D1; stale-SHA guard before the
migration and before publish; Environment protection (`<app>/prod`) on the Migration job; one migration result validated by owner kind + D1 id in every Delivery call;
Production/non-production isolation; never an automatic schema rollback; per-target Smoke and Rollback; deferred verification never migrates.

**Complexity removed.** 8 reusable-workflow calls (4 Preflight, 4 Migration) and their `peers`/`kinds` strings, the per-kind `owns_<kind>` plan outputs, `own_`/`owner_`
migration inputs and the Native-Branch-as-owner special case, `checkPeerPreflights`, the `stage` input and the `preflight`/`migrate` jobs of the delivery workflow.

**Engine changes for several calls in one job:** `init` starts from a clean state directory; the tooling `npm ci` and the application `Setup dependencies` run once per job
(`CFV2_DEPS`, reserved name); the migration gate only guards `publish`.

Measured locally on the original PR head (`aa19cf6`) and this branch, same machine, same suites (mocked Cloudflare/GitHub; this is **not** a live-run measurement):

| Metric | Before (`aa19cf6`) | After |
| --- | --- | --- |
| `_cf-worker-v2.yml` lines | 1249 | 1081 |
| `_cf-worker-delivery.yml` lines | 446 | 320 |
| Preflight calls | 4 | 0 |
| Migration job declarations | 4 calls | 1 job |
| Migration executions per run (`d1 migrations apply`) | 1 (deduplicated by owner) | 1 (at most 1; distinct D1s rejected) |
| Runner jobs after the gate - main push + D1 | 6 | 5 |
| Runner jobs - feature push + D1 (Native Branch) | 6 | 5 |
| Runner jobs - Native Branch + Native Dev on one D1 | 9 | 7 |
| Runner jobs - no D1 | 4 | 4 |
| Application builds - main + D1 / feature + D1 / Branch + Dev on one D1 / no D1 | 3 / 3 / 5 / 1 | 2 / 2 / 4 / 1 |
| Engine `init` calls (same four scenarios) | 4 / 4 / 7 / 2 | 3 / 3 / 6 / 2 |
| Fast profile | ~12.1 s | ~11.7 s |
| Chain profile (parallelism 4) | 103 s | 92 s |
| Full profile | 1m54s | 1m43s |
| Chain checks | 404 | 413 |
| Workflow-contract checks | 626 | 583 |
| Node tests (lib + delivery + cli) | 131 | 130 |
| Harness workspaces / step subprocesses | 1456 / 7668 | 1285 / 6878 |

Contract checks fell by 43 because the per-kind loops over eight removed call jobs (needs, stage literals, descriptors, secrets, permission ceilings) no longer exist; each
protection they covered has a counterpart on the single job (needs, Environment expression, permissions, secrets, engine-step order, status step). One Node test (the peer
gate) was replaced by the migration-result test. Chain manifest minimums were only raised (`migration-d1` 55 → 62, `review-regressions` 34 → 36). Saved wall time comes from fewer
runner jobs and builds; fewer YAML lines alone are not claimed as a performance gain, and no live-runner timing exists.

**Required scenarios** (all in `test-jobs.py` / `test-workflow-contract.py` / `delivery.test.mjs`): 1 main migrates Production once; 2 feature push migrates Preview once; 3 Branch + Native
Dev on one D1 migrate once; 4/5 D1-less or disabled migration deploys and never blocks; 6 Preview mapped to Production D1 fails before mutation; 7 distinct D1s fail the plan;
8 a failed Migration starts no Delivery; 9 Smoke/Rollback isolated per target; 10 Release needs a verified Production; 11 deferred verification never repeats the Migration; 12
cross-service ordering unchanged; 13 public outputs unchanged (`migration_status` now always `applied` / `none` / `failed`); 14 the Migration job runs in `<app>/prod` on main.

### Real-runner evidence for the single Migration job (pinned to `e69365e4087fb3af0aa50629b79f368a8b4738e2`; later commits are docs only)

Disposable private `firstsun-dev/cf-v2-scratch-caller`; public workflow and `tools_ref` both the SHA above; org `linux` runners; **no Cloudflare credentials and unprotected scratch
Environments**, so nothing remote was touched and approval protection was not exercised. Fake D1 ids in the scratch `wrangler.toml`.

| Run | What it proves | Observed |
| --- | --- | --- |
| [41575194834](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41575194834) (ref `feat/x`, Native Branch + Native Dev, `d1_migrations`) | Nested reusable-workflow resolution; the single `Database Migration` job runs in the plan's **dynamic Environment** (`scratch/preview`); validation of the non-owner target happens first | Plan success; Migration failed closed at the first validation step (`preview credentials not provided`); the always-run status step ran; **all Delivery calls skipped**; public `migration_status=failed` readable by the caller |
| [41575205777](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41575205777) (ref `main`) | Production path: Environment `scratch/prod`, owner = production | Migration failed closed (`prod credentials not provided`), Deliveries skipped, `migration_status=failed` |
| [41575198199](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41575198199) (`d1_migrations: false`) | **Skipped Migration does not block Delivery** | `Database Migration` skipped; Delivery (native-branch) and (native-dev) both started (Deploy failed closed on credentials, Smoke reported `none`); `migration_status=none` |
| [41575202262](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41575202262) (`wrangler.two.toml`, Native Branch + persistent Dev on distinct D1s) | Single-DB rule on a real runner | Plan failed: `d1_migrations: the enabled targets name 2 distinct physical D1 databases: scratch-preview-db (native-branch) vs scratch-dev-db (persistent-dev) ... (nothing was changed)`; Migration and all Delivery calls skipped |
| [41575755238](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41575755238) (`pipeline_phase: deploy` then `verify`) | `pipeline_phase` compatibility; the verify call never migrates | Deploy-phase plan, Migration skipped (no D1), Deploy failed closed on credentials; the verify call planned from the real `phase_state`, `Database Migration` skipped, Smoke ran |
| [41575759173](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41575759173) (hand-built v1 `phase_state`) | Backward-compatible hand-off | success; Migration skipped, Production Smoke ran from the hand-off |

YAML anchors (`&delivery_permissions`, `&delivery_secrets`, `&fetch_ci_tools`) were accepted by the GitHub parser in all runs; the permission ceilings did not produce a `startup_failure`.
**Not demonstrated and not claimed:** a live D1 migration, publish, smoke against a live Worker, rollback, and Environment approval gates (no credentials / unprotected scratch Environments).

### Final-SHA verification (implementation `8ad4d4029b42f173dec5b6c39ddc8738fc6bf0a8`)

* Fast + Full profiles, ShellCheck and `actionlint 1.7.12` pass at this SHA (PR CI [41595263650](https://github.com/firstsun-dev/.github/actions/runs/41595263650); local re-run: 561 contract + 420 chain checks).
* **Stale-migration gate on real runners:** [41598319442](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598319442) runs the real `cli.mjs migration-gate` entrypoint from `8ad4d40` on the org `linux` runner and `ubuntu-latest` with a validated descriptor and synthetic handoffs. `stale=true`, `stale="true"`, stale + valid metadata, empty, malformed, `none`, owner mismatch, D1 mismatch and a failed Migration job all exit `1` with no call to `wrangler`/`gh`/`curl`/`pnpm`/`npx` (PATH shims record none); the valid and `d1_migrations=false` controls exit `0`. The full A → B → A race is covered by the chain regression only, not reproduced on Actions.
* Cross-repository scratch scenarios, every nested `$/` reference and the public workflow resolved to `8ad4d40`, per-scenario run links and expected-failure classification: [cf-worker-v2-refactor.md](cf-worker-v2-refactor.md#final-sha-merge-readiness-validation-implementation-8ad4d4029b42f173dec5b6c39ddc8738fc6bf0a8).
* Single-D1 rule against the real consumers: the planner was run over all eight consumer calls × seven event/ref scenarios; none plans two distinct physical D1s.
* Not demonstrated: a live migration/publish/smoke/recovery, Environment approval, GHES, a runner older than 2.336.0.

## Known limitations

* **Hosted `ubuntu-latest` runners lack `pnpm`** (resolved for validation by using the org's provisioned `linux` runners; see the evidence above). `actions/setup` with `setup-pnpm: 'false'` (used by every validation job and the engine) assumes a runner image with pnpm
  baked in (the org's `linux` runners). On `ubuntu-latest` the Validation jobs fail with `pnpm: command not found`, so a full real chain
  (Gate → Plan → Migration → …) cannot be exercised on a hosted runner. This is pre-existing behaviour and unchanged by this fix.
* A caller of the public workflow must grant at least `contents: write` (Release job), `deployments: write` and `pull-requests: write`
  (ceiling of the called jobs), as before.

* Locks are repository-scoped; a D1 shared across repositories must be serialized by the owner (documented on `d1_lock_scope`).
* The compiled-asset scan ("an artifact embeds another target's callback/issuer/origin") is **not** implemented; the build-env checks above and the
  post-publish URL assertion are. Consumers must keep `PUBLIC_*` / `VITE_*` / `IDP_URL` in `build_env_*`.
* `wrangler preview --name dev` custom-domain attachment is unverified and not assumed; custom-domain Preview hosting needs `preview_url_suffix`.
* Preview URL derivation depends on the Workers subdomain API reporting `preview_url_suffix`. If the API is unreadable and no suffix / declared URL
  is supplied, Native Branch / Native Dev fail before the build (by design) rather than build for a guessed URL.
* **Single-DB rule (compatibility restriction).** One pipeline migrates at most one physical D1. A caller that enables targets with distinct D1s in one
  invocation (typically Native Branch on `[previews]` plus `shared_dev_on_branch_push` on a different `[env.dev]` D1, with `d1_migrations: true`) now fails the plan
  with a message naming both databases. Nothing is changed before it fails. The consumer plan (blog, innovation-apps, heaven-monorepo, anas-mcp, heaven-www) does not
  need this, but each caller must be confirmed before the `@v2` move; the rollout stays blocked on that confirmation.
* The Migration job runs in the owner's Environment, so on main the `<app>/prod` approval is requested **before** the read-only validation steps as well as before the migration.
