#!/usr/bin/env node
// CLI used by _cf-worker-v2.yml. Prints key=value lines (GITHUB_OUTPUT-safe) or exits non-zero.
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import {
  assertWranglerSupported, auditDevConfig, previewHostReadiness, auditPreviewConfig, auditProdConfig, classifyEvent, extractPreviewUrl,
  isStale, migrationPlan, workerName, classifyMigrationSql, previewIdentity, readConfig, rebindSha, artifactDigest, checkDeployedD1, resolveSharedDev, resolveTarget, sharedPreviewDevD1,
} from './lib.mjs';
import {
  DELIVERY_KINDS, ENGINE_TARGET, assertKind, branchPreviewIdentity, environmentName, foreignUrlsInBuildEnv, planMigrations, resolveDeliveryTargets, resolveSmokeMode,
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
    case 'plan': {
      // The delivery plan of a run, from the validated workflow inputs (INPUTS_JSON) - data only, no remote effects.
      const i = JSON.parse(process.env.INPUTS_JSON ?? '{}');
      const str = (k) => (i[k] == null ? '' : String(i[k]));
      const bool = (k) => i[k] === true || i[k] === 'true';
      let kinds;
      if (opt('phase') === 'verify') {
        const st = JSON.parse(process.env.PHASE_STATE ?? '{}');
        kinds = Array.isArray(st.kinds) ? st.kinds : [];
        for (const k of kinds) assertKind(k);
      } else {
        kinds = resolveDeliveryTargets({
          event: opt('event'), ref: opt('ref'), strategy: str('deployment_strategy'), previewOnPush: bool('preview_on_branch_push'),
          sharedDevOnPush: bool('shared_dev_on_branch_push'), sharedDevNativeOnPush: bool('shared_dev_native_preview_on_branch_push'),
          dispatchTarget: str('dispatch_target'), devRef: str('persistent_dev_ref'),
        });
      }
      for (const k of kinds) resolveSmokeMode(k, str('smoke_mode') || 'full', str('smoke_mode_overrides')); // fail closed on a bad mode
      let migrations = [];
      if (opt('phase') !== 'verify' && bool('d1_migrations') && kinds.length) {
        const cfgPath = resolve(opt('app-dir', '.'), str('wrangler_config') || 'wrangler.toml');
        migrations = planMigrations(readConfig(cfgPath), kinds, {
          accountId: process.env.CLOUDFLARE_ACCOUNT_ID ?? '', binding: str('d1_binding'), devEnv: str('dev_wrangler_env') || 'dev',
          migrationsDirAbs: (d) => resolve(dirname(cfgPath), d),
        });
      }
      const app = str('app_name');
      const entry = (k) => ({ kind: k, target: ENGINE_TARGET[k], environment: environmentName(app, k) });
      const plan = {
        kinds,
        deliveries: { include: kinds.map(entry) },
        migrations: { include: migrations.map((g) => ({ ...entry(g.owner), members: g.members.join(','), database: g.databaseName })) },
      };
      for (const m of migrations) console.error(`::notice::D1 ${m.databaseName}: one migration (owner ${m.owner}) serves ${m.members.join(', ')}`);
      out({
        kinds: JSON.stringify(plan.kinds), has_targets: String(kinds.length > 0), deliveries: JSON.stringify(plan.deliveries),
        has_migrations: String(migrations.length > 0), migrations: JSON.stringify(plan.migrations),
      });
      break;
    }
    case 'stale':
      out({ stale: String(isStale({ runSha: opt('sha'), branchTipSha: opt('tip') })) });
      break;
    default:
      console.error('usage: cli.mjs identity|wrangler-check|extract-url|preview-host-check|classify|resolve|resolve-dev|rebind-env|artifact-digest|d1-consistency|d1-shared|dev-config-check|preflight|migration-config|effective-config|audit-prod|worker-name|migration-scan|resolve-kinds|smoke-mode|url-isolation|plan|stale');
      process.exit(2);
  }
} catch (e) {
  console.log(`::error::${e.message}`);
  process.exit(1);
}
