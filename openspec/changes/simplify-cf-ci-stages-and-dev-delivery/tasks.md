# Implementation tasks: stage-based CI and unified Dev delivery

> **Not started.** This OpenSpec change is documentation-only. Do not mark runtime tasks complete because the specification PR was merged. Target: **one OpenSpec commit** and one draft review PR now; implementation later as coordinated, separately reviewed repository PRs (prefer one focused PR / squash commit per affected repository). No permanent legacy-mode workflow.

## G0 — Review and freeze design (spec-only PR)

- [ ] Review `proposal.md` and `design.md`; approve the Push / PR / merge_group / main event matrix.
- [ ] Review proposed JSON task schema (`verify_tasks` / `test_tasks`), E2E exception, bounded concurrency, Gate skip/failure semantics, stable check names.
- [ ] Approve fixed Dev **manual promotion** (approved SHA/ref and environment approver), and blog **PR-only Native Preview** instead of current feature-push branch Preview. Decide whether a pre-PR URL is genuinely needed.
- [ ] Confirm measured P95 of each consumer's Build/Lint/Typecheck/UT/Fast IT/Heavy IT/E2E before assigning stage commands; avoid taxonomy-based assumptions.
- [ ] Confirm Blog PR-preview D1 policy (remote PR migrations disabled by default), possible friendly hostname, and Preview 14-day GC policy before implementation.
- [ ] Inventory every active caller of `@v2` / any `v1` references, Required Checks / rulesets, default runner security, Dev/Production resources, current names/aliases/URLs and Terraform-managed infrastructure.
- [ ] Record exact G0 acceptance and any deviations in this change; do not silently add flags or multi-channel exceptions later.

## G1 — Shared engine implementation in `firstsun-dev/.github`

### CI stage executor and events

- [ ] Replace fixed per-type public CI command inputs with validated app-owned `verify_tasks` and `test_tasks` lists; retain dedicated optional E2E matrix with explicit build/setup/env/report contract.
- [ ] Define strict unique task ID / nonempty command / app-path / empty-array validation; reject unsupported task options and duplicate names before launching work.
- [ ] Implement parallel stage execution with bounded matrix, shared setup/cache, no pointless sequential dependencies; only express `needs` for actual prerequisites.
- [ ] Implement stable, app-scoped Verify Gate and PR Gate as fail-closed aggregators; distinguish an intentionally empty stage and a no-op unaffected app from unexpected skipped/failed/cancelled tasks.
- [ ] Route feature Push to Verify, same-repo PR to Verify+Test on merge SHA, `merge_group` to no-deploy Verify+Test where supported, main Push to protected Production eligibility, and manual Dev to authorized exact-revision promotion.
- [ ] Maintain one eligible Required Check name per app; audit how caller workflows are always invoked even when changed-path filtering says no work. Avoid PR required-check starvation from workflow-level path filters.
- [ ] Remove the same-repo PR blanket skip. Do not implement Push-run status polling or a home-grown cross-run scheduler.
- [ ] Separate untrusted fork PR CI from self-hosted runners, privileged secrets and any deploy steps. Preserve `anas-mcp` safety pending an explicit safe hosted-runner alternative.
- [ ] Preserve credible test reporting and avoid double E2E from Push and PR; main Push requires protected merge evidence or equivalent release validation.

### Delivery targets and safety

- [ ] Replace the four delivery kinds (`production`, `native-branch`, `native-dev`, `persistent-dev`) with validated logical targets (`production`, `preview`, `dev`) and drivers (`worker`, `native-preview`) in the pure planner.
- [ ] Normalize target descriptors and outputs (target/driver/URL/SHA/verified/unverified/rollback) without breaking approved consumer behavior in the coordinated release.
- [ ] Enforce one nonproduction automatic target per event and no fixed Dev updates from arbitrary feature pushes; enable stable Dev only from approved exact SHA/ref and protected `workflow_dispatch`.
- [ ] Consolidate public controls to `preview_policy` and `dev_driver` plus the minimum environment-specific settings; remove obsolete branch-push switches, strategy combinations and unused per-kind output aliases **after** all callers adopt the new contract.
- [ ] Keep one target-aware build + audited effective Wrangler config + canonical URL + stale SHA + resource checks; choose the provider-specific publish command only at the driver boundary.
- [ ] Keep migration as a separately gated, provider-identity-audited action: no Production resource collision, at-most-once per physical D1 transition, safe lock ownership, no mutation before validation.
- [ ] Maintain verified vs published-unverified distinction, complete Smoke, target-owned guarded recovery/rollback, and verified Production Release gates.
- [ ] Preserve/de-risk the Heaven OIDC deployed-test readiness and release constraints; remove existing deferred `pipeline_phase` / `phase_state` only after independently proving equivalent behavior.
- [ ] Ensure no non-Production token can publish prod / access Production D1, Queue, R2 or other forbidden bindings.

### Streamline engine self-tests

- [ ] Add parallel `static` (actionlint / shellcheck / syntax), `unit` (pure plan / tasks / routing / Gate), `safety` (negative D1 / auth / stale / migration / smoke / rollback) and a stable `Pipeline Gate`.
- [ ] Retire redundant GitHub-expression emulation (`ghexpr.py`), job simulator (`test-jobs.py`) and count-based `run-job-groups.py` manifest **only once** their meaningful mutation-safety cases are preserved in deterministic focused tests or real-runner canaries.
- [ ] Use `cf-v2-scratch-caller` to test real GitHub reusable call, permissions, PR checks, preview, manual Dev and no-production-side-effects on a candidate SHA.
- [ ] Keep significant regression cases: stale A→B→A, missing/incorrect migration owner/DB, dual-bindings, smoke failed after publish, protected recovery, Access-only liveness, skipped required check and runner/fork isolation.
- [ ] Publish G1 evidence and freeze an immutable shared-engine candidate SHA. Leave floating `@v2` unchanged at G1.

## G2 — Consumer PRs, all pinned to the same frozen SHA

- [ ] **Blog:** set Verify commands for lint/check/build/UT and measured fast IT; Test for heavy IT/E2E; preserve content-only detection, SEO build outputs, test artifacts, D1/R2 restrictions. Enable PR Preview only; disable automatic Native Dev and branch Push preview in new contract. Confirm actual published URL, PR status and no Preview remote schema migration.
- [ ] **Heaven Monorepo (ID, Checkin, Test Client):** configure per-app Verify/Test, affected-app detection and isolated gating; preserve local per-app E2E and post-deploy OIDC cross-service E2E. Use fixed Dev Native Preview with controlled compatible-cohort promotion. Check issuer/redirect URL, readiness, DB and cross-app release safety; avoid three duplicate deployments on a single merge.
- [ ] **Innovation Apps / Diet Manager:** configure Verify/Test/optional E2E, keep FatSecret Secrets Store allowlist and dev D1/R2 denial of Prod, fixed native Dev only on authorized promotion, semantic-release after verified Production.
- [ ] **Innovation Apps / Notification Hub:** configure Verify/Test, protect real `env.dev` Worker/Queue/DLQ/dev D1, Telegram dry-run, dev-only privileged dispatch and app-specific smoke. Reject Native Preview as its replacement for Queue/Cron behavior.
- [ ] **Heaven WWW:** configure Verify (lint/check/build), optional empty Test with stable PR Gate, fixed native Dev explicit promotion, Access/probe and SEO asset smoke.
- [ ] **Anas MCP:** keep production-only; prohibit fork PR using self-hosted runners or secrets. Either port safe Verify/Test to GitHub-hosted runner with explicit tests or document an enforceable trusted-branch alternative. Do not enable a public Dev Preview.
- [ ] **Infra-config (only if needed):** prepare PR for access policy, DNS/worker/route, secret provisioning and environment rules; require Terraform plan review and separate approval before apply.
- [ ] **Scratch Caller:** exercise event matrix and actual target-driver calls with no sensitive Production side effects.
- [ ] For every caller: assert required-check identity, no skipped check on unmodified paths, fork trust, latest SHA, Matrix result aggregation, affected-app selection, Stage boundaries and exact target resource bindings.

## G3 — One coordinated cutover

- [ ] Freeze shared new input/output contract and consumer refs; run full static/unit/safety checks, scratch canary and one representative real path per app.
- [ ] Prepare backup record of old `@v2` SHA and each caller's original commit, Required Check mappings and deployment state. Do not claim cross-repo atomicity.
- [ ] Coordinate repo PR merging in a controlled window using immutable candidate SHA pins. Prefer squash merge; do not merge an incompatible caller against an unverified floating tag.
- [ ] Update per-repo Required Checks/rulesets only where permissions allow; when API access is unavailable, record manual verification steps and block completion until confirmed.
- [ ] Validate one real feature Push, one same-repo PR, appropriate fork/merge_group case, one authorized Dev promotion and one protected Production deployment as permitted; inspect actual GitHub Checks and Cloudflare target histories.
- [ ] Only once all supported callers are ready, advance `@v2` to the frozen verified engine SHA. Monitor unexpected CI fanout, Dev overwrites, D1 migrations, Smoke, Rollback and release outcomes.
- [ ] If G3 fails, use the documented pinned-ref/tag contingency while preserving schema safety; never roll back database migrations automatically.

## G4 — Delete old implementation, then consider provider resources

- [ ] Remove old CI category inputs, target-switch booleans, redundant v2 DAG branches, per-kind output shims, fake expression/job emulator and stale tests/docs. Run search on all callers to prove the old symbols have no active use.
- [ ] Verify that no unrelated v1/v1.5 consumer still relies on a legacy entrypoint before deleting it; avoid retaining unused compatibility by default.
- [ ] Audit the old `blog-preview` Worker, any obsolete `*-dev` workers, DNS, Access, preview ledger and stateful bindings. This task is **inventory only** until explicit cleanup approval.
- [ ] If approved, run dry-run Preview GC and side-effect checks before any time-based deletion; preserve retained fixed `dev` previews and active branches/PRs. Require provider/resource-specific confirmation for data, routes and credentials.
- [ ] Update canonical docs/specs after observed cutover; archive or mark superseded historical designs without rewriting their past claims.

## Completion criteria

- [ ] One documented event matrix, one PR Gate contract and one target/driver delivery engine are operative across all supported apps.
- [ ] Feature Push fast Verify / PR Test split is observable, no unapproved shared Dev overwrites, and no hidden cross-run merge-gate dependency remains.
- [ ] Existing D1 safety, resource isolation, smoke, rollback, security, release and deployed OIDC cases pass.
- [ ] Obsolete CI/Delivery selectors and redundant simulation infrastructure are deleted, with evidence for the preserved cases.
- [ ] No deployment, migration, secrets, DNS or data mutation occurred during the **OpenSpec-only** review PR.

