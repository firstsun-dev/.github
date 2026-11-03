# CF Worker v2 — in-place refactor: interface, concurrency design, migration guide

`.github/workflows/_cf-worker-v2.yml` was refactored **in place** (breaking, no `v3`). Spec: `openspec/changes/refactor-cloudflare-worker-cicd-v2` (PR #57). Shipped v2.x behavior stays documented by `docs/cf-worker-v2.md` / PR #56 as the historical record; **the interface below replaces it**. The floating `@v2` tag has not moved and no consumer has been changed.

> **Topology superseded:** the job set below (Migration → Deploy → Smoke → Rollback + Shared Dev trio) was replaced by the matrix topology in [cf-worker-v2-unified-delivery.md](cf-worker-v2-unified-delivery.md). Engine, security and recovery rules here still apply.

## Phase 1 simplification (PR #89 refactor) — current structure

Goal: less orchestration, no duplicated configuration, no needless set-up, **same behaviour, same public interface, same security boundaries**.
The full-regression and real-runner evidence is at the end of this section.

### Before / after

```
BEFORE  _cf-worker-v2.yml (1,081 lines, 17 jobs)                 AFTER  _cf-worker-v2.yml (769 lines, 11 jobs)
  lint typecheck unit integration build e2e  (6 jobs, each           ci  ──► _cf-worker-ci.yml: lint ‖ typecheck ‖ unit ‖ integration ‖
        its own .cf-tools checkout, 5 near-identical bodies)                build ‖ e2e  ─► Validation Gate  (output validation=passed)
  gate                                                                │
  plan  (checkout .cf-tools + npm ci)                               plan  (uses: $/actions/cf-tools, install)   needs: [ci]
  migration (checkout .cf-tools)  needs: gate, plan                 migration (ONE job, unchanged)              needs: [plan]
  delivery_production | _native_branch | _native_dev | _persistent_dev    delivery_* ×4 (thin calls)              needs: [plan, migration]
        (uses ./.github/workflows/_cf-worker-delivery.yml,                (uses $/.github/workflows/_cf-worker-delivery.yml)
         tools_ref forwarded, needs: gate, plan, migration)             release (Production only)
  release   config-guard                                                config-guard
  lifecycle (inline, 5 steps)                                           lifecycle      ─► _cf-worker-lifecycle.yml
  persistent-dev (inline, two contracts)                                persistent-dev ─► _cf-worker-persistent-dev.yml
```

| File | Before | After |
| --- | ---: | ---: |
| `_cf-worker-v2.yml` (public workflow) | 1,081 | 769 |
| `_cf-worker-delivery.yml` | 320 | 282 |
| `_cf-worker-ci.yml` (new) | – | 263 |
| `_cf-worker-lifecycle.yml` (new) | – | 93 |
| `_cf-worker-persistent-dev.yml` (new) | – | 109 |
| `actions/cf-tools` (new) | – | 26 |
| `_cf-worker-preview-gc.yml` | 113 | 104 |
| **pipeline workflows (v2 + delivery + ci + lifecycle + persistent-dev)** | 1,401 | 1,516 |

Total lines grew by 8 % (each extracted workflow repeats its own `on:`/`permissions:`/`inputs:` header); what shrank is the **public** workflow (−29 %), the
number of places where behaviour is defined once instead of five times, and the set-up: **0** checkouts of this repository remain (before: 6 definitions, up to
11 executed in one feature-branch push: 5 CI checks + E2E + plan + Migration + Deploy + Smoke + Rollback). Defined jobs: 20 → 23 (the three new ones are the
extracted event handlers and the CI call; jobs *executed* per event are unchanged). **No runtime improvement is claimed**: the real-runner pairs (same scenario before and after,
n = 1 each) differ by less than runner-queue noise (plan 16 s → 18 s, Migration 15 s → 16 s, Build Validation 16 s → 17 s); a checkout of a sparse tree takes about a second.
`cf-deploy` keeps its 391 lines (only its `setup` reference and header comment changed).

### Duplicated sections removed

* Six copies of the tool checkout + `cf-ci-check` call (one anchor + five aliases) → one reusable CI workflow; the `Validation Gate` script moved with it (and now also emits the verdict).
* The 3.5 KB top-level `concurrency.group` expression existed **three times** (deploy call, verify call, `phase_state.group`) → once; see "Concurrency and phase_state".
* `phase_state.d1` (a per-kind D1 map the descriptors already carry) is gone; `d1_migrations` is no longer forwarded to Delivery (it was unused there).
* Four `Fetch delivery tooling` steps in Delivery/Migration/persistent-dev, the Preview lifecycle's and the plan's checkout + `npm ci` + `TOOLS_DIR` env → `uses: $/…`.
* `tools_ref` plumbing (a required input forwarded to every call and compared in the verify identity check) → a deprecated, ignored input.

### `$/` (self-repository reference) coverage

GitHub changelog 2026-07-30: `uses: $/path` is *this workflow's repository at the exact commit that is running*, with no checkout (needs runner ≥ 2.336.0; not on GHES; no `@ref` suffix; docs:
"always resolves against the repository of the file it appears in, not the repository that called it" — which is exactly what a cross-repository reusable workflow needs).

| Was | Now |
| --- | --- |
| `actions/checkout` of `firstsun-dev/.github` @ `tools_ref` into `.cf-tools` + `uses: ./.cf-tools/actions/cf-deploy` (Delivery ×3, Migration ×3, persistent Dev) | `uses: $/actions/cf-deploy` |
| same + `./.cf-tools/actions/cf-ci-check` (CI ×5) | `uses: $/actions/cf-ci-check` (inside `_cf-worker-ci.yml`) |
| sparse checkout + `parse-env.mjs` (E2E), checkout + `npm ci` + `TOOLS_DIR` (plan, lifecycle, GC) | `uses: $/actions/cf-tools` (new, 25 lines: exports `TOOLS_DIR`, optional `npm ci`) |
| `uses: ./.github/workflows/_cf-worker-delivery.yml` | `uses: $/.github/workflows/_cf-worker-delivery.yml` (also `-ci`, `-lifecycle`, `-persistent-dev`) |
| `firstsun-dev/.github/actions/{setup,mailpit}@v1` inside the pipeline | `$/actions/{setup,mailpit}` (byte-identical to `v1`, so no behaviour change; exact-version coupling instead of a floating pin) |

Sibling files are **not** assumed to land in `$GITHUB_WORKSPACE`. The runner downloads this repository at the running SHA; each action finds its scripts relative to itself
(`$GITHUB_ACTION_PATH/../cf-preview`, already how `cf-deploy` and `cf-ci-check` worked), and `cf-tools` exports that path for the jobs that run the scripts directly. The application checkout
is unchanged; nothing from this repository is ever in the application's workspace (the old `find .cf-tools … -delete` workaround in `cf-ci-check` is gone).
`tools_ref` is still accepted (required → optional, default empty) and ignored; a caller passing a non-existent ref keeps working (scenario r6 below).
**New environmental requirement:** every runner that executes a pipeline job (the `runner` input) must be ≥ 2.336.0; the org `linux` runners are 2.338.0 and GitHub-hosted images 2.338.2. A caller on an older self-hosted runner fails at "Prepare all required actions".
`actionlint` 1.7.12 does not know `$/`; `.github/actionlint.yaml` ignores exactly those two message formats and the contract test validates every `$/` reference itself (static, no `@ref`, target exists).

### CI validation workflow

`_cf-worker-ci.yml` runs the six checks as independent jobs without `needs` between them (asserted by the contract test; in the real run r7 below all seven jobs — E2E counts twice for two variants — started within about one second, the last one after waiting for a free runner), reusing `actions/cf-ci-check`
(E2E keeps its own set-up and test build, the variants matrix and the optional Mailpit). The **Validation Gate** (`if: always()`, so it fails instead of being skipped) accepts only
`success` for a configured job and `skipped` for an unconfigured one; only then it writes `validation=passed`, which the workflow exposes. The public workflow calls it **once**, with the
skip rules in one place (not on `delete`, not on the persistent-dev dispatch, not in a verify call, and not on a same-repo `pull_request` — the push run of the same SHA covers it; fork PRs run it credential-free, so CI is never duplicated).
The Delivery plan runs only when `needs.ci.result == 'success' && needs.ci.outputs.validation == 'passed'` (a skipped, failed or cancelled call, or a green call without the verdict, blocks it).
Migration and the four Delivery calls depend on the plan only: a plan can only succeed after a passed CI (or in a verify call), so they no longer repeat the CI condition.
Build Validation stays separate from the target build of Deploy. **Check names change:** the checks now appear as `<caller job> / CI / Lint` … `<caller job> / CI / Validation Gate` (was `<caller job> / Lint`). Callers with *required status checks* must update them; for the two consumer repositories that could be
checked read-only (`heaven-monorepo`, `innovation-apps`) this cannot matter — GitHub rejects the branch-protection and ruleset API on their plan, so they have no required checks; the other consumers were not inspected.

### Migration (unchanged guarantees)

Exactly one remote-D1 job per pipeline, same order, same Environment protection: `Validation → Plan → Database Migration (resolve D1 · validate bindings · audit effective config · stale-SHA guard · apply once) → Delivery`.
It stays inline in the public workflow: extracting it would add a workflow-call layer and re-plumb the descriptors, the inputs blob and the credentials without removing a stage or
weakening anything, which the brief rules out. Preview/Dev still can never reach the Production D1 (plan + engine audit); the shared-D1 group still serialises Workers sharing one D1; distinct physical D1s are still rejected by the plan.

### Delivery engine and the matrix question

One engine (`cf-deploy` → `engine.sh`) and one delivery workflow serve all four kinds, driven by the target descriptor. **The four calls are not a matrix, on purpose:** the public outputs
(`deploy_*`, `native_dev_*`, `dev_*`, `rollback_result`, `phase_state`), the per-kind hand-off of a deferred verify, Migration attribution and the Production Release gate each read *one* kind's outputs; a matrix call exposes only
one unspecified leg's output per key, so keeping them apart would need a results-collector job (a new stage and a new JSON layer). The four 12-line calls share `permissions:` and `secrets:` through YAML anchors (accepted by the GitHub parser in every real run).

### Event handlers

`lifecycle` (PR comment of the verified Preview URL; branch-delete ledger retirement, only if this branch still owns the name and the branch does not exist again; the Cloudflare Native Preview is **kept**) and `persistent-dev`
(Hybrid, protected dispatch of the approved ref, `<app>/dev` Environment; engine in manual mode when `dev_build_cmd` is set, the unchanged legacy two-command contract otherwise) are small reusable workflows. They contain no deployment
implementation: the persistent-dev engine path is the same `$/actions/cf-deploy`. The legacy path stays because `heaven-monorepo` and `innovation-apps` pass `persistent_dev_ref`; it cannot move into the engine without changing its "one command builds and publishes" contract.

### Concurrency and `phase_state`

* GitHub evaluates workflow-level `concurrency.group` before any job, so it cannot read a Plan output. The scheduling-time expression therefore stays in YAML, **once** (previously three copies). Its tested twin is `runLockGroup()` in `delivery.mjs`; the Plan now *emits* that
  canonical value (`group` output), `phase_state.group` is taken from it, and `test-workflow-contract.py` renders the YAML expression against `runLockGroup()` over 512 event/ref/input scenarios.
* **One deliberate semantic note.** A verify call used to schedule under a group *recomputed* from `phase_state.inputs` (a second copy of the expression). It now schedules under `phase_state.group`, the value its own deploy call recorded. A forged or wrong value is rejected by the Plan job (`restoreDescriptors` compares it with `runLockGroup()` of the deploy inputs)
  **before any job that can mutate**, so the worst outcome is a failed verify run that queued in the wrong group; it can never mutate under another lock. Tests: `test-jobs.py` (forged / foreign-family / missing group → Plan fails, nothing smoked or released), contract test (rejection executed against the real validator).
* `phase_state` keeps its shape minus the redundant `d1` map: `{inputs, kinds, deploy, migration, group, descriptors}`. It is produced and consumed inside one run by the same ref, and hand-built legacy blobs still verify (scenario r5).

### Public interface

All 60 inputs and 20 outputs remain with their names, defaults and semantics (diffed against the PR base: **no input or output removed, renamed or re-typed**). Two values changed, both internal: `tools_ref` is now optional and ignored, and the *content* of the opaque `phase_state` output lost its redundant `d1` key (callers pass it through unchanged). Semantics unchanged: `deploy_url`, `deploy_verified_url`, `deploy_verified`, `dev_url`, `native_dev_url`, `migration_status`, `rollback_result`, `phase_state` (opaque), all others.
Recorded for a later phase (not done, would be breaking or out of scope): drop `tools_ref`; collapse the `build_env_*`/`app_url_*` per-channel input families into one `targets` object; give Release its own verified-Production output so callers do not re-derive it; a results collector plus matrix once GitHub aggregates reusable-workflow matrix outputs; make `phase_state` keys kind-indexed so the Delivery workflow can read its hand-off itself.

### Verification

* **Static + contract** (`scripts/cf-preview/test-workflow-contract.py`, 546 checks): YAML parse, job set and `needs` (no serial dependency between the six checks), permission ceilings per called workflow, every `$/` reference static/no-`@ref`/existing,
  call `with:` keys vs called inputs, secrets isolation, event matrix for every job `if`, Validation Gate **executed** over success/failure/cancelled/skipped for each check, Release gate, concurrency expression vs `runLockGroup()` (512 scenarios), forged-group rejection, interface (inputs/outputs/defaults).
* **Behavioural chain** (`scripts/cf-preview/test-jobs.py`, 413 checks, 8 groups, real `run:` steps and the composite engine against stubbed `gh`/`pnpm`/`curl`, each job in a fresh workspace): main push → Production; non-main → Branch Preview; Native Dev; persistent Dev; production-only; Hybrid dispatch; D1 on/off and shared D1; distinct D1s rejected;
  failed Migration blocks every Delivery; each failing validation job blocks Migration and Delivery; smoke success / failure / skip / Access liveness; Production rollback; Preview/Dev recovery; Release only after a verified Production delivery; `pipeline_phase` deploy/verify and the cross-service `upstream_gate`; PR comment and branch-delete lifecycle.
* **CI on the PR** (`cf-worker-v2-ci.yml`, run 41589279882 @ `3bda5fb`): fast profile, chain profile, `shellcheck`, `shellcheck` of the composite scripts, `actionlint` — green.
* **Cross-repository, real runner** — disposable private `firstsun-dev/cf-v2-scratch-caller` calling `firstsun-dev/.github/.github/workflows/_cf-worker-v2.yml@3bda5fb40ddde8091c320702f58f6efe40d6faae`, org `linux` runners (2.338.0), **no Cloudflare credentials, no live deployment**, so every failure below is the designed fail-closed one.
  The runner log shows `Download action repository 'firstsun-dev/.github@3bda5fb…'` for the nested `$/` actions and workflows: they resolved to the called workflow's repository and SHA, not to the caller.

| Scenario (run) | Observed (jobs that ran) |
| --- | --- |
| r7 six checks + 2 E2E variants (41589291655, `feat/x`) | Lint, Typecheck, Unit, Integration, Build, E2E(a), E2E(b) all `success` and started together → Validation Gate → plan → `Deploy` failed closed (`preview credentials not provided`) → Smoke `none`; `d1_migrations: false` ⇒ Migration skipped |
| r8 `lint_cmd: exit 3` (41589295764) | Lint `failure`, the other five still ran and reported independently → Validation Gate `failure` (`LINT finished 'failure'`) → **plan, Migration, every Delivery and Release skipped** |
| r1 Branch Preview + Native Dev + D1 (41589300476, `feat/x`; 41589322177, `main`) | Gate → plan → the single `Database Migration` (Environment of the owner) failed closed on credentials before any remote call; Deliveries skipped; `migration_status=failed`. `main`: `prod credentials not provided` |
| r2 `d1_migrations: false` (41589304686) | Migration skipped, **both Deliveries started** (Deploy failed on credentials, Smoke `none`) |
| r3 two distinct D1s (41589308923) | Plan failed: `… 2 distinct physical D1 databases: scratch-preview-db (native-branch) vs scratch-dev-db (persistent-dev) … (nothing was changed)`; Migration and all Deliveries skipped |
| r6 caller still passing `tools_ref: this-ref-does-not-exist` (41589313763) | identical to r1 — the deprecated input is ignored |
| r4 `pipeline_phase` deploy → verify (41589317795) | deploy-phase plan + Delivery; the verify call's plan accepted the real `phase_state` (group + descriptors) and its Smoke ran; no Migration in the verify call |
| r5 hand-built legacy `phase_state` (41589326325) | success: plan + Production Smoke from the hand-off |
| r9 persistent Dev, legacy contract (41589330341, `val/pd`) | success: `dev_deploy_cmd` / `dev_smoke_cmd` ran (`$/actions/setup` in the nested workflow) |
| r10 persistent Dev, engine contract (41589334806) | `$/actions/cf-deploy` ran; failed closed on `dev credentials not provided` |
| r11 same-repo PR (41589372547), push to `evt/a` (41589358522), branch delete (41589501121) | PR: only `Preview lifecycle` ran (CI/plan/Delivery skipped), success, comment skipped as no verified Preview exists; push: CI → plan → Deploy failed on credentials; delete (default-branch workflow): lifecycle success |

**Not demonstrated, not claimed:** a live D1 migration, publish, smoke against a live Worker, rollback, Environment approval gates (scratch Environments are unprotected), Mailpit, a runner older than 2.336.0, `$/` on GHES, and any runtime improvement.

* **P1 follow-up (stale migration):** fixed after review — see "P1 fix" in [cf-worker-v2-unified-delivery.md](cf-worker-v2-unified-delivery.md). A stale-skipped migration no longer authorizes a publish; a superseded run's Delivery now fails at the Migration Gate instead of ending green.

### Final-SHA merge-readiness validation (implementation `8ad4d4029b42f173dec5b6c39ddc8738fc6bf0a8`)

Everything below was run against exactly `8ad4d40`; no implementation file was changed afterwards (this section is docs only). Nothing was merged, `@v2` was not moved, no consumer or Cloudflare resource was changed.

**Regression (Phase 1).** PR CI [41595263650](https://github.com/firstsun-dev/.github/actions/runs/41595263650) passed at this SHA. Independently re-run locally on the detached checkout of `8ad4d40`: `scripts/cf-preview/test.sh fast` and `full` → `PASSED` (561 contract checks, 420 chain checks in 8 groups), `shellcheck 0.11.0 -x -S warning` over `actions/cf-preview/*.sh scripts/cf-preview/*.sh` + `shellcheck-actions.py` clean, `actionlint 1.7.12` over the seven workflows clean. Required assertions and the test that proves each:

| Assertion | Evidence |
| --- | --- |
| `stale=true` / `stale="true"` never authorizes publishing | contract: `Migration Gate (executed): stale=true / stale='true' / stale=true with otherwise valid metadata blocks the publish` |
| Missing or malformed result fails closed | contract: empty result, invalid JSON, `migration none`, owner mismatch, D1 mismatch, failed / cancelled / skipped-while-required job all block |
| Valid Migration + matching owner and physical D1 succeeds | contract: `a succeeded Migration job with an applied migration of the planned owner on the planned D1 authorizes the publish` |
| `d1_migrations=false` does not block Delivery | contract: `Migration Gate (executed): d1_migrations=false needs no migration result`; chain: `d1_migrations=false … Delivery is NOT blocked` |
| A → B → A cannot bypass | chain group `migration-d1`: `A->B->A: the Delivery sees a FRESH tip but the Migration Gate rejects the stale result BEFORE the build; nothing is built or published` (+ nothing smoked, no rollback, no Release) |
| Failed Migration cannot start publishing | chain: `Migration failure -> the dependent Delivery never starts …, nothing published, Release blocked` |
| Deferred verification never repeats Migration | contract: `Migration: never in a verify call`; real runner r4 below |
| Smoke / Rollback target-isolated | chain: forged / cross-target / tampered `phase_state` descriptors fail the plan closed; Native Dev recovery never touches a Production version |
| Production Release requires verified success | chain group `production-release`; contract: `Release reads ONLY the Production delivery call` |

**Stale Migration gate on a real runner (r12).** [41598319442](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598319442) checks out `8ad4d40` of this repository and runs the actual engine entrypoint (`actions/cf-preview/cli.mjs migration-gate`, the command `engine.sh migration-gate` runs in the publish stage) with a descriptor built by `delivery.mjs` and synthetic handoffs, on the org `linux` runner (`heavenweb-runner-3`) **and** `ubuntu-latest`. No Cloudflare secret exists in the repository, the environment is `env -i`, and `wrangler gh curl pnpm npx` are PATH shims that record any call. Result on both runners: `stale=true`, `stale="true"`, stale + otherwise valid metadata, empty, malformed JSON, `none`, owner mismatch, D1 mismatch and a failed Migration job all exit `1` (the stale refusal prints `the migration for native-branch was skipped because its run became stale; it does not authorize a publish (nothing is published)`); the valid and `d1_migrations=false` controls exit `0`; the shim log is empty (no publish-side tool was invoked). The first dispatch ([41598162595](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598162595)) failed because of a bug in the test script itself (`bash -e` aborted on the first expected non-zero exit) and is not an implementation result. This is a single-gate smoke test: the full A → B → A sequence needs a force-push race between a Migration and its Delivery against a live Cloudflare account and is **not** reproduced on GitHub Actions; its evidence remains the chain regression above.

**Scratch caller pinned to `8ad4d40` (Phase 2).** `firstsun-dev/cf-v2-scratch-caller`, org `linux` runners (all `2.338.0`), no Cloudflare credentials. `referenced_workflows` of the run lists `_cf-worker-v2/ci/delivery/lifecycle/persistent-dev.yml` all at `@8ad4d40…`, and the nested `$/actions/cf-deploy`, `cf-tools`, `cf-ci-check` steps log `@8ad4d4029b42f173dec5b6c39ddc8738fc6bf0a8`, i.e. the public workflow and every `$/` reference resolved to the same revision. Overall `failure` below is the designed fail-closed result (no credentials), not an implementation regression.

| Scenario | Run | Observed | Expected? |
| --- | --- | --- | --- |
| Parallel CI (6 checks, 2 E2E variants) | [41598183911](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598183911) | Lint, Typecheck, Unit, Integration, Build, E2E(a), E2E(b) `success`; Validation Gate, Plan `success`; Migration skipped (`d1_migrations: false`); `Deploy` failed `preview credentials not provided`; Smoke ran with status `none` | yes (conclusion `failure` from missing credentials) |
| Validation failure | [41598188577](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598188577) | Lint `failure`, the other checks still ran; Gate `failure` (`LINT finished 'failure'`); Plan, Migration, every Delivery, Release **skipped** | yes |
| Single D1 Migration | [41598193186](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598193186) (`feat/final`), [41598197646](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598197646) (`main`) | exactly one `Database Migration` job; failed closed on `preview` / `prod credentials not provided` before any remote call; every Delivery skipped | yes |
| Migration disabled | [41598201733](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598201733) | Migration skipped; Native Branch **and** Native Dev Deliveries started (failed on credentials) | yes |
| Multiple distinct D1s | [41598206295](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598206295) | Plan failed `… 2 distinct physical D1 databases: scratch-preview-db (native-branch) vs scratch-dev-db (persistent-dev) … (nothing was changed)`; Migration and Deliveries skipped | yes |
| Stale Migration | r12 above (real gate on runner); chain tests for the full path | gate exits nonzero before any publish-side call | yes |
| Deferred verification | [41598211512](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598211512), [41598215678](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598215678) | deploy-phase Plan + Delivery (failed on credentials); the verify call's Plan accepted the handoff, **Database Migration skipped** in both calls, Smoke ran from the original handoff (status `none`: nothing was published); hand-built legacy `phase_state` → success | yes |
| Deprecated `tools_ref` | [41598219951](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598219951) | `tools_ref: this-ref-does-not-exist` ignored; same fail-closed result as r1 | yes |
| Same-repo PR | [41598279417](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598279417) | only `Preview lifecycle` ran (no CI, Plan or Delivery); `success`; PR comment step success without a verified Preview | yes |
| Branch push / Branch Delete | [41598264159](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598264159) / [41598501202](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598501202) | push: CI → Plan → Deploy failed on credentials; delete (default-branch workflow): `Retire GitHub ledger (Preview is kept)` success; the lifecycle job's only mutating step is the ledger retire (the `wrangler preview delete` command appears only as a manual hint in the summary) | yes |
| Manual Persistent Dev | [41598224024](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598224024) (legacy contract), [41598228322](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41598228322) (engine contract) | legacy `dev_deploy_cmd` / `dev_smoke_cmd` ran → success; engine path failed closed on `dev credentials not provided` | yes |

The scratch Environments are unprotected and hold fake D1 ids, so Environment approval and every remote Cloudflare effect remain undemonstrated. The pre-existing `call.yml`/`v_*` workflows of the scratch repository are pinned to older SHAs and also fire on pushes; their runs are not evidence for `8ad4d40`.

**Consumer compatibility (Phase 3, read from each default branch on 2026-11-03).** Callers: blog, heaven-monorepo (3 apps), innovation-apps (2 apps), anas-mcp, heaven-www — an org-wide code search for `_cf-worker-v2.yml` found no others (the `cf-v2-scratch-caller` is the disposable test repository). All call `_cf-worker-v2.yml@v2` and pass `tools_ref: v2` (deprecated, ignored). Every `with:` key and every output they read exists in the 60-input / 20-output interface. The **real planner** (`cli.mjs plan`, data only) was run over every call's actual `wrangler` config for push `main`, push `feat/x`, dispatch `main` (default and `persistent-dev`), dispatch `feat/x`, `pull_request` and `delete`:

| Repository / App | Runner compatible | Required Checks compatible | D1 compatible | Deployment compatible | Action |
| --- | --- | --- | --- | --- | --- |
| blog | yes — `runner: linux` (ten org runners, all `2.338.0` ≥ 2.336.0); `[self-hosted, linux]` jobs run in the same pool | **UNVERIFIED — ADMIN ACCESS REQUIRED** (see below) | yes — main: prod `45031ada…`; branch push / dispatch: preview `feb45b04…`; one D1 per invocation (Shared Dev off) | yes (Native Branch + Production) | **fix `secrets:` (see rollout blocker 1)**; update Required Checks |
| heaven-monorepo / heaven-id | yes | UNVERIFIED | yes — main: prod `193440aa…`; branch push / `persistent-dev` dispatch: dev `ca755ecb…` | yes (hybrid + Shared Dev) | update Required Checks |
| heaven-monorepo / heaven-checkin | yes | UNVERIFIED | yes — prod `41fa4337…`; branch push: dev `8e3f8323…` | yes | update Required Checks |
| heaven-monorepo / test-client | yes | UNVERIFIED | yes — no D1, `d1_migrations: false` | yes | update Required Checks |
| innovation-apps / diet-manager | yes | UNVERIFIED | yes — prod `e12e2d9e…`; branch push: preview `b9421085…` | yes (Native Branch) | update Required Checks |
| innovation-apps / notification-hub | yes | UNVERIFIED | yes — prod `311ab3c8…`; branch push / dispatch: dev `9a69f8e9…` | yes (hybrid, Native Preview off) | update Required Checks |
| anas-mcp | yes — default `runner: linux` | **yes — verified: public repo, `main` not protected, no rulesets** | yes — no D1 | yes (`production-only`) | none required for checks; context names change but nothing requires them |
| heaven-www | yes | UNVERIFIED | yes — no D1 | yes (`production-preview`) | **fix `secrets:` (rollout blocker 1)**; update Required Checks |

No call enables two distinct physical D1s in any modeled event, so the single-D1 rule does not reject any supported configuration. Not modeled: the post-build effective-config audit of Astro/adapter-generated configs (blog Shared Dev is off; it runs only in a live Migration/Deploy).

**Required Checks.** Branch protection and rulesets could not be read for the four private repositories: the API answers `403 Upgrade to GitHub Pro or make this repository public` although the token has repository admin rights (organization plan: `free`; organization rulesets: `Upgrade to GitHub Team`). This is a plan limit, not a permissions gap, and it makes enforcement unlikely, but it is not proof — a rule configured under an earlier plan would be invisible here. Treat them as **UNVERIFIED — ADMIN ACCESS REQUIRED** and have an administrator confirm in the repository settings that none of the following contexts is required (`<job>` is the caller's job name: `Pipeline` for blog, innovation-apps and heaven-www; `Heaven ID`, `Heaven Checkin`, `Heaven ID Test Client` for heaven-monorepo; `cf` for anas-mcp). Required-check names as seen on the consumers' recent default-branch runs ("today") and in the `8ad4d40` scratch runs ("after"):

| Today (consumer runs) | After `8ad4d40` |
| --- | --- |
| `<job> / Lint` | `<job> / CI / Lint` |
| `<job> / Typecheck` | `<job> / CI / Typecheck` |
| `<job> / Unit Tests` | `<job> / CI / Unit Tests` |
| `<job> / Integration Tests` | `<job> / CI / Integration Tests` |
| `<job> / Build Validation` | `<job> / CI / Build Validation` |
| `<job> / E2E` and `<job> / E2E (<name>, <args>, <suffix>)` (blog: four variants) | `<job> / CI / E2E` and `<job> / CI / E2E (<name>, <args>, <suffix>)` |
| — | `<job> / CI / Validation Gate` (new aggregate) |
| `<job> / Migration` | `<job> / Database Migration` |
| `<job> / Deploy`, `Smoke Tests`, `Revert on Verify Failure`, `Shared Dev`, `Shared Dev Smoke Tests`, `Shared Dev Revert on Verify Failure` | `<job> / Delivery (production\|native-branch\|native-dev\|persistent-dev) / Deploy\|Smoke Tests\|Revert on Verify Failure` |
| `<job> / Release`, `Config guard`, `Preview lifecycle`, `Persistent Dev (Hybrid)` | unchanged; new `<job> / Delivery plan` |

Skipped jobs (unconfigured checks) report as skipped; a required context that is never emitted blocks merging. Nothing was modified in any repository's protection settings.

**Runner findings.** All ten org runners (`heavenweb-runner-1…10`, labels `self-hosted, Linux, X64, 32gb-ram, e2e, docker, playwright`) report `2.338.0` through the API (confirmed, not assumed); the label match `linux`/`Linux` is case-insensitive and the scratch jobs ran on `heavenweb-runner-3`. No repository-level runners exist. Hosted `ubuntu-latest` accepted `$/` as well (r12) but lacks `pnpm`, so full pipelines still need the org pool. The runner binary auto-updates, so the floor must be re-checked if a runner is rebuilt from an older image. Setup expectations are unchanged from `main` (same `actions/setup` with `setup-node`/`setup-pnpm: 'false'`, same `PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers`, same optional Mailpit action). No consumer uses `pipeline_phase`/`phase_state`/`upstream_gate`.

**Environments.** No consumer Environment (`*/prod`, `*/dev`, `*/dev/shared`, `*/preview*`, in all five repositories) has required reviewers, a wait timer or a deployment-branch policy. The Migration job now runs in the owner's Environment, so the `<app>/prod` approval is only as strong as these (currently absent) protections; approval behaviour was **not** exercised anywhere.

**Non-production deployment evidence (Phase 4): BLOCKED.** The only Cloudflare credentials reachable from this organization are the Production-scoped `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` org secrets; the scratch repository has none, and no disposable, explicitly approved Cloudflare account/zone/D1 or protected scratch Environment exists or is documented. Missing prerequisites: a scratch Cloudflare account (or an API token scoped to disposable Workers, a throw-away D1 with a `migrations_dir`, and a Worker name prefix), repository secrets in `cf-v2-scratch-caller`, and a protected Environment with a reviewer (needs a paid plan for private repositories, or a public scratch repository). Nothing in this section is live deployment evidence.

#### Rollout blockers found (not caused by this PR, must be resolved before `@v2` moves)

1. **Undeclared secrets → `startup_failure`.** `blog/.github/workflows/cicd.yml` and `heaven-www/.github/workflows/cicd.yml` pass `CLOUDFLARE_PREVIEW_API_TOKEN` / `CLOUDFLARE_PREVIEW_ACCOUNT_ID`, and `blog/.github/workflows/preview-gc.yml` passes them to `_cf-worker-preview-gc.yml`. Neither the current `@v2` (`def3679`) nor `8ad4d40` declares them (commit `5def01d`, already on `main`: Native Preview uses the shared `CLOUDFLARE_API_TOKEN`/`ACCOUNT_ID`). GitHub rejects such a caller before any job starts — reproduced with [41599151750](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41599151750) at `8ad4d40` and [41600536212](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41600536212) at `fd6ab85` (`startup_failure`, zero jobs). The run page states: `Invalid secret, CLOUDFLARE_PREVIEW_API_TOKEN is not defined in the referenced workflow.` (and the same for `CLOUDFLARE_PREVIEW_ACCOUNT_ID`) — the exact message is only visible in the UI, not through the API. The consumers' own history is consistent with this (runs that ended `startup_failure` with zero jobs; the cause was inferred from the matching interface, not read from a log): heaven-www `CI/CD` runs [41017574023](https://github.com/firstsun-dev/heaven-www/actions/runs/41017574023) / [41017577275](https://github.com/firstsun-dev/heaven-www/actions/runs/41017577275) (2026-10-30) and blog `Preview GC` ([41559816233](https://github.com/firstsun-dev/blog/actions/runs/41559816233) and earlier nightly runs). Minimum remediation: remove the two `CLOUDFLARE_PREVIEW_*` lines from those three callers (the shared token must be allowed to manage Previews), or re-declare them as optional secrets in the called workflows.
2. Required Checks audit and update as tabulated above.
3. Live non-production evidence (Phase 4) and a staged rollout pinned to an immutable SHA.

### Remaining risks and limitations

* **Runner floor 2.336.0** for every runner passed as `runner` (see above); GHES is unsupported by `$/`. Confirmed `2.338.0` on all ten org runners on 2026-11-03.
* **Check names changed** (`<job> / CI / Lint`, `<job> / Database Migration`, `<job> / Delivery (<kind>) / …`): required status checks in other consumers must be updated when they adopt this ref; they are unreadable on four of the five consumers (Free plan), see the table above.
* `actionlint` cannot lint `$/` yet (two message formats ignored, guarded by the contract test).
* A verify call queues under the recorded group before the Plan proves it (see "Concurrency and phase_state").
* The public workflow is 769 lines, above the 600–750 target: the remaining bulk is the 60-input / 20-output public interface (≈ 300 lines), which Phase 1 may not change.
* Total pipeline lines grew (see table); the gain is separation of concerns and the removal of duplication, not volume.

## Job DAG and architecture (thin workflow, one delivery engine)

```
Detect Changes (monorepo only — the CALLER runs _detect-changes.yml and gates its calls)
        │
 ┌──────┬───────────┬──────┬─────────────┬──────────────────┬─────┐
 Lint  Typecheck  Unit Tests  Integration Tests  Build Validation  E2E     six independent jobs, no `needs` between them
 └──────┴───────────┴──────┴─────────────┴──────────────────┴─────┘        (actions/cf-ci-check; E2E has its own body)
        │
   Migration            only with d1_migrations: build + audit + exact D1 target check + stale check + migrate (stage migrate)
        │
     Deploy             target = prod | preview: build + audit + stale check + PUBLISH the audited artifact (stage publish)
        │
  Smoke Tests           probes THIS deployment (URL, commit SHA, smoke_cmd); verified | unverified (stage verify, no CF credentials)
        ├── success ──► Release            optional (release_cmd), main only, after a VERIFIED Production deploy
        └── failure ──► Revert on Verify Failure   (stage rollback; conditional) → the run stays FAILED, Release is blocked
                          prod    : policy `automatic` only; fail-closed Worker rollback + re-smoke
                          preview : last-known-good redeploy + re-smoke
                                                  Shared Dev  (optional; target = dev; ONE job; `needs` the Preview jobs for ORDERING only)

 every job calls actions/cf-deploy → ONE delivery engine (engine.sh); `stage` picks the phases:
   migrate  : init → stale → setup → prepare → BUILD + audit → exact D1 target check → stale → MIGRATE
   publish  : init → stale → setup → prepare (ledger) → BUILD + audit → D1 target check → stale → PUBLISH
   verify   : init → hydrate hand-off → setup → smoke → mark verified | mark UNVERIFIED
   rollback : init → hydrate hand-off → setup → prod: guards → rollback → re-smoke | preview: recover-find → rebuild last good → re-smoke
   all      : Shared Dev / persistent Dev only — the former single-job sequence, unchanged

 outside the core DAG:  lifecycle (same-repo PR comment / branch-delete ledger retire)
                        config-guard (production-only + Shared Dev is rejected)
                        persistent-dev (Hybrid, protected workflow_dispatch on the approved ref; the same engine, mode=manual)
```

There is **no Prepare, Plan or Test-Gate job**. Migration is its own job again (v1 parity), but it keeps the v2 order *inside* it: the deployable is built and the effective Wrangler config audited and the exact D1 target validated BEFORE the schema is touched, and the migration publishes nothing. Deploy then repeats build + audit from the same SHA and publishes. Smoke and Rollback are separate jobs so the DAG shows whether a deployment was verified or reverted.

### Migration → Deploy → Smoke → Rollback (job split)

**State between jobs** is data only, never runner files: each job re-runs the engine's `init` (same `inputs_json`, same target binding, same credential isolation) and the Smoke / Rollback stages `hydrate` a hand-off that is validated field by field (https URL, `[A-Za-z0-9-]` version ids, Worker-name and Preview-name patterns, enum for the migration facts, `published_sha == GITHUB_SHA`) before anything uses it.

| Fact | Produced by | Consumed by |
|---|---|---|
| target (`prod`/`preview`) | every job derives it from `github.ref` (never from output) | all |
| `published`, `published_sha` | Deploy | Smoke, Rollback, Release (a stale run publishes nothing → Smoke/Rollback skipped) |
| deploy URL (`url`) | Deploy | Smoke (Preview), workflow output `deploy_url` |
| `version_id`, `previous_version_id` | Deploy | Smoke (summary), Rollback (target + "is it still our deployment") |
| `worker_name` (audited config) | Deploy | Rollback (rollback identity; the job builds nothing) |
| `ledger_id`, `preview_name` | Deploy | Smoke (ledger status), Rollback (known-good lookup) |
| `migration`, `schema_changed`, `d1_scan` | Migration | Deploy (`migration` output), Rollback (D1 decision), Shared Dev guard |
| verification (`deployed_and_verified`, `smoke_level`, `verified_url`) | Smoke | Release, workflow outputs |
| rollback verdict (`rollback_result`) | Rollback | workflow output |

**Concurrency:** the workflow-level `cfv2-*` group spans the whole run, so no other run can publish or roll back the same Worker between *Publish* and the end of *Smoke / Rollback* (`queue: max`, FIFO). Splitting the jobs added no job-level group (a nested group could deadlock with it). The stale-SHA guards (start / before migration / before publish) and the D1 locks are unchanged; Deploy re-checks the branch tip, so a push that lands during the migration still prevents the old code from being published.

**Verification levels** (explicit, `smoke_level`): `content-sha` (the served commit SHA equals this commit) > `content` (the application answered the probe + `smoke_cmd`) > `liveness` (Cloudflare Access login reachable only: *published but NOT verified*). Only the first two make `deploy_verified` true. A failed smoke marks the deployment **unverified**: the Environment deployment GitHub created for the Deploy job (which only means "published") gets a `failure` status, and the Preview ledger entry is set to `failure`.

**Production rollback is fail-closed.** `production_rollback_policy: automatic` runs the Rollback job after a failed Smoke. It changes nothing unless ALL hold, otherwise it prints the manual command and stays red (`rollback_result=blocked`):
1. a previous Worker version id was recorded (a first deployment, or an unreadable deployment list, is never "rolled back");
2. the version currently live is still the one THIS run deployed (nobody deployed out of band, no newer run);
3. D1: if this run **applied** migrations, `d1_rollback_compat: expand-contract` must be set (caller's Expand/Contract attestation) AND the applied SQL must be statically provable additive (`CREATE TABLE/INDEX`, `ADD COLUMN` that is nullable or defaulted, `INSERT`). Anything else — `DROP`, `RENAME`, `UPDATE/DELETE`, a `NOT NULL` column without default, a constraint old code may violate, an unreadable file list — blocks. With nothing pending the schema is unchanged and no attestation is needed. **D1 is never reverted.**
4. After `wrangler versions deploy <prev>@100` the restored Production is smoked again: the previous commit when it can be read from the version message, otherwise "no longer serves the failed commit". Only `restored_verified` turns the Rollback job green.

The Smoke job is red in every failure case, so a rolled-back deployment is never reported as successful and Release stays blocked. `rollback_result` is `restored_verified | restored_unverified | restored_unhealthy | blocked | failed | no_known_good`; everything except an empty value is a failed run. Policies `manual` (default) and `disabled` skip the Rollback job; the Smoke job prints the manual command.

**Preview / Shared Dev** keep their last-known-good recovery (same eligibility rules: same repository, application, branch, host Worker; never a Production version). For Preview it now runs in the Rollback job; Shared Dev still recovers inside its single job.

**Required status checks:** the job display names are `Migration`, `Deploy`, `Smoke Tests`, `Revert on Verify Failure` (+ the unchanged validation names). Repositories that require specific check names should add the new ones; `Migration` and `Revert on Verify Failure` are *skipped* (not failed) when not applicable.

**Environment protection:** `Migration`, `Deploy` and `Revert on Verify Failure` reference the `<app>/prod|preview` Environment (they change Production / Preview resources). Required-reviewer rules therefore prompt once per such job; prefer branch rules / wait timers on that Environment. `Smoke Tests` references none and holds no Cloudflare credentials.

### Deferred verification (`pipeline_phase`) — a Smoke that waits for ANOTHER Worker

A job inside one reusable-workflow call cannot `needs` a job of a different call, so "Heaven ID's OIDC smoke must wait for the
Test Client's deployment + smoke, but nothing else of Heaven ID may" is not expressible with one call. The shared workflow
therefore supports an **opt-in** split into two calls of the same file (default `pipeline_phase: all` = the unchanged single call;
no existing caller changes):

```
detect ─┬─ A (deploy call):   CI ‖ E2E ─► Migration ─► Deploy ─► (Shared Dev: migrate+publish)      output: phase_state
        │                                                              │
        ├─ B (other Worker):  CI ‖ E2E ─► Migration ─► Deploy ─► Smoke Tests ─► (Revert) ─┐          result
        │                                                              │                  │
        └─ A' (verify call, needs A AND B):  Smoke Tests ─► Revert on Verify Failure ─► Release verdict
```

| `pipeline_phase` | jobs that run |
|---|---|
| `all` (default) | everything, as before |
| `deploy` | six validation jobs, Migration, Deploy, Shared Dev (stage `deploy` = migrate + publish, **no smoke, no recovery**), handlers. Smoke, Rollback, Release never run. Output `phase_state` is the hand-off. |
| `verify` | `smoke`, `rollback`, `release`, `dev-verify`, `dev-rollback` only. No CI, Migration, Deploy or publish. |

* **Hand-off = data.** `phase_state` (workflow output of the deploy call) is `{inputs, deploy, migration, dev, group}`:
  the deploy call's inputs, its job outputs and its concurrency group. The verify call passes it unchanged; the engine takes
  its inputs from `phase_state.inputs`, so the verify call only repeats `app_name`, `app_path`, `runner`,
  `production_rollback_policy` (+ `release_cmd`, the secrets). `engine.sh init` compares `app_name`, `app_path`,
  and `production_rollback_policy` of the verify call with the blob and **refuses a mismatch**. `hydrate` still
  validates every field by regex and refuses a hand-off published from another SHA.
* **Cross-service gate (`upstream_gate`).** The caller passes `needs.<other>.result`. `success` or `skipped` (the other Worker is
  unchanged: its existing verified environment is used) satisfy it. Anything else (failed, cancelled, unknown) makes the
  verify stage run `gate-blocked` **before any smoke**: the deployment is marked UNVERIFIED (Environment deployment / ledger),
  the job fails, `gate_blocked=true`, **Release is blocked and no rollback runs** — the failure is not attributable to
  this Worker, so a healthy deployment is not reverted; the manual commands are printed instead.
* **A failed smoke of this Worker** behaves exactly as in the single call (Production: `production_rollback_policy`, fail-closed
  rollback + re-smoke; Shared Dev: last-known-good recovery + re-smoke; the smoke job stays red).
* **Concurrency.** The verify call joins the deploy call's run-level group (`phase_state.group`), but the group is *released
  between the two calls* (GitHub cannot hold one across calls). The remaining window is covered by existing fail-closed guards:
  content-SHA smoke (a deployment someone else published meanwhile is not "ours" → unverified), Production rollback requires the
  live version to still be the one this run deployed, Shared Dev recovery requires that no NEWER Dev deployment has started
  (`gh.sh newest-id`) and takes the Dev job-level group. Worst case under rapid pushes: a spurious red run, never an overwritten
  newer deployment.
* **Compatibility risk to document in the caller:** the first Worker is published *before* the second one is verified, so the
  second must work with the previous and the new version of the first (N / N-1).
* **Secrets/size:** `phase_state` is a job output (≤ 1 MB); a secret value inside any input would be masked and the hand-off
  refused (fail closed) — pass secrets as `secrets`, not inputs.

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
| Credentials in the job env | `CLOUDFLARE_API_TOKEN/ACCOUNT_ID` | `CLOUDFLARE_API_TOKEN/ACCOUNT_ID` (same account token; + `PREVIEW_SECRETS`) | `CLOUDFLARE_API_TOKEN/ACCOUNT_ID` (same account token as Production) |
| Environment | `<app>/prod` | `<app>/preview` | `<app>/dev` |
| Build env / vars | `build_env_prod` | `build_env_preview` | `build_env_dev`, `dev_build_cmd`, `CLOUDFLARE_ENV=dev_wrangler_env` |
| D1 (same project SQL) | top-level `d1_databases` | `previews.d1_databases` | `env.<dev_wrangler_env>.d1_databases` |
| Config audit on the published config | Preview-resource ban | Preview binding / auth isolation | Dev Worker identity + resource policy |
| Publish | `versions upload` + `versions deploy @100` | `wrangler preview --name <slug>` | `wrangler deploy --config <audited> --env <dev env>` or the caller's `dev_deploy_cmd` |
| Smoke URL | `app_url_prod` | discovered Preview URL | `app_url_dev` |
| Recovery | policy-driven rollback to the previous version | redeploy last-good of THIS branch + host Worker | redeploy last verified Dev SHA (any branch) |

### Local composite actions in a cross-repository reusable workflow

**Superseded by `$/` (see "Phase 1 simplification").** The old pattern — check out `firstsun-dev/.github` at `tools_ref` into `.cf-tools` and reference `./.cf-tools/actions/<name>` — existed because `./` resolves against the *caller's* workspace and `uses:` cannot take an expression. Actions and nested workflows are now `uses: $/actions/<name>` / `uses: $/.github/workflows/<file>`, which resolve to this repository at the running commit.

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
* **Migration** and **Deploy** need the six validation jobs (a CONFIGURED one that is skipped, failed or cancelled blocks them); Deploy also needs Migration (`success`, or `skipped` when `d1_migrations` is false). When `d1_migrations` is true the **Migration job** (and Shared Dev's single job) builds, audits the effective config, validates the exact D1 target and runs a stale check before applying migrations; Deploy follows. A failed migration fails the job and nothing is published. **Smoke Tests** need a Deploy that published; **Revert on Verify Failure** runs only after a failed smoke.
* **Deploy** (engine): target re-resolved from the event and bound once, credentials present (fail closed), stale-SHA guard, exact-SHA checkout, setup, target build env, **build**, source and **effective (adapter-generated)** Wrangler config audit, exact D1 target check, stale check, **migrate**, stale check, publish **of the audited config only**, real URL, smoke (Access-aware), Deployment status, recovery.
* Same-repo PRs do not re-run validation (the push run of the same SHA covers them); fork PRs run it credential-free. No job other than `lifecycle` ever runs on a PR with write capabilities.
* **Runner overhead.** One run now needs up to 5 validation jobs + the E2E matrix concurrently (e.g. 5 + 4 = 9 runners for Blog) instead of 1 + 4, and each repeats checkout and dependency install (cache hits make this cheap, never share a mutable workspace). Peak memory per runner is unchanged; peak *runner count* roughly doubles on a self-hosted pool. Mitigations: leave unused command inputs empty (the job is skipped, not a no-op runner), keep the pool large enough for the largest consumer's fan-out, and do not serialize checks. `queue: max` still serializes whole runs per resource group, so fan-out only multiplies within a run.
* **Consumer guidance.** No input changed: callers that already pass `lint_cmd`/`typecheck_cmd`/`test_unit_cmd`/`test_integration_cmd` get distinct jobs automatically after pinning a SHA containing this change. A caller without unit/integration tests (e.g. heaven-www) leaves them empty and keeps passing. A caller's content-only fast path may keep passing an empty unit/integration/E2E command; Lint/Typecheck/Build stay required.

## Interface (inputs)

| Group | Inputs |
|---|---|
| Identity | `tools_ref` (deprecated, ignored), `app_name`, `app_path`, `app_version`, `runner`, `wrangler_config` |
| Strategy | `deployment_strategy` (`production-preview` \| `hybrid` \| `production-only`), `preview_on_branch_push`, `shared_dev_on_branch_push` (default `false`), `preview_worker_name` |
| Validation | `lint_cmd`, `typecheck_cmd`, `test_unit_cmd`, `test_integration_cmd`, `build_cmd` (required), `ci_env`, `use_mailpit` |
| E2E | `test_e2e_cmd`, `test_e2e_setup_cmd`, `test_e2e_build_cmd`, `test_e2e_env`, `test_e2e_variants` |
| Deploy | `build_env_prod`, `build_env_preview`, `smoke_cmd`, `smoke_env`, `probe_path`, `probe_expect_status`, `sha_path`, `app_url_prod`, `access_protected_targets`, `production_rollback_policy`, `preview_binding_allow`, `preview_deny_resources` |
| Migration | `d1_migrations`, `d1_binding`, `d1_lock_scope` (names the shared-D1 concurrency group; no command override) |
| Release | `release_cmd` (+ secret `RELEASE_TOKEN`) |
| Hybrid | `persistent_dev_ref`, `dev_deploy_cmd`, `dev_smoke_cmd`, `dispatch_target` |
| Shared Dev | `shared_dev_on_branch_push`, `app_url_dev`, `dev_deploy_cmd`, `dev_smoke_cmd`, `build_env_dev`, `dev_build_cmd`, `dev_wrangler_env` (default `dev`), `dev_worker_name`, `dev_binding_allow`, `dev_deny_resources`; reuses `d1_migrations`, `d1_binding`, `sha_path`, `smoke_env`, `access_protected_targets`, `preview_deny_resources` |

Outputs: `deploy_url`, `deploy_target`, `version_id` (prod only), `dev_url` (Shared Dev). The internal `deployed_and_verified` Deploy output is what gates Release. Secrets: `CLOUDFLARE_API_TOKEN/ACCOUNT_ID` (prod), `PREVIEW_SECRETS`, `SMOKE_USERNAME/PASSWORD`, `RELEASE_TOKEN`.

`*_env` inputs are `KEY=VALUE` lines imported by `actions/cf-preview/parse-env.mjs` **from an environment variable** (never interpolated into a script). It rejects malformed lines and refuses `CLOUDFLARE_*`, `GITHUB_*`, `RUNNER_*`, `PATH`, `NODE_OPTIONS`, `LD_*`, `BASH_ENV`, `GIT_*`, `SMOKE_*` credentials, etc.; all-or-nothing. Application variable names remain consumer-owned. Command inputs are passed through `env:` and run with `bash -c "$CMD"`.

### Removed (old v2 → refactored)

| Removed | Replacement |
|---|---|
| jobs `plan`, `test-unit`, `test-integration`, `test-e2e`, `test` (gate), `preview`, `production`, `production-verify`, `production-recovery`, `pr-link`, `cleanup`, and (PR #64 refactor) the standalone `migrate` job | `lint`, `typecheck`, `unit`, `integration`, `build`, `e2e`, `migration`, `deploy` (publish only), `smoke`, `rollback`, `dev-deploy`, `release`, `lifecycle` |
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
* Credentials: Dev reuses `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` in the `<app>/dev` Environment. `PREVIEW_SECRETS` is not read in Dev. Missing account credentials fail closed; Dev Worker, environment and resource audits remain separate.
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
* **Order inside the Migration job (Shared Dev: inside its single job):** `init` (target bound, credentials) → stale check → `prepare` (Wrangler version, Preview identity and **ledger ownership**, source binding policy) → **build + audit of the effective config** → exact D1 target check (read-only; the deployed D1 must be the migrated D1) → stale check → **`migrate.sh`** → stale check → publish. Schema is never changed before a deployable build and a valid target configuration exist; a deleted/superseded branch applies nothing, and a push that lands *during* a long migration is detected by the final stale check so old code is not published over a newer deployment.
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
