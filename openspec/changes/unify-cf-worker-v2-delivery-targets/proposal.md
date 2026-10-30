# Proposal: Unify Cloudflare Worker v2 target delivery with a shared-engine-first rollout

## Status

**Proposed; specification-only commit.** This change does not implement CI/CD code, move the floating `@v2` tag, open consumer implementation sessions, change Cloudflare resources, or authorize Terraform apply/legacy Worker deletion.

## Why

The current `_cf-worker-v2.yml` mixes Production/Native Preview, Shared Persistent Dev, manual Persistent Dev, phase-split verification, and event-only handlers in a single visible DAG. For example, `dev-deploy` depends on six validation jobs and four unrelated Preview migration/deploy/smoke/rollback jobs, even when an app has no branch Preview. This creates unnecessary coupling, skipped nodes, and difficult cross-service scheduling.

Consumers also need a stable **named Native Dev Preview** alongside optional per-branch Native Previews. Client-visible application URLs are compiled into some Astro/Vite/OIDC/OAuth builds; publishing one compiled artifact to two different URLs is not acceptable.

## Decisions

- **One shared delivery implementation, many explicit targets.** Production (`production`), per-branch Native Preview (`native-branch`), stable named `dev` Native Preview (`native-dev`), and legacy separate Dev Worker (`persistent-dev`) use the same target-parameterized delivery engine. Target-specific build environments, Wrangler commands/config, URLs, bindings, secrets, smoke and recovery policies remain isolated.
- **Canonical DAG:** changed-app detection (when applicable) → independent Lint/Typecheck/Unit/Integration/Build Validation/E2E → Validation Gate → **independent D1 Migration Stage** → target-specific Build + Publish → optional Smoke → Verified or conditional Recovery → optional Production Release. Migration belongs **after Validation and before Publish**, never inside an opaque `Shared Dev` deploy job.
- **Target-independent gate.** The enabled targets have equivalent validation/migration prerequisites. No target unconditionally waits for a different target's Deploy, Smoke or Rollback. Cross-service Smoke and shared physical resources are explicit exceptions, not copied pipelines.
- **D1 identity, not target URL.** Migrate once per distinct physical D1 for the eligible run; shared nonproduction D1 targets use one migration execution. Preflight validates all target bindings/effective Wrangler configurations before DB mutation; re-audit the actual deploy configuration before publish. Serialize migration by physical resource identity. Never migrate Production D1 from Dev/Preview, and never auto-rollback schema.
- **Target-specific builds.** Resolve the canonical target URL before each build, then compile each enabled target independently. E2E/local builds remain separate from deployment builds; no shared compiled artifact or mandatory GitHub artifact handoff.
- **Optional Smoke, explicit status.** `full` preserves existing HTTP plus app-level smoke; `basic` performs meaningful content/health verification; `skip` produces Published/Unverified, not Verified. Production Release requires a verified deployment. Rollback is conditional on an eligible failed Smoke and restricted to the same target/version.
- **Opt-in Native Dev.** Preserve existing `preview_on_branch_push` and `shared_dev_on_branch_push` inputs; introduce `shared_dev_native_preview_on_branch_push` (default false), `build_env_shared_dev_native_preview`, and an opt-in target-aware `smoke_mode` interface. Native Dev uses a protected named Native Preview `dev`; it is **not** a Wrangler `[env.dev]` Worker.
- **Compatibility.** Preserve existing `_cf-worker-v2.yml@v2` caller inputs, outputs, default behavior and `pipeline_phase`/`phase_state` until coordinated cutover. Do not modify v1/v1.5. A new internal delivery reusable workflow is allowed, but app-specific delivery implementations must not be duplicated.

## App target policy (desired AFTER the shared-engine gate)

| Worker | Native Branch | Native Dev | Persistent Dev |
| --- | --- | --- | --- |
| blog | ON | ON | OFF |
| diet-manager | OFF | ON | OFF |
| notification-hub | OFF | OFF | ON (Queue/Cron-safe existing Dev) |
| heaven-id | OFF | ON | OFF |
| heaven-checkin | OFF | ON | OFF |
| heaven-id-test-client | OFF | ON | OFF |

Production remains main-only for each affected app. Unaffected consumers, notably `anas-mcp` and `heaven-www`, should require no YAML changes.

## Mandatory sequencing: HARD STOP before any consumer implementation

1. **Phase 0, this commit only:** record this OpenSpec on `.github/main`. Do not start other repository sub-sessions.
2. **Phase 1, MAIN SESSION ONLY:** implement the shared contract and engine **entirely inside `firstsun-dev/.github`**. Complete required tests, cross-repository reusable-workflow proof, security review, documentation, and merge/publish the validated shared implementation to `.github/main`. Record an immutable tested commit SHA, final inputs/outputs, version policy and a written gate verdict.
3. **Gate G1:** only when ALL Phase 1 acceptance checks pass and the shared implementation is merged to `.github/main` may implementation sub-sessions be launched. **Do not merely start Phase 1 or merge this OpenSpec and treat G1 as satisfied.** Do not start consumer `blog`, `innovation-apps`, `heaven-monorepo`, `infra-config`, or compatibility-check sub-sessions before G1.
4. **Phase 2:** after G1, create one isolated sub-session per consumer repository; independent sub-sessions may work in parallel against the **same frozen shared SHA/contract**. Sub-sessions must not redesign the shared interface. Include compatibility-only verification of `anas-mcp` and `heaven-www`.
5. **Phase 3:** integration, staged real-environment acceptance and controlled floating `@v2` tag update after consumers/compatibility are verified; retain old Dev Workers, domains and data until independently approved cleanup.

A frozen commit SHA enables consumer integration tests without prematurely moving the floating `@v2` tag. Any shared-engine contract change after G1 **closes G1** until retested and reapproved.

## Non-goals and prohibitions

- No changes outside `firstsun-dev/.github/openspec/changes/unify-cf-worker-v2-delivery-targets/` in this spec-only commit.
- No automatic Cloudflare production deployments, Access policy widening, Terraform apply, DNS/custom-domain reassignment, legacy Worker/Preview deletion, or D1/R2 destruction.
- No unverified Access redirect counted as healthy, no Production D1 fallback for Preview, and no bypass of existing test/safety gates to simplify the DAG.
- No requirement that ordinary consumers migrate in lockstep; preserving existing `@v2` behavior is a release gate.

## Related contracts

- `openspec/changes/refactor-cloudflare-worker-cicd-v2/`
- `openspec/changes/add-cloudflare-shared-dev-channel/`
- `openspec/changes/allow-explicit-preview-shared-resources/`
- `docs/cf-worker-v2-refactor.md`

Where older changes describe Migration inside Deploy or extra target-to-target DAG dependencies, **this newer proposed change intentionally supersedes that topology when implemented**.
