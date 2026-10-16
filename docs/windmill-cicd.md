# Windmill CI/CD model (consumer repos)

`windmill-flows` (workspace `general`) and `infra-config` (workspace `infra`) share one
CI/CD shape. This repo only owns the **generic** primitive they both call:
`_detect-changes.yml`. Workspace-specific secrets, namespaces, and deployment policy stay
in each consumer repo on purpose.

```
PR            detect-changes -> lint / config-check / migration-test      (no mutation, no prod token)
push main     detect-changes -> [migrate-prod] -> deploy-config -> sync-flows   (conditional)
manual        workflow_dispatch target=scripts|config|all, dry-run default true
```

## `_detect-changes.yml` contract

Outputs `db_changed`, `app_changed`, `config_changed` are unchanged. Added (optional, additive):
`base_resolved`. If `base-sha` is empty / all-zero / not in the checkout, **all categories are
`true`** (fail-safe) and `base_resolved=false`. An unusable `head-sha` fails the job.
Manual deploys must not call it with an empty base; callers select categories from inputs.

## Why there is no shared deploy helper

Map validation and `wmill variable push` logic is duplicated (~100 lines) in the two consumers
as `scripts/windmill-deploy-config.py`. A shared copy would couple two workspaces' secret policy
to one `@v1` tag and would require a tag bump before either consumer could merge. Revisit if a
third consumer appears.

## Releasing the change (`@v1` does not follow `main`)

Consumers pin `@v1`, a moving tag. Merging to `main` changes nothing for them.

1. Merge the `.github` PR.
2. `./scripts/release.sh v1.5.5` (or next minor) — moves `v1`. Note `v1` currently lags `main`
   (it predates two merged changes to `_pg-migration-test.yml` / `rclone-sync.yml`); releasing
   ships those too.
3. Consumer PRs were written to work with **either** the old or new `v1`: they only pass
   a valid SHA or skip detection, so merge order is not load-bearing. The new fail-safe behaviour
   is only relied on for pushes with `before=000…0`, where the old version fails closed
   (error, no deploy) — safe.

## Tests

`./scripts/test-detect-changes.sh` runs the workflow's match step against temporary git repos.
