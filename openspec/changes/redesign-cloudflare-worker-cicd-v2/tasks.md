# Tasks: Cloudflare Worker CI/CD v2 — branch-push previews

## Phase 0 — OpenSpec (this planning PR; no deployment)

- [x] Review shared v1/v1.5.0 pipeline, existing consumers and old alias limitation.
- [x] Define three strategies with security, data, migration and rollback boundaries.
- [x] Adopt user decision: **every eligible non-main branch push creates/updates a stable branch URL even without PR**; lifecycle is branch-owned, not PR-owned.
- [x] Supersede legacy PR-alias feedback issue #33 under parent #43.
- [ ] Review per-Worker Preview bindings and authentication threat model; approve resource policies.
- [x] Validate Native Preview CLI (Wrangler 4.151.1): `--json` shape, deletion + 10025, explicit-id requirement (see design.md "Verified platform facts"). Quota/cost and Preview secrets behavior still unverified.

## Phase 1 — .github shared v2 (implementation PR; no existing v1 changes)

- [x] Add new v2 reusable workflow/entrypoint (released as `v2.0.0`) with event routing; v1 callers untouched.
- [ ] Add branch-identity normalization + unconditional short hash, deterministic Worker-scoped Preview name, safe collision handling.
- [ ] Trigger trusted same-repo non-main **push** Preview after tests; PR required=false; fork PR tests-only, no credentials.
- [ ] Implement Native Preview binding preflight, Preview-safe resource provisioning/migrations, build, create/update, JSON URL extraction and URL-specific smoke.
- [ ] Publish Deployment/summary for branches without PR; if a PR exists, update one idempotent bot-owned URL/status, guarding older runs.
- [x] Implement default-branch GitHub branch `delete` lifecycle: retire the GitHub ledger and keep the Preview (no deletion on PR close or branch delete).
- [ ] Optional scheduled sweep to reclaim Previews of deleted branches (not implemented; manual `wrangler preview delete` for now).
- [ ] Keep Hybrid Dev on explicit protected ref/dispatch, separate concurrency keys, no branch overwrite.
- [ ] Preserve gated Production release/verify and migration-aware policy-based recovery.
- [ ] Add actionlint, event-matrix, branch without PR, same/different branches, negative binding, fork token, branch-delete/PR-close, race, and rollback tests.

## Phase 2 — heaven-www pilot

- [x] Enable non-main push trigger; pinned exact shared SHA during the pilot, now the immutable `v2.0.0` tag (heaven-www#48, #49, #51).
- [x] First feature push with **no PR** creates URL; subsequent push keeps it; another branch has another URL.
- [x] Exact-URL smoke/Assets; PR feedback when opened later (no duplicate Preview); PR close keeps the Preview; Production `versions upload/deploy` verified on main. Branch-delete cleanup was verified in v2.0–v2.3 (deleted only that Preview) and is **superseded**: since v2.4 a deleted branch keeps its Preview (owner decision).
- [x] Smoke-failure recovery drill (redeploys the last successful Deployment SHA, run stays red).
- [x] Tag immutable `v2.0.0` (and floating `v2`) after the verified pilot.
- [ ] Prove caller pin revert; concurrent-push/force-push race, fork PR and build-failure-retains-Preview tests on real Cloudflare.

## Phase 3 — blog and simpler stateful consumers (separate consumer PRs)

- [ ] Blog: stop branch pushes to shared `blog-preview`; review path filters so every branch push creates/updates Preview even without PR.
- [ ] Blog: shared non-prod preview D1 + migrations before publish, dedicated preview R2 (no per-branch resources — owner decision), Preview Base secrets (pending), rate limits, notification/TTS/analytics side-effect restrictions. PR firstsun-dev/blog#448 (draft).
- [ ] Blog: decouple `SITE_URL` from voice Preview QA gating; preserve noindex, SEO/E2E, content-only optimization and preview-URL smoke.
- [ ] `heaven-monorepo/apps/checkin`: D1 migrations, auth/ID integrations, independent Worker and branch preview identity.
- [ ] `heaven-monorepo/apps/test-client`: safe auth target, independent Worker and branch URL.
- [ ] `innovation-apps/apps/diet-manager`: separate R2 from `general-bucket`, inspect D1/Secrets Store/FatSecret and outbound side effects; provision branch-safe data before preview.

## Phase 4 — Hybrid consumers (separate consumer PRs)

- [ ] `heaven-monorepo/apps/id`: every affected non-main push gets a Native Preview, distinct from protected persistent Dev Cron; D1/R2, Access and integration gates.
- [ ] `innovation-apps/apps/notification-hub`: every affected non-main push gets HTTP Native Preview; Queue/Cron tests use protected Dev; D1/Queue/chat destinations isolated.
- [ ] In monorepos only changed or explicitly selected Worker apps deploy; a branch may have multiple **independent** Worker Preview URLs, never one shared resource.

## Phase 5 — infra-config, governance and exceptions

- [ ] `infra-config`: Terraform/IaC for durable Preview-safe baselines where applicable; ephemeral per-branch resource ledger, scoped provisioning/cleanup and quota/drift/TTL review.
- [ ] Report skipped previews, stale branches, orphan resources, failure/cost metrics to monitoring; confirm observability.
- [ ] `anas-mcp`: keep production-only/CI-only for non-main pushes; do not weaken Cloudflare Access or enable public Preview URLs. Separate threat model required for any future Preview.
- [ ] Audit other repositories for Workers; add migration only after explicit classification/review.
- [ ] Close #43 after approved implementation coverage and evidence, not upon planning merge.

## Rollout constraints

Each repository uses an independent feature branch and focused PR. Reuse existing Issues/PRs or create the smallest missing tracking record. Merge shared contract before consumers; trial with a commit SHA, release a tested immutable v2 tag. Do not auto-merge. Do not edit live credentials, Production data or Cloudflare runtime during OpenSpec planning.
