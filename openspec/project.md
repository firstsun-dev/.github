# OpenSpec project conventions

## Mission

`firstsun-dev/.github` is the organization-level engineering platform for Firstsun repositories. It centralizes reusable delivery workflows, composite GitHub Actions, shared engineering configuration, organization GitHub templates, and the public engineering profile/docs that describe this operating model.

The repository is a shared contract surface. Changes can affect multiple consumer repositories even when the physical change is small.

## Scope

OpenSpec in this repository owns current-state and proposed behavior for:

- reusable GitHub Actions workflows under `.github/workflows/`;
- composite actions under `actions/`;
- shared engineering configuration packages under `packages/`;
- release/tagging conventions used to distribute shared workflows, actions, and packages;
- organization issue/PR templates and the public GitHub organization profile;
- public platform/brand documentation when it defines or explains a contract owned by this repository.

Application-specific product behavior, infrastructure implementation, runtime secrets, and deployment policy owned by consumer repositories are out of scope unless a change explicitly coordinates across repositories.

## Change structure

Use `openspec/changes/<change-id>/` with:

- `proposal.md` — rationale, scope, compatibility impact, rollout, repository ownership, and gates.
- `design.md` — architecture, interfaces, migration/cutover decisions, and failure handling.
- `tasks.md` — implementation checklist grouped by phase/repository.
- `specs/<capability>/spec.md` — normative requirements and scenarios introduced or changed by the proposal.

Implemented, still-supported behavior belongs under `openspec/specs/<capability>/spec.md`.

## Capability boundaries

The current platform is divided into these capabilities:

1. `organization-github-surface` — organization profile and shared issue/PR entry points.
2. `shared-ci-primitives` — generic composite actions and changed-path detection used by higher-level workflows.
3. `cloudflare-worker-delivery` — reusable Cloudflare Worker test/build/migrate/deploy/verify/revert pipeline.
4. `postgres-migration-delivery` — Atlas-managed PostgreSQL migration validation and deployment.
5. `obsidian-plugin-delivery` — reusable Obsidian plugin CI, packaging, release metadata, provenance, and release assets.
6. `post-deploy-verification` — reusable Lighthouse, PageSpeed Insights, and ZAP verification workflows.
7. `rclone-sync` — reusable Google Drive-backed rclone setup and synchronization.
8. `shared-engineering-config` — shared ESLint and TypeScript configuration packages.

A new spec SHOULD extend an existing capability unless the behavior has a distinct consumer contract, lifecycle, or failure domain.

## Consumer ownership model

Consumer repositories SHOULD remain thin at the organization-platform boundary.

- This repository owns generic setup, delivery, verification, rollback, and reusable configuration behavior.
- Consumer repositories own their triggers, app-specific commands, paths, target URLs, repository-specific environment bindings, and product-specific policy.
- A consumer SHOULD NOT copy or fork generic shared workflow/action logic merely to customize one application.
- A product-specific exception that requires divergence from the shared contract SHOULD be documented in that product's OpenSpec and, when it changes the shared contract, coordinated with an OpenSpec change here.

## Compatibility and versioning

Shared consumers may pin an exact release tag/commit or a supported floating major tag such as `v1`.

- Exact `vX.Y.Z` tags are immutable release points.
- Floating `vX` tags represent the latest compatible release in that major line and may move forward.
- `main` is development state and MUST NOT be treated as an immutable compatibility boundary.
- Breaking changes to a published shared workflow/action interface require a new major compatibility line or an explicit coordinated migration.

Current consumers include both versioned and `@main` references; that is recorded as a source-audit observation rather than normalized away by these specifications.

## Security and secret boundaries

OpenSpec SHALL document secret inputs and trust boundaries but SHALL NOT record secret values.

- Shared workflows/actions may define required secret names and how those secrets are consumed.
- Runtime/provider credentials remain owned by the consumer or platform that provisions them.
- Public docs and examples MUST NOT contain production credentials or private infrastructure details.

## Current-state source precedence

When reconciling existing material, use this order:

1. Current executable workflow/action/package code on `main`.
2. Current consumer usage that demonstrates the supported integration contract.
3. Maintained repository docs and profile material.
4. Recent commits for intent and compatibility context.
5. Historical documents and comments as supporting context only.

When documentation conflicts with executable behavior, current executable behavior wins until a deliberate OpenSpec change updates the contract.

The repository-wide source audit and inclusion decisions are recorded in [`openspec/SOURCES.md`](./SOURCES.md).
