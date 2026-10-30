# CF Worker v2 — unified target-driven delivery (shared-engine contract)

Spec: `openspec/changes/unify-cf-worker-v2-delivery-targets`. This document is the **frozen Phase 1 contract** consumers build against.
It supersedes the job topology in `cf-worker-v2-refactor.md` ("Job DAG", "Shared Dev job order"); the engine, security rules, ledger
and recovery rules described there still apply. `_cf-worker-v2.yml` remains the only public entrypoint. v1 / v1.5 and the floating `@v2`
tag are untouched by this change.

## Topology

```
Lint ‖ Typecheck ‖ Unit ‖ Integration ‖ Build Validation ‖ E2E        (unchanged, independent)
                         │
                 Validation Gate        every CONFIGURED job succeeded, every unconfigured one skipped, else fail closed
                         │
                  Delivery plan         enabled kinds of this event + smoke-mode validation + D1 plan (pure data, no credentials)
                         │
        Migration (matrix: one leg per DISTINCT physical D1)
                         │
        Deploy    (matrix: one leg per enabled kind; each builds for ITS OWN URL / build env and publishes that artifact)
                         │
        Smoke     (matrix, full | basic | skip)
                         ├─ success ──► Release (Production, main only, requires Verified)
                         └─ failure ──► Rollback (matrix; only the failed kind; guarded)
```

Kinds: `production`, `native-branch` (per-branch Native Preview), `native-dev` (the stable Native Preview named `dev`),
`persistent-dev` (the separate `[env.dev]` Dev Worker; Notification Hub). Only enabled kinds exist as matrix legs, so there are no
permanently skipped per-target jobs. No leg waits for another kind's Deploy / Smoke / Rollback; a Deploy leg waits only for the Gate,
the plan, and the Migration **leg that serves its own D1** (`mx_<kind>` must be `success`).

| Event | Kinds |
| --- | --- |
| trusted push to `main` | `production` |
| trusted push to another branch | `native-branch` if `preview_on_branch_push`; `native-dev` if `shared_dev_native_preview_on_branch_push`; `persistent-dev` if `shared_dev_on_branch_push` |
| protected `workflow_dispatch` persistent-dev | the existing manual `persistent-dev` job (unchanged) |
| PR / delete / tag | none (lifecycle handler only) |

Rejected (plan fails closed, `config-guard` also reports): `production-only` with any Dev channel; `shared_dev_on_branch_push` together with
`shared_dev_native_preview_on_branch_push`; unknown `smoke_mode` / `smoke_mode_overrides`; an unknown kind in a `phase_state` hand-off.

## Inputs

All pre-existing inputs are unchanged (names, types, defaults). New, all optional and default-off:

| Input | Default | Meaning |
| --- | --- | --- |
| `shared_dev_native_preview_on_branch_push` | `false` | Native Dev: every eligible branch push updates Native Preview `dev` (host = `preview_worker_name` or `app_name`, `[previews]` bindings, **not** `[env.dev]`). |
| `build_env_shared_dev_native_preview` | `""` | Build-time `KEY=VALUE` env of the Native Dev build. Never falls back to another kind's env. |
| `app_url_shared_dev_native_preview` | `""` | Optional canonical Native Dev URL: exported to the build as `CFV2_TARGET_URL`; the URL Cloudflare returns must equal it (else the publish is rejected); used as the smoke target. |
| `smoke_mode` | `full` | `full` = built-in probe + `smoke_cmd` (current behaviour); `basic` = content-level HTTP/health only (no `smoke_cmd`); `skip` = no request. |
| `smoke_mode_overrides` | `{}` | JSON keyed by kind, e.g. `{"native-dev":"basic"}`. |

Engine-owned build variables (reserved, cannot be set from caller env): `CFV2_DELIVERY_KIND`, `CFV2_TARGET_URL`; `VITE_APP_ENV` is
`prod` / `preview` / `dev` (`dev` for **both** `native-dev` and `persistent-dev`).

## Outputs

Pre-existing outputs keep their meaning: `deploy_*`, `version_id`, `previous_version_id`, `migration_status`, `rollback_result`
describe the Production / per-branch Preview delivery; `dev_*` describe `persistent-dev`; `deploy_gate_blocked`; `phase_state`.
Added: `native_dev_url`, `native_dev_verified_url`, `native_dev_published_unverified`, `native_dev_verified`, `native_dev_smoke_level`.
`deploy_smoke_level` / `native_dev_smoke_level` may now be `skipped`. `phase_state` is opaque: pass it unchanged to the verify call.

## Behaviour

* **Per-target builds.** Every Deploy leg checks out the validated SHA, imports its own build env, builds, audits its generated Wrangler
  config and publishes that artifact. A build env that contains another target's public URL (`app_url_prod`, `app_url_dev`,
  `app_url_shared_dev_native_preview`) is rejected before any build.
* **Migration.** The plan groups kinds by `account_id/database_id` of the source config: Branch + Native Dev on one preview D1 migrate it
  **once**; distinct D1s migrate independently; D1-less targets skip the stage. A Preview/Dev kind mapped to the Production D1 fails the
  plan. Before publish each leg re-checks the built config still binds the audited D1. A failed migration blocks only the deliveries on
  that D1. D1 is never rolled back. Same-repository serialization is the workflow-level concurrency group (`cfv2-nonprod-d1-<scope>` /
  `cfv2-shared-dev-<app>` also cover Native Dev); a D1 shared with **another repository** is not locked (GitHub limitation, see limits).
* **Native Dev.** Fixed name `dev`, own ledger environment `<app>/native-dev/dev`, own GitHub Environment `<app>/native-dev`, never
  advertised on PRs. The name `dev` is reserved: with Native Dev on, a branch named `dev` gets Preview `b-dev`; Preview GC never collects `dev`.
* **Smoke / recovery.** `skip`: no request, `deployed_and_verified=false`, `verified_url` empty, `smoke_level=skipped`, ledger `inactive`
  (never known-good), no rollback, no Release. `basic`: Access-redirect = liveness only = Published/Unverified. Recovery is target-owned:
  `native-dev` / `persistent-dev` redeploy the last known-good deployment of the same ledger unless a newer one exists; Production keeps its
  fail-closed policy; a blocked cross-service gate never rolls back.
* **Release** requires the Production Smoke to be content-verified and no Rollback to have run.

## Verification evidence (local, mocked Cloudflare/GitHub)

See the PR description for the exact counts of the final run. Suites: `test-workflow-contract.py` (structure, gating matrix, credential
isolation, concurrency, interface compatibility), `test-jobs.py` (executes the real jobs and composite engine per matrix leg against
stubs: all four kinds, stale runs, smoke modes, migration dedupe/isolation, recovery, deferred `pipeline_phase`), `delivery.test.mjs`,
`lib.test.mjs`, `test-*.sh`, `gc.test.mjs`, shellcheck, actionlint 1.7.12.

## Known limitations

* No local GitHub runner: matrix-output merging and `job.status` are validated by the simulator and actionlint, then by the cross-repository
  scratch run recorded in the PR.
* The compiled-asset scan ("an artifact embeds another target's callback/issuer/origin") is **not** implemented; only the build-env URL
  isolation and the post-publish URL assertion are. Consumers must keep `PUBLIC_*` / `VITE_*` / `IDP_URL` in `build_env_*`.
* Locks are repository-scoped; a D1 shared across repositories must be serialized by the owner (documented on `d1_lock_scope`).
* `wrangler preview --name dev` custom-domain attachment is unverified and not assumed.
