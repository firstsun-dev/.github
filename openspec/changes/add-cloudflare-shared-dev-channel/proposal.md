# Proposal: Independent Shared Dev channel for Cloudflare Worker CI/CD v2 on ONE delivery engine

Tracking: #43 (parent), #63 (this change), Draft PR #64. Consumers: `firstsun-dev/blog#448`, `heaven-monorepo`, `innovation-apps`.

## Why

v2 deploys a stable Native Preview per branch, but some Workers also need one **stable, shared, persistent Dev** (fixed custom domain, fixed OAuth callbacks, shared Dev D1/R2, Cron/Queue-adjacent resources) that every non-main branch can update. Feature Preview and Shared Dev must be separately switchable; neither may require the other.

The first implementation added Shared Dev by duplicating the Preview/Production deployment logic inside `_cf-worker-v2.yml`, which grew to ~1,700 lines and produced review findings that all stem from the duplication (a Dev migration target that could differ from the deployed D1, a `dev_deploy_cmd` that could rebuild the audited artifact, application env able to overwrite deployment controls). This change therefore also **refactors the architecture**.

## What changes

- **Thin workflow, one engine.** `_cf-worker-v2.yml` keeps events, gating, Environments, credentials, permissions, concurrency and Release (≈800 lines incl. the ~300-line public interface). All delivery logic is the composite action `actions/cf-deploy` (phases of `actions/cf-preview/engine.sh`, `build.sh`, `publish.sh`, `migrate.sh`, `smoke.sh`, `guard.sh`, `gh.sh`, `cli.mjs`). Production, Native Preview and Shared Dev are the same pipeline with `target = prod | preview | dev`; manual persistent Dev is the same engine in `mode: manual`. Validation jobs share `actions/cf-ci-check`.
- **Migration is a step, not a job.** Order: validate CI → build the deployable → audit the effective Wrangler config → validate the exact migration target (and that the deployed D1 is that D1) → stale check → apply pending D1 migrations → stale check → publish the audited artifact → smoke → mark verified / recover. No Prepare, Plan, Test-Gate or Migration job.
- New input `shared_dev_on_branch_push` (default `false`) next to the preserved `preview_on_branch_push`; four combinations. New inputs `app_url_dev`, `build_env_dev`, `dev_build_cmd`, `dev_wrangler_env`, `dev_worker_name`, `dev_binding_allow`, `dev_deny_resources`; `dev_deploy_cmd` becomes an optional publish-only command (default `wrangler deploy --config <audited config>`).
- Review fixes: protected deployment-control env names, complete Dev resource isolation, deployed-D1 == migrated-D1, publish of exactly the audited artifact, shared-D1 concurrency identity, stale re-check after the migration.
- `config-guard` rejects `production-only` + Shared Dev.

## Non-goals

No v3, no v1 changes, no Worker deletion, no Production route/OAuth/D1 changes, no per-branch Dev environments, no live pilot in this change, no move of the floating `@v2` tag, no consumer changes.
