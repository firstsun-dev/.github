# CF Worker v2 — unified target-driven delivery (shared-engine contract)

Spec: `openspec/changes/unify-cf-worker-v2-delivery-targets`. This document is the **frozen Phase 1 contract** consumers build against.
It supersedes the job topology in `cf-worker-v2-refactor.md` ("Job DAG", "Shared Dev job order"); the engine, security rules, ledger
and recovery rules described there still apply. `_cf-worker-v2.yml` remains the only public entrypoint; `_cf-worker-delivery.yml` is an
**internal** reusable workflow called only by it. v1 / v1.5 and the floating `@v2` tag are untouched by this change.

## Topology (final DAG)

```
Lint ‖ Typecheck ‖ Unit ‖ Integration ‖ Build Validation ‖ E2E        (unchanged, independent)
                         │
                 Validation Gate        every CONFIGURED job succeeded, every unconfigured one skipped, else fail closed
                         │
                  Delivery plan         enabled kinds of this event + smoke-mode validation + D1 plan (pure data, no credentials)
                         │                outputs per kind: on_<kind>, d1_<kind>, owns_<kind>, owner_of_<kind>
        ┌────────────────┼───────────────────┐
 preflight_production  preflight_native_branch  preflight_native_dev  preflight_persistent_dev     one CALL per enabled kind (only with d1_migrations)
        └────────────────┼───────────────────┘      non-publishing: canonical URL, target build, effective-config audit, exact D1 binding
                         │   (every migration waits for the preflight of EVERY enabled kind: nothing mutates while a target is unvalidated)
 migration_<owner kind>  one CALL per OWNER of a distinct physical D1 (Native Branch + Native Dev on one D1 -> one migration)
                         │
 delivery_production | delivery_native_branch | delivery_native_dev | delivery_persistent_dev      one CALL per enabled kind, mutually independent
      └─ deploy ──► smoke ──► (conditional) rollback          jobs of the SAME internal workflow, scoped to that one target
                         │
                  Release (Production, main only; reads ONLY delivery_production)
```

Kinds: `production`, `native-branch` (per-branch Native Preview), `native-dev` (the stable Native Preview named `dev`),
`persistent-dev` (the separate `[env.dev]` Dev Worker; Notification Hub). Only enabled kinds exist as calls (a disabled call is skipped by the
plan flag `on_<kind>`), so no phantom delivery paths exist. **A delivery call waits only for the Validation Gate, the plan, and the
migration call(s) of ITS OWN D1; it never waits for, and is never affected by, another kind's Deploy / Smoke / Rollback.** There is no
matrix anywhere in the delivery graph: every `needs.<call>.outputs.*` belongs to exactly one target.

| Event | Kinds |
| --- | --- |
| trusted push to `main` | `production` |
| trusted push to another branch | `native-branch` if `preview_on_branch_push`; `native-dev` if `shared_dev_native_preview_on_branch_push`; `persistent-dev` if `shared_dev_on_branch_push` |
| protected `workflow_dispatch` persistent-dev | the existing manual `persistent-dev` job (unchanged) |
| PR / delete / tag | none (lifecycle handler only) |

Rejected (plan fails closed, `config-guard` also reports): `production-only` with any Dev channel; `shared_dev_on_branch_push` together with
`shared_dev_native_preview_on_branch_push`; unknown `smoke_mode` / `smoke_mode_overrides`; an unknown kind in a `phase_state` hand-off;
a Preview/Dev kind whose D1 is the Production D1.

### Reusable-workflow limits honoured

| Constraint | How |
| --- | --- |
| Nesting depth | caller → `_cf-worker-v2.yml` → `_cf-worker-delivery.yml` = 3 levels (limit 10); the delivery workflow calls nothing further |
| Permission ceiling | each `uses:` job declares the permissions the called jobs need (`contents: read`; `deployments: read` for Preflight / Migration, `deployments: write` + `pull-requests: write` for Delivery); the caller of the public workflow already had to grant these |
| Static `uses:` | `./.github/workflows/_cf-worker-delivery.yml` per call (no expression); actions are fetched by `tools_ref` as before |
| `tools_ref` | forwarded unchanged to every call; the engine always comes from the same pinned ref |
| Secrets | passed explicitly per call (smoke credentials only to Delivery); never `secrets: inherit` |
| Environments | job property inside the called workflow (`<app>/prod`, `/preview`, `/native-dev`, `/dev`); Smoke and Preflight have none |
| Concurrency | the run-level group stays on the public workflow; the delivery workflow has **only job-level** groups (`cfv2-leg-<run>-<kind>[-rollback]`, persistent Dev keeps `cfv2-dev-<repo>-<scope>`) so nothing deadlocks |

## Inputs

All pre-existing inputs are unchanged (names, types, defaults). New, all optional and default-off:

| Input | Default | Meaning |
| --- | --- | --- |
| `shared_dev_native_preview_on_branch_push` | `false` | Native Dev: every eligible branch push updates Native Preview `dev` (host = `preview_worker_name` or `app_name`, `[previews]` bindings, **not** `[env.dev]`). |
| `build_env_shared_dev_native_preview` | `""` | Build-time `KEY=VALUE` env of the Native Dev build. Never falls back to another kind's env. |
| `app_url_shared_dev_native_preview` | `""` | Optional declared Native Dev URL. Must equal the URL derived from Cloudflare (below) or the run fails **before** the build. |
| `preview_url_suffix` | `""` | Optional host suffix of Preview URLs (`-<worker>.<subdomain>.workers.dev` or `.<custom domain>`). Normally empty: Cloudflare reports it. Required for custom-domain Preview hosting (`preview_host_check: false`). |
| `smoke_mode` | `full` | `full` = built-in probe + `smoke_cmd` (current behaviour); `basic` = content-level HTTP/health only (no `smoke_cmd`); `skip` = no request. |
| `smoke_mode_overrides` | `{}` | JSON keyed by kind, e.g. `{"native-dev":"basic"}`. |

Engine-owned build variables (reserved, cannot be set from caller env): `CFV2_DELIVERY_KIND`, `CFV2_TARGET_URL`; `VITE_APP_ENV` is
`prod` / `preview` / `dev` (`dev` for **both** `native-dev` and `persistent-dev`).

## Outputs (public contract)

Pre-existing outputs keep their meaning: `deploy_*`, `version_id`, `previous_version_id`, `migration_status`, `rollback_result`
describe the Production / per-branch Preview delivery; `dev_*` describe `persistent-dev`; `deploy_gate_blocked`; `phase_state`.
Added: `native_dev_url`, `native_dev_verified_url`, `native_dev_published_unverified`, `native_dev_verified`, `native_dev_smoke_level`.
`deploy_smoke_level` / `native_dev_smoke_level` may now be `skipped`. `phase_state` is opaque: pass it unchanged to the verify call.

**Output determinism.** Every per-kind output SELECTS its source call from the plan (`jobs.plan.outputs.on_<kind> == 'true'`, or `owns_<kind>`
for `migration_status`) and then reads that call's own result — never "whichever string is non-empty". `deploy_*` read Production, else
Native Branch (mutually exclusive by event: `main` vs. other branches); `dev_*` read `persistent-dev`; `native_dev_*` read `native-dev`;
`rollback_result` is the first rolled-back kind in the order production, native-branch, native-dev, persistent-dev.

## Behaviour

* **Per-target builds and URL pre-resolution (P1-B).** Every Deploy / Preflight checks out the validated SHA, imports its own build env, resolves
  **its canonical URL before building**, builds, audits its generated Wrangler config and publishes that artifact.
  * Native Preview URL (Cloudflare Workers "Previews" documentation): `workers.dev` → `<preview-name>-<worker-name>.<subdomain>.workers.dev`;
    custom domain → `<preview-name>.<domain>`. The Workers subdomain API (`GET /accounts/{id}/workers/scripts/{worker}/subdomain`, the same
    read-only call as `preview-host-check`) reports `previews_enabled` and `preview_url_suffix`; the URL is `https://<preview-name><suffix>`.
    The identity is the real one: `<branch slug>` (Native Branch, via the collision-guarded identity) or the reserved name `dev`.
  * Nothing is guessed: if neither Cloudflare nor `preview_url_suffix` provides a suffix and no URL is declared the run fails before the build;
    a declared URL (`app_url_shared_dev_native_preview`) that contradicts the derived one fails before the build; two suffix sources must agree.
  * The URL is exported to the build as `CFV2_TARGET_URL`; the published URL must equal it (also in recovery rebuilds), else the publish is rejected.
  * Build-env checks before the build: another target's public URL is rejected; this application's own callback / redirect / app-origin / site
    variables (`*CALLBACK*`, `*REDIRECT_URI/URL*`, `*APP_ORIGIN*`, `*APP_URL*`, `*SITE_URL*`) must be **same-origin with the target URL**.
    An IdP issuer / `IDP_URL` names another service and is not verifiable, so it is only checked against this app's other targets.
* **Preflight → Migration (P2-B).** The plan groups kinds by `account_id/database_id` of the source config. Each Preflight (one per enabled kind) builds
  non-publishingly, audits the *effective* (adapter-generated) config and requires that the built config binds **exactly** the plan's D1 id.
  Every Migration call waits for the Preflight of **all** enabled kinds, so no schema changes while a target sharing or using a D1 is unvalidated.
  Branch + Native Dev on one preview D1 are migrated **once** (owner: Native Branch); distinct D1s migrate independently; D1-less targets skip
  the stage. A Preview/Dev kind mapped to the Production D1 fails the plan. The publish stage re-audits the built config against the migrated D1 id,
  and the Delivery call validates that the migration result it received has the planned owner kind and D1 id. A failed migration blocks only the
  deliveries on that D1. Stale-SHA checks run before migration and again before publish; D1 is never rolled back. Same-repository serialization is
  the workflow-level concurrency group (`cfv2-nonprod-d1-<scope>` / `cfv2-shared-dev-<app>` also cover Native Dev); a D1 shared with **another
  repository** is not locked (GitHub limitation, see limits).
* **Result routing (P1-A).** Each Delivery call receives only its own hand-off and its own migration result (its own call, or the shared-D1
  owner's — validated by kind and D1 id inside the call). The Smoke and Rollback jobs refuse a hand-off whose `kind`, `published_sha` or URL belong
  to anything else (fail closed on missing / ambiguous / stale / mismatched data).
* **Native Dev.** Fixed name `dev`, own ledger environment `<app>/native-dev/dev`, own GitHub Environment `<app>/native-dev`, never
  advertised on PRs. The name `dev` is reserved: with Native Dev on, a branch named `dev` gets Preview `b-dev`; Preview GC never collects `dev`.
* **Smoke / recovery.** `skip`: no request, `deployed_and_verified=false`, `verified_url` empty, `smoke_level=skipped`, ledger `inactive`
  (never known-good), no rollback, no Release. `basic`: Access-redirect = liveness only = Published/Unverified. Recovery is target-owned and runs
  only in the failing kind's call: `native-dev` / `persistent-dev` redeploy the last known-good deployment of the same ledger unless a newer one
  exists; Production keeps its fail-closed policy; a blocked cross-service gate never rolls back.
* **Release** requires the Production Smoke to be content-verified and no Rollback to have run; it reads only `delivery_production`.
* **`pipeline_phase`.** `deploy` runs Preflight, Migration and each call's Deploy only; the `phase_state` output carries each kind's own Deploy
  result, its migration result and D1 id, the kinds, the inputs and the concurrency group. `verify` (second call) runs only each kind's Smoke and
  Rollback from that data (`handoff_json`, `migration_json_in`, `d1_expected_id` are that kind's own entries) and `upstream_gate`; Heaven's
  ID ↔ Test Client ↔ Check-in dependencies are expressed by the caller's `needs` between the deploy and verify calls, unchanged.

## Verification evidence

Local (mocked Cloudflare/GitHub) — suites: `test-workflow-contract.py` (job graph, no matrix / no cross-kind `needs`, gating, credential isolation,
permission ceiling, concurrency, interface compatibility, `pipeline_phase`), `test-jobs.py` (executes the real jobs and composite engine per call
against stubs: all four kinds, simultaneous Branch + Native Dev, reversed completion order, one-target failure isolation, shared-D1 dedupe, URL
pre-resolution, smoke full/basic/skip, rollback isolation and stale-deployment protection, Release gate, deferred `pipeline_phase`),
`delivery.test.mjs`, `lib.test.mjs`, `test-*.sh`, `gc.test.mjs`, shellcheck, actionlint 1.7.12. Local gate at `e72ae91`+: contract 623, test-jobs 379, node (lib+delivery+gc) 114, test-deploy 84, migrate 26, smoke 49, parse-env 27, probe 20, gh 24 — all
passing; actionlint 1.7.12 and shellcheck clean.

**Real-runner evidence: NOT yet recorded.** The disposable private caller repo `firstsun-dev/cf-v2-scratch-caller` (pinned-SHA call of the public workflow,
`runner: ubuntu-latest`, no Cloudflare secrets, plus a read-back job over every public output) could not be created: the repository-creation command was
denied by the session's permission classifier twice. Until it runs, the following remain unproven on GitHub itself: `uses: ./.github/workflows/_cf-worker-delivery.yml`
resolution from a cross-repository call, the permission ceiling, the rendered `phase_state` / output expressions, and that a hosted runner provides `pnpm`
for `actions/setup` with `setup-pnpm: false` (it is baked into the org's `linux` runners). G1 stays BLOCKED on this item.

## Known limitations

* Locks are repository-scoped; a D1 shared across repositories must be serialized by the owner (documented on `d1_lock_scope`).
* The compiled-asset scan ("an artifact embeds another target's callback/issuer/origin") is **not** implemented; the build-env checks above and the
  post-publish URL assertion are. Consumers must keep `PUBLIC_*` / `VITE_*` / `IDP_URL` in `build_env_*`.
* `wrangler preview --name dev` custom-domain attachment is unverified and not assumed; custom-domain Preview hosting needs `preview_url_suffix`.
* Preview URL derivation depends on the Workers subdomain API reporting `preview_url_suffix`. If the API is unreadable and no suffix / declared URL
  is supplied, Native Branch / Native Dev fail before the build (by design) rather than build for a guessed URL.
* Migration calls of different D1s are not ordered against each other's *Deploy*, but Native Dev / persistent Dev deliveries statically `need` the
  Native Branch migration call (to read the shared-D1 result); that is a wait for a migration call, never for a Branch Deploy / Smoke / Rollback.
