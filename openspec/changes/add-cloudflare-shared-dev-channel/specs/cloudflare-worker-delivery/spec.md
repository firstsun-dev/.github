# Cloudflare Worker Delivery — Shared Dev Delta Specification

## ADDED Requirements

### Requirement: Feature Preview and Shared Dev SHALL be independently switchable

A non-main push SHALL deploy a Native Preview only when `preview_on_branch_push` is true and a Shared Dev deployment only when `shared_dev_on_branch_push` is true (default false). Neither channel SHALL require the other; main SHALL always follow the Production path and never deploy Shared Dev.

#### Scenario: Four toggle combinations
- **WHEN** a trusted non-main push passes validation
- **THEN** both/feature-only/Dev-only/neither SHALL deploy exactly the enabled channels
- **AND** a skipped or failed channel SHALL NOT block the other or the validation jobs.

#### Scenario: Production-only Worker
- **WHEN** `deployment_strategy` is `production-only` and `shared_dev_on_branch_push` is true
- **THEN** the run SHALL fail with an explicit configuration error and deploy no Dev.

### Requirement: Shared Dev SHALL be one stable, serialized, last-verified-wins deployment

All eligible branches SHALL target the same configured Dev Worker and `app_url_dev`. Dev deployments from different branches SHALL be serialized. A superseded run for the same branch SHALL deploy nothing. A failed build, config audit or migration SHALL leave the running Dev Worker unchanged. A failed smoke SHALL fail the job and report either a verified recovery to the last known-good commit or that manual recovery is required.

#### Scenario: Two branches push
- **WHEN** two branches push while Shared Dev is enabled
- **THEN** their Dev deployments run one after another and the last verified one is served at the same URL.

#### Scenario: Branch deleted or switch disabled
- **THEN** the Dev Worker, its URL and data SHALL be retained.

### Requirement: Shared Dev SHALL isolate environment and data (credentials intentionally shared)

Shared Dev SHALL use the existing `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ACCOUNT_ID` pair in the `<app>/dev` Environment by the 2026-10-28 owner decision; no dedicated Dev token is required. It SHALL keep the Dev build environment, named Wrangler environment, and `env.<name>.d1_databases` for migrations (no fallback to top-level/Production D1). Its effective Wrangler config SHALL name the expected Dev Worker and bind no Production resource. Dev and Native Preview MAY share one non-Production D1; Production SHALL NOT.

### Requirement: Manual Persistent Dev dispatch SHALL remain available and distinct

The `persistent-dev` workflow_dispatch path SHALL keep its approved-ref gate, Environment, concurrency group and behaviour, and SHALL NOT be triggered by the automatic Shared Dev switch.

### Requirement: Shared Dev resource sharing SHALL be exact, explicit and independent of Preview policy

Shared Dev SHALL audit its effective config with its own policy inputs (`dev_binding_allow`, `dev_deny_resources`), never `preview_binding_allow`/`preview_deny_resources`. A Production collision SHALL fail unless an exact `kind:BINDING=identity` entry for a supported storage kind (R2, KV, Vectorize, Analytics Engine) matches the bound resource. Hard-denied resources SHALL always fail; a Production D1 SHALL never be approvable; unknown kinds, malformed, duplicate or unmatched entries, missing identities and identity mismatches SHALL fail closed.

#### Scenario: Exact approved R2
- **WHEN** the Dev config binds `SHINYI_MUYU_VOICE_CACHE` to `firstsun-blog-assets` and the allowlist approves exactly that
- **THEN** the audit passes; a different bucket under the same binding, or the same bucket under another binding, fails.

### Requirement: Shared Dev SHALL NOT reach Production queues, services, routes or unverifiable bindings

Queue producers, consumers and dead-letter queues, Service Bindings, routes/custom domains, cross-Worker Durable Objects and bindings the audit cannot verify SHALL fail closed against Production identities; Service Bindings SHALL require an exact approval and SHALL NOT target Production Workers.

### Requirement: Manual and automatic Dev deployments SHALL share one command contract and one verification rule

With `dev_build_cmd` set, build, publish and smoke SHALL be separate commands and both paths SHALL run them with the same audit. A deployment SHALL enter the known-good ledger only after its smoke passes; legacy manual runs SHALL never enter it.

### Requirement: Dev recovery SHALL rebuild the last-good commit with that commit's identity

Recovery SHALL rebind the failed run's SHA in build environment and version to the last-good SHA, repeat the audit and full smoke, state when commit identity is unverified, never claim success when unverified, and never roll back D1.

## RENAMED Requirements
- FROM: `### Requirement: Remote D1 migrations SHALL run as a separate, target-bound job`
- TO: `### Requirement: Remote D1 migrations SHALL run as an explicit, target-bound step of the delivery engine`

## MODIFIED Requirements

### Requirement: Remote D1 migrations SHALL run as an explicit, target-bound step of the delivery engine

A D1-capable Worker SHALL apply remote migrations from one explicit step of the single delivery engine inside the Deploy (Production / Native Preview) and Shared Dev jobs, never from a standalone job and never from a caller-supplied command. Production, Preview and Shared Dev SHALL apply the same project-owned migration SQL to their own D1. The required order SHALL be: validation succeeded → deployable built → effective Wrangler configuration audited → exact migration target validated → stale-SHA check → pending migrations applied → stale-SHA check → audited artifact published → smoke → verified / recovery. Schema SHALL NOT be migrated before the deployable build and the configuration audit succeeded. The target SHALL be bound from the engine target and SHALL NOT be derivable from application build or smoke environment. Worker recovery SHALL NEVER roll a D1 schema back.

#### Scenario: Failed migration prevents publish
- **WHEN** a migration step fails
- **THEN** the job SHALL fail, nothing SHALL be published or marked verified, and Release SHALL NOT run.

#### Scenario: Branch advances during a long migration
- **WHEN** the branch tip changes while the migration runs
- **THEN** the run SHALL re-check the exact tip immediately before publishing and SHALL NOT publish the superseded code over a newer deployment.

#### Scenario: Deployed D1 differs from the migration target
- **WHEN** the D1 binding of the configuration that will be published (including an adapter-generated configuration) has a different physical `database_id` than the validated migration target
- **THEN** the run SHALL fail before the migration and before the publish.

## ADDED Requirements

### Requirement: Production, Native Preview and Shared Dev SHALL use one delivery implementation

The workflow SHALL contain a single Deploy job for `target = prod | preview` and SHALL call the same composite delivery engine from the Shared Dev job (`target = dev`) and the manual persistent-Dev job (`mode = manual`). Build, audit, migration, publish, smoke and recovery logic SHALL NOT be duplicated per target; target differences SHALL be explicit, validated parameters. Complex logic SHALL live in tested scripts, not inline workflow YAML.

#### Scenario: Cross-repository reusable workflow
- **WHEN** the workflow is called from another repository
- **THEN** each job SHALL check out `firstsun-dev/.github` at `tools_ref` into `.cf-tools` before referencing the composite actions by the static path `./.cf-tools/actions/<name>`; no `uses:` SHALL contain an expression.

### Requirement: Application environment SHALL NOT override deployment controls

`build_env_prod`, `build_env_preview`, `build_env_dev`, `smoke_env` and Preview secrets are data. Names that control the delivery target, credentials, D1 target, branch SHA, verification or engine state (including `TARGET`, `DEV_ENV`, `GH_TOKEN`, `EXPECTED_SHA`) SHALL be rejected for the whole block, and application env SHALL never be written to the job environment.

### Requirement: The audited artifact SHALL be exactly the published artifact

The build stage SHALL be separate from the publish stage. The audited configuration (and its Worker entry and assets fingerprint) SHALL be recorded and the publish SHALL pass it explicitly, SHALL not rely on an ambient named environment, and SHALL fail if a caller-supplied publish command changes it.

### Requirement: Concurrency SHALL serialize by shared resource identity

The run-level concurrency group SHALL be derived from the shared resource (`d1_lock_scope`, else the app) with distinct Production and non-Production groups, so applications that share one physical D1 are serialized regardless of `app_name`. Automatic Shared Dev and manual persistent Dev SHALL share one group. The limitation that GitHub concurrency cannot lock a D1 shared with another repository SHALL be documented.
