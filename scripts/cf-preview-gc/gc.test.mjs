import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runGc, renderSummary, GcAbort, TOMBSTONE_DESCRIPTION, DAY_MS } from '../../actions/cf-preview-gc/gc.mjs';
import { githubClient, cloudflareClient } from '../../actions/cf-preview-gc/clients.mjs';
import { loadConfig } from '../../actions/cf-preview-gc/cli.mjs';

const REPO = 'acme/app';
const NOW = Date.parse('2026-10-28T00:00:00Z');
const iso = (daysAgo) => new Date(NOW - daysAgo * DAY_MS).toISOString();
const T = { appName: 'blog', hostWorker: 'blog-preview', accountId: 'acc1' };

const payload = (branch, over = {}) => ({ repo: REPO, worker: 'blog', host_worker: 'blog-preview', preview_name: branch.toLowerCase().replace(/[^a-z0-9]+/g, '-'), branch_ref: `refs/heads/${branch}`, ...over });
let nextId = 1;
/** deployment + its statuses (newest first) */
const dep = (branch, statuses, over = {}) => ({
  id: nextId++, environment: `blog/preview/${over.name ?? payload(branch).preview_name}`, payload: payload(branch, over.payload), statuses, ...over.dep,
});
const tomb = (daysAgo, extra = {}) => ({ id: 900 + nextId, state: 'inactive', description: TOMBSTONE_DESCRIPTION, created_at: iso(daysAgo), creator: { login: 'github-actions[bot]' }, ...extra });
const ok = () => ({ id: 800 + nextId, state: 'success', description: 'Serving x (content)', created_at: iso(30), creator: { login: 'github-actions[bot]' } });

function world({ deployments = [], branches = [], cfPreviews = [], hooks = {} } = {}) {
  const calls = [];
  const w = {
    calls, deployments, branches: new Set(branches), cf: new Set(cfPreviews),
    github: {
      async listDeployments() { calls.push('gh:list'); return w.deployments; },
      async listBranches() { return [...w.branches]; },
      async branchExists(b) { return w.branches.has(b); },
      async getLatestDeployment(env) { hooks.beforeRecheck?.(w); return w.deployments.find((d) => d.environment === env) ?? null; },
      async listStatuses(id) { return w.deployments.find((d) => d.id === id).statuses; },
      async recordCleaned(id, desc) { calls.push(`gh:record:${id}`); w.deployments.find((d) => d.id === id).statuses.unshift({ id: 5000, state: 'inactive', description: desc, created_at: iso(0), creator: { login: 'github-actions[bot]' } }); },
    },
    cloudflare: {
      async previewExists({ accountId, worker, name }) { return w.cf.has(`${accountId}/${worker}/${name}`); },
      async deletePreview({ accountId, worker, name }) { calls.push(`cf:delete:${accountId}/${worker}/${name}`); w.cf.delete(`${accountId}/${worker}/${name}`); },
    },
  };
  return w;
}
const cfg = (over = {}) => ({ repo: REPO, enabled: true, dryRun: false, retentionDays: 7, maxDeletions: 5, only: null, targets: [T], protected: [], credentialAccountId: 'acc1', tombstoneCreators: ['github-actions[bot]'], ...over });
const run = (w, c = cfg()) => runGc({ cfg: c, github: w.github, cloudflare: w.cloudflare, now: NOW });
const cfKey = (name) => `acc1/blog-preview/${name}`;
const byPreview = (r, n) => r.entries.find((e) => e.preview === n);
const deletes = (w) => w.calls.filter((c) => c.startsWith('cf:delete'));

test('active branch is never deleted', async () => {
  const w = world({ deployments: [dep('feat-a', [ok()])], branches: ['feat-a'], cfPreviews: [cfKey('feat-a')] });
  const r = await run(w);
  assert.equal(byPreview(r, 'feat-a').status, 'KEEP');
  assert.equal(r.summary.active, 1);
  assert.equal(deletes(w).length, 0);
});

test('PR merged / closed but branch still exists: kept (branch liveness is the only signal)', async () => {
  const w = world({ deployments: [dep('merged', [ok()])], branches: ['merged'], cfPreviews: [cfKey('merged')] });
  assert.equal(byPreview(await run(w), 'merged').reason, 'branch still exists');
});

test('deleted 6 days: retention pending; 7 days: deleted', async () => {
  const w = world({ deployments: [dep('six', [tomb(6)]), dep('seven', [tomb(7)])], cfPreviews: [cfKey('six'), cfKey('seven')] });
  const r = await run(w);
  assert.equal(byPreview(r, 'six').category, 'retention_pending');
  assert.equal(byPreview(r, 'seven').status, 'DELETE');
  assert.deepEqual(deletes(w), [`cf:delete:${cfKey('seven')}`]);
  assert.ok(w.cf.has(cfKey('six')));
});

test('retention counts from the tombstone, not from the (older) deploy time', async () => {
  const w = world({ deployments: [dep('old', [tomb(1), ok()])], cfPreviews: [cfKey('old')] });
  assert.equal(byPreview(await run(w), 'old').category, 'retention_pending');
});

test('fixed Dev preview in the allowlist is never deleted, even without a branch or ledger', async () => {
  const w = world({ deployments: [dep('dev', [tomb(400)])], cfPreviews: [cfKey('dev'), cfKey('stable')] });
  const prot = [{ accountId: 'acc1', hostWorker: 'blog-preview', previewName: 'dev' }, { accountId: 'acc1', hostWorker: 'blog-preview', previewName: 'stable' }];
  const r = await run(w, cfg({ protected: prot }));
  assert.equal(deletes(w).length, 0);
  assert.equal(r.summary.protected, 2);
});

test('the reserved Native Dev name `dev` is never collected, with NO allowlist entry (a legacy `dev` branch ledger cannot delete it)', async () => {
  const w = world({ deployments: [dep('dev', [tomb(400)])], cfPreviews: [cfKey('dev')] });
  const r = await run(w);
  assert.equal(deletes(w).length, 0);
  assert.equal(r.summary.protected, 1);
});

test('allowlist matches the full account + worker + name tuple', async () => {
  const w = world({ deployments: [dep('stable', [tomb(40)])], cfPreviews: [cfKey('stable')] });
  const r = await run(w, cfg({ protected: [{ accountId: 'other', hostWorker: 'blog-preview', previewName: 'stable' }] }));
  assert.equal(byPreview(r, 'stable').status, 'DELETE'); // a different account's allowlist entry does not protect this one
});

test('branch deleted then recreated (before or after a new push): not deleted', async () => {
  const w1 = world({ deployments: [dep('again', [tomb(30)])], branches: ['again'], cfPreviews: [cfKey('again')] });
  assert.equal(byPreview(await run(w1), 'again').category, 'active');
  const w2 = world({ deployments: [dep('again', [ok()]), dep('again', [tomb(30)])], cfPreviews: [cfKey('again')] });
  const r = await run(w2);
  assert.equal(deletes(w2).length, 0);
  assert.equal(byPreview(r, 'again').category, 'skipped'); // newest deployment has no tombstone
});

test('multiple deployments of one branch: newest decides', async () => {
  const newest = dep('multi', [tomb(8)]);
  const w = world({ deployments: [newest, dep('multi', [{ ...tomb(9), id: 1 }]), dep('multi', [ok()])], cfPreviews: [cfKey('multi')] });
  const r = await run(w);
  assert.equal(r.entries.filter((e) => e.preview === 'multi').length, 1);
  assert.equal(byPreview(r, 'multi').status, 'DELETE');
});

test('an ordinary superseded inactive deployment is not a tombstone', async () => {
  const w = world({ deployments: [dep('sup', [{ id: 1, state: 'inactive', description: 'Superseded', created_at: iso(60), creator: { login: 'github-actions[bot]' } }])], cfPreviews: [cfKey('sup')] });
  const r = await run(w);
  assert.equal(byPreview(r, 'sup').category, 'skipped');
  assert.equal(deletes(w).length, 0);
});

test('tombstone written by a non-bot user is not trusted', async () => {
  const w = world({ deployments: [dep('imp', [tomb(30, { creator: { login: 'mallory' } })])], cfPreviews: [cfKey('imp')] });
  await run(w);
  assert.equal(deletes(w).length, 0);
});

test('two branches normalising to the same slug: fail closed', async () => {
  const a = dep('Feat/A', [tomb(30)], { name: 'feat-a' });
  const b = dep('feat-a', [tomb(30)], { name: 'feat-a' });
  const w = world({ deployments: [a, b], cfPreviews: [cfKey('feat-a')] });
  const r = await run(w);
  assert.match(byPreview(r, 'feat-a').reason, /different branch refs/);
  assert.equal(deletes(w).length, 0);
});

test('another live branch normalising to the same name keeps the Preview', async () => {
  const w = world({ deployments: [dep('Feat/A', [tomb(30)], { name: 'feat-a' })], branches: ['feat-a'], cfPreviews: [cfKey('feat-a')] });
  const r = await run(w);
  assert.equal(byPreview(r, 'feat-a').category, 'active');
  assert.equal(deletes(w).length, 0);
});

test('other repository / worker / host / app ledger records never match', async () => {
  const cases = [{ repo: 'evil/app' }, { worker: 'other' }, { host_worker: 'elsewhere' }, { preview_name: 'zzz' }];
  for (const over of cases) {
    const w = world({ deployments: [dep('x', [tomb(30)], { payload: over })], cfPreviews: [cfKey('x')] });
    await run(w);
    assert.equal(deletes(w).length, 0, JSON.stringify(over));
  }
});

test('target account differing from the credentials account is an ERROR and never touched', async () => {
  const w = world({ deployments: [dep('x', [tomb(30)])], cfPreviews: [cfKey('x')] });
  const r = await run(w, cfg({ credentialAccountId: 'accOther' }));
  assert.equal(r.summary.failed, 1);
  assert.equal(deletes(w).length, 0);
});

test('legacy record without payload / without host_worker: report only', async () => {
  const noPayload = { id: 77, environment: 'blog/preview/old', payload: null, statuses: [tomb(30)] };
  const noHost = dep('old2', [tomb(30)], { payload: { host_worker: undefined } });
  const w = world({ deployments: [noPayload, noHost], cfPreviews: [cfKey('old'), cfKey('old2')] });
  const r = await run(w);
  assert.equal(r.summary.skipped_unknown, 2);
  assert.equal(deletes(w).length, 0);
});

test('Preview already absent on Cloudflare: idempotent, ledger closed, no remote delete', async () => {
  const w = world({ deployments: [dep('gone', [tomb(30)])] });
  const r = await run(w);
  assert.equal(byPreview(r, 'gone').status, 'DELETE');
  assert.equal(deletes(w).length, 0);
  assert.ok(w.calls.some((c) => c.startsWith('gh:record')));
  const r2 = await run(w); // recorded: never retried
  assert.equal(byPreview(r2, 'gone').category, 'skipped');
});

test('dry run performs zero mutations and reports ELIGIBLE', async () => {
  const w = world({ deployments: [dep('d', [tomb(30)])], cfPreviews: [cfKey('d')] });
  const r = await run(w, cfg({ dryRun: true }));
  assert.equal(byPreview(r, 'd').status, 'ELIGIBLE');
  assert.equal(r.mode, 'dry-run');
  assert.deepEqual(w.calls.filter((c) => c.startsWith('cf:') || c.startsWith('gh:record')), []);
});

test('disabled GC never mutates', async () => {
  const w = world({ deployments: [dep('d', [tomb(30)])], cfPreviews: [cfKey('d')] });
  await run(w, cfg({ enabled: false, dryRun: false }));
  assert.equal(deletes(w).length, 0);
});

test('max_deletions_per_run bounds remote deletes, oldest first', async () => {
  const ds = ['a', 'b', 'c', 'd'].map((n, i) => dep(n, [tomb(10 + i)]));
  const w = world({ deployments: ds, cfPreviews: ['a', 'b', 'c', 'd'].map(cfKey) });
  const r = await run(w, cfg({ maxDeletions: 2 }));
  assert.deepEqual(deletes(w), [`cf:delete:${cfKey('d')}`, `cf:delete:${cfKey('c')}`]);
  assert.equal(r.summary.deleted, 2);
  assert.equal(r.summary.eligible, 2);
});

test('only_previews restricts a controlled cleanup', async () => {
  const w = world({ deployments: [dep('a', [tomb(30)]), dep('b', [tomb(30)])], cfPreviews: [cfKey('a'), cfKey('b')] });
  await run(w, cfg({ only: ['b'] }));
  assert.deepEqual(deletes(w), [`cf:delete:${cfKey('b')}`]);
});

test('concurrent CI deploy / branch recreation between plan and delete: skipped', async () => {
  const mk = (hook) => world({ deployments: [dep('race', [tomb(30)])], cfPreviews: [cfKey('race')], hooks: { beforeRecheck: hook } });
  const w1 = mk((w) => w.deployments.unshift(dep('race', [{ id: 1, state: 'in_progress', description: '', created_at: iso(0), creator: { login: 'github-actions[bot]' } }])));
  assert.match(byPreview(await run(w1), 'race').reason, /newer deployment/);
  assert.equal(deletes(w1).length, 0);
  const w2 = mk((w) => w.branches.add('race'));
  assert.match(byPreview(await run(w2), 'race').reason, /branch exists again/);
  assert.equal(deletes(w2).length, 0);
});

test('Cloudflare state changing between plan and delete skips', async () => {
  const w = world({ deployments: [dep('cfrace', [tomb(30)])], cfPreviews: [cfKey('cfrace')], hooks: { beforeRecheck: (x) => x.cf.clear() } });
  await run(w);
  assert.equal(deletes(w).length, 0);
});

test('production Worker is never a target: only <app>/preview/<name> ledger envs are considered', async () => {
  const prod = { id: 5, environment: 'blog/production', payload: payload('main'), statuses: [tomb(99)] };
  const dev = { id: 6, environment: 'blog/dev/shared', payload: payload('dev'), statuses: [tomb(99)] };
  const w = world({ deployments: [prod, dev], cfPreviews: [cfKey('main'), cfKey('dev'), 'acc1/blog/blog'] });
  const r = await run(w);
  assert.equal(r.entries.length, 0);
  assert.equal(w.calls.filter((c) => c.startsWith('cf:')).length, 0);
});

test('GitHub/Cloudflare abort errors stop the run before further deletes', async () => {
  const w = world({ deployments: [dep('a', [tomb(30)]), dep('b', [tomb(31)])], cfPreviews: [cfKey('a'), cfKey('b')] });
  w.cloudflare.previewExists = async () => { throw new GcAbort('Cloudflare API 429'); };
  await assert.rejects(run(w), GcAbort);
  assert.equal(deletes(w).length, 0);
});

test('summary renders every column and category', async () => {
  const w = world({ deployments: [dep('s', [tomb(30)])], cfPreviews: [cfKey('s')] });
  const md = renderSummary(await run(w, cfg({ dryRun: true })));
  for (const s of ['Total discovered', 'Retention pending', 'Skipped / Unknown', 'Deleted At', 'ELIGIBLE', 'blog-preview']) assert.ok(md.includes(s), s);
});

// ---- real clients against a fake fetch ----
const resp = (status, body, headers = {}) => ({ status, ok: status >= 200 && status < 300, headers: { get: (k) => headers[k.toLowerCase()] ?? null }, json: async () => body });

test('github client paginates via Link headers and aborts on rate limit', async () => {
  const urls = [];
  const fetchImpl = async (u) => {
    urls.push(u);
    if (u.endsWith('page=2')) return resp(200, [{ id: 2 }]);
    return resp(200, [{ id: 1 }], { link: '<https://api.github.com/repos/acme/app/deployments?per_page=100&page=2>; rel="next"' });
  };
  const gh = githubClient({ repo: REPO, token: 't', fetchImpl });
  assert.deepEqual((await gh.listDeployments()).map((d) => d.id), [1, 2]);
  const limited = githubClient({ repo: REPO, token: 't', fetchImpl: async () => resp(429, {}) });
  await assert.rejects(limited.listBranches(), GcAbort);
  const err500 = githubClient({ repo: REPO, token: 't', fetchImpl: async () => resp(500, {}) });
  await assert.rejects(err500.listBranches(), /500/);
});

test('github client: 404 branch => false, 403 => abort', async () => {
  assert.equal(await githubClient({ repo: REPO, token: 't', fetchImpl: async () => resp(404, {}) }).branchExists('x'), false);
  await assert.rejects(githubClient({ repo: REPO, token: 't', fetchImpl: async () => resp(403, {}) }).branchExists('x'), GcAbort);
});

test('cloudflare client: 404 absent, 200 present, mismatched name / 429 fail closed', async () => {
  const mk = (r) => cloudflareClient({ token: 'secret-token', fetchImpl: async () => r });
  const a = { accountId: 'acc1', worker: 'w', name: 'n' };
  assert.equal(await mk(resp(404, {})).previewExists(a), false);
  assert.equal(await mk(resp(200, { result: { name: 'n' } })).previewExists(a), true);
  await assert.rejects(mk(resp(200, { result: { name: 'other' } })).previewExists(a), /returned Preview/);
  await assert.rejects(mk(resp(429, {})).previewExists(a), GcAbort);
  await assert.rejects(mk(resp(500, {})).previewExists(a), /500/);
});

test('cloudflare delete uses `wrangler preview delete` with explicit worker, never `wrangler delete`, and redacts the token', async () => {
  let argv; let env;
  const spawnImpl = (cmd, args, opts) => {
    argv = [cmd, ...args]; env = opts.env;
    const handlers = {};
    const child = { stderr: { on: (_e, f) => f('boom secret-token') }, on: (e, f) => { handlers[e] = f; if (e === 'close') setImmediate(() => f(1)); } };
    return child;
  };
  const cf = cloudflareClient({ token: 'secret-token', spawnImpl });
  await assert.rejects(cf.deletePreview({ accountId: 'acc1', worker: 'blog-preview', name: 'x' }), (e) => !e.message.includes('secret-token') && /exited 1/.test(e.message));
  assert.ok(argv.join(' ').includes('preview delete --name x --worker-name blog-preview --skip-confirmation'));
  assert.ok(!argv.includes('delete') || argv[argv.indexOf('delete') - 1] === 'preview');
  assert.equal(env.CLOUDFLARE_ACCOUNT_ID, 'acc1');
});

test('loadConfig: safe defaults and validation', () => {
  const base = { GITHUB_REPOSITORY: REPO, CLOUDFLARE_ACCOUNT_ID: 'acc1', GC_TARGETS: JSON.stringify([{ app_name: 'blog', account_id: 'acc1' }]) };
  const saved = { ...process.env };
  try {
    for (const k of Object.keys(process.env)) if (k.startsWith('GC_')) delete process.env[k];
    Object.assign(process.env, base);
    const c = loadConfig();
    assert.equal(c.enabled, false);
    assert.equal(c.dryRun, true);
    assert.equal(c.retentionDays, 7);
    assert.equal(c.maxDeletions, 5);
    assert.equal(c.targets[0].hostWorker, 'blog');
    process.env.GC_RETENTION_DAYS = '0';
    assert.throws(loadConfig, /retention_days/);
    process.env.GC_RETENTION_DAYS = '7';
    process.env.GC_PROTECTED_PREVIEWS = JSON.stringify([{ preview_name: 'dev' }]);
    assert.throws(loadConfig, /protected_previews/);
  } finally {
    for (const k of Object.keys(process.env)) if (!(k in saved)) delete process.env[k];
    Object.assign(process.env, saved);
  }
});
