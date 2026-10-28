# Cloudflare Native Preview garbage collection

Opt-in companion to [CF Worker CI/CD v2](cf-worker-v2.md). v2 keeps a branch's Native Preview after the branch is deleted;
this reusable workflow (`.github/workflows/_cf-worker-preview-gc.yml`, tooling in `actions/cf-preview-gc`) removes it
**7 days after the branch deletion**. It never changes the Deploy / Smoke / Rollback / Release DAG and has no effect on v1.

Disabled by default (`enabled: false`), and dry-run by default (`dry_run: true`). Deletion needs `enabled: true` **and** `dry_run: false`, set explicitly by the consumer repo.

## Lifecycle policy

| State | Result |
|---|---|
| Branch exists (any PR state: open / merged / closed) | KEEP |
| Branch deleted < `retention_days` ago | KEEP (retention pending) |
| Branch deleted ≥ `retention_days` ago, all checks pass | eligible → delete |
| In `protected_previews` | KEEP forever (even with no branch / ledger) |
| Ownership, timestamp, branch state, account or Worker uncertain; legacy ledger | SKIP (report only) |

Retention starts at the **branch-deletion tombstone**, not at creation or last deploy. Repeated pushes to a branch share one Preview, and recreating the branch (even before a new push) keeps it.

## How a decision is made

1. **Ledger** (GitHub Deployments of the consumer repo, read with its own `github.token`, fully paginated): environments `<app>/preview/<name>`. All records of a Preview must agree on `repo`, `worker`, `host_worker`, `preview_name` and a single `branch_ref`, and `previewIdentity()` of that ref must reproduce `<name>`. Anything else (legacy records without payload/host, two refs sharing a slug) → SKIP.
2. **Tombstone**: the latest status of the newest deployment must be `inactive` with description exactly `Branch deleted; Preview kept` (written by the v2 `lifecycle` job via `gh.sh retire`) and created by `github-actions[bot]`. Its `created_at` is `deleted_at`. A superseded/other inactive status is not a tombstone; a newer deployment (including one in progress) after it means the Preview is live.
3. **Branch**: live `git/ref/heads/<branch>` must be 404, and no existing branch may normalise to the same Preview name.
4. **Cloudflare**: the target `account_id` must equal `CLOUDFLARE_PREVIEW_ACCOUNT_ID`; `GET /accounts/{id}/workers/workers/{host}/previews/{name}` decides existence (404 = already gone, treated as idempotent ledger cleanup). 401/403/429 abort the run.
5. **Just before each delete**: branch, newest deployment id, ownership payload, tombstone status and the Cloudflare Preview are re-read; any change skips it (protects against concurrent pushes / branch recreation).
6. **Delete**: `wrangler@4.135.0 preview delete --name <name> --worker-name <host> --skip-confirmation` with the account passed explicitly. Never `wrangler delete`. At most `max_deletions_per_run`, oldest first. Afterwards an `inactive` status `Preview garbage-collected (<ts>)` is appended to the newest deployment so it is never retried. GitHub Deployment history is never deleted.

Only Preview names derived from `<app>/preview/<name>` ledger environments are ever candidates. Production Workers, the Shared Dev Worker (`<app>/dev/*`), D1/R2/KV/Queues, domains, DNS and Terraform state are never addressed.

> **Impact of `wrangler preview delete`** (Cloudflare docs): deletes the Preview and all its deployments; it does not remove Worker-level settings or the separate resources bound to a Preview. A Preview's own Durable Object namespace / container instances go with it. Cloudflare does not document further side effects, so Phase 3 verifies this on one explicitly selected Preview before anything is scheduled.

## Inputs

| Input | Default | |
|---|---|---|
| `enabled` | `false` | master switch |
| `dry_run` | `true` | report only |
| `retention_days` | `7` | |
| `max_deletions_per_run` | `5` | |
| `targets` | required | JSON `[{"app_name","preview_worker_name?","account_id"}]`, one entry per Worker (monorepo) |
| `protected_previews` | `[]` | JSON `[{"account_id","host_worker","preview_name"}]` — explicit allowlist, no name guessing |
| `only_previews` | – | JSON `["name"]`, restrict a controlled cleanup to exactly these |
| `tools_ref` | required | must equal the ref the caller pins |

Secrets: `CLOUDFLARE_PREVIEW_API_TOKEN`, `CLOUDFLARE_PREVIEW_ACCOUNT_ID` (same contract as v2). A target whose `account_id` differs from the supplied credentials is reported as ERROR and untouched; for several Cloudflare accounts add one caller job per account with its own secrets.

Permissions: `contents: read`, `deployments: write` (only used when deleting). The Cloudflare token needs Workers Scripts Edit on the Preview account.

## Reports

Every run writes a Job Summary (Repository / Worker / Preview / Branch / Deleted At / Age / Status / Reason, plus counts for Total, Active, Protected, Retention pending, Eligible, Deleted, Skipped/Unknown, Failed) and uploads `preview-gc-report` (JSON, 30 days). Status values: KEEP, ELIGIBLE, DELETE, SKIP, ERROR. The job fails on any ERROR or abort.

## Consumer setup

```yaml
# .github/workflows/preview-gc.yml
name: Preview GC
on:
  schedule:
    - cron: '0 19 * * *'   # 03:00 Asia/Taipei (UTC+8)
  workflow_dispatch:
    inputs:
      dry_run: { type: boolean, default: true }
      only_previews: { type: string, default: '' }   # e.g. ["feat-old-branch"]
permissions:
  contents: read
  deployments: write
jobs:
  gc:
    uses: firstsun-dev/.github/.github/workflows/_cf-worker-preview-gc.yml@<immutable ref>
    with:
      tools_ref: <same immutable ref>
      enabled: true
      dry_run: ${{ github.event_name != 'workflow_dispatch' || inputs.dry_run }}   # schedule stays dry-run until you edit this
      only_previews: ${{ github.event_name == 'workflow_dispatch' && inputs.only_previews || '' }}
      targets: '[{"app_name":"www","preview_worker_name":"www","account_id":"<cloudflare account id>"}]'
      protected_previews: '[{"account_id":"<id>","host_worker":"www","preview_name":"<fixed-dev-preview-name>"}]'
    secrets:
      CLOUDFLARE_PREVIEW_API_TOKEN: ${{ secrets.CLOUDFLARE_PREVIEW_API_TOKEN }}
      CLOUDFLARE_PREVIEW_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_PREVIEW_ACCOUNT_ID }}
```

The workflow file must be on the default branch for `schedule` to run.

## Rollout

1. **Shared implementation** (this change): tool, workflow, docs, tests.
2. **Dry-run pilot**: one consumer with Native Previews, `dry_run: true`; review the report and the protected allowlist.
3. **Controlled cleanup**: `workflow_dispatch` with `dry_run: false` and `only_previews` naming one test Preview; confirm no other Preview / Worker / resource changed.
4. **Scheduled rollout**: per repo, change the schedule line to `dry_run: false` (a reviewed PR in that repo), one repo at a time.

## Not automatically cleanable

* Previews with legacy ledger records (no payload / no `host_worker`) — report only.
* Previews of branches deleted before the tombstone existed or while the `lifecycle` job did not run (no `deleted_at`) — report only; delete manually with `wrangler preview delete`.
* Two branches normalising to the same slug — ambiguous owner.
