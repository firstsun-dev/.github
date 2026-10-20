# Design: Cloudflare Worker CI/CD v2 — push-to-preview branch lifecycle

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
| `delete` for non-main branch | Cleanup that branch Preview | Same | No Preview |
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

- Use `github.ref` for a `refs/heads/...` `push`, never PR number. Produce a stable `br-<normalized-slug>-<hash>` from the **full original branch ref**; normalize to Cloudflare-supported charset/length, always add a suffix hash so case/punctuation normalization cannot collide, check registry collisions and fail closed if needed. The Preview is scoped to the target Worker; two monorepo Workers may use the same branch name safely.
- Explicitly pass the name, because GitHub checkout may be detached: `pnpm exec wrangler preview --name "$PREVIEW_NAME" --json`. Parse/validate `.preview.urls[0]` from machine-readable output (per the official GitHub Actions example); do not synthesize a hostname from the branch name or assume an account subdomain.
- The Preview's stable URL is the deployment target. The separate immutable deployment URL may be recorded for audit, but never replace the stable branch link with a new URL on every push.
- Emit URL, repo, Worker, full branch ref, commit SHA, run link, and Preview identity as a job output/step summary/GitHub Deployment even without a PR. When an open same-repo PR matches that exact head branch/SHA, update one idempotent bot-owned PR status/comment; forks and ambiguous matches get no deployment feedback.
- Revalidate the branch still exists and SHA is current before creation and publishing, under a per-repo+Worker+branch concurrency key. Out-of-order runs may not regress URL state or GitHub feedback.

### Preview configuration and state

- Top-level Wrangler config is Production. New Native Previews MUST have an explicit `[previews]` (or equivalent JSON `previews`) block and resource bindings. Do not assume `[env.dev]` or `preview_urls = true` creates Native Preview isolation. Keep top-level assets and compatibility flags at their documented location; build each branch's assets before Preview deployment.
- Preview secrets use separate Preview Base/Preview-specific secrets, never Prod credentials. Base-secret changes do not automatically update existing Preview instances; include a rotation/reseed process.
- Preflight audits D1, R2, KV, Queues, Durable Objects, Containers, Workflows, Service Bindings, Hyperdrive, Secrets Store, routes, auth, outgoing webhook/API side effects, rate limits and analytics. Reject missing/unknown/Production-risk bindings and unsafe public ingress. Service bindings from Preview target the bound Worker's **Production** deployment by default; isolate, mock or explicitly deny unsafe cross-service calls.
- `blog` requires branch-scoped D1 (schema migrations first on that branch DB, generated temporary Wrangler preview config if required), an R2 binding that cannot write to Production `firstsun-blog-assets`, safe Preview secrets, test-only notification/TTS/analytics, `SITE_NOINDEX=true`, and a real Preview URL in smoke. Refactor build flags (such as voice QA) so they do not infer Preview solely from the old shared `SITE_URL`.
- For other Workers, mutable shared non-prod resources require a formal namespace isolation proof; when migrations or concurrent writes can interfere, allocate branch-scoped resources. Never bind a Preview to a Production resource as a fallback when provisioning/quota checks fail.
- `infra-config` owns durable resource baselines and IaC contracts where appropriate. Ephemeral branch-scoped resources require a lifecycle inventory/ownership ledger, idempotent provisioning, explicit quotas, cleanup permission and orphan reconciliation; do not check generated resource IDs into shared mainline config.
- Workers with protected custom-domain-only ingress, notably `anas-mcp`, remain `production-only` until a separate Access-safe preview design is approved. No public workers.dev bypass.

### Lifecycle and cleanup

```text
trusted branch push --> app selection --> tests --> Preview resource inventory/provision/migrate
                    --> build with Preview vars/assets --> wrangler preview --name <stable> --json
                    --> exact-URL smoke --> Deployment/summary --> optional matching PR link

PR opened / sync ------> CI/report only (no second Native Preview for same branch)
PR closed / merged -----> keep Preview when branch still exists
branch deleted ---------> identify branch+Worker --> safe preview delete + owned resource cleanup
protected Hybrid Dev ---> separately approved Dev deploy/test (Cron/Queue)
main push --------------> gated Production upload/deploy/migration/verify/recovery
```

Cleanup is triggered by the repository default-branch workflow's GitHub `delete` event (`ref_type=branch`); do not depend on the deleted branch's workflow being available. Use `pnpm exec wrangler preview delete --name "$PREVIEW_NAME" --skip-confirmation`, validating Worker identity and resource ownership. Only delete resources created for the same full branch ref/Worker; tolerate repeated deletes and race with a new branch of the same name. Add a safe TTL/orphan reconciliation task for deleted branches and a manual recovery path; never delete Prod, shared Dev, another branch Preview, or shared non-prod resources.

## Hybrid and Production controls

Hybrid `heaven-id` Cron and `notification-hub` Queue/Cron are **not executed by Native Previews**. Run them in tests or protected, independently deployed persistent Dev; no generic branch push changes that environment. Ensure Dev Queue producers/consumers and Cron do not deliver real notifications or production side effects.

Production retains required quality gates, version history, migration approval and smoke. Recovery policy is per Worker: automatic rollback only with proven schema/state compatibility, otherwise manual/disabled; Worker rollback cannot undo D1 schema or third-party mutations. Separate concurrency groups and credentials by app/branch/environment.

## Release and validation

Maintain pinned v1/v1.5.0; create a separately named v2 workflow with opt-in caller interface, contract tests and an immutable v2.0.0 release after the pilot. Initial trial pins a commit SHA, then moves to the tested immutable tag. Rollback is an individual caller pin revert, not repointing floating `v1`.

Test: first push without PR, second push stable URL/new SHA, two branches with independent code and resources, fork PR no token, docs-only single-Worker push, monorepo affected-app selection, simultaneous pushes, force-push race, deleted-branch cleanup, merged PR with retained branch, pre-deploy failures retaining last-good Preview, post-deploy smoke failures with explicit red status and tested recovery, quota/provisioning failures, missing bindings/auth bypass, Preview URL smoke, scheduled/queue Hybrid tests, and production rollback drills.

Cloudflare reference: https://developers.cloudflare.com/workers/previews/configuration/ ; https://developers.cloudflare.com/workers/previews/examples/ ; https://developers.cloudflare.com/workers/previews/resources/ .
