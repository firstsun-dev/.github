# ZAP scanning policy and triage notes

Reusable workflow: `.github/workflows/_zap-scan-template.yml`. Tests: `scripts/test-zap-template.sh`.

## Policy

| `environment` | `scan_mode: baseline` | `scan_mode: full` | API (OpenAPI) scan |
|---|---|---|---|
| `production` (default) | passive spider + passive rules | **refused** | **not scheduled** |
| `staging` / `development` | passive | active | active, only with `full` + `openapi_path` |

`protected_hosts` (comma-separated globs) blocks active scans against matching hosts, including hosts named in the OpenAPI `servers` list.
The `environment` input defaults to `production`, so existing callers that do not pass it can no longer trigger active scans.

## Incident notes (heaven-monorepo run 38849565824, 2026-10-16)

- **Artifact failure:** `zaproxy/action-*` always uploads raw reports and fails the step when the account quota is exhausted (`Failed to CreateArtifact: Artifact storage quota has been hit`). The repository itself only held ~0.5 MB across 14 artifacts, so the quota is account/organisation level, not caused by this repo; usage recalculates every 6-12 hours. The template now runs ZAP directly, writes redacted findings to the job summary, and uploads a single small redacted JSON with `report_retention_days` (default 7), best-effort.
- **Production was actively scanned:** the `api` matrix entry used `zaproxy/action-api-scan` (an active scanner; the log shows 107 URLs and SQL-injection attack rules running) against the production URLs on `main` even though `scan_mode` was `baseline`. `fail_action: false` does not make it passive. This is fixed by the policy above.

## SQL injection alerts (rule 40018: Heaven ID x1, Checkin x4)

The raw report was lost with the failed upload, so the exact requests could not be recovered; re-run against development to obtain them.
Static review of `apps/id/api/src/infra/db/d1.ts` and `apps/checkin/api/src/infra/db/d1.ts` (v1.1.4):

- All user-supplied *values* are bound with `prepare(...).bind(...)`; `IN (...)` lists are built from `?` placeholders only.
- Column names are interpolated from object keys in `updateUser`, `updateServiceProvider`, `updateGroup`, `updateCheckinRecord` (and `createUser` filters against an allow-list). Callers inspected pass literal keys, so no injection path was found, but these sinks lack a local allow-list.
- Assessment: **no confirmed vulnerability; probable false positives** (rule 40018 is heuristic and fires on 5xx/error-differing responses, and the same scan also reported "Server Error" and "Application Error Disclosure"). Confidence: medium, pending a development-environment re-run.
- Recommended follow-up (separate app PR, Low severity, defence in depth): add column allow-lists to the four dynamic `SET` builders, with tests.

## Rollout

`v1` is a floating major tag moved by `scripts/release.sh`; exact `vX.Y.Z` tags are immutable. Do not retag. After merge, cut the next minor with `release.sh`, then update `heaven-monorepo/.github/workflows/zap-scan.yml` to pass `environment` (`production` for `main`, `development` for dev/schedule/manual full) and `protected_hosts`. Until `v1` moves, the consumer is unchanged; after it moves, an un-updated consumer degrades safely to production baseline-only (dev scans would also be baseline until it passes `environment: development`).
