# Cloudflare Worker Delivery — Stage and Dev Target Simplification Delta

This delta applies to the **coordinated next v2 interface**. It does not retroactively alter the published v1 workflow. Terms **SHALL**, **SHALL NOT**, **MUST** and **MAY** are normative.

## ADDED Requirements

### Requirement: Delivery SHALL use target and driver instead of four unrelated deployment kinds

A reusable Cloudflare deployment SHALL normalize the logical target to `production`, `preview` or `dev`, then select a driver from `worker` or `native-preview`. Build audit, migration authorization, publishing evidence, smoke, rollback and release gates SHALL have one target-aware implementation. `production` SHALL use the Worker driver; `preview` SHALL use Native Preview; `dev` MAY use Native Preview or a separate Dev Worker as explicitly configured.

#### Scenario: Preview and fixed Dev use the same provider mechanism
- **WHEN** an application is configured for a per-PR Preview and a fixed Native Preview Dev
- **THEN** both SHALL use the same Native Preview publish implementation and target descriptor validation
- **AND** separate ad-hoc delivery pipelines for `native-branch` and `native-dev` SHALL NOT be required.

#### Scenario: A trigger-capable Dev service is needed
- **WHEN** a service requires Queue Consumer or scheduled trigger execution
- **THEN** its Dev target SHALL use an isolated real Dev Worker rather than pretending Native Preview runs those triggers
- **AND** its published Dev URL and safety evidence SHALL be represented using the same normalized output contract.

### Requirement: A feature Push SHALL NOT overwrite shared fixed Dev without promotion

Fixed Dev publication SHALL require an approved revision, ref and protected authorization step. A normal unreviewed feature-branch Push SHALL execute Verify but SHALL NOT automatically change fixed Dev. One event for one application SHALL produce at most one automatic nonproduction deployment target.

#### Scenario: Two feature branches are updated
- **WHEN** commits land on unrelated feature branches
- **THEN** neither Push SHALL silently replace the fixed `dev` revision
- **AND** both Pushes MAY independently produce their configured fast verification results.

#### Scenario: Authorized Dev promotion
- **WHEN** an operator dispatches `workflow_dispatch` with `target=dev` on exactly the caller-configured `approved_dev_ref` (a `refs/heads/...` branch ref; any other ref or target is rejected by the Plan before any mutation) for an application whose `dev_driver` is not `none`, and the protected GitHub Environment (`<app>/dev` or `<app>/native-dev`, with required reviewers) permits the deployment
- **THEN** the revision SHALL be the commit `GITHUB_SHA` of that dispatch; the CI Gate, the Build artifact (`source_sha = tested_sha = artifact_sha`) and the published version SHALL all refer to that one SHA, and the Migrate/Deploy jobs SHALL verify this identity against their own GitHub context rather than trust the artifact manifest
- **AND** exactly one fixed Dev target SHALL be planned and audited
- **AND** its migration/build/publish/smoke SHALL use the ordinary Delivery Engine with its selected driver
- **AND** it SHALL NOT deploy Production.

#### Scenario: Unapproved or stale candidate
- **WHEN** the requested revision/ref has changed, is not approved, or its environment approval is missing
- **THEN** no remote DB migration or publish SHALL occur.

### Requirement: PR Preview publication SHALL be a single trusted, stable Native Preview per PR

Where `preview_policy=pr`, a same-repository PR SHALL create or update one named Native Preview associated with the PR identity; reopening/updating the same PR SHALL update that target rather than creating another automatic Dev channel. A fork PR SHALL NOT be allowed to publish using privileged credentials.

#### Scenario: The PR is synchronized
- **WHEN** a new commit is pushed to the PR source branch
- **THEN** the per-PR URL identity SHALL remain stable
- **AND** the published revision and observed canonical URL SHALL match the intended revision and provider result
- **AND** no fixed Dev shall be overwritten.

#### Scenario: PR from a fork
- **WHEN** the PR's head repository differs from the trusted caller repository
- **THEN** no preview publish, migration or secret-bearing deployment action SHALL start.

### Requirement: Target canonical URL and resource identity SHALL be audited before mutation

Every deployment SHALL validate the exact target URL, commit SHA, effective Wrangler config, environment bindings and physical resource identity before any remote migration or publish. Target-built artifacts with embedded application origins MUST correspond to their intended URL. A Preview/Dev SHALL NOT be bound to writable Production D1/R2/Queue or unauthorized service credentials.

#### Scenario: Preview D1 maps to Production D1
- **WHEN** the plan or built effective config identifies a Production D1 binding for a Preview/Dev target
- **THEN** the operation SHALL fail before any remote mutation.

#### Scenario: Preview URL differs from actual provider URL
- **WHEN** the declared Preview URL disagrees with the canonical URL Cloudflare publishes
- **THEN** the deployment SHALL NOT be reported as verified or known-good
- **AND** the mismatch SHALL be visible as a failed deployment.

#### Scenario: Preview and production require different build-time origins
- **WHEN** client bundles embed callback/app URLs
- **THEN** a bundle built against the wrong target SHALL NOT be re-used for publish
- **AND** the effective build/config audit SHALL prove URL consistency.

### Requirement: Migrations SHALL be target-gated and safe for shared state

Migration SHALL be attempted only after target-specific read-only preflight and the required CI/authorization gate. A physical D1 SHALL be migrated at most once for a target transition, under appropriate resource-level concurrency. A PR SHALL NOT automatically apply schema-changing migrations to a D1 shared by unrelated PR Previews unless separately approved isolated-resource policy is established. Worker code rollback SHALL NOT claim to reverse D1 schema.

#### Scenario: A PR changes the D1 schema
- **WHEN** multiple PR Previews share the same nonproduction database
- **THEN** the PR's SQL/schema checks SHALL use a local or dedicated test DB
- **AND** its automatic Preview publishing SHALL NOT apply arbitrary migrations to that shared D1.

#### Scenario: A migration is failed, stale, mismatched or not authorized
- **WHEN** no successful matching target/physical-D1 migration evidence is available where one is required
- **THEN** the Publish operation SHALL be blocked before remote mutation.

#### Scenario: Fixed Dev migration changes compatibility
- **WHEN** a Dev promotion has an incompatible schema transition
- **THEN** it SHALL require an approved expand/contract plan or fail closed instead of breaking the active Dev cohort.

### Requirement: Smoke, recovery and release SHALL preserve target-identity safety

Smoke SHALL test the exact published target. Results SHALL distinguish content-verified, liveness-only/published-unverified, failed and intentionally skipped. A recovery attempt SHALL be target-owned and SHALL reject superseded revisions, incorrect URLs or wrong ledgers. Production Release SHALL require an independently content-verified Production deployment and any app-specific downstream gate.

#### Scenario: Cloudflare Access only redirects to login
- **WHEN** HTTP liveness succeeds but app content was not authenticated and checked
- **THEN** the deployment SHALL remain published-unverified
- **AND** it SHALL NOT satisfy known-good, automatic Release or verification-dependent rollback selection.

#### Scenario: A newer deployment supersedes the failed one
- **WHEN** an earlier Smoke failure attempts recovery after a newer valid publish
- **THEN** the earlier run SHALL NOT overwrite the newer version.

#### Scenario: A downstream OIDC integration fails
- **WHEN** Heaven ID / Checkin / Test Client each pass their local smoke but their deployed login flow fails
- **THEN** the cohort verification and dependent Release Gate SHALL fail
- **AND** prior per-app smoke success SHALL NOT mask this cross-service defect.

### Requirement: Production SHALL roll back; Preview and Dev SHALL be fixed forward

When a Production Smoke fails on a deployment published by the same run, and the caller's `production_rollback_policy` is `automatic`, the Delivery Engine SHALL attempt a guarded rollback to the recorded previous version (fail closed when the previous version is unknown, the D1 change is not additive, the failed version is no longer live, or a newer deployment exists). Preview and Dev SHALL have no automatic recovery: a failed or unverified Preview/Dev SHALL be marked unverified and corrected by a new revision (fix-forward). Redeploying an older revision to Preview/Dev is out of scope because it would require a credentialed rebuild of old source. A Worker rollback SHALL NOT be described as reverting D1 migrations or external side effects.

#### Scenario: Production smoke fails
- **WHEN** Production Smoke fails on this run's deployment under policy `automatic`
- **THEN** a guarded rollback SHALL be attempted and re-smoked with the built-in probe only.

#### Scenario: Preview or Dev smoke fails
- **WHEN** a Preview or Dev Smoke fails or is unverified
- **THEN** no rollback or redeploy of an older revision SHALL occur
- **AND** the target SHALL be reported unverified, never known-good, until a new revision is verified.

### Requirement: A Preview's application smoke SHALL run outside every credential boundary

A Preview's `smoke_cmd` is PR-controlled code. It SHALL run only in a separate job that holds no Cloudflare credential, no GitHub write credential, no Preview secret and no smoke credential, and SHALL receive only an explicit non-sensitive execution context (`TARGET_URL`, `DEPLOY_TARGET`, `TEST_ENV`, `EXPECTED_SHA`, `CI`). Sensitive smoke configuration (Access service token, `PROBE_HEADERS`, and any application credential under any name) SHALL be supplied only as an explicit GitHub Actions secret (`SMOKE_ENV`) that is passed solely to the trusted Smoke job (and the Production rollback re-probe) and masked per value; it SHALL NOT be a public workflow input and SHALL NOT appear in `toJSON(inputs)`, `inputs_json`, Plan outputs, job logs, artifacts or any untrusted job's environment or disk. Non-sensitive smoke data uses a separate public `smoke_vars` input, which refuses credential-looking names and is the only caller data the Preview command receives. `build_env_*` data SHALL NOT be written to the App Smoke job's disk. Authenticated probing and SHA/content verification SHALL remain in the trusted Smoke job, which SHALL require the application job's result and SHALL fail closed when it is missing, skipped or failed. Production and Dev run their application smoke in the trusted Smoke job against the protected revision.

#### Scenario: A Preview smoke command prints its environment
- **WHEN** the `SMOKE_ENV` secret contains `INTERNAL_API_KEY`, an Access service token and `PROBE_HEADERS`
- **THEN** none of them SHALL be visible to the Preview's `smoke_cmd`, to the Plan/Build/Deploy/App Smoke jobs or in any log, while the trusted probe still authenticates with them
- **AND** a credential-looking name in the public `smoke_vars` input SHALL be refused at initialisation.

#### Scenario: The application smoke result is absent
- **WHEN** the Preview application smoke did not succeed or did not run
- **THEN** the Preview SHALL NOT be verified.

### Requirement: Preview lifecycle SHALL be non-destructive until approved cleanup

Closing/merging a PR or deleting its source branch SHALL retire/mark the Preview's ledger eligibility but SHALL NOT immediately remove resources by default. Any eventual cleanup SHALL verify ownership, retained Dev names, active PR/branch references and approved expiry. A cleanup failure SHALL not imply successful resource deletion.

#### Scenario: PR is closed
- **WHEN** the PR is closed and a Preview remains published
- **THEN** the Preview SHALL be marked eligible for policy-controlled expiry, without deleting the fixed `dev` Preview
- **AND** unapproved or unmatched resources SHALL be retained.

## MODIFIED Requirements

### Requirement: Migration is a push-only pre-deployment stage

For the coordinated v2 contract, Migration SHALL be a **deployment-only** stage rather than a universally Push-only stage. It MAY run for an approved PR Preview or manual Dev promotion **only if** the selected target has an explicitly approved safe migration policy and satisfies the preflight/authorization gates. Production Migration SHALL remain allowed only for an eligible protected production delivery. PR Preview shared-D1 migration restrictions above take precedence. `pull_request` Test-only and fork requests SHALL never migrate.

#### Scenario: Trusted approved Dev promotion
- **WHEN** the Dev target is selected through protected dispatch and needs a compatible nonproduction D1 migration
- **THEN** that migration MAY execute with the same audited physical-resource checks as Production.

#### Scenario: Unauthorized PR
- **WHEN** the workflow is running Test-only or on an untrusted PR
- **THEN** no migration SHALL run.

## REMOVED Requirements (coordinated v2 interface only)

### Requirement: Separate automatic branch and fixed Dev channels controlled by booleans

The v2 interface SHALL NOT require, advertise or continue honoring the old combination of `preview_on_branch_push`, `shared_dev_on_branch_push`, `shared_dev_native_preview_on_branch_push` and `deployment_strategy` following coordinated consumer cutover. Target selection SHALL instead be performed from the validated event and explicit target policy.

#### Scenario: Caller sends a retired switch after cutover
- **WHEN** a caller includes a removed v2 workflow_call input
- **THEN** GitHub's interface validation SHALL reject it rather than silently invoking a hidden legacy mode.

