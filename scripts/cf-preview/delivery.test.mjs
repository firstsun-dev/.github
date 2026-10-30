import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DELIVERY_KINDS, resolveDeliveryTargets, resolveSmokeMode, parseSmokeOverrides, planMigrations, branchPreviewIdentity, ENGINE_TARGET,
  canonicalOrigin, previewUrlFromSuffix, resolveCanonicalUrl, checkBuildEnvOrigins,
  targetDescriptor, validateDescriptor, buildDescriptors, parseDescriptor, checkPeerPreflights, resolveMigrationUsed,
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
