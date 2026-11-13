# Design: stage-based CI and target-driven Cloudflare Worker delivery

## Baseline and boundaries

Current authoritative code: `.github/workflows/_cf-worker-v2.yml`, `_cf-worker-ci.yml`, `_cf-worker-delivery.yml`, `actions/cf-preview/delivery.mjs`, its release/rollback engine and actual callers. The four current target kinds are `production`, `native-branch`, `native-dev` and `persistent-dev`. The current shared CI uses parallel fixed category jobs and an aggregate gate; same-repo PR CI is skipped in favor of the Push SHA. The existing blog caller can deploy two nonproduction targets on one Push. Preserve safety invariants, not those specific branches or names.

This design describes a **proposed contract**, not fields already accepted by `@v2`.

## 1. Stages, ownership and task schema

Three **logical** stages: `verify`, `test`, `delivery`. A stage is a policy and gate boundary, **not** a demand that all its tasks execute serially.

A caller owns a declared list of jobs under each CI stage. Proposed public inputs:

~~~yaml
# Proposal only: new interface; not available in the current @v2.
with:
  app_name: blog
  app_path: .
  verify_tasks: >-
    [{"id":"lint","cmd":"pnpm run lint"},
     {"id":"check","cmd":"pnpm run check"},
     {"id":"build","cmd":"pnpm run build"},
     {"id":"unit","cmd":"pnpm run test:unit"}]
  test_tasks: >-
    [{"id":"integration","cmd":"pnpm run test:integration"}]
  test_e2e_cmd: pnpm run test:e2e:ci
  preview_policy: pr          # none | pr
  dev_driver: none            # none | native-preview | worker
~~~

Minimal task schema: `{"id":"<stable-unique-id>","cmd":"<nonempty-command>"}`. Stage task IDs SHALL be unique **within the app and stage** and shall be stable, safe for a job label, nonempty, and validated before dispatch. Task commands execute from `app_path`. The repo chooses whether UT/IT belong in Verify or Test. No generic per-task workflow expressions, dynamic GitHub secret names or arbitrary new deployment privileges are introduced.

- Keep one special optional **E2E Test executor** for genuine browser matrix, mock/Mailpit setup, reports and independent test build. It contributes to the **Test Gate**, not a second test framework. E2E remains omitted where not configured.
- Each stage uses a bounded matrix of independent tasks when useful; a one-task stage may execute as one Job. Favor fewer runners when jobs are too small to justify fan-out. A parsed, validated empty stage SHALL NOT attempt a zero-row matrix.
- Do not duplicate dependency bootstrap unnecessarily; reuse cache/setup action, and reuse artifacts only if build inputs/environment/revision are identical and the artifact is verified. Target-specific Vite/Cloudflare build output MUST be built/audited for its destination URL.
- Inputs such as `use_mailpit`, caller-approved nonsecret test environment and runner selection remain supported by the relevant executor; the implementation SHALL document any input removed/replaced and ensure E2E coverage remains equivalent.
- `verify_tasks` / `test_tasks` are a single source of truth. The shared engine SHALL NOT retain the old category-specific `lint_cmd`, `typecheck_cmd`, `test_unit_cmd` or `test_integration_cmd` inputs after cutover.

## 2. DAG and aggregate gates

~~~text
FEATURE PUSH:   detect -> [Verify tasks in parallel] -> Verify Gate -> (no delivery by default)

PR/MERGE GROUP: detect -> [Verify tasks || Test tasks || optional E2E] -> PR Gate
                                                                        |
                                                                      Merge

MANUAL DEV:     authorize SHA/ref -> [Verify] -> Verify Gate -> Plan -> Migration -> Publish -> Smoke -> Recovery
MAIN PUSH:      authorize release -> [Verify / required release validation] -> Plan -> Migration -> Publish -> Smoke -> Release
~~~

Only add `needs` when a real artifact/data/service dependency exists. Tests independent of Verify start in parallel on PR. A task needing a built artifact waits for its producer, not for every job in a previous logical stage. Do not chain empty or redundant gate jobs to manufacture a GitLab-like visual DAG.

**Verify Gate** is the AND reduction over *expected configured* Verify tasks. **PR Gate** is the AND reduction over expected Verify + Test + E2E checks for the **same PR merge SHA or merge_group SHA**. Each gate is an always-executed/fail-closed result aggregator, explicitly distinguishing expected disabled tasks from unexpected skip/failure/cancellation. The only stable branch-protection requirement is the repo/app-scoped **PR Gate** name, with names unique across apps in a monorepo. Affected-app detection records an explicit **no-op / unaffected** reason; it cannot silently fabricate success for an app that should run.

GitHub `needs` does not cross workflow runs; **do not query/reconstruct a Push-run verdict to greenlight a PR**. GitHub may test a synthetic merge SHA rather than feature HEAD. Fast Verify intentionally runs again on PR to prove integration with the target branch. A Required Check SHALL be produced for every eligible PR even when no expensive Test task is configured; never rely on workflow-level `paths` filtering for a mandatory check. Add `merge_group` for a repository only if/when its merge-queue protection requires it; the queue runs the same no-deploy Verify/Test gates.

**Feature Push:** Verify only; no heavy tests, unless an approved app's Verify list contains them. **Main Push:** Production requires successful local Verify plus an enforceable PR/merge gate (or equivalent same-run full verification if direct main pushes are possible). Delivery SHALL NOT depend on stale Push checks for PR merge eligibility.

## 3. Event/trust/permissions

| Event/ref | Trust | Enabled jobs | Credential policy |
| --- | --- | --- | --- |
| Same-repo feature push | trusted code after branch policy | Verify | no deployment secrets for verification |
| Same-repo PR | trusted repository branch; still untrusted until checked | Verify + Test; optional PR Preview | deploy secrets only in gated, approved deployment job |
| Fork PR | untrusted | safe hosted-runner checks only where explicitly supported | no self-hosted privileged runner, no deployment token, no `pull_request_target` checkout of head |
| `merge_group` | merge candidate | Verify + Test | no deployment secrets |
| `main` push | protected | release validation and production | least-privileged production environment approval |
| `workflow_dispatch` Dev | protected approval + exact ref/SHA | Verify and one target's delivery | scoped Dev token/environment |
| Delete/PR close | lifecycle | no CI | no destructive provider action by default |

Public `anas-mcp` currently uses push-only CI because its test setup requires an organization self-hosted runner. A coordinated migration MUST either make the credential-free PR checks genuinely safe on a GitHub-hosted runner or explicitly choose a secure non-PR policy and record the resulting merge-gate tradeoff. The shared workflow MUST NOT infer that a fork PR is safe merely because `CLOUDFLARE_API_TOKEN` is absent.

## 4. Normalize environments and drivers

Use one normalized deployment descriptor:

~~~json
{
  "target": "dev",
  "driver": "native-preview",
  "app": "heaven-id",
  "revision_sha": "<40-hex-commit>",
  "identity": "dev",
  "canonical_url": "<resolved-before-build-https-origin>",
  "resource_scope": "<verified-nonprod-identity>"
}
~~~

Targets: `production`, `preview`, `dev`. Drivers: `worker` (production or isolated environment Worker) and `native-preview` (per-PR or stable named Preview). Policy: `production -> worker`; `preview -> native-preview`; `dev -> native-preview|worker`. Neither target nor driver alone proves isolation: the plan and audited effective Wrangler config must establish exact resource IDs, URLs, secret/trust policy and release conditions.

Proposed caller controls:

- `preview_policy: none | pr`. For eligible PRs, one per-PR Preview identity `pr-<number>` under the host Worker. PR synchronization updates the same URL, not an extra branch Preview. Fork PRs cannot publish.
- `dev_driver: none | native-preview | worker`. Fixed endpoint keyed `dev` or a named `env.dev` Worker; only approved manual promotion updates it.
- `approved_dev_ref` (where Dev enabled) and exact requested candidate SHA/ref, verified before any mutation. Protected GitHub Environment approval is required for privileged promotion; never trust a string input alone as authorization.

Current independent switches `preview_on_branch_push`, `shared_dev_on_branch_push`, `shared_dev_native_preview_on_branch_push` and `deployment_strategy` are replaced by target policy; no legacy knobs survive the coordinated release. Existing URL/build-env/permissions inputs should collapse only when a single normalized representation genuinely reduces complexity; avoid exchanging six typed values for an unvalidated opaque JSON bag.

**One app, one automatic nonproduction target per event**. Stable Dev is not overwritten by PRs or arbitrary feature pushes. A manual Dev promotion never deploys Production. An affected-app-only rule still applies to monorepos; independent app targets need not serialize behind unrelated apps, but jobs sharing physical D1/Queue/Worker state MUST be serialized at the resource level.

Canonical Native Preview URL is resolved and checked against Cloudflare's actual published hostname. A custom friendly `blog-pr-123...` hostname is a separate DNS/TLS/Access decision; do not create a guessed hostname as a successful delivery output.

## 5. Build, database, smoke, rollback and downstream verification

**Prepare/Plan:** validate requested event, SHA, target, driver, destination URL, expected resource ownership, permissions, disabled/optional tasks and provider preconditions before mutation. No-op when no affected app. Do not allow a user-supplied input to bypass this gate.

**Migration:** source/target configuration auditable before any remote database change, explicitly selected physical D1 ID, exactly once per required target transition with lock. Preview/Dev SHALL NOT map production D1/R2. Shared Preview D1 may be reused for HTTP/visual testing, but a PR SHALL NOT apply arbitrary incompatible migrations to it; test SQL locally or on an explicitly provisioned isolated DB instead. Fixed Dev migrations require expand/contract compatibility with existing service versions; no automatic D1 schema rollback.

**Publish:** target-specific build (URL/env) and binding audit; assert canonical URL, exact SHA, stale/superseded revision state and migration identity again before publish. The actual Provider driver is the only variation; both drivers return the same normalized handoff.

**Verify/Recover:** Smoke verifies the exact published URL, appropriate auth and target binding. Classify `verified` vs `published-unverified` (including Access redirect-only or skipped smoke). Rollback/recovery is target-owned and guarded by known-good ledger/version, SHA and supersession. Never overwrite a newer deployment or claim D1 reverted. Production Release is allowed only after verified Production and any required cross-service gate.

**Heaven OIDC:** preserve post-deploy cross-service OIDC E2E separately from local PR E2E. A fixed Dev promotion is a **compatible cohort**: the effective ID issuer, Checkin and Test Client URL/dependency versions must be recorded and checked, and the post-deploy login scenario must run only after required services are ready. Deleting `pipeline_phase` / `phase_state` is permitted only after equivalent readiness, deduplication and release/rollback protections are demonstrated.

**Notification Hub:** keep real `env.dev` Worker, dev Queue/DLQ, dry-run notifications, D1 and explicit Production queue denylist. A Native Preview cannot replace its real recurring/consumer behavior.

## 6. Simplify pipeline tests without weakening safety

Current `scripts/cf-preview/test-workflow-contract.py` interprets GitHub expressions; `ghexpr.py` and `test-jobs.py` simulate job scheduling and data passing; `run-job-groups.py` maintains a count-based manifest. Their purpose is partly replaced by a simpler contract.

Target self-test jobs, **independent and parallel**:

1. `static` — actionlint, shellcheck, workflow/schema/declared-action validation.
2. `unit` — pure planner, identity, task matrix input parser, event routing, target descriptor and Gate verdict tests.
3. `safety` — provider-side-effect-free negative tests for D1/secret/URL isolation, stale SHA, Migration failure, publish blocking, smoke and rollback ordering.

A stable `Pipeline Gate` aggregates all three with fail-closed semantics. Keep deterministic test stubs only where required to exercise dangerous mutations; do not reimplement GitHub scheduling or evaluate an arbitrary subset of Actions Expressions. Replace count-of-assertions manifests with named scenarios/coverage assertions of required invariants. Retain one small approved **real-runner canary suite** against `cf-v2-scratch-caller`: reusable-workflow call, permitted event matrix, skip/required checks, runner permissions, Preview publish and no-Production-secrets guarantee. Promotion is blocked until canary evidence exists.

No test may claim that actionlint proves runtime GitHub scheduling or that a shell stub proves actual Cloudflare behavior.

## 7. Cutover and validation plan

**One coordinated breaking release, no permanent legacy lane.** Produce the engine candidate on a development SHA, keep floating `@v2` untouched while consumer PR branches validate that exact SHA, then merge in a documented window (cross-repository commits cannot be atomic). Confirm all eight app pipelines plus `cf-v2-scratch-caller` before moving the shared tag. Read/update Required Checks and permissions for each repo, including any protection restrictions. If a caller cannot be cut over safely, block tag promotion rather than shipping an old-mode selector. Revert tag/caller refs as a controlled rollback only if compatible and explicitly preplanned; never roll back a D1 migration merely because code was reverted.

Temporary SHA pins and candidate branches are deployment coordination tools, **not** persistent compatibility modes. Remove obsolete category inputs, four-kind fanout, per-channel output shims, outdated docs and simulator fixtures only once equivalent test coverage and callers are verified. Actual Worker/DNS/Access/DB deletion is a separate approval with dependency inventory.

## 8. Acceptance matrix

| Case | Expected |
| --- | --- |
| Feature Push lint failure | Verify Gate red; no Dev/Preview publish |
| Feature Push verify success | fast green feedback; no shared Dev overwrite |
| Same-repo PR with Verify + E2E | both independent groups may run concurrently; PR Gate requires both on test-merge SHA |
| PR with no configured Test | PR Gate still exists and requires Verify |
| Changed-path detector reports unaffected | stable Gate reports explicit no-op; no false Pending Required Check |
| Fork PR to public anas-mcp | untrusted code never executes on privileged self-hosted runner; no deploy secrets |
| Merge queue candidate | Verify/Test on merge-group SHA; no publish |
| Manual Dev promote of stale/unapproved SHA | reject before migration or publish |
| Two branches race to update fixed Dev | cannot silently overwrite; only authorized promoted SHA may become known-good |
| PR Preview shares nonprod D1 | no arbitrary PR-driven remote schema migration |
| Dev/Preview bind Production D1/Queue | reject before remote change |
| Migration failed/stale or identity mismatch | publish forbidden |
| Smoke fails after publish | failure is visible; guarded recovery; no false verified release |
| Access 302 without content proof | published-unverified; not release-ready |
| Heaven issuer/test-client cohort mismatch | post-deploy OIDC gate red; no false success |
| Notification Hub Preview driver requested for queues | configuration rejected; retain real Dev Worker |
| Branch/PR closed | lifecycle/gc safe and idempotent; no immediate destructive cleanup |
| `@v2` promotion | all callers on same frozen contract; no legacy options required |

## External constraints

- GitHub required checks and skipped jobs: https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks
- GitHub merge queue event: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
- Cloudflare Previews and resource isolation: https://developers.cloudflare.com/workers/previews/ and https://developers.cloudflare.com/workers/previews/resources/

