# Tasks: Cloudflare Worker CI/CD vNext refactor

## Phase 0 — specification only (this PR)

- [x] Audit current v1/v2 shared job topology and selected consumers.
- [x] Document agreed architecture: Detect (monorepo) → [CI ∥ E2E] → Migration → Deploy → optional Release.
- [x] Document target-specific builds, env separation, artifact-free job handoff and shared Preview resources.
- [x] Preserve previously approved Native Preview identity, retention, protected Hybrid Dev, production recovery and v1/v2 compatibility.
- [x] Record v2 partial implementation evidence and supersession: link v2 OpenSpec/docs PR #56 and map every remaining obligation below; do not auto-complete transferred work.
- [ ] Review/approve this delta and explicitly resolve or assign the open architecture decisions.
- [ ] Do not modify workflows, scripts, secrets, Cloudflare runtime, or consumer repos under this doc-only PR.

## v2 work handoff and non-duplication

The preceding v2 design was **partially delivered**, not cancelled or erased. [PR #56](https://github.com/firstsun-dev/.github/pull/56) records the currently shipped v2.x behavior and an honest carried/unverified task register. The v2 reusable workflow and already migrated consumers continue to run and receive necessary fixes. **Do not begin a second unfinished v2 feature rollout in parallel with vNext**; target future implementation and remaining consumer adoption to this vNext change. Historical v2 requirements remain valid for v2 callers until a separately reviewed migration.

Cross-repository handoff (evidence preserved; no box auto-marked complete):

| Prior v2 work | Evidence / remaining gap | New vNext owner |
|---|---|---|
| Native Preview CLI, URL identity, ownership ledger, preflight, last-good recovery; heaven-www pilot | Already delivered and partly validated; **reuse**, not rebuild | Phase 1: adapt proven primitives to lean DAG, preserve regression tests |
| v2 Plan, multiple test jobs, Test Gate and inline Preview/Production Migration | Currently shipped topology; **superseded as a future architecture**, not retroactively changed | Phase 1: CI ∥ E2E → separate Migration → single target-aware Deploy |
| Optional test skip / `always()` fix, Access-aware smoke and hosting-worker `preview_worker_name` | Already implemented; keep test coverage | Phase 1 contract tests; Phase 2 production/preview pilot |
| Concurrency, force-push ordering, real fork PR, build-failure-retains-preview, pin revert/rollback, quota | Real-world verification incomplete; **must not be marked passed** | Phases 1, 2 and 5 acceptance gates |
| Blog shared Preview D1/R2, URL/noindex/QA work | Present in [Blog PR #448](https://github.com/firstsun-dev/blog/pull/448) (Draft), not proof of merged rollout | Phase 2 pilot; reuse/adapt existing PR rather than duplicating it |
| Blog Preview Base secrets and Access-authenticated content-smoke checks | Unprovisioned/unverified in v2 register | Phase 2 explicit blockers/acceptance, no premature green status |
| Heaven ID Native Preview, OAuth, protected Cron Dev | Consumer still pending; Dev D1 sharing requires approval | Phase 2 Heaven ID pilot, Phase 3 affected-app deployment |
| Checkin, ID Test Client, dependency-aware monorepo change detection | Consumer work pending | Phase 3 ID/Checkin/Test Client |
| Diet Manager / Notification Hub, protected Queue/Cron Dev | Consumer work pending | Phase 4 adoption and Hybrid tests |
| D1/R2 IaC, stateful-resource audit, ANAS Access exception, remaining worker inventory | Incomplete or exception requiring preservation | Phase 4 resource/governance work |
| Orphan, quota, failure and cost monitoring | Outstanding | Phase 5 acceptance and ongoing governance |
| Optional auto-sweep of deleted-branch Previews | **Not implemented; deferred** | Separate owner-approved follow-up, not assumed part of vNext; existing delete-keeps-Preview policy stays |
| Tracking issue #43 closure | Not ready | Phase 5 only, after verified vNext consumer rollout |

This register establishes **ownership**, not completion. Do not reinterpret `[x]` in v2 as a promise that a draft consumer PR has merged or every test has been exercised; do not reinterpret `[ ]` as completed merely because the task moved. Preserve both [v2 docs PR #56](https://github.com/firstsun-dev/.github/pull/56) and [v2 planning PR #44](https://github.com/firstsun-dev/.github/pull/44) as an auditable history.

## Phase 1 — reusable contract (separate implementation PR)

- [ ] Introduce a new-major opt-in shared reusable entrypoint; do not change existing v1/v2 major tags/callers.
- [ ] Build minimal job DAG with CI and E2E parallel; independently gated Migration, unified target-aware Deploy, optional Release.
- [ ] Keep Setup/Lint/Check/Unit/Integration/Build Validation within CI steps by default; remove unnecessary Plan/Test Gate/Prepare jobs.
- [ ] Preserve optional E2E browser matrix, skip handling, test reports, Mailpit and failure status propagation.
- [ ] Make remote D1 migrations a reusable **distinct job**, with validated prod/preview target and migration history checks; bypass for no-D1 apps.
- [ ] Prove migration-to-deploy serialization covers the entire state-mutation interval, including cross-run pending/cancellation behavior.
- [ ] Reuse v2 branch identity/collision ledger, stale SHA checks, Wrangler Preview publishing, URL lookup, smoke and code recovery.
- [ ] Keep Production ref protection, scoped env/credentials, fork PR restrictions and version-aware rollback.
- [ ] Preserve app-owned per-job env values; safely validate/import multiline KEY=VALUE inputs; no broad secret inheritance by default.
- [ ] Build independently in E2E/Deploy; no required GitHub build artifacts. Limit failure-only reports to short retention.
- [ ] Verify composite setup on configured GitHub-hosted/self-hosted runners; don't assume a fixed filesystem path exists everywhere.
- [ ] Add actionlint and workflow contract tests: event matrix, skips, concurrent runs, dependencies, ref/target guard, no-D1.

## Phase 2 — pilot: Blog and Heaven ID

- [ ] Blog: pilot native previews for two feature branches, stable URLs, shared `firstsun-blog-db-preview` and dedicated preview Voice R2.
- [ ] Blog: verify Astro-generated effective Wrangler config, `SITE_NOINDEX`, Analytics/Turnstile separation, local E2E build versus target Deploy build, and smoke.
- [ ] Blog: validate same-SQL migrations on prod/preview D1 through independent Migration job; reject unsafe cross-branch migration history.
- [ ] Heaven ID: approve or block use of `heaven-id-db-dev` by branch previews while protected Persistent Dev uses it.
- [ ] Heaven ID: validate OAuth callbacks, origins/cookies, Test Client, Cron Dev isolation, Preview D1/R2 and exact-URL smoke.
- [ ] Demonstrate two simultaneous branches with code isolation, same-branch update, migration/no-op/failure gates and recovery.
- [ ] Audit E2E matrix cost and preserve required Blog Safari/Lighthouse and Heaven ID login checks without duplicate generic stages.

## Phase 3 — monorepo affected-worker execution

- [ ] Replace heavyweight `prepare` with lightweight changed-file/affected-package `Detect` job where justified.
- [ ] Cover ID, Checkin and Test Client dependencies; shared packages, test helpers, D1 migrations, lockfile/config/new-branch/force-push.
- [ ] Trigger only affected-worker CI/E2E/Migration/Deploy; E2E-only changes do not deploy unchanged Worker.
- [ ] Keep individual Worker Preview names and per-app Production deployment/release ordering.
- [ ] Verify sibling Worker CI/E2E jobs can run concurrently without shared local ports/test DB interference.

## Phase 4 — remaining consumer rollout

- [ ] Migrate `heaven-www` (preserve docs-only single-worker Preview contract).
- [ ] Migrate `innovation-apps` Diet Manager and Notification Hub separately; protect Queue/Cron Dev and notification side effects.
- [ ] Retain `anas-mcp` Production Only; fork/non-main CI does not enable Access-bypassing preview.
- [ ] Inventory and classify other Cloudflare Workers; obtain app-specific resource/secret review.
- [ ] Coordinate `infra-config` only for required shared Preview baseline resource changes, not per-branch default provisioning.

## Phase 5 — acceptance and cleanup

- [ ] Review CI/E2E parallel durations, repeated build cost, cache use, GitHub artifact/quota behavior and affected-app skips.
- [ ] Verify branch deletion retains Native Preview per existing v2 policy and only retires the correct ledger identity.
- [ ] Run production rollback drill while proving D1 migrations are never implied to roll back.
- [ ] Publish immutable new-major tag after pilots pass; migrate callers in focused PRs and document compatibility.
- [ ] Retire unused workflows/duplicated configs only after all consumers are confirmed migrated; close tracking issue only after rollout acceptance.

## Work management

Parent tracking: `firstsun-dev/.github#43`. Use existing issues/PRs when applicable, open minimal new implementation tracking if needed, keep each repo on its own feature branch/PR, favor **one logical commit per PR** and squash at merge. Do not auto-merge, and do not launch implementation as part of the OpenSpec-only update.
