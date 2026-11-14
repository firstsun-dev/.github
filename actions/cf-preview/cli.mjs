#!/usr/bin/env node
// CLI used by _cf-worker-v2.yml. stdout carries ONLY key=value records (it is piped into $GITHUB_OUTPUT / captured by the shell); every
// diagnostic and workflow annotation (::error:: / ::warning:: / ::notice:: / ::add-mask::) goes to stderr. Failure = non-zero exit.
import { artifactBase, packageArtifact, verifyArtifact } from './artifact.mjs';
import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import {
  assertWranglerSupported, auditDevConfig, previewHostReadiness, auditPreviewConfig, auditProdConfig, extractPreviewUrl,
  migrationPlan, workerName, classifyMigrationSql, previewIdentity, readConfig, checkDeployedD1,
} from './lib.mjs';
import {
  assertSlot, canonicalOrigin, checkBuildEnvOrigins, foreignUrlsInBuildEnv, planMigration, resolveCanonicalUrl, resolveDelivery, resolveSmokeMode,
  buildDescriptor, parseDescriptor, resolveMigrationUsed, parseD1Rows, pendingMigrations, classifyMigrationEntries, verificationOutcome, prodRollbackBlock, rollbackVerdict,
  restoreDescriptor, validateInit, runLockGroup, TARGETS,
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
      // A PR Preview is named `pr-<number>` (stable for the life of the PR, never collides with the fixed Dev name); without --pr it is the
      // branch-derived identity (used only by the Preview ledger bookkeeping of branches that already have one).
      const id = previewIdentity({ repo: opt('repo'), worker: opt('worker'), ref: opt('ref'), hostWorker: opt('host-worker') });
      const pr = opt('pr');
      if (pr && !/^[1-9][0-9]{0,8}$/.test(pr)) throw new Error(`invalid pull request number '${pr}'`);
      out(pr ? { preview_name: `pr-${pr}`, preview_slug: `pr-${pr}` } : { preview_name: id.name, preview_slug: id.slug });
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
      if (r.level === 'block') { console.error(`::error::${r.message}`); process.exit(1); }
      console.log(r.level === 'ok' ? `::notice::${r.message}` : `::warning::${r.message}`);
      out({ preview_host: r.level });
      break;
    }
    case 'preflight': {
      const { errors, warnings } = auditPreviewConfig(readConfig(opt('config')), {
        allow: list(opt('allow')), denyResources: list(opt('deny')),
      });
      for (const w of warnings) console.error(`::warning::${w}`);
      for (const e of errors) console.error(`::error::${e}`);
      if (errors.length) process.exit(1);
      out({ preflight: 'ok' });
      break;
    }
    case 'd1-consistency': {
      // The D1 the audited/published config binds must be the exact database the migration targets.
      let deployed = readConfig(opt('config'));
      if (opt('target') === 'dev' && deployed.env?.[opt('dev-env', 'dev')]) deployed = { ...deployed.env[opt('dev-env', 'dev')] }; // a Dev Worker packaged with its named environment
      const r = checkDeployedD1(deployed, readConfig(opt('source')), opt('target'), {
        binding: opt('binding'), devEnv: opt('dev-env', 'dev'), mode: opt('mode', 'effective'),
      });
      out({ d1_binding: r.binding, d1_id: r.databaseId });
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
        console.error(`::notice::No adapter-generated config; auditing source env.${opt('dev-env', 'dev')}`);
      }
      const { errors } = auditDevConfig(effective, source, { worker: opt('worker'), denyResources: list(opt('deny')), allow: list(opt('allow')), prodUrl: opt('prod-url') });
      for (const e of errors) console.error(`::error::${e}`);
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
    case 'package-artifact': {
      // UNPRIVILEGED Build job: assemble the artifact from the audited config + the dry-run bundle (see artifact.mjs). Identity comes from env.
      const e = process.env;
      const cfgPath = resolve(opt('config'));
      const cfg = readConfig(cfgPath);
      const devEnv = opt('dev-env', 'dev');
      const section = opt('mode') === 'source' && opt('target') === 'dev' ? (cfg.env?.[devEnv] ?? {}) : {};
      const effectiveMain = section.main ?? cfg.main;
      if (typeof effectiveMain !== 'string') throw new Error('the config has no `main`: a Worker artifact needs an entry point');
      const m = packageArtifact({
        cfg, configDir: dirname(cfgPath), workspace: resolve(opt('app-dir', '.')), bundleDir: resolve(opt('bundle-dir')), dest: resolve(opt('dest')), effectiveMain,
        manifest: {
          repository: e.CFV2_REPOSITORY, app: e.CFV2_APP, target: e.CFV2_TARGET, driver: e.CFV2_DRIVER, slot: e.CFV2_SLOT, run_id: e.CFV2_RUN_ID, run_attempt: e.CFV2_RUN_ATTEMPT,
          source_sha: e.CFV2_SOURCE_SHA, tested_sha: e.CFV2_TESTED_SHA, artifact_sha: e.CFV2_ARTIFACT_SHA, target_url: e.CFV2_TARGET_URL ?? '', config_mode: opt('mode'),
          worker_name: String(section.name ?? cfg.name ?? ''),
        },
      });
      out({ artifact_digest: m.files_digest, worker_name: m.worker_name });
      break;
    }
    case 'verify-artifact': {
      // PRIVILEGED jobs: the artifact must match what THIS job expects (its own trusted data), before any audit or publish.
      const e = process.env;
      const { manifest, mode } = verifyArtifact(resolve(opt('dir')), {
        repository: e.GITHUB_REPOSITORY, app: e.CFV2_APP, target: e.CFV2_TARGET, driver: e.CFV2_DRIVER, slot: e.CFV2_SLOT, runId: e.GITHUB_RUN_ID, runAttempt: e.CFV2_RUN_ATTEMPT, currentAttempt: e.GITHUB_RUN_ATTEMPT,
        sourceSha: e.CFV2_SOURCE_SHA, testedSha: e.CFV2_TESTED_SHA, targetUrl: e.CFV2_TARGET_URL ?? '', workerName: e.CFV2_WORKER_NAME ?? '', devEnv: e.CFV2_DEV_ENV || 'dev',
      });
      out({ artifact_digest: manifest.files_digest, artifact_sha: manifest.artifact_sha, config_mode: mode, worker_name: manifest.worker_name });
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
      for (const w of warnings) console.error(`::warning::${w}`);
      for (const e of errors) console.error(`::error::${e}`);
      if (errors.length) process.exit(1);
      out({ audit_prod: 'ok' });
      break;
    }
    case 'worker-name':
      out({ worker_name: workerName(readConfig(opt('config'))) });
      break;
    case 'migration-pending': {
      // Read-only precheck: --dir <migrations dir> --exists <json file> [--applied <json file>] (both `wrangler d1 execute --json` stdout).
      // Prints pending=true|false and pending_count. A missing migrations table = everything pending. Any malformed input throws (fail closed).
      const { files, ambiguous } = classifyMigrationEntries(readdirSync(resolve(opt('dir')), { withFileTypes: true }).map((e) => ({ name: e.name, file: e.isFile() })));
      const tableExists = parseD1Rows(readFileSync(opt('exists'), 'utf8')).length > 0;   // parsed even when unused: malformed output always throws
      const applied = tableExists ? parseD1Rows(readFileSync(opt('applied'), 'utf8')) : [];
      if (ambiguous.length) { // cannot prove the complete migration set -> never "nothing pending"
        console.error(`::notice::migration entries the precheck cannot classify with certainty (${JSON.stringify(ambiguous)}); running the full path.`);
        out({ pending: 'true', pending_count: String(ambiguous.length) });
        break;
      }
      const pending = pendingMigrations({ files, tableExists, applied });
      out({ pending: String(pending.length > 0), pending_count: String(pending.length) });
      break;
    }
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
    case 'resolve-url': {
      // Canonical URL of ONE delivery, resolved BEFORE its build (fails closed). native-preview deliveries (PR Preview, native Dev): derived from the
      // real Preview identity + the host Worker's Cloudflare-reported preview_url_suffix (or the caller's preview_url_suffix), cross-checked
      // with any declared URL. Worker deliveries: the declared URL (required for a Dev Worker). --kind = the internal slot.
      const kind = assertSlot(opt('kind'));
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
        throw new Error('a Dev Worker requires app_url_dev (the canonical Dev URL must be known before the build)');
      }
      const envText = opt('build-env') && existsSync(opt('build-env')) ? readFileSync(opt('build-env'), 'utf8') : '';
      const bad = checkBuildEnvOrigins(envText, url);
      for (const b of bad) console.error(`::error::build environment of ${kind}: ${b}`);
      if (bad.length) process.exit(1);
      out({ target_url: url, url_source: source });
      break;
    }
    case 'plan': {
      // The delivery plan of a run, from the validated workflow inputs (INPUTS_JSON) - data only, no remote effects. At most ONE delivery.
      const i = JSON.parse(process.env.INPUTS_JSON ?? '{}');
      const str = (k) => (i[k] == null ? '' : String(i[k]));
      const bool = (k) => i[k] === true || i[k] === 'true';
      const ev = { event: opt('event'), ref: opt('ref'), isFork: opt('fork') === 'true', prAction: opt('pr-action'), prNumber: opt('pr-number') };
      const repo = process.env.GITHUB_REPOSITORY ?? '';
      let descriptor = null;
      let effective = i; // the inputs every later job and the engine read (a Preview never migrates: d1_migrations is off for it)
      let group;
      if (opt('phase') === 'verify') {
        const state = JSON.parse(process.env.PHASE_STATE ?? '{}');
        descriptor = restoreDescriptor(state, { repo, runId: opt('run-id', '0'), ...ev });
        effective = state.inputs;
        group = String(state.group ?? '');
      } else {
        const delivery = resolveDelivery({
          ...ev, previewPolicy: str('preview_policy') || 'none', devDriver: str('dev_driver') || 'none', dispatchTarget: str('dispatch_target'), approvedDevRef: str('approved_dev_ref'),
        });
        if (delivery) {
          let d1 = '';
          if (bool('d1_migrations') && delivery.target !== 'preview') {
            const cfgPath = resolve(opt('app-dir', '.'), str('wrangler_config') || 'wrangler.toml');
            const m = planMigration(readConfig(cfgPath), delivery, {
              accountId: process.env.CLOUDFLARE_ACCOUNT_ID ?? '', binding: str('d1_binding'), devEnv: str('dev_wrangler_env') || 'dev',
              migrationsDirAbs: (d) => resolve(dirname(cfgPath), d),
            });
            d1 = m.databaseId;
            console.error(`::notice::D1 ${m.databaseName}: ONE migration serves ${delivery.target}/${delivery.driver}`);
          } else if (bool('d1_migrations')) effective = { ...i, d1_migrations: false };
          descriptor = buildDescriptor({
            delivery, app: str('app_name'), runId: opt('run-id', '0'), repo, lockScope: str('d1_lock_scope'), d1ExpectedId: d1,
            smokeMode: str('smoke_mode') || 'full', smokeOverrides: str('smoke_mode_overrides'), phase: opt('phase') || 'all',
          });
        }
        group = runLockGroup({ ...ev, inputs: i, runId: opt('run-id', '0') });
      }
      // Arguments of the canonical-URL resolution (the Plan's only Cloudflare read): which Preview name / host Worker / declared URL belong to this delivery.
      const slot = descriptor?.slot ?? '';
      const eff = (k) => (effective[k] == null ? '' : String(effective[k]));
      const urlArgs = {
        slot,
        preview_name: slot === 'native-branch' ? `pr-${opt('pr-number')}` : slot === 'native-dev' ? 'dev' : '',
        url_host: slot === 'persistent-dev' ? eff('dev_worker_name') : slot === 'production' ? eff('app_name') : slot ? (eff('preview_worker_name') || eff('app_name')) : '',
        url_explicit: slot === 'production' ? eff('app_url_prod') : slot === 'persistent-dev' || slot === 'native-dev' ? eff('app_url_dev') : '',
      };
      out({
        ...urlArgs,
        artifact_base: descriptor ? artifactBase({ repository: repo, app: str('app_name'), slot: descriptor.slot }) : '',
        has_delivery: String(Boolean(descriptor)), target: descriptor?.target ?? '', driver: descriptor?.driver ?? '', group,
        descriptor: descriptor ? JSON.stringify(descriptor) : '', inputs_json: JSON.stringify(effective),
        has_migrations: String(Boolean(descriptor?.d1_expected_id)),
        migration: descriptor?.d1_expected_id ? JSON.stringify({ environment: descriptor.environment, d1_id: descriptor.d1_expected_id }) : '',
      });
      break;
    }
    case 'migration-gate': { // publish stage; env: DESCRIPTOR, MIGRATION_RESULT / MIGRATION_JSON (needs.migration.result / outputs.migration_json)
      const e = process.env;
      const m = resolveMigrationUsed({ descriptor: JSON.parse(e.DESCRIPTOR ?? 'null'), result: e.MIGRATION_RESULT ?? '', json: e.MIGRATION_JSON ?? '', runSha: e.RUN_SHA ?? e.GITHUB_SHA ?? '' });
      out({ migration_used: JSON.stringify(m) });
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
    case 'rollback-verdict': { // Production only; env: RB_RESULT RB_VERIFY RB_LEVEL
      const e = process.env;
      const v = rollbackVerdict({
        rbResult: e.RB_RESULT ?? '', rbVerify: e.RB_VERIFY ?? '', rbLevel: e.RB_LEVEL ?? '',
      });
      out({ result: v.result, level: v.level, ok: String(v.ok) });
      break;
    }
    case 'init-checks': { // --state DIR --inputs FILE; env ENGINE_* GITHUB_EVENT_NAME GITHUB_REF CALL_INPUTS_JSON PREVIEW_SECRETS. ONE process for every init check.
      const e = process.env;
      const r = validateInit({
        inputs: JSON.parse(readFileSync(opt('inputs'), 'utf8')), target: e.ENGINE_TARGET, kind: e.ENGINE_KIND ?? '', stage: e.ENGINE_STAGE || 'all',
        d1Expect: e.ENGINE_D1_EXPECT ?? '', descriptor: e.ENGINE_DESCRIPTOR ?? '', callInputs: e.CALL_INPUTS_JSON ?? '', event: e.GITHUB_EVENT_NAME, ref: e.GITHUB_REF,
        isFork: e.CFV2_IS_FORK === 'true', prAction: e.CFV2_PR_ACTION ?? '', previewSecrets: e.PREVIEW_SECRETS ?? '',
      });
      const dir = opt('state');
      const put = (name, text) => writeFileSync(resolve(dir, name), text, { mode: 0o600 });
      const env = (name, pairs) => { if (pairs?.length) appendFileSync(resolve(dir, name), pairs.map(([k, v]) => `${k}=${v}`).join('\n') + '\n', { mode: 0o600 }); };
      put('kind', r.slot); put('delivery_target', r.delivery.target); put('driver', r.delivery.driver); put('smoke_mode', r.smokeMode); put('stage', r.stage); put('d1_expect', r.d1Expect); put('target', r.target);
      env('build.env', r.buildEnv); env('smoke.env', r.smokeEnv); env('preview.env', r.previewEnv);
      for (const [, v] of r.previewEnv ?? []) if (v) console.error(`::add-mask::${v}`);
      out({ kind: r.slot, smoke_mode: r.smokeMode });
      break;
    }
    default:
      console.error('usage: cli.mjs identity|wrangler-check|extract-url|preview-host-check|d1-consistency|dev-config-check|preflight|migration-config|effective-config|audit-prod|worker-name|migration-pending|migration-scan|resolve-url|plan|migration-gate|verification-outcome|rollback-guard|rollback-verdict|init-checks');
      process.exit(2);
  }
} catch (e) {
  console.error(`::error::${e.message}`);
  process.exit(1);
}
