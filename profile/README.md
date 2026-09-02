# Firstsun Dev

**Engineering behind [Firstsun / 首陽問路](https://firstsun.org/en).**

> Build useful things. Operate them well. Share what we learn.

Firstsun Dev turns real needs into software, operates the systems that matter, and publishes what survives contact with reality.

[Firstsun Journal](https://firstsun.org/en) · [Tools](https://firstsun.heavenfortress.com/en/tools/) · [Service status](https://uptime.firstsun.org/status)

![Firstsun Dev — build, operate, share](./firstsun-operating-loop.svg)

## Selected work

The projects below are selected because they show different parts of the engineering loop rather than simply being the newest or largest repositories.

### Build — [Git File Sync](https://github.com/firstsun-dev/git-files-sync)

**Selective Git synchronization for Obsidian.**

Sync individual notes or batches with GitHub, GitLab, and Gitea without requiring a local Git installation. The plugin includes status review, conflict handling, `.gitignore` support, and mobile-ready workflows.

[![Obsidian plugin downloads](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fobsidianmd%2Fobsidian-releases%2Fmaster%2Fcommunity-plugin-stats.json&query=%24%5B%22git-file-sync%22%5D.downloads&label=downloads&style=flat-square&color=007acc)](https://obsidian.md/plugins?id=git-file-sync)

[Obsidian Community Plugin](https://obsidian.md/plugins?id=git-file-sync) · [Read the guide](https://firstsun.heavenfortress.com/en/blog/git-file-sync-obsidian-github-gitlab-gitea/)

### Extend — [Code Insights](https://github.com/firstsun-dev/code-insights)

**A maintained fork for turning AI coding sessions into reusable knowledge.**

Firstsun-maintained additions include Kilo Code support, multi-home analysis, personality and recurring-insight views, OpenAI-compatible analysis, reporting workflows, reliability fixes, and deployment operations while preserving upstream attribution and the local-first foundation.

[Open the app](https://code-insights.app) · [Upstream](https://github.com/melagiri/code-insights)

### Operate — Heaven Platform

**Cloudflare-native identity and application platform.**

A privately implemented, publicly operated system built around a shared OIDC identity boundary, auth client SDK, consumer applications, and centralized Playwright E2E testing. The service carries recurring real-world traffic and is continuously monitored, so availability, deployments, and regressions have real operational consequences.

[Architecture case study](https://github.com/firstsun-dev/.github/blob/main/docs/case-studies/heaven-platform.md) · [Service status](https://uptime.firstsun.org/status)

## Emerging

### [Firstsun Bot](https://github.com/firstsun-dev/firstsun-bot)

**Provider-agnostic issue-to-PR engineering automation.**

Firstsun Bot is being developed around a human-approved workflow: issue readiness → plan → approval → implementation → pull request → review and merge reconciliation. Tracker and coding-agent providers are isolated behind interfaces so orchestration is not tied to one platform or model.

It is treated as an emerging project until its public documentation, working demo, and real dogfooding evidence justify stronger placement.

## Engineering backbone

Firstsun projects share an operational backbone instead of treating deployment and reliability as an afterthought.

- **Shared delivery** — reusable GitHub Actions for test, release, and deployment live in [`.github`](https://github.com/firstsun-dev/.github)
- **Infrastructure as code** — production and self-hosted infrastructure is provisioned with Terraform and configured with Ansible
- **Verification** — unit, integration, E2E, compatibility, lint, and build checks are applied where they materially reduce risk
- **Observable services** — monitoring and public service-health visibility are part of the operating model
- **Private where appropriate** — privileged production source and infrastructure remain private while architecture and operational evidence stay public

`infra-config` is the private multi-cloud infrastructure and service-operations monorepo behind Firstsun services. It covers Cloudflare, Oracle Cloud, Proxmox, VM lifecycle, storage and backups, tunnels, self-hosted GitHub Actions runners, PostgreSQL, Prometheus monitoring, workflow automation, and container-service operations.

[Public infrastructure overview](https://github.com/firstsun-dev/.github/blob/main/docs/infrastructure-overview.md) · [Service status](https://uptime.firstsun.org/status)

## More projects

- [Watermark Bucket Uploader](https://github.com/firstsun-dev/watermark-bucket-uploader) — image processing and S3-compatible object-storage delivery inside Obsidian
- [Firstsun Skills](https://github.com/firstsun-dev/skills) — reusable agent skills for engineering workflows
- [news-getter](https://github.com/firstsun-dev/news-getter) — deterministic news scoring, AI-assisted analysis, and a published digest pipeline
- [books-mgmt](https://github.com/firstsun-dev/books-mgmt) — Kavita collection automation and Google Drive export
- [Firstsun Tools](https://firstsun.heavenfortress.com/en/tools/) — small tools for practice, everyday life, design, and development

The organization profile is intentionally curated rather than a complete repository index. Browse [all repositories](https://github.com/firstsun-dev?tab=repositories) for workshop and supporting projects.

## Firstsun / 首陽問路

Firstsun is broader than software. The journal connects engineering, practice, everyday life, and lessons learned along the way; Firstsun Dev is the engineering arm that turns those needs into software and keeps the systems running.

[Visit Firstsun](https://firstsun.org/en) · [Explore tools](https://firstsun.heavenfortress.com/en/tools/) · [Brand system](https://github.com/firstsun-dev/.github/blob/main/docs/brand-system.md)
