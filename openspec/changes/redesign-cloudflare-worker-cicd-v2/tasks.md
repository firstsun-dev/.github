# Tasks: Cloudflare Worker CI/CD v2 — branch-push previews

> **Lifecycle status (2026-10-21): v2 partially implemented, now maintenance-only.** Retain checked boxes as historical **implementation evidence**, not proof that each consumer has merged, deployed or passed every real-world test; retain unchecked boxes as unresolved. **Do not continue the unfinished v2 rollout as a second competing roadmap.** See [vNext OpenSpec (PR #57)](https://github.com/firstsun-dev/.github/pull/57) for the active refactor and detailed [vNext task mapping](https://github.com/firstsun-dev/.github/blob/docs/openspec-cloudflare-worker-cicd-vnext/openspec/changes/refactor-cloudflare-worker-cicd-vnext/tasks.md). Old v2 major tags and live callers remain supported; security/hotfix maintenance is allowed.

## Transfer register — v2 to vNext

| v2 item / evidence | Status as recorded in this v2 change | Ownership after handoff |
|---|---|---|
| Shared Native Preview core, stable branch URLs, ledger, smoke, recovery; heaven-www pilot | Implemented and verified in specified cases; keep running | vNext Phase 1 reuses tested primitives; no reimplementation solely to repeat v2 |
| Blog preview code and bindings in firstsun-dev/blog#448 | Draft PR work, **not automatically merged or production-deployed** | vNext Phase 2 reviews/adapts existing PR; complete Preview Base secrets and authenticated content-smoke evidence |
| Real fork PR, concurrent/force pushes, build-failure preservation, quota, pin-revert/rollback evidence | **Not fully verified** | vNext Phases 1, 2 and 5 as explicit acceptance gates |
| Protected Hybrid Dev Cron/Queue behaviour, including live test evidence | Entrypoint present; consumer evidence incomplete | vNext Phases 2 (Heaven ID) and 4 (Notification Hub) |
| Heaven ID, Checkin, Test Client and affected-app detection | Consumer rollouts incomplete | vNext Phases 2–3, with dependency-aware monorepo execution |
| Diet Manager and Notification Hub production/preview rollout | Consumer rollouts incomplete | vNext Phase 4; preserve state, notification and Queue/Cron boundaries |
| Shared Preview IaC, stateful audit, ANAS production-only and remaining Worker inventory | Partial / pending by consumer | vNext Phase 4, then Phase 5 governance; no Access bypass |
| Preview stale/orphan reporting, quota and cost observability | Pending | vNext Phase 5 acceptance/governance |
| Automatic TTL/orphan Preview sweep | **Deferred, not implemented**; manual deletion remains | Separate explicit follow-up, **not assumed** in vNext unless approved; branch delete continues to retain Preview |
| Closing tracking issue #43 | **Not done** | Only after approved vNext rollout acceptance; not upon merging #56 or #57 |

### Handoff rules

- The v2 checkboxes below remain an honest historical status register; do **not** mark unfinished tasks complete just because vNext now owns them.
- Future feature work and new consumer migrations target vNext; existing v2 consumers remain supported and may receive critical fixes.
- The v2 design documents current shipped behavior, while vNext specifies the future DAG, separate Migration job, build/env interfaces and concurrency gate. Do not mix the two into one currently supported contract.
- The historical [v2 planning PR #44](https://github.com/firstsun-dev/.github/pull/44), [v2 state/docs PR #56](https://github.com/firstsun-dev/.github/pull/56), [vNext OpenSpec (PR #57)](https://github.com/firstsun-dev/.github/pull/57) and issue #43 remain cross-linked for traceability.


## Phase 0 — OpenSpec (this planning PR; no deployment)

- [x] Review shared v1/v1.5.0 pipeline, existing consumers and old alias limitation.
- [x] Define three strategies with security, data, migration and rollback boundaries.
- [x] Adopt user decision: **every eligible non-main branch push creates/updates a stable branch URL even without PR**; lifecycle is branch-owned, not PR-owned.
- [x] Supersede legacy PR-alias feedback issue #33 under parent #43.
- [ ] Review per-Worker Preview bindings and authentication threat model; approve resource policies.
- [x] Validate Native Preview CLI (Wrangler 4.151.1): `--json` shape, deletion + 10025, explicit-id requirement (see design.md "Verified platform facts"). Quota/cost and Preview secrets behavior still unverified.

## Phase 1 — .github shared v2 (implementation PR; no existing v1 changes)

- [x] Add new v2 reusable workflow/entrypoint (released as `v2.0.0`) with event routing; v1 callers untouched.
- [x] Branch-identity normalization to a plain slug (owner decision: no prefix/hash), ledger-based collision handling (fail closed in Plan).
- [x] Trigger trusted same-repo non-main **push** Preview after tests; PR required=false. (Fork-PR credential isolation is by design — no deploy job on `pull_request` — but not exercised on a real fork.)
- [x] Native Preview binding preflight (source + build-generated config, explicit ids), optional provision/migrate hooks, build, create/update, JSON URL extraction and exact-URL smoke (status-aware probe, Access-aware).
- [x] Publish Deployment/summary/job environment URL for branches without PR; idempotent PR comment when a PR exists; stale-SHA guards (authenticated lookup).
- [x] Implement default-branch GitHub branch `delete` lifecycle: retire the GitHub ledger and keep the Preview (no deletion on PR close or branch delete).
- [ ] Optional scheduled sweep to reclaim Previews of deleted branches (not implemented; manual `wrangler preview delete` for now).
- [x] Hybrid Dev path on explicit protected ref/dispatch with separate concurrency keys implemented (not yet exercised by a consumer).
- [x] Production release/verify and policy-based recovery preserved (Production upload/deploy verified on heaven-www `main`; automatic rollback not exercised).
- [x] Contract CI: actionlint+shellcheck, event matrix, identity/collision, negative binding, probe, ledger, publish and workflow-gating tests (stub-based) plus real-Cloudflare runs on heaven-www and Blog.
- [ ] Still untested: concurrent pushes / force-push ordering, real fork PR, Hybrid Dev background behaviour, quota exhaustion.

## Phase 2 — heaven-www pilot

- [x] Enable non-main push trigger; pinned exact shared SHA during the pilot, now the immutable `v2.0.0` tag (heaven-www#48, #49, #51).
- [x] First feature push with **no PR** creates URL; subsequent push keeps it; another branch has another URL.
- [x] Exact-URL smoke/Assets; PR feedback when opened later (no duplicate Preview); PR close keeps the Preview; Production `versions upload/deploy` verified on main. Branch-delete cleanup was verified in v2.0–v2.3 (deleted only that Preview) and is **superseded**: since v2.4 a deleted branch keeps its Preview (owner decision).
- [x] Smoke-failure recovery drill (redeploys the last successful Deployment SHA, run stays red).
- [x] Tag immutable `v2.0.0` (and floating `v2`) after the verified pilot.
- [ ] Prove caller pin revert; concurrent-push/force-push race, fork PR and build-failure-retains-Preview tests on real Cloudflare.

## Phase 3 — blog and simpler stateful consumers (separate consumer PRs)

- [x] Blog: branch pushes no longer deploy to the shared `blog-preview` (Previews are *hosted under* it via `--worker-name`); path filter removed for branch pushes, kept for `main`.
- [x] Blog: shared non-prod preview D1 (migrations run before publish — accepted, uncontrolled from feature branches) and dedicated preview R2, no per-branch resources (owner decision), rate-limit slots, deny-list of Production resources. PR firstsun-dev/blog#448 (draft).
- [ ] Blog: Preview Base secrets (HMAC, TTS, ADMIN_PASSWORD, RESEND/NOTIFY) and an Access service token for content smoke are not provisioned.
- [x] Blog: voice Preview QA gating decoupled from `SITE_URL`; E2E/content-only optimization preserved; preview-URL smoke asserts Access protection (noindex/404 need the service token).
- [ ] `heaven-monorepo/apps/checkin`: D1 migrations, auth/ID integrations, independent Worker and branch preview identity.
- [ ] `heaven-monorepo/apps/test-client`: safe auth target, independent Worker and branch URL.
- [ ] `innovation-apps/apps/diet-manager`: separate R2 from `general-bucket`, inspect D1/Secrets Store/FatSecret and outbound side effects; provision branch-safe data before preview.

## Phase 4 — Hybrid consumers (separate consumer PRs)

- [ ] `heaven-monorepo/apps/id`: every affected non-main push gets a Native Preview, distinct from protected persistent Dev Cron; D1/R2, Access and integration gates.
- [ ] `innovation-apps/apps/notification-hub`: every affected non-main push gets HTTP Native Preview; Queue/Cron tests use protected Dev; D1/Queue/chat destinations isolated.
- [ ] In monorepos only changed or explicitly selected Worker apps deploy; a branch may have multiple **independent** Worker Preview URLs, never one shared resource.

## Phase 5 — infra-config, governance and exceptions

- [ ] `infra-config`: Terraform/IaC for the durable shared Preview baselines where applicable (D1/R2 for Blog); per-branch resource ledger is only needed if an owner opts into branch-scoped resources.
- [ ] Report skipped previews, stale branches, orphan resources, failure/cost metrics to monitoring; confirm observability.
- [ ] `anas-mcp`: keep production-only/CI-only for non-main pushes; do not weaken Cloudflare Access or enable public Preview URLs. Separate threat model required for any future Preview.
- [ ] Audit other repositories for Workers; add migration only after explicit classification/review.
- [ ] Close #43 after approved implementation coverage and evidence, not upon planning merge.

## Rollout constraints

Each repository uses an independent feature branch and focused PR. Reuse existing Issues/PRs or create the smallest missing tracking record. Existing v2 releases are already published and maintained; for **new** CI/CD work, approve the vNext contract before pilot consumer migration, test at an immutable SHA, and release a separately versioned new major after evidence. Do not auto-merge. Do not edit live credentials, Production data or Cloudflare runtime during OpenSpec planning.
