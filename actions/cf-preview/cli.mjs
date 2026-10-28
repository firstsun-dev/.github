#!/usr/bin/env node
// CLI used by _cf-worker-v2.yml. Prints key=value lines (GITHUB_OUTPUT-safe) or exits non-zero.
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import {
  assertWranglerSupported, auditDevConfig, previewHostReadiness, auditPreviewConfig, auditProdConfig, classifyEvent, extractPreviewUrl,
  isStale, migrationPlan, previewIdentity, readConfig, rebindSha, artifactDigest, checkDeployedD1, resolveSharedDev, resolveTarget, sharedPreviewDevD1,
} from './lib.mjs';

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
      const id = previewIdentity({ repo: opt('repo'), worker: opt('worker'), ref: opt('ref'), hostWorker: opt('host-worker') });
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
    case 'stale':
      out({ stale: String(isStale({ runSha: opt('sha'), branchTipSha: opt('tip') })) });
      break;
    default:
      console.error('usage: cli.mjs identity|wrangler-check|extract-url|preview-host-check|classify|resolve|resolve-dev|rebind-env|artifact-digest|d1-consistency|d1-shared|dev-config-check|preflight|migration-config|effective-config|audit-prod|stale');
      process.exit(2);
  }
} catch (e) {
  console.log(`::error::${e.message}`);
  process.exit(1);
}
