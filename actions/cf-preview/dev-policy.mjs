// Shared Dev resource policy: audit the EFFECTIVE Dev Wrangler config against the Production (SOURCE top-level)
// config. Identity-aware, fail-closed. Schema reference: https://developers.cloudflare.com/workers/wrangler/configuration/
//
// Precedence (per binding):  1. dev_deny_resources (hard deny)  2. allowlist identity mismatch
//                            3. Production collision (fails unless an exact, supported allow entry matches)
// An allow entry never skips another check on the same Worker (identity, queues, services, routes, ...).
// Production D1 can never be approved. Queues, Service Bindings, Hyperdrive, Secrets Store and Rate-limit
// namespaces cannot be shared through the storage allowlist.

// kind -> identity components of one binding (all must be present). `single` is what an allow entry compares.
const KINDS = {
  d1_databases: { key: (b) => b.binding, ids: (b) => [b.database_id, b.database_name], single: (b) => b.database_id },
  r2_buckets: { key: (b) => b.binding, ids: (b) => [b.bucket_name], single: (b) => b.bucket_name },
  kv_namespaces: { key: (b) => b.binding, ids: (b) => [b.id], single: (b) => b.id },
  vectorize: { key: (b) => b.binding, ids: (b) => [b.index_name], single: (b) => b.index_name },
  analytics_engine_datasets: { key: (b) => b.binding, ids: (b) => [b.dataset], single: (b) => b.dataset },
  pipelines: { key: (b) => b.binding, ids: (b) => [b.pipeline], single: (b) => b.pipeline },
  hyperdrive: { key: (b) => b.binding, ids: (b) => [b.id], single: (b) => b.id },
  secrets_store_secrets: {
    key: (b) => b.binding,
    ids: (b) => [b.store_id && b.secret_name ? `${b.store_id}/${b.secret_name}` : undefined],
    single: (b) => (b.store_id && b.secret_name ? `${b.store_id}/${b.secret_name}` : undefined),
  },
  ratelimits: { key: (b) => b.name, ids: (b) => [b.namespace_id === undefined ? undefined : String(b.namespace_id)], single: (b) => String(b.namespace_id) },
};
// Kinds where application-level isolation (e.g. object-key prefixes) is a reasonable, documented choice.
export const DEV_SHAREABLE_KINDS = ['r2_buckets', 'kv_namespaces', 'vectorize', 'analytics_engine_datasets'];
// Bindings that make the Dev Worker reach other Workers' state or code; not supported by this audit at all.
const UNSUPPORTED = ['workflows', 'dispatch_namespaces', 'mtls_certificates', 'containers', 'tail_consumers', 'unsafe'];

const ENTRY_RE = /^([a-z0-9_]+):([A-Za-z0-9_.-]+)=(\S+)$/;

/** Parse dev_binding_allow tokens ("kind:BINDING=identity"). Returns { entries: Map, errors }. */
export function parseDevAllow(tokens = []) {
  const entries = new Map();
  const errors = [];
  for (const t of tokens) {
    const m = ENTRY_RE.exec(t);
    if (!m) { errors.push(`dev_binding_allow entry "${t}" is malformed; use kind:BINDING=exact-resource-identity`); continue; }
    const [, kind, binding, identity] = m;
    if (kind === 'd1_databases') { errors.push(`dev_binding_allow cannot approve a D1 database (${t}): Production D1 is never shared with Dev`); continue; }
    if (!DEV_SHAREABLE_KINDS.includes(kind) && kind !== 'services') {
      errors.push(`dev_binding_allow kind "${kind}" is not supported (${t}); supported: ${[...DEV_SHAREABLE_KINDS, 'services'].join(', ')}`);
      continue;
    }
    const k = `${kind}:${binding}`;
    if (entries.has(k)) { errors.push(`dev_binding_allow has more than one entry for ${k} (ambiguous)`); continue; }
    entries.set(k, { identity, used: false, token: t });
  }
  return { entries, errors };
}

function prodSets(source) {
  const byKind = {};
  for (const [kind, spec] of Object.entries(KINDS)) {
    byKind[kind] = new Set((source[kind] ?? []).flatMap((b) => spec.ids(b)).filter(Boolean));
  }
  const queues = new Set();
  for (const p of source.queues?.producers ?? []) if (p.queue) queues.add(p.queue);
  for (const c of source.queues?.consumers ?? []) { if (c.queue) queues.add(c.queue); if (c.dead_letter_queue) queues.add(c.dead_letter_queue); }
  const services = new Set(source.name ? [source.name] : []);
  for (const s of source.services ?? []) if (s.service) services.add(s.service);
  return { byKind, queues, services };
}

const hostOf = (pattern) => String(pattern).replace(/^https?:\/\//, '').split('/')[0].replace(/^\*\./, '').toLowerCase();
function routePatterns(cfg) {
  const out = [];
  const add = (r) => { const p = typeof r === 'string' ? r : r?.pattern; if (p) out.push(p); };
  add(cfg.route);
  for (const r of cfg.routes ?? []) add(r);
  return out;
}

export function auditDevConfig(effective, source, { worker = '', denyResources = [], allow = [], prodUrl = '' } = {}) {
  const { entries, errors } = parseDevAllow(allow);
  const deny = new Set(denyResources);
  if (worker && effective.name !== worker) errors.push(`effective Wrangler config targets Worker "${effective.name}", expected the Shared Dev Worker "${worker}"`);
  const prod = prodSets(source);

  for (const [kind, spec] of Object.entries(KINDS)) {
    const seen = new Set();
    for (const b of effective[kind] ?? []) {
      const key = spec.key(b);
      const label = `${kind}:${key}`;
      if (!key) { errors.push(`${kind}: a binding has no name (ambiguous)`); continue; }
      if (seen.has(key)) { errors.push(`${label}: declared more than once (ambiguous)`); continue; }
      seen.add(key);
      const ids = spec.ids(b);
      if (!ids.length || ids.some((i) => !i)) { errors.push(`${label}: resource identity is missing or incomplete (Shared Dev needs an explicit, full identity)`); continue; }
      const hit = ids.find((i) => deny.has(i));
      if (hit) { errors.push(`${label}: binds the hard-denied resource ${hit} (dev_deny_resources)`); continue; }
      const entry = entries.get(label);
      const collides = ids.some((i) => prod.byKind[kind].has(i));
      if (entry) {
        entry.used = true;
        if (!DEV_SHAREABLE_KINDS.includes(kind)) { errors.push(`${label}: kind is not shareable`); continue; }
        if (spec.single(b) !== entry.identity) { errors.push(`${label}: is bound to ${spec.single(b)} but the allowlist approves ${entry.identity} (identity mismatch)`); continue; }
        continue; // exact, approved sharing (Production collision or not)
      }
      if (collides) errors.push(`${label}: Shared Dev binds the Production resource ${ids.find((i) => prod.byKind[kind].has(i))}${DEV_SHAREABLE_KINDS.includes(kind) ? ` (approve explicitly with dev_binding_allow: ${label}=<identity> if sharing is intended)` : kind === 'd1_databases' ? ' (Production D1 can never be shared)' : ' (this kind cannot be shared with Production)'}`);
    }
  }

  // Queues: producers, consumers and dead-letter queues must never be Production queues (no allowlist).
  const q = effective.queues ?? {};
  const queueRefs = [
    ...(q.producers ?? []).map((p) => ['producer', p.queue]),
    ...(q.consumers ?? []).flatMap((c) => [['consumer', c.queue], ...(c.dead_letter_queue !== undefined ? [['dead-letter', c.dead_letter_queue]] : [])]),
  ];
  for (const [role, name] of queueRefs) {
    if (!name) { errors.push(`queues ${role}: queue name is missing (ambiguous)`); continue; }
    if (prod.queues.has(name)) errors.push(`queues ${role} "${name}" is a Production queue; a Dev Worker must not produce to, consume from or dead-letter into it`);
    else if (deny.has(name)) errors.push(`queues ${role} "${name}" is hard-denied (dev_deny_resources)`);
  }

  // Service bindings: default deny. Each needs an exact allow entry and must never target Production.
  const seenSvc = new Set();
  for (const s of effective.services ?? []) {
    const key = s.binding;
    const label = `services:${key}`;
    if (!key || !s.service) { errors.push(`${label}: service binding without a binding name or target service (ambiguous)`); continue; }
    if (seenSvc.has(key)) { errors.push(`${label}: declared more than once (ambiguous)`); continue; }
    seenSvc.add(key);
    const identity = s.environment ? `${s.service}#${s.environment}` : s.service;
    const entry = entries.get(label);
    if (entry) entry.used = true;
    if (prod.services.has(s.service) || deny.has(s.service) || deny.has(identity)) { errors.push(`${label}: targets "${identity}", a Production/denied Worker; this cannot be approved`); continue; }
    if (!entry) { errors.push(`${label}: Service Binding to "${identity}" is not approved (add dev_binding_allow: ${label}=${identity})`); continue; }
    if (entry.identity !== identity) errors.push(`${label}: targets "${identity}" but the allowlist approves "${entry.identity}" (identity mismatch)`);
  }

  // Routes / custom domains must not take over a Production route or host.
  const prodRoutes = new Set(routePatterns(source));
  const prodHost = prodUrl ? hostOf(prodUrl) : '';
  for (const p of routePatterns(effective)) {
    if (prodRoutes.has(p) || (prodHost && hostOf(p) === prodHost)) errors.push(`route "${p}" targets a Production route/host`);
  }

  // Durable Objects from another Worker share that Worker's state.
  for (const d of effective.durable_objects?.bindings ?? []) {
    if (d.script_name && d.script_name !== effective.name) errors.push(`durable_objects:${d.name}: class from another Worker "${d.script_name}" is not supported in Shared Dev`);
  }
  for (const k of UNSUPPORTED) {
    const v = effective[k];
    if (Array.isArray(v) ? v.length : v && typeof v === 'object' ? Object.keys(v).length : v) errors.push(`${k}: not supported by the Shared Dev audit (cannot be verified safely); remove it from the Dev config`);
  }

  for (const [k, e] of entries) if (!e.used) errors.push(`dev_binding_allow entry "${e.token}" matches no binding in the Dev config (unknown or missing binding)`);
  return { errors };
}
