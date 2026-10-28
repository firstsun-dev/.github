// Entry point for the reusable GC workflow. Configuration is read from GC_* env vars (set by the workflow from
// validated inputs); credentials from CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID / GH_TOKEN.
import { appendFileSync, writeFileSync } from 'node:fs';
import { runGc, renderSummary, GcAbort } from './gc.mjs';
import { githubClient, cloudflareClient } from './clients.mjs';

const need = (k) => { const v = process.env[k]; if (!v) throw new Error(`${k} is required`); return v; };
const json = (k, d) => { const v = process.env[k]; return v ? JSON.parse(v) : d; };
const bool = (k, d) => (process.env[k] === undefined || process.env[k] === '' ? d : process.env[k] === 'true');
const int = (k, d) => { const v = process.env[k]; if (!v) return d; if (!/^\d+$/.test(v)) throw new Error(`${k} must be a non-negative integer`); return Number(v); };

export function loadConfig(env = process.env) {
  const repo = need('GITHUB_REPOSITORY');
  const targets = json('GC_TARGETS', []).map((t) => {
    if (!t.app_name || !t.account_id) throw new Error('every target needs app_name and account_id');
    return { appName: t.app_name, hostWorker: t.preview_worker_name || t.app_name, accountId: String(t.account_id) };
  });
  if (!targets.length) throw new Error('GC_TARGETS is empty');
  const prot = json('GC_PROTECTED_PREVIEWS', []).map((p) => {
    if (!p.account_id || !p.host_worker || !p.preview_name) throw new Error('protected_previews entries need account_id, host_worker and preview_name');
    return { accountId: String(p.account_id), hostWorker: p.host_worker, previewName: p.preview_name };
  });
  const retentionDays = int('GC_RETENTION_DAYS', 7);
  if (retentionDays < 1) throw new Error('retention_days must be >= 1');
  return {
    repo,
    enabled: bool('GC_ENABLED', false),
    dryRun: bool('GC_DRY_RUN', true),
    retentionDays,
    maxDeletions: int('GC_MAX_DELETIONS', 5),
    only: json('GC_ONLY_PREVIEWS', null),
    targets,
    protected: prot,
    credentialAccountId: need('CLOUDFLARE_ACCOUNT_ID'),
    tombstoneCreators: ['github-actions[bot]'],
  };
}

async function main() {
  const cfg = loadConfig();
  if (!cfg.enabled) {
    console.log('Preview GC is disabled (enabled: false); nothing to do.');
    appendSummary('## Preview GC disabled\n\nSet `enabled: true` (and keep `dry_run: true`) to produce a report.\n');
    return;
  }
  const github = githubClient({ repo: cfg.repo, token: need('GH_TOKEN'), apiUrl: process.env.GITHUB_API_URL });
  const cloudflare = cloudflareClient({ token: need('CLOUDFLARE_API_TOKEN') });
  let report;
  try {
    report = await runGc({ cfg, github, cloudflare });
  } catch (e) {
    if (!(e instanceof GcAbort)) throw e;
    console.error(`::error::Preview GC aborted before any deletion: ${e.message}`);
    process.exitCode = 1;
    appendSummary(`## Preview GC aborted\n\n${e.message}\n`);
    return;
  }
  writeFileSync(process.env.GC_REPORT_PATH || 'preview-gc-report.json', JSON.stringify(report, null, 2));
  appendSummary(renderSummary(report));
  console.log(JSON.stringify(report.summary));
  if (report.summary.failed || report.aborted.length) process.exitCode = 1;
}

function appendSummary(md) {
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, md + '\n');
  else console.log(md);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((e) => { console.error(`::error::${e.message}`); process.exit(1); });
}
