# CF Worker v2 — `v2` tag promotion: preconditions, procedure, rollback

Status at the time of writing (2026-11-03): **PR #89 is ready to merge; the floating `v2` tag is NOT ready to be promoted.** This document does not move any tag. Promotion needs a separate, explicit authorization.

| Item | Value |
| --- | --- |
| Current `v2` tag (lightweight, commit) | `def36792bf2fa79bb171abc7101f63320680e06b` |
| `v1` tag (still used by `actions/setup@v1`) | `cf678d19…` — untouched by PR #89 |
| Candidate (PR #89 head, implementation unchanged since `8ad4d40`) | `f86062d6110723d8b58622a9dfbdf05361bfa2f9` — a fast-forward of the `v2` tag (`ahead 36, behind 0`) |
| Final merge SHA | **to be recorded after merge** (a squash/merge commit gets a new SHA; promote that SHA, not the PR head) |

## Compatibility facts (verified against `f86062d`)

* Interface v2 tag → candidate: no input or output removed; 6 inputs and 5 outputs added; `tools_ref` becomes optional and ignored; the 6 declared secrets are unchanged (`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `PREVIEW_SECRETS`, `SMOKE_USERNAME`, `SMOKE_PASSWORD`, `RELEASE_TOKEN`).
* Every `with:` key, secret and read output of all eight consumer calls (blog, heaven-www, heaven-id, heaven-checkin, heaven-id-test-client, diet-manager, notification-hub, anas-mcp) exists in the candidate; caller permissions cover the maximum the called jobs request. The only defect found was blog / heaven-www passing `CLOUDFLARE_PREVIEW_*` (below).
* Other shared references used by consumers (`_detect-changes.yml@v2`, `actions/setup@v1`, `_zap-scan-template.yml@v1`, `_lighthouse-template.yml@v2`, `_speedinsights-template.yml@v2`) are not changed by PR #89. `_cf-worker-preview-gc.yml` is changed but keeps its inputs, outputs and secrets.
* The real planner never plans two distinct physical D1s for any consumer call × (push main, push feature, dispatch main default / persistent-dev, dispatch feature, pull_request, delete).
* Runners: every online org runner reports `2.338.0` (floor `2.336.0`). On 2026-11-03 nine of the ten `heavenweb-runner-*` were listed online; `heavenweb-runner-10` was absent/offline (version unknown).

## Preconditions (all required before moving the tag)

1. **PR #89 reviewed and merged.** Record the merge commit SHA (`M`).
2. **Consumer compatibility PRs reviewed and merged** — [blog#461](https://github.com/firstsun-dev/blog/pull/461) (`cicd.yml`, `preview-gc.yml`) and [heaven-www#56](https://github.com/firstsun-dev/heaven-www/pull/56) (`cicd.yml`). They are valid against the current tag *and* the candidate, so merge them first; they are the only fix for the `startup_failure` that GitHub raises (`Invalid secret, CLOUDFLARE_PREVIEW_API_TOKEN is not defined in the referenced workflow.`).
   * **Merging runs the real pipelines.** A merge to `main` of either repository runs the Production pipeline through the *current* `v2` tag (deploy, and for blog the D1 migration on the Production D1). heaven-www's `CI/CD` has ended in `startup_failure` since 2026-10-30, and blog's `cicd.yml` has not run since 2026-10-30 (its last runs succeeded), so a merge can publish everything that has accumulated on `main` since then. Merge deliberately, when a Production deploy is acceptable, and watch the run.
   * The shared `CLOUDFLARE_API_TOKEN` must be allowed to manage Previews (v2 no longer has dedicated Preview credentials).
3. **No `startup_failure` from workflow-call contracts.** Evidence at `f86062d`: scratch replicas of the three fixed callers, built from the PR branches, started their jobs (1 job for Preview GC, 17–19 for the Pipeline calls) against both the v2 tag and the candidate and failed closed only on missing credentials — see "Validation evidence".
4. **Required Checks audited, or the limitation explicitly accepted.** Branch protection/rulesets of blog, heaven-www, heaven-monorepo and innovation-apps cannot be read (Free plan, `403 Upgrade to GitHub Pro`); anas-mcp has none (verified). An administrator must inspect the contexts below. **Recommendation: require only `<job> / CI / Validation Gate`.** Delivery jobs are skipped as whole jobs on PRs, so a required `<job> / <kind> / Deploy` would wait forever for a status that is never reported. innovation-apps has two workflows that both name their job `Pipeline`, so a requirement on that name is ambiguous.
5. **Runner compatibility** re-checked immediately before promotion (`gh api orgs/firstsun-dev/actions/runners --paginate`), all online runners ≥ `2.336.0`; decide what to do about `heavenweb-runner-10`.
6. **Single-D1 assumptions** re-run if a consumer's `wrangler` config or switches changed (the planner harness used for this audit is described in `docs/cf-worker-v2-refactor.md`).
7. **Non-production deployment validation** completed, or explicitly accepted as a release risk. Today it is **not done**: no approved disposable Cloudflare account exists (only Production-scoped org secrets), so live migrate / publish / smoke / recovery and Environment approval are unproven (see "Not demonstrated").
8. Scratch-caller validation repeated on `M` (below).

### Required-check contexts to inspect

`<job>` is the caller's job display name: `Pipeline` (blog, heaven-www, innovation-apps; blog and heaven-www are being renamed to `CD`), `Heaven ID` / `Heaven Checkin` / `Heaven ID Test Client` (heaven-monorepo), `cf` (anas-mcp).

| Old | New |
| --- | --- |
| `<job> / Lint`, `Typecheck`, `Unit Tests`, `Integration Tests`, `Build Validation`, `E2E[ (variant)]` | `<job> / CI / Lint`, `Typecheck`, `Unit Tests`, `Integration Tests`, `Build Validation`, `E2E[ (variant)]` |
| — | `<job> / CI / Validation Gate`, `<job> / Plan` |
| `<job> / Migration` | `<job> / Migrate` |
| `<job> / Deploy`, `Smoke Tests`, `Revert on Verify Failure`, `Shared Dev`, `Shared Dev Smoke Tests`, `Shared Dev Revert on Verify Failure` | `<job> / <kind> / Deploy\|Smoke Tests\|Revert on Verify Failure` (`<kind>` = `production\|native-branch\|native-dev\|persistent-dev`) |
| `Release` | unchanged |
| `Config guard`, `Preview lifecycle`, `Persistent Dev (Hybrid)` | `Guard`, `lifecycle / ledger`, `persistent-dev / manual` |

## Procedure

```text
PR #89 merged  ──►  record M
        │
        ▼
consumer PRs (blog#461, heaven-www#56) merged  ──►  confirm the resulting runs start (no startup_failure)
        │
        ▼
scratch caller on M (parallel CI, gate, one Migration, disabled, distinct-D1, stale gate, deferred, persistent Dev, PR / delete)
        │
        ▼
authorized non-production deployment (separate approval)
        │
        ▼
Go / No-Go
        │
        ▼
move v2 ──►  monitor
```

1. Merge PR #89 (human decision); `M=$(git rev-parse origin/main)`.
2. Merge the consumer PRs one at a time; confirm each resulting run starts (jobs exist) and that Production deploys as intended.
3. Re-run the scratch scenarios pinned to `M` (`uses: …/_cf-worker-v2.yml@M`) and compare with the evidence below.
4. Run the authorized non-production exercise when an approved environment exists.
5. Go/No-Go, then move the tag (explicitly authorized only):

   ```bash
   # record the old value first
   OLD=$(gh api repos/firstsun-dev/.github/git/ref/tags/v2 --jq .object.sha)   # expected def36792bf2fa79bb171abc7101f63320680e06b
   gh api -X PATCH repos/firstsun-dev/.github/git/refs/tags/v2 -f sha="$M" -F force=true
   ```

6. Monitor the first run of every consumer pipeline: blog, heaven-www, heaven-monorepo (3 apps), innovation-apps (2 apps), anas-mcp, plus the nightly blog Preview GC. Watch for `startup_failure`, the Migration Gate, Environment approvals, Smoke and Release.

## Rollback

* **Restore the tag** (the action that restores workflow behaviour for every consumer at once):

  ```bash
  gh api -X PATCH repos/firstsun-dev/.github/git/refs/tags/v2 -f sha=def36792bf2fa79bb171abc7101f63320680e06b -F force=true
  ```

  The consumer-side fixes (no `CLOUDFLARE_PREVIEW_*`) are valid against both refs, so they do not need reverting.
* **Pin one consumer** to a known-good immutable SHA instead (per-repository, no tag change): change `uses: firstsun-dev/.github/.github/workflows/_cf-worker-v2.yml@v2` to `@<40-character SHA>`. `tools_ref` is deprecated and ignored, so it needs no change. Pin to `def3679…` (old behaviour) or to `M` (new behaviour).
* **Not reverted by restoring the tag:** Workers and Preview deployments that were already published (restore with the pipeline's own Production rollback, or redeploy a known-good commit; Production rollback is `manual` or `automatic` per caller via `production_rollback_policy`); **D1 migrations** that were applied (there is no automatic schema rollback; additive-only is assumed by the `automatic` policy, which blocks otherwise); Cloudflare Previews created; GitHub Deployment ledger entries and Environments; semantic-release tags / releases; Smoke / Release side effects. A tag restore only changes what the *next* run executes. A run already in flight keeps the revision it resolved.

## Validation evidence (candidate `f86062d`)

* PR CI `CF Worker v2 contract tests` green at `8ad4d40`, `fd6ab85` and `f86062d` (the implementation is byte-identical across the three). Local `test.sh fast|full` PASSED, `actionlint 1.7.12` and `shellcheck 0.11.0` clean at `8ad4d40`.
* Scratch caller (`firstsun-dev/cf-v2-scratch-caller`, **no Cloudflare credentials**, org `linux` runners, workflows pinned to `f86062d6110723d8b58622a9dfbdf05361bfa2f9`): real stale-migration gate on `linux` and `ubuntu-latest` ([41603813297](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41603813297)); r7 parallel CI [41603819671](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41603819671); r8 Validation Gate [41603825658](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41603825658); r1 single Migration [41603830812](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41603830812) / [41603836186](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41603836186); r2 Migration disabled [41603841545](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41603841545); r3 distinct D1s [41603847106](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41603847106); r4 / r5 deferred verification [41603852919](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41603852919) / [41603857824](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41603857824); r6 deprecated `tools_ref` [41603863062](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41603863062); r9 / r10 manual Persistent Dev [41603868914](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41603868914) / [41603875248](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41603875248); same-repo PR [41604617855](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41604617855), push [41604599527](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41604599527) and branch delete [41604768826](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41604768826); undeclared-secret contract probe [41603880413](https://github.com/firstsun-dev/cf-v2-scratch-caller/actions/runs/41603880413) (`startup_failure` by design).
  Observed results are identical to the earlier `8ad4d40` set: every `failure` is the designed fail-closed result (`preview|prod|dev credentials not provided`, `LINT finished 'failure'`, `… 2 distinct physical D1 databases …`).
* **Startup vs fail-closed:** replicas of the *fixed* callers built from the PR branches (`c_blog_*`, `c_blogGc_*`, `c_www_*` in the scratch repository; keys kept, shell commands neutralized) against the v2 tag and against `f86062d`: all six started jobs (Preview GC 1; Pipeline calls 17–19) and failed only on `CLOUDFLARE_ACCOUNT_ID is required` / `prod credentials not provided`. A caller that still passes `CLOUDFLARE_PREVIEW_*` ends `startup_failure` with zero jobs (r13).
* Consumer audit: no defect in heaven-monorepo, innovation-apps or anas-mcp; `dev-config-check` / `preflight` policy checks passed on their source configs.

## Not demonstrated

Live Cloudflare migration, publish, smoke, rollback; GitHub Environment approval (no consumer Environment has required reviewers or a branch policy today); the post-build effective-config audit of adapter-generated configs; the blog Preview GC dry run with real credentials; the full A → B → A race (chain regression only); GHES; runners older than 2.336.0; Required Checks of the four private repositories.
