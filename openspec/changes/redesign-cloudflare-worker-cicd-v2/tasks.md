# Tasks: Cloudflare Worker CI/CD v2 — branch-push previews

## Phase 0 — OpenSpec (this planning PR; no deployment)

- [x] Review shared v1/v1.5.0 pipeline, existing consumers and old alias limitation.
- [x] Define three strategies with security, data, migration and rollback boundaries.
- [x] Adopt user decision: **every eligible non-main branch push creates/updates a stable branch URL even without PR**; lifecycle is branch-owned, not PR-owned.
- [x] Supersede legacy PR-alias feedback issue #33 under parent #43.
- [ ] Review per-Worker Preview bindings and authentication threat model; approve resource policies.
- [ ] Validate exact Native Preview CLI (Wrangler >=4.135.0), preview --json, deletion, assets/compatibility rules, quota/cost and secrets behavior on pinned dependencies.

## Phase 1 — .github shared v2 (implementation PR; no existing v1 changes)

- [ ] Add new v2 reusable workflow/entrypoint and event-specific routing; preserve published v1 callers.
- [ ] Add branch-identity normalization + unconditional short hash, deterministic Worker-scoped Preview name, safe collision handling.
- [ ] Trigger trusted same-repo non-main **push** Preview after tests; PR required=false; fork PR tests-only, no credentials.
- [ ] Implement Native Preview binding preflight, Preview-safe resource provisioning/migrations, build, create/update, JSON URL extraction and URL-specific smoke.
- [ ] Publish Deployment/summary for branches without PR; if a PR exists, update one idempotent bot-owned URL/status, guarding older runs.
- [ ] Implement default-branch GitHub branch `delete` lifecycle and scoped Worker/resource cleanup; do not delete on PR close while branch exists.
- [ ] Keep Hybrid Dev on explicit protected ref/dispatch, separate concurrency keys, no branch overwrite.
- [ ] Preserve gated Production release/verify and migration-aware policy-based recovery.
- [ ] Add actionlint, event-matrix, branch without PR, same/different branches, negative binding, fork token, branch-delete/PR-close, race, and rollback tests.

## Phase 2 — heaven-www pilot

- [ ] Enable non-main push trigger (current caller only handles `main` push); pin tested v2 SHA initially.
- [ ] First feature push with **no PR** creates URL; subsequent push keeps it; another branch has another URL.
- [ ] Verify exact-URL smoke/Assets, PR feedback if opened later, branch-delete cleanup, Production unaffected.
- [ ] Tag immutable v2.0.0 after verified pilot; prove caller pin revert.

## Phase 3 — blog and simpler stateful consumers (separate consumer PRs)

- [ ] Blog: stop branch pushes to shared `blog-preview`; review path filters so every branch push creates/updates Preview even without PR.
- [ ] Blog: branch-scoped D1 + per-branch migrations, non-Prod R2 and isolation, Preview Base secrets, rate limits, notification/TTS/analytics side-effect restrictions.
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
