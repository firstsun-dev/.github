import test from 'node:test';
import assert from 'node:assert/strict';
import {
  TARGETS, DRIVERS, assertDelivery, slotOf, engineTarget, environmentName, resolveDelivery, resolveSmokeMode, parseSmokeOverrides, planMigration,
  canonicalOrigin, previewUrlFromSuffix, resolveCanonicalUrl, checkBuildEnvOrigins, foreignUrlsInBuildEnv, sameUrl,
  targetDescriptor, validateDescriptor, buildDescriptor, parseDescriptor, resolveMigrationUsed, parseD1Rows, pendingMigrations, classifyMigrationEntries, VERIFIED_NO_PENDING,
  isContentLevel, verificationOutcome, prodRollbackBlock, nonProdRecoveryBlock, rollbackVerdict, restoreDescriptor, validateInit, runLockGroup,
} from '../../actions/cf-preview/delivery.mjs';

const PROD = { target: 'production', driver: 'worker' };
const PREV = { target: 'preview', driver: 'native-preview' };
const DEV_N = { target: 'dev', driver: 'native-preview' };
const DEV_W = { target: 'dev', driver: 'worker' };

// ---- vocabulary and target policy ------------------------------------------------------------------------------------
test('three targets x two drivers; production -> worker, preview -> native-preview, dev -> either', () => {
  assert.deepEqual(TARGETS, ['production', 'preview', 'dev']);
  assert.deepEqual(DRIVERS, ['worker', 'native-preview']);
  for (const d of [PROD, PREV, DEV_N, DEV_W]) assert.equal(assertDelivery(d), d);
  assert.throws(() => assertDelivery({ target: 'production', driver: 'native-preview' }), /worker driver only/);
  assert.throws(() => assertDelivery({ target: 'preview', driver: 'worker' }), /native-preview driver only/);
  assert.throws(() => assertDelivery({ target: 'staging', driver: 'worker' }), /unknown delivery target/);
  assert.throws(() => assertDelivery({ target: 'dev', driver: 'x' }), /unknown delivery driver/);
});
test('engine class and persistence slot are derived once; Environment names are the historical ones', () => {
  assert.deepEqual([PROD, PREV, DEV_N, DEV_W].map(engineTarget), ['prod', 'preview', 'preview', 'dev']);
  assert.deepEqual([PROD, PREV, DEV_N, DEV_W].map(slotOf), ['production', 'native-branch', 'native-dev', 'persistent-dev']);
  assert.deepEqual([PROD, PREV, DEV_N, DEV_W].map((d) => environmentName('app', d)), ['app/prod', 'app/preview', 'app/native-dev', 'app/dev']);
});

// ---- event -> delivery routing (THE policy table) ---------------------------------------------------------------------
const R = (o) => resolveDelivery({ event: 'push', ref: 'refs/heads/feat/x', ...o });
const DEVCFG = { devDriver: 'native-preview', approvedDevRef: 'refs/heads/main' };
test('routing: a feature push never delivers, whatever is enabled', () => {
  for (const o of [{}, { previewPolicy: 'pr' }, DEVCFG, { previewPolicy: 'pr', ...DEVCFG }]) assert.equal(R(o), null);
});
test('routing: main push is Production (worker); nothing else is automatic', () => {
  assert.deepEqual(R({ ref: 'refs/heads/main' }), PROD);
  assert.deepEqual(R({ ref: 'refs/heads/main', previewPolicy: 'pr', ...DEVCFG }), PROD, 'enabled Preview / Dev never add a second target');
  assert.equal(R({ ref: 'refs/tags/v1' }), null);
});
test('routing: a same-repo PR gets ONE Preview only with preview_policy pr; fork, closed, none -> nothing', () => {
  const pr = (o) => resolveDelivery({ event: 'pull_request', ref: 'refs/pull/7/merge', previewPolicy: 'pr', ...o });
  assert.deepEqual(pr({ prAction: 'synchronize' }), PREV);
  assert.deepEqual(pr({ prAction: 'opened' }), PREV);
  assert.equal(pr({ isFork: true }), null, 'fork PR never deploys');
  assert.equal(pr({ prAction: 'closed' }), null);
  assert.equal(pr({ previewPolicy: 'none' }), null);
});
test('routing: merge_group, delete and other events never deliver', () => {
  for (const event of ['merge_group', 'delete', 'schedule', 'release']) assert.equal(resolveDelivery({ event, ref: 'refs/heads/main', previewPolicy: 'pr', ...DEVCFG }), null, event);
});
test('routing: manual Dev promotion needs dispatch_target dev, a dev driver and EXACTLY the approved ref', () => {
  const d = (o) => resolveDelivery({ event: 'workflow_dispatch', ref: 'refs/heads/main', dispatchTarget: 'dev', ...DEVCFG, ...o });
  assert.deepEqual(d({}), DEV_N);
  assert.deepEqual(d({ devDriver: 'worker' }), DEV_W);
  assert.throws(() => d({ ref: 'refs/heads/feat/x' }), /not the approved_dev_ref/);
  assert.throws(() => d({ ref: 'refs/tags/v1' }), /not the approved_dev_ref/);
  assert.throws(() => d({ devDriver: 'none', approvedDevRef: '' }), /no Dev/);
  assert.throws(() => d({ dispatchTarget: '' }), /only promotes to Dev/);
  assert.throws(() => d({ dispatchTarget: 'production' }), /only promotes to Dev/, 'a manual dispatch can never reach Production');
  assert.throws(() => d({ dispatchTarget: 'persistent-dev' }), /only promotes to Dev/, 'the old kind name is gone');
});
test('routing: an explicit dispatch_target on any other event is refused, never ignored', () => {
  for (const event of ['push', 'pull_request', 'delete', 'merge_group']) assert.throws(() => R({ event, dispatchTarget: 'dev', ...DEVCFG }), /only valid for a workflow_dispatch/, event);
});
test('routing: invalid policy values fail closed', () => {
  assert.throws(() => R({ previewPolicy: 'branch' }), /preview_policy/);
  assert.throws(() => R({ devDriver: 'both', approvedDevRef: 'refs/heads/main' }), /dev_driver/);
  assert.throws(() => R({ devDriver: 'worker' }), /requires approved_dev_ref/);
  assert.throws(() => R({ devDriver: 'worker', approvedDevRef: 'main' }), /branch ref/);
});

// ---- smoke mode ------------------------------------------------------------------------------------------------------
test('smoke_mode: default full, per-target override wins, strict validation', () => {
  assert.equal(resolveSmokeMode('production'), 'full');
  assert.equal(resolveSmokeMode('dev', 'basic'), 'basic');
  assert.equal(resolveSmokeMode('dev', 'full', '{"dev":"skip"}'), 'skip');
  assert.equal(resolveSmokeMode('production', 'full', '{"dev":"skip"}'), 'full');
  assert.throws(() => resolveSmokeMode('production', 'sometimes'), /Unknown smoke_mode/);
  assert.throws(() => resolveSmokeMode('native-dev'), /unknown target/);
  assert.throws(() => parseSmokeOverrides('{"native-dev":"basic"}'), /unknown target/);
  assert.throws(() => parseSmokeOverrides('{"dev":"maybe"}'), /unknown smoke mode/);
  assert.throws(() => parseSmokeOverrides('[]'), /JSON object/);
  assert.throws(() => parseSmokeOverrides('{'), /not valid JSON/);
  assert.deepEqual(parseSmokeOverrides(''), {});
});

// ---- migration plan: ONE delivery, ONE physical D1 -------------------------------------------------------------------------
const cfg = (prev, dev, prod = 'PROD') => ({
  name: 'app', d1_databases: [{ binding: 'DB', database_name: 'prod-db', database_id: prod }],
  previews: { d1_databases: [{ binding: 'DB', database_name: 'prev-db', database_id: prev }] },
  env: { dev: { d1_databases: [{ binding: 'DB', database_name: 'dev-db', database_id: dev }] } },
});
test('migration plan: production and dev migrate their own database; account scopes the identity', () => {
  assert.equal(planMigration(cfg('PREV', 'DEV'), PROD, { accountId: 'acc' }).id, 'acc/PROD');
  assert.equal(planMigration(cfg('PREV', 'DEV'), DEV_W, { accountId: 'acc' }).databaseName, 'dev-db');
  assert.equal(planMigration(cfg('PREV', 'DEV'), DEV_N, { accountId: 'acc' }).databaseName, 'prev-db', 'native Dev uses the [previews] database');
  assert.notEqual(planMigration(cfg('X', 'D'), DEV_W, { accountId: 'a1' }).id, planMigration(cfg('X', 'D'), DEV_W, { accountId: 'a2' }).id);
});
test('migration plan: a Preview NEVER migrates a remote D1', () => {
  assert.throws(() => planMigration(cfg('PREV', 'DEV'), PREV, { accountId: 'acc' }), /never migrates/);
});
test('migration plan: the production D1 can never be reached from Dev', () => {
  assert.throws(() => planMigration(cfg('PROD', 'DEV', 'PROD'), DEV_N, { accountId: 'acc' }), /shared across trust boundaries/);
  assert.throws(() => planMigration(cfg('PREV', 'PROD', 'PROD'), DEV_W, { accountId: 'acc' }), /shared across trust boundaries/);
});
test('migration plan: a target without a D1 binding fails closed', () => {
  assert.throws(() => planMigration({ name: 'a' }, DEV_W, { accountId: 'acc' }), /No D1 binding/);
});

// ---- URL helpers ------------------------------------------------------------------------------------------------------
test('build env URL isolation: another target URL in this build env is reported', () => {
  const urls = { production: 'https://app.example.com', 'native-dev': 'https://dev-app.x.workers.dev' };
  assert.deepEqual(foreignUrlsInBuildEnv('native-dev', 'PUBLIC_URL=https://dev-app.x.workers.dev\nIDP=https://id.example.org', urls), []);
  assert.deepEqual(foreignUrlsInBuildEnv('native-dev', 'PUBLIC_URL=https://app.example.com/', urls).length, 1);
  assert.deepEqual(foreignUrlsInBuildEnv('production', 'X=https://dev-app.x.workers.dev', urls).length, 1);
  assert.deepEqual(foreignUrlsInBuildEnv('production', '# c\n\nX=1', urls), []);
});
test('sameUrl ignores case and trailing slashes only', () => {
  assert.ok(sameUrl('https://A.example.com/', 'https://a.example.com'));
  assert.ok(!sameUrl('https://a.example.com/x', 'https://a.example.com'));
});

// ---------------------------------------------------------------------------------------------------------------------
// P1-B: the canonical URL is resolved BEFORE the build from the real Preview identity (Cloudflare documents
// `<preview-name>-<worker-name>.<subdomain>.workers.dev`; the Workers subdomain API reports the host part as preview_url_suffix).
// ---------------------------------------------------------------------------------------------------------------------
test('previewUrlFromSuffix: https://<preview-name><suffix>, never a guessed format', () => {
  assert.equal(previewUrlFromSuffix('feat-x', '-blog.acme.workers.dev'), 'https://feat-x-blog.acme.workers.dev');
  assert.equal(previewUrlFromSuffix('dev', '-blog.acme.workers.dev'), 'https://dev-blog.acme.workers.dev');
  assert.equal(previewUrlFromSuffix('dev', '.app.example.com'), 'https://dev.app.example.com');
  assert.throws(() => previewUrlFromSuffix('Feat_X', '-w.s.workers.dev'), /unsafe Preview name/);
  for (const bad of ['', 'blog.acme.workers.dev', '-', '-a/b', '-a b.workers.dev', 'https://x.dev']) assert.throws(() => previewUrlFromSuffix('x', bad), /suffix/);
  assert.throws(() => previewUrlFromSuffix('a'.repeat(40), '-' + 'w'.repeat(30) + '.s.workers.dev'), /63/);
});
test('canonicalOrigin: only a bare https origin is a valid app URL', () => {
  assert.equal(canonicalOrigin('https://dev-blog.acme.workers.dev/'), 'https://dev-blog.acme.workers.dev');
  for (const bad of ['http://x.dev', 'https://x.dev/path', 'https://x.dev?a=1', 'https://u:p@x.dev', 'nope']) assert.throws(() => canonicalOrigin(bad));
});
test('resolveCanonicalUrl: derived from Cloudflare, cross-checked with the declared URL, fails closed when unresolvable', () => {
  const api = '-blog.acme.workers.dev';
  assert.deepEqual(resolveCanonicalUrl({ previewName: 'feat-x', apiSuffix: api }), { url: 'https://feat-x-blog.acme.workers.dev', source: 'cloudflare' });
  assert.equal(resolveCanonicalUrl({ previewName: 'dev', apiSuffix: api, explicit: 'https://dev-blog.acme.workers.dev' }).source, 'declared+derived');
  assert.equal(resolveCanonicalUrl({ previewName: 'dev', suffix: '.dev.example.com' }).url, 'https://dev.dev.example.com');
  assert.equal(resolveCanonicalUrl({ previewName: 'dev', explicit: 'https://dev.example.com' }).url, 'https://dev.example.com');
  // incorrect URL: the declared one contradicts what Cloudflare will serve -> the build must not start
  assert.throws(() => resolveCanonicalUrl({ previewName: 'dev', apiSuffix: api, explicit: 'https://dev-other.acme.workers.dev' }), /differs from the URL Cloudflare will serve/);
  assert.throws(() => resolveCanonicalUrl({ previewName: 'dev', apiSuffix: api, explicit: 'https://app.example.com' }), /differs/);
  // two suffix sources must agree
  assert.throws(() => resolveCanonicalUrl({ previewName: 'dev', suffix: '-a.b.workers.dev', apiSuffix: api }), /contradicts/);
  // missing URL: nothing declared and Cloudflare reported no suffix -> unresolved (never guessed)
  assert.throws(() => resolveCanonicalUrl({ previewName: 'feat-x' }), /cannot resolve the canonical URL/);
  assert.throws(() => resolveCanonicalUrl({ previewName: 'feat-x', apiSuffix: '' , explicit: '' }), /before the build/);
  assert.throws(() => resolveCanonicalUrl({ previewName: 'dev', explicit: 'http://dev.example.com' }), /https/);
});
test('checkBuildEnvOrigins: this application\'s own callback / redirect / origin variables must be same-origin with the target', () => {
  const url = 'https://dev-id.acme.workers.dev';
  const ok = 'PUBLIC_APP_URL=https://dev-id.acme.workers.dev\nOAUTH_CALLBACK_URL=https://dev-id.acme.workers.dev/auth/callback\nIDP_URL=https://idp.example.com\nPUBLIC_API=https://api.example.com';
  assert.deepEqual(checkBuildEnvOrigins(ok, url), []);
  const bad = checkBuildEnvOrigins('OAUTH_CALLBACK_URL=https://id.example.com/cb\nPUBLIC_APP_ORIGIN=https://feat-x-id.acme.workers.dev\nOIDC_REDIRECT_URI=https://dev-id.acme.workers.dev/cb', url);
  assert.equal(bad.length, 2);
  assert.match(bad[0], /OAUTH_CALLBACK_URL=https:\/\/id\.example\.com/);
  assert.match(bad[1], /PUBLIC_APP_ORIGIN/);
  assert.deepEqual(checkBuildEnvOrigins('A=1\n# c\n\nSITE_URL=notaurl', url), []);
  assert.deepEqual(checkBuildEnvOrigins('OAUTH_CALLBACK_URL=https://x.example.com', ''), []);
});
test('Branch and Native Dev never share a build URL: each resolves its own Preview name', () => {
  const api = '-blog.acme.workers.dev';
  const a = resolveCanonicalUrl({ previewName: 'feat-x', apiSuffix: api }).url;
  const b = resolveCanonicalUrl({ previewName: 'dev', apiSuffix: api }).url;
  assert.notEqual(a, b);
  assert.deepEqual(checkBuildEnvOrigins('PUBLIC_APP_URL=' + a, b).length, 1);
});


// ---- descriptor -------------------------------------------------------------------------------------------------------
const D = (delivery, o = {}) => targetDescriptor({ delivery, app: 'app', runId: '42', repo: 'o/r', ...o });
test('descriptor: derived once from target x driver - engine class, slot, Environment, concurrency groups', () => {
  const p = D(PROD);
  assert.deepEqual([p.target, p.driver, p.slot, p.engine_target, p.environment], ['production', 'worker', 'production', 'prod', 'app/prod']);
  assert.equal(p.deploy_group, 'cfv2-leg-42-production');
  assert.equal(p.rollback_group, 'cfv2-leg-42-production-rollback');
  const w = D(DEV_W, { lockScope: 'shared' });
  assert.equal(w.deploy_group, 'cfv2-dev-o/r-shared', 'Dev Worker keeps the shared-D1 group');
  assert.equal(w.rollback_group, w.deploy_group);
  assert.equal(D(DEV_N).environment, 'app/native-dev');
  assert.equal(D(PREV).engine_target, 'preview');
});
test('descriptor: only a D1-migrating delivery owns a migration; a Preview can never carry a D1', () => {
  assert.equal(D(PROD).owns_migration, false);
  assert.equal(D(DEV_W, { d1ExpectedId: 'DB1' }).owns_migration, true);
  assert.throws(() => D(PREV, { d1ExpectedId: 'DB1' }), /Preview never migrates/);
});
test('descriptor: buildDescriptor resolves the per-target smoke mode', () => {
  assert.equal(buildDescriptor({ delivery: DEV_N, app: 'a', runId: '1', smokeMode: 'full', smokeOverrides: '{"dev":"basic"}' }).smoke_mode, 'basic');
  assert.equal(buildDescriptor({ delivery: PROD, app: 'a', runId: '1', smokeMode: 'full', smokeOverrides: '{"dev":"basic"}' }).smoke_mode, 'full');
});
test('descriptor: validation rejects forged, cross-target, unknown-key and wrong-version records (trust boundary)', () => {
  const d = D(DEV_W, { d1ExpectedId: 'DB1' });
  assert.deepEqual(validateDescriptor({ ...d }), d);
  const bad = {
    'extra key': { ...d, secret: 'x' }, 'missing key': (({ phase, ...r }) => r)(d), 'wrong version': { ...d, v: 1 },
    'bad driver': { ...d, driver: 'native-preview', slot: 'persistent-dev' }, 'slot mismatch': { ...d, slot: 'native-dev' },
    'class mismatch': { ...d, engine_target: 'preview' }, 'foreign environment': { ...d, environment: 'app/prod' },
    'forged group': { ...d, deploy_group: 'cfv2-prod-app', rollback_group: 'cfv2-prod-app' }, 'split dev group': { ...d, rollback_group: 'cfv2-dev-o/r-other' },
    'owner contradiction': { ...d, owns_migration: false }, 'bad d1': { ...d, d1_expected_id: 'a b' }, 'bad smoke': { ...d, smoke_mode: 'none' }, 'bad phase': { ...d, phase: 'later' },
    'not an object': null, 'array': [],
  };
  for (const [name, v] of Object.entries(bad)) assert.throws(() => validateDescriptor(v), /delivery descriptor/, name);
  assert.throws(() => parseDescriptor('{nope'), /not valid JSON/);
  assert.deepEqual(parseDescriptor(JSON.stringify(d)), d);
});
test('descriptor: carries no secret and no URL', () => {
  const t = JSON.stringify(D(PROD, { d1ExpectedId: 'DB1' }));
  assert.doesNotMatch(t, /https?:|token|secret|password/i);
});

// ---- migration gate (publish stage) -----------------------------------------------------------------------------------
const MD = (o = {}) => D(DEV_W, { d1ExpectedId: 'DB1', ...o });
const MJ = (o = {}) => JSON.stringify({ migration: 'applied', schema_changed: 'true', d1_scan: 'additive', kind: 'persistent-dev', d1_id: 'DB1', ...o });
const U = (o = {}) => resolveMigrationUsed({ descriptor: MD(), result: 'success', json: MJ(), ...o });
const SHA = 'a'.repeat(40);
const NP = (o = {}) => JSON.stringify({ precheck: VERIFIED_NO_PENDING, precheck_sha: SHA, migration: 'none', kind: 'persistent-dev', d1_id: 'DB1', ...o });

test('migration used: a delivery without a D1 migration needs nothing (even with garbage lying around)', () => {
  assert.deepEqual(resolveMigrationUsed({ descriptor: D(PROD) }), {});
  assert.deepEqual(resolveMigrationUsed({ descriptor: D(PREV), result: 'skipped', json: JSON.stringify({ stale: 'true' }) }), {});
});
test('migration used: an applied migration of exactly this delivery and D1 authorizes', () => {
  assert.equal(U().d1_id, 'DB1');
  assert.equal(U({ descriptor: D(PROD, { d1ExpectedId: 'P1' }), json: MJ({ kind: 'production', d1_id: 'P1' }) }).kind, 'production');
});
test('migration used: fails closed on a missing, failed, foreign or malformed migration result', () => {
  for (const r of ['failure', 'cancelled', '', 'skipped']) assert.throws(() => U({ result: r }), /finished/, `result '${r}'`);
  assert.throws(() => U({ json: MJ({ migration: 'none' }) }), /no applied migration/);
  for (const j of ['', '{not json']) assert.throws(() => U({ json: j }), /missing or not valid JSON/);
  for (const j of ['[1]', 'null', '"applied"']) assert.throws(() => U({ json: j }), /malformed/);
  assert.throws(() => U({ json: '{}' }), /no applied migration/);
  assert.throws(() => U({ json: MJ({ kind: 'native-dev' }) }), /another delivery/);
  assert.throws(() => U({ json: MJ({ d1_id: 'FOREIGN' }) }), /another D1/);
  assert.throws(() => U({ descriptor: MD({ d1ExpectedId: 'OTHER' }) }), /another D1/);
});
test('migration used: a stale-skipped migration NEVER authorizes a publish (the branch can point at this commit again, A -> B -> A)', () => {
  for (const stale of [true, 'true']) {
    assert.throws(() => U({ json: JSON.stringify({ stale }) }), /became stale/);
    assert.throws(() => U({ json: MJ({ stale }) }), /became stale/, 'even with otherwise valid applied metadata');
    assert.throws(() => U({ json: JSON.stringify({ stale, migration: 'none', kind: 'persistent-dev', d1_id: 'DB1' }) }), /became stale/);
  }
  assert.equal(U({ json: MJ({ stale: 'false' }) }).d1_id, 'DB1');
  assert.throws(() => U({ json: JSON.stringify({ stale: 'false', kind: 'persistent-dev', d1_id: 'DB1' }) }), /no applied migration/);
});
test('migration used: a verified no-pending precheck authorizes only for the exact slot, D1 and commit', () => {
  const r = U({ json: NP(), runSha: SHA });
  assert.deepEqual([r.migration, r.schema_changed, r.d1_scan], ['none', 'false', 'none']);
  assert.throws(() => U({ json: NP(), runSha: 'b'.repeat(40) }), /belongs to commit/);
  assert.throws(() => U({ json: NP(), runSha: '' }), /belongs to commit/);
  assert.throws(() => U({ json: NP({ kind: 'native-dev' }), runSha: SHA }), /another delivery/);
  assert.throws(() => U({ json: NP({ d1_id: 'X' }), runSha: SHA }), /another D1/);
  assert.throws(() => U({ json: NP({ migration: 'applied' }), runSha: SHA }), /both an applied migration and a verified-empty/);
  assert.throws(() => U({ json: NP({ stale: 'true' }), runSha: SHA }), /became stale/);
});

// ---- lock identity ---------------------------------------------------------------------------------------------------------
const LI = { app_name: 'app', d1_migrations: false, preview_policy: 'pr', dev_driver: 'native-preview', approved_dev_ref: 'refs/heads/main' };
test('lock identity: the run-level group follows the resource', () => {
  const g = (o, i = {}) => runLockGroup({ event: 'push', ref: 'refs/heads/main', inputs: { ...LI, ...i }, runId: '9', ...o });
  assert.equal(g({}), 'cfv2-prod-app');
  assert.equal(g({}, { d1_lock_scope: 'shared' }), 'cfv2-prod-shared');
  assert.equal(g({ event: 'workflow_dispatch' }, { dispatch_target: 'dev' }), 'cfv2-shared-dev-app', 'a Dev promotion never takes the Production group, even from main');
  assert.equal(g({ event: 'workflow_dispatch' }, { dispatch_target: 'dev', d1_migrations: true, d1_lock_scope: 's' }), 'cfv2-nonprod-d1-s');
  assert.equal(g({ event: 'pull_request', ref: 'refs/pull/5/merge', prNumber: '5' }), 'cfv2-app-pr-5');
  assert.equal(g({ event: 'pull_request', ref: 'refs/pull/5/merge', prNumber: '5', isFork: true }), 'cfv2-run-9', 'fork PR: private group');
  assert.equal(g({ event: 'pull_request', ref: 'refs/pull/5/merge', prNumber: '5' }, { preview_policy: 'none' }), 'cfv2-run-9');
  assert.equal(g({ ref: 'refs/heads/feat/x' }), 'cfv2-run-9', 'feature push: private group');
  assert.equal(g({ event: 'merge_group', ref: 'refs/heads/gh-readonly-queue/main/x' }), 'cfv2-run-9');
});

// ---- deferred verify (pipeline_phase=verify) ------------------------------------------------------------------------------
const STATE = (o = {}) => ({ inputs: { ...LI, dev_driver: 'worker', dispatch_target: 'dev', d1_migrations: true }, descriptor: D(DEV_W, { d1ExpectedId: 'DB1', phase: 'deploy', repo: 'o/r' }), group: 'cfv2-nonprod-d1-app', ...o });
const RST = (state, o = {}) => restoreDescriptor(state, { repo: 'o/r', runId: '77', event: 'workflow_dispatch', ref: 'refs/heads/main', ...o });
test('deferred verify: deployment-time policy survives; only the phase changes', () => {
  const d = RST(STATE());
  assert.equal(d.phase, 'verify');
  assert.deepEqual({ ...d, phase: 'deploy' }, STATE().descriptor);
});
test('deferred verify: Production and Preview restore too', () => {
  const prod = { inputs: { ...LI }, descriptor: D(PROD, { phase: 'deploy', runId: '5' }), group: 'cfv2-prod-app' };
  assert.equal(restoreDescriptor(prod, { repo: 'o/r', runId: '77', event: 'push', ref: 'refs/heads/main' }).target, 'production');
  const pr = { inputs: { ...LI }, descriptor: D(PREV, { phase: 'deploy', runId: '5' }), group: 'cfv2-app-pr-3' };
  assert.equal(restoreDescriptor(pr, { repo: 'o/r', runId: '77', event: 'pull_request', ref: 'refs/pull/3/merge', prNumber: '3' }).target, 'preview');
});
test('deferred verify: tampering is rejected (group, phase, app, repo, D1, schema)', () => {
  const s = STATE();
  assert.throws(() => RST({ ...s, group: 'cfv2-prod-app' }), /lock identity/);
  assert.throws(() => RST({ ...s, group: '' }), /lock identity/);
  assert.throws(() => RST({ ...s, descriptor: { ...s.descriptor, phase: 'verify' } }), /only a deploy-phase descriptor/);
  assert.throws(() => RST({ ...s, descriptor: D(PROD, { phase: 'deploy' }) }), /not the delivery the deploy inputs plan/, 'swapped target');
  assert.throws(() => RST({ ...s, descriptor: D(DEV_N, { d1ExpectedId: 'DB1', phase: 'deploy', repo: 'o/r' }) }), /not the delivery the deploy inputs plan/, 'swapped driver');
  assert.throws(() => RST({ ...s, inputs: { ...s.inputs, app_name: 'other' } }), /another application|lock identity/);
  assert.throws(() => RST(s, { repo: 'o/other' }), /expected 'cfv2-dev-o\/other/);
  assert.throws(() => RST({ ...s, d1: 'OTHER' }), /hand-off recorded/);
  for (const bad of [null, [], 'x', { inputs: {} }, { ...s, inputs: {} }, { ...s, descriptor: { ...s.descriptor, v: 9 } }]) assert.throws(() => RST(bad));
});

// ---- engine init -----------------------------------------------------------------------------------------------------------
const IN = { app_name: 'app', build_cmd: 'build', production_rollback_policy: 'manual', preview_policy: 'pr', dev_driver: 'native-preview', approved_dev_ref: 'refs/heads/main', app_url_prod: 'https://app.example.com', dev_build_cmd: 'build-dev' };
const I = (delivery, o = {}) => {
  const d = D(delivery, o.dopts);
  return validateInit({ inputs: { ...IN, ...o.inputs }, target: d.engine_target, kind: d.slot, stage: o.stage ?? 'publish', descriptor: JSON.stringify(d), event: 'push', ref: 'refs/heads/main', ...o.ev });
};
test('init: a valid delivery binds slot, class and the smoke policy', () => {
  const r = I(PROD);
  assert.deepEqual([r.slot, r.target, r.stage, r.smokeMode], ['production', 'prod', 'publish', 'full']);
});
test('init: a PR Preview and a manual Dev promotion validate by the same rule as everything else', () => {
  const pr = I(PREV, { ev: { event: 'pull_request', ref: 'refs/pull/3/merge', prAction: 'synchronize' } });
  assert.equal(pr.slot, 'native-branch');
  const dev = I(DEV_N, { ev: { event: 'workflow_dispatch', ref: 'refs/heads/main' }, inputs: { dispatch_target: 'dev' } });
  assert.equal(dev.slot, 'native-dev');
});
test('init: a Preview can never run a migration stage', () => {
  const ev = { event: 'pull_request', ref: 'refs/pull/3/merge', prAction: 'synchronize' };
  for (const stage of ['precheck', 'migrate']) assert.throws(() => I(PREV, { stage, ev }), /never migrates/, stage);
  assert.equal(I(PREV, { stage: 'publish', ev }).slot, 'native-branch');
});
test('init: no phantom delivery - the descriptor must be what THIS event plans', () => {
  assert.throws(() => I(PROD, { ev: { event: 'push', ref: 'refs/heads/feat/x' } }), /delivery mismatch/);
  assert.throws(() => I(PREV, { ev: { event: 'pull_request', ref: 'refs/pull/3/merge', isFork: true } }), /delivery mismatch/);
  assert.throws(() => I(PREV, { ev: { event: 'push', ref: 'refs/heads/main' } }), /delivery mismatch/);
  assert.throws(() => I(DEV_N, { ev: { event: 'workflow_dispatch', ref: 'refs/heads/feat/x' }, inputs: { dispatch_target: 'dev' } }), /not the approved_dev_ref/);
  assert.throws(() => I(DEV_W, { ev: { event: 'workflow_dispatch', ref: 'refs/heads/main' }, inputs: { dispatch_target: 'dev' } }), /delivery mismatch/, 'dev_driver native-preview cannot run as a Worker');
});
test('init: class, slot, D1 and stage bindings refuse everything unknown or cross-target', () => {
  const d = D(PROD);
  const v = (o) => validateInit({ inputs: IN, target: 'prod', kind: 'production', stage: 'publish', descriptor: JSON.stringify(d), event: 'push', ref: 'refs/heads/main', ...o });
  assert.equal(v({}).slot, 'production');
  assert.throws(() => v({ target: 'preview' }), /engine target/);
  assert.throws(() => v({ kind: 'native-dev' }), /slot/);
  assert.throws(() => v({ d1Expect: 'DB1' }), /D1 id differs/);
  assert.throws(() => v({ descriptor: '' }), /descriptor is required/);
  assert.throws(() => v({ stage: 'preflight' }), /bad engine stage/);
  assert.throws(() => v({ stage: 'all' }), /split stages/);
  assert.throws(() => v({ stage: 'x' }), /bad engine stage/);
});
test('init: a Dev Worker needs its identity, URL and a dev build before a D1 migration', () => {
  const dev = { ev: { event: 'workflow_dispatch', ref: 'refs/heads/main' }, stage: 'all' };
  const inputs = (o) => ({ dispatch_target: 'dev', dev_driver: 'worker', dev_worker_name: 'app-dev', app_url_dev: 'https://dev.example.com', ...o });
  assert.equal(I(DEV_W, { ...dev, inputs: inputs({}) }).slot, 'persistent-dev');
  assert.throws(() => I(DEV_W, { ...dev, inputs: inputs({ dev_worker_name: '' }) }), /dev_worker_name/);
  assert.throws(() => I(DEV_W, { ...dev, inputs: inputs({ app_url_dev: '' }) }), /app_url_dev/);
  assert.throws(() => I(DEV_W, { ...dev, inputs: inputs({ d1_migrations: 'true', dev_build_cmd: '' }) }), /dev_build_cmd/);
});
test('init: env data - reserved names and another target URL in a build env are rejected; each target uses ONLY its own env', () => {
  assert.throws(() => I(PROD, { inputs: { build_env_prod: 'CLOUDFLARE_API_TOKEN=x' } }), /CFV2_BUILD_ENV/);
  assert.throws(() => I(PROD, { inputs: { build_env_prod: 'PUBLIC=https://dev.example.com', app_url_dev: 'https://dev.example.com' } }), /dev URL/);
  const r = I(PROD, { inputs: { build_env_prod: 'A=1', build_env_preview: 'A=2', build_env_dev: 'A=3' } });
  assert.deepEqual(r.buildEnv, [['A', '1']]);
});
test('init: deferred verify identity inputs must equal the deploy call\'s', () => {
  const d = D(PROD, { phase: 'verify' });
  const v = (call) => validateInit({ inputs: { ...IN, app_path: '.' }, target: 'prod', kind: 'production', stage: 'verify', descriptor: JSON.stringify(d), callInputs: JSON.stringify(call), event: 'push', ref: 'refs/heads/main' });
  const ok = { app_name: 'app', app_path: '.', production_rollback_policy: 'manual' };
  assert.equal(v(ok).slot, 'production');
  for (const k of ['app_name', 'app_path', 'production_rollback_policy']) assert.throws(() => v({ ...ok, [k]: 'other' }), new RegExp(k));
  assert.throws(() => v('x'), /call inputs/);
});

// ---- verification and recovery decisions (unchanged) -----------------------------------------------------------------
test('verification: ONLY a content-level smoke verifies a deployment', () => {
  for (const l of ['content', 'content-sha']) assert.deepEqual(verificationOutcome(l), { verified: true, kind: 'content' });
  for (const l of ['liveness', 'access', '', undefined, 'CONTENT', 'content ']) assert.equal(verificationOutcome(l).verified, false);
  assert.equal(verificationOutcome('skipped').kind, 'skipped');
  assert.equal(verificationOutcome('liveness').kind, 'liveness');
  assert.equal(isContentLevel('content-sha'), true);
});

const RB = { policy: 'automatic', previous: 'v1', current: 'v2', worker: 'app', d1Pending: false };

test('production rollback guard: every refusal reason, in order, fail closed', () => {
  assert.equal(prodRollbackBlock({ ...RB, live: 'v2' }), null);
  assert.equal(prodRollbackBlock(RB), null, 'without an observed live version only the static checks run');
  for (const p of ['manual', 'never', '']) assert.equal(prodRollbackBlock({ ...RB, policy: p }).code, 'policy');
  assert.equal(prodRollbackBlock({ ...RB, previous: '' }).code, 'no_previous_version');
  assert.equal(prodRollbackBlock({ ...RB, current: '' }).code, 'no_identity');
  assert.equal(prodRollbackBlock({ ...RB, worker: '' }).code, 'no_identity');
  assert.equal(prodRollbackBlock({ ...RB, previous: 'v2' }).code, 'same_version');
  assert.equal(prodRollbackBlock({ ...RB, policy: 'manual', previous: '' }).code, 'policy', 'policy is checked first');
});

test('production rollback guard: no D1 schema rollback - applied migrations need attested expand-contract AND an additive scan', () => {
  const D = { ...RB, d1Pending: true };
  assert.equal(prodRollbackBlock({ ...D, d1Compat: '' }).code, 'd1_unattested');
  assert.equal(prodRollbackBlock({ ...D, d1Compat: 'none', d1Scan: 'additive' }).code, 'd1_unattested');
  for (const scan of ['unknown', 'destructive', '', 'none']) assert.equal(prodRollbackBlock({ ...D, d1Compat: 'expand-contract', d1Scan: scan }).code, 'd1_not_additive');
  assert.match(prodRollbackBlock({ ...D, d1Compat: 'expand-contract', d1Scan: '' }).why, /scan: unknown/);
  assert.equal(prodRollbackBlock({ ...D, d1Compat: 'expand-contract', d1Scan: 'additive', live: 'v2' }), null);
  assert.equal(prodRollbackBlock({ ...RB, d1Pending: false, d1Compat: '', d1Scan: 'destructive' }), null, 'no applied migration: D1 is irrelevant');
});

test('production rollback guard: never replaces a deployment that is no longer ours (stale / out-of-band)', () => {
  assert.equal(prodRollbackBlock({ ...RB, live: 'someone-elses' }).code, 'live_version_changed');
  assert.equal(prodRollbackBlock({ ...RB, live: '' }).code, 'live_version_changed');
  assert.match(prodRollbackBlock({ ...RB, live: '' }).why, /'unknown'/);
});

test('rollback verdict (production): restored_verified only after a content-level re-smoke of the rolled-back version', () => {
  const P = (o) => rollbackVerdict({ target: 'prod', ...o });
  assert.deepEqual(P({ rbResult: 'rolled_back', rbVerify: 'success', rbLevel: 'content-sha' }), { result: 'restored_verified', level: 'content-sha', ok: true });
  assert.equal(P({ rbResult: 'rolled_back', rbVerify: 'success', rbLevel: 'liveness' }).result, 'restored_unverified');
  assert.equal(P({ rbResult: 'rolled_back', rbVerify: 'success', rbLevel: '' }).ok, false);
  assert.equal(P({ rbResult: 'rolled_back', rbVerify: 'failure', rbLevel: 'content' }).result, 'restored_unhealthy');
  assert.equal(P({ rbResult: 'rolled_back', rbVerify: '' }).result, 'restored_unhealthy');
  assert.equal(P({ rbResult: 'blocked' }).result, 'blocked');
  for (const r of ['failed', '', 'weird']) assert.deepEqual(P({ rbResult: r, rbVerify: 'success', rbLevel: 'content' }), { result: 'failed', level: '', ok: false });
});

test('rollback verdict (Preview / Dev): known-good recovery must be rebuilt AND re-verified at content level', () => {
  const R = (o) => rollbackVerdict({ target: 'preview', goodSha: 'abc1234', recovered: 'success', verified: 'success', smokeLevel: 'content', ...o });
  assert.deepEqual(R({}), { result: 'restored_verified', level: 'content', ok: true });
  assert.equal(R({ smokeLevel: 'liveness' }).result, 'restored_unverified');
  assert.equal(R({ goodSha: '' }).result, 'no_known_good');
  assert.equal(R({ goodSha: '', recovered: 'success' }).ok, false);
  assert.equal(R({ recovered: 'failure' }).result, 'failed');
  assert.equal(R({ verified: 'failure' }).result, 'failed');
  assert.equal(R({ recovered: '' }).result, 'failed');
  assert.equal(rollbackVerdict({ target: 'dev', goodSha: 'x', recovered: 'success', verified: 'success', smokeLevel: 'content-sha' }).ok, true);
});

test('nonProdRecoveryBlock: non-Production recovery is fail-closed against the migration result of the same pipeline', () => {
  const M = { migration: 'applied', schema_changed: 'true', d1_scan: 'additive', kind: 'persistent-dev', d1_id: 'D1' };
  const b = (o) => nonProdRecoveryBlock({ d1Migrations: true, migration: M, expectedId: 'D1', d1Compat: 'expand-contract', ...o });
  assert.equal(nonProdRecoveryBlock({ d1Migrations: false, migration: null }), null, 'no D1 migrations: allowed');
  assert.equal(b({ migration: { ...M, schema_changed: 'false', d1_scan: 'none' }, d1Compat: '' }), null, 'no schema change: allowed without attestation');
  assert.equal(b({}), null, 'additive + attested: allowed');
  assert.equal(b({ d1Compat: '' }).code, 'd1_unattested');
  assert.equal(b({ d1Compat: 'none' }).code, 'd1_unattested');
  for (const scan of ['non-additive', 'unknown', '', 'none']) assert.equal(b({ migration: { ...M, d1_scan: scan } }).code, 'd1_not_additive', `scan ${scan}`);
  for (const bad of [null, {}, [], 'x', { ...M, migration: 'failed' }, { ...M, migration: 'none' }]) assert.equal(b({ migration: bad }).code, 'migration_result_missing');
  assert.equal(b({ migration: { ...M, d1_id: 'OTHER' } }).code, 'd1_identity_mismatch');
  assert.equal(b({ expectedId: '' }).code, 'd1_identity_mismatch');
  for (const v of ['', undefined, 'maybe', 'TRUE']) assert.equal(b({ migration: { ...M, schema_changed: v } }).code, 'migration_result_invalid');
  // identical D1 rule as Production
  assert.equal(prodRollbackBlock({ policy: 'automatic', previous: 'a', current: 'b', worker: 'w', d1Pending: true, d1Compat: '', d1Scan: 'additive' }).code, 'd1_unattested');
  assert.equal(rollbackVerdict({ target: 'dev', rbResult: 'blocked', goodSha: 'abc' }).result, 'blocked');
  assert.equal(rollbackVerdict({ target: 'dev', rbResult: 'blocked', goodSha: '' }).ok, false);
});

const rows = (results, success = true) => JSON.stringify([{ results, success, meta: {} }]);
test('parseD1Rows: only a single successful statement result is data; everything else throws (fail closed)', () => {
  assert.deepEqual(parseD1Rows(rows([{ name: 'a.sql' }])), [{ name: 'a.sql' }]);
  assert.deepEqual(parseD1Rows(rows([])), []);
  for (const bad of ['', 'not json', '{}', '[]', rows([], false), '[{"success":true}]', JSON.stringify([{ results: [], success: true }, { results: [], success: true }]), '{"error":{"text":"auth"}}']) {
    assert.throws(() => parseD1Rows(bad), /D1 query/, `rejected: ${bad.slice(0, 30)}`);
  }
});
test('pendingMigrations: unrecorded files are pending; a missing table means everything; malformed rows throw', () => {
  const files = ['0002.sql', '0001.sql'];
  assert.deepEqual(pendingMigrations({ files, tableExists: true, applied: [{ name: '0001.sql' }, { name: '0002.sql' }] }), []);
  assert.deepEqual(pendingMigrations({ files, tableExists: true, applied: [{ name: '0001.sql' }] }), ['0002.sql'], 'a file left unapplied by an earlier failed run is pending');
  assert.deepEqual(pendingMigrations({ files, tableExists: true, applied: [{ name: '0001.sql' }, { name: '0009-from-a-newer-branch.sql' }] }), ['0002.sql'], 'extra remote rows never hide a pending file');
  assert.deepEqual(pendingMigrations({ files, tableExists: false }), ['0001.sql', '0002.sql'], 'no migrations table: everything is pending');
  assert.throws(() => pendingMigrations({ files, tableExists: true, applied: [{ id: 1 }] }), /without a name/);
  assert.throws(() => pendingMigrations({ files: [], tableExists: true, applied: [] }), /no \.sql files/);
});
const VNP = (o = {}) => JSON.stringify({ precheck: VERIFIED_NO_PENDING, kind: 'native-branch', d1_id: 'DB1', precheck_sha: 'abc1234', ...o });
test('nonProdRecoveryBlock: a verified no-pending result is a known D1 state (schema unchanged); an unmarked none is not', () => {
  const g = (m) => nonProdRecoveryBlock({ d1Migrations: true, migration: m, expectedId: 'DB1', d1Compat: '' });
  assert.equal(g({ migration: 'none', precheck: VERIFIED_NO_PENDING, kind: 'native-branch', d1_id: 'DB1', schema_changed: 'false', d1_scan: 'none' }), null);
  assert.equal(g({ migration: 'none', kind: 'native-branch', d1_id: 'DB1', schema_changed: 'false' }).code, 'migration_result_missing');
  assert.equal(g({ migration: 'none', precheck: VERIFIED_NO_PENDING, kind: 'native-branch', d1_id: 'OTHER', schema_changed: 'false' }).code, 'd1_identity_mismatch');
});

test('classifyMigrationEntries: every name Wrangler discovers via *.sql is kept as-is (spaces, Unicode); anything uncertain is reported, never dropped', () => {
  const f = (name) => ({ name, file: true });
  const r = classifyMigrationEntries([f('0001_init.sql'), f('0002_add status.sql'), f('0003_調整.sql'), f('0004_é.sql'), f('README.md'), f('notes.sql.bak'), { name: 'sub', file: false }]);
  assert.deepEqual(r.files, ['0001_init.sql', '0002_add status.sql', '0003_調整.sql', '0004_é.sql'].sort());
  assert.deepEqual(r.ambiguous, []);
  const a = classifyMigrationEntries([f('0001.sql'), { name: 'dir.sql', file: false }, { name: 'link.sql', file: false }, f('.hidden.sql'), f('UPPER.SQL')]);
  assert.deepEqual(a.files, ['0001.sql']);
  assert.deepEqual(a.ambiguous, ['.hidden.sql', 'UPPER.SQL', 'dir.sql', 'link.sql'].sort(), 'directories, symlinks, dotfiles and .SQL are reported, not silently ignored');
});
test('pendingMigrations: names with spaces / Unicode are compared exactly', () => {
  const files = ['0001_init.sql', '0002_add status.sql', '0003_調整.sql'];
  const rec = (...n) => n.map((name) => ({ name }));
  assert.deepEqual(pendingMigrations({ files, tableExists: true, applied: rec('0001_init.sql') }), ['0002_add status.sql', '0003_調整.sql']);
  assert.deepEqual(pendingMigrations({ files, tableExists: true, applied: rec('0001_init.sql', '0003_調整.sql') }), ['0002_add status.sql']);
  assert.deepEqual(pendingMigrations({ files, tableExists: true, applied: rec(...files) }), []);
});
