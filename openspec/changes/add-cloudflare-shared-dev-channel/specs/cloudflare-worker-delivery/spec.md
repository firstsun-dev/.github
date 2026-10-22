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

### Requirement: Shared Dev SHALL isolate credentials, environment and data

Shared Dev SHALL use only `CLOUDFLARE_DEV_API_TOKEN`/`CLOUDFLARE_DEV_ACCOUNT_ID` in the `<app>/dev` Environment, the Dev build environment, the named Wrangler environment, and `env.<name>.d1_databases` for migrations (no fallback to top-level/Production D1). Its effective Wrangler config SHALL name the expected Dev Worker and bind no Production resource. Dev and Native Preview MAY share one non-Production D1; Production SHALL NOT.

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
