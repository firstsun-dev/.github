# Cloudflare Worker Delivery Capability — v2 Delta Specification

These requirements apply only to **opt-in v2** callers. Current v1/v1.5.0 delivery requirements remain valid for existing callers.

## ADDED Requirements

### Requirement: An eligible branch push SHALL produce a stable branch Native Preview URL

Every successful, trusted, non-`main` `push` affecting a Preview-enabled Worker SHALL create or update that Worker's Native Preview identified by **branch ref**, without requiring a pull request. The URL SHALL stay stable for the lifetime of the branch. `production-only` workers are explicitly exempt.

#### Scenario: First push with no open PR
- **WHEN** a branch is pushed, required tests/preflight pass, and no PR exists
- **THEN** Native Preview deployment SHALL occur for that Worker
- **AND** the real verified stable URL SHALL be published in the GitHub deployment/job summary
- **AND** the Worker SHALL NOT deploy to persistent shared Dev

#### Scenario: Same branch is pushed again
- **WHEN** a later valid commit is pushed to the same branch
- **THEN** the existing Preview SHALL be updated while its URL stays unchanged
- **AND** the new commit SHA SHALL be visible in deployment metadata
- **AND** unrelated branches' URLs/code SHALL remain unaffected

#### Scenario: Two normalized branch names look alike
- **WHEN** different raw refs normalize to the same slug (for example `Feat/A` and `feat-a`)
- **THEN** the ledger collision check SHALL fail the later branch before tests and deployment, naming the owning ref
- **AND** neither branch SHALL overwrite the other's Preview

#### Scenario: Branch push fails before deploying
- **WHEN** build, tests or binding preflight fail before the Preview update begins
- **THEN** the workflow SHALL NOT claim success or publish an unverified new URL
- **AND** the prior Preview deployment SHALL remain unchanged

#### Scenario: Post-deploy Preview smoke fails
- **WHEN** Wrangler has updated the branch Preview but exact-URL smoke fails
- **THEN** the workflow SHALL report failure and explicitly identify the active Preview as unverified
- **AND** a tested recovery mechanism SHALL be used when available, or an actionable manual recovery path and alert SHALL be recorded
- **AND** the workflow SHALL NOT assume `wrangler preview` provides an undocumented automatic rollback

### Requirement: V2 SHALL classify each Worker by explicit deployment strategy

A Worker SHALL be configured as `production-preview`, `hybrid`, or `production-only`; monorepo apps SHALL be independently targeted.

#### Scenario: Single Worker with branch path filters
- **WHEN** any trusted non-main push occurs in a single-Worker repo
- **THEN** CI SHALL NOT suppress the branch Preview merely because changed paths are documentation-only or otherwise outside existing deployment filters

#### Scenario: Monorepo push changes one Worker
- **WHEN** a branch push affects only a selected Worker app and its dependencies
- **THEN** only affected Workers SHALL create/update their own branch Previews
- **AND** unaffected Workers SHALL NOT be rebuilt or redeployed without reason

#### Scenario: Hybrid branch push
- **WHEN** a feature branch is pushed for a Hybrid Worker
- **THEN** it SHALL create/update that branch's HTTP Native Preview after checks
- **AND** it SHALL NOT update protected persistent Dev or automatically execute Cron/Queue consumer triggers

#### Scenario: Production-only security exception
- **WHEN** a non-main branch is pushed for `anas-mcp` or another approved production-only Worker
- **THEN** credential-free/least-privilege CI SHALL run
- **AND** no public Preview URL SHALL be enabled without a separately approved Access-safe threat model

### Requirement: Native Preview and Aliased Version URL SHALL NOT be conflated

V2 SHALL use the pinned supported Native Preview interface and an explicit `previews` configuration; it SHALL NOT use v1's aliased version deployments as a fallback.

#### Scenario: Native Preview URL extraction
- **WHEN** `wrangler preview --name <branch-identity> --json` succeeds
- **THEN** the workflow SHALL extract the stable URL from machine-readable output
- **AND** verify that exact URL before publishing a successful GitHub Deployment

#### Scenario: Wrangler does not support the required Preview contract
- **WHEN** the pinned project Wrangler or Cloudflare account lacks required capability
- **THEN** Preview SHALL fail closed with clear diagnostics
- **AND** it SHALL NOT deploy to shared Dev or Production instead

### Requirement: Preview resource and authentication isolation SHALL be explicit

V2 SHALL audit all mutable resources, secrets, integration calls and ingress before Preview creation. State in distinct Previews is NOT assumed isolated merely because code and URL differ.

#### Scenario: Shared non-production Preview data (owner-approved)
- **WHEN** a Worker such as Blog requires D1 migrations and its owner has chosen a shared non-production Preview baseline
- **THEN** all branch Previews SHALL bind only that explicitly identified non-production D1/R2 resources, and migrations SHALL be applied only to that database before Preview creation
- **AND** it SHALL NOT migrate Production or the legacy shared Dev Worker's data
- **AND** the consumer PR SHALL record that branches share state and that differing schema revisions can interfere

#### Scenario: Branch-scoped resources (optional)
- **WHEN** an owner instead chooses per-branch D1/R2
- **THEN** the resources SHALL have deterministic, ownership-checked names, a quota ceiling that fails closed, idempotent provisioning and deletion on branch deletion

#### Scenario: Preview binding without an explicit resource id
- **WHEN** the source or the build-generated Wrangler config declares a Preview storage binding without a resource id (for example an adapter-added KV `SESSION`)
- **THEN** preflight SHALL fail before `wrangler preview` runs
- **AND** the prior Preview SHALL remain unchanged

#### Scenario: Production resource or unsafe Service Binding detected
- **WHEN** Preview bindings can write Production state, share unsafe Secrets, route to an unsafe production service binding, or bypass required Access/auth
- **THEN** the deployment SHALL fail closed until reconfigured
- **AND** no runtime secret shall be logged or returned as a Preview URL

#### Scenario: Untrusted fork PR
- **WHEN** a PR originates from a fork
- **THEN** credential-free validation MAY run
- **AND** Cloudflare deployment tokens or trusted Preview secrets SHALL NOT be exposed

### Requirement: Preview lifecycle SHALL be branch-owned rather than PR-owned

V2 SHALL safely identify, update and delete Previews by repository + Worker + full branch ref. PR feedback is optional and secondary to push ownership.

#### Scenario: PR opened after Preview already exists
- **WHEN** a PR is opened for a pushed branch
- **THEN** the workflow MAY surface that branch's existing verified URL on the matching PR
- **AND** SHALL NOT deploy a second PR-number-scoped Preview

#### Scenario: PR closed, source branch remains
- **WHEN** a PR is closed/merged but the source branch still exists
- **THEN** the branch's stable Preview SHALL remain available

#### Scenario: Branch deletion
- **WHEN** GitHub reports deletion of a non-main branch
- **THEN** the Native Preview, its stable URL and its resources SHALL be retained (owner decision; the stable URL cannot be removed independently of the Preview)
- **AND** only the GitHub deployment ledger for that repo, Worker and branch identity SHALL be marked inactive, never deleted
- **AND** any later deletion SHALL be an explicit, manual or separately approved sweep action targeting only that identity
- **AND** it SHALL NOT delete an unrelated branch Preview, persistent Dev or Production

#### Scenario: Older and newer runs complete out of order
- **WHEN** the same branch is pushed repeatedly
- **THEN** concurrency and SHA checks SHALL prevent stale runs from restoring old code or status

### Requirement: Background trigger testing SHALL be separate from HTTP Previews

Queue Consumers and Cron Triggers SHALL NOT be presumed active on Native Previews.

#### Scenario: Hybrid Worker validation
- **WHEN** a Worker uses Queue Consumer/Cron behavior
- **THEN** those paths SHALL be tested in protected persistent Dev or dedicated test harnesses
- **AND** normal branch pushes SHALL NOT trigger shared Dev deployment

### Requirement: Production release SHALL preserve migration-aware recovery and existing v1 callers

V2 SHALL keep production version traceability and smoke tests with per-app recovery policy; migration safety is independent of Worker version rollback.

#### Scenario: Production smoke fails
- **WHEN** smoke verification fails after a Production deploy
- **THEN** configured recovery (automatic/manual/disabled) SHALL execute as approved
- **AND** automatic rollback SHALL require proven state/schema compatibility

#### Scenario: Worker rollback after database migration
- **WHEN** a Worker deployment is rolled back
- **THEN** the workflow SHALL NOT imply a D1 schema migration or side effect has been reverted

#### Scenario: Consumer stays on pinned v1
- **WHEN** a repository continues using v1/v1.5.0
- **THEN** its current deployment contract and release tag SHALL remain unchanged

### Requirement: Branch deletion cleanup SHALL be driven from the default branch

#### Scenario: Delete event handling
- **WHEN** GitHub emits a branch `delete` event
- **THEN** only the default branch's workflow definition runs; the caller workflow MUST therefore be merged to the default branch before cleanup works
- **AND** deleting an already-deleted Preview (Cloudflare code 10025) SHALL be treated as success when a manual or approved sweep deletes it
