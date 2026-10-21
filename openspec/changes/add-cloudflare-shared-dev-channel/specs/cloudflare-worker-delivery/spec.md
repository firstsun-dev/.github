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
