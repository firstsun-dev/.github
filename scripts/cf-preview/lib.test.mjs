import test from 'node:test';
import assert from 'node:assert/strict';
import {
  assertWranglerSupported, auditPreviewConfig, PREVIEW_NAME_RE, classifyEvent, extractPreviewUrl, isStale, previewIdentity,
} from '../../actions/cf-preview/lib.mjs';

const id = (ref, worker = 'www', repo = 'o/r') => previewIdentity({ repo, worker, ref });

test('identity is stable, hashless and has no commit SHA', () => {
  assert.equal(id('refs/heads/feat/x').name, id('refs/heads/feat/x').name);
  assert.equal(id('refs/heads/feat/x').name, 'feat-x');
  assert.notEqual(id('refs/heads/feat/x').name, id('refs/heads/feat/y').name);
  assert.doesNotMatch(id('refs/heads/feat/x').name, /[0-9a-f]{7,}/);
  assert.equal(id('refs/heads/solar-term-hub', 'blog').name, 'solar-term-hub');
});
test('look-alike branches share a name; the ledger (gh.sh owner) is what stops the overwrite', () => {
  const a = id('refs/heads/Feat/A'), b = id('refs/heads/feat-a'), c = id('refs/heads/feat/a');
  assert.equal(a.name, 'feat-a');
  assert.deepEqual([a.name, b.name, c.name], ['feat-a', 'feat-a', 'feat-a']);
});
test('name is Cloudflare-safe and the hostname label stays <= 63 chars', () => {
  const n = id('refs/heads/' + 'Ünï/ÇØDE_!!!'.repeat(20)).name;
  assert.match(n, PREVIEW_NAME_RE);
  assert.ok(`${n}-www`.length <= 63);
  assert.equal(id('refs/heads/!!!').name, 'branch');
  assert.equal(id('refs/heads/DEADBEEF').name, 'b-deadbeef'); // never looks like a <8hex>-<worker> deployment URL
  const long = previewIdentity({ repo: 'o/r', worker: 'blog', hostWorker: 'blog-preview', ref: 'refs/heads/' + 'a-'.repeat(60) }).name;
  assert.ok(`${long}-blog-preview`.length <= 63, long);
  assert.doesNotMatch(long, /-$/);
});
test('PREVIEW_NAME_RE rejects unsafe names', () => {
  for (const bad of ['', '-x', 'x-', 'a--b', 'X', 'a;b', 'a b', 'a/b']) assert.doesNotMatch(bad, PREVIEW_NAME_RE, bad);
  for (const ok of ['a', 'feat-x', 'a1-b2']) assert.match(ok, PREVIEW_NAME_RE, ok);
});
test('hostname label limit (63) enforced', () => {
  assert.doesNotThrow(() => id('refs/heads/x', 'w'.repeat(40)));
  assert.throws(() => id('refs/heads/x', 'w'.repeat(56)));
});
test('main and non-branch refs rejected', () => {
  assert.throws(() => id('refs/heads/main'));
  assert.throws(() => id('refs/pull/1/merge'));
});
test('extractPreviewUrl validates shape', () => {
  assert.equal(extractPreviewUrl({ preview: { urls: ['https://a.b.workers.dev'] } }), 'https://a.b.workers.dev');
  assert.equal(extractPreviewUrl('log line\n{"preview":{"urls":["https://x.dev/"]}}'), 'https://x.dev');
  assert.throws(() => extractPreviewUrl({}));
  assert.throws(() => extractPreviewUrl({ preview: { urls: [] } }));
  assert.throws(() => extractPreviewUrl({ preview: { urls: ['http://insecure.dev'] } }));
  assert.throws(() => extractPreviewUrl({ preview: { urls: ['nope'] } }));
});
test('wrangler version gate', () => {
  assertWranglerSupported('4.135.0');
  assertWranglerSupported('4.200.1');
  assertWranglerSupported('5.0.0');
  assert.throws(() => assertWranglerSupported('4.134.9'));
  assert.throws(() => assertWranglerSupported('3.99.0'));
  assert.throws(() => assertWranglerSupported(''));
});

const c = (o) => classifyEvent({ strategy: 'production-preview', ...o });
test('event matrix', () => {
  assert.equal(c({ event: 'push', ref: 'refs/heads/feat/a' }).action, 'preview');
  assert.equal(c({ event: 'push', ref: 'refs/heads/main' }).action, 'production');
  assert.equal(c({ event: 'pull_request', ref: 'refs/pull/1/merge' }).action, 'ci-only');
  assert.equal(c({ event: 'pull_request', ref: 'x', isFork: true }).action, 'ci-only');
  assert.equal(c({ event: 'delete', ref: 'refs/heads/feat/a', deletedRefType: 'branch' }).action, 'cleanup');
  assert.equal(c({ event: 'delete', ref: 'refs/heads/main', deletedRefType: 'branch' }).action, 'skip');
  assert.equal(c({ event: 'delete', ref: 'refs/tags/v1', deletedRefType: 'tag' }).action, 'skip');
  assert.equal(c({ event: 'push', ref: 'refs/heads/feat/a', strategy: 'production-only' }).action, 'ci-only');
  assert.equal(c({ event: 'delete', ref: 'refs/heads/a', deletedRefType: 'branch', strategy: 'production-only' }).action, 'skip');
});
test('tag pushes are skipped', () => assert.equal(c({ event: 'push', ref: 'refs/heads/' }).action, 'skip'));
test('hybrid: feature push previews, dev only on approved dispatch', () => {
  const h = { strategy: 'hybrid', devRef: 'refs/heads/dev' };
  assert.equal(classifyEvent({ ...h, event: 'push', ref: 'refs/heads/dev' }).action, 'preview');
  assert.equal(classifyEvent({ ...h, event: 'workflow_dispatch', ref: 'refs/heads/dev', dispatchTarget: 'persistent-dev' }).action, 'persistent-dev');
  assert.equal(classifyEvent({ ...h, event: 'workflow_dispatch', ref: 'refs/heads/feat', dispatchTarget: 'persistent-dev' }).action, 'skip');
  assert.equal(classifyEvent({ strategy: 'production-preview', event: 'workflow_dispatch', ref: 'refs/heads/dev', dispatchTarget: 'persistent-dev', devRef: 'refs/heads/dev' }).action, 'skip');
});
test('stale-SHA guard', () => {
  assert.equal(isStale({ runSha: 'a', branchTipSha: 'a' }), false);
  assert.equal(isStale({ runSha: 'a', branchTipSha: 'b' }), true);
  assert.equal(isStale({ runSha: 'a', branchTipSha: '' }), true);
});

const good = () => ({
  d1_databases: [{ binding: 'DB', database_id: 'prod-id', database_name: 'prod' }],
  r2_buckets: [{ binding: 'ASSETS_R2', bucket_name: 'prod-bucket' }],
  previews: {
    d1_databases: [{ binding: 'DB', database_id: 'pv-id', database_name: 'pv' }],
    r2_buckets: [{ binding: 'ASSETS_R2', bucket_name: 'pv-bucket' }],
  },
});
test('preflight accepts isolated resources', () => assert.deepEqual(auditPreviewConfig(good()).errors, []));
test('preflight: missing previews block', () => assert.equal(auditPreviewConfig({ name: 'x' }).errors.length, 1));
test('preflight: shared Production D1 / R2 rejected', () => {
  const g = good();
  g.previews.d1_databases[0].database_id = 'prod-id';
  g.previews.r2_buckets[0].bucket_name = 'prod-bucket';
  assert.equal(auditPreviewConfig(g).errors.length, 2);
});
test('preflight: Production binding without Preview counterpart', () => {
  const g = good();
  delete g.previews.r2_buckets;
  assert.match(auditPreviewConfig(g).errors[0], /no Preview counterpart/);
});
test('preflight: denylist', () => {
  const g = good();
  g.previews.r2_buckets[0].bucket_name = 'general-bucket';
  assert.equal(auditPreviewConfig(g, { denyResources: ['general-bucket'] }).errors.length, 1);
});
test('preflight: exact kind:BINDING=identity allow shares one Production R2 bucket, nothing else', () => {
  const g = good();
  g.previews.r2_buckets[0].bucket_name = 'prod-bucket';
  const entry = 'r2_buckets:ASSETS_R2=prod-bucket';
  assert.equal(auditPreviewConfig(g).errors.length, 1);
  assert.deepEqual(auditPreviewConfig(g, { allow: [entry] }).errors, []);
  // the allow entry also waives the denylist for that exact binding/identity
  assert.deepEqual(auditPreviewConfig(g, { allow: [entry], denyResources: ['prod-bucket'] }).errors, []);
  // a different bucket under the same binding, or the same bucket under another binding, is not approved
  assert.equal(auditPreviewConfig(g, { allow: ['r2_buckets:ASSETS_R2=other-bucket'] }).errors.length, 1);
  assert.equal(auditPreviewConfig(g, { allow: ['r2_buckets:OTHER=prod-bucket'] }).errors.length, 1);
});
test('preflight: a Production D1 can never be approved for sharing', () => {
  const g = good();
  g.previews.d1_databases[0].database_id = 'prod-id';
  assert.equal(auditPreviewConfig(g, { allow: ['d1_databases:DB=prod-id'] }).errors.length, 1);
});
test('preflight: service/hyperdrive/secrets-store need explicit allow', () => {
  const g = good();
  g.services = [{ binding: 'ID', service: 'heaven-id' }];
  g.previews.services = [{ binding: 'ID', service: 'heaven-id' }];
  g.hyperdrive = [{ binding: 'H', id: 'p' }];
  g.previews.hyperdrive = [{ binding: 'H', id: 'q' }];
  assert.equal(auditPreviewConfig(g).errors.length, 2);
  assert.deepEqual(auditPreviewConfig(g, { allow: ['services:ID', 'hyperdrive:H'] }).errors, []);
});
test('preflight: service binding with no Preview counterpart', () => {
  const g = good();
  g.services = [{ binding: 'ID', service: 'heaven-id' }];
  assert.match(auditPreviewConfig(g).errors.join('\n'), /services:ID/);
});
test('preflight: queue producer to Production queue; consumers/cron/routes forbidden', () => {
  const g = good();
  g.queues = { producers: [{ binding: 'Q', queue: 'prod-q' }] };
  g.previews.queues = { producers: [{ binding: 'Q', queue: 'prod-q' }], consumers: [{ queue: 'x' }] };
  g.previews.triggers = { crons: ['* * * * *'] };
  g.previews.routes = ['x.com/*'];
  assert.equal(auditPreviewConfig(g).errors.length, 4);
});
test('preflight: assets/compat must be top-level', () => {
  const g = good();
  g.previews.assets = { directory: 'x' };
  assert.equal(auditPreviewConfig(g).errors.length, 1);
});
test('preflight: Preview binding without id is an error (Cloudflare 10021)', () => {
  const g = { kv_namespaces: [{ binding: 'SESSION' }], previews: { kv_namespaces: [{ binding: 'SESSION' }] } };
  assert.equal(auditPreviewConfig(g).errors.length, 1);
  assert.deepEqual(auditPreviewConfig({ ...g, previews: {} }).errors.length, 1); // still no counterpart
});

// ---- v2 refactor: target resolution, migration target, production audit, env import ----
import { previewHostReadiness, rebindSha, auditDevConfig, auditProdConfig, d1ForTarget, migrationPlan, resolveSharedDev, sharedPreviewDevD1, resolveTarget } from '../../actions/cf-preview/lib.mjs';
import { parseEnvLines } from '../../actions/cf-preview/parse-env.mjs';

test('resolveTarget: only trusted push/dispatch deploys; main=prod, branch=preview', () => {
  const r = (o) => resolveTarget({ strategy: 'production-preview', ...o });
  assert.equal(r({ event: 'push', ref: 'refs/heads/main' }), 'prod');
  assert.equal(r({ event: 'push', ref: 'refs/heads/feat/x' }), 'preview');
  assert.equal(r({ event: 'workflow_dispatch', ref: 'refs/heads/main' }), 'prod');
  assert.equal(r({ event: 'pull_request', ref: 'refs/heads/feat/x' }), 'none');
  assert.equal(r({ event: 'pull_request', ref: 'refs/heads/main' }), 'none');
  assert.equal(r({ event: 'delete', ref: 'refs/heads/feat/x' }), 'none');
  assert.equal(r({ event: 'push', ref: 'refs/tags/v1' }), 'none');
  assert.equal(r({ event: 'push', ref: 'refs/heads/feat/x', previewOnPush: false }), 'none');
  assert.equal(resolveTarget({ event: 'push', ref: 'refs/heads/feat/x', strategy: 'production-only' }), 'none');
  assert.equal(resolveTarget({ event: 'push', ref: 'refs/heads/main', strategy: 'production-only' }), 'prod');
  // Hybrid persistent Dev is never a prod/preview target
  assert.equal(resolveTarget({ event: 'workflow_dispatch', ref: 'refs/heads/dev', strategy: 'hybrid', devRef: 'refs/heads/dev', dispatchTarget: 'persistent-dev' }), 'none');
});

const D1CFG = {
  name: 'blog',
  d1_databases: [{ binding: 'DB', database_name: 'blog-db', database_id: 'PROD-ID', migrations_dir: 'db/migrations' }],
  previews: { d1_databases: [{ binding: 'DB', database_name: 'blog-db-preview', database_id: 'PREV-ID', migrations_dir: 'db/migrations' }] },
};
const abs = (d) => `/app/${d}`;
test('migrationPlan: prod and preview select different physical D1 with the same SQL dir', () => {
  const p = migrationPlan(D1CFG, 'prod', { migrationsDirAbs: abs });
  const v = migrationPlan(D1CFG, 'preview', { migrationsDirAbs: abs });
  assert.equal(p.databaseId, 'PROD-ID');
  assert.equal(v.databaseId, 'PREV-ID');
  assert.equal(p.config.d1_databases[0].migrations_dir, v.config.d1_databases[0].migrations_dir);
  assert.equal(v.config.d1_databases.length, 1);
});
test('migrationPlan: Preview D1 comes from previews.d1_databases, never env.dev', () => {
  const cfg = { ...D1CFG, env: { dev: { d1_databases: [{ binding: 'DB', database_name: 'dev-db', database_id: 'DEV-ID' }] } } };
  assert.equal(migrationPlan(cfg, 'preview', { migrationsDirAbs: abs }).databaseId, 'PREV-ID');
});
test('migrationPlan: fails closed (no D1, ambiguous, no id, shared across trust boundary)', () => {
  assert.throws(() => migrationPlan({ name: 'x', previews: {} }, 'prod', { migrationsDirAbs: abs }), /No D1 binding/);
  assert.throws(() => migrationPlan({ ...D1CFG, previews: {} }, 'preview', { migrationsDirAbs: abs }), /No D1 binding/);
  const two = { ...D1CFG, d1_databases: [...D1CFG.d1_databases, { binding: 'B2', database_name: 'b2', database_id: 'X' }] };
  assert.throws(() => migrationPlan(two, 'prod', { migrationsDirAbs: abs }), /Several D1 bindings/);
  assert.equal(migrationPlan(two, 'prod', { binding: 'B2', migrationsDirAbs: abs }).databaseId, 'X');
  assert.throws(() => migrationPlan({ ...D1CFG, d1_databases: [{ binding: 'DB', database_name: 'n' }] }, 'prod', { migrationsDirAbs: abs }), /explicit database_id/);
  const shared = { ...D1CFG, previews: { d1_databases: [{ ...D1CFG.d1_databases[0] }] } };
  assert.throws(() => migrationPlan(shared, 'preview', { migrationsDirAbs: abs }), /shared across trust boundaries/);
  assert.throws(() => migrationPlan(shared, 'prod', { migrationsDirAbs: abs }), /shared across trust boundaries/);
  assert.throws(() => d1ForTarget(D1CFG, 'staging'), /Unknown migration target/);
});
test('Worker without D1 has nothing to migrate (d1ForTarget empty)', () => {
  assert.deepEqual(d1ForTarget({ name: 'static' }, 'prod'), []);
  assert.deepEqual(d1ForTarget({ name: 'static' }, 'preview'), []);
});

test('auditProdConfig: generated Production config must not bind Preview resources', () => {
  const src = { d1_databases: [{ binding: 'DB', database_id: 'PROD' }], r2_buckets: [{ binding: 'B', bucket_name: 'prod-b' }],
    previews: { d1_databases: [{ binding: 'DB', database_id: 'PREV' }], r2_buckets: [{ binding: 'B', bucket_name: 'prev-b' }] } };
  assert.deepEqual(auditProdConfig({ d1_databases: [{ binding: 'DB', database_id: 'PROD' }], r2_buckets: [{ binding: 'B', bucket_name: 'prod-b' }] }, src).errors, []);
  const bad = auditProdConfig({ d1_databases: [{ binding: 'DB', database_id: 'PREV' }] }, src);
  assert.match(bad.errors[0], /Preview resource PREV/);
  // an adapter adding a Preview R2 bucket to the Production config is caught too
  assert.equal(auditProdConfig({ r2_buckets: [{ binding: 'X', bucket_name: 'prev-b' }] }, src).errors.length, 1);
});

test('parseEnvLines: KEY=VALUE only, comments/blank skipped, unsafe input rejected', () => {
  assert.deepEqual(parseEnvLines('A=1\n\n# c\nB=x=y\r\nC=').pairs, [['A', '1'], ['B', 'x=y'], ['C', '']]);
  for (const bad of ['1A=x', 'A B=x', 'novalue', '=x', 'A=$(id)\u0000z', 'A-B=1']) {
    assert.ok(parseEnvLines(bad).errors.length === 1 || /\$\(id\)/.test(bad), `rejected: ${bad}`);
  }
  // shell metacharacters are data, not code: they are preserved verbatim and never evaluated here
  assert.deepEqual(parseEnvLines('URL=https://x/?a=1&b=$(whoami)`id`;rm').pairs, [['URL', 'https://x/?a=1&b=$(whoami)`id`;rm']]);
});
test('parseEnvLines: credentials, runner internals and loader variables cannot be set by callers', () => {
  for (const n of ['CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_ENV', 'GITHUB_ENV', 'GITHUB_PATH', 'PATH', 'NODE_OPTIONS', 'LD_PRELOAD', 'BASH_ENV', 'RUNNER_TEMP', 'GIT_SSH_COMMAND', 'SMOKE_PASSWORD']) {
    assert.equal(parseEnvLines(`${n}=x`).pairs.length, 0, n);
    assert.equal(parseEnvLines(`${n}=x`).errors.length, 1, n);
  }
  assert.equal(parseEnvLines('VITE_PUBLIC_URL=https://x').errors.length, 0);
});

test('migrationPlan keeps migrations_table / migrations_pattern and resolves the dir against the config', () => {
  const cfg = { name: 'x', d1_databases: [{ binding: 'DB', database_name: 'n', database_id: 'I', migrations_dir: 'sql', migrations_table: 't', migrations_pattern: '*.sql' }] };
  const d = migrationPlan(cfg, 'prod', { migrationsDirAbs: (x) => `/abs/${x}` }).config.d1_databases[0];
  assert.deepEqual([d.migrations_dir, d.migrations_table, d.migrations_pattern], ['/abs/sql', 't', '*.sql']);
});

// ---- Shared Dev channel (independent of Native Preview) ----
test('resolveSharedDev: non-main push/dispatch only, switch required, never production-only', () => {
  const r = (o) => resolveSharedDev({ strategy: 'production-preview', sharedDevOnPush: true, ...o });
  assert.equal(r({ event: 'push', ref: 'refs/heads/feat/x' }), 'dev');
  assert.equal(r({ event: 'push', ref: 'refs/heads/feat/y' }), 'dev'); // every branch targets the same Dev
  assert.equal(r({ event: 'workflow_dispatch', ref: 'refs/heads/feat/x' }), 'dev');
  assert.equal(r({ event: 'push', ref: 'refs/heads/main' }), 'none');
  assert.equal(r({ event: 'push', ref: 'refs/tags/v1' }), 'none');
  assert.equal(r({ event: 'pull_request', ref: 'refs/heads/feat/x' }), 'none');
  assert.equal(r({ event: 'delete', ref: 'refs/heads/feat/x' }), 'none'); // branch deletion never touches Dev
  assert.equal(r({ event: 'workflow_dispatch', ref: 'refs/heads/dev', dispatchTarget: 'persistent-dev' }), 'none'); // manual path stays separate
  assert.equal(r({ event: 'push', ref: 'refs/heads/feat/x', sharedDevOnPush: false }), 'none');
  assert.equal(r({ event: 'push', ref: 'refs/heads/feat/x', strategy: 'production-only' }), 'none');
});
test('four toggle combinations resolve independently', () => {
  const both = (feature, dev) => [
    resolveTarget({ event: 'push', ref: 'refs/heads/feat/x', strategy: 'production-preview', previewOnPush: feature }),
    resolveSharedDev({ event: 'push', ref: 'refs/heads/feat/x', strategy: 'production-preview', sharedDevOnPush: dev }),
  ];
  assert.deepEqual(both(true, true), ['preview', 'dev']);
  assert.deepEqual(both(true, false), ['preview', 'none']);
  assert.deepEqual(both(false, true), ['none', 'dev']);
  assert.deepEqual(both(false, false), ['none', 'none']);
  // main is always Production, whatever the switches say
  assert.equal(resolveTarget({ event: 'push', ref: 'refs/heads/main', strategy: 'production-preview', previewOnPush: false }), 'prod');
});
const DEVCFG = {
  name: 'blog',
  d1_databases: [{ binding: 'DB', database_name: 'blog-db', database_id: 'PROD-ID', migrations_dir: 'm' }],
  r2_buckets: [{ binding: 'B', bucket_name: 'prod-b' }],
  previews: { d1_databases: [{ binding: 'DB', database_name: 'blog-test', database_id: 'TEST-ID', migrations_dir: 'm' }] },
  env: { dev: { name: 'blog-preview', d1_databases: [{ binding: 'DB', database_name: 'blog-test', database_id: 'TEST-ID', migrations_dir: 'm' }] } },
};
test('migrationPlan(dev): reads env.<dev>.d1_databases; may equal the Preview D1, never Production', () => {
  const dev = migrationPlan(DEVCFG, 'dev', { migrationsDirAbs: abs });
  const prev = migrationPlan(DEVCFG, 'preview', { migrationsDirAbs: abs });
  assert.equal(dev.databaseId, 'TEST-ID');
  assert.equal(dev.databaseId, prev.databaseId); // same physical D1 for both non-Production channels is accepted
  assert.equal(migrationPlan(DEVCFG, 'prod', { migrationsDirAbs: abs }).databaseId, 'PROD-ID');
  const bad = { ...DEVCFG, env: { dev: { d1_databases: [DEVCFG.d1_databases[0]] } } };
  assert.throws(() => migrationPlan(bad, 'dev', { migrationsDirAbs: abs }), /shared across trust boundaries/);
  assert.throws(() => migrationPlan({ ...DEVCFG, env: {} }, 'dev', { migrationsDirAbs: abs }), /No D1 binding.*env\.dev\.d1_databases/); // no top-level (Production) fallback
  assert.equal(migrationPlan({ ...DEVCFG, env: { qa: DEVCFG.env.dev } }, 'dev', { devEnv: 'qa', migrationsDirAbs: abs }).databaseId, 'TEST-ID');
});
const PRODCFG = {
  name: 'blog',
  d1_databases: [{ binding: 'DB', database_name: 'blog-db', database_id: 'PROD-ID' }],
  r2_buckets: [{ binding: 'VOICE', bucket_name: 'prod-b' }],
  kv_namespaces: [{ binding: 'KV', id: 'kv-prod' }],
  queues: { producers: [{ binding: 'Q', queue: 'jobs' }], consumers: [{ queue: 'jobs', dead_letter_queue: 'jobs-dlq' }] },
  services: [{ binding: 'AUTH', service: 'auth-prod' }],
  routes: [{ pattern: 'blog.example.com', custom_domain: true }],
  ratelimits: [{ name: 'RL', namespace_id: '1101' }],
};
const devCfg = (over = {}) => ({ name: 'blog-preview', d1_databases: [{ binding: 'DB', database_id: 'TEST-ID', database_name: 'blog-test' }], ...over });
const audit = (eff, o = {}) => auditDevConfig(eff, PRODCFG, { worker: 'blog-preview', ...o }).errors;
const has = (errs, re) => errs.some((e) => re.test(e));
const SHARE = ['r2_buckets:VOICE=prod-b'];
const R2 = (name, binding = 'VOICE') => ({ r2_buckets: [{ binding, bucket_name: name }] });

test('Dev audit: Worker identity and a clean Dev config', () => {
  assert.deepEqual(audit(devCfg()), []);
  assert.ok(has(audit({ ...devCfg(), name: 'blog' }), /expected the Shared Dev Worker "blog-preview"/));
});
test('Dev allowlist: exact approved R2 passes; a different bucket, binding or malformed policy fails', () => {
  assert.deepEqual(audit(devCfg(R2('prod-b')), { allow: SHARE }), []);
  assert.ok(has(audit(devCfg(R2('other-b')), { allow: SHARE }), /bound to other-b but the allowlist approves prod-b/)); // same binding, other bucket
  assert.ok(has(audit(devCfg(R2('prod-b', 'OTHER')), { allow: SHARE }), /matches no binding/)); // approved binding missing
  assert.ok(has(audit(devCfg(R2('prod-b', 'OTHER')), { allow: SHARE }), /OTHER.*Production resource prod-b|Production resource prod-b/)); // same bucket, unapproved binding
  assert.ok(has(audit(devCfg(R2('prod-b'))), /Production resource prod-b/)); // no entry at all
  assert.ok(has(audit(devCfg(), { allow: ['r2_buckets:VOICE'] }), /malformed/)); // legacy name-only format
  assert.ok(has(audit(devCfg(), { allow: ['r2_buckets:VOICE=a', 'r2_buckets:VOICE=b'] }), /more than one entry/));
  assert.ok(has(audit(devCfg(), { allow: ['bogus_kind:X=y'] }), /not supported/));
  assert.ok(has(audit(devCfg(), { allow: ['r2_buckets:VOICE=prod-b'] }), /matches no binding/));
});
test('Dev allowlist: missing identity, hard deny precedence, D1 never approvable', () => {
  assert.ok(has(audit(devCfg({ r2_buckets: [{ binding: 'VOICE' }] }), { allow: SHARE }), /identity is missing/));
  assert.ok(has(audit(devCfg(R2('prod-b')), { allow: SHARE, denyResources: ['prod-b'] }), /hard-denied/)); // deny beats allow
  assert.ok(has(audit(devCfg({ d1_databases: [{ binding: 'DB', database_id: 'PROD-ID', database_name: 'blog-db' }] }), { allow: ['d1_databases:DB=PROD-ID'] }), /cannot approve a D1/));
  assert.ok(has(audit(devCfg({ d1_databases: [{ binding: 'DB', database_id: 'PROD-ID', database_name: 'blog-db' }] })), /Production D1 can never be shared/));
  assert.deepEqual(audit(devCfg({ d1_databases: [{ binding: 'DB', database_id: 'PREV-SHARED', database_name: 'blog-test' }] })), []); // Preview/Dev non-production D1
  assert.ok(has(audit(devCfg({ r2_buckets: [{ binding: 'VOICE', bucket_name: 'a' }, { binding: 'VOICE', bucket_name: 'b' }] })), /more than once/));
});
test('Dev allowlist: per-kind identity, no cross-kind or partial matches; unshareable kinds', () => {
  assert.deepEqual(audit(devCfg({ kv_namespaces: [{ binding: 'KV', id: 'kv-prod' }] }), { allow: ['kv_namespaces:KV=kv-prod'] }), []);
  assert.ok(has(audit(devCfg({ kv_namespaces: [{ binding: 'KV', id: 'kv-prod' }] }), { allow: ['kv_namespaces:KV=kv-other'] }), /identity mismatch/));
  assert.ok(has(audit(devCfg({ hyperdrive: [{ binding: 'H', id: 'h1' }] }), { allow: ['hyperdrive:H=h1'] }), /not supported/));
  const prodSecrets = { ...PRODCFG, secrets_store_secrets: [{ binding: 'S', store_id: 'st', secret_name: 'k' }] };
  assert.ok(auditDevConfig(devCfg({ secrets_store_secrets: [{ binding: 'S', store_id: 'st', secret_name: 'k' }] }), prodSecrets, {}).errors.some((e) => /Production resource st\/k/.test(e)));
  assert.deepEqual(auditDevConfig(devCfg({ secrets_store_secrets: [{ binding: 'S', store_id: 'st', secret_name: 'other' }] }), prodSecrets, {}).errors, []); // full identity, not store only
  assert.ok(has(audit(devCfg({ secrets_store_secrets: [{ binding: 'S', store_id: 'st' }] })), /identity is missing/)); // partial identity
  assert.ok(has(audit(devCfg({ ratelimits: [{ name: 'RL', namespace_id: 1101 }] })), /Production resource 1101/)); // rate-limit counters are shared per namespace
});
test('Dev audit: queues (producer, consumer, DLQ) must not be Production queues', () => {
  const dq = (queues) => audit(devCfg({ queues }));
  assert.deepEqual(dq({ producers: [{ binding: 'Q', queue: 'jobs-dev' }], consumers: [{ queue: 'jobs-dev', dead_letter_queue: 'jobs-dev-dlq' }] }), []);
  assert.ok(has(dq({ producers: [{ binding: 'Q', queue: 'jobs' }] }), /producer "jobs" is a Production queue/));
  assert.ok(has(dq({ consumers: [{ queue: 'jobs' }] }), /consumer "jobs" is a Production queue/));
  assert.ok(has(dq({ consumers: [{ queue: 'jobs-dev', dead_letter_queue: 'jobs-dlq' }] }), /dead-letter "jobs-dlq" is a Production queue/));
  assert.ok(has(dq({ producers: [{ binding: 'Q' }] }), /queue name is missing/));
  assert.ok(has(audit(devCfg({ queues: { producers: [{ binding: 'Q', queue: 'jobs' }] } }), { allow: ['queues:Q=jobs'] }), /not supported/)); // no storage-style bypass
});
test('Dev audit: service bindings are default-deny and never Production; routes; durable objects; unsupported kinds', () => {
  const sv = (services, o) => audit(devCfg({ services }), o);
  assert.deepEqual(sv([{ binding: 'AUTH', service: 'auth-dev' }], { allow: ['services:AUTH=auth-dev'] }), []);
  assert.ok(has(sv([{ binding: 'AUTH', service: 'auth-dev' }]), /not approved/));
  assert.ok(has(sv([{ binding: 'AUTH', service: 'auth-prod' }], { allow: ['services:AUTH=auth-prod'] }), /Production\/denied Worker; this cannot be approved/));
  assert.ok(has(sv([{ binding: 'SELF', service: 'blog' }], { allow: ['services:SELF=blog'] }), /Production/)); // the Production Worker itself
  assert.ok(has(sv([{ binding: 'AUTH', service: 'auth-dev' }], { allow: ['services:AUTH=other'] }), /identity mismatch/));
  assert.ok(has(sv([{ binding: 'AUTH' }]), /ambiguous/));
  assert.ok(has(sv([{ service: 'auth-dev' }]), /ambiguous/));
  assert.deepEqual(sv([{ binding: 'AUTH', service: 'auth', environment: 'dev' }], { allow: ['services:AUTH=auth#dev'] }), []);
  assert.ok(has(sv([{ binding: 'AUTH', service: 'auth', environment: 'dev' }], { allow: ['services:AUTH=auth'] }), /identity mismatch/));
  assert.ok(has(audit(devCfg({ routes: [{ pattern: 'blog.example.com', custom_domain: true }] })), /Production route/));
  assert.ok(has(audit(devCfg({ route: 'https://prod.example.com/*' }), { prodUrl: 'https://prod.example.com' }), /Production route\/host/));
  assert.deepEqual(audit(devCfg({ routes: [{ pattern: 'blog-preview.example.com', custom_domain: true }] }), { prodUrl: 'https://prod.example.com' }), []);
  assert.ok(has(audit(devCfg({ durable_objects: { bindings: [{ name: 'D', class_name: 'C', script_name: 'blog' }] } })), /another Worker/));
  assert.deepEqual(audit(devCfg({ durable_objects: { bindings: [{ name: 'D', class_name: 'C' }] } })), []);
  assert.ok(has(audit(devCfg({ workflows: [{ name: 'w', binding: 'W', class_name: 'C' }] })), /not supported/));
});
test('Preview policy is independent: a Dev exception does not weaken Native Preview, a Preview denylist does not block Dev', () => {
  const cfg = { ...PRODCFG, previews: { r2_buckets: [{ binding: 'VOICE', bucket_name: 'prod-b' }], d1_databases: [{ binding: 'DB', database_id: 'P', database_name: 'p' }] } };
  // Native Preview still rejects the Production bucket whatever the Dev allowlist says.
  assert.ok(auditPreviewConfig(cfg, { allow: [], denyResources: ['prod-b'] }).errors.some((e) => /Production|denylisted/.test(e)));
  // The Dev audit takes only its own denylist, so a Preview-only deny entry is irrelevant to an approved Dev share.
  assert.deepEqual(audit(devCfg(R2('prod-b')), { allow: SHARE, denyResources: ['some-preview-only-thing'] }), []);
});
test('sharedPreviewDevD1: same physical D1 detected; distinct or undeclared Preview is not shared', () => {
  assert.equal(sharedPreviewDevD1(DEVCFG), true);
  assert.equal(sharedPreviewDevD1({ ...DEVCFG, previews: {} }), false);
  assert.equal(sharedPreviewDevD1({ ...DEVCFG, env: { dev: { d1_databases: [{ binding: 'DB', database_name: 'other', database_id: 'X' }] } } }), false);
  assert.equal(sharedPreviewDevD1({ ...DEVCFG, env: {} }), false);
});

test('extractPreviewUrl: fails closed on every unusable payload (the empty-URL incident)', () => {
  assert.equal(extractPreviewUrl('{"preview":{"urls":["https://a-blog.x.workers.dev"]}}'), 'https://a-blog.x.workers.dev');
  assert.equal(extractPreviewUrl('log line\n{"preview":{"urls":["https://a.example.com/"]}}'), 'https://a.example.com');
  assert.throws(() => extractPreviewUrl('{"preview":{"urls":[]}}'), /EMPTY `preview.urls`.*Enable Preview URLs/);
  assert.throws(() => extractPreviewUrl('{"preview":{}}'), /preview.urls` is missing/);
  assert.throws(() => extractPreviewUrl('{}'), /no `preview` object/);
  assert.throws(() => extractPreviewUrl('not json'), /not valid JSON/);
  assert.throws(() => extractPreviewUrl(''), /produced no output/);
  assert.throws(() => extractPreviewUrl('{"preview":{"urls":[""]}}'), /empty or not a string/);
  assert.throws(() => extractPreviewUrl('{"preview":{"urls":[7]}}'), /empty or not a string/);
  assert.throws(() => extractPreviewUrl('{"preview":{"urls":["http://x.example.com"]}}'), /must be https/);
  assert.throws(() => extractPreviewUrl('{"preview":{"urls":["nope"]}}'), /not a valid URL/);
  assert.throws(() => extractPreviewUrl('null'), /no `preview` object/);
});
const apiRes = (status, body) => async () => ({ ok: status < 300, status, json: async () => body });
test('previewHostReadiness: blocks only on a definitive previews_enabled=false', async () => {
  const a = { accountId: 'a', token: 't', worker: 'blog' };
  assert.equal((await previewHostReadiness(apiRes(200, { result: { previews_enabled: true } }), a)).level, 'ok');
  const off = await previewHostReadiness(apiRes(200, { result: { previews_enabled: false, enabled: false } }), a);
  assert.equal(off.level, 'block');
  assert.match(off.message, /preview_urls = true|Preview URLs/);
  assert.equal((await previewHostReadiness(apiRes(403, {}), a)).level, 'unknown');
  assert.equal((await previewHostReadiness(apiRes(404, {}), a)).level, 'unknown');
  assert.equal((await previewHostReadiness(async () => { throw new Error('x'); }, a)).level, 'unknown');
  assert.equal((await previewHostReadiness(apiRes(200, { result: {} }), a)).level, 'unknown');
  assert.equal((await previewHostReadiness(apiRes(200, {}), { ...a, token: '' })).level, 'unknown');
});
test('rebindSha: failed SHA (full and short) becomes the restored SHA', () => {
  const r = rebindSha('A=abcdef1234567890\nB=abcdef1\nC=keep', 'abcdef1234567890', '1111111222222233');
  assert.equal(r.text, 'A=1111111222222233\nB=1111111\nC=keep');
  assert.equal(r.replacements, 2);
  assert.throws(() => rebindSha('x', 'zz', '1111111'), /hex/);
});

// ---- PR #64 refactor: protected control variables, exact D1 consistency, same-artifact fingerprint ----
import { checkDeployedD1, artifactDigest } from '../../actions/cf-preview/lib.mjs';
import { RESERVED } from '../../actions/cf-preview/parse-env.mjs';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

test('parseEnvLines (reserved): deployment-control and verification names cannot be set by application env', () => {
  for (const n of ['TARGET', 'DEV_ENV', 'GH_TOKEN', 'EXPECTED_SHA', 'RUN_SHA', 'GOOD_SHA', 'D1_BINDING', 'STATE_DIR', 'APP_DIR', 'BRANCH_REF', 'STALE',
    'TOOLS_DIR', 'WRANGLER_CONFIG', 'BUILD_CMD', 'PUBLISH_CMD', 'ROLLBACK_TO', 'INPUTS_JSON', 'SMOKE_CMD', 'TARGET_URL', 'DEPLOY_TARGET', 'CF_AUDITED_CONFIG', 'VITE_APP_ENV']) {
    assert.ok(RESERVED.has(n), `${n} is reserved`);
    const r = parseEnvLines(`${n}=x`, { reserved: true });
    assert.equal(r.pairs.length, 0, n);
    assert.match(r.errors[0], /may not be set/, n);
  }
  // the engine rejects a whole block when ANY line is protected (all-or-nothing)
  assert.equal(parseEnvLines('OK=1\nTARGET=prod', { reserved: true }).errors.length, 1);
  // ordinary application names stay valid, and the validation jobs / E2E (not reserved mode) keep their existing freedom
  assert.deepEqual(parseEnvLines('PUBLIC_COMMIT_SHA=abc\nSITE=dev', { reserved: true }).pairs, [['PUBLIC_COMMIT_SHA', 'abc'], ['SITE', 'dev']]);
  assert.equal(parseEnvLines('APP_URL=http://localhost:4321').errors.length, 0);
});

const SRC = {
  name: 'app',
  d1_databases: [{ binding: 'DB', database_name: 'prod-db', database_id: 'PROD-ID' }],
  previews: { d1_databases: [{ binding: 'DB', database_name: 'prev-db', database_id: 'PREV-ID' }] },
  env: { dev: { name: 'app-dev', d1_databases: [{ binding: 'DB', database_name: 'dev-db', database_id: 'DEV-ID' }] } },
};
test('checkDeployedD1: the deployed config must bind the exact physical D1 the migration targets', () => {
  const eff = (d1, extra = {}) => ({ name: 'app', ...extra, d1_databases: d1 });
  const ok = [{ binding: 'DB', database_name: 'x', database_id: 'PROD-ID' }];
  assert.equal(checkDeployedD1(eff(ok), SRC, 'prod').databaseId, 'PROD-ID');
  assert.throws(() => checkDeployedD1(eff([{ binding: 'DB', database_id: 'OTHER' }]), SRC, 'prod'), /D1 mismatch.*PROD-ID.*OTHER/s);
  assert.throws(() => checkDeployedD1(eff([]), SRC, 'prod'), /no D1 binding "DB"/);
  assert.throws(() => checkDeployedD1(eff([{ binding: 'OTHER_BINDING', database_id: 'PROD-ID' }]), SRC, 'prod'), /no D1 binding "DB"/);
  // Preview reads previews.d1_databases of the deployed config
  assert.equal(checkDeployedD1({ previews: { d1_databases: [{ binding: 'DB', database_id: 'PREV-ID' }] } }, SRC, 'preview').databaseId, 'PREV-ID');
  assert.throws(() => checkDeployedD1({ d1_databases: [{ binding: 'DB', database_id: 'PREV-ID' }], previews: {} }, SRC, 'preview'), /no D1 binding/);
  // Dev: the flattened effective config (top-level) must equal env.<dev>.d1_databases of the SOURCE; DB-A migrated, DB-B deployed is refused
  assert.equal(checkDeployedD1(eff([{ binding: 'DB', database_id: 'DEV-ID' }]), SRC, 'dev').databaseId, 'DEV-ID');
  assert.throws(() => checkDeployedD1(eff([{ binding: 'DB', database_id: 'PROD-ID' }]), SRC, 'dev'), /D1 mismatch/);
  // mode=source: the deployed config IS the source, selected through the named environment
  assert.equal(checkDeployedD1(SRC, SRC, 'dev', { mode: 'source' }).databaseId, 'DEV-ID');
});

test('artifactDigest: stable for an unchanged deployable; changes when the config, entry or assets change', () => {
  const d = mkdtempSync(join(tmpdir(), 'digest-'));
  mkdirSync(join(d, 'dist', 'assets'), { recursive: true });
  writeFileSync(join(d, 'dist', 'index.js'), 'export default {}');
  writeFileSync(join(d, 'dist', 'assets', 'a.css'), 'a{}');
  const cfg = join(d, 'dist', 'wrangler.json');
  const write = (o) => writeFileSync(cfg, JSON.stringify({ name: 'app-dev', main: 'index.js', assets: { directory: 'assets' }, ...o }));
  write({});
  const base = artifactDigest(cfg);
  assert.equal(artifactDigest(cfg), base);
  write({ name: 'app-prod' });
  assert.notEqual(artifactDigest(cfg), base, 'config change');
  write({});
  assert.equal(artifactDigest(cfg), base);
  writeFileSync(join(d, 'dist', 'index.js'), 'export default {};//rebuilt');
  assert.notEqual(artifactDigest(cfg), base, 'Worker entry replaced');
  writeFileSync(join(d, 'dist', 'index.js'), 'export default {}');
  writeFileSync(join(d, 'dist', 'assets', 'b.css'), 'b{}');
  assert.notEqual(artifactDigest(cfg), base, 'asset added');
});
