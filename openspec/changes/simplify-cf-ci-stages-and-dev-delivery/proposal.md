# Proposal: Simplify CF Worker CI stages and development delivery

Status: **Proposed / documentation-only** (2026-11-13). This change records a coordinated, intentionally breaking redesign of the firstsun-dev shared CI/CD contract. **It does not authorize deployment, resource deletion, release-tag movement, or consumer modification in this PR.**

## Why

The current `firstsun-dev/.github` Cloudflare Worker v2 workflow mixes (1) event routing, (2) six fixed CI check types, (3) four deployment kinds, and (4) GitHub Actions simulation/contract testing. Callers must maintain many flags and channel-specific inputs. Blog currently enables both a branch Native Preview and a shared named Native Dev on the same branch push, while other applications require stable Dev or a real Worker with Queue/Cron capabilities. This creates unnecessary runs, DAG branches, per-kind outputs, cross-run gates and test infrastructure.

The desired contract resembles a GitLab stage model without emulating GitLab's scheduler: **Verify / Test / Delivery** are stable logical stages; application repositories choose their commands, while the shared platform owns orchestration, gates, identity, resource safety and deployment.

## Goals

1. **Stage-driven CI**: callers assign any inexpensive tasks (lint, check, build, UT, fast IT, etc.) to Verify and costly checks (heavy IT, local E2E, contract tests, etc.) to Test. No hard-coded UT/IT event ownership.
2. **Parallel by default**: independent checks within a stage fan out, with one fail-closed aggregate gate. Avoid needless sequential dependencies and excessive jobs.
3. **Clear event policy**: feature push gives fast feedback; PR validates the proposed merge and expensive tests; main push is guarded before production; protected manual dispatch is explicit. Do not implement a cross-workflow-run GitHub Checks simulator.
4. **One delivery implementation**: reduce `production / native-branch / native-dev / persistent-dev` to **targets** (`production / preview / dev`) with two **drivers** (Worker publish and Native Preview). A single eligible event SHALL NOT automatically publish two nonproduction targets for one app.
5. **Safe Dev ownership**: PR preview for projects that need a review URL, and protected explicit promotion to fixed Dev for apps requiring a stable environment; no arbitrary feature push overwriting shared Dev.
6. **Lean pipeline self-tests**: prefer actionlint, shellcheck, deterministic unit/safety tests, and a few real GitHub-runner canaries to maintaining a home-grown Actions expression/scheduling emulator.
7. **One-time cutover**: update all consumers against one frozen shared revision, promote the `@v2` tag only after evidence, and remove superseded inputs/paths/tests without shipping a permanent legacy compatibility mode.

## Scope and ownership

| Repository | Proposed target | Non-negotiable constraints |
| --- | --- | --- |
| `firstsun-dev/.github` | Own shared CI stages, target planner, drivers, gates, safety and tests | Public workflow contract and coordinated `@v2` cutover |
| `firstsun-dev/blog` | PR Native Preview; no automatically updated fixed Dev | Keep real URL, SEO build checks, content-only detection, E2E, D1/R2 preview isolation |
| `firstsun-dev/heaven-monorepo` | Fixed Dev Native Previews for ID / Checkin / Test Client, promoted as a compatible cohort | Cross-service OIDC, exact issuer/redirect origins, migrations, post-deploy login E2E |
| `firstsun-dev/innovation-apps` | Diet Manager: fixed Dev Native Preview; Notification Hub: fixed Dev Worker | Diet Manager shared dev D1/R2 and external secrets; Notification Hub Queue/DLQ, dry-run and separate dev D1 |
| `firstsun-dev/heaven-www` | Fixed Dev Native Preview by explicit promotion | Static assets, SEO smoke, Access-protected verification |
| `firstsun-dev/anas-mcp` | Production only | Public fork PR isolation; no untrusted code on privileged/self-hosted runner |
| `firstsun-dev/infra-config` | Provision or retire environment resources only where necessary | Terraform plan, explicit approval before apply, DNS/Access/Secrets ownership |
| `firstsun-dev/cf-v2-scratch-caller` | Real-runner contract canary | Never touch Production resources or cause external side effects |

This is **one organization-wide OpenSpec change**. The implementation will be spread across separate repository PRs but released through one coordinated cutover.

## Proposed behavior

| Event | Verify | Test | Delivery |
| --- | --- | --- | --- |
| Trusted feature-branch push | Yes | No | None by default |
| Same-repository PR open/update | Yes, against PR test-merge SHA | Yes, in parallel with independent Verify tasks | Optional single PR Native Preview after a safe gate |
| Fork PR | Credential-free safe validation only | Only where the configured runner and permissions are safe | Never |
| `merge_group` (where enabled) | Yes | Yes | Never |
| `main` push | Yes; require evidence of pre-merge tests or equivalent policy | Only if needed to safely cover direct main pushes | Production after its own gate |
| Approved `workflow_dispatch` promote | Yes for exact approved revision | As dictated by promotion policy | One fixed Dev target with audit and smoke |
| PR close / branch delete | No | No | Lifecycle bookkeeping; destructive GC only under approved retention policy |

PR Verify intentionally repeats some low-cost Push checks to validate the GitHub-generated merge result. No required check may depend solely on a Push run associated with a different SHA. A failed or unexpectedly skipped configured check MUST NOT produce a green gate.

## Deployment policy

- Logical targets: `production`, `preview`, `dev`. Drivers: `worker`, `native-preview`; this is a single engine, not two policy implementations.
- `preview` is a per-PR Native Preview for eligible trusted same-repo PRs; immutable SHA and canonical published URL are recorded. A Preview is not automatically a separate D1/R2 database.
- `dev` is one protected, stable endpoint per app. The driver is Native Preview for ordinary HTTP apps and a real `env.dev` Worker where Queue/Cron/other triggers require it.
- The standard fixed-Dev flow is **explicit promotion from an approved ref/SHA**, not last-writer-wins deployment from every feature push. No production/development environment is silently repointed.
- Production D1/R2, secrets and privileged bindings MUST be denied to Preview/Dev. DB migrations run only after audited target resolution and compatibility checks; Worker code rollback never rolls back schema.
- Cloudflare Access redirects count as liveness, **not content verification**; unverified deployments cannot satisfy release or known-good gates.
- Preview names/URLs follow what Cloudflare actually publishes. Friendly `blog-pr-*` aliases require separate verified DNS/TLS/routing evidence; no fabricated URLs.
- Candidate retention: 14 days after PR close for eligible PR previews, subject to owner approval, active ownership checks, non-destructive dry run and safe GC. Branch deletion alone remains non-destructive until the policy is approved.

## Compatibility and explicit exclusions

This is a **coordinated v2 breaking change**, allowed by `openspec/project.md` only with an explicit coordinated consumer migration. Do **not** add `legacy` / `tiered` selectors, duplicate v2 entrypoints, old input aliases, or permanent compatibility shims. Inventory other v1/v1.5 callers before deleting any unrelated published entrypoint. Preserve necessary behavior, not legacy implementation.

Out of scope for the OpenSpec-only PR: modifying executable workflows, moving `@v2`, applying Terraform, altering Cloudflare resources, updating Required Checks, deleting old workers/domains/data, or merging consumer PRs.

## Decision points for review

1. **Approve PR-first Blog Preview** vs. the current branch-push URL contract. If reviewers require a URL before opening a PR, specify that exception before implementation; do not silently preserve two automatic channels.
2. **Approve protected manual Dev promotion** for ID/Checkin/Diet Manager/WWW/Notification Hub; document who may promote and whether main-only or reviewed feature SHAs are accepted.
3. **Confirm actual P95** for UT/IT/E2E in each app; assign tasks to Verify/Test from measured cost rather than names.
4. **Confirm D1 policy for PR previews**: for shared Preview D1, remote schema changes MUST NOT be automatically applied from arbitrary PRs; local/sandbox schema validation is the default.
5. **Approve cleanup retention** and any desired custom Preview hostname only after DNS/TLS/provider checks.

## Release gates

- **G0 — Design acceptance:** this proposal/design/tasks/delta specification reviewed; no runtime changes.
- **G1 — Engine freeze:** shared implementation passes static, unit, safety and real-runner canary checks; publish an immutable candidate SHA and stable new input/output contract.
- **G2 — Consumer readiness:** every supported app validates against **the same candidate SHA**; PR required-check mappings, runner permissions, affected-app detection and target-specific migration/smoke are proven.
- **G3 — Coordinated cutover:** under a documented merge freeze/window, update callers, verify actual runs, then move `@v2`. GitHub merges are not atomic; do not move floating tag before caller readiness. No long-lived legacy mode.
- **G4 — Cleanup:** delete superseded source/tests/docs and only then, with separate approval, retire obsolete Cloudflare resources after dependency and data-retention review.

## Success criteria

A consumer defines stage commands without implementing CI orchestration; adding a fast UT needs no shared-workflow source change. Push/PR checks are correct and do not produce false-green merge gates. Independent stage tasks run concurrently and configured failures fail closed. Each delivery attempt targets only one logical environment/driver, and production data, rollback, secrets and inter-app readiness remain protected. The shared contract and its tests become measurably smaller/simpler, with no unused legacy selection branches.

