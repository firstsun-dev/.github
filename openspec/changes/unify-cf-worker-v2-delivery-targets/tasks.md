# Tasks: Unified CF Worker v2 delivery and Native Dev

## Phase 0 — spec-only commit to .github/main (NOW)

- [x] Define the shared Validation → independent Migration → target Build/Deploy → optional Smoke → Recovery → Release DAG.
- [x] Specify target separation, Native Dev naming, per-target build URLs, D1 identity, smoke modes and recovery semantics.
- [x] Document the **mandatory .github-first G1 gate** and strict sub-session sequencing.
- [ ] **No workflow, Cloudflare/Terraform, consumer repo or floating @v2 tag changes in this commit.** (Safety condition, not an implementation task.)

## Phase 1 — .github shared engine ONLY (MAIN SESSION; no sub-sessions)

- [x] Implement shared Validation Gate with independent parallel lint/typecheck/unit/integration/build-validation/E2E.
- [x] Implement standalone audited, target-bound Migration Stage after Validation, grouped by physical D1, including no-D1 bypass and DB locking.
- [x] Refactor target-parameterized Delivery (Production / Native Branch / Native Dev / Persistent Dev) around one engine, with no unconditional cross-target `needs`.
- [x] Add opt-in Native Dev named Preview `dev`, protected identity, separate ledger/recovery and concurrent-branch serialization.
- [x] Add target-specific URL pre-resolution/build-env, build/artifact audit, no shared compiled bundle, and post-publish URL assertion.
- [x] Add `smoke_mode=full|basic|skip` and target overrides, explicit Published/Unverified semantics and conditional target-owned Recovery.
- [x] Preserve `pipeline_phase`/`phase_state`, public caller inputs/outputs, release gates, v1/v1.5 and the currently deployed `@v2` behavior. (Contract + 404 chain checks; deferred verification now keeps the deploy call's descriptors; real-runner evidence in docs.)
- [x] Add/extend contract, shell/unit, DAG, smoke, skip, migration, DB lock, stale SHA, rollback, Access, preview cleanup and cross-service handoff tests.
- [x] Run actionlint and relevant OpenSpec checks; prove reusable workflow calls from a separate scratch repository/runnable caller; record evidence. (actionlint+shellcheck green in CI on 8ad9f5f; scratch-caller runs 41062254408, 41062261540, 41062268866, 41062278092. Live Cloudflare behaviour is NOT proven — no credentials.)
- [x] Review finding 1 (P1): deferred verification restores the deploy call's validated descriptors; verify-call defaults never replace deployment-time policy.
- [x] Review finding 2 (P2): structural refactor — descriptor replaces seven per-call fields, inline migration/rollback/verification policy moved to `delivery.mjs`, one build and one publish primitive, `engine.sh init` is one validated Node process.
- [x] Review finding 3 (P2): AST-based test grouping replaced by explicit `group_*` functions + manifest-checked launcher.
- [x] Review finding 4 (P2): measured subprocess/CPU reduction inside the engine (init 213 ms -> 58 ms; Node processes per chain 17 -> 9); the harness step-shell count is NOT reduced (documented).
- [x] Review finding 5 (P1): final-SHA scratch-caller validation on org runners; credential-less scope stated.
- [x] Final review fixes: lock-identity integrity (scheduling-time lock derived from `phase_state.inputs`, group/descriptor/owner/D1 cross-checks, documented trust boundary), CLI annotations on stderr, legacy D1 ownership proof, fail-fast `test.sh full`.
- [ ] Merge the fully tested shared implementation to **.github/main**; record frozen immutable SHA, exact interface, compatibility proof and explicit G1 pass/fail. Do not move `@v2` yet.

## Phase 1b — PR #89 simplification (supersedes the Preflight matrix / per-target Migration calls)

- [x] Remove the four Preflight calls and the four Migration calls; add ONE `migration` job (validation steps first, one migration last). No matrix, no new reusable layer.
- [x] Plan: one physical D1 per pipeline (dedupe identical, reject distinct), `migration` output replaces `owns_<kind>`; Production D1 never reachable from Preview/Dev.
- [x] Delivery workflow: only deploy / smoke / rollback; `migration_result` + `migration_json` replace the stage, peer and owner inputs; a failed Migration skips every Delivery.
- [x] Engine: state is fresh per `init`; tooling and application dependencies install once per job; migration gate only for publish.
- [x] Tests updated in the same slices (contract, job chain, `delivery.mjs`, `cli.mjs`); required scenarios 1-14 covered; manifest minimums only raised.
- [x] Measured before/after (docs/cf-worker-v2-unified-delivery.md, "Single Migration job").
- [x] Real-runner scratch-caller evidence for the final SHA `8ad4d40` (docs/cf-worker-v2-refactor.md, "Final-SHA merge-readiness validation"), including the real Migration Gate entrypoint with stale handoffs on the `linux` and `ubuntu-latest` runners; live Cloudflare migration/deploy remain unproven (no approved non-production credentials — BLOCKED).
- [x] Confirm that no supported caller needs distinct D1s in one invocation: the real planner over blog, heaven-monorepo (3 apps), innovation-apps (2 apps), anas-mcp and heaven-www × push/dispatch/PR/delete never plans two distinct physical D1s. Re-confirm if a caller's `wrangler` config or switches change before the `@v2` move.
- [ ] **Before the `@v2` move** (procedure and rollback: docs/cf-worker-v2-rollout.md; fix PRs blog#461 and heaven-www#56 prepared, not merged): remove the undeclared `CLOUDFLARE_PREVIEW_API_TOKEN` / `CLOUDFLARE_PREVIEW_ACCOUNT_ID` secrets from blog `cicd.yml`, heaven-www `cicd.yml` and blog `preview-gc.yml` (they cause `startup_failure`), have an administrator confirm/update Required Checks for the renamed contexts in blog, heaven-monorepo, innovation-apps and heaven-www (unreadable on the Free plan), and run a live non-production deployment/smoke/recovery exercise.

## G1 — MANDATORY HARD GATE (all conditions required)

- [ ] Shared .github implementation is complete and merged to main (not merely proposed).
- [ ] All shared CI, contract, security, regression and cross-repository callable workflow tests pass.
- [ ] Final inputs/outputs and verified immutable implementation SHA are documented.
- [ ] No unresolved critical blockers (resource isolation, D1, Access, stale deploy/rollback, Release).
- [ ] Main Session records **G1 = PASS**. No consumer implementation or sub-sessions may start before this line is checked.

## Phase 1 simplification (PR #89 refactor, no behaviour change)

- [x] `$/` self-repository references replace every `.cf-tools` checkout; `tools_ref` stays accepted (optional, ignored); runner floor 2.336.0 documented.
- [x] `_cf-worker-ci.yml`: six parallel checks + fail-closed Validation Gate with a `validation=passed` verdict; one CI call; no duplicate CI between push and same-repo PR.
- [x] ONE Migration job unchanged; Migration and Delivery depend on the plan only (the plan requires a passed CI).
- [x] Preview lifecycle and manual persistent Dev extracted (`_cf-worker-lifecycle.yml`, `_cf-worker-persistent-dev.yml`); one shared Delivery engine kept; four thin calls kept (no matrix, rationale in docs).
- [x] One scheduling-time `concurrency.group` expression; canonical `runLockGroup()` emitted by the Plan; `phase_state` without the redundant `d1` map.
- [x] P1 review fix: `resolveMigrationUsed` rejects `stale` migration results (no stale-skipped migration authorizes a publish); regression tests incl. A→B→A.
- [x] Contract + chain tests ported; cross-repository real-runner scenarios r1–r11 recorded in `docs/cf-worker-v2-refactor.md`.

## Phase 2 — AFTER G1 ONLY: consumer sub-sessions (parallel when independent)

- [ ] **Sub-session A, blog:** Native Branch ON, Native Dev ON, Persistent Dev OFF; distinct Astro URL builds, shared Preview D1 audit, Preview GC protection; retain content-only CI optimization.
- [ ] **Sub-session B, innovation-apps:** Diet Manager Native Dev ON/Branch OFF/Persistent OFF; keep Notification Hub Persistent Dev ON and Native Preview OFF, with Queue/Cron/DLQ and dry-run protections.
- [ ] **Sub-session C, heaven-monorepo:** Native Dev ON and Branch/Persistent Dev OFF for ID/Check-in/Test Client; correct OIDC/public build URLs, changed-app CI, cross-service deployment readiness and smoke dependency DAG.
- [ ] **Sub-session D, infra-config:** Preview enablement, Access Service Token/content checks, D1/R2 secrets/permissions and Terraform full plan; no apply or route changes without separate explicit approval.
- [ ] **Sub-session E, read-only compatibility:** anas-mcp and heaven-www @v2 caller/permission/outputs regression; modify only if a proven issue warrants a separately reviewed change.
- [ ] Give every sub-session the **same frozen .github SHA**, use isolated worktrees and separate PRs; Main Session owns shared interface and integration.

## Phase 3 — acceptance and controlled release

- [ ] Verify each enabled target performs its own URL-specific build and audited publish; blog's two Native Previews remain independent.
- [ ] Verify real D1 deduplication, no-Prod-data fallback, physical-resource concurrency, stale-SHA guard and fail-closed migration.
- [ ] Verify full/basic/skip outcomes, known-good ledger, guarded recovery, Production Release gate and cross-service OIDC dependency checks.
- [ ] Verify Notification Hub persistent Dev and unchanged anas-mcp/heaven-www behavior with real evidence.
- [ ] Only after consumer and compatibility gates pass, coordinate the `@v2` tag update and staged rollout; monitor actual jobs, Access/OAuth, Smoke and recovery.

## Phase 4 — deferred cleanup

- [ ] Produce separate reviewed inventory and cleanup plan for old persistent Dev Workers (including blog-preview), obsolete custom domains, Preview bases and related policies.
- [ ] Delete/repoint resources **only after** evidence of replacement, state/data retention checks and specific approval; never delete Production D1/R2 or Dev state by default.
