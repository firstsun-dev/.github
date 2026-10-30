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
- [x] Preserve `pipeline_phase`/`phase_state`, public caller inputs/outputs, release gates, v1/v1.5 and the currently deployed `@v2` behavior. (Contract + 393 chain checks; deferred verification now keeps the deploy call's descriptors; real-runner evidence in docs.)
- [x] Add/extend contract, shell/unit, DAG, smoke, skip, migration, DB lock, stale SHA, rollback, Access, preview cleanup and cross-service handoff tests.
- [x] Run actionlint and relevant OpenSpec checks; prove reusable workflow calls from a separate scratch repository/runnable caller; record evidence. (actionlint+shellcheck green in CI on 506c101; scratch-caller runs 41056958707, 41056964707, 41057340143. Live Cloudflare behaviour is NOT proven — no credentials.)
- [x] Review finding 1 (P1): deferred verification restores the deploy call's validated descriptors; verify-call defaults never replace deployment-time policy.
- [x] Review finding 2 (P2): structural refactor — descriptor replaces seven per-call fields, inline migration/rollback/verification policy moved to `delivery.mjs`, one build and one publish primitive, `engine.sh init` is one validated Node process.
- [x] Review finding 3 (P2): AST-based test grouping replaced by explicit `group_*` functions + manifest-checked launcher.
- [x] Review finding 4 (P2): measured subprocess/CPU reduction inside the engine (init 213 ms -> 58 ms; Node processes per chain 17 -> 9); the harness step-shell count is NOT reduced (documented).
- [x] Review finding 5 (P1): final-SHA scratch-caller validation on org runners; credential-less scope stated.
- [ ] Merge the fully tested shared implementation to **.github/main**; record frozen immutable SHA, exact interface, compatibility proof and explicit G1 pass/fail. Do not move `@v2` yet.

## G1 — MANDATORY HARD GATE (all conditions required)

- [ ] Shared .github implementation is complete and merged to main (not merely proposed).
- [ ] All shared CI, contract, security, regression and cross-repository callable workflow tests pass.
- [ ] Final inputs/outputs and verified immutable implementation SHA are documented.
- [ ] No unresolved critical blockers (resource isolation, D1, Access, stale deploy/rollback, Release).
- [ ] Main Session records **G1 = PASS**. No consumer implementation or sub-sessions may start before this line is checked.

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
