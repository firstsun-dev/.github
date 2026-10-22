# Tasks: Shared Dev channel + single delivery engine

- [x] Shared v2 workflow: `shared_dev_on_branch_push`, `dev-deploy`, `config-guard`, concurrency
- [x] Tooling: `resolveSharedDev`, Dev migration target, `auditDevConfig`, `migrate.sh` TARGET=dev
- [x] Round 2: identity-aware Dev resource policy, Dev/Preview policy separation, queue/service/route checks, recovery commit identity
- [x] Refactor (review of #64): thin workflow + `actions/cf-deploy` engine (`engine.sh` phases `init … cleanup`), `build.sh`/`publish.sh` split of the former `deploy.sh`, `actions/cf-ci-check` for the five single-command validation jobs
- [x] Remove the standalone `migrate` job; Migration is an engine step after build + audit + D1 target check and between two stale checks
- [x] P1 `parse-env.mjs --reserved`: deployment-control names cannot be set by `build_env_*`, `smoke_env`, `PREVIEW_SECRETS`; target bound from the composite input; no `TARGET` job env
- [x] P1 Dev isolation: policy covers D1, R2, KV, Vectorize, Analytics Engine, Hyperdrive, Secrets Store, Pipelines, Rate limits, Queues (producer / consumer / DLQ), Service bindings (default deny), routes, cross-Worker Durable Objects, unsupported kinds; negative tests with Production Queue / Service / D1 / KV
- [x] P1 `d1-consistency`: deployed (effective) D1 `database_id` must equal the migration target before migration and publish
- [x] P1 same artifact: `build.sh` records config path + SHA + `artifact-digest`; `publish.sh` passes `--config` explicitly and rejects any change; `dev_deploy_cmd` is publish-only with `CF_AUDITED_CONFIG`
- [x] P2 concurrency identity: `cfv2-prod-<scope|app>`, `cfv2-nonprod-d1-<scope|app>`, Dev job group keyed by the shared-D1 scope; repository-scope limit documented
- [x] P2 stale re-check after the migration (and `inactive` ledger status for a superseded run); recovery skipped for a superseded run
- [x] Tests: contract (DAG, gating, 4 combinations, credentials, composite structure, cross-repo references, concurrency), executed job tests through the composite engine, `build.sh`/`publish.sh` behaviour, unit tests
- [ ] Review of the Draft PR (open decision: should `production-only` + `preview_on_branch_push: true` also fail?)
- [ ] Verify on a real runner that `uses: ./.cf-tools/actions/...` resolves in a cross-repository `uses: firstsun-dev/.github/...@<sha>` call (scratch repo, no Cloudflare)
- [ ] Blog #448: both switches, Native Preview on `blog` (verify real `previews_enabled`), Shared Dev `blog-preview`
- [ ] heaven-monorepo per-Worker switches (Heaven ID: Dev only), innovation-apps (Notification Hub keeps Dev Queue/Cron; ANAS MCP production-only)
- [ ] Pilot Preview + Shared Dev against explicitly approved non-production resources
