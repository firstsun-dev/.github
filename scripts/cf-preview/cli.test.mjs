// CLI contract: stdout = ONLY key=value records (it is piped into $GITHUB_OUTPUT), diagnostics/annotations = stderr, failure = non-zero.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { targetDescriptor, buildDescriptors } from '../../actions/cf-preview/delivery.mjs';

const CLI = new URL('../../actions/cf-preview/cli.mjs', import.meta.url).pathname;
const run = (args, env = {}) => spawnSync('node', [CLI, ...args], { encoding: 'utf8', env: { ...process.env, ...env } });
const RECORD = /^[A-Za-z_][\w.-]*=.*$/;
const records = (out) => out.split('\n').filter(Boolean);

test('invalid descriptor: non-zero exit, annotation on stderr, stdout empty (cannot corrupt $GITHUB_OUTPUT)', () => {
  const r = run(['descriptor-check', '--kind', 'production', '--target', 'prod', '--d1', ''], { DESCRIPTOR: '{"v":1,"kind":"production"}' });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /^::error::target descriptor keys/m);
  assert.equal(r.stdout, '');
});

test('valid descriptor: unchanged output records on stdout, nothing on stderr', () => {
  const d = targetDescriptor({ kind: 'production', app: 'app', runId: 1, smokeMode: 'skip' });
  const r = run(['descriptor-check', '--kind', 'production', '--target', 'prod', '--d1', ''], { DESCRIPTOR: JSON.stringify(d) });
  assert.equal(r.status, 0);
  assert.equal(r.stdout, 'descriptor=ok\nsmoke_mode=skip\n');
  assert.equal(r.stderr, '');
});

test('plan: a rejected phase_state fails with the ORIGINAL validation error and leaves a valid output file (piped like the workflow does)', () => {
  const dir = mkdtempSync(join(tmpdir(), 'cli-'));
  const out = join(dir, 'output');
  writeFileSync(out, '');
  const st = { inputs: { app_name: 'app' }, kinds: ['production'], d1: { production: 'REAL' }, migration: {}, group: 'cfv2-prod-app',
    descriptors: { ...buildDescriptors({ kinds: ['production'], app: 'app', runId: 1, phase: 'deploy' }) } };
  st.descriptors.production.d1_expected_id = 'FOREIGN';
  const r = spawnSync('bash', ['-c', `set -eo pipefail; node "${CLI}" plan --event push --ref refs/heads/main --phase verify --run-id 9 | tee -a "${out}"`], {
    encoding: 'utf8', env: { ...process.env, INPUTS_JSON: '{"app_name":"app"}', PHASE_STATE: JSON.stringify(st), GITHUB_REPOSITORY: 'o/r' },
  });
  assert.notEqual(r.status, 0, 'fail closed');
  assert.match(r.stderr, /::error::descriptor for production binds D1 'FOREIGN' but the hand-off recorded 'REAL'/);
  assert.equal(readFileSync(out, 'utf8'), '', 'nothing written to $GITHUB_OUTPUT, so GitHub cannot add an "Invalid format" error');
});

test('plan: a valid verify hand-off still produces only well-formed records', () => {
  const inputs = { app_name: 'app', deployment_strategy: 'production-preview', preview_on_branch_push: true };
  const st = { inputs, kinds: ['production'], d1: {}, migration: {}, group: 'cfv2-prod-app', descriptors: buildDescriptors({ kinds: ['production'], app: 'app', runId: 1, phase: 'deploy' }) };
  const r = run(['plan', '--event', 'push', '--ref', 'refs/heads/main', '--phase', 'verify', '--run-id', '9'], { INPUTS_JSON: JSON.stringify(inputs), PHASE_STATE: JSON.stringify(st), GITHUB_REPOSITORY: 'o/r' });
  assert.equal(r.status, 0, r.stderr);
  for (const line of records(r.stdout)) assert.match(line, RECORD);
  assert.ok(records(r.stdout).includes('has_targets=true'));
  assert.ok(records(r.stdout).some((l) => l.startsWith('descriptors={')));
});

test('every failing command keeps stdout record-clean: unknown command, bad kind, bad inputs, migration gate', () => {
  const cases = [
    [['nope'], {}],
    [['smoke-mode', '--kind', 'bogus', '--mode', 'full'], {}],
    [['verification-outcome'], {}],
    [['migration-gate', '--kind', 'production'], { DESCRIPTOR: 'not json' }],
    [['init-checks', '--state', tmpdir(), '--inputs', '/nonexistent'], { ENGINE_TARGET: 'prod' }],
  ];
  for (const [args, env] of cases) {
    const r = run(args, env);
    for (const line of records(r.stdout)) assert.match(line, RECORD, `${args[0]}: stdout line '${line}' would corrupt $GITHUB_OUTPUT`);
    if (r.status !== 0) assert.equal(r.stdout.split('\n').filter((l) => l.startsWith('::')).length, 0, `${args[0]}: annotations must be on stderr`);
  }
  assert.notEqual(run(['nope']).status, 0);
  assert.notEqual(run(['smoke-mode', '--kind', 'bogus', '--mode', 'full']).status, 0);
});

test('migration gate: a failed Migration job is annotated on stderr with a non-zero exit and clean stdout', () => {
  const d = targetDescriptor({ kind: 'production', app: 'app', runId: 1, d1ExpectedId: 'DB1', ownerKind: 'production' });
  const env = { DESCRIPTOR: JSON.stringify(d), D1_MIGRATIONS: 'true' };
  const r = run(['migration-gate', '--kind', 'production'], { ...env, MIGRATION_RESULT: 'failure', MIGRATION_JSON: '{}' });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /finished 'failure'/);
  assert.equal(r.stdout, '');
  const ok = run(['migration-gate', '--kind', 'production'], { ...env, MIGRATION_RESULT: 'success', MIGRATION_JSON: JSON.stringify({ migration: 'applied', kind: 'production', d1_id: 'DB1' }) });
  assert.equal(ok.status, 0, ok.stderr);
  assert.match(ok.stdout, /^migration_used=\{.*\}$/m);
});

test('add-mask for Preview secrets goes to stderr, not into any output file', () => {
  const dir = mkdtempSync(join(tmpdir(), 'cli-'));
  const inputsFile = join(dir, 'inputs.json');
  writeFileSync(inputsFile, JSON.stringify({ app_name: 'app', deployment_strategy: 'production-preview', production_rollback_policy: 'manual', build_cmd: 'true', preview_on_branch_push: true }));
  const r = run(['init-checks', '--state', dir, '--inputs', inputsFile], { ENGINE_TARGET: 'preview', ENGINE_STAGE: 'migrate', GITHUB_EVENT_NAME: 'push', GITHUB_REF: 'refs/heads/feat/x', PREVIEW_SECRETS: 'API_KEY=s3cret' });
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
test('migration-pending: standard file recorded remotely -> nothing pending', () => {
  assert.equal(pending(['0001_init.sql'], ['0001_init.sql']).out, 'pending=false\npending_count=0\n');
});
test('migration-pending: a file with a space that is not recorded is PENDING', () => {
  assert.equal(pending(['0001_init.sql', '0002_add status.sql'], ['0001_init.sql']).out, 'pending=true\npending_count=1\n');
});
test('migration-pending: a Unicode file name that is not recorded is PENDING', () => {
  assert.equal(pending(['0001_init.sql', '0003_調整.sql'], ['0001_init.sql']).out, 'pending=true\npending_count=1\n');
});
test('migration-pending: several files, only the standard names recorded -> pending', () => {
  assert.equal(pending(['0001_init.sql', '0002_add status.sql', '0003_調整.sql', '0004_plain.sql'], ['0001_init.sql', '0004_plain.sql']).out, 'pending=true\npending_count=2\n');
});
test('migration-pending: every file recorded (spaces and Unicode included) -> verified nothing pending', () => {
  const all = ['0001_init.sql', '0002_add status.sql', '0003_調整.sql'];
  assert.equal(pending(all, all).out, 'pending=false\npending_count=0\n');
});
test('migration-pending: non-.sql files are not migrations; a missing migrations table means everything pending', () => {
  assert.equal(pending(['0001.sql', 'README.md'], ['0001.sql']).out, 'pending=false\npending_count=0\n');
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
