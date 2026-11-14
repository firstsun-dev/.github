// CLI contract: stdout = ONLY key=value records (it is piped into $GITHUB_OUTPUT), diagnostics/annotations = stderr, failure = non-zero.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { targetDescriptor } from '../../actions/cf-preview/delivery.mjs';

const CLI = new URL('../../actions/cf-preview/cli.mjs', import.meta.url).pathname;
const run = (args, env = {}) => spawnSync('node', [CLI, ...args], { encoding: 'utf8', env: { ...process.env, ...env } });
const RECORD = /^[A-Za-z_][\w.-]*=.*$/;
const records = (out) => out.split('\n').filter(Boolean);

const PROD = { target: 'production', driver: 'worker' };
const DEV_N = { target: 'dev', driver: 'native-preview' };

const planCli = (args, env) => run(['plan', '--run-id', '9', ...args], { GITHUB_REPOSITORY: 'o/r', ...env });
const out2obj = (stdout) => Object.fromEntries(records(stdout).map((l) => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));

test('plan: a main push plans ONE Production delivery; a feature push plans none', () => {
  const inputs = { app_name: 'app', preview_policy: 'pr', dev_driver: 'native-preview', approved_dev_ref: 'refs/heads/main' };
  const main = planCli(['--event', 'push', '--ref', 'refs/heads/main'], { INPUTS_JSON: JSON.stringify(inputs) });
  assert.equal(main.status, 0, main.stderr);
  for (const line of records(main.stdout)) assert.match(line, RECORD);
  const m = out2obj(main.stdout);
  assert.deepEqual([m.has_delivery, m.target, m.driver, m.group, m.has_migrations], ['true', 'production', 'worker', 'cfv2-prod-app', 'false']);
  assert.equal(JSON.parse(m.descriptor).environment, 'app/prod');
  const feat = out2obj(planCli(['--event', 'push', '--ref', 'refs/heads/feat/x'], { INPUTS_JSON: JSON.stringify(inputs) }).stdout);
  assert.deepEqual([feat.has_delivery, feat.target, feat.descriptor], ['false', '', '']);
});

test('plan: a same-repo PR plans the Preview, which never migrates (d1_migrations is switched off in the effective inputs); a fork PR plans nothing', () => {
  const inputs = { app_name: 'app', preview_policy: 'pr', d1_migrations: true };
  const pr = out2obj(planCli(['--event', 'pull_request', '--ref', 'refs/pull/4/merge', '--pr-action', 'synchronize', '--pr-number', '4'], { INPUTS_JSON: JSON.stringify(inputs) }).stdout);
  assert.deepEqual([pr.target, pr.driver, pr.has_migrations, pr.group], ['preview', 'native-preview', 'false', 'cfv2-app-pr-4']);
  assert.equal(JSON.parse(pr.inputs_json).d1_migrations, false);
  const fork = out2obj(planCli(['--event', 'pull_request', '--ref', 'refs/pull/4/merge', '--fork', 'true', '--pr-number', '4'], { INPUTS_JSON: JSON.stringify(inputs) }).stdout);
  assert.equal(fork.has_delivery, 'false');
});

test('plan: an unservable request fails with the ORIGINAL error and leaves $GITHUB_OUTPUT untouched (piped like the workflow does)', () => {
  const dir = mkdtempSync(join(tmpdir(), 'cli-'));
  const out = join(dir, 'output');
  writeFileSync(out, '');
  const inputs = { app_name: 'app', dev_driver: 'native-preview', approved_dev_ref: 'refs/heads/main', dispatch_target: 'production' };
  const r = spawnSync('bash', ['-c', `set -eo pipefail; node "${CLI}" plan --event workflow_dispatch --ref refs/heads/main --run-id 9 | tee -a "${out}"`], {
    encoding: 'utf8', env: { ...process.env, INPUTS_JSON: JSON.stringify(inputs), GITHUB_REPOSITORY: 'o/r' },
  });
  assert.notEqual(r.status, 0, 'fail closed');
  assert.match(r.stderr, /::error::a manual dispatch only promotes to Dev/);
  assert.equal(readFileSync(out, 'utf8'), '', 'nothing written to $GITHUB_OUTPUT, so GitHub cannot add an "Invalid format" error');
});

test('plan: a manual Dev promotion from the approved ref plans Dev with the configured driver and the Dev lock', () => {
  const inputs = { app_name: 'app', dev_driver: 'native-preview', approved_dev_ref: 'refs/heads/main', dispatch_target: 'dev' };
  const r = planCli(['--event', 'workflow_dispatch', '--ref', 'refs/heads/main'], { INPUTS_JSON: JSON.stringify(inputs) });
  assert.equal(r.status, 0, r.stderr);
  const o = out2obj(r.stdout);
  assert.deepEqual([o.target, o.driver, o.group, JSON.parse(o.descriptor).environment], ['dev', 'native-preview', 'cfv2-shared-dev-app', 'app/native-dev']);
});

test('plan: a valid verify hand-off restores the SAME descriptor with phase verify; a tampered D1 is rejected', () => {
  const inputs = { app_name: 'app', preview_policy: 'none' };
  const st = { inputs, group: 'cfv2-prod-app', descriptor: targetDescriptor({ delivery: PROD, app: 'app', runId: 1, phase: 'deploy' }) };
  const ok = planCli(['--event', 'push', '--ref', 'refs/heads/main', '--phase', 'verify'], { INPUTS_JSON: JSON.stringify(inputs), PHASE_STATE: JSON.stringify(st) });
  assert.equal(ok.status, 0, ok.stderr);
  for (const line of records(ok.stdout)) assert.match(line, RECORD);
  assert.equal(JSON.parse(out2obj(ok.stdout).descriptor).phase, 'verify');
  const bad = planCli(['--event', 'push', '--ref', 'refs/heads/main', '--phase', 'verify'], { INPUTS_JSON: JSON.stringify(inputs), PHASE_STATE: JSON.stringify({ ...st, d1: 'REAL' }) });
  assert.notEqual(bad.status, 0);
  assert.match(bad.stderr, /::error::descriptor binds D1 '' but the hand-off recorded 'REAL'/);
  assert.equal(bad.stdout, '');
});

test('every failing command keeps stdout record-clean: unknown command, bad inputs, migration gate', () => {
  const cases = [
    [['nope'], {}],
    [['verification-outcome'], {}],
    [['migration-gate'], { DESCRIPTOR: 'not json' }],
    [['init-checks', '--state', tmpdir(), '--inputs', '/nonexistent'], { ENGINE_TARGET: 'prod' }],
  ];
  for (const [args, env] of cases) {
    const r = run(args, env);
    for (const line of records(r.stdout)) assert.match(line, RECORD, `${args[0]}: stdout line '${line}' would corrupt $GITHUB_OUTPUT`);
    if (r.status !== 0) assert.equal(r.stdout.split('\n').filter((l) => l.startsWith('::')).length, 0, `${args[0]}: annotations must be on stderr`);
  }
  assert.notEqual(run(['nope']).status, 0);
});

test('migration gate: a failed Migration job is annotated on stderr with a non-zero exit and clean stdout', () => {
  const d = targetDescriptor({ delivery: PROD, app: 'app', runId: 1, d1ExpectedId: 'DB1' });
  const env = { DESCRIPTOR: JSON.stringify(d) };
  const r = run(['migration-gate'], { ...env, MIGRATION_RESULT: 'failure', MIGRATION_JSON: '{}' });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /finished 'failure'/);
  assert.equal(r.stdout, '');
  const ok = run(['migration-gate'], { ...env, MIGRATION_RESULT: 'success', MIGRATION_JSON: JSON.stringify({ migration: 'applied', kind: 'production', d1_id: 'DB1' }) });
  assert.equal(ok.status, 0, ok.stderr);
  assert.match(ok.stdout, /^migration_used=\{.*\}$/m);
});

test('add-mask for Preview secrets goes to stderr, not into any output file', () => {
  const dir = mkdtempSync(join(tmpdir(), 'cli-'));
  const inputsFile = join(dir, 'inputs.json');
  writeFileSync(inputsFile, JSON.stringify({ app_name: 'app', preview_policy: 'pr', production_rollback_policy: 'manual', build_cmd: 'true' }));
  const d = targetDescriptor({ delivery: { target: 'preview', driver: 'native-preview' }, app: 'app', runId: 1 });
  const r = run(['init-checks', '--state', dir, '--inputs', inputsFile], {
    ENGINE_TARGET: 'preview', ENGINE_KIND: 'native-branch', ENGINE_STAGE: 'publish', ENGINE_DESCRIPTOR: JSON.stringify(d), GITHUB_EVENT_NAME: 'pull_request', GITHUB_REF: 'refs/pull/3/merge', PREVIEW_SECRETS: 'API_KEY=s3cret',
  });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stderr, /::add-mask::s3cret/);
  for (const line of records(r.stdout)) assert.match(line, RECORD);
  assert.doesNotMatch(r.stdout, /s3cret/);
});

// ---- migration-pending: real directory, real `wrangler d1 execute --json` shaped files (the precheck's file discovery must equal Wrangler's)
const pending = (names, applied, { table = true, setup = () => {}, appliedText } = {}) => {
  const d = mkdtempSync(join(tmpdir(), 'mp-')); const dir = join(d, 'm'); mkdirSync(dir);
  for (const n of names) writeFileSync(join(dir, n), 'select 1;');
  setup(dir);
  const ex = join(d, 'e.json'); const ap = join(d, 'a.json');
  writeFileSync(ex, JSON.stringify([{ results: table ? [{ name: 'd1_migrations' }] : [], success: true }]));
  writeFileSync(ap, appliedText ?? JSON.stringify([{ results: applied.map((name) => ({ name })), success: true }]));
  const r = run(['migration-pending', '--dir', dir, '--exists', ex, ...(table ? ['--applied', ap] : [])]);
  return { status: r.status, out: r.stdout, stderr: r.stderr };
};
// The decision rules (pendingMigrations / classifyMigrationEntries) are unit-tested in delivery.test.mjs; here only the CLI contract: it reads a REAL directory the way Wrangler discovers it.
test('migration-pending: reads a real directory (spaces, Unicode, non-.sql files, a missing table) and prints the verdict records', () => {
  const all = ['0001_init.sql', '0002_add status.sql', '0003_調整.sql'];
  assert.equal(pending(all, all).out, 'pending=false\npending_count=0\n');
  assert.equal(pending([...all, 'README.md'], all).out, 'pending=false\npending_count=0\n');
  assert.equal(pending(all, ['0001_init.sql']).out, 'pending=true\npending_count=2\n');
  assert.equal(pending(['0001.sql'], [], { table: false }).out, 'pending=true\npending_count=1\n');
});
test('migration-pending: a directory, symlink, dotfile or .SQL entry the precheck cannot place with certainty -> pending=true, reported on stderr, never dropped', () => {
  for (const [label, setup] of [['directory named x.sql', (dir) => mkdirSync(join(dir, 'x.sql'))], ['symlink', (dir) => symlinkSync(join(dir, '0001.sql'), join(dir, 'link.sql'))],
    ['dotfile', (dir) => writeFileSync(join(dir, '.h.sql'), 'select 1;')], ['upper-case extension', (dir) => writeFileSync(join(dir, 'U.SQL'), 'select 1;')]]) {
    const r = pending(['0001.sql'], ['0001.sql'], { setup });
    assert.match(r.out, /^pending=true\n/, label);
    assert.match(r.stderr, /cannot classify/, label);
  }
});
test('migration-pending: malformed or failed query output fails closed (non-zero, no verdict on stdout)', () => {
  for (const bad of ['not json', '{}', '[]', '[{"results":[],"success":false}]']) {
    const r = pending(['0001.sql'], [], { appliedText: bad });
    assert.notEqual(r.status, 0, bad); assert.equal(r.out, '', bad);
  }
});
