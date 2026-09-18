# Tasks: Cloudflare Worker branch-specific Aliased Preview URLs

## `.github` (this change)

- [x] Add `enable_preview_alias` input (default `false`) to `_cf-worker-template.yml`.
- [x] Add `actions/normalize-branch-alias` composite action + `normalize.sh`.
- [x] Wire alias computation into the `deploy` job, gated on
      `enable_preview_alias && github.ref != 'refs/heads/main'`.
- [x] Pass `--preview-alias` into the existing `wrangler versions upload`
      call for opted-in non-main deployments only; no second upload added.
- [x] Log branch/alias to stdout and best-effort append to
      `$GITHUB_STEP_SUMMARY`.
- [x] Add `scripts/test-normalize-branch-alias.sh` covering charset, max
      length, determinism, and truncation-collision safety against the
      required representative branch names.
- [x] Update `openspec/specs/cloudflare-worker-delivery/spec.md` with the new
      requirements/scenarios.
- [x] Write this change's `proposal.md` / `design.md` / `tasks.md` / delta
      spec.
- [x] Manually reason through the four execution-path cases against the
      final YAML:
      - `main+false`: `preview-alias` step condition (`ref != main`) is
        false, `Deploy` step's `main` branch sets `PREVIEW_ALIAS_ARGS=""`
        unconditionally — identical to pre-change behavior.
      - `main+true`: same as above; the `main` branch of the `Deploy` step
        never reads `enable_preview_alias` — identical to pre-change
        behavior regardless of the input.
      - `feature+false`: `preview-alias` step condition is false (input
        false) so `steps.preview-alias.outputs.alias` is empty;
        `Deploy` step's non-main branch requires the input `== 'true'`
        before setting `PREVIEW_ALIAS_ARGS`, so it stays empty — identical
        to pre-change shared-dev-only behavior.
      - `feature+true`: `preview-alias` step runs and outputs a non-empty
        alias; `Deploy` step sets `PREVIEW_ALIAS_ARGS="--preview-alias
        <alias>"` and still runs the unchanged `versions deploy
        $WRANGLER_ENV "$VERSION_ID@100"` afterward — new alias, existing
        shared dev deploy both happen from one upload.
- [x] Open PR (#21), merged to `main`.
- [x] Cut release `v1.4.0` via `scripts/release.sh v1.4.0`; moved floating
      `v1` to the same commit; verified older exact tags (`v1.0.0`–`v1.3.0`)
      untouched.
- [x] **Incident**: `v1.4.0`/`v1` shipped with the composite action placed at
      `.github/actions/normalize-branch-alias/` while the `uses:` reference
      (and this repo's existing convention — `actions/setup`,
      `actions/mailpit`, `actions/parse-env` all live at repo-root
      `actions/`) resolves to root-level `actions/normalize-branch-alias/`.
      First live Blog deploy failed with `Can't find 'action.yml' ... for
      action 'firstsun-dev/.github/actions/normalize-branch-alias@v1'`.
      Fixed by moving the action to `actions/normalize-branch-alias/`
      (matching convention) and released as `v1.4.1` (see below) rather than
      rewriting the immutable `v1.4.0` tag.
- [x] Cut patch release `v1.4.1` with the path fix; moved floating `v1` to
      `v1.4.1`; `v1.4.0` left immutable/untouched.

## `firstsun-dev/blog` (separate cross-repo change)

Tracked in that repository's own `openspec/changes/enable-cloudflare-branch-previews/`.

- [x] `wrangler.toml`: `preview_urls = true` under `[env.dev]`.
- [x] `.github/workflows/cicd.yml`: pinned shared workflow to `@v1.4.1`
      (bumped from `@v1.4.0` after the incident above), set
      `enable_preview_alias: true`.
- [x] Local quality gate green before push.
- [x] Pushed a real non-main branch (PR #403); full CI pipeline succeeded
      on the second run (after the `v1.4.1` fix). Live-verified: the
      branch's alias (`feat-enable-cloudflare-dea1ad76-blog-preview.<account>.workers.dev`)
      served the branch's exact commit via `PUBLIC_COMMIT_SHA`, and kept
      serving it even after an unrelated concurrent rollback moved the
      shared `blog-preview.firstsun.org` URL back to an older commit —
      demonstrating alias independence from the shared dev URL under real
      interference. A same-branch redeploy also confirmed the alias
      hostname stays stable while the served version updates.
- [x] **Second incident found + fixed** (Blog-side, not `.github`):
      `wrangler versions upload`/`versions deploy` never sync the
      `workers_dev`/`preview_urls` subdomain-enablement flags to Cloudflare
      — confirmed via the Workers API that `blog-preview`'s subdomain
      settings stayed `{"enabled": false, "previews_enabled": false}` even
      after a successful aliased upload. Fixed with a one-time
      `POST .../workers/scripts/blog-preview/subdomain
      {"enabled": false, "previews_enabled": true}` (persistent Cloudflare
      script setting, not reset by future CI runs; production `blog`
      script's subdomain settings confirmed untouched). Documented in
      Blog's `design.md` as a caveat for anyone recreating the script from
      scratch.
- [ ] Two-simultaneous-branches scenario (a second throwaway branch) — not
      yet exercised; the alias-independence property was already
      demonstrated live via the concurrent-rollback observation above.

## Explicitly not done in this phase

- No alias cleanup/deletion on branch deletion.
- No per-branch D1/R2 isolation.
- No changes to `heaven-monorepo`, `heaven-www`, or `innovation-apps` — they
  remain unaffected by the default-`false` input.
