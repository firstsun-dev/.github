# Proposal: Independent Shared Dev channel for Cloudflare Worker CI/CD v2

Tracking: #43 (parent), focused issue linked from the Draft PR. Consumers: `firstsun-dev/blog#448`, `heaven-monorepo`, `innovation-apps`.

## Why

v2 deploys a stable Native Preview per branch, but some Workers also need one **stable, shared, persistent Dev** (fixed custom domain, fixed OAuth callbacks, shared Dev D1/R2, Cron/Queue-adjacent resources) that every non-main branch can update. Today that is only available through the manual `persistent-dev` dispatch. Feature Preview and Shared Dev must be separately switchable; neither may require the other.

## What changes

- New input `shared_dev_on_branch_push` (default `false`) next to the preserved `preview_on_branch_push`; four combinations (both, feature only, Dev only, validation only). Main is always Production.
- New optional job `dev-deploy` after the six validation jobs: Dev build, effective-config audit, Dev D1 migration, publish via the existing `dev_deploy_cmd`, smoke of `app_url_dev`, ledger-based recovery. Reuses `migrate.sh`, `smoke.sh`, `guard.sh`, `gh.sh`, `parse-env.mjs`.
- New inputs `app_url_dev`, `build_env_dev`, `dev_build_cmd`, `dev_wrangler_env`, `dev_worker_name`; existing `dev_deploy_cmd`, `dev_smoke_cmd`, `CLOUDFLARE_DEV_*` reused.
- Concurrency: all branches of a Shared Dev app queue on one workflow-level group; the job also shares the `persistent-dev` group.
- `config-guard` rejects `production-only` + Shared Dev.

## Non-goals

No v3, no v1 changes, no Worker deletion, no Production route/OAuth/D1 changes, no per-branch Dev environments, no live pilot in this change, no move of the floating `@v2` tag.
