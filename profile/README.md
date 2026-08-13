# Firstsun Dev

**Engineering behind [Firstsun / 首陽問路](https://firstsun.org/en).**

> Build useful things. Operate them well. Share what we learn.

Firstsun Dev is the engineering side of Firstsun: open-source plugins, public tools, production services, shared infrastructure, and practical automation built from real needs.

[Firstsun Journal](https://firstsun.org/en) · [Tools](https://firstsun.heavenfortress.com/en/tools/) · [Service status](https://uptime.firstsun.org/status)

![Firstsun Dev — build, operate, share](./firstsun-operating-loop.svg)

## What we build

### Open tools & plugins

#### [Git File Sync](https://github.com/firstsun-dev/git-files-sync)

**Selective Git synchronization for Obsidian.**

Sync individual notes or batches with GitHub, GitLab, and Gitea without requiring a local Git installation. The plugin includes status review, conflict handling, `.gitignore` support, and mobile-ready workflows.

[![Obsidian plugin downloads](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fobsidianmd%2Fobsidian-releases%2Fmaster%2Fcommunity-plugin-stats.json&query=%24%5B%22git-file-sync%22%5D.downloads&label=downloads&style=flat-square&color=007acc)](https://obsidian.md/plugins?id=git-file-sync)

[Obsidian Community Plugin](https://obsidian.md/plugins?id=git-file-sync) · [Read the guide](https://firstsun.heavenfortress.com/en/blog/git-file-sync-obsidian-github-gitlab-gitea/)

#### [Watermark Bucket Uploader](https://github.com/firstsun-dev/watermark-bucket-uploader)

**Image processing and object-storage delivery inside Obsidian.**

Adds text or logo watermarks, converts and compresses images, uploads them to S3-compatible storage such as Cloudflare R2, and writes the resulting URL back into the note.

#### [Firstsun Tools](https://firstsun.heavenfortress.com/en/tools/)

**Small tools for practice, everyday life, design, and development.**

The collection includes practice timers and calendars, image utilities, converters, text/data tools, and developer helpers. The goal is simple: solve one concrete problem well and keep the tool easy to use.

### Open-source extensions

#### [Code Insights](https://github.com/firstsun-dev/code-insights)

**A maintained fork for turning AI coding sessions into reusable knowledge.**

Firstsun extensions add Kilo Code support, multi-home analysis, personality and recurring-insight views, OpenAI-compatible analysis, and work-log workflows while preserving the upstream local-first model.

[Open the app](https://code-insights.app) · [Upstream](https://github.com/melagiri/code-insights)

### Production systems

#### Heaven Platform

**Cloudflare-native identity and application platform.**

A privately implemented, publicly operated system built around a shared OIDC identity boundary, auth client SDK, consumer applications, and centralized Playwright E2E testing. The service carries recurring real-world traffic and is continuously monitored, so availability, deployments, and regressions have real operational consequences.

[Architecture case study](https://github.com/firstsun-dev/.github/blob/main/docs/case-studies/heaven-platform.md) · [Service status](https://uptime.firstsun.org/status)

### Automation & knowledge systems

- [news-getter](https://github.com/firstsun-dev/news-getter) — RSS collection, deterministic scoring, AI-assisted analysis, and a published news digest
- [skills](https://github.com/firstsun-dev/skills) — reusable skills for coding agents
- [firstsun-bot](https://github.com/firstsun-dev/firstsun-bot) — issue → plan → coding agent → pull request workflow across providers
- [books-mgmt](https://github.com/firstsun-dev/books-mgmt) — Kavita collection automation and Google Drive export

## How we operate

Firstsun projects share the same engineering backbone instead of treating deployment and operations as afterthoughts. Real consumers make that operational discipline necessary: plugin downloads and recurring production traffic mean compatibility, availability, deployments, and regressions affect people outside the development environment.

- **Shared delivery** — reusable GitHub Actions for test, release, and deployment live in [`.github`](https://github.com/firstsun-dev/.github)
- **Infrastructure as code** — production and self-hosted services are managed with Terraform and Ansible
- **Observable services** — public service health is exposed through the [Firstsun status page](https://uptime.firstsun.org/status)
- **Private where appropriate** — some production source and infrastructure repositories remain private while their public products, documentation, and operational status stay visible

## Firstsun / 首陽問路

Firstsun is broader than a software organization. The journal connects engineering, practice, everyday life, and the things learned along the way; Firstsun Dev is the part that turns those needs into software and keeps the systems running.

[Visit Firstsun](https://firstsun.org/en) · [Explore tools](https://firstsun.heavenfortress.com/en/tools/) · [Browse repositories](https://github.com/firstsun-dev?tab=repositories)
