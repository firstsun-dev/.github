import test from 'node:test';
import assert from 'node:assert/strict';
import {
  assertWranglerSupported, auditPreviewConfig, classifyEvent, extractPreviewUrl, isStale, previewIdentity,
} from '../../actions/cf-preview/lib.mjs';

const id = (ref, worker = 'www', repo = 'o/r') => previewIdentity({ repo, worker, ref });

test('identity is stable and branch-scoped', () => {
  assert.equal(id('refs/heads/feat/x').name, id('refs/heads/feat/x').name);
  assert.notEqual(id('refs/heads/feat/x').name, id('refs/heads/feat/y').name);
  assert.notEqual(id('refs/heads/feat/x', 'a').name, id('refs/heads/feat/x', 'b').name);
});
test('look-alike branches never collide', () => {
  const a = id('refs/heads/Feat/A'), b = id('refs/heads/feat-a'), c = id('refs/heads/feat/a');
  assert.equal(a.slug, b.slug);
  assert.equal(new Set([a.name, b.name, c.name]).size, 3);
});
test('name is Cloudflare-safe and bounded', () => {
  const n = id('refs/heads/' + 'Ünï/ÇØDE_!!!'.repeat(20)).name;
  assert.match(n, /^br-[a-z0-9-]+-[0-9a-f]{10}$/);
  assert.ok(n.length <= 40);
  assert.match(id('refs/heads/!!!').name, /^br-branch-[0-9a-f]{10}$/);
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
