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
- [ ] Cut patch release `v1.4.1` with the path fix; move floating `v1` to
      `v1.4.1`.

## `firstsun-dev/blog` (separate cross-repo change)

Tracked in that repository's own `openspec/changes/enable-cloudflare-branch-previews/`.

- [ ] `wrangler.toml`: `preview_urls = true` under `[env.dev]`.
- [ ] `.github/workflows/cicd.yml`: pin shared workflow to `@v1.4.0`,
      set `enable_preview_alias: true`.
- [ ] Local quality gate green before push.
- [ ] Push a real non-main branch and confirm both the shared dev URL and
      the branch-specific alias serve the expected version; push a second
      branch and confirm the first branch's alias is unaffected; push again
      to the first branch and confirm its alias now serves the new version.

## Explicitly not done in this phase

- No alias cleanup/deletion on branch deletion.
- No per-branch D1/R2 isolation.
- No changes to `heaven-monorepo`, `heaven-www`, or `innovation-apps` — they
  remain unaffected by the default-`false` input.
