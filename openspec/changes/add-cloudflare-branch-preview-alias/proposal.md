# Proposal: Cloudflare Worker branch-specific Aliased Preview URLs (Phase 1)

## Why

All non-`main` pushes to a repository using `_cf-worker-template.yml` deploy to
the same shared `dev` Worker environment. Two people working on different
branches at the same time overwrite each other's preview at the shared dev
URL (e.g. `blog-preview.firstsun.org` for `firstsun-dev/blog`). There is
currently no way to get a stable, branch-specific preview URL without
building a bespoke deployment path per repository.

Cloudflare Workers support Aliased Preview URLs via
`wrangler versions upload --preview-alias <alias>`, which attaches an
additional stable hostname to a specific uploaded Worker version without
changing how that version is otherwise deployed.

## What Changes

- Add an opt-in `enable_preview_alias` boolean input (default `false`) to
  `_cf-worker-template.yml`.
- Add a composite action, `actions/normalize-branch-alias`, that
  deterministically normalizes a branch name into a Cloudflare-safe alias
  (lowercase `a-z0-9-`, starts with a letter, ≤32 chars, collision-safe
  truncation via a hash of the original branch name).
- When a caller opts in and the ref is not `refs/heads/main`, the `deploy`
  job's existing `wrangler versions upload` call additionally passes
  `--preview-alias <alias>`. The same uploaded `VERSION_ID` continues to be
  deployed to the `dev` environment at 100% traffic exactly as before — this
  is one upload, not two.
- `main` deployments are never aliased, regardless of the input.

## Compatibility

- Default is `false`. Existing consumers (`firstsun-dev/blog`,
  `firstsun-dev/heaven-monorepo`, `firstsun-dev/heaven-www`,
  `firstsun-dev/innovation-apps`) require **no changes** and are unaffected.
- The existing shared dev deployment architecture, environment URL,
  migration, verification, and rollback behavior are unchanged for all
  callers, opted-in or not.
- Production (`main`) behavior is unchanged unconditionally.

## Explicitly out of scope (Phase 1)

- Alias cleanup/deletion when a branch is deleted or merged.
- Per-branch D1 database isolation.
- Per-branch R2 bucket isolation.
- Any change to which consumer repositories are opted in — opting in is a
  per-repository decision made in that repository's own workflow call, not a
  default flip here.

## Affected repositories

- `firstsun-dev/.github` — implements the capability (this change).
- `firstsun-dev/blog` — first (and, at time of writing, only) consumer to opt
  in, via a separate cross-repo OpenSpec change in that repository.
- `firstsun-dev/heaven-monorepo`, `firstsun-dev/heaven-www`,
  `firstsun-dev/innovation-apps` — no changes required; verified unaffected
  because the new input defaults to `false`.

## Rollout / version impact

Backward compatible, additive input — ships as `v1.4.0`, with the floating
`v1` tag moved forward to the same release commit.
