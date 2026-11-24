# Cloudflare Worker CI/CD v2 — stages and targets

Reusable workflow `.github/workflows/_cf-worker-v2.yml` (+ `actions/cf-preview`). Current contract index: `openspec/specs/cloudflare-worker-delivery/spec.md`; shipped v2 baseline: `openspec/changes/redesign-cloudflare-worker-cicd-v2/specs/cloudflare-worker-delivery/spec.md`. Follow-on v2 proposals are tracked under `openspec/changes/`.
The legacy v1 workflow (`_cf-worker-template.yml`) and its branch-alias action were removed on 2026-11-16 after checking organization callers. This page replaces the previous v2 guide; the older design records
(`cf-worker-v2-refactor.md`, `-unified-delivery.md`, `-rollout.md`) describe the superseded four-kind model and are kept as history only.

## Model

Three logical **stages**; the caller chooses the commands, the platform owns orchestration, gates and delivery.

| Stage | What | Parallelism |
| --- | --- | --- |
| **Verify** | `verify_tasks`: fast checks (lint, check, build, unit, fast integration, ...) | one matrix leg per task |
| **Test** | `test_tasks` (+ optional E2E): costly checks (heavy integration, contract, E2E, ...) | one matrix leg per task, concurrent with Verify |
| **Delivery** | Plan → Migrate → Deploy (builds locally) → Smoke → conditional Rollback → Release | one **target**, one **driver** |

```text
CI:        Plan -> [Verify tasks || Test tasks || E2E] -> Gate (fail closed)        (no `needs` between Verify and Test)
Delivery:  Plan ---------------------------------+            (Plan runs concurrently with CI; nothing is mutated before the CI Gate)
           CI Gate -------------------------------+--> Migrate (production/dev only) -> Deploy (build + publish) -> Smoke -> Rollback? -> Release (main only)
```

Targets: `production`, `preview`, `dev`. Drivers: `worker` (production, or a real `env.dev` Worker) and `native-preview` (PR Preview, stable Dev).
`production → worker`, `preview → native-preview`, `dev → native-preview | worker`. An event selects **at most one** delivery.

## Events

| Event | Verify | Test | Delivery |
| --- | --- | --- | --- |
| feature push | yes | no | none |
| same-repo PR (opened / synchronize / reopened) | yes, on the merge candidate | yes | `preview` (only with `preview_policy: pr`), after the CI Gate |
| fork PR | only on the approved GitHub-hosted runner `ubuntu-24.04` (a constant, not an input) | same | never |
| `merge_group` | yes | yes | none |
| push to `main` | yes | no (ran pre-merge; protect `main` with the PR Gate) | `production` |
| `workflow_dispatch`, `dispatch_target: dev`, ref == `approved_dev_ref` | yes | no | `dev` (the configured `dev_driver`) |
| PR closed | no | no | lifecycle: retire the PR Preview ledger (the Preview is kept) |

A manual dispatch can never deploy Production; no automatic event ever reaches the stable Dev. Caller triggers: `push`, `pull_request`
(`opened, synchronize, reopened, closed`), `workflow_dispatch` (when Dev is enabled), `merge_group` (when a merge queue is required).

## Inputs that define behavior

```yaml
with:
  app_name: blog
  verify_tasks: >-
    [{"id":"lint","cmd":"pnpm run lint"},{"id":"check","cmd":"pnpm run check"},
     {"id":"build","cmd":"pnpm run build"},{"id":"unit","cmd":"pnpm run test:unit"}]
  test_tasks: >-
    [{"id":"integration","cmd":"pnpm run test:integration"}]
  test_e2e_cmd: pnpm run test:e2e:ci        # optional E2E executor (own setup/build, browser variants, report artifact)
  build_cmd: pnpm run build                  # the DELIVERY build (target URL / env), independent of Verify
  preview_policy: pr                         # none | pr
  dev_driver: native-preview                 # none | native-preview | worker
  approved_dev_ref: refs/heads/main          # required with dev_driver
  dispatch_target: ${{ github.event.inputs.target }}   # '' | dev
```

* **Tasks**: `{"id","cmd"}` only; ids unique per stage (`[a-z0-9-]`, ≤ 40 chars), commands non-empty, ≤ 12 tasks. Malformed lists fail the CI Plan before any command runs.
  An empty list is valid (an empty Test stage still yields a passing Gate). Commands run from `app_path`.
* **Gate**: job `Gate` (appears as `<caller job> / CI / Gate`). Every expected job must `success`, every unexpected one must be `skipped`, the Plan must succeed;
  failure, cancellation, unexpected skip or a missing result is red. Make **the Gate of the CI call** the required check; the caller job id keeps it unique per app in a monorepo.
* **Preview** (`preview_policy: pr`): one Native Preview `pr-<number>` per same-repo PR, delivering the PR head commit after CI passed. A Preview **never migrates a remote D1**
  (`d1_migrations` is ignored for it); test SQL locally. URL = what Cloudflare publishes (`<name>-<worker>.<subdomain>.workers.dev`), never guessed.
* **Dev**: a stable `dev` Native Preview, or (`dev_driver: worker`) a real `env.dev` Worker — required for Queue consumers / Cron triggers. Updated only by an approved manual promotion;
  protect the `<app>/dev` / `<app>/native-dev` GitHub Environment with required reviewers. Dev settings: `app_url_dev`, `build_env_dev`, `dev_build_cmd`, `dev_smoke_cmd`,
  `dev_worker_name`, `dev_wrangler_env`, `dev_binding_allow`, `dev_deny_resources`.
* `smoke_mode_overrides` is keyed by target (`production|preview|dev`).

* **Smoke configuration**: `smoke_vars` is a PUBLIC, non-sensitive input (`KEY=VALUE` lines; credential-looking names such as `*_KEY`, `*_TOKEN`, `*SECRET*`, `*PASSWORD*`, `PROBE_HEADERS`, `CF_ACCESS_*` are refused at init). Credentials (Cloudflare Access service token, `PROBE_HEADERS`, anything the application smoke needs to authenticate) go in the **`SMOKE_ENV` secret**
  (`KEY=VALUE` lines), which only the trusted Smoke job (and the Production rollback re-probe) receives and masks per value: it is never part of `inputs`, `inputs_json`, Plan outputs, Build, Deploy or a Preview's PR-controlled App Smoke. The former `smoke_env` input is removed.
Removed (no compatibility shim): `lint_cmd`, `typecheck_cmd`, `test_unit_cmd`, `test_integration_cmd`, `deployment_strategy`, `preview_on_branch_push`,
`shared_dev_on_branch_push`, `shared_dev_native_preview_on_branch_push`, `persistent_dev_ref`, `build_env_shared_dev_native_preview`,
`app_url_shared_dev_native_preview`, the per-kind outputs (`dev_*`, `native_dev_*`). Outputs now describe the one delivery:
`deploy_target`, `deploy_driver`, `deploy_url`, `deploy_verified_url`, `deploy_published_unverified`, `deploy_verified`, `deploy_smoke_level`,
`version_id`, `previous_version_id`, `migration_status`, `rollback_result`, `deploy_gate_blocked`, `phase_state` (opt-in `pipeline_phase`).

## Build / Publish trust boundary

```text
untrusted code ──► Deploy job, `build` step (no Cloudflare token / write token in its env; same-repository PRs: the caller's EPHEMERAL `runner` pool; fork PRs: GitHub-hosted)
                      deps + target build + config audit + `wrangler deploy --dry-run --outdir` + package
                      └─► local bundle in $RUNNER_TEMP, NOT uploaded  (manifest.json · wrangler.json[main→worker/, no_bundle, no `build`] · worker/ · assets/)
                                       │  same job, later steps
 trusted tooling ──► publish / migrate steps (step-level credentials; Environment approvals)
                      verify identity + digest + structure ─► re-audit resource bindings ─► stale check ─► pinned Wrangler publishes ONLY the artifact
                      (the publish step installs and builds nothing)
```

* Publisher inputs it trusts: its own Plan descriptor, GitHub context, caller inputs and the event payload - never the manifest's claims. The manifest is cross-checked
  (repository, app, target, driver, slot, run id + attempt, `source_sha`, `tested_sha`, `artifact_sha == tested_sha`, canonical URL). The Preview host Worker, Preview name and
  Worker name for Dev are set by the publisher. The digest detects corruption and mix-ups, **not** a malicious Build: the defence is that the Build holds no credential and
  the publisher re-audits the packaged config (`preview_binding_allow` / `preview_deny_resources`, Dev / Production audits, structural rules) before anything runs on Cloudflare.
* The Build receives **no secret**: `PREVIEW_SECRETS` is not passed to it (a PR's build command could read it); build-time values must be non-secret `build_env_*`. Audit / publish mode
  (flat vs a Dev Worker's named environment), the Production Worker name and the Preview host Worker are derived by the publisher, never from the manifest. A packaged config may not
  carry `build`, `site`, `containers`, `alias`, `tsconfig`, `find_additional_modules`, `base_dir`, `dev`; the artifact may contain only `manifest.json`, `wrangler.json`, `worker/**`, `assets/**` (no hidden files).
* There is no cross-job artifact: "Re-run failed jobs" re-runs the Deploy job, which rebuilds.
* Application code (`smoke_cmd`) never receives GitHub or Cloudflare tokens; the credential-bearing Production rollback re-smokes with the built-in probe only and installs no application dependency.
* **Revision contract**: `source_sha` = PR head, `tested_sha` = the merge candidate CI tested, `artifact_sha` = what the build step checked out (must equal `tested_sha`), `deployed_sha` = recorded
  in the ledger = `tested_sha`. A PR Preview is built from and published as the tested merge candidate. Publish proceeds only while the PR is open, its head is still `source_sha` and
  GitHub's merge candidate is still `tested_sha` (a newer synchronization or a moved base makes the run stale). Main / Dev publish the pushed / approved commit under the existing branch-tip guard.
* Non-production has **no automatic recovery** (decision pending owner acceptance): redeploying an older Preview / Dev would require rebuilding it in a credential-bearing job, and no verified artifact is retained. A failed Smoke marks it UNVERIFIED; fix forward.
* A Preview's `smoke_cmd` (PR-controlled) runs only in the separate credential-free `app-smoke` job; the credential-bearing Smoke job runs the trusted probe and requires its result. Production rollback
  re-deploys a stored Worker version (no build) and never reverts D1.

## Build inside Deploy (no Actions artifact)

GitHub Free caps Actions artifact storage, so there is no separate Build job and nothing is uploaded or downloaded between jobs. The target is built where it is used:

* **Deploy**: `build` (dependency install, target build, effective-config audit, `wrangler deploy --dry-run --outdir`, packaging to `$RUNNER_TEMP/cfv2-artifact`) then `publish` (`artifact-verify`, packaged-config audit incl. `dev-policy.mjs`, stale guard, `versions upload` + `versions deploy`). The job env holds **no credential**; `GH_TOKEN` / `CLOUDFLARE_*` are **step-level env on the publish step only** (the engine's build stage also refuses them).
* **Migrate** (only with `d1_migrations`): builds nothing and uses no artifact. Read-only `precheck` (verified no-op when nothing is pending), then `migrate` if pending; credentials are step-level. The D1 is the one the SOURCE config names, checked against the plan's expected physical D1 before any remote call. That the BUILT config binds the same D1 is proven by Deploy's `publish` stage (`audit-d1`) before anything is published, so a mismatch fails Deploy after the migration but never publishes.

Consequences: build code runs on the runner that later holds the token (accepted: private repos, trusted `main`); a PR's Deploy runs on the caller's ephemeral `runner` pool (fork PRs never deliver); the build no longer overlaps CI (Deploy / Migrate wait for the Gate); "Re-run failed jobs" rebuilds. `check-workflows.py` pins: no job-level credential, credentials only on precheck / migrate / publish steps, stage order, no Actions artifacts.

### Artifact retention (all callers)

The delivery artifact no longer exists. Failure-only Smoke and E2E reports are kept **1 day** (were 7 / 3).
The legacy v1 template (`_cf-worker-template.yml`) is not touched.

## Safety invariants (kept)

Failed CI never deploys · Preview/Dev can never bind Production D1/R2/Queue (audit + deny lists) · the migration runs against the audited physical D1, a failed, stale or
mismatched migration never authorizes a publish (read-only precheck first; no pending → verified no-op without a build) · stale runs never overwrite newer revisions (A→B→A) ·
Smoke verifies the exact published URL; an Access redirect is liveness only (`published_unverified`, no Release, never known-good) · rollback is target-owned and guarded, a Worker
rollback never reverts D1 · Release only after a verified Production delivery · fork PRs never reach a privileged runner or secrets · Required Checks cannot pass through an unexpected skip.

## Internal persistence slot

The engine keys GitHub Environments (`<app>/prod`, `<app>/preview`, `<app>/native-dev`, `<app>/dev`) and the known-good ledger by a slot derived from target × driver
(`delivery.mjs` `SLOT`). It is not a public input or output; it exists so existing Environment protection rules and known-good records survive the cutover.
A PR Preview ledger is `<app>/preview/pr-<number>`.

## Pipeline self-tests

`scripts/cf-preview/test.sh [static|unit|safety|full]` — three parallel suites, one `Pipeline Gate` (`.github/workflows/cf-worker-v2-ci.yml`).
No GitHub-expression or scheduler emulation: GitHub's real behavior is proven by the canary on `cf-v2-scratch-caller` against the candidate SHA.
