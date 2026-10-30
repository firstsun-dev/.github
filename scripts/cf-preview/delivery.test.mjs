import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DELIVERY_KINDS, resolveDeliveryTargets, resolveSmokeMode, parseSmokeOverrides, planMigrations, branchPreviewIdentity, ENGINE_TARGET,
  canonicalOrigin, previewUrlFromSuffix, resolveCanonicalUrl, checkBuildEnvOrigins,
  targetDescriptor, validateDescriptor, buildDescriptors, parseDescriptor, checkPeerPreflights, resolveMigrationUsed,
  isContentLevel, verificationOutcome, prodRollbackBlock, rollbackVerdict, restoreDescriptors, validateInit, runLockGroup,
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

import { environmentName, ledgerEnv, foreignUrlsInBuildEnv, sameUrl } from '../../actions/cf-preview/delivery.mjs';
test('environment and ledger names: legacy names unchanged, Native Dev gets its own', () => {
  assert.equal(environmentName('blog', 'production'), 'blog/prod');
  assert.equal(environmentName('blog', 'native-branch'), 'blog/preview');
  assert.equal(environmentName('blog', 'persistent-dev'), 'blog/dev');
  assert.equal(environmentName('blog', 'native-dev'), 'blog/native-dev');
  assert.equal(ledgerEnv('blog', 'native-dev'), 'blog/native-dev/dev');
  assert.equal(ledgerEnv('blog', 'persistent-dev'), 'blog/dev/shared');
  assert.equal(ledgerEnv('blog', 'native-branch', 'feat-x'), 'blog/preview/feat-x');
  assert.throws(() => environmentName('blog', 'prod'), /Unknown delivery kind/);
});
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

// ---------------------------------------------------------------------------------------------------------------------
// P2-B: migration plan - one execution per physical D1; the owner is the first member; Production D1 is never shared.
// ---------------------------------------------------------------------------------------------------------------------
test('planMigrations: Branch + Native Dev on one preview D1 -> ONE group (owner native-branch); distinct D1s stay separate', () => {
  const cfg = { name: 'app', d1_databases: [{ binding: 'DB', database_name: 'prod', database_id: 'PROD' }],
    previews: { d1_databases: [{ binding: 'DB', database_name: 'prev', database_id: 'PREV' }] },
    env: { dev: { name: 'app-dev', d1_databases: [{ binding: 'DB', database_name: 'devdb', database_id: 'DEV' }] } } };
  const g = planMigrations(cfg, ['native-branch', 'native-dev'], { accountId: 'A' });
  assert.equal(g.length, 1);
  assert.deepEqual(g[0].members, ['native-branch', 'native-dev']);
  assert.equal(g[0].owner, 'native-branch');
  assert.equal(g[0].databaseId, 'PREV');
  const h = planMigrations(cfg, ['native-branch', 'persistent-dev'], { accountId: 'A' });
  assert.equal(h.length, 2);
  assert.deepEqual(h.map((x) => x.owner).sort(), ['native-branch', 'persistent-dev']);
  assert.deepEqual(planMigrations(cfg, ['production'], { accountId: 'A' })[0].members, ['production']);
});
test('planMigrations: a Production D1 exposed to Preview/Dev is refused', () => {
  const cfg = { name: 'app', d1_databases: [{ binding: 'DB', database_name: 'prod', database_id: 'PROD' }],
    previews: { d1_databases: [{ binding: 'DB', database_name: 'prod', database_id: 'PROD' }] } };
  assert.throws(() => planMigrations(cfg, ['native-branch'], { accountId: 'A' }), /Production D1|production/i);
});

// ---- target descriptor: the single versioned record the thin per-kind calls consume -------------------------------------
const D = { kind: 'native-dev', app: 'app', runId: 77, d1ExpectedId: 'DB1', ownerKind: 'native-branch' };

test('descriptor: derived once from the kind - engine target, environment, concurrency groups', () => {
  const expect = { production: ['prod', 'app/prod'], 'native-branch': ['preview', 'app/preview'], 'native-dev': ['preview', 'app/native-dev'], 'persistent-dev': ['dev', 'app/dev'] };
  for (const k of DELIVERY_KINDS) {
    const d = targetDescriptor({ kind: k, app: 'app', runId: 5, repo: 'o/r' });
    assert.deepEqual([d.target, d.environment], expect[k]);
    if (k !== 'persistent-dev') {
      assert.equal(d.deploy_group, `cfv2-leg-5-${k}`);
      assert.equal(d.rollback_group, `cfv2-leg-5-${k}-rollback`);
    }
    assert.equal(d.v, 1);
  }
});

test('descriptor: migration ownership - only the owner of the physical D1 owns the migration', () => {
  assert.equal(targetDescriptor(D).owns_migration, false);
  assert.equal(targetDescriptor({ ...D, kind: 'native-branch' }).owns_migration, true);
  assert.equal(targetDescriptor({ ...D, d1ExpectedId: '', ownerKind: '' }).owns_migration, false);
});

test('descriptor: buildDescriptors maps the D1 plan (shared D1 -> one owner) and keeps enabled kinds only, in stable order', () => {
  const groups = [{ databaseId: 'SHARED', members: ['native-branch', 'native-dev'], owner: 'native-branch' }, { databaseId: 'PROD', members: ['production'], owner: 'production' }];
  const m = buildDescriptors({ kinds: ['native-dev', 'production', 'native-branch'], app: 'app', runId: 9, groups, smokeOverrides: '{"native-dev":"skip"}' });
  assert.deepEqual(Object.keys(m), ['production', 'native-branch', 'native-dev']);
  assert.equal(m['native-dev'].d1_expected_id, 'SHARED');
  assert.equal(m['native-dev'].d1_owner_kind, 'native-branch');
  assert.equal(m['native-dev'].owns_migration, false);
  assert.equal(m['native-branch'].owns_migration, true);
  assert.equal(m.production.d1_expected_id, 'PROD');
  assert.equal(m['native-dev'].smoke_mode, 'skip');
  assert.equal(m['native-branch'].smoke_mode, 'full');
});

test('descriptor: deferred verify takes the D1 ids from the hand-off, not from a re-plan', () => {
  const m = buildDescriptors({ kinds: ['production'], app: 'app', runId: 9, d1ByKind: { production: 'HANDED' }, phase: 'verify' });
  assert.equal(m.production.d1_expected_id, 'HANDED');
  assert.equal(m.production.phase, 'verify');
});

test('descriptor: validation rejects forged, cross-kind, unknown-key and wrong-version records (trust boundary)', () => {
  const ok = targetDescriptor(D);
  assert.doesNotThrow(() => validateDescriptor({ ...ok }));
  const bad = (patch, re) => assert.throws(() => validateDescriptor({ ...ok, ...patch }), re);
  bad({ v: 2 }, /version/);
  bad({ target: 'prod' }, /engine target/);
  bad({ environment: 'app/prod' }, /environment/);
  bad({ environment: 'a b/native-dev' }, /environment/);
  bad({ deploy_group: 'cfv2-leg-77-production' }, /concurrency groups/);
  bad({ rollback_group: 'cfv2-leg-77-native-dev' }, /concurrency groups/);
  bad({ deploy_group: 'other' }, /concurrency groups/);
  assert.throws(() => validateDescriptor({ ...targetDescriptor({ kind: 'persistent-dev', app: 'app', runId: 1, repo: 'o/r' }), rollback_group: 'cfv2-dev-o/r-other' }), /concurrency groups/);
  assert.throws(() => validateDescriptor({ ...targetDescriptor({ kind: 'persistent-dev', app: 'app', runId: 1, repo: 'o/r' }), deploy_group: 'cfv2-leg-1-persistent-dev', rollback_group: 'cfv2-leg-1-persistent-dev-rollback' }), /concurrency groups/);
  bad({ d1_expected_id: 'a b' }, /database id/);
  bad({ d1_owner_kind: 'bogus' }, /Unknown delivery kind/);
  bad({ owns_migration: true }, /owns_migration/);
  bad({ smoke_mode: 'maybe' }, /smoke mode/);
  bad({ phase: 'later' }, /phase/);
  bad({ CLOUDFLARE_API_TOKEN: 'x' }, /unexpected/);
  assert.throws(() => validateDescriptor({ ...ok, kind: undefined }), /./);
  const { phase, ...noPhase } = ok;
  assert.throws(() => validateDescriptor(noPhase), /missing \[phase\]/);
  assert.throws(() => validateDescriptor(null), /object/);
  assert.throws(() => parseDescriptor('{'), /not valid JSON/);
  assert.throws(() => parseDescriptor(JSON.stringify(ok), 'production'), /not production/);
  assert.equal(parseDescriptor(JSON.stringify(ok), 'native-dev').kind, 'native-dev');
});

test('descriptor: carries no secret and no URL (secrets stay in job env, URLs are resolved per build)', () => {
  const s = JSON.stringify(targetDescriptor(D));
  assert.doesNotMatch(s, /https?:|token|secret|password/i);
});

test('descriptor: persistent-dev keeps the legacy Shared Dev group (Deploy AND Rollback serialise per repo + shared-D1 scope)', () => {
  const g = (o) => targetDescriptor({ kind: 'persistent-dev', app: 'blog', runId: 9, repo: 'o/r', ...o });
  assert.equal(g({ lockScope: 'shared-db' }).deploy_group, 'cfv2-dev-o/r-shared-db');
  assert.equal(g({}).deploy_group, 'cfv2-dev-o/r-blog');
  assert.equal(g({}).rollback_group, g({}).deploy_group);
  assert.notEqual(g({}).deploy_group, g({ app: 'wiki' }).deploy_group);
  assert.notEqual(g({ lockScope: 'a' }).deploy_group, g({ lockScope: 'b' }).deploy_group);
  // other kinds never share a group across runs' legs or kinds
  assert.notEqual(targetDescriptor({ kind: 'native-dev', app: 'a', runId: 1 }).deploy_group, targetDescriptor({ kind: 'native-branch', app: 'a', runId: 1 }).deploy_group);
});

// ---- migration gates (were inline bash/jq in the delivery workflow) -----------------------------------------------------
test('migrate gate: no D1 is migrated unless EVERY enabled kind passed its preflight; a missing result blocks', () => {
  const all = { production: 'success', 'native-branch': 'success' };
  assert.deepEqual(checkPeerPreflights(['production', 'native-branch'], all), []);
  assert.deepEqual(checkPeerPreflights([], {}), []);
  for (const bad of ['failure', 'cancelled', 'skipped', '']) {
    const f = checkPeerPreflights(['production', 'native-branch'], { ...all, 'native-branch': bad });
    assert.equal(f.length, 1);
    assert.match(f[0], /native-branch/);
  }
  assert.match(checkPeerPreflights(['native-dev'], all)[0], /'missing'/);
  assert.match(checkPeerPreflights(['native-dev'], null)[0], /'missing'/);
  assert.throws(() => checkPeerPreflights(['bogus'], {}), /Unknown delivery kind/);
});

const MJ = (o = {}) => JSON.stringify({ migration: 'applied', kind: 'native-branch', d1_id: 'DB1', ...o });
const U = (o) => resolveMigrationUsed({ d1Migrations: true, kind: 'native-branch', ownerKind: 'native-branch', expectedId: 'DB1', ownResult: 'success', ownJson: MJ(), ...o });

test('migration used: without d1_migrations nothing is required', () => {
  assert.deepEqual(resolveMigrationUsed({ d1Migrations: false, kind: 'production' }), {});
});

test('migration used: the owner uses its own result; a sharing kind uses the native-branch owner result', () => {
  assert.equal(U({}).d1_id, 'DB1');
  const shared = resolveMigrationUsed({ d1Migrations: true, kind: 'native-dev', ownerKind: 'native-branch', expectedId: 'DB1', ownResult: '', ownJson: '', ownerResult: 'success', ownerJson: MJ() });
  assert.equal(shared.kind, 'native-branch');
});

test('migration used: fails closed on a missing, failed, foreign or malformed migration result', () => {
  assert.throws(() => U({ ownerKind: '' }), /no migration owner is planned/);
  assert.throws(() => U({ ownerKind: 'persistent-dev' }), /no migration owner is planned/);
  assert.throws(() => U({ ownResult: 'failure' }), /finished 'failure'/);
  assert.throws(() => U({ ownResult: '' }), /finished 'missing'/);
  assert.throws(() => U({ ownResult: 'skipped' }), /finished 'skipped'/);
  assert.throws(() => U({ ownJson: MJ({ migration: 'none' }) }), /no applied migration/);
  assert.throws(() => U({ ownJson: '' }), /no applied migration/);
  assert.throws(() => U({ ownJson: '[1]' }), /no applied migration/);
  assert.throws(() => U({ ownJson: MJ({ kind: 'persistent-dev' }) }), /another kind/);
  assert.throws(() => U({ ownJson: MJ({ d1_id: 'FOREIGN' }) }), /another D1/);
  assert.throws(() => U({ expectedId: 'OTHER' }), /another D1/);
});

test('migration used: a stale-skipped migration is accepted (nothing was applied, nothing will be published)', () => {
  assert.equal(U({ ownJson: JSON.stringify({ stale: 'true' }) }).stale, 'true');
  assert.equal(U({ ownJson: JSON.stringify({ stale: true }) }).stale, true);
});

// ---- verification and recovery decisions ---------------------------------------------------------------------------------
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

// ---- deferred verification: the deploy call's descriptors are the source of truth ------------------------------------------
const REPO = 'o/r';
const deployState = (over = {}, kinds = ['native-branch', 'native-dev'], dOver = {}) => {
  const descriptors = buildDescriptors({
    kinds, app: 'app', runId: 11, repo: REPO, lockScope: 'shared-db', smokeMode: 'skip', phase: 'deploy',
    groups: [{ databaseId: 'DB1', members: kinds, owner: kinds[0] }], ...dOver,
  });
  const inputs = { app_name: 'app', d1_lock_scope: 'shared-db', smoke_mode: 'skip', deployment_strategy: 'hybrid', preview_on_branch_push: true, d1_migrations: true };
  return {
    inputs, group: runLockGroup({ ...EV, inputs, runId: 11 }), kinds, d1: Object.fromEntries(kinds.map((k) => [k.replace(/-/g, '_'), 'DB1'])),
    migration: Object.fromEntries(kinds.map((k) => [`mj_${k.replace(/-/g, '_')}`, JSON.stringify({ kind: kinds[0], d1_id: 'DB1' })])), descriptors, ...over,
  };
};
const EV = { event: 'push', ref: 'refs/heads/feat/x' };
const R = (state, o = {}) => restoreDescriptors(state, { repo: REPO, runId: 99, ...EV, ...o });

test('deferred verify: deployment-time policy survives (smoke skip, lock scope, owner, D1, groups); only the phase changes', () => {
  const st = deployState();
  const r = R(st);
  for (const k of ['native-branch', 'native-dev']) assert.deepEqual({ ...r[k], phase: 'deploy' }, st.descriptors[k]);
  assert.equal(r['native-dev'].smoke_mode, 'skip');
  assert.equal(r['native-dev'].phase, 'verify');
  assert.equal(r['native-dev'].d1_owner_kind, 'native-branch');
  const pd = R(deployState({}, ['persistent-dev']));
  assert.equal(pd['persistent-dev'].deploy_group, 'cfv2-dev-o/r-shared-db', 'persistent Dev keeps the deploy-time lock identity, not a verify-call default');
  assert.equal(pd['persistent-dev'].rollback_group, pd['persistent-dev'].deploy_group);
});

test('deferred verify: every kind restores (Native Branch, Native Dev, Persistent Dev, Production)', () => {
  for (const k of DELIVERY_KINDS) assert.equal(R(deployState({}, [k]))[k].phase, 'verify');
});

test('deferred verify: tampering is rejected (kind set, cross-target injection, D1, owner, app, repo, phase, schema)', () => {
  const bad = (st, re) => assert.throws(() => R(st), re);
  const st = deployState();
  bad({ ...st, descriptors: { 'native-branch': st.descriptors['native-branch'] } }, /do not match phase_state.kinds/);
  bad({ ...st, descriptors: { ...st.descriptors, production: targetDescriptor({ kind: 'production', app: 'app', runId: 1, phase: 'deploy' }) } }, /do not match/);
  bad({ ...st, descriptors: { 'native-branch': st.descriptors['native-dev'], 'native-dev': st.descriptors['native-branch'] } }, /filed under/);
  bad({ ...st, d1: { native_branch: 'FOREIGN', native_dev: 'DB1' } }, /hand-off recorded 'FOREIGN'/);
  bad({ ...st, descriptors: { ...st.descriptors, 'native-dev': { ...st.descriptors['native-dev'], d1_expected_id: 'FOREIGN' } } }, /binds D1 'FOREIGN'/);
  bad({ ...st, descriptors: { ...st.descriptors, 'native-dev': { ...st.descriptors['native-dev'], d1_owner_kind: 'native-dev', owns_migration: true } } }, /migration owner/);
  bad({ ...st, inputs: { ...st.inputs, app_name: 'other' } }, /another application/);
});

test('deferred verify: wrong repository, wrong phase, schema violations and missing identity fail closed', () => {
  const st = deployState({}, ['persistent-dev']);
  assert.throws(() => R(st, { repo: 'evil/r' }), /carries lock/);
  const allPhase = deployState({}, ['native-branch'], { phase: 'all' });
  assert.throws(() => R(allPhase), /only a deploy-phase descriptor/);
  const already = deployState({}, ['native-branch'], { phase: 'verify' });
  assert.throws(() => R(already), /only a deploy-phase descriptor/);
  const forged = deployState({}, ['native-branch']);
  forged.descriptors['native-branch'] = { ...forged.descriptors['native-branch'], smoke_mode: 'maybe' };
  assert.throws(() => R(forged), /smoke mode/);
  assert.throws(() => R({ ...deployState(), kinds: [] }), /no delivery kind/);
  assert.throws(() => R({ ...deployState(), kinds: ['native-dev', 'native-dev'] }), /duplicate/);
  assert.throws(() => R({ ...deployState(), kinds: ['bogus'] }), /unknown/);
  assert.throws(() => R({ ...deployState(), inputs: {} }), /app_name is missing/);
  assert.throws(() => R(null), /not an object/);
  assert.throws(() => R({ ...deployState(), descriptors: [] }), /not an object/);
});

test('deferred verify: a legacy hand-off (no descriptors) is rebuilt ONLY from the deploy call\'s own inputs, else fails closed', () => {
  const { descriptors, ...legacy } = deployState();
  const r = R(legacy);
  assert.equal(r['native-dev'].smoke_mode, 'skip', 'from phase_state.inputs (the deploy call), not from the verify call');
  assert.equal(r['native-dev'].d1_expected_id, 'DB1');
  assert.equal(r['native-dev'].phase, 'verify');
  const pd = R({ ...deployState({}, ['persistent-dev']), descriptors: undefined });
  assert.equal(pd['persistent-dev'].deploy_group, 'cfv2-dev-o/r-shared-db');
  const badIn = { ...legacy.inputs, smoke_mode: 'maybe' };
  assert.throws(() => R({ ...legacy, inputs: badIn, group: runLockGroup({ ...EV, inputs: badIn, runId: 11 }) }), /smoke_mode/);
  assert.throws(() => R({ ...legacy, inputs: { smoke_mode: 'skip' } }), /app_name is missing/);
});

// ---- engine initialisation: one pure validation (was ~6 Node processes + jq in engine.sh init) --------------------------------
const IN0 = {
  app_name: 'app', deployment_strategy: 'production-preview', production_rollback_policy: 'automatic', build_cmd: 'pnpm build', preview_on_branch_push: true,
  app_url_prod: 'https://app.example.com', app_url_dev: 'https://dev.example.com', dev_worker_name: 'app-dev', smoke_env: '', build_env_prod: '', build_env_preview: '',
};
const INIT = (o = {}, i = {}) => validateInit({ inputs: { ...IN0, ...i }, target: 'preview', stage: 'migrate', event: 'push', ref: 'refs/heads/feat/x', ...o });
const initFails = (re, o, i) => assert.throws(() => INIT(o, i), re);

test('init: a valid Native Branch push binds kind, stage, mode and the smoke policy', () => {
  const r = INIT();
  assert.deepEqual([r.kind, r.target, r.stage, r.mode, r.smokeMode], ['native-branch', 'preview', 'migrate', 'auto', 'full']);
  assert.equal(INIT({ target: 'prod', stage: 'publish', ref: 'refs/heads/main' }).kind, 'production');
});

test('init: target, kind, stage and mode binding refuse everything unknown or cross-target', () => {
  initFails(/bad engine target/, { target: 'staging' });
  initFails(/does not belong to engine target/, { kind: 'production' });
  initFails(/must run as split stages/, { stage: 'all' });
  initFails(/bad engine stage/, { stage: 'nope' });
  initFails(/malformed expected D1 id/, { d1Expect: 'a b' });
  initFails(/bad engine mode/, { mode: 'weird' });
  initFails(/manual mode is only for the persistent Dev target/, { mode: 'manual' });
  assert.throws(() => INIT({ target: 'dev', kind: 'persistent-dev', mode: 'manual', stage: 'migrate' }, { dev_build_cmd: 'x' }), /manual Dev runs as one job/);
  assert.throws(() => validateInit({ inputs: [], target: 'preview', event: 'push', ref: 'refs/heads/x' }), /inputs_json is not an object/);
});

test('init: strategy, rollback policy and Dev channel combinations fail closed before anything runs', () => {
  initFails(/bad deployment_strategy/, {}, { deployment_strategy: 'x' });
  initFails(/bad production_rollback_policy/, {}, { production_rollback_policy: 'maybe' });
  initFails(/production-only cannot be combined/, { ref: 'refs/heads/main' }, { deployment_strategy: 'production-only', shared_dev_on_branch_push: true });
  initFails(/conflicting automatic Dev channels/, {}, { shared_dev_on_branch_push: true, shared_dev_native_preview_on_branch_push: true });
});

test('init: the engine kind must be among the kinds this event enables (no phantom delivery)', () => {
  initFails(/target mismatch/, { target: 'prod', stage: 'publish' }); // branch push never enables production
  initFails(/target mismatch/, {}, { preview_on_branch_push: false });
  initFails(/target mismatch/, { ref: 'refs/heads/main' }); // main push enables production only
  assert.equal(INIT({ kind: 'native-dev' }, { shared_dev_native_preview_on_branch_push: true }).kind, 'native-dev');
});

test('init: manual persistent Dev only from the approved ref via dispatch', () => {
  const M = (o = {}, i = {}) => validateInit({ inputs: { ...IN0, deployment_strategy: 'hybrid', dev_build_cmd: 'x', persistent_dev_ref: 'refs/heads/dev', dispatch_target: 'persistent-dev', ...i }, target: 'dev', kind: 'persistent-dev', stage: 'all', mode: 'manual', event: 'workflow_dispatch', ref: 'refs/heads/dev', ...o });
  assert.equal(M().kind, 'persistent-dev');
  assert.throws(() => M({ ref: 'refs/heads/other' }), /manual Dev is only allowed from the approved persistent_dev_ref/);
  assert.throws(() => M({ event: 'push' }), /manual Dev is only allowed/);
});

test('init: per-target requirements (Native Dev flag, Dev identity/URL/build, build command)', () => {
  initFails(/target mismatch/, { kind: 'native-dev' }); // without the flag Native Dev is not an enabled kind at all
  const D = (i = {}) => validateInit({ inputs: { ...IN0, deployment_strategy: 'hybrid', shared_dev_on_branch_push: true, dev_build_cmd: 'x', ...i }, target: 'dev', stage: 'all', event: 'push', ref: 'refs/heads/feat/x' });
  assert.equal(D().kind, 'persistent-dev');
  assert.throws(() => D({ dev_worker_name: '' }), /dev_worker_name/);
  assert.throws(() => D({ app_url_dev: '' }), /app_url_dev/);
  assert.throws(() => D({ dev_build_cmd: '', build_cmd: '' }), /no build command for target dev/);
  assert.throws(() => D({ dev_build_cmd: '', d1_migrations: true }), /requires dev_build_cmd/);
  assert.equal(D({ dev_build_cmd: '' }).kind, 'persistent-dev', 'dev_build_cmd falls back to build_cmd without D1 migrations');
  initFails(/no build command for target preview/, {}, { build_cmd: '' });
});

test('init: env data - reserved/forbidden names rejected, foreign target URLs rejected, each kind uses ONLY its own build env', () => {
  initFails(/CFV2_BUILD_ENV: .*TARGET may not be set/, {}, { build_env_preview: 'TARGET=prod' });
  initFails(/CFV2_SMOKE_ENV/, {}, { smoke_env: 'GITHUB_TOKEN=x' });
  initFails(/CFV2_PS/, { previewSecrets: 'CLOUDFLARE_API_TOKEN=x' });
  initFails(/contains the production URL/, {}, { build_env_preview: 'PUBLIC_URL=https://app.example.com' });
  const r = INIT({ previewSecrets: 'API_KEY=s3cret' }, { build_env_preview: 'A=1\nB=2', build_env_prod: 'PROD_ONLY=1', smoke_env: 'S=1' });
  assert.deepEqual(r.buildEnv, [['A', '1'], ['B', '2']], 'a Preview build never sees the Production build env');
  assert.deepEqual(r.smokeEnv, [['S', '1']]);
  assert.deepEqual(r.previewEnv, [['API_KEY', 's3cret']]);
  assert.equal(INIT({ target: 'prod', stage: 'publish', ref: 'refs/heads/main', previewSecrets: 'API_KEY=s3cret' }).previewEnv, null, 'Preview secrets are only read for the Preview target');
});

test('init: deferred verify identity inputs must equal the deploy call\'s; a descriptor must match the stage', () => {
  const ci = (o = {}) => JSON.stringify({ app_name: 'app', app_path: '', production_rollback_policy: 'automatic', tools_ref: '', ...o });
  assert.doesNotThrow(() => INIT({ callInputs: ci() }));
  for (const k of ['app_name', 'app_path', 'production_rollback_policy', 'tools_ref']) initFails(new RegExp(`'${k}' differs`), { callInputs: ci({ [k]: 'x' }) });
  initFails(/call inputs are not an object/, { callInputs: '[1]' });
  const d = targetDescriptor({ kind: 'native-branch', app: 'app', runId: 1, repo: 'o/r', smokeMode: 'skip' });
  assert.equal(INIT({ descriptor: JSON.stringify(d) }).smokeMode, 'skip', 'the smoke policy comes from the descriptor');
  initFails(/is for production, not native-branch/, { descriptor: JSON.stringify(targetDescriptor({ kind: 'production', app: 'app', runId: 1 })) });
  initFails(/D1 id differs/, { descriptor: JSON.stringify({ ...d, d1_expected_id: 'X', d1_owner_kind: '' }) });
});

// ---- lock identity integrity across Deploy -> Verify -> Rollback -------------------------------------------------------------
const PD = (o = {}, i = {}) => deployState({ inputs: { ...deployState().inputs, shared_dev_on_branch_push: true, preview_on_branch_push: false, ...i }, ...o }, ['persistent-dev']);

test('lock identity: run-level group follows the same rule as the workflow (scope or app, channel, ref)', () => {
  const I = { app_name: 'blog', deployment_strategy: 'hybrid', preview_on_branch_push: true, d1_migrations: true };
  assert.equal(runLockGroup({ event: 'push', ref: 'refs/heads/main', inputs: I, runId: 1 }), 'cfv2-prod-blog');
  assert.equal(runLockGroup({ event: 'push', ref: 'refs/heads/main', inputs: { ...I, d1_lock_scope: 'shared-db' }, runId: 1 }), 'cfv2-prod-shared-db');
  assert.equal(runLockGroup({ event: 'push', ref: 'refs/heads/x', inputs: { ...I, d1_lock_scope: 'shared-db' }, runId: 1 }), 'cfv2-nonprod-d1-shared-db');
  assert.equal(runLockGroup({ event: 'push', ref: 'refs/heads/x', inputs: { ...I, d1_migrations: false, shared_dev_on_branch_push: true }, runId: 1 }), 'cfv2-shared-dev-blog');
  assert.equal(runLockGroup({ event: 'push', ref: 'refs/heads/x', inputs: { ...I, d1_migrations: false }, runId: 1 }), 'cfv2-blog-preview-x');
  assert.equal(runLockGroup({ event: 'pull_request', ref: 'refs/pull/1/merge', inputs: I, runId: 7 }), 'cfv2-run-7');
  assert.equal(runLockGroup({ event: 'push', ref: 'refs/tags/v1', inputs: I, runId: 7 }), 'cfv2-run-7');
  assert.equal(runLockGroup({ event: 'workflow_dispatch', ref: 'refs/heads/dev', inputs: { ...I, dispatch_target: 'persistent-dev' }, runId: 7 }), 'cfv2-run-7');
});

test('lock identity: same Deploy/Verify d1_lock_scope -> same effective lock; a verify default never selects another', () => {
  const st = PD();
  const r = R(st)['persistent-dev'];
  assert.equal(r.deploy_group, 'cfv2-dev-o/r-shared-db');
  assert.equal(r.rollback_group, r.deploy_group);
  assert.equal(st.group, 'cfv2-nonprod-d1-shared-db', 'the run-level group is also keyed by the deploy-time scope');
  // restoring takes ONLY the hand-off: there is no verify-call input to override the scope
  assert.equal(R(PD())['persistent-dev'].deploy_group, r.deploy_group);
});

test('lock identity: modified Persistent Dev group, rollback group, repository and application are rejected', () => {
  const st = PD();
  const tamper = (patch) => ({ ...st, descriptors: { 'persistent-dev': { ...st.descriptors['persistent-dev'], ...patch } } });
  assert.throws(() => R(tamper({ deploy_group: 'cfv2-dev-o/r-other', rollback_group: 'cfv2-dev-o/r-other' })), /carries lock/);
  assert.throws(() => R(tamper({ rollback_group: 'cfv2-dev-o/r-other' })), /rollback_group|concurrency groups|carries lock/);
  assert.throws(() => R(tamper({ deploy_group: 'cfv2-dev-o/r-app', rollback_group: 'cfv2-dev-o/r-app' })), /carries lock/, 'the scope-less lock is a different lock');
  assert.throws(() => R(st, { repo: 'evil/r' }), /carries lock/);
  assert.throws(() => R({ ...st, inputs: { ...st.inputs, app_name: 'other' } }), /another application|phase_state.group|carries lock/);
  assert.throws(() => R({ ...st, inputs: { ...st.inputs, d1_lock_scope: '' } }), /carries lock|phase_state.group/, 'a different deploy-time scope is not the original lock');
});

test('lock identity: phase_state.group must be the lock the deploy inputs imply (inconsistent / forged / missing -> rejected)', () => {
  const st = deployState();
  for (const g of ['cfv2-prod-app', 'attacker-group', '', undefined, 'cfv2-nonprod-d1-other']) assert.throws(() => R({ ...st, group: g }), /phase_state.group/);
  assert.doesNotThrow(() => R(st));
  // no branch push (e.g. a rerun on a PR event): only the numeric cfv2-run-<id> shape is acceptable
  const pr = { event: 'pull_request', ref: 'refs/pull/1/merge' };
  assert.doesNotThrow(() => R({ ...st, group: 'cfv2-run-123' }, pr));
  assert.throws(() => R({ ...st, group: 'cfv2-run-abc' }, pr), /phase_state.group/);
  assert.throws(() => R({ ...st, group: 'cfv2-nonprod-d1-shared-db' }, pr), /phase_state.group/);
  // verifying from another ref/event than the deploy ran on cannot silently join a different lock
  assert.throws(() => R(st, { ref: 'refs/heads/main' }), /phase_state.group/);
});

test('lock identity: Native Branch / Native Dev / Production restore exactly as before', () => {
  for (const k of ['native-branch', 'native-dev', 'production']) {
    const st = deployState({}, [k]);
    const r = R(st)[k];
    assert.equal(r.deploy_group, st.descriptors[k].deploy_group);
    assert.equal(r.rollback_group, `${st.descriptors[k].deploy_group}-rollback`);
    assert.equal(r.phase, 'verify');
  }
});

// ---- legacy hand-off (no descriptors): migration ownership is reconstructed only from proof ---------------------------------------
const legacyOf = (o = {}, kinds = ['native-branch', 'native-dev']) => { const { descriptors, ...l } = deployState(o, kinds); return l; };
const mj = (kind, d1 = 'DB1') => JSON.stringify({ migration: 'applied', kind, d1_id: d1 });

test('legacy migration ownership: no D1 -> valid, empty owner; D1 migrations disabled -> no ownership needed', () => {
  const noD1 = legacyOf({ inputs: { ...deployState().inputs, d1_migrations: false }, d1: {}, migration: {} });
  const r = R({ ...noD1, group: runLockGroup({ ...EV, inputs: noD1.inputs, runId: 1 }) });
  assert.equal(r['native-dev'].d1_expected_id, '');
  assert.equal(r['native-dev'].d1_owner_kind, '');
  assert.equal(r['native-dev'].owns_migration, false);
});

test('legacy migration ownership: one D1 shared by Native Branch and Native Dev -> the proven owner and member relationship', () => {
  const r = R(legacyOf());
  assert.equal(r['native-branch'].d1_owner_kind, 'native-branch');
  assert.equal(r['native-branch'].owns_migration, true);
  assert.equal(r['native-dev'].d1_owner_kind, 'native-branch');
  assert.equal(r['native-dev'].owns_migration, false);
  assert.equal(r['native-dev'].d1_expected_id, 'DB1');
});

test('legacy migration ownership: a single kind owns its own D1; distinct D1s have independent owners', () => {
  const one = R(legacyOf({}, ['production']));
  assert.equal(one.production.owns_migration, true);
  const two = legacyOf({ d1: { native_branch: 'DB1', persistent_dev: 'DB2' }, migration: { mj_native_branch: mj('native-branch', 'DB1'), mj_persistent_dev: mj('persistent-dev', 'DB2') } }, ['native-branch', 'persistent-dev']);
  const r = R(two);
  assert.deepEqual([r['native-branch'].owns_migration, r['persistent-dev'].owns_migration], [true, true]);
  assert.deepEqual([r['native-branch'].d1_expected_id, r['persistent-dev'].d1_expected_id], ['DB1', 'DB2']);
});

test('legacy migration ownership: conflicting, missing, foreign or self-contradicting evidence fails closed', () => {
  const base = legacyOf();
  assert.throws(() => R({ ...base, migration: { mj_native_branch: mj('native-branch'), mj_native_dev: mj('native-dev') } }), /conflicting migration owners/);
  assert.throws(() => R({ ...base, migration: {} }), /no migration-owner evidence/);
  assert.throws(() => R({ ...base, migration: { mj_native_branch: mj('native-branch'), mj_native_dev: JSON.stringify({ stale: 'true' }) } }), /no migration-owner evidence/);
  assert.throws(() => R({ ...base, migration: { mj_native_branch: mj('native-branch'), mj_native_dev: mj('native-branch', 'OTHER') } }), /for D1 'OTHER'/);
  assert.throws(() => R({ ...base, migration: { mj_native_branch: mj('production'), mj_native_dev: mj('production') } }), /not among the kinds/);
  // The owner is whatever the hand-off consistently proves; a self-consistent swap cannot be told from the truth (it only changes the informational
  // owns_migration of a verify descriptor - verify/rollback never run a migration). It is accepted, and only the consistent owner is reported:
  const swapped = R({ ...base, migration: { mj_native_branch: mj('native-dev'), mj_native_dev: mj('native-dev') } });
  assert.deepEqual([swapped['native-dev'].d1_owner_kind, swapped['native-dev'].owns_migration, swapped['native-branch'].owns_migration], ['native-dev', true, false]);
});

test('legacy migration ownership: D1 identity mismatch is rejected; the descriptor-based hand-off is unchanged', () => {
  const base = legacyOf();
  assert.throws(() => R({ ...base, d1: { native_branch: 'DB1', native_dev: 'DB2' } }), /conflicting migration owners|for D1/);
  const withDesc = deployState();
  assert.deepEqual(R(withDesc)['native-dev'], { ...withDesc.descriptors['native-dev'], phase: 'verify' });
});
