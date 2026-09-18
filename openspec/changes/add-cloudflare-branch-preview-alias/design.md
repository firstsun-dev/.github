# Design: Cloudflare Worker branch-specific Aliased Preview URLs

## Control flow

```text
branch push (non-main)
        |
        v
enable_preview_alias == true? --no--> existing behavior, unchanged
        |
       yes
        |
        v
normalize-branch-alias (composite action)
  input:  github.ref_name
  output: alias
        |
        v
wrangler versions upload --env dev --preview-alias <alias> ...
        |
        v
   Worker VERSION_ID
       /        \
      /          \
branch alias   wrangler versions deploy --env dev VERSION_ID@100
(new, additive)         |
                 existing shared dev endpoint
                 (e.g. blog-preview.firstsun.org)
```

The alias and the shared dev deployment point at the **same** `VERSION_ID`
immediately after a given branch's deploy step runs. A later push to a
*different* branch moves the shared dev endpoint to that branch's version,
but does not touch the *first* branch's alias — the alias keeps pointing at
whatever `VERSION_ID` was uploaded the last time alias's own branch was
pushed, since each push recomputes the same alias and reattaches it to that
push's new version via `--preview-alias`.

`main` never enters the `enable_preview_alias == true?` branch — the
condition additionally requires `github.ref != 'refs/heads/main'`.

## Interfaces

### `_cf-worker-template.yml` (workflow_call input)

```yaml
enable_preview_alias:
  description: Create a branch-specific Cloudflare Aliased Preview URL for non-main deployments
  type: boolean
  default: false
```

### `.github/actions/normalize-branch-alias` (composite action)

- Input: `branch-name` (string, required) — raw branch name, e.g. `github.ref_name`.
- Output: `alias` (string) — normalized, ≤32-char, Cloudflare-safe alias.
- Implementation: a single bash script, `normalize.sh`, co-located with the
  action so the normalization logic exists in exactly one place and can be
  unit-tested outside of a workflow run (see `scripts/test-normalize-branch-alias.sh`).

### Deploy job wiring

A new step, `Compute preview alias` (`id: preview-alias`), runs before
`Set build environment`, gated on
`inputs.enable_preview_alias && github.ref != 'refs/heads/main'`. Its output
is read inside the existing `Deploy` step's shell script, alongside the
pre-existing `WRANGLER_ENV` branch/dev split, to build a `PREVIEW_ALIAS_ARGS`
string appended to the existing `wrangler versions upload` invocation. No
second upload is introduced.

## Alias normalization rules

| Step | Rule |
|---|---|
| Case | lowercase the entire branch name |
| Charset | replace any run of characters outside `[a-z0-9]` with a single `-` |
| Collapse | collapse any remaining repeated `-` (handles branch names with literal `--`) |
| Trim | strip leading/trailing `-` |
| Empty fallback | if the result is empty, use `branch` |
| Leading-letter rule | if the result does not start with `a-z`, prefix with `branch-` |
| Length | if the result exceeds 32 characters, truncate |

### Collision-safe truncation

Truncation does not merely cut the string — two branches whose names are
identical for the first ~23 characters and differ only near the end (e.g. a
shared long prefix with different suffixes) must not collapse to the same
alias. The truncated form is:

```text
<first 23 chars of the normalized, pre-truncation name>-<8-char sha256 hex of the ORIGINAL branch name>
```

The hash input is the **original raw branch name**, not the already-truncated
prefix, so any difference anywhere in the branch name — including near the
end — changes the hash and therefore the resulting alias. The hash is *not*
based on the commit SHA, which is what keeps the alias stable across repeated
pushes to the same branch.

23 + 1 (`-`) + 8 (hash) = 32, matching the target maximum.

### Worked examples

| Input branch | Output alias |
|---|---|
| `feat/calendar-ui` | `feat-calendar-ui` |
| `fix/muyu_audio` | `fix-muyu-audio` |
| `Feature/New.Page` | `feature-new-page` |
| `123-new-page` | `branch-123-new-page` |
| `very-long-feature-branch-name-that-needs-truncation-aaaa...` | `very-long-feature-branc-<hash8>` |

## Failure handling / logging

- `wrangler versions upload` has no `--json` output for `versions upload`
  (verified against installed wrangler 4.111.0's `--help`), so there is no
  stable machine-readable field to extract the resulting Aliased Preview URL
  from. The design deliberately does not add fragile output parsing to
  manufacture that URL.
- The Deploy step logs `Branch: <name>` / `Preview alias: <alias>` to stdout
  unconditionally when alias mode is active, and makes one best-effort
  `grep` for a `*.workers.dev` URL in Wrangler's own stdout to append to
  `$GITHUB_STEP_SUMMARY`; a non-match is silently skipped rather than failing
  the step. A successful upload/deploy always takes priority over this
  decorative summary.
