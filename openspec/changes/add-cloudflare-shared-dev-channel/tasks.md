# Tasks: Shared Dev channel

- [x] Shared v2 workflow: `shared_dev_on_branch_push`, `dev-deploy`, `config-guard`, concurrency
- [x] Tooling: `resolveSharedDev`, Dev migration target, `auditDevConfig`, `migrate.sh` TARGET=dev
- [x] Contract/unit/behaviour tests and docs (`docs/cf-worker-v2-refactor.md`)
- [x] Round 2: identity-aware Dev resource policy, Dev/Preview policy separation, queue/service/route checks, split manual/automatic Dev contract, recovery commit identity, executed dev-deploy/persistent-dev tests
- [ ] Review of the Draft PR (open decision: should `production-only` + `preview_on_branch_push: true` also fail?)
- [ ] Blog #448: both switches, Native Preview on `blog` (verify real `previews_enabled`), Shared Dev `blog-preview`
- [ ] heaven-monorepo per-Worker switches (Heaven ID: Dev only), innovation-apps (Notification Hub keeps Dev Queue/Cron; ANAS MCP production-only)
- [ ] Pilot Preview + Shared Dev against explicitly approved non-production resources
