# Design: Cloudflare Worker CI/CD v2 refactor

> **Decision (2026-10-21):** v2 is refactored **in place** and breaking changes are allowed. No `_cf-worker-v3.yml`, no `@v3`, no legacy-input adapters. [PR #56](https://github.com/firstsun-dev/.github/pull/56) remains the historical record of previously shipped v2.x behavior; [tasks](./tasks.md#v2-work-handoff-and-non-duplication) inherit unfinished obligations. v1/v1.5 are unchanged.

## Baseline and versioning

`_cf-worker-template.yml` (v1) is untouched. `_cf-worker-v2.yml` previously included Plan, unit/integration/E2E, Test Gate, Preview, PR link, Cleanup, Persistent Dev, Production, verification and recovery jobs; some consumer pipelines add Detect, Prepare, Lint and Build Check. This change replaces the **orchestration and public interface** of `_cf-worker-v2.yml` directly (v1 stays as is). Immutable `v2.x.y` tags continue to resolve to the old workflow; the floating `@v2` tag moves only at the reviewed cutover, after consumers are ready. The original v2 OpenSpec remains a record of its branch-preview decisions; this change overrides only explicitly altered behavior.

### Normal push DAG

```text
Single Worker:          CI  ──┐
                            ├── Migration ── Deploy ── Release (main only)
                        E2E ──┘

Monorepo:       Detect Changes
                  ├── affected Worker A: CI ∥ E2E → Migration → Deploy
                  └── affected Worker B: CI ∥ E2E → Migration → Deploy
                                  required prod Deploys → Release
```

CI and E2E are **different jobs scheduled in parallel**, never one waiting on the other. They each check out the same immutable target SHA but may build with different variables. E2E variants may form a matrix without preventing unrelated variants from finishing. Within CI, Setup, Lint, Typecheck, Unit, Integration and target-aware Build Validation normally remain **steps**; only evidence-backed runtime or isolation problems justify splitting them into more jobs. No separate Plan, Test Gate, Prepare or generic Build Job. `Detect` is a dedicated lightweight job only when needed for monorepo app selection.

`Migration` starts after all *required* CI/E2E results succeed, and rejects failure/cancellation; an intentionally skipped optional E2E must not block it. `Deploy` starts after a successful Migration or a confirmed no-D1/no-migration skip. A successful `Release` is allowed only after all relevant main-target deployments and smoke verifications have succeeded. A consumer may retain specialized optional main-only verification jobs, but they must have explicit ownership and must not duplicate ordinary E2E/build tests.

### Events and trusted targets

| Event | Expected action |
|---|---|
| Trusted, eligible non-main push | CI ∥ E2E → shared Preview Migration → Native Preview Deploy/smoke |
| Trusted main push | CI ∥ E2E → Production Migration → Production Deploy/smoke → optional Release |
| Same-repo PR | Feedback/required checks without a competing second Preview publish or duplicate full push tests |
| Fork PR | Credential-free tests only; no Preview/Production secrets or deploy |
| Branch delete | Mark matching Deployment ledger inactive; **keep Native Preview and data**, per existing v2 owner policy |
| PR closed/merged | Do not remove a branch Preview solely for this event |
| Approved Hybrid Dev dispatch/ref | Separate protected Dev delivery; never a side effect of a feature-branch push |
| Production-only Worker | CI-only on non-main; main may follow guarded Production delivery |

For a single-Worker Preview-enabled repo, an eligible non-main push includes docs-only pushes under the existing v2 URL contract. For a monorepo, `Detect` selects **only changed or dependency-affected Workers**, including initial feature-branch pushes; unaffected Workers do not get a gratuitous deployment. E2E-only changes may run E2E without publishing an unchanged Worker; migration-only changes validate/apply the D1 migration and require a deploy only when the consumer's release/compatibility contract calls for it. Detect must account for the application/package dependency graph, changed shared tests, migrations, lockfiles, workflow config, and new-branch/force-push diff failures. GitHub path filters must not suppress branch-delete lifecycle handling.

### Environment and build contract

Keep per-caller command/environment customization rather than introducing a universal config generator.

| Job | Inputs / source | Boundary |
|---|---|---|
| CI | app commands, optional `ci_env` and nonsecret target-aware build-validation variables | no Production deploy tokens |
| E2E | `test_e2e_env`, optional Mailpit, mock services, local DB, local URL and test build | test-only credentials |
| Migration | trusted `target`, migration command/config, resource-specific D1 credentials | no frontend build variables |
| Deploy | `build_env_prod` or `build_env_preview`, target-specific app command, deployment credentials | only chosen environment credentials |
| Smoke (Deploy step) | actual resolved `TARGET_URL`, app `smoke_env`, scoped test credentials | never infer Preview URL from a constant |

These are the **refactored v2 interface names**; the old v2 names (`preview_build_cmd`, `preview_*_cmd` provisioning hooks, `preview_smoke_env`, `verify_cmd`, ...) are removed. Preserve v1's ability to inject newline-separated KEY=VALUE values *per job*, and validate variable names/format safely instead of unsafely interpolating expressions into shell scripts. Runtime Worker `vars`, resource bindings and secrets are distinct from build-time public variables. No secret shall enter client-side `VITE_*`/`PUBLIC_*` values, logs or test reports.

CI performs a build **validation** under a documented target-aware configuration when needed; it does not produce a promoted deploy artifact. E2E performs its own local/test build; it may run with localhost origin, mock OAuth, Mailpit and test D1. Deploy checks out the *same source SHA*, independently builds for Prod or Preview, validates the **effective generated Wrangler config** (critical for Astro Cloudflare adapter output), publishes, then probes the exact returned URL. Build must not accidentally inherit `CLOUDFLARE_ENV=dev` from v1: Native Preview `[previews]` config and a named `[env.dev]` are different concepts.

Never depend on GitHub Build Artifacts between jobs. Use fast package caches only as expendable optimization, not artifact promotion. Optional JUnit/report annotations are preferred; Playwright traces/screenshots may be uploaded on failure with short retention (default goal 1–3 days), and artifact quota/upload errors must not hide original test failures. Setup may be a reusable **composite action/step**, not a mandatory extra job; audit GitHub-hosted versus self-hosted runner Node/pnpm/store-path assumptions.

### D1 Migration contract

`Migration` is a distinct reusable job for any Worker with applicable remote D1 migrations, not a step inside Deploy. It uses the **same app-owned migration SQL** for Production and Preview, selecting a different physical D1 by verified target/config. No per-branch migration DB and no centralized migration management service.

- Production targets the explicitly configured Production D1; Preview targets the D1 referenced by `[previews.d1_databases]` (or JSON equivalent), **not merely the old `--env dev` Worker deployment**. A dedicated Wrangler migration config referencing the same Preview D1 is allowed; do not duplicate SQL.
- Validate migration directory, target resource ID, permission scope, current migration history and pending files. Re-running the same applied migration skips it; previously applied filenames must not be silently reused with different SQL. Inconsistent/conflicting branch history fails closed with actionable diagnostics.
- On a remote migration failure, fail Migration and prevent that run's Deploy. Do not treat blind retries as a solution to schema conflicts.
- With no pending migration, Migration succeeds without modifying D1. Workers with no D1 bypass Migration cleanly. PRs never migrate remote D1.
- Two Branch Previews may share a D1 and R2 baseline without sharing their published code/URLs; known limit: a forward migration from one branch can break another branch's code. Require forward-compatible schema changes and state the risk; do not promise independent data isolation.
- A Worker code rollback never automatically rolls back the D1 schema.

**Critical concurrency condition:** GH Actions job-level group on Migration and a separate group on Deploy do not protect the gap between them. Select/validate a serialization strategy covering the whole state-mutating migration-to-deploy transaction for a given physical D1+Worker target. When safe cross-run coordination cannot be guaranteed, use conservative target-level pipeline serialization before rollout; CI and E2E within each run must still be parallel. Assess GitHub pending-run cancellation/queue semantics explicitly. If a D1 is shared across repositories or persistent Dev, use a cross-repo coordination mechanism or document and gate the exception. Protect Prod and Preview with different scopes.

**Implementation choice (Phase 1, mocked-test evidence only):** no custom distributed lock. A job-level `concurrency` group cannot span Migration and Deploy, and the default pending slot silently cancels an older queued run (which would drop another branch's push on a shared Preview D1). The shared workflow therefore sets **workflow-level** `concurrency` with `queue: max` (FIFO, max 100 pending; extra runs are cancelled = fail-closed; incompatible with `cancel-in-progress: true`), so the whole run is the serialized unit and CI/E2E stay parallel inside it. The group identifies the **shared mutable resource**, not the branch: `<app>-prod`; one group for the shared Preview D1 (`d1_lock_scope`, default app name) when migrations exist; per-branch for D1-less Workers; a private group for PR/delete/tag/persistent-dev runs. The `cfv2-` prefix is static and `github.workflow` is not used, to avoid the documented caller/called-workflow group deadlock. A queued run that is no longer the branch tip applies no migration and publishes nothing.

Everything that can be validated without mutating remote state (trusted target and credentials, Preview identity and ledger ownership, source binding policy, exact migration target, branch tip) runs inside the Migration job **before** the first remote command; Deploy re-checks ownership and tip before publishing. There is no custom migration command override: the target D1 is always derived and validated from the source config.

Release is gated on an explicit `deployed_and_verified` Deploy output (this run published its own SHA and smoke passed); a stale/skipped/failed/rolled-back Deploy never releases.

Known limits (documented, not resolved): a repo-local group does not protect a D1 shared with another repository or persistent Dev; Environment approvals are asked for both Migration and Deploy and a run waiting for approval holds the group; top-level `concurrency` in a *called* workflow is not explicitly confirmed by the GitHub docs read and `queue` is unknown to actionlint 1.7.12, so both must be proven in a scratch repository before the floating `@v2` moves; SQL checksum/drift detection is not implemented. Details and test evidence: `docs/cf-worker-v2-refactor.md` in the implementation PR (#58).

### Unified Deploy contract

One reusable `Deploy` job implements target-specific behavior; do not define parallel Production and Preview Deploy job implementations for the normal pipeline.

1. Verify trusted event/ref and `target=prod|preview`; resolve Worker, account, branch identity and source SHA.
2. Checkout exact SHA; Setup dependencies; verify nonsecret app build inputs and environment-scoped secrets.
3. Preflight the **source and generated effective Wrangler configuration**, storage bindings, auth and side effects.
4. Execute target-specific build once **inside this job**.
5. Production: upload/deploy a traceable Worker version; Preview: `wrangler preview --name <branch-slug> --worker-name <hosting-worker> --json`.
6. Read Preview stable URL from Wrangler JSON (do not fabricate), record GH Deployment/summary and probe exact target URL.
7. If smoke fails, mark deployment failed and follow a documented, tested code recovery policy; recovery never claims to revert D1.
8. Reject stale Preview pushes and branch normalization collisions before overwriting an existing Preview.

Reuse tested v2 identity ledger, smoke, production version/recovery and safety functionality; simplify orchestration, not the correctness checks. Protected Persistent Dev and branch deletion/PR URL linking may use separate event-specific handlers outside the normal core DAG, without duplicating whole pipelines. Production and Preview credentials must not coexist in an untrusted execution context: validate trust and GitHub Environment protection, not merely a caller-supplied target string.

### Preview and special worker policy

One Worker may host many stable branch Native Preview URLs with **one shared Preview base** (config/secrets/D1/R2); this does not imply one environment per branch. Preserve `preview_worker_name` when a Production Worker keeps workers.dev Preview disabled (Blog uses `blog-preview`). Never use Prod D1 or writable Prod R2 as a Preview fallback. Confirm Cloudflare Preview Base secrets propagation/rotation and bindings to other services.

Heaven ID: hybrid Worker with protected Cron Dev and OAuth callback/cookie/CORS validation; candidate Preview D1 is `heaven-id-db-dev` and R2 `general-bucket-dev`, but whether sharing D1 with persistent Dev is acceptable remains **UNAPPROVED**. Blog: shared `firstsun-blog-db-preview` and dedicated `firstsun-blog-shinyi-muyu-voice-cache-preview`, noindex/analytics and exact Preview URL, Production D1 `firstsun-blog-db`. Notification Hub: Native Preview handles HTTP only; Queue/Cron must be validated separately. ANAS MCP remains production-only until an approved Cloudflare Access-safe Preview design.

### Rollout and release controls

Stage shared workflow + contract tests, then Blog and Heaven ID pilots, then monorepo selective apps, then remaining opt-in consumer repos. Consumer PRs keep existing tests, release behavior and required real-world smoke; no bulk migration without inventory. Pin a candidate SHA for pilots; publish an immutable `v2.x.y` and move floating `@v2` only after acceptance. Phase 1 (shared implementation) changes no consumer repo, live credential, Cloudflare resource, release tag or production.

## Outstanding approvals

- Heaven ID shared Preview D1 versus protected persistent Dev D1: approve explicitly.
- Locking design and runner availability, including cross-repo database consumers: prove before implementation acceptance.
- Repo-specific E2E variants/post-deploy special checks, release policy and build-time origins: capture in consumer OpenSpec.
