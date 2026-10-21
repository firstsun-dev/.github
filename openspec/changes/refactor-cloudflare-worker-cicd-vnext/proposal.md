# Proposal: Cloudflare Worker CI/CD vNext — lean, parallel and environment-correct delivery

## Why

The existing opt-in v2 Native Preview implementation meets the branch-URL goal but expands into many orchestration jobs (Plan, three tests, Test Gate, Preview, Production, verification and recovery). Callers may additionally run Prepare, Lint and Build Check, duplicating installation, tests and application builds. v2 also applies D1 migrations within deployment jobs. Consumers need a more readable, uniform pipeline without weakening preview, data or production safety.

This change is a **new, docs-only OpenSpec proposal**. It supersedes the v2 job topology where stated, not the deployed v1/v2 behavior or the established Native Preview security/lifecycle contract. It does not authorize implementation or deployment.

## Proposed outcome

A single Worker uses **CI and E2E in parallel, then Migration, then Deploy, then optional main-only Release**. Monorepos prepend lightweight Detect Changes and invoke the same worker contract for dependency-affected applications. CI and E2E are parallel jobs; lint, typecheck, unit/integration checks and build validation are normally steps in CI, not more jobs. E2E may use a browser matrix where valuable.

- **Migration is always a distinct job when remote D1 migrations apply.** It selects Production or shared Preview D1 from a trusted target, applies the same project-owned migration files, and gates Deploy. D1-less Workers bypass it.
- **Deploy is one reusable job implementation for both Production and branch Native Preview**, selected by a validated `target=prod|preview`; it performs its own target-specific build and post-publish smoke/recovery.
- **No GitHub Build Artifact is required between jobs.** E2E builds its own local/test configuration; Deploy rebuilds for the real target URL/bindings. Report/trace artifacts are optional, failure-only and short-lived.
- **Retain v1's app-owned, per-job environment input model.** Separate nonsecret CI/E2E configuration, production/preview build inputs, migration target credentials and post-deploy smoke configuration.
- **Every eligible branch keeps its own stable Native Preview URL**, while previews of one Worker may deliberately share one nonproduction D1/R2 baseline. A Branch Preview is not a Wrangler named `dev` deployment.
- **Retain security and compatibility:** fork PRs cannot deploy; trusted main alone deploys Production; Hybrid Cron/Queue tests remain protected; branch deletion retains Native Previews under the existing v2 owner policy; v1/v2 callers are unchanged until they opt into a separately versioned replacement.

## Scope and ownership

`firstsun-dev/.github` owns the shared contract, orchestration helpers, environment injection and publish/recovery primitives. Consumer repos own their app-specific scripts, builds, tests, migrations, resource mappings, event filtering, secrets, and release policies.

In-scope first-pass candidates: `heaven-www`, `blog`, `heaven-monorepo` (ID, Checkin, Test Client), `innovation-apps` (Diet Manager, Notification Hub), and `anas-mcp` (Production Only). Discover and explicitly classify any other Worker before migration. `infra-config` owns required baseline resource/IaC changes, not generic CI logic.

## Non-goals

- No change to Production runtime or Production data in this planning PR.
- No per-feature-branch D1/R2 by default and no centralized migration-service/database.
- No guarantee that CI, E2E and Deploy builds are bit-identical; target URLs and bindings may differ.
- No GitHub artifact-based deployment handoff.
- No replacement of Cloudflare Access or bypass of protected Dev and background-trigger policies.
- No mandatory Semantic Release for repositories that do not use it.
- No premature deletion or rewriting of existing v1/v2 OpenSpec history.

## Relationship to partially delivered v2

This vNext change becomes the **only active target for further CI/CD refactoring and unfinished Worker consumer migrations** after approval. [v2 state/OpenSpec PR #56](https://github.com/firstsun-dev/.github/pull/56) records v2.x as shipped, its completed milestones and a transfer register; it remains authoritative for running v2 consumers, not for the new architecture. v1/v2 stay supported for existing users (including security/correctness maintenance). The existing [Blog draft PR #448](https://github.com/firstsun-dev/blog/pull/448) is reviewed/adapted in the vNext pilot, not duplicated or assumed merged.

Do not close tracking issue #43 merely because either documentation PR is merged. Remaining v2 validations and consumer work are explicitly mapped in [vNext tasks.md](./tasks.md#v2-work-handoff-and-non-duplication).

## Compatibility and rollout

This is a **breaking shared-workflow DAG/interface revision**. Implement under a new major compatibility line (provisionally `v3`), preserve `@v1`/`@v1.5.0`/`@v2` while callers migrate, pin an immutable SHA during pilots, then publish a tested immutable release. Do not repoint old major tags.

Ship shared workflows first, then pilot Blog and Heaven ID, then selective monorepo and remaining workers in separate repo PRs. Prefer focused branches, one logically grouped commit per docs or code PR, and squash merge after review. Track under `firstsun-dev/.github#43`; do not close it merely because the spec PR merged.

## Questions intentionally not decided by this spec PR

1. Heaven ID: whether Native Previews may share `heaven-id-db-dev` with protected persistent Dev, or need a separate shared Preview D1. Until approved, do not treat the two trust/lifecycle boundaries as interchangeable.
2. Concurrency: exact orchestration/lock design that keeps the **entire Migration → Deploy interval** safe across runs (and, if relevant, repositories) without unnecessarily serializing CI/E2E. Job-local migration locks alone are insufficient.
3. Browser-matrix breadth and required post-deploy extended checks per consumer, including Blog Safari/Lighthouse.
4. Repo-specific release/version metadata and build-time URL dependencies that need target-aware overrides.

## Success criteria

The reviewed plan and consumer acceptance tests shall demonstrate parallel CI/E2E, an independent D1 Migration job, a single production/preview Deploy contract, correct build environments without required artifacts, stable URLs for two simultaneous branches, controlled shared Preview D1 migrations, monorepo affected-worker selection, preserved v1/v2 consumers, and safe smoke/recovery.
