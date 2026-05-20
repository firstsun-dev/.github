# Firstsun's Digital Forge

![GitHub followers](https://img.shields.io/github/followers/firstsun-dev?style=flat-square) ![GitHub repos](https://img.shields.io/badge/Repos-12-blue?style=flat-square) ![License](https://img.shields.io/github/license/firstsun-dev/.github?style=flat-square)
![Standards: Conventional Commits](https://img.shields.io/badge/Standards-Conventional%20Commits-FE5196?style=flat-square&logo=git) ![Documentation: Diátaxis](https://img.shields.io/badge/Docs-Di%C3%A1taxis-339933?style=flat-square)
![Tech: TypeScript](https://img.shields.io/badge/Tech-TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white) ![Tech: Python](https://img.shields.io/badge/Tech-Python-3776AB?style=flat-square&logo=python&logoColor=white) ![Tech: Obsidian](https://img.shields.io/badge/Tech-Obsidian-7C3AED?style=flat-square&logo=obsidian&logoColor=white) ![Tech: Astro](https://img.shields.io/badge/Tech-Astro-BC52EE?style=flat-square&logo=astro&logoColor=white)

Welcome to my central configuration and automation hub. This repository serves as the backbone for my cross-project standards, agent skills, and workflow automations.

## Core Ecosystem

I build and maintain highly automated workflows for content creation, spiritual study, and modern web development.

### Obsidian Plugin Development & Ecosystem
| Repository | Purpose | Tech Stack |
| :--- | :--- | :--- |
| **[watermark-s3-uploader](https://github.com/firstsun-dev/watermark-s3-uploader)** | Obsidian plugin that watermarks images and uploads to Cloudflare R2 / S3 on paste or drop. | TypeScript, Node.js, AWS S3 |
| **[git-files-sync](https://github.com/firstsun-dev/git-files-sync)** | Obsidian plugin for selective note sync with GitHub/GitLab — granular push/pull with visual dashboard. | TypeScript, Obsidian API, Git |
| **[obsidian-releases](https://github.com/firstsun-dev/obsidian-releases)** | Community plugins list, theme list, and releases of Obsidian. | TypeScript, Obsidian API |
| **[obsidian-plugin-template](https://github.com/firstsun-dev/obsidian-plugin-template)** | Standardized template for Obsidian plugin development with shared CI/CD and semantic-release. | TypeScript, Obsidian API |

### Content & Knowledge Management
| Repository | Purpose | Tech Stack |
| :--- | :--- | :--- |
| **[heaven-video-summary](https://github.com/firstsun-dev/heaven-video-summary)** | 天界之舟 YouTube lecture transcription pipeline: playlist → Whisper → Markdown → Google Drive. | Python, Whisper, Bash |
| **[news-getter](https://github.com/firstsun-dev/news-getter)** | Personalized AI news digest: RSS feeds → Gemini CLI summary → e-ink friendly RSS feed. | Python, Bash |
| **[books-mgmt](https://github.com/firstsun-dev/books-mgmt)** | Kavita book server automation: sync collections by directory structure and export to Google Drive as TXT. | Python, uv, rclone |
| **[blog](https://github.com/firstsun-dev/blog)** | Personal tech blog with multi-language support, dark mode, article likes, and view stats (Private). | Astro, Cloudflare Pages, D1 |
| **[my-apple-health](https://github.com/firstsun-dev/my-apple-health)** | Apple Health XML export pipeline → CSV/Markdown for AI agents like NotebookLM (Private). | Python, uv |

### 天界之舟 Ecosystem
| Repository | Purpose | Tech Stack |
| :--- | :--- | :--- |
| **[heaven-monorepo](https://github.com/firstsun-dev/heaven-monorepo)** | Core system for Heaven (天界之舟): Heaven ID (OIDC on Cloudflare Workers), Check-in app, shared packages (Private). | TypeScript, Turborepo |

### DevOps & Automation
| Repository | Purpose | Tech Stack |
| :--- | :--- | :--- |
| **[.github](https://github.com/firstsun-dev/.github)** | Centralized governance, shared workflows, and issue templates. | GitHub Actions |

---

## AI Agent Strategy

I leverage a **Skill-First** approach to AI collaboration. My agents are equipped with domain-specific knowledge "Gems" and modular skills to ensure consistency and precision across all tasks.

### Repositories
| Repository | Purpose | Tech Stack |
| :--- | :--- | :--- |
| **[yao-agent-skills](https://github.com/firstsun-dev/yao-agent-skills)** | Centralized skill arsenal for AI agents (Gemini CLI, Claude Code) — custom and community skills. | YAML, Markdown |

### Featured Gems (Gemini Optimized)
*   **Lifestyle Architect**: Integrated health (HealthKit), learning, and thinking frameworks.
*   **DevOps Engineer**: Production-grade CI/CD and Cloudflare deployment patterns.
*   **Content Alchemist**: Multi-language transcription (Whisper) and Diátaxis-compliant documentation.

---

## Standards & Guardrails

*   **Documentation**: Guided by the [Diátaxis framework](https://diataxis.fr/).
*   **Development**: Strict adherence to [Conventional Commits](https://www.conventionalcommits.org/).
*   **Automation**: Heavy reliance on GitHub CLI (`gh`) and custom GitHub Actions.

---

## Connectivity

*   **GitHub**: [@firstsun-dev](https://github.com/firstsun-dev)
*   **Website**: [firstsun.org/en](https://firstsun.org/en)
*   **Purpose**: To build tools that bridge the gap between ancient wisdom and modern technology.

*"Technology is best when it brings people together and clarifies the path."*
