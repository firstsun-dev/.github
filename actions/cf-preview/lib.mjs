// Pure helpers for Cloudflare Worker CI/CD v2 (branch-owned Native Previews).
// No I/O except readConfig(); everything else is deterministic and unit-tested.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { parse as parseToml } from 'smol-toml';
import { parse as parseJsonc } from 'jsonc-parser';

export const STRATEGIES = ['production-preview', 'hybrid', 'production-only'];
export const MIN_WRANGLER = '4.135.0';

/** Compare dotted numeric versions; returns <0, 0, >0. Ignores prerelease tags. */
export function compareVersions(a, b) {
  const pa = String(a).split('-')[0].split('.').map(Number);
  const pb = String(b).split('-')[0].split('.').map(Number);
  for (let i = 0; i < 3; i++) {
    const d = (pa[i] || 0) - (pb[i] || 0);
    if (d) return d;
  }
  return 0;
}

export function assertWranglerSupported(version) {
  if (!/^\d+\.\d+\.\d+/.test(String(version))) {
    throw new Error(`Cannot parse Wrangler version: ${JSON.stringify(version)}`);
  }
  if (compareVersions(version, MIN_WRANGLER) < 0) {
    throw new Error(`Wrangler ${version} < ${MIN_WRANGLER}: Native Previews unsupported; refusing to fall back to Dev/Production`);
  }
}

/**
 * Stable Preview identity: br-<slug>-<hash10>.
 * Hash covers repo + worker + FULL ref so case/punctuation normalisation can
 * never collide ("Feat/A" vs "feat-a") and same branch in two Workers differ.
 */
export function previewIdentity({ repo, worker, ref }) {
  if (!repo || !worker || !ref) throw new Error('repo, worker and ref are required');
  if (!ref.startsWith('refs/heads/')) throw new Error(`Not a branch ref: ${ref}`);
  const branch = ref.slice('refs/heads/'.length);
  if (branch === 'main') throw new Error('main is Production; it never gets a Preview identity');
  const slug =
    branch
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 24)
      .replace(/-+$/g, '') || 'branch';
  const hash = createHash('sha256').update(`${repo}\n${worker}\n${ref}`).digest('hex').slice(0, 10);
  return { name: `br-${slug}-${hash}`, slug, hash, branch };
}

/** Validate `wrangler preview --json` output and return the stable URL. */
export function extractPreviewUrl(json) {
  let data = json;
  if (typeof json === 'string') {
    try {
      data = JSON.parse(json);
    } catch {
      // wrangler may print log lines before the JSON document; take the last top-level object.
      const start = json.lastIndexOf('\n{');
      data = JSON.parse(json.slice(start >= 0 ? start + 1 : json.indexOf('{')));
    }
  }
  const url = data?.preview?.urls?.[0];
  if (typeof url !== 'string') throw new Error('Unexpected wrangler preview --json shape: .preview.urls[0] missing');
  let u;
  try {
    u = new URL(url);
  } catch {
    throw new Error(`Preview URL is not a valid URL: ${url}`);
  }
  if (u.protocol !== 'https:') throw new Error(`Preview URL must be https: ${url}`);
  return u.origin + (u.pathname === '/' ? '' : u.pathname);
}

/**
 * Event classification. Returns { action, reason }.
 * action: preview | ci-only | cleanup | production | persistent-dev | skip
 */
export function classifyEvent({ event, ref, strategy, isFork = false, baseRef = '', deletedRefType = '', devRef = '', dispatchTarget = '' }) {
  if (!STRATEGIES.includes(strategy)) throw new Error(`Unknown deployment_strategy: ${strategy}`);
  if (!ref || ref === 'refs/heads/') return { action: 'skip', reason: 'not a branch ref (tag or other)' };
  const isMain = ref === 'refs/heads/main';
  switch (event) {
    case 'pull_request':
      return { action: 'ci-only', reason: isFork ? 'fork PR: no credentials' : 'PR: CI/report only; push owns Preview lifecycle' };
    case 'delete':
      if (deletedRefType !== 'branch') return { action: 'skip', reason: 'not a branch deletion' };
      if (isMain) return { action: 'skip', reason: 'refusing to clean up main' };
      return strategy === 'production-only'
        ? { action: 'skip', reason: 'production-only has no Preview' }
        : { action: 'cleanup', reason: 'branch deleted' };
    case 'push':
      if (isMain) return { action: 'production', reason: 'main push' };
      if (strategy === 'production-only') return { action: 'ci-only', reason: 'production-only: no Preview' };
      return { action: 'preview', reason: 'non-main push' };
    case 'workflow_dispatch':
      if (dispatchTarget === 'persistent-dev') {
        if (strategy !== 'hybrid') return { action: 'skip', reason: 'persistent Dev requires hybrid' };
        if (!devRef || ref !== devRef) return { action: 'skip', reason: 'ref is not the approved persistent_dev_ref' };
        return { action: 'persistent-dev', reason: 'approved dispatch' };
      }
      if (isMain) return { action: 'production', reason: 'dispatch on main' };
      return strategy === 'production-only'
        ? { action: 'ci-only', reason: 'production-only' }
        : { action: 'preview', reason: 'dispatch on branch' };
    default:
      return { action: 'skip', reason: `unhandled event ${event}` };
  }
}

export function readConfig(path) {
  const text = readFileSync(path, 'utf8');
  if (path.endsWith('.toml')) return parseToml(text);
  const errors = [];
  const cfg = parseJsonc(text, errors, { allowTrailingComma: true });
  if (errors.length) throw new Error(`Cannot parse ${path}: error code ${errors[0].error}`);
  return cfg;
}

// Binding kinds -> how to read each resource's identity.
const RESOURCE_KINDS = {
  d1_databases: (b) => [b.database_id, b.database_name],
  r2_buckets: (b) => [b.bucket_name],
  kv_namespaces: (b) => [b.id],
  hyperdrive: (b) => [b.id],
  vectorize: (b) => [b.index_name],
  secrets_store_secrets: (b) => [b.store_id && `${b.store_id}/${b.secret_name}`],
  analytics_engine_datasets: (b) => [b.dataset],
  pipelines: (b) => [b.pipeline],
};
// Kinds that can reach Production state and are never allowed without an explicit allowlist entry.
const HIGH_RISK = ['services', 'hyperdrive', 'secrets_store_secrets'];

function bindingKey(kind, b) {
  return b.binding ?? b.name ?? JSON.stringify(b);
}

function queueProducers(cfg) {
  return cfg?.queues?.producers ?? [];
}

/**
 * Audit a parsed Wrangler config for Preview safety. Returns { errors, warnings }.
 * `allow` is a list of "kind:binding" strings the caller has explicitly approved
 * (e.g. "services:ID_SERVICE"), `denyResources` extra Production identities to reject.
 */
export function auditPreviewConfig(cfg, { allow = [], denyResources = [] } = {}) {
  const errors = [];
  const warnings = [];
  const allowed = new Set(allow);
  const deny = new Set(denyResources);
  const pv = cfg.previews;

  if (!pv || typeof pv !== 'object') {
    errors.push('missing top-level `previews` block (Previews do not inherit production settings; use `previews = {}` only if no storage/data bindings exist)');
    return { errors, warnings };
  }

  for (const k of ['routes', 'route', 'triggers', 'crons']) {
    if (k in pv) errors.push(`previews.${k} is not allowed: Cron/routes do not target Previews`);
  }
  if (pv.queues?.consumers?.length) errors.push('previews.queues.consumers is not allowed: consumers do not target Previews');
  if (pv.tail_consumers?.length) warnings.push('previews.tail_consumers set: confirm destination is non-production');
  for (const k of ['assets', 'compatibility_date', 'compatibility_flags']) {
    if (k in pv) errors.push(`previews.${k} must live at top level`);
  }

  for (const [kind, ids] of Object.entries(RESOURCE_KINDS)) {
    const prod = (cfg[kind] ?? []);
    const prev = (pv[kind] ?? []);
    const prodIds = new Set(prod.flatMap((b) => ids(b)).filter(Boolean));
    for (const b of prev) {
      const key = `${kind}:${bindingKey(kind, b)}`;
      const mine = ids(b).filter(Boolean);
      if (!mine.length) errors.push(`${key}: Preview binding needs an explicit resource id (Native Previews do not auto-provision; Cloudflare rejects it with 10021)`);
      for (const id of mine) {
        if (prodIds.has(id)) errors.push(`${key}: Preview binds the same resource as Production (${id})`);
        if (deny.has(id)) errors.push(`${key}: Preview binds denylisted Production resource (${id})`);
      }
      if (HIGH_RISK.includes(kind) && !allowed.has(key)) {
        errors.push(`${key}: high-risk binding needs explicit allow entry "${key}"`);
      }
    }
    for (const b of prod) {
      const name = bindingKey(kind, b);
      if (!prev.some((p) => bindingKey(kind, p) === name)) {
        errors.push(`${kind}:${name}: Production binding has no Preview counterpart; add one pointing at a Preview-safe resource`);
      }
    }
  }

  const prodQ = queueProducers(cfg);
  const prevQ = queueProducers(pv);
  for (const q of prevQ) {
    if (prodQ.some((p) => p.queue === q.queue)) errors.push(`queues.producers:${q.binding}: Preview produces to Production queue ${q.queue}`);
    if (deny.has(q.queue)) errors.push(`queues.producers:${q.binding}: denylisted queue ${q.queue}`);
  }
  for (const q of prodQ) {
    if (!prevQ.some((p) => p.binding === q.binding)) errors.push(`queues.producers:${q.binding}: no Preview counterpart`);
  }

  for (const s of pv.services ?? []) {
    const key = `services:${bindingKey('services', s)}`;
    if (!allowed.has(key)) {
      errors.push(`${key}: Preview service bindings target the other Worker's PRODUCTION deployment; needs explicit allow entry "${key}"`);
    }
  }
  for (const s of cfg.services ?? []) {
    if (!(pv.services ?? []).some((p) => p.binding === s.binding)) {
      errors.push(`services:${s.binding}: Production service binding has no Preview counterpart (deny, mock, or allow explicitly)`);
    }
  }

  // Heuristic secret/URL scan of previews.vars: obvious Production endpoints.
  for (const [k, v] of Object.entries(pv.vars ?? {})) {
    if (typeof v === 'string' && deny.has(v)) errors.push(`previews.vars.${k}: value is a denylisted Production endpoint`);
  }

  return { errors, warnings };
}

/** Is `candidateSha` still the tip of the branch? Guards stale/out-of-order runs. */
export function isStale({ runSha, branchTipSha }) {
  return !branchTipSha || runSha !== branchTipSha;
}
