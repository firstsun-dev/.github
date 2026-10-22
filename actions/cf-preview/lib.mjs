// Pure helpers for Cloudflare Worker CI/CD v2 (branch-owned Native Previews).
// No I/O except readConfig(); everything else is deterministic and unit-tested.
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
    throw new Error(`Wrangler ${version} < ${MIN_WRANGLER}: unsupported (Native Previews, d1 migrations); refusing to fall back to Dev/Production`);
  }
}

/**
 * Stable Preview identity: <slug>  (just the branch name: no prefix, no hash, no commit SHA).
 * URL: https://<slug>-<worker>.<subdomain>.workers.dev  — one stable URL per branch, forever.
 *
 * The slug is the normalised branch name, truncated so `<name>-<hostWorker>` fits one 63-char DNS
 * label (Preview hostname = <name>-<worker>.<subdomain>.workers.dev). Because normalisation and
 * truncation are lossy ("Feat/A" and "feat-a" both give `feat-a`), uniqueness is enforced by the
 * GitHub Deployment ledger (see gh.sh `owner`): a name already owned by a different full branch ref
 * fails closed instead of overwriting that Preview.
 * `hostWorker` is the Worker that hosts the Preview (preview_worker_name, else app_name).
 */
export function previewIdentity({ repo, worker, ref, hostWorker }) {
  if (!repo || !worker || !ref) throw new Error('repo, worker and ref are required');
  if (!ref.startsWith('refs/heads/')) throw new Error(`Not a branch ref: ${ref}`);
  const branch = ref.slice('refs/heads/'.length);
  if (branch === 'main') throw new Error('main is Production; it never gets a Preview identity');
  const host = hostWorker || worker;
  const max = Math.min(40, 63 - 1 - host.length);
  if (max < 8) throw new Error(`Worker name too long for a Preview hostname label: ${host}`);
  const slug =
    branch
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, max)
      .replace(/-+$/g, '') || 'branch';
  // An 8-hex slug would look like a per-deployment URL prefix (<8hex>-<worker>); keep them distinct.
  const name = /^[0-9a-f]{8}$/.test(slug) ? `b-${slug}` : slug;
  return { name, slug, branch };
}

export const PREVIEW_NAME_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;

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

/**
 * Which deployment target does this event deploy to? 'prod' | 'preview' | 'none'.
 * Single source of truth shared by the Migration and Deploy jobs (they re-check it fail-closed in addition
 * to their job-level `if`), so a mismatch between the two can never silently select Production.
 */
export function resolveTarget({ event, ref, strategy, previewOnPush = true, devRef = '', dispatchTarget = '' }) {
  if (!String(ref).startsWith('refs/heads/')) return 'none'; // tags, refs/pull/*: never a deploy target
  const r = classifyEvent({ event, ref, strategy, devRef, dispatchTarget });
  if (r.action === 'production') return 'prod';
  if (r.action === 'preview') return previewOnPush ? 'preview' : 'none';
  return 'none';
}

/**
 * Does this event deploy the SHARED persistent Dev Worker? 'dev' | 'none'.
 * Independent of the Native Preview channel: only the Shared Dev switch matters. Never main (Production),
 * never a PR / delete / tag, never the manual persistent-dev dispatch, never a production-only Worker.
 */
export function resolveSharedDev({ event, ref, strategy, sharedDevOnPush = false, dispatchTarget = '' }) {
  if (!STRATEGIES.includes(strategy)) throw new Error(`Unknown deployment_strategy: ${strategy}`);
  if (!sharedDevOnPush || strategy === 'production-only') return 'none';
  if (!String(ref).startsWith('refs/heads/') || ref === 'refs/heads/main') return 'none';
  if (event === 'push') return 'dev';
  if (event === 'workflow_dispatch' && dispatchTarget !== 'persistent-dev') return 'dev';
  return 'none';
}

/** D1 bindings the given target will use, straight from the project's SOURCE Wrangler config. */
export function d1ForTarget(cfg, target, { devEnv = 'dev' } = {}) {
  if (target === 'prod') return cfg.d1_databases ?? [];
  if (target === 'preview') return cfg.previews?.d1_databases ?? [];
  if (target === 'dev') return cfg.env?.[devEnv]?.d1_databases ?? []; // the named Wrangler environment, never top-level
  throw new Error(`Unknown migration target: ${target}`);
}

/**
 * Pick the physical D1 for a Migration job and build a minimal Wrangler config that points ONLY at it.
 * Native Preview D1 comes from `previews.d1_databases` (never the named `env.dev`). Fails closed when the
 * target has no D1, an ambiguous choice, no explicit id, or when the Preview and Production databases are
 * the same physical database.
 */
export function migrationPlan(cfg, target, { binding = '', migrationsDirAbs, devEnv = 'dev' }) {
  const all = d1ForTarget(cfg, target, { devEnv });
  const pool = binding ? all.filter((b) => b.binding === binding) : all;
  if (!pool.length) throw new Error(`No D1 binding${binding ? ` "${binding}"` : ''} declared for target ${target} (${target === 'preview' ? 'previews.d1_databases' : target === 'dev' ? `env.${devEnv}.d1_databases` : 'd1_databases'})`);
  if (pool.length > 1) throw new Error(`Several D1 bindings for target ${target}; set d1_binding to one of: ${pool.map((b) => b.binding).join(', ')}`);
  const db = pool[0];
  if (!db.database_id || !db.database_name) throw new Error(`D1 binding ${db.binding} needs explicit database_id and database_name for target ${target}`);
  // Production is never shared with a non-Production target. Preview and Shared Dev MAY name the same physical
  // D1 (both are non-Production); the run order and the workflow concurrency group keep their migrations apart.
  const other = d1ForTarget(cfg, target === 'prod' ? 'preview' : 'prod');
  if (other.some((o) => o.database_id === db.database_id || o.database_name === db.database_name)) {
    throw new Error(`Target ${target} D1 ${db.database_name} is also declared for ${target === 'prod' ? 'Preview' : 'Production'}; refusing to migrate a database shared across trust boundaries`);
  }
  if (target === 'prod') {
    const dev = d1ForTarget(cfg, 'dev', { devEnv });
    if (dev.some((o) => o.database_id === db.database_id || o.database_name === db.database_name)) {
      throw new Error(`Target prod D1 ${db.database_name} is also declared for Dev; refusing to migrate a database shared across trust boundaries`);
    }
  }
  return {
    binding: db.binding,
    databaseName: db.database_name,
    databaseId: db.database_id,
    config: {
      name: `${cfg.name ?? 'migrate'}-migrate`,
      compatibility_date: cfg.compatibility_date ?? '2025-01-01',
      d1_databases: [{
        binding: db.binding,
        database_name: db.database_name,
        database_id: db.database_id,
        // Keep every supported migration setting (migrations_table, migrations_pattern, ...) as the project wrote it.
        ...Object.fromEntries(Object.entries(db).filter(([k]) => k.startsWith('migrations_') && k !== 'migrations_dir')),
        migrations_dir: migrationsDirAbs(db.migrations_dir ?? 'migrations'),
      }],
    },
  };
}

/** Do the Preview and Shared Dev targets name the same physical D1? (Previews not declared => false.) */
export function sharedPreviewDevD1(cfg, { binding = '', devEnv = 'dev' } = {}) {
  const pick = (list) => (binding ? list.filter((b) => b.binding === binding) : list);
  const prev = pick(d1ForTarget(cfg, 'preview'));
  const dev = pick(d1ForTarget(cfg, 'dev', { devEnv }));
  return dev.some((d) => prev.some((p) => (d.database_id && d.database_id === p.database_id) || (d.database_name && d.database_name === p.database_name)));
}

/**
 * Audit the EFFECTIVE (adapter-generated) config of a Shared Dev build: it must target the expected Dev Worker
 * and must not bind any Production resource (top-level resources of the SOURCE config, plus denylist).
 */
export function auditDevConfig(effective, source, { worker = '', denyResources = [], allow = [] } = {}) {
  const errors = [];
  const allowed = new Set(allow);
  for (const a of allowed) {
    if (!/^[a-z0-9_]+:[A-Za-z0-9_.-]+$/.test(a)) errors.push(`dev_binding_allow entry "${a}" must look like kind:BINDING`);
    else if (a.startsWith('d1_databases:')) errors.push(`dev_binding_allow cannot allow a D1 database (${a}): Shared Dev never shares a D1 with Production`);
  }
  if (worker && effective.name !== worker) errors.push(`effective Wrangler config targets Worker "${effective.name}", expected the Shared Dev Worker "${worker}"`);
  const prod = topLevelResourceIds(source);
  const deny = new Set(denyResources);
  for (const [kind, ident] of Object.entries(RESOURCE_KINDS)) {
    for (const b of effective[kind] ?? []) {
      if (allowed.has(`${kind}:${bindingKey(kind, b)}`) && kind !== 'd1_databases') continue; // explicit, documented exception
      for (const id of ident(b).filter(Boolean)) {
        if (prod.has(id)) errors.push(`${kind}:${bindingKey(kind, b)}: Shared Dev binds the Production resource ${id}`);
        else if (deny.has(id)) errors.push(`${kind}:${bindingKey(kind, b)}: Shared Dev binds denylisted Production resource ${id}`);
      }
    }
  }
  return { errors };
}

/** Resource ids declared at the top level of a config, per binding kind (flat set). */
function topLevelResourceIds(cfg) {
  const ids = new Set();
  for (const [kind, ident] of Object.entries(RESOURCE_KINDS)) {
    for (const b of cfg[kind] ?? []) ident(b).filter(Boolean).forEach((id) => ids.add(id));
  }
  return ids;
}

/**
 * Production must never bind a Preview resource. `source` is the project's wrangler config, `effective`
 * the build-generated one (Astro/other adapters can add bindings the source lacks).
 */
export function auditProdConfig(effective, source) {
  const errors = [];
  const previewIds = new Set();
  for (const [kind, ident] of Object.entries(RESOURCE_KINDS)) {
    for (const b of source.previews?.[kind] ?? []) ident(b).filter(Boolean).forEach((id) => previewIds.add(id));
  }
  for (const id of topLevelResourceIds(effective)) {
    if (previewIds.has(id)) errors.push(`Production config binds Preview resource ${id}`);
  }
  return { errors, warnings: [] };
}
