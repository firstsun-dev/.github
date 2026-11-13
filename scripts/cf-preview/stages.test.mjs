import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseTasks, routeEvent, ciTargetEnv, runnerFor, planCi, gateVerdict, MAX_TASKS, HOSTED_RUNNER, APPROVED_HOSTED_RUNNERS } from '../../actions/cf-preview/stages.mjs';

const T = (id, cmd = 'true') => ({ id, cmd });

test('parseTasks: empty stage is valid, valid list round-trips', () => {
  assert.deepEqual(parseTasks(''), []);
  assert.deepEqual(parseTasks('  '), []);
  assert.deepEqual(parseTasks('[]'), []);
  assert.deepEqual(parseTasks(JSON.stringify([T('lint', 'pnpm lint'), T('unit-2')])), [T('lint', 'pnpm lint'), T('unit-2')]);
});

test('parseTasks: malformed configuration is rejected before anything runs', () => {
  const bad = {
    'invalid json': '[{',
    'not an array': '{"id":"a","cmd":"x"}',
    'non-object entry': '["lint"]',
    'unknown option': JSON.stringify([{ id: 'a', cmd: 'x', timeout: 5 }]),
    'uppercase id': JSON.stringify([T('Lint')]),
    'space in id': JSON.stringify([T('a b')]),
    'missing id': JSON.stringify([{ cmd: 'x' }]),
    'empty cmd': JSON.stringify([T('a', '  ')]),
    'missing cmd': JSON.stringify([{ id: 'a' }]),
    'duplicate id': JSON.stringify([T('a'), T('a')]),
    'too many': JSON.stringify(Array.from({ length: MAX_TASKS + 1 }, (_, i) => T(`t${i}`))),
  };
  for (const [name, text] of Object.entries(bad)) assert.throws(() => parseTasks(text, 'verify_tasks'), /verify_tasks/, name);
});

test('routeEvent: default event strategy', () => {
  assert.deepEqual(routeEvent({ event: 'push', ref: 'refs/heads/feat/x' }).stages, ['verify']);
  assert.deepEqual(routeEvent({ event: 'push', ref: 'refs/heads/main' }).stages, ['verify']);
  assert.deepEqual(routeEvent({ event: 'pull_request', ref: 'refs/pull/1/merge' }).stages, ['verify', 'test']);
  assert.deepEqual(routeEvent({ event: 'merge_group', ref: 'refs/heads/gh-readonly-queue/main/x' }).stages, ['verify', 'test']);
  assert.deepEqual(routeEvent({ event: 'workflow_dispatch', ref: 'refs/heads/feat/x' }).stages, ['verify']);
  assert.deepEqual(routeEvent({ event: 'delete', ref: 'refs/heads/x' }).stages, []);
  assert.deepEqual(routeEvent({ event: 'push', ref: 'refs/tags/v1' }).stages, []);
  assert.deepEqual(routeEvent({ event: 'schedule' }).stages, []);
});

test('ciTargetEnv: production env only for a main push', () => {
  assert.equal(ciTargetEnv({ event: 'push', ref: 'refs/heads/main' }), 'prod');
  assert.equal(ciTargetEnv({ event: 'push', ref: 'refs/heads/feat' }), 'preview');
  assert.equal(ciTargetEnv({ event: 'pull_request', ref: 'refs/pull/1/merge' }), 'preview');
  assert.equal(ciTargetEnv({ event: 'workflow_dispatch', ref: 'refs/heads/main' }), 'dev');
});

test('runnerFor: a fork PR always gets the approved hosted runner, never the privileged one, and a caller cannot choose', () => {
  assert.equal(runnerFor({ event: 'pull_request', isFork: true, runner: 'linux' }), HOSTED_RUNNER);
  assert.equal(runnerFor({ event: 'pull_request', isFork: true, runner: 'self-hosted-big' }), HOSTED_RUNNER, 'the caller runner label is ignored for untrusted code');
  assert.ok(APPROVED_HOSTED_RUNNERS.includes(HOSTED_RUNNER) && /^ubuntu-\d/.test(HOSTED_RUNNER));
  assert.equal(runnerFor({ event: 'pull_request', isFork: false, runner: 'linux' }), 'linux');
  assert.equal(runnerFor({ event: 'push', isFork: false, runner: 'linux' }), 'linux');
});

test('planCi: stage selection, empty stages and E2E', () => {
  const base = { verifyTasks: JSON.stringify([T('lint'), T('build')]), testTasks: '', e2eCmd: 'pnpm e2e' };
  let p = planCi({ ...base, event: 'push', ref: 'refs/heads/f' });
  assert.deepEqual([p.run_verify, p.run_test, p.run_e2e], [true, false, false]);
  p = planCi({ ...base, event: 'pull_request', ref: 'refs/pull/1/merge' });
  assert.deepEqual([p.run_verify, p.run_test, p.run_e2e], [true, false, true], 'empty Test list is valid; E2E still runs on PR');
  p = planCi({ event: 'pull_request', ref: 'refs/pull/1/merge' });
  assert.deepEqual([p.run_verify, p.run_test, p.run_e2e], [false, false, false], 'a fully empty configuration plans nothing (gate decides)');
  assert.throws(() => planCi({ ...base, verifyTasks: '[{"id":"a"}]', event: 'push', ref: 'refs/heads/f' }), /cmd/);
});

const ok = { plan: 'success', verify: 'success', test: 'success', e2e: 'success' };
test('gateVerdict: configured must succeed, unconfigured must be skipped, plan must succeed', () => {
  assert.equal(gateVerdict({ planOk: true, expected: { verify: true, test: true, e2e: true }, results: ok }).passed, true);
  assert.equal(gateVerdict({ planOk: true, expected: { verify: true, test: false, e2e: false }, results: { ...ok, test: 'skipped', e2e: 'skipped' } }).passed, true);
  assert.equal(gateVerdict({ planOk: true, expected: { verify: false, test: false, e2e: false }, results: { ...ok, verify: 'skipped', test: 'skipped', e2e: 'skipped' } }).passed, true, 'intentionally empty');
  for (const bad of ['failure', 'cancelled', 'skipped', '']) {
    const g = gateVerdict({ planOk: true, expected: { verify: true, test: true, e2e: false }, results: { ...ok, test: bad, e2e: 'skipped' } });
    assert.equal(g.passed, false, `expected-test result '${bad}'`);
  }
  assert.equal(gateVerdict({ planOk: true, expected: { verify: true, test: false, e2e: false }, results: { ...ok, test: 'success', e2e: 'skipped' } }).passed, false, 'unexpected run');
  assert.equal(gateVerdict({ planOk: false, expected: { verify: false, test: false, e2e: false }, results: { plan: 'failure' } }).passed, false, 'plan failure');
  assert.equal(gateVerdict({ planOk: false, expected: { verify: true, test: false, e2e: false }, results: { plan: 'skipped' } }).passed, false, 'plan skipped');
});
