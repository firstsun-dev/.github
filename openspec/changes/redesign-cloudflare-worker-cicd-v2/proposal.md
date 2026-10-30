# Proposal: Cloudflare Worker CI/CD v2 — branch-push Native Previews and Hybrid Delivery

> **Lifecycle status (2026-10-21): partially delivered / maintenance-only.** Shipped v2 Native Preview and release behavior is retained for existing consumers; this proposal is a **historical v2 contract**, not the active design for further CI/CD refactoring. Unfinished v2 implementation, consumer rollout and validation work is assigned to [vNext OpenSpec (PR #57)](https://github.com/firstsun-dev/.github/pull/57), with an explicit [task handoff](./tasks.md). See [v2 documentation](../../../docs/cf-worker-v2.md) for currently released behavior. No v1/v2 consumer is automatically migrated.


## Why

The published `_cf-worker-template.yml` v1/v1.5.0 uploads a version and then deploys **every non-main branch push** to a shared `dev` Worker. Blog's opt-in Aliased Version Preview URL is branch-specific, but data bindings still point at that shared Dev environment. Multiple branches can overwrite the shared Dev endpoint, share D1 data, and mutate shared resources. Native Worker Previews (Wrangler >= 4.135.0) offer a different, branch-aware isolation contract.

**Approved decision (2026-10-20): every eligible non-`main` branch push SHALL create or update that branch's stable, independent Native Preview URL. A pull request is NOT required.** The next successful push to the same branch updates its existing URL rather than creating a new one. A pre-deployment failure SHALL leave the previous Preview unchanged. If post-deployment smoke fails after Wrangler has already changed the stable URL, report failure and run a tested, explicit recovery procedure; do NOT assume Native Preview has a built-in rollback command. This applies to preview-enabled Workers; production-only security exceptions remain explicit.

## What changes

- Introduce opt-in `production-preview`, `hybrid`, and `production-only` strategies.
- Route eligible trusted `push` events on **any non-main branch** to branch-scoped Native Previews, not to persistent/shared Dev. Preserve build/test gates.
- Build a deterministic Preview identity equal to the **sanitized branch-name slug** (owner decision: simple `feat-xxx-<worker>` URLs, no prefix/hash/SHA). Uniqueness is enforced by the GitHub Deployment ledger (name → owning full branch ref, fail closed); no PR-number dependency.
- Publish the real URL reported by Wrangler in workflow output / GitHub Deployment / step summary even without an open PR. If a matching PR exists, surface the same URL there idempotently.
- Remove a branch's Preview only through an explicitly approved expiry/cleanup policy (branch deletion alone keeps it). Merging or closing a PR alone SHALL NOT delete a still-existing branch Preview.
- Audit per-Worker Preview configuration, bindings, secrets, authentication, outbound side effects, resources, migrations and retention. Protect persistent Dev (Hybrid) independently.
- Keep `@v1` and pinned `@v1.5.0` behavior unchanged until each caller opts in to a separately released v2.

## Approved decisions and platform facts (2026-10-20)

- Previews use the repository's existing Cloudflare deploy credentials (`CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID`, same as Production and Dev); there is no separate Preview token or account ID and no fallback — owner decision.
- No per-branch D1/R2 for Blog (see Data and security policy).
- Facts proven in the `heaven-www` pilot and `wrangler 4.151.1`: see design.md "Verified platform facts".

## Event-level behavior

| Event | production-preview / hybrid | production-only |
|---|---|---|
| Non-main same-repo branch push | Tests, resource preflight, create/update **branch** Native Preview, smoke and publish URL | Tests only |
| Main push | Production gates/deploy/verify/recovery | Same |
| PR opened/reopened | Link the already-created branch URL (comment); **no second competing Preview**, no duplicate test run required (push already ran them) | Tests only |
| Fork PR | Credential-free tests only; no deployment secrets or Preview | Same |
| Branch deleted | Keep the Preview and its URL; only retire the GitHub ledger (owner decision 2026-10-20; Cloudflare cannot remove just the stable URL) | No Preview |
| PR closed/merged while branch remains | Keep Preview | No Preview |
| Approved Hybrid Dev trigger | Separately deploy persistent Dev, run background tests | Not applicable |

A pipeline must not silently skip Preview creation due to a repository's existing branch filters or path filters when a Worker is in scope. For a **single-Worker repo**, every non-main push is eligible. For a **monorepo**, only changed/dependency-affected or explicitly selected Worker apps are eligible, with independently scoped URL/resource identities; untouched apps are not spuriously deployed. Any exceptional skip (failed build, unsafe bindings, intentional opt-out) must report its reason.

## Ownership and consumer scope

| Repository / Worker | Candidate strategy | Key constraints |
|---|---|---|
| `firstsun-dev/.github` | Shared v2 workflow/actions | Generic branch identity, event/trust gate, lifecycle, compatibility |
| `firstsun-dev/heaven-www` / `www` | production-preview **pilot** | Existing CI pushes only main; enable feature-branch push |
| `firstsun-dev/blog` / `blog` | production-preview | All branch pushes (existing path filters must not suppress); **one shared non-production preview D1 and one shared preview R2 bucket (owner decision 2026-10-20, no per-branch resources)**; Preview-only secrets, noindex, real URL smoke |
| `firstsun-dev/heaven-monorepo/apps/checkin` | production-preview | Independent app identity, D1, safe ID/auth integration |
| `firstsun-dev/heaven-monorepo/apps/test-client` | production-preview | Independent app identity, auth endpoint isolation |
| `firstsun-dev/heaven-monorepo/apps/id` | hybrid | Branch Previews plus separately protected persistent Dev for Cron; D1/R2/auth |
| `firstsun-dev/innovation-apps/apps/diet-manager` | production-preview | D1/R2/Secrets Store/third-party credentials/side effects |
| `firstsun-dev/innovation-apps/apps/notification-hub` | hybrid | Branch Previews plus separately protected persistent Dev for Queue/Cron; D1, notifications |
| `firstsun-dev/anas-mcp` | production-only exception | Do NOT expose Worker Previews or bypass Cloudflare Access without a separate approved threat model |
| `firstsun-dev/infra-config` | Provisioning support as needed | Manage durable Preview-safe baseline resources/IaC; coordinate ephemeral branch-resource lifecycle |

Other Workers discovered during implementation MUST be inventoried, classified, and proposed for a scoped migration; do not silently switch unknown consumers to v2.

## Data and security policy

Stable branch URL does **not** by itself mean isolated D1/R2. Isolation level is a per-Worker **owner decision** and must be written down in the consumer PR:

- *Shared non-production baseline* (accepted for Blog, 2026-10-20): all branch Previews bind one Preview-only D1 and one Preview-only R2 bucket; no per-branch resources are created. Known limit: branches share state, so branches with different schema revisions can disturb each other's Preview data. Migrations run only against that non-production DB.
- *Branch-scoped resources*: a distinct resource per branch (with ownership naming, quota ceiling and cleanup). Recommended when concurrent branches routinely carry incompatible migrations, but not mandated.

In every case R2/other mutable bindings must be independent of Production and bindings must carry explicit resource ids (Native Previews do not auto-provision). No Preview may point at writable Production state, trusted Production Secrets, a Production service binding with side effects, or a public authentication bypass. Do not copy Dev/Production Secrets Store values into Previews by default.

Native Previews do not automatically execute Cron Triggers or Queue Consumers. Validate those in tests or protected persistent Dev only. Do not infer that a Worker rollback reverts database migrations.

## Compatibility, scope and acceptance

This OpenSpec PR is documentation-only; no Worker/Cloudflare/CI changes. V2 releases and individual consumer migrations must be separate, independently reviewed PRs with stable URL creation, same-branch update, cross-branch isolation, branch-deletion cleanup, quota/leak checks and recovery evidence. Existing v1 consumers remain unchanged until explicitly migrated.

Tracking: `firstsun-dev/.github#43` (supersedes #33). Cloudflare references:
- https://developers.cloudflare.com/workers/previews/get-started/
- https://developers.cloudflare.com/workers/previews/configuration/
- https://developers.cloudflare.com/workers/previews/resources/
- https://developers.cloudflare.com/workers/previews/examples/
