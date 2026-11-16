# OpenSpec Source Audit

Audit date: 2026-09-15 (Asia/Taipei)

Baseline: `firstsun-dev/.github` `main` at `6fb04eb86b52a2f4d290db8f7bdfd82e190d48a3`.

## Purpose

This audit records which repository and consumer sources were used to derive the current-state OpenSpec. The specifications describe implemented contracts; they do not silently redesign inconsistent or imperfect behavior discovered during the audit.

This document preserves the source inventory and evidence reviewed for the 2026-09-15 baseline. The inventory below is historical: a file listed there is not thereby claimed to exist or remain supported today. Current status for capabilities changed since that audit is summarized next.

## Current support update (2026-11-16)

| Capability | Current status | Current specification and executable sources |
| --- | --- | --- |
| Cloudflare Worker delivery | The supported shared interface is v2. The v1 workflow and branch-alias action were retired after organization-wide searches found no current callers. | Current contract index: [Cloudflare Worker Delivery capability spec](./specs/cloudflare-worker-delivery/spec.md). Shipped v2 baseline: [v2 baseline delta specification](./changes/redesign-cloudflare-worker-cicd-v2/specs/cloudflare-worker-delivery/spec.md). The simplification, refactor, and target-unification changes are follow-on contracts, not silent replacements of shipped behavior. Executable engine: `.github/workflows/_cf-worker-v2.yml` and internal `_cf-worker-ci.yml`, `_cf-worker-delivery.yml`, `_cf-worker-lifecycle.yml`, `_cf-worker-preview-gc.yml`, with `actions/cf-preview/`. Operator guide: [docs/cf-worker-v2.md](../docs/cf-worker-v2.md). |
| CodeGraph Index CI | The reusable workflow was retired after an organization-wide search found no current callers. | [Retired capability status](./specs/codegraph-index-ci/spec.md). |

The inventory below retains the audit meaning of the 2026-09-15 snapshot; references there to `_cf-worker-template.yml` and `_codegraph.yml` are historical source evidence, not currently supported executable sources.

## Historical executable source inventory

### Organization GitHub surface

- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `.github/ISSUE_TEMPLATE/enhancement.yml`
- `.github/ISSUE_TEMPLATE/feature_request.yml`
- `.github/ISSUE_TEMPLATE/quick_task.yml`
- `.github/ISSUE_TEMPLATE/config.yml`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `profile/README.md`

These define the shared issue/PR entry points and public organization profile.

### Shared CI primitives

- `actions/setup/action.yml`
- `actions/mailpit/action.yml`
- `actions/parse-env/action.yml`
- `.github/workflows/_detect-changes.yml`

`actions/setup-rclone` is classified under `rclone-sync` because its credentials and lifecycle are specific to that capability.

### Cloudflare Worker delivery

- `.github/workflows/_cf-worker-template.yml`

The workflow itself is authoritative for test, migration, deployment, verification, and rollback behavior.

### PostgreSQL migration delivery

- `.github/workflows/_pg-migration-test.yml`
- `.github/workflows/_pg-migration-deploy.yml`

### Obsidian plugin delivery

- `.github/workflows/obsidian-plugin-ci.yml`
- `.github/workflows/obsidian-plugin-release-build.yml`

The split between release metadata and release-asset build/provenance is part of the current contract.

### Post-deploy verification

- `.github/workflows/_lighthouse-template.yml`
- `.github/workflows/_speedinsights-template.yml`
- `.github/workflows/_zap-scan-template.yml`

### Rclone sync

- `actions/setup-rclone/action.yml`
- `actions/setup-rclone/README.md`
- `.github/workflows/rclone-sync.yml`

### CodeGraph index CI

- `.github/workflows/_codegraph.yml`

### Shared engineering configuration

- `packages/eslint-config/index.js`
- `packages/eslint-config/package.json`
- `packages/tsconfig/base.json`
- `packages/tsconfig/react.json`
- `packages/tsconfig/worker.json`
- `packages/tsconfig/package.json`
- `scripts/release.sh`

## Historical maintained documentation

- `docs/brand-system.md`
- `docs/infrastructure-overview.md`
- `docs/case-studies/README.md`
- `docs/case-studies/heaven-platform.md`
- `profile/README.md`

These sources explain organization intent and public positioning. They do not override executable workflow/action/package behavior.

## Consumer evidence reviewed for the historical audit

Organization code search confirms that the shared surface is consumed across multiple repositories, including:

- `firstsun-dev/blog` — shared setup action and Cloudflare Worker reusable workflow.
- `firstsun-dev/anas-mcp` — OpenSpec explicitly assigns generic Cloudflare Worker delivery to this repository and requires a thin caller.
- `firstsun-dev/heaven-www` — shared setup action / centralized delivery convention.
- `firstsun-dev/innovation-apps` — shared setup and Cloudflare-oriented workflow usage.
- `firstsun-dev/skills`, `books-mgmt`, and `heaven-video-summary` — `setup-rclone` usage.
- Obsidian plugin repositories — shared plugin CI/release workflow usage.

Consumer repositories are evidence of the integration contract, not owners of generic behavior implemented here.

## Recent commit context reviewed for the historical audit

Recent changes were used as intent/compatibility context, especially:

- addition of reusable changed-path and Atlas/PostgreSQL migration workflows;
- fixing `atlas-config` so the declared input is passed to Atlas CLI calls;
- moving Obsidian release asset build/attestation to the exact published tag;
- allowing shared Node/pnpm setup to be skipped on pre-provisioned self-hosted runners;
- making successful test-report handling lighter while retaining failure evidence;
- establishment of the public Firstsun Dev brand/profile system.

Commit messages explain why a contract exists but do not supersede the executable source.

## Historical observations deliberately not normalized

### Mixed consumer pinning

The repository release script establishes exact `vX.Y.Z` tags and a moving major `vX` tag. Current consumers nevertheless include both versioned references such as `@v1` and development references such as `@main`.

The current-state spec records the intended tag semantics without rewriting existing consumers. Standardizing all consumers requires a separate coordinated change.

### Package release scope

`scripts/release.sh` bumps both `@firstsun/eslint-config` and `@firstsun/tsconfig`, creates an exact release tag, and moves the matching major tag. The script is interactive and does not itself publish packages to a registry. The OpenSpec therefore specifies tag/version behavior, not an npm publishing guarantee.

### Workflow-specific runner assumptions

Some reusable workflows default to GitHub-hosted runner labels while the Cloudflare Worker pipeline defaults to the organization `linux` runner and skips Node/pnpm installation because that runner image provides them. These differences are current behavior and are not collapsed into one universal runner contract.

## Sources excluded from the historical audit

- Secret values, private infrastructure configuration, and credentials.
- Historical agent transcripts or generated run artifacts as normative sources.
- Proposed behavior not represented by current executable code.
- Product-specific deployment or application behavior owned by consumer repositories.

## Reconciliation rule for future audits

If a future audit finds disagreement between these specs and `main`, first determine whether code changed without its corresponding OpenSpec update. Until reconciled, executable code remains the observed current behavior, while an accepted OpenSpec change remains the normative target for work in progress.
