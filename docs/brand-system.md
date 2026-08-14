# Firstsun Dev Brand System

This document is the canonical public-facing brand and repository-governance reference for Firstsun Dev.

It exists to keep project presentation consistent without turning every repository into the same template.

## Brand position

**Firstsun Dev is the engineering arm of Firstsun / 首陽問路.**

It turns real needs into useful software, operates the systems that matter, and shares what is learned from building and running them.

The organization should not present itself primarily as an SRE portfolio, an AI-project collection, or a generic software studio.

### Tagline

> **Build useful things. Operate them well. Share what we learn.**

This is the long-term Firstsun Dev tagline.

### Internal quality test

A useful internal test for brand fit is:

> Engineering that survives contact with reality.

This is a decision aid, not a public slogan that needs to appear everywhere.

## Audience

When public material needs a priority order, write for:

1. users
2. engineers and contributors
3. industry peers
4. recruiters and evaluators

Recruiter value should emerge from credible engineering evidence rather than resume-style framing.

## Brand architecture

- **Firstsun / 首陽問路** — parent brand spanning engineering, practice, everyday life, tools, and published perspectives
- **Firstsun Dev** — engineering arm / engineering studio beneath Firstsun
- **Projects** — retain their own product names and identities

Use Firstsun endorsement where useful, but do not prefix every project name with `Firstsun` or force a common product identity.

For owned projects, secondary attribution may use:

> A Firstsun Dev project.

For maintained forks:

> Maintained by Firstsun Dev.

Forks must preserve upstream authorship and clearly distinguish inherited work from Firstsun-maintained changes.

## Evidence-first editorial rule

Let repositories, tests, releases, architecture, service status, compatibility work, and real usage demonstrate quality.

Avoid self-awarded adjectives such as `powerful`, `world-class`, `enterprise-grade`, or `robust` unless they are needed in a precise technical sense and supported by evidence.

Live or automatically updated metrics can be useful evidence when relevant. Temporary snapshot numbers should not become long-term brand claims.

## Repository tiers

Repository visibility and brand prominence are separate decisions.

### Workshop

The default tier for new repositories.

A Workshop project may be public, useful, tested, and released. It is not automatically promoted into the organization profile, pinned repositories, or case studies.

### Supporting

A project may be promoted to Supporting when it demonstrates most of the following:

- a clear concrete problem or use case
- documentation understandable without insider context
- a reproducible install, execution, or demo path
- baseline verification such as tests, lint, build, or equivalent
- a reasonable maintenance state
- public metadata consistent with this document

Promotion is still an explicit curation decision.

### Flagship

Flagship projects are deliberately rare.

A candidate should demonstrate most of the following:

- real external usage or meaningful production dogfooding
- sustained maintenance
- operational evidence such as releases, compatibility tests, monitoring, service status, or reliability work
- meaningful engineering or product trade-offs
- mature public documentation and attribution
- a distinct role in the Firstsun Dev story that is not already represented by another flagship

Technical complexity or personal enthusiasm alone does not make a project a flagship.

## Current flagship story

The current selected-work model is intentionally complementary:

- **Git File Sync — Build**: can Firstsun build something genuinely useful?
- **Code Insights — Extend**: can Firstsun evolve a complex existing system responsibly while preserving lineage?
- **Heaven Platform — Operate**: can Firstsun keep real systems running and expose operational evidence?

Together they express **Build → Extend → Operate**.

Firstsun Bot is an emerging candidate and should earn stronger placement through documentation, a working demonstration, and dogfooding evidence.

## Organization profile

The organization profile is a curated storefront, not a repository index.

Preferred structure:

1. brand relationship and tagline
2. selected flagship work
3. emerging work when justified
4. engineering backbone
5. a deliberately small set of supporting projects
6. Firstsun relationship and navigation

Do not add every public repository to the profile.

## Pinned repositories

Pinned repositories are also a storefront, not an inventory list.

Do not fill all available slots merely because they exist.

Current preferred order:

1. `git-files-sync`
2. `code-insights`
3. `watermark-bucket-uploader`
4. `skills`
5. `news-getter`

Reserve a future slot for `firstsun-bot` after it earns promotion through public presentation and operating evidence.

Do not use a pinned slot for `.github` or `status-page` merely to show supporting infrastructure. Link the useful public evidence surface instead.

## GitHub metadata contract

### Description

The repository description should explain **what the project is or what problem it solves**.

Good:

> Obsidian plugin for selective file sync with GitHub, GitLab, and Gitea.

Avoid turning the description into ownership, self-evaluation, or a technology dump.

### Topics

Topics exist for discoverability.

Prefer:

- domain
- ecosystem
- primary technology when useful

Do not add `firstsun`, `portfolio`, or `side-project` merely as branding labels.

### Homepage URL

Use a real product page, documentation site, live application, distribution page, or other useful destination.

Leave it empty rather than filling it with an unrelated Firstsun URL.

## README contract

Repositories keep their own product personality, but public Supporting and Flagship projects should answer these questions quickly:

1. What is this?
2. Who is it for / what problem does it solve?
3. Why does it matter?
4. Can I use or verify it now?

A useful first-view order is:

```text
Project name
One-line value / problem statement
Useful status evidence
Screenshot or demo when relevant
How it works / usage
Architecture / development details
Attribution / Firstsun relationship
```

Do not force a Firstsun logo, common hero banner, or decorative badge set into every README.

When only one visual can occupy the high-value first viewport, prefer a product screenshot or useful diagram over branding artwork.

## Language policy

Firstsun Dev GitHub repositories are **English-first** where they are intended for external engineering use.

Traditional Chinese is first-class localization where useful. Prefer separate documents such as `README.zh-TW.md` or `USAGE_zh.md` instead of paragraph-by-paragraph bilingual duplication.

Firstsun / 首陽問路 itself may remain Chinese-first where culture and readership make that appropriate; the GitHub organization is the engineering interoperability layer.

## Visual policy

Unify the endorsement and quality standard, not the visual appearance of every product.

- project identity comes first
- Firstsun endorsement is secondary
- useful badges are preferred over decorative badges
- screenshots and architecture diagrams are preferred over generic brand banners
- do not create a mandatory repository CIS

The strongest visual consistency should come from repeated engineering behavior: clear problem framing, working products, tests, operational evidence, and disciplined documentation.

## Private systems and public evidence

Private repositories are not portfolio objects by themselves.

When private implementation is brand-relevant, translate it into an appropriate public evidence surface:

- architecture overview
- design decisions
- operating model
- monitoring / service status
- deployment or reliability practices
- incident or operational lessons

The principle is:

> **Private implementation → public architecture and operational evidence.**

## Case studies

A case study is justified when there is a real engineering story with decisions and evidence.

Use this structure:

```text
Context
→ Constraint
→ Decision
→ Trade-offs
→ Operational evidence
→ What changed / what we learned
```

Do not create a case study merely because a project is technically interesting. A case study should not be a longer README, a feature list, or a portfolio-style recap.

## Promotion workflow

The default lifecycle is:

> **Workshop → Supporting → Flagship**

Projects are public when appropriate, but prominence is earned by evidence.

Initialization tools should bootstrap the engineering baseline, not publicity. `firstsun-project-init` must therefore default new projects to Workshop and treat profile placement, pinning, and case studies as separate curation decisions.

## Naming and casing

Use **Firstsun** and **Firstsun Dev** as the canonical brand casing in prose.

Use lowercase technical identifiers such as `firstsun-dev` where required by repository names, image names, URLs, package identifiers, or other machine-facing conventions.

Avoid `FirstSun` in new prose unless preserving an external or historical identifier that should not be renamed.

## Governance principle

The goal is not to make every repository look the same.

The goal is to make repeated contact with Firstsun Dev reveal the same engineering behavior:

> clear problem → useful implementation → verification → operation → evidence → learning
