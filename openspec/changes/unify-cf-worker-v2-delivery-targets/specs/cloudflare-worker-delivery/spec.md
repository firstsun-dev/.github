# Cloudflare Worker Delivery — Unified Target Delivery Delta Specification

## ADDED Requirements

### Requirement: Shared-engine implementation SHALL finish before any consumer sub-session starts

The organization SHALL complete and merge the shared implementation to `firstsun-dev/.github/main`, pass its full regression and cross-repository `workflow_call` tests, freeze the public interface and record an immutable tested commit SHA and an explicit G1 PASS **before initiating any consumer repository implementation or verification sub-session**. The OpenSpec-only commit SHALL NOT satisfy this gate.

#### Scenario: Only this OpenSpec is merged
- **WHEN** this specification has been committed to `.github/main` but the shared delivery engine has not passed G1
- **THEN** no sub-session SHALL begin changes to `blog`, `innovation-apps`, `heaven-monorepo`, `infra-config`, `anas-mcp` or `heaven-www`.

#### Scenario: G1 has passed with a frozen interface
- **WHEN** the .github shared implementation is complete on main with all G1 evidence
- **THEN** separate consumer sub-sessions MAY be started, one owning each consumer repository, against the same immutable shared-engine SHA.
- **AND** consumers SHALL NOT redesign or implement local copies of the shared delivery engine.

#### Scenario: Shared contract changes after G1
- **WHEN** an implementation change invalidates the frozen contract or safety tests
- **THEN** G1 SHALL be revoked and new consumer sub-sessions SHALL NOT start until the shared change is revalidated.

### Requirement: All deployment kinds SHALL share one parameterized delivery implementation

Production, per-branch Native Preview, named Native Dev Preview and Persistent Dev SHALL use the same build/audit/publish/smoke/recovery primitives. Deployment kind SHALL be an explicit validated parameter rather than duplicated implementation. All enabled targets SHALL depend on equivalent Validation and Migration gates; a target SHALL NOT unconditionally depend on a different target's Deploy, Smoke or Rollback.

#### Scenario: Persistent Dev without Native Branch enabled
- **WHEN** notification-hub uses only Persistent Dev on a non-main push
- **THEN** it SHALL proceed after its own Validation and Migration gates without waiting on skipped Branch Preview Deploy/Smoke/Rollback jobs.

#### Scenario: Branch and Native Dev both enabled
- **WHEN** blog enables both target kinds on a non-main push
- **THEN** they SHALL run independent delivery instances with their own URL, build, smoke and recovery; a failure in one SHALL NOT by itself imply the other failed.

### Requirement: Native Dev SHALL be a protected stable named Native Preview

An opted-in `shared_dev_native_preview_on_branch_push` SHALL publish to named Native Preview `dev` under the existing host Worker, using audited `[previews]` bindings rather than Wrangler `[env.dev]`. Branch deletions and Preview GC SHALL preserve this `dev` target. Existing `shared_dev_on_branch_push` SHALL retain its separate Persistent Dev meaning.

#### Scenario: Two branches update Native Dev
- **WHEN** two eligible branches push
- **THEN** their updates to `dev` SHALL be serialized, and only the latest successfully verified eligible deployment SHALL be marked known-good.

#### Scenario: Disabled Native Dev channel
- **WHEN** `shared_dev_native_preview_on_branch_push` is omitted or false
- **THEN** the legacy caller SHALL NOT create a named Native Dev Preview or change other deployment behavior.

### Requirement: Every target SHALL build separately against its canonical pre-resolved URL

Each enabled target SHALL independently build an application artifact for the exact intended URL and target build environment. The URL SHALL be resolved and checked before build and asserted against actual publish results. A deploy artifact built for one URL SHALL NOT be reused for another URL, and the effective configuration and assets published SHALL match the audited build.

#### Scenario: Branch Preview and fixed Dev built from the same commit
- **WHEN** their deployment URLs differ
- **THEN** the system SHALL execute separate target-specific builds, not build once and publish the same client bundle to both Preview targets.

#### Scenario: OAuth/OIDC URL mismatch
- **WHEN** a generated artifact embeds a callback, issuer, origin or public endpoint belonging to another target
- **THEN** publish SHALL fail rather than ship an incorrectly configured build.

### Requirement: Smoke policy SHALL support full, basic and skip without inventing verification

Each target SHALL support `full`, `basic` and `skip`, defaulting to behavior equivalent to the existing HTTP probe plus optional application smoke. `full` SHALL run the configured application smoke, `basic` SHALL require meaningful content/health verification, and `skip` SHALL make no post-publish verification request. Access-only redirects SHALL NOT count as content verification.

#### Scenario: Smoke explicitly skipped
- **WHEN** a target uses `smoke_mode=skip`
- **THEN** its deployment SHALL be Published/Unverified, SHALL NOT populate `verified_url` or known-good ledger, SHALL NOT count as verified for release, and SHALL NOT trigger smoke-failure rollback.

#### Scenario: Production release with skipped smoke
- **WHEN** the Production target published without Verified smoke evidence
- **THEN** Release SHALL NOT run, even if publishing succeeded.

### Requirement: Recovery SHALL be target-owned and guarded by deployment identity

An eligible failed Smoke SHALL trigger only the affected target's approved recovery policy. Recovery SHALL verify target, published version/commit, ledger and supersession state before changing a live deployment, SHALL reverify any restored known-good version, and SHALL never roll back D1 schema.

#### Scenario: A newer Dev deploy supersedes a failed older deploy
- **WHEN** the older run attempts rollback
- **THEN** it SHALL NOT overwrite the newer target deployment.

### Requirement: Caller compatibility and production safety SHALL gate floating v2 cutover

The shared engine SHALL preserve existing `_cf-worker-v2.yml` public inputs/outputs, event/Environment permissions, `pipeline_phase`/`phase_state`, release policies and unchanged callers. The floating `@v2` tag SHALL remain unchanged until consumer and compatibility tests pass. No consumer implementation SHALL begin before G1.

#### Scenario: Existing production-only consumer
- **WHEN** anas-mcp calls the unchanged `@v2` interface
- **THEN** no Dev or Native Preview SHALL be newly exposed and the prior Production deploy semantics SHALL remain.

## MODIFIED Requirements

### Requirement: Migration is a push-only pre-deployment stage

Remote D1 Migration SHALL be a distinct stage **after successful Validation Gate and before target Publish**, never an opaque step within Shared Dev Deploy. It SHALL run only for trusted eligible deployment events, and only against the selected target's audited physical D1. The Migration Stage SHALL preflight effective target config/bindings before changing schema, group enabled target mappings by physical database identity and migrate once for each unique physical D1. Independent physical D1s MAY run concurrently; migrations against the same D1 SHALL be serialized. D1-free targets SHALL pass through a no-op gate. Preview and Dev SHALL never use Production D1, and schema SHALL NOT be automatically rolled back with Worker code.

#### Scenario: Two Preview targets share one D1
- **WHEN** both Native Branch and Native Dev map to the same nonproduction D1
- **THEN** the Migration Stage SHALL apply that D1's pending SQL at most once for the run, and both deliveries SHALL wait for that same migration result.

#### Scenario: Migration changes a database used by an older Worker
- **WHEN** a migration is not backward-compatible with the currently deployed Worker
- **THEN** deployment SHALL fail closed or require an approved expand/contract rollout rather than unsafe immediate schema mutation.

#### Scenario: Target config differs after the deploy build
- **WHEN** the actual publish artifact's D1 differs from the Migration Stage's audited database identity
- **THEN** publish SHALL be rejected before a different database can be accessed.

#### Scenario: D1 migration fails
- **WHEN** the target's required migration fails
- **THEN** dependent publish and Production Release SHALL not run.

### Requirement: Smoke verification runs against the deployed environment

Where Smoke is enabled, it SHALL operate against the exact deployed target URL and receive the correct deployment identity and application-specific environment. The verification verdict SHALL distinguish successful content verification, Access-only liveness, failure and explicit skipping.

#### Scenario: App config requires full OIDC smoke
- **WHEN** Heaven ID's full smoke is enabled
- **THEN** it SHALL wait for the correct test-client deployment and required ID readiness without forcing unrelated build/deploy jobs to wait.
