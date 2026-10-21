# Design: Cloudflare Worker CI/CD v2 — push-to-preview branch lifecycle

> **Lifecycle status (2026-10-21): v2 as shipped; maintenance-only.** The job graph and inline migration steps below describe existing v2 behavior, **not** the approved target architecture. New work uses [vNext OpenSpec (PR #57)](https://github.com/firstsun-dev/.github/pull/57): CI/E2E parallel, separate Migration job, one target-aware Deploy job, and independent target-specific builds. Existing v2 deployments remain supported pending explicit migration. Consult [tasks.md](./tasks.md) for carry-over and evidence status.


## Baseline and invariants

Published v1/v1.5.0 deployments still use `wrangler versions upload` + `versions deploy`, so shared Dev and Aliased Version URLs remain as-is until a caller opts into v2. Native Previews are separate deployments. Node.js 24 + pnpm 12.14.0 are the expected toolchain baseline; the installed **project** Wrangler dependency must be >= 4.135.0 and proven compatible before adoption.

**Key contract:** an eligible, trusted non-`main` branch push creates/updates a stable branch Preview **without a PR**. No branch push can deploy another branch's Preview or overwrite persistent Dev. A push failing tests, resource preflight or build leaves the previous Preview unchanged. Because `wrangler preview` updates the stable URL before post-deploy smoke, a smoke failure MUST be marked failed and trigger a separately validated recovery plan; do not claim old code is still active or invent an undocumented Native Preview rollback command.

## Event matrix

| Event | production-preview | hybrid | production-only |
|---|---|---|---|
| Trusted same-repo non-main `push` | CI + isolated branch Native Preview | Same; no persistent Dev write | CI only |
| `pull_request` opened / synchronize | CI as appropriate; attach existing branch URL if present; do not create another Preview | Same | CI only |
| Fork PR | CI without deploy credentials | Same | Same |
| Main `push` | Production deploy/verify | Same | Same |
| Approved Dev ref or dispatch | Not enabled | Protected Dev deploy + trigger-specific tests | Not enabled |
| `delete` for non-main branch | **Keep** the Preview; retire GitHub ledger only (owner decision) | Same | No Preview |
| PR closed / merged; branch not deleted | Retain branch Preview | Same | No Preview |

Single-Worker repositories must trigger preview-capable CI on all non-main branch pushes, including documentation-only commits if the requested contract is literally "push gives URL"; implement an inexpensive build/cache fast path only if it still creates or updates the Preview correctly. Existing GitHub Actions `paths` restrictions must not silently suppress the branch URL contract. In monorepos retain changed-path/dependency selection per Worker folder: create/update previews for affected Worker apps only, and report unchanged apps explicitly.

Avoid duplicate deployments from `push` and `pull_request` on the same SHA. `pull_request` is useful for reporting/testing but **push is authoritative for Preview lifecycle**. A protected non-main Dev branch may have its own Preview plus an independently approved Hybrid Dev delivery; the latter is not implied by a normal feature push.

## Proposed v2 workflow contract (new; not v1 inputs)

```yaml
deployment_strategy: production-preview  # production-preview | hybrid | production-only
preview_on_branch_push: true            # default for preview-capable v2 consumers
preview_smoke_cmd: ''
persistent_dev_ref: ''                  # required for automatic Hybrid Dev
production_rollback_policy: manual    # automatic | manual | disabled
```

Preserve app-owned build, unit/integration/E2E/migrate/verify commands, app_path, Worker name, and runner selection. Refine names with workflow_call validation; no unsupported input is presumed to exist in v1.

### Branch identity and URL

- Use `github.ref` for a `refs/heads/...` `push`, never PR number. The Preview name is the normalized branch-name slug (Cloudflare-supported charset, bounded so `<slug>-<host Worker>` is a valid 63-char DNS label; no prefix, no hash, no commit SHA — owner decision). Because normalization is lossy, the GitHub Deployment ledger records the full ref that owns each name and the run fails closed (in Plan, before tests) when a different ref already owns it. The Preview is scoped to the target Worker; two monorepo Workers may use the same branch name safely.
- Explicitly pass the name, because GitHub checkout may be detached: `pnpm exec wrangler preview --name "$PREVIEW_NAME" --json`. Parse/validate `.preview.urls[0]` from machine-readable output (per the official GitHub Actions example); do not synthesize a hostname from the branch name or assume an account subdomain.
- The Preview's stable URL is the deployment target. The separate immutable deployment URL may be recorded for audit, but never replace the stable branch link with a new URL on every push.
- Emit URL, repo, Worker, full branch ref, commit SHA, run link, and Preview identity as a job output/step summary/GitHub Deployment even without a PR. When an open same-repo PR matches that exact head branch/SHA, update one idempotent bot-owned PR status/comment; forks and ambiguous matches get no deployment feedback.
- Revalidate the branch still exists and SHA is current before creation and publishing, under a per-repo+Worker+branch concurrency key. Out-of-order runs may not regress URL state or GitHub feedback.

### Preview configuration and state

- Top-level Wrangler config is Production. New Native Previews MUST have an explicit `[previews]` (or equivalent JSON `previews`) block and resource bindings. Do not assume `[env.dev]` or `preview_urls = true` creates Native Preview isolation. Keep top-level assets and compatibility flags at their documented location; build each branch's assets before Preview deployment.
- Preview secrets use separate Preview Base/Preview-specific secrets, never Prod credentials. Base-secret changes do not automatically update existing Preview instances; include a rotation/reseed process.
- Preflight audits D1, R2, KV, Queues, Durable Objects, Containers, Workflows, Service Bindings, Hyperdrive, Secrets Store, routes, auth, outgoing webhook/API side effects, rate limits and analytics. Reject missing/unknown/Production-risk bindings and unsafe public ingress. Service bindings from Preview target the bound Worker's **Production** deployment by default; isolate, mock or explicitly deny unsafe cross-service calls.
- `blog` binds ONE shared non-production Preview D1 (`firstsun-blog-db-preview`, migrations applied to it before publish; never Production) and ONE preview R2 bucket (`firstsun-blog-shinyi-muyu-voice-cache-preview`, objects namespaced by `SHINYI_MUYU_VOICE_CACHE_ENVIRONMENT=preview`) — **no per-branch resources (owner decision)**; it cannot write to Production `firstsun-blog-assets`. Also safe Preview secrets, test-only notification/TTS/analytics, `SITE_NOINDEX=true`, and a real Preview URL in smoke. Build flags (such as voice QA) are keyed on `VITE_APP_ENV=preview`, not the old shared `SITE_URL`. Accepted limit: branches share the preview DB, so differing schema revisions can interfere; concurrent migrations may race, so the migrate step retries once.
- For other Workers, shared non-production resources need a written owner decision stating the isolation level and its limits (namespace isolation proof, or accepted shared state); branch-scoped resources are the recommended option when migrations or concurrent writes can interfere, never an unconditional requirement. Never bind a Preview to a Production resource as a fallback when provisioning/quota checks fail.
- Every Preview binding must carry an explicit resource id; adapters can add bindings at build time, so preflight audits **both** the source Wrangler config and the build-generated (redirected) config.
- `infra-config` owns durable resource baselines and IaC contracts where appropriate. Ephemeral branch-scoped resources require a lifecycle inventory/ownership ledger, idempotent provisioning, explicit quotas, cleanup permission and orphan reconciliation; do not check generated resource IDs into shared mainline config.
- Workers with protected custom-domain-only ingress, notably `anas-mcp`, remain `production-only` until a separate Access-safe preview design is approved. No public workers.dev bypass.

### Lifecycle and cleanup

```text
trusted branch push --> Plan (identity + name-ownership check) --> tests --> binding preflight + caller hooks
                    --> build with Preview vars/assets --> wrangler preview --name <stable> --json
                    --> exact-URL smoke --> Deployment/summary --> optional matching PR link

PR opened / sync ------> CI/report only (no second Native Preview for same branch)
PR closed / merged -----> keep Preview when branch still exists
branch deleted ---------> identify branch+Worker --> retire GitHub ledger; Preview and resources are KEPT
protected Hybrid Dev ---> separately approved Dev deploy/test (Cron/Queue)
main push --------------> gated Production upload/deploy/migration/verify/recovery
```

Branch deletion is handled by the repository default-branch workflow's GitHub `delete` event (`ref_type=branch`); it must not depend on the deleted branch's workflow being available. **Owner decision (2026-10-20): the Preview is kept.** Cloudflare cannot remove only the stable URL (Preview `urls` is read-only; `wrangler preview delete` removes the Preview with all deployments), so the `delete` run only retires the GitHub Deployment ledger (`inactive`, never deleted) after validating the identity and that the deleted branch owns the name, tolerates the delete/recreate race, and prints the manual `wrangler preview delete --name <slug> --worker-name <worker>` command. Nothing is reclaimed automatically; an optional ledger-driven sweep is left as future work. Never delete Prod, shared Dev, another branch's Preview, or shared non-prod resources.

## Verified platform facts (heaven-www pilot, Wrangler 4.151.1, 2026-10-20)

- `wrangler preview --name <n> --json` prints the stable URL at `.preview.urls[0]`; the hostname is `https://<preview-name>-<worker>.<account-subdomain>.workers.dev`, so `<preview-name>-<worker>` must fit one 63-char DNS label.
- A binding without an id fails the publish (`API 10021 … must have a namespace_id`). Native Previews do not auto-provision KV/D1/R2. The Astro Cloudflare adapter silently adds a `SESSION` KV; Preview builds must disable sessions (Production output stays unchanged).
- `wrangler preview delete --name <n> --skip-confirmation` succeeds once; a repeat returns `Preview not found [code: 10025]` (treat as already-deleted). It needs `CLOUDFLARE_ACCOUNT_ID` when the login has several accounts. There is no `wrangler preview list`; any unknown word after `preview` is parsed as an entry-point path.
- GitHub `delete` events run only the **default branch's** workflow file, so branch-delete cleanup cannot be exercised until the caller is merged to main. A PR close/merge with the branch kept does not touch the Preview.
- Stale-run guards must use authenticated GitHub API lookups (anonymous `git ls-remote` fails on private repos) and a failed lookup must fail the job, never read as "superseded". Pipelines feeding `GITHUB_OUTPUT` must run with `pipefail`.
- Post-publish smoke failure recovery is implemented by redeploying the newest earlier GitHub Deployment with state `success` (rebuild that SHA with the same Preview env, probe the URL, keep the run red). Drill verified.

### Visibility and gating (implementation notes)

- The Preview job declares `environment: <app>/preview` with the Preview URL, so the CI job itself shows *View deployment*; the per-branch ledger uses separate `<app>/preview/<slug>` environments.
- Downstream jobs of the optional test jobs MUST use `always()` with explicit `needs.*.result` gating: GitHub's implicit `success()` skips any job with a skipped ancestor, which silently skipped Preview **and Production** for consumers without all three test commands (found in v2.1.0–v2.5.0, fixed in v2.5.1, guarded by a static contract test).
- The smoke probe requires an explicit 2xx; a redirect to `*.cloudflareaccess.com` is reported as `access-protected` (exists, not public, content unverified). `curl -f` is never used (it accepts any 3xx).
- Preview-hosting Worker: when the production Worker keeps `previews_enabled=false` deliberately, Previews are hosted under another Worker with `--worker-name` (`preview_worker_name`).

## Hybrid and Production controls

Hybrid `heaven-id` Cron and `notification-hub` Queue/Cron are **not executed by Native Previews**. Run them in tests or protected, independently deployed persistent Dev; no generic branch push changes that environment. Ensure Dev Queue producers/consumers and Cron do not deliver real notifications or production side effects.

Production retains required quality gates, version history, migration approval and smoke. Recovery policy is per Worker: automatic rollback only with proven schema/state compatibility, otherwise manual/disabled; Worker rollback cannot undo D1 schema or third-party mutations. Separate concurrency groups and credentials by app/branch/environment.

## Release and validation

Maintain pinned v1/v1.5.0; the separately named v2 workflow has an opt-in caller interface and contract tests. `v2.0.0` (immutable) and the floating `v2` exist; additive input changes ship as `v2.x.0`. Consumers pin a SHA while validating, then the immutable tag (`uses` and `tools_ref` must be identical). Rollback is an individual caller pin revert, not repointing floating `v1`.

Test: first push without PR, second push stable URL/new SHA, two branches with independent code and resources, fork PR no token, docs-only single-Worker push, monorepo affected-app selection, simultaneous pushes, force-push race, deleted-branch cleanup, merged PR with retained branch, pre-deploy failures retaining last-good Preview, post-deploy smoke failures with explicit red status and tested recovery, quota/provisioning failures, missing bindings/auth bypass, Preview URL smoke, scheduled/queue Hybrid tests, and production rollback drills.

Cloudflare reference: https://developers.cloudflare.com/workers/previews/configuration/ ; https://developers.cloudflare.com/workers/previews/examples/ ; https://developers.cloudflare.com/workers/previews/resources/ .
