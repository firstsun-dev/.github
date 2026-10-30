#!/usr/bin/env node
// CLI used by _cf-worker-v2.yml. Prints key=value lines (GITHUB_OUTPUT-safe) or exits non-zero.
import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import {
  assertWranglerSupported, auditDevConfig, previewHostReadiness, auditPreviewConfig, auditProdConfig, classifyEvent, extractPreviewUrl,
  isStale, migrationPlan, workerName, classifyMigrationSql, previewIdentity, readConfig, rebindSha, artifactDigest, checkDeployedD1, resolveSharedDev, resolveTarget, sharedPreviewDevD1,
} from './lib.mjs';
import {
  DELIVERY_KINDS, ENGINE_TARGET, assertKind, branchPreviewIdentity, canonicalOrigin, checkBuildEnvOrigins, environmentName, foreignUrlsInBuildEnv, planMigrations, resolveCanonicalUrl, resolveDeliveryTargets, resolveSmokeMode,
  buildDescriptors, parseDescriptor, checkPeerPreflights, resolveMigrationUsed, verificationOutcome, prodRollbackBlock, rollbackVerdict, restoreDescriptors, validateInit,
} from './delivery.mjs';

const [cmd, ...rest] = process.argv.slice(2);
const opt = (n, d = '') => {
  const i = rest.indexOf(`--${n}`);
  return i >= 0 ? rest[i + 1] : d;
};
const list = (s) => s.split(/[\s,]+/).filter(Boolean);
const out = (o) => { for (const [k, v] of Object.entries(o)) console.log(`${k}=${v}`); };

try {
  switch (cmd) {
    case 'identity': {
      const id = branchPreviewIdentity({ repo: opt('repo'), worker: opt('worker'), ref: opt('ref'), hostWorker: opt('host-worker') }, { reserveNativeDev: opt('reserve-native-dev') === 'true' });
      out({ preview_name: id.name, preview_slug: id.slug });
      break;
    }
    case 'wrangler-check':
      assertWranglerSupported(opt('version'));
      out({ wrangler_ok: 'true' });
      break;
    case 'extract-url':
      out({ preview_url: extractPreviewUrl(readFileSync(opt('file'), 'utf8')) });
      break;
    case 'preview-host-check': {
      const r = await previewHostReadiness(fetch, {
        accountId: process.env.CLOUDFLARE_ACCOUNT_ID, token: process.env.CLOUDFLARE_API_TOKEN, worker: opt('worker'),
        base: process.env.CF_API_BASE || undefined,
      });
      if (r.level === 'block') { console.log(`::error::${r.message}`); process.exit(1); }
      console.log(r.level === 'ok' ? `::notice::${r.message}` : `::warning::${r.message}`);
      out({ preview_host: r.level });
      break;
    }
    case 'classify': {
      const r = classifyEvent({
        event: opt('event'), ref: opt('ref'), strategy: opt('strategy'),
        isFork: opt('fork') === 'true', deletedRefType: opt('ref-type'),
        devRef: opt('dev-ref'), dispatchTarget: opt('dispatch-target'),
      });
      out({ action: r.action, reason: r.reason });
      break;
    }
    case 'preflight': {
      const { errors, warnings } = auditPreviewConfig(readConfig(opt('config')), {
        allow: list(opt('allow')), denyResources: list(opt('deny')),
      });
      for (const w of warnings) console.log(`::warning::${w}`);
      for (const e of errors) console.log(`::error::${e}`);
      if (errors.length) process.exit(1);
      out({ preflight: 'ok' });
      break;
    }
    case 'resolve': {
      const target = resolveTarget({
        event: opt('event'), ref: opt('ref'), strategy: opt('strategy'),
        previewOnPush: opt('preview-on-push', 'true') === 'true',
        devRef: opt('dev-ref'), dispatchTarget: opt('dispatch-target'),
      });
      out({ target });
      break;
    }
    case 'resolve-dev': {
      out({ target: resolveSharedDev({
        event: opt('event'), ref: opt('ref'), strategy: opt('strategy'),
        sharedDevOnPush: opt('shared-dev-on-push', 'false') === 'true', dispatchTarget: opt('dispatch-target'),
      }) });
      break;
    }
    case 'rebind-env': {
      // stdout = rewritten text of environment variable --var (raw); the count goes to stderr.
      const r = rebindSha(process.env[opt('var')] ?? '', opt('from'), opt('to'));
      process.stdout.write(r.text);
      console.error(`rebound ${r.replacements} commit reference(s) in ${opt('var')}`);
      break;
    }
    case 'artifact-digest':
      out({ artifact_digest: artifactDigest(opt('config')) });
      break;
    case 'd1-consistency': {
      // The D1 the audited/published config binds must be the exact database the migration targets.
      const r = checkDeployedD1(readConfig(opt('config')), readConfig(opt('source')), opt('target'), {
        binding: opt('binding'), devEnv: opt('dev-env', 'dev'), mode: opt('mode', 'effective'),
      });
      out({ d1_binding: r.binding, d1_id: r.databaseId });
      break;
    }
    case 'd1-shared': {
      out({ shared: String(sharedPreviewDevD1(readConfig(opt('config')), { binding: opt('binding'), devEnv: opt('dev-env', 'dev') })) });
      break;
    }
    case 'dev-config-check': {
      // Worker identity is mandatory. Without an adapter-generated effective config, the named Wrangler
      // environment of the source config (env.<dev-env>) is what `wrangler deploy --env` will use: audit that.
      if (!opt('worker')) throw new Error('Shared Dev requires an expected Worker name (dev_worker_name)');
      const source = readConfig(opt('source'));
      let effective;
      if (opt('effective')) effective = readConfig(opt('effective'));
      else {
        effective = source.env?.[opt('dev-env', 'dev')];
        if (!effective) throw new Error(`No effective config was generated and the source config has no env.${opt('dev-env', 'dev')}`);
        console.log(`::notice::No adapter-generated config; auditing source env.${opt('dev-env', 'dev')}`);
      }
      const { errors } = auditDevConfig(effective, source, { worker: opt('worker'), denyResources: list(opt('deny')), allow: list(opt('allow')), prodUrl: opt('prod-url') });
      for (const e of errors) console.log(`::error::${e}`);
      if (errors.length) process.exit(1);
      out({ dev_config: 'ok' });
      break;
    }
    case 'migration-config': {
      // Source config -> minimal config naming ONLY the selected target's D1. Preview uses previews.d1_databases.
      const cfgPath = resolve(opt('config'));
      const plan = migrationPlan(readConfig(cfgPath), opt('target'), {
        binding: opt('binding'), devEnv: opt('dev-env', 'dev'),
        migrationsDirAbs: (d) => resolve(dirname(cfgPath), d),
      });
      const dir = plan.config.d1_databases[0].migrations_dir;
      if (!existsSync(dir) || !readdirSync(dir).some((f) => f.endsWith('.sql'))) {
        throw new Error(`Migration directory ${dir} is missing or has no .sql files; refusing to migrate ${plan.databaseName}`);
      }
      const dest = resolve(opt('out'));
      mkdirSync(dirname(dest), { recursive: true });
      writeFileSync(dest, JSON.stringify(plan.config, null, 2));
      out({ d1_binding: plan.binding, d1_name: plan.databaseName, d1_id: plan.databaseId, migrate_config: dest });
      break;
    }
    case 'effective-config': {
      // Adapters (e.g. Astro) emit a redirected config at build time: .wrangler/deploy/config.json -> dist/.../wrangler.json
      const redirect = resolve(opt('dir', '.'), '.wrangler/deploy/config.json');
      let path = '';
      try { path = resolve(dirname(redirect), JSON.parse(readFileSync(redirect, 'utf8')).configPath); } catch { /* no redirect */ }
      out({ effective_config: path });
      break;
    }
    case 'audit-prod': {
      const { errors, warnings } = auditProdConfig(readConfig(opt('effective')), readConfig(opt('source')), { allow: list(opt('allow')) });
      for (const w of warnings) console.log(`::warning::${w}`);
      for (const e of errors) console.log(`::error::${e}`);
      if (errors.length) process.exit(1);
      out({ audit_prod: 'ok' });
      break;
    }
    case 'worker-name':
      out({ worker_name: workerName(readConfig(opt('config'))) });
      break;
    case 'migration-scan': {
      // --dir <migrations dir> --files "<a.sql b.sql>": files must exist inside the dir. An unreadable or empty
      // set is NOT additive (fail closed): the caller treats it as unknown.
      const dir = resolve(opt('dir'));
      const files = list(opt('files')).filter((f) => /^[\w.-]+\.sql$/.test(f));
      if (!files.length) { out({ d1_scan: 'unknown', d1_scan_reason: 'no pending migration file names could be determined' }); break; }
      const texts = files.map((f) => readFileSync(resolve(dir, f), 'utf8'));
      const r = classifyMigrationSql(texts);
      out({ d1_scan: r.level, d1_scan_reason: r.reasons.slice(0, 3).join(' | ') || 'only additive statements' });
      break;
    }
    case 'resolve-kinds': {
      // Enabled delivery kinds for the event (comma separated; empty = nothing to deliver). Throws on a forbidden combination.
      const kinds = resolveDeliveryTargets({
        event: opt('event'), ref: opt('ref'), strategy: opt('strategy'), previewOnPush: opt('preview-on-push', 'true') === 'true',
        sharedDevOnPush: opt('shared-dev-on-push', 'false') === 'true', sharedDevNativeOnPush: opt('shared-dev-native-on-push', 'false') === 'true',
        dispatchTarget: opt('dispatch-target'), devRef: opt('dev-ref'),
      });
      out({ kinds: kinds.join(',') });
      break;
    }
    case 'smoke-mode':
      out({ smoke_mode: resolveSmokeMode(assertKind(opt('kind')), opt('mode', 'full'), opt('overrides')) });
      break;
    case 'url-isolation': {
      // --kind K --env-var VAR (this kind's build env text) --urls JSON {kind:url}
      const bad = foreignUrlsInBuildEnv(assertKind(opt('kind')), process.env[opt('env-var')] ?? '', JSON.parse(opt('urls', '{}')));
      for (const b of bad) console.log(`::error::build environment of ${opt('kind')}: ${b}; each target must be built with its own URLs`);
      if (bad.length) process.exit(1);
      out({ url_isolation: 'ok' });
      break;
    }
    case 'resolve-url': {
      // Canonical URL of ONE target, resolved BEFORE its build (fails closed). Preview kinds: derived from the real Preview
      // identity + the host Worker's Cloudflare-reported preview_url_suffix (or the caller's preview_url_suffix), cross-checked
      // with any declared URL. Production / persistent Dev: the declared URL (required for Dev).
      const kind = assertKind(opt('kind'));
      let url = '';
      let source = 'declared';
      if (kind === 'native-branch' || kind === 'native-dev') {
        let apiSuffix = '';
        if (!opt('suffix') && opt('host-check', 'true') !== 'false') {
          const r = await previewHostReadiness(fetch, {
            accountId: process.env.CLOUDFLARE_ACCOUNT_ID, token: process.env.CLOUDFLARE_API_TOKEN, worker: opt('host-worker'),
            base: process.env.CF_API_BASE || undefined,
          });
          if (r.level === 'block') throw new Error(r.message);
          apiSuffix = r.suffix ?? '';
        }
        ({ url, source } = resolveCanonicalUrl({ previewName: opt('name'), explicit: opt('explicit'), suffix: opt('suffix'), apiSuffix }));
      } else if (opt('explicit')) {
        url = canonicalOrigin(opt('explicit'), `${kind} app URL`);
      } else if (kind === 'persistent-dev') {
        throw new Error('persistent-dev requires app_url_dev (the canonical Dev URL must be known before the build)');
      }
      const envText = opt('build-env') && existsSync(opt('build-env')) ? readFileSync(opt('build-env'), 'utf8') : '';
      const bad = checkBuildEnvOrigins(envText, url);
      for (const b of bad) console.log(`::error::build environment of ${kind}: ${b}`);
      if (bad.length) process.exit(1);
      out({ target_url: url, url_source: source });
      break;
    }
    case 'plan': {
      // The delivery plan of a run, from the validated workflow inputs (INPUTS_JSON) - data only, no remote effects.
      const i = JSON.parse(process.env.INPUTS_JSON ?? '{}');
      const str = (k) => (i[k] == null ? '' : String(i[k]));
      const bool = (k) => i[k] === true || i[k] === 'true';
      let kinds;
      let restored = null; // verify: the DEPLOY call's own descriptors (validated); nothing is re-derived from this call's inputs
      if (opt('phase') === 'verify') {
        restored = restoreDescriptors(JSON.parse(process.env.PHASE_STATE ?? '{}'), { repo: process.env.GITHUB_REPOSITORY ?? '', runId: opt('run-id', '0') });
        kinds = Object.keys(restored);
      } else {
        kinds = resolveDeliveryTargets({
          event: opt('event'), ref: opt('ref'), strategy: str('deployment_strategy'), previewOnPush: bool('preview_on_branch_push'),
          sharedDevOnPush: bool('shared_dev_on_branch_push'), sharedDevNativeOnPush: bool('shared_dev_native_preview_on_branch_push'),
          dispatchTarget: str('dispatch_target'), devRef: str('persistent_dev_ref'),
        });
      }
      let migrations = [];
      if (opt('phase') !== 'verify' && bool('d1_migrations') && kinds.length) {
        const cfgPath = resolve(opt('app-dir', '.'), str('wrangler_config') || 'wrangler.toml');
        migrations = planMigrations(readConfig(cfgPath), kinds, {
          accountId: process.env.CLOUDFLARE_ACCOUNT_ID ?? '', binding: str('d1_binding'), devEnv: str('dev_wrangler_env') || 'dev',
          migrationsDirAbs: (d) => resolve(dirname(cfgPath), d),
        });
      }
      const kindKey = (k) => k.replace(/-/g, '_');
      const flat = {};
      for (const k of DELIVERY_KINDS) {
        const g = migrations.find((m) => m.members.includes(k));
        flat[`on_${kindKey(k)}`] = String(kinds.includes(k));
        flat[`owns_${kindKey(k)}`] = String(Boolean(g && g.owner === k));
      }
      const descriptors = restored ?? buildDescriptors({
          kinds, app: str('app_name'), runId: opt('run-id', '0'), repo: process.env.GITHUB_REPOSITORY ?? '', lockScope: str('d1_lock_scope'), groups: migrations,
          smokeMode: str('smoke_mode') || 'full', smokeOverrides: str('smoke_mode_overrides'), phase: opt('phase') || 'all',
      });
      for (const m of migrations) console.error(`::notice::D1 ${m.databaseName}: one migration (owner ${m.owner}) serves ${m.members.join(', ')}`);
      out({ kinds: JSON.stringify(kinds), descriptors: JSON.stringify(descriptors), has_targets: String(kinds.length > 0), has_migrations: String(migrations.length > 0), ...flat });
      break;
    }
    case 'descriptor-check': { // --kind K --target T --d1 ID (descriptor JSON in env DESCRIPTOR): the stage's own bindings must match the record
      const d = parseDescriptor(process.env.DESCRIPTOR ?? '', opt('kind'));
      if (d.target !== opt('target')) throw new Error(`target descriptor engine target '${d.target}' differs from the stage's '${opt('target')}'`);
      if (d.d1_expected_id !== opt('d1')) throw new Error('target descriptor D1 id differs from the stage\'s expected D1');
      out({ descriptor: 'ok', smoke_mode: d.smoke_mode });
      break;
    }
    case 'migration-gate': { // env: GATE_STAGE migrate|publish, DESCRIPTOR, D1_MIGRATIONS, KINDS+PEERS (migrate) | OWN_* / OWNER_* results (publish)
      const e = process.env;
      const d = parseDescriptor(e.DESCRIPTOR ?? '', opt('kind'));
      const parse = (t, dflt) => { try { return JSON.parse(t || dflt); } catch { return null; } };
      if (e.GATE_STAGE === 'migrate') {
        const kinds = parse(e.KINDS, '[]');
        if (!Array.isArray(kinds)) throw new Error('kinds is not a JSON array');
        const bad = checkPeerPreflights(kinds, parse(e.PEERS, '{}'));
        for (const b of bad) console.log(`::error::${b}`);
        if (bad.length) process.exit(1);
        out({ migration_gate: 'ok' });
      } else {
        const m = resolveMigrationUsed({
          d1Migrations: e.D1_MIGRATIONS === 'true', kind: d.kind, ownerKind: d.d1_owner_kind, expectedId: d.d1_expected_id,
          ownResult: e.OWN_RESULT ?? '', ownJson: e.OWN_JSON ?? '', ownerResult: e.OWNER_RESULT ?? '', ownerJson: e.OWNER_JSON ?? '',
        });
        out({ migration_used: JSON.stringify(m) });
      }
      break;
    }
    case 'verification-outcome': { // --level L
      const o = verificationOutcome(opt('level'));
      out({ verified: String(o.verified), verification_kind: o.kind });
      break;
    }
    case 'rollback-guard': { // env: POLICY PREVIOUS CURRENT WORKER D1_PENDING D1_COMPAT D1_SCAN [LIVE set only for the live check]
      const e = process.env;
      const b = prodRollbackBlock({
        policy: e.POLICY ?? '', previous: e.PREVIOUS ?? '', current: e.CURRENT ?? '', worker: e.WORKER ?? '', d1Pending: e.D1_PENDING === 'true',
        d1Compat: e.D1_COMPAT ?? '', d1Scan: e.D1_SCAN ?? '', live: 'LIVE' in e ? e.LIVE : undefined,
      });
      out({ block_code: b ? b.code : '', block_why: b ? b.why : '' });
      break;
    }
    case 'rollback-verdict': { // env: TARGET RB_RESULT RB_VERIFY RB_LEVEL GOOD_SHA RECOVERED VERIFIED SMOKE_LEVEL
      const e = process.env;
      const v = rollbackVerdict({
        target: e.TARGET, rbResult: e.RB_RESULT ?? '', rbVerify: e.RB_VERIFY ?? '', rbLevel: e.RB_LEVEL ?? '', goodSha: e.GOOD_SHA ?? '',
        recovered: e.RECOVERED ?? '', verified: e.VERIFIED ?? '', smokeLevel: e.SMOKE_LEVEL ?? '',
      });
      out({ result: v.result, level: v.level, ok: String(v.ok) });
      break;
    }
    case 'init-checks': { // --state DIR --inputs FILE; env ENGINE_* GITHUB_EVENT_NAME GITHUB_REF CALL_INPUTS_JSON PREVIEW_SECRETS. ONE process for every init check.
      const e = process.env;
      const r = validateInit({
        inputs: JSON.parse(readFileSync(opt('inputs'), 'utf8')), target: e.ENGINE_TARGET, kind: e.ENGINE_KIND ?? '', stage: e.ENGINE_STAGE || 'all', mode: e.ENGINE_MODE || 'auto',
        d1Expect: e.ENGINE_D1_EXPECT ?? '', descriptor: e.ENGINE_DESCRIPTOR ?? '', callInputs: e.CALL_INPUTS_JSON ?? '', event: e.GITHUB_EVENT_NAME, ref: e.GITHUB_REF,
        previewSecrets: e.PREVIEW_SECRETS ?? '',
      });
      const dir = opt('state');
      const put = (name, text) => writeFileSync(resolve(dir, name), text, { mode: 0o600 });
      const env = (name, pairs) => { if (pairs?.length) appendFileSync(resolve(dir, name), pairs.map(([k, v]) => `${k}=${v}`).join('\n') + '\n', { mode: 0o600 }); };
      put('kind', r.kind); put('smoke_mode', r.smokeMode); put('stage', r.stage); put('d1_expect', r.d1Expect); put('target', r.target); put('mode', r.mode);
      env('build.env', r.buildEnv); env('smoke.env', r.smokeEnv); env('preview.env', r.previewEnv);
      for (const [, v] of r.previewEnv ?? []) if (v) console.log(`::add-mask::${v}`);
      out({ kind: r.kind, smoke_mode: r.smokeMode });
      break;
    }
    case 'stale':
      out({ stale: String(isStale({ runSha: opt('sha'), branchTipSha: opt('tip') })) });
      break;
    default:
      console.error('usage: cli.mjs identity|wrangler-check|extract-url|preview-host-check|classify|resolve|resolve-dev|rebind-env|artifact-digest|d1-consistency|d1-shared|dev-config-check|preflight|migration-config|effective-config|audit-prod|worker-name|migration-scan|resolve-kinds|smoke-mode|url-isolation|resolve-url|plan|stale');
      process.exit(2);
  }
} catch (e) {
  console.log(`::error::${e.message}`);
  process.exit(1);
}
