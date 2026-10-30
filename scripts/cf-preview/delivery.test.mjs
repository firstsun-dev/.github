import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DELIVERY_KINDS, resolveDeliveryTargets, resolveSmokeMode, parseSmokeOverrides, planMigrations, branchPreviewIdentity, ENGINE_TARGET,
} from '../../actions/cf-preview/delivery.mjs';

const base = { event: 'push', ref: 'refs/heads/feat/x', strategy: 'production-preview', previewOnPush: true };
const T = (o) => resolveDeliveryTargets({ ...base, ...o });

test('deployment target matrix: exactly the enabled targets run, nothing else', () => {
  assert.deepEqual(T({ ref: 'refs/heads/main' }), ['production']);
  assert.deepEqual(T({ ref: 'refs/heads/main', sharedDevNativeOnPush: true, sharedDevOnPush: false }), ['production']);
  assert.deepEqual(T({}), ['native-branch']);
  assert.deepEqual(T({ previewOnPush: false }), []);
  assert.deepEqual(T({ previewOnPush: false, sharedDevNativeOnPush: true }), ['native-dev']);
  assert.deepEqual(T({ sharedDevNativeOnPush: true }), ['native-branch', 'native-dev']);
  assert.deepEqual(T({ previewOnPush: false, sharedDevOnPush: true }), ['persistent-dev']);
  assert.deepEqual(T({ sharedDevOnPush: true }), ['native-branch', 'persistent-dev']);
});
test('non-deploy events and refs never produce a target', () => {
  assert.deepEqual(T({ event: 'pull_request', sharedDevNativeOnPush: true }), []);
  assert.deepEqual(T({ event: 'delete', sharedDevNativeOnPush: true }), []);
  assert.deepEqual(T({ ref: 'refs/tags/v1', sharedDevNativeOnPush: true }), []);
  assert.deepEqual(T({ ref: 'refs/pull/1/merge' }), []);
});
test('manual persistent-dev dispatch is its own path, only from the approved ref on hybrid', () => {
  const d = { event: 'workflow_dispatch', strategy: 'hybrid', dispatchTarget: 'persistent-dev', devRef: 'refs/heads/dev-ref', ref: 'refs/heads/dev-ref' };
  assert.deepEqual(T(d), ['persistent-dev']);
  assert.deepEqual(T({ ...d, ref: 'refs/heads/other' }), []);
  assert.deepEqual(T({ ...d, strategy: 'production-preview' }), []);
});
test('production-only rejects every Dev channel; conflicting automatic Dev channels are rejected', () => {
  assert.throws(() => T({ strategy: 'production-only', sharedDevNativeOnPush: true }), /production-only/);
  assert.throws(() => T({ strategy: 'production-only', sharedDevOnPush: true }), /production-only/);
  assert.throws(() => T({ sharedDevOnPush: true, sharedDevNativeOnPush: true }), /conflicting automatic Dev channels/);
  assert.deepEqual(T({ strategy: 'production-only', ref: 'refs/heads/main' }), ['production']); // existing: ignores preview default
  assert.deepEqual(T({ strategy: 'production-only' }), []);
});
test('unknown strategy rejects', () => assert.throws(() => T({ strategy: 'x' }), /Unknown deployment_strategy/));
test('every kind maps to an engine target class', () => {
  for (const k of DELIVERY_KINDS) assert.ok(ENGINE_TARGET[k], k);
  assert.equal(ENGINE_TARGET['native-dev'], 'preview'); // Native Dev is a Preview, never a [env.dev] Worker
});

test('smoke_mode: default full, per-target override wins, strict validation', () => {
  assert.equal(resolveSmokeMode('production'), 'full');
  assert.equal(resolveSmokeMode('native-dev', 'basic'), 'basic');
  assert.equal(resolveSmokeMode('native-dev', 'basic', '{"native-dev":"skip"}'), 'skip');
  assert.equal(resolveSmokeMode('production', 'basic', '{"native-dev":"skip"}'), 'basic');
  assert.equal(resolveSmokeMode('production', '', ''), 'full');
  assert.throws(() => resolveSmokeMode('production', 'quick'), /Unknown smoke_mode/);
  assert.throws(() => resolveSmokeMode('prod'), /Unknown delivery kind/);
  assert.throws(() => parseSmokeOverrides('{"prod":"skip"}'), /unknown delivery kind/);
  assert.throws(() => parseSmokeOverrides('{"production":"off"}'), /unknown smoke mode/);
  assert.throws(() => parseSmokeOverrides('[1]'), /JSON object/);
  assert.throws(() => parseSmokeOverrides('{bad'), /not valid JSON/);
  assert.deepEqual(parseSmokeOverrides('  '), {});
});

test('Native Dev owns the preview name "dev": a branch called dev gets b-dev when Native Dev is on', () => {
  const a = { repo: 'o/r', worker: 'blog', ref: 'refs/heads/dev' };
  assert.equal(branchPreviewIdentity(a).name, 'dev'); // legacy behaviour unchanged while Native Dev is off
  assert.equal(branchPreviewIdentity(a, { reserveNativeDev: true }).name, 'b-dev');
  assert.equal(branchPreviewIdentity({ ...a, ref: 'refs/heads/feat/x' }, { reserveNativeDev: true }).name, 'feat-x');
});

const cfg = (prev, dev, prod = 'PROD') => ({
  name: 'app', d1_databases: [{ binding: 'DB', database_name: 'prod-db', database_id: prod }],
  previews: { d1_databases: [{ binding: 'DB', database_name: 'prev-db', database_id: prev }] },
  env: { dev: { d1_databases: [{ binding: 'DB', database_name: 'dev-db', database_id: dev }] } },
});
test('migration plan: Branch and Native Dev sharing one preview D1 migrate ONCE', () => {
  const g = planMigrations(cfg('PREV', 'DEV'), ['native-branch', 'native-dev'], { accountId: 'acc' });
  assert.equal(g.length, 1);
  assert.equal(g[0].id, 'acc/PREV');
  assert.deepEqual(g[0].members, ['native-branch', 'native-dev']);
  assert.equal(g[0].owner, 'native-branch');
});
test('migration plan: distinct physical D1s each get their own execution', () => {
  const g = planMigrations(cfg('PREV', 'DEV'), ['native-branch', 'persistent-dev'], { accountId: 'acc' });
  assert.deepEqual(g.map((x) => x.id).sort(), ['acc/DEV', 'acc/PREV']);
});
test('migration plan: Preview and persistent Dev on the SAME physical D1 dedupe (owner = first in order)', () => {
  const g = planMigrations(cfg('SHARED', 'SHARED'), ['persistent-dev', 'native-dev'], { accountId: 'acc' });
  assert.equal(g.length, 1);
  assert.equal(g[0].owner, 'native-dev');
});
test('migration plan: production D1 can never be reached from Preview/Dev', () => {
  assert.throws(() => planMigrations(cfg('PROD', 'DEV', 'PROD'), ['native-dev'], { accountId: 'acc' }), /shared across trust boundaries/);
  assert.throws(() => planMigrations(cfg('PREV', 'PROD', 'PROD'), ['persistent-dev'], { accountId: 'acc' }), /shared across trust boundaries/);
  assert.equal(planMigrations(cfg('PREV', 'DEV'), ['production'], { accountId: 'acc' })[0].id, 'acc/PROD');
});
test('migration plan: a target without a D1 binding fails closed; no kinds = empty plan', () => {
  assert.throws(() => planMigrations({ name: 'a' }, ['native-dev'], { accountId: 'acc' }), /No D1 binding/);
  assert.deepEqual(planMigrations(cfg('P', 'D'), [], { accountId: 'acc' }), []);
});
test('same database_id under a different account is a different physical D1', () => {
  const a = planMigrations(cfg('X', 'D'), ['native-dev'], { accountId: 'a1' })[0].id;
  const b = planMigrations(cfg('X', 'D'), ['native-dev'], { accountId: 'a2' })[0].id;
  assert.notEqual(a, b);
});
