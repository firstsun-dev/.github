// Native Preview garbage collection for CF Worker CI/CD v2.
// Pure decision + orchestration logic; every remote effect goes through the injected `github` / `cloudflare`
// clients so the whole policy is unit-testable with fakes. Fail closed: any uncertainty => SKIP, never delete.
import { previewIdentity } from '../cf-preview/lib.mjs';

/** Exact description written by `gh.sh retire` when a branch is deleted. */
export const TOMBSTONE_DESCRIPTION = 'Branch deleted; Preview kept';
/** Written by this tool after a Preview was removed (or found absent) so it is never retried. */
export const CLEANED_DESCRIPTION = 'Preview garbage-collected';
export const DAY_MS = 86_400_000;

/** Abort the whole run (no further deletes): rate limit, permissions, incomplete enumeration. */
export class GcAbort extends Error {}

const PREVIEW_ENV_RE = /^(.+)\/preview\/([a-z0-9]+(?:-[a-z0-9]+)*)$/;
const IN_FLIGHT = new Set(['queued', 'pending', 'in_progress']);

export const sameProtected = (p, accountId, hostWorker, previewName) =>
  p.accountId === accountId && p.hostWorker === hostWorker && p.previewName === previewName;

function entry(target, previewName, fields) {
  return {
    repository: fields.repository,
    worker: target.hostWorker,
    app: target.appName,
    account_id: target.accountId,
    preview: previewName,
    branch: fields.branch ?? null,
    deleted_at: fields.deletedAt ?? null,
    age_days: fields.ageDays ?? null,
    status: fields.status,
    category: fields.category,
    reason: fields.reason,
  };
}

/**
 * Classify one Preview (read-only). Returns { entry, plan } where plan is set only for ELIGIBLE previews.
 */
export async function classify({ cfg, target, previewName, deployments, github, cloudflare, liveNames, now }) {
  const base = { repository: cfg.repo };
  const mk = (status, category, reason, extra = {}) => ({
    entry: entry(target, previewName, { ...base, ...extra, status, category, reason }),
  });
  const { accountId, hostWorker, appName } = target;

  if (cfg.protected.some((p) => sameProtected(p, accountId, hostWorker, previewName))) {
    return mk('KEEP', 'protected', 'in the protected_previews allowlist (account + host worker + preview name)');
  }

  // --- ownership: every ledger record of this env on this host must agree on repo/app/host/name/branch ---
  const refs = new Set();
  for (const d of deployments) {
    const p = d.payload;
    if (!p || typeof p !== 'object') return mk('SKIP', 'skipped', 'legacy ledger record without payload: report only');
    if (p.repo !== cfg.repo || p.worker !== appName || p.host_worker !== hostWorker || p.preview_name !== previewName || typeof p.branch_ref !== 'string') {
      return mk('SKIP', 'skipped', 'ledger ownership mismatch (repository / app / host worker / preview name): report only');
    }
    refs.add(p.branch_ref);
  }
  if (refs.size !== 1) return mk('SKIP', 'skipped', `ledger names ${refs.size} different branch refs for this Preview: ambiguous owner`);
  const [ref] = refs;
  const branch = ref.startsWith('refs/heads/') ? ref.slice('refs/heads/'.length) : null;
  const ex = { branch: branch ?? ref };
  if (!branch) return mk('SKIP', 'skipped', 'ledger branch_ref is not a branch ref', ex);
  let ident;
  try {
    ident = previewIdentity({ repo: cfg.repo, worker: appName, ref, hostWorker });
  } catch (e) {
    return mk('SKIP', 'skipped', `cannot recompute Preview identity: ${e.message}`, ex);
  }
  if (ident.name !== previewName) return mk('SKIP', 'skipped', 'ledger Preview name does not match the branch identity', ex);

  // --- branch liveness (live API, plus any other branch normalising to the same Preview name) ---
  if (await github.branchExists(branch)) return mk('KEEP', 'active', 'branch still exists', ex);
  if (liveNames.has(previewName)) return mk('KEEP', 'active', 'another existing branch normalises to this Preview name', ex);

  // --- tombstone: latest status of the NEWEST deployment ---
  const newest = deployments[0];
  const statuses = await github.listStatuses(newest.id);
  const latest = statuses[0];
  if (!latest) return mk('SKIP', 'skipped', 'newest deployment has no status', ex);
  if (IN_FLIGHT.has(latest.state)) return mk('KEEP', 'active', `a deployment is ${latest.state}`, ex);
  if (latest.state === 'inactive' && String(latest.description).startsWith(CLEANED_DESCRIPTION)) {
    return mk('SKIP', 'skipped', 'already garbage-collected (ledger records it)', { ...ex, deletedAt: latest.created_at });
  }
  const isTombstone =
    latest.state === 'inactive' &&
    latest.description === TOMBSTONE_DESCRIPTION &&
    cfg.tombstoneCreators.includes(latest.creator?.login);
  if (!isTombstone) {
    return mk('SKIP', 'skipped', 'branch is gone but the newest deployment has no branch-deletion tombstone (deleted_at unknown)', ex);
  }
  const deletedMs = Date.parse(latest.created_at);
  if (!Number.isFinite(deletedMs)) return mk('SKIP', 'skipped', 'tombstone timestamp unparseable', ex);
  const ageMs = now - deletedMs;
  const ex2 = { ...ex, deletedAt: latest.created_at, ageDays: Math.floor(ageMs / DAY_MS * 100) / 100 };
  if (ageMs < 0) return mk('SKIP', 'skipped', 'tombstone is in the future (clock skew)', ex2);
  if (ageMs < cfg.retentionDays * DAY_MS) {
    return mk('KEEP', 'retention_pending', `branch deleted ${ex2.ageDays}d ago; retention is ${cfg.retentionDays}d`, ex2);
  }

  // --- Cloudflare: Preview must exist under exactly this account + host worker (or be already gone) ---
  const exists = await cloudflare.previewExists({ accountId, worker: hostWorker, name: previewName });
  const plan = { target, previewName, branch, ref, deploymentId: newest.id, tombstoneId: latest.id, exists };
  const reason = exists
    ? `branch deleted ${ex2.ageDays}d ago (>= ${cfg.retentionDays}d); ownership verified`
    : 'Preview already absent on Cloudflare; only the ledger needs closing (idempotent)';
  return { ...mk('ELIGIBLE', 'eligible', reason, ex2), plan };
}

export async function runGc({ cfg, github, cloudflare, now = Date.now(), log = () => {} }) {
  const entries = [];
  const aborted = [];
  const targets = cfg.targets;

  const deployments = await github.listDeployments(); // paginated, newest first; throws GcAbort if incomplete
  const branches = await github.listBranches();
  const namesFor = (list, t) => {
    const s = new Set();
    for (const b of list) {
      if (b === 'main') continue;
      try {
        s.add(previewIdentity({ repo: cfg.repo, worker: t.appName, ref: `refs/heads/${b}`, hostWorker: t.hostWorker }).name);
      } catch { /* not a valid Preview branch */ }
    }
    return s;
  };

  const plans = [];
  for (const target of targets) {
    if (target.accountId !== cfg.credentialAccountId) {
      entries.push(entry(target, '*', { repository: cfg.repo, status: 'ERROR', category: 'failed', reason: `target account ${target.accountId} does not match the account of the supplied credentials: target skipped` }));
      continue;
    }
    const envPrefix = `${target.appName}/preview/`;
    const byName = new Map();
    for (const d of deployments) {
      if (!d.environment?.startsWith(envPrefix)) continue;
      const m = PREVIEW_ENV_RE.exec(d.environment);
      if (!m || m[1] !== target.appName) continue;
      // Ledger records for this env on ANOTHER host worker belong to another target / other Preview: ignored.
      // Records with no host_worker at all are legacy: they poison the group (report only).
      const host = d.payload?.host_worker;
      if (host !== undefined && host !== target.hostWorker) continue;
      if (!byName.has(m[2])) byName.set(m[2], []);
      byName.get(m[2]).push(d);
    }
    const liveNames = namesFor(branches, target);
    for (const [previewName, ds] of byName) {
      if (cfg.only && !cfg.only.includes(previewName)) continue;
      try {
        const r = await classify({ cfg, target, previewName, deployments: ds, github, cloudflare, liveNames, now });
        entries.push(r.entry);
        if (r.plan) plans.push({ ...r.plan, entry: r.entry });
      } catch (e) {
        if (e instanceof GcAbort) throw e;
        entries.push(entry(target, previewName, { repository: cfg.repo, status: 'ERROR', category: 'failed', reason: `classification failed: ${e.message}` }));
      }
    }
    // Protected previews with no ledger at all are still reported (they must be visibly kept).
    for (const p of cfg.protected) {
      if (p.accountId === target.accountId && p.hostWorker === target.hostWorker && !byName.has(p.previewName) && !(cfg.only && !cfg.only.includes(p.previewName))) {
        entries.push(entry(target, p.previewName, { repository: cfg.repo, status: 'KEEP', category: 'protected', reason: 'in the protected_previews allowlist (no branch ledger needed)' }));
      }
    }
  }

  // --- execution: oldest deletion first, bounded, re-verified immediately before each remote mutation ---
  plans.sort((a, b) => Date.parse(a.entry.deleted_at) - Date.parse(b.entry.deleted_at));
  let remoteDeletes = 0;
  const mayDelete = !cfg.dryRun && cfg.enabled;
  let freshBranches = null;
  for (const p of plans) {
    const e = p.entry;
    if (!mayDelete) {
      e.reason += cfg.dryRun ? ' [dry run: no mutation]' : ' [GC disabled: no mutation]';
      continue;
    }
    if (p.exists && remoteDeletes >= cfg.maxDeletions) {
      e.reason += ` [deferred: max_deletions_per_run=${cfg.maxDeletions} reached]`;
      continue;
    }
    try {
      freshBranches ??= await github.listBranches();
      const freshNames = namesFor(freshBranches, p.target);
      const why = await recheck({ cfg, p, github, cloudflare, freshNames });
      if (why) { setSkip(e, why); continue; }
      if (p.exists) {
        await cloudflare.deletePreview({ accountId: p.target.accountId, worker: p.target.hostWorker, name: p.previewName });
        remoteDeletes++;
      }
      e.status = 'DELETE'; e.category = 'deleted';
      e.reason = p.exists ? 'deleted from Cloudflare after re-verification' : 'already absent on Cloudflare (idempotent); ledger closed';
      try {
        await github.recordCleaned(p.deploymentId, `${CLEANED_DESCRIPTION} (${new Date(now).toISOString()})`);
      } catch (err) {
        if (err instanceof GcAbort) throw err;
        e.reason += `; WARNING: ledger write failed (${err.message}), next run re-checks idempotently`;
      }
    } catch (err) {
      if (err instanceof GcAbort) { aborted.push(err.message); e.status = 'ERROR'; e.category = 'failed'; e.reason = `aborted: ${err.message}`; break; }
      e.status = 'ERROR'; e.category = 'failed'; e.reason = `delete failed: ${err.message}`;
    }
  }
  return buildReport({ cfg, entries, aborted, now });
}

function setSkip(e, why) { e.status = 'SKIP'; e.category = 'skipped'; e.reason = `re-check before delete: ${why}`; }

/** Second, independent verification right before the mutation. Returns a reason to skip, or null. */
async function recheck({ cfg, p, github, cloudflare, freshNames }) {
  if (freshNames.has(p.previewName)) return 'a branch normalising to this Preview name exists';
  const newest = await github.getLatestDeployment(`${p.target.appName}/preview/${p.previewName}`);
  if (!newest || newest.id !== p.deploymentId) return 'a newer deployment appeared (concurrent CI deploy)';
  const pl = newest.payload;
  if (!pl || pl.repo !== cfg.repo || pl.branch_ref !== p.ref || pl.host_worker !== p.target.hostWorker || pl.worker !== p.target.appName || pl.preview_name !== p.previewName) return 'ledger ownership changed';
  const [latest] = await github.listStatuses(newest.id);
  if (!latest || latest.id !== p.tombstoneId || latest.description !== TOMBSTONE_DESCRIPTION) return 'ledger state changed since planning';
  const exists = await cloudflare.previewExists({ accountId: p.target.accountId, worker: p.target.hostWorker, name: p.previewName });
  if (exists !== p.exists) return 'Cloudflare Preview state changed since planning';
  // Last check, immediately before the mutation: narrowest possible window for a branch re-creation.
  if (await github.branchExists(p.branch)) return 'branch exists again';
  return null;
}

export function buildReport({ cfg, entries, aborted, now }) {
  const c = (cat) => entries.filter((e) => e.category === cat).length;
  return {
    generated_at: new Date(now).toISOString(),
    repository: cfg.repo,
    mode: !cfg.enabled ? 'disabled' : cfg.dryRun ? 'dry-run' : 'delete',
    retention_days: cfg.retentionDays,
    max_deletions_per_run: cfg.maxDeletions,
    aborted,
    summary: {
      total_discovered: entries.length,
      active: c('active'),
      protected: c('protected'),
      retention_pending: c('retention_pending'),
      eligible: c('eligible'),
      deleted: c('deleted'),
      skipped_unknown: c('skipped'),
      failed: c('failed'),
    },
    entries,
  };
}

export function renderSummary(report) {
  const s = report.summary;
  const esc = (v) => String(v ?? '-').replace(/\|/g, '\\|').replace(/\n/g, ' ');
  const rows = report.entries.map((e) =>
    `| ${esc(e.repository)} | ${esc(e.worker)} | ${esc(e.preview)} | ${esc(e.branch)} | ${esc(e.deleted_at)} | ${esc(e.age_days)} | ${e.status} | ${esc(e.reason)} |`);
  return [
    `## Preview GC — ${report.repository} (${report.mode})`,
    '',
    `Retention ${report.retention_days}d · max deletions/run ${report.max_deletions_per_run}`,
    ...(report.aborted.length ? ['', `**ABORTED:** ${report.aborted.join('; ')}`] : []),
    '',
    '| Category | Count |', '|---|---|',
    `| Total discovered | ${s.total_discovered} |`, `| Active | ${s.active} |`, `| Protected | ${s.protected} |`,
    `| Retention pending | ${s.retention_pending} |`, `| Eligible | ${s.eligible} |`, `| Deleted | ${s.deleted} |`,
    `| Skipped / Unknown | ${s.skipped_unknown} |`, `| Failed | ${s.failed} |`,
    '',
    '| Repository | Worker | Preview | Branch | Deleted At | Age (d) | Status | Reason |', '|---|---|---|---|---|---|---|---|',
    ...rows,
    '',
  ].join('\n');
}
