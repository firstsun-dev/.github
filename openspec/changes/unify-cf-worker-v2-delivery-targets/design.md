# Design: Target-driven Cloudflare Worker v2 delivery

## Logical topology

```text
Detect (consumer-specific, optional)
  -> Lint || Typecheck || Unit || Integration || Build Validation || E2E
  -> Validation Gate
  -> Delivery plan [enabled targets; ONE physical D1; migration owner]
  -> Database Migration (ONE job) [validate every target's effective config + D1 binding -> migrate once; resource lock]
  -> Target Delivery [one independent build + publish per enabled target]
  -> Smoke [full | basic | skip]
     pass -> Verified ---------> Production Release (main only)
     fail -> guarded Recovery -> Failed (never counted as successful release)
     skip -> Published/Unverified (no Release)

Target Delivery instances: production | native-branch | native-dev | persistent-dev
```

The diagram describes **one engine instantiated for each enabled target**, not a shared compiled artifact. GitHub Actions may retain independent validation checks for precise feedback. A single Validation Gate aggregates their results, including configured-optional skip semantics. Prefer a caller-selected delivery matrix or target-specific reusable workflow over permanently visible, always-skipped target branches. Keep generic deployment logic in `actions/cf-deploy`/`actions/cf-preview`; avoid four forks of the same shell code.

## Interface and target resolution

Keep `_cf-worker-v2.yml` as the external `workflow_call` entrypoint. A new internal `_cf-worker-delivery.yml` may own target-specific orchestration while reusing the current audited engine. The implementation must maintain existing caller permissions, Event/Environment protections and `pipeline_phase=all|deploy|verify`, `phase_state` output compatibility for cross-service deferred verification. Do not break `tools_ref` checkout resolution.

Proposed additive inputs (subject to contract tests, not yet implemented):

```yaml
preview_on_branch_push: false                    # existing per-branch Native Preview
shared_dev_on_branch_push: false                 # existing separate persistent Dev Worker
shared_dev_native_preview_on_branch_push: false  # NEW stable named Native Preview "dev"
build_env_shared_dev_native_preview: ""          # NEW per-target build-time KEY=VALUE lines
smoke_mode: full                                 # NEW default; full | basic | skip
smoke_mode_overrides: '{}'                       # NEW optional JSON keyed by target kind
```

The normalized internal target descriptor includes `kind`, `worker_name`, optional `preview_name`, canonical `url`, `build_env`, effective Wrangler config policy, bindings, secrets, D1 identity, `smoke_mode`, and recovery policy. Parse and validate all fields before any mutation; reject unknown target kinds/modes, invalid JSON and conflicting automatic Dev channels unless explicitly supported. `smoke_mode_overrides` may specify `production`, `native-branch`, `native-dev` or `persistent-dev`; otherwise `smoke_mode` applies. Default `full` must preserve existing implicit HTTP probe plus configured `smoke_cmd` and current protected-environment verification.

Target selection:

| Event / configuration | Target |
| --- | --- |
| trusted main push | production only |
| trusted non-main push, `preview_on_branch_push=true` | native-branch |
| trusted non-main push, `shared_dev_native_preview_on_branch_push=true` | native-dev |
| trusted non-main push, `shared_dev_on_branch_push=true` | persistent-dev |
| protected manual persistent-dev dispatch | existing persistent-dev path with approved ref |
| PR feedback / branch-delete | separate lifecycle handler, no deploy |

For non-main push, exactly the enabled targets run; a disabled branch does not create a phantom delivery path. Production-only strategy rejects forbidden Dev targets. Do not duplicate full tests for PR and push.

| Behavior | production | native-branch | native-dev | persistent-dev |
| --- | --- | --- | --- | --- |
| Build inputs | production URL/env | branch-specific URL/env | fixed Dev preview URL/env | persistent Dev URL/env |
| Wrangler publish | version upload + deploy | `wrangler preview --name <branch-identity>` | `wrangler preview --name dev` | `wrangler deploy --env dev` |
| Binding source | top-level production | `[previews]` | `[previews]` | `[env.dev]` |
| Smoke, ledger and recovery | target-owned | branch-owned | fixed Dev preview-owned | persistent Dev-owned |
| Rollback and migration | fail-closed policy | preview-only | fixed preview-only | dev-only |

For Native Dev, protect `dev` from Preview GC and branch deletion. Use the same Worker host that permits Native Previews and validate its actual Cloudflare Preview URL capability. Do **not** create a separate persistent Worker for Native Dev. Persistent Dev remains available for Notification Hub (Queue/Cron constraints). These are workflow/CLI design requirements; do not assume custom domains can be attached to a specific named Native Preview without verified provider support.

## Validation and Migration as independent stages

Validation checks stay parallel and separately visible. The Gate succeeds only when every configured check succeeds; intentionally omitted optional checks may be skipped. Fail/cancel/unexpected skip is fail-closed.

> **Superseded (PR #89 simplification).** There is no separate Preflight stage and no per-target Migration call. One `migration` job holds the read-only safety
> checks as its first steps and the single remote mutation as its last; the plan admits at most one physical D1 per pipeline. See "Single Migration job (revised decision)" at the end.

The independent **Migration Stage** is downstream of the Gate and upstream of every relevant target's publish. Plan migrations by validated physical D1 identity (`account_id/database_id`), with target-specific effective-config preflight in the Migration Stage **before executing any remote schema change**. Where adapters materialize Wrangler configuration at build time, preflight may run a nonpublishing target-specific config-generation/build command; do not confuse this with the later target-specific deploy build. Before publish, verify the independently rebuilt artifact still binds the exact audited D1. If identities disagree, fail without publishing.

- Build the unique-D1 migration plan across enabled targets; two Native Preview URLs sharing one D1 yield **one** migration execution for that D1, not two.
- Each distinct D1 may migrate independently; serialize all writes to one physical D1 using resource-keyed concurrency/locks, including concurrent branch pushes. Never require a separate Delivery channel to finish its Smoke/Rollback merely to migrate a common D1.
- Use the same project-owned migrations. Do not allow arbitrary caller commands to bypass audited target mapping.
- No-D1 targets produce a successful no-op migration gate; D1 errors block dependent delivery only.
- Production D1 must never appear in a Preview/Dev migration plan. Shared nonproduction D1/R2 require verified, explicit bindings and permitted resource-sharing policy; Production D1 is never allowlisted.
- Enforce schema forward-compatibility with previous deployed Workers (expand/contract) or block unsafe concurrent rollout. A successful Worker rollback does not roll back D1.
- Preserve stale-SHA checks before migration and immediately before publish. Lock scope must protect the publish → smoke → recovery interval from stale rollbacks; GitHub concurrency is repository-scoped, so shared D1 across repositories needs an explicitly verified stronger serialization method or must fail closed.

## Target-specific build, URLs and security

Each Delivery instance independently checks out exactly the validated SHA, imports the **target-specific** build environment, builds, audits its generated Wrangler config and compiled assets, fingerprints the result, then publishes **that** artifact. Never reuse an Astro/Vite build between Dev/Branch/Production URLs. The target URL must be deterministic and validated **before build**, then confirmed against the published URL; never substitute Production/OAuth callback URLs in Preview.

Runtime bindings/secrets are not build-time public variables. Validate `PUBLIC_*`, `VITE_*`, `IDP_URL`, callback and allowed-origin settings; prohibit leaking secrets into client bundles. For Native Preview, use audited `[previews]` bindings and Preview Base secrets. Existing exact resource-sharing exemptions remain intentional and narrowly scoped. Prod resources are default denied. Protected Cloudflare Access must support actual content-level machine smoke using appropriately scoped Service Tokens; an Access login page/302 alone is not Verified.

Serialization of a fixed `native-dev` target is per Worker (across branch pushes); native branch previews retain per-branch identity and independent recovery. Maintain explicit Dev vs branch deployment ledgers, distinct last-known-good version ownership and immutable SHA tracing. Prevent branch lifecycle cleanup from deleting named `dev`.

## Smoke, rollback and Release

- `full`: preserve built-in HTTP probe and any caller-defined application/Playwright smoke, with SHA proof when supported.
- `basic`: require meaningful configured HTTP/health response (not an Access redirect); preserve probe status/path contract, and report verification level accurately. If content cannot be proven, return Published/Unverified, not Verified.
- `skip`: execute no Smoke calls; set `deployed_and_verified=false`, clear `verified_url`, record Published/Unverified; do not add the deployment to known-good recovery history.
- A failed Smoke of the current published deployment may invoke the target's approved guarded recovery. Before rollback, verify deployed Worker/Preview identity, SHA, version ownership, current target and absence of superseding rollout. Re-smoke the restored version before calling it healthy.
- A skipped or blocked cross-service Smoke must **not** roll back unrelated or newer code. `pipeline_phase=deploy/verify` handoffs must preserve target identity, gate results and freshness, and must not unlock an unsafe interleaving between publish and delayed verify.
- Production Release requires **Verified**, no outstanding failed recovery, every affected Worker verified and existing repo-specific version policy; `skip` cannot bypass this. Rollback attempts are not green release evidence.
- Preserve existing production manual/automatic rollback policies. Never auto-undo D1 schema changes.

## Cross-service execution and compatibility

Heaven ID, Check-in and Test Client can run their CI and target Build/Deploy independently when safe. ID OIDC smoke waits for the matching Test Client to be deployed and ready; Test Client's IdP discovery smoke waits for ID's named Dev Preview to be deployed; Check-in IdP integration smoke waits for ID verification. Use Deploy/Verify split or equivalent without cyclic `needs` dependencies. An unaffected dependency can be reused only after confirming the existing environment is healthy and matching, not by equating a skipped job with verified readiness.

Existing callers `anas-mcp`, `heaven-www` and the existing Notification Hub behavior retain their event filters, outputs, auth boundaries, smoke policy and tag semantics. Validate compile/run-time caller compatibility, including GitHub reusable-workflow `workflow_call` types and permission ceilings; do not claim compatibility based only on YAML parsing.

## Strict phased integration gate

```text
Phase 0 (this OpenSpec only) -> Phase 1 (.github only)
Phase 1 complete on .github/main + all tests + contract freeze
                           -> G1 APPROVED
                           -> Phase 2 consumer sub-sessions begin
                           -> Phase 3 staged acceptance / controlled @v2 cutover
                           -> Phase 4 explicitly approved legacy cleanup
```

**G1 must be a documented pass with**: implementation commit on `.github/main`, static and executed engine tests, actionlint/OpenSpec checks, success/failure/concurrency/skip simulations, cross-repo `workflow_call` scratch proof, full inputs/outputs contract, no known failing critical safety controls, and one pinned shared-engine SHA. If any item is missing, consumers (including `infra-config`) must not begin implementation sub-sessions. If the contract changes, revoke the prior gate.

After G1, create independent sub-sessions in isolated clean worktrees: one for `blog`, one for `innovation-apps`, one for `heaven-monorepo`, one for `infra-config`, and one read-only compatibility session covering `anas-mcp`/`heaven-www`. The main session reviews integration and changes to the frozen contract; consumer sessions do not edit `.github`.

Do not move floating `@v2` before all compatibility tests and coordinated rollout are complete. Retain legacy Dev Workers, custom domains, Access rules and stateful resources until their replacements are verified and a separate cleanup is approved.

## Single Migration job (revised decision, PR #89 simplification)

**No separate Preflight stage. One Migration job with integrated safety validation. Reusable target-driven Delivery.** The earlier requirement of a Preflight
matrix / four Preflight calls and four Migration calls is superseded.

```text
Gate -> Delivery plan -> Database Migration (ONE job) -> Delivery(kind)* -> Release
                           1. non-owner targets: build + effective-config audit + exact D1 binding (engine stage preflight; nothing remote)
                           2. owner: build + effective-config audit + exact D1 binding + stale-SHA guard + migrate ONCE (engine stage migrate)
```

* **Plan.** Resolves every enabled kind's physical D1 (`account_id/database_id`), deduplicates identical ones and **fails closed on two distinct databases**
  (single-DB rule) or on a Preview/Dev kind that maps to the Production D1 - before any mutation. `migration` = `{kind (owner), environment, d1_id, kinds}`.
* **Job.** Environment = the owner's (`<app>/prod` on main: the approval gate is unchanged and not optional). Steps run in order, so a failing validation
  stops the job before the migrate step - no peer-result mapping. The engine (not YAML) implements every check; the owner is validated inside its own
  migrate stage, so a shared-D1 run builds each target once for validation (N targets = N builds, previously N + 1).
* **Handoff.** `migration_json` (the `migrate` step outputs) + `migration_result` go to every Delivery call; its publish gate requires `success`, the planned
  owner kind and exactly the call's D1 id (a stale-superseded migration is accepted: nothing is published). `d1_migrations=false` skips the job; Delivery proceeds.
* **Deferred verification** never migrates: the job is skipped in a `verify` call and each kind uses its own `migration_json_in` from `phase_state`.
* **Compatibility restriction.** A pipeline that enables targets with distinct D1s in one invocation (e.g. Native Branch on the `[previews]` D1 together with
  `shared_dev_on_branch_push` on a different `[env.dev]` D1) is rejected by the plan. None of the planned consumers (blog, innovation-apps Notification Hub,
  heaven-monorepo, anas-mcp, heaven-www) is specified to need this; each caller must be confirmed before the rollout, and the `@v2` move stays blocked until then.
