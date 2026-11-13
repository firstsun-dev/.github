# Cloudflare Worker CI/CD v2 — stages and targets

Reusable workflow `.github/workflows/_cf-worker-v2.yml` (+ `actions/cf-preview`). OpenSpec: `openspec/changes/simplify-cf-ci-stages-and-dev-delivery`.
v1 (`_cf-worker-template.yml`, `@v1`) is independent and unchanged. This page replaces the previous v2 guide; the older design records
(`cf-worker-v2-refactor.md`, `-unified-delivery.md`, `-rollout.md`) describe the superseded four-kind model and are kept as history only.

## Model

Three logical **stages**; the caller chooses the commands, the platform owns orchestration, gates and delivery.

| Stage | What | Parallelism |
| --- | --- | --- |
| **Verify** | `verify_tasks`: fast checks (lint, check, build, unit, fast integration, ...) | one matrix leg per task |
| **Test** | `test_tasks` (+ optional E2E): costly checks (heavy integration, contract, E2E, ...) | one matrix leg per task, concurrent with Verify |
| **Delivery** | Plan → Migrate → Deploy → Smoke → conditional Rollback → Release | one **target**, one **driver** |

```text
CI:        Plan -> [Verify tasks || Test tasks || E2E] -> Gate (fail closed)        (no `needs` between Verify and Test)
Delivery:  Plan -> Migrate (production/dev only) -> Deploy -> Smoke -> Rollback? -> Release (main only)
```

Targets: `production`, `preview`, `dev`. Drivers: `worker` (production, or a real `env.dev` Worker) and `native-preview` (PR Preview, stable Dev).
`production → worker`, `preview → native-preview`, `dev → native-preview | worker`. An event selects **at most one** delivery.

## Events

| Event | Verify | Test | Delivery |
| --- | --- | --- | --- |
| feature push | yes | no | none |
| same-repo PR (opened / synchronize / reopened) | yes, on the merge candidate | yes | `preview` (only with `preview_policy: pr`), after the CI Gate |
| fork PR | only on `fork_runner` (else the CI Plan fails closed) | same | never |
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

Removed (no compatibility shim): `lint_cmd`, `typecheck_cmd`, `test_unit_cmd`, `test_integration_cmd`, `deployment_strategy`, `preview_on_branch_push`,
`shared_dev_on_branch_push`, `shared_dev_native_preview_on_branch_push`, `persistent_dev_ref`, `build_env_shared_dev_native_preview`,
`app_url_shared_dev_native_preview`, the per-kind outputs (`dev_*`, `native_dev_*`). Outputs now describe the one delivery:
`deploy_target`, `deploy_driver`, `deploy_url`, `deploy_verified_url`, `deploy_published_unverified`, `deploy_verified`, `deploy_smoke_level`,
`version_id`, `previous_version_id`, `migration_status`, `rollback_result`, `deploy_gate_blocked`, `phase_state` (opt-in `pipeline_phase`).

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
