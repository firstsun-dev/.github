// Pure decision logic of the target-driven delivery (OpenSpec simplify-cf-ci-stages-and-dev-delivery). No I/O. Imported by cli.mjs,
// unit-tested in scripts/cf-preview/delivery.test.mjs.
//
// ONE vocabulary: three TARGETS (production | preview | dev) delivered by two DRIVERS (worker | native-preview). An event selects AT MOST
// ONE delivery (resolveDelivery); everything downstream (plan, migration, publish, smoke, recovery, release) is the same engine and differs
// only at the driver boundary.
//
// Internal persistence slot: the engine still keys GitHub Environments (`<app>/prod|preview|native-dev|dev`) and the known-good ledger by one of
// four historical slot names. They are DERIVED from (target, driver) in ONE place (SLOT below) and never appear in a public input or
// output: renaming them would orphan the existing known-good records and Environment protection rules at cutover.
import { migrationPlan } from './lib.mjs';
import { parseEnvLines } from './parse-env.mjs';

export const TARGETS = ['production', 'preview', 'dev'];
export const DRIVERS = ['worker', 'native-preview'];
export const PREVIEW_POLICIES = ['none', 'pr'];
export const DEV_DRIVERS = ['none', 'native-preview', 'worker'];
export const SMOKE_MODES = ['full', 'basic', 'skip'];
/** Fixed name of the stable Native Dev Preview. */
export const NATIVE_DEV_NAME = 'dev';
/** Preview names no GC / retention action may ever delete (the stable Dev). PR Previews are `pr-<number>` and can never collide with it. */
export const RESERVED_PREVIEW_NAMES = [NATIVE_DEV_NAME];

const SLOT = { 'production/worker': 'production', 'preview/native-preview': 'native-branch', 'dev/native-preview': 'native-dev', 'dev/worker': 'persistent-dev' };
const ENVIRONMENT_SUFFIX = { production: 'prod', 'native-branch': 'preview', 'native-dev': 'native-dev', 'persistent-dev': 'dev' };

/** Target policy: production -> worker, preview -> native-preview, dev -> either. Throws on anything else. */
export function assertDelivery(d) {
  if (d === null || typeof d !== 'object' || !TARGETS.includes(d.target)) throw new Error(`unknown delivery target '${d?.target}' (expected ${TARGETS.join(' | ')})`);
  if (!DRIVERS.includes(d.driver)) throw new Error(`unknown delivery driver '${d.driver}' (expected ${DRIVERS.join(' | ')})`);
  if (d.target === 'production' && d.driver !== 'worker') throw new Error('target production is delivered by the worker driver only');
  if (d.target === 'preview' && d.driver !== 'native-preview') throw new Error('target preview is delivered by the native-preview driver only');
  return d;
}
export const slotOf = (d) => SLOT[`${assertDelivery(d).target}/${d.driver}`];
/** Engine config / credential class: prod | preview | dev. A native-preview Dev uses the `[previews]` bindings, i.e. the `preview` class. */
export const engineTarget = (d) => (assertDelivery(d).target === 'production' ? 'prod' : d.target === 'preview' || d.driver === 'native-preview' ? 'preview' : 'dev');
export const environmentName = (app, d) => `${app}/${ENVIRONMENT_SUFFIX[slotOf(d)]}`;

/** smoke_mode_overrides: JSON object keyed by TARGET; values full|basic|skip. Strict: unknown key/value rejects. */
export function parseSmokeOverrides(text) {
  const s = String(text ?? '').trim();
  if (!s) return {};
  let o;
  try { o = JSON.parse(s); } catch (e) { throw new Error(`smoke_mode_overrides is not valid JSON: ${e.message}`); }
  if (o === null || typeof o !== 'object' || Array.isArray(o)) throw new Error('smoke_mode_overrides must be a JSON object keyed by target');
  for (const [k, v] of Object.entries(o)) {
    if (!TARGETS.includes(k)) throw new Error(`smoke_mode_overrides: unknown target '${k}' (expected ${TARGETS.join(' | ')})`);
    if (!SMOKE_MODES.includes(v)) throw new Error(`smoke_mode_overrides.${k}: unknown smoke mode '${v}' (expected ${SMOKE_MODES.join(' | ')})`);
  }
  return o;
}

export function resolveSmokeMode(target, smokeMode = 'full', overridesText = '') {
  if (!TARGETS.includes(target)) throw new Error(`unknown target '${target}'`);
  const base = smokeMode === '' || smokeMode == null ? 'full' : smokeMode;
  if (!SMOKE_MODES.includes(base)) throw new Error(`Unknown smoke_mode '${base}' (expected ${SMOKE_MODES.join(' | ')})`);
  return parseSmokeOverrides(overridesText)[target] ?? base;
}

/**
 * THE event -> delivery routing. Returns null (CI only) or ONE { target, driver }. Throws (the Plan fails before any mutation) on an
 * explicit request that cannot be served, instead of silently dropping it.
 *   push main                        -> production (worker)
 *   push, any other ref              -> nothing (feature pushes never deploy)
 *   pull_request, same repo, opened/synchronize/reopened, preview_policy=pr -> preview (native-preview); fork PR / closed -> nothing
 *   merge_group, delete, schedule    -> nothing
 *   workflow_dispatch dispatch_target=dev, ref == approved_dev_ref, dev_driver != none -> dev (the configured driver); anything else rejects
 * No automatic event ever reaches the stable Dev, and a manual dispatch can never reach Production.
 */
export function resolveDelivery({ event, ref = '', isFork = false, prAction = '', previewPolicy = 'none', devDriver = 'none', dispatchTarget = '', approvedDevRef = '' }) {
  if (!PREVIEW_POLICIES.includes(previewPolicy)) throw new Error(`Unknown preview_policy '${previewPolicy}' (expected ${PREVIEW_POLICIES.join(' | ')})`);
  if (!DEV_DRIVERS.includes(devDriver)) throw new Error(`Unknown dev_driver '${devDriver}' (expected ${DEV_DRIVERS.join(' | ')})`);
  if (approvedDevRef !== '' && !String(approvedDevRef).startsWith('refs/heads/')) throw new Error(`approved_dev_ref '${approvedDevRef}' must be a branch ref (refs/heads/...)`);
  if (devDriver !== 'none' && !approvedDevRef) throw new Error('dev_driver requires approved_dev_ref (only an approved ref may be promoted to Dev)');
  if (dispatchTarget !== '' && event !== 'workflow_dispatch') throw new Error(`dispatch_target '${dispatchTarget}' is only valid for a workflow_dispatch (event: ${event || 'unknown'}); refusing to ignore it (nothing was changed)`);
  switch (event) {
    case 'workflow_dispatch':
      if (dispatchTarget !== 'dev') throw new Error(`a manual dispatch only promotes to Dev (dispatch_target 'dev'), got '${dispatchTarget}'; Production deploys only from a main push (nothing was changed)`);
      if (devDriver === 'none') throw new Error('manual Dev promotion rejected: this application has no Dev (dev_driver none); nothing was changed');
      if (ref !== approvedDevRef) throw new Error(`manual Dev promotion rejected: ref '${ref}' is not the approved_dev_ref '${approvedDevRef}'; nothing was changed`);
      return { target: 'dev', driver: devDriver };
    case 'pull_request':
      if (previewPolicy !== 'pr' || isFork || prAction === 'closed') return null;
      return { target: 'preview', driver: 'native-preview' };
    case 'push':
      return ref === 'refs/heads/main' ? { target: 'production', driver: 'worker' } : null;
    default:
      return null;
  }
}

/**
 * Migration plan of the ONE delivery: the physical D1 (`account/database_id`) it migrates. Production and Dev migrate their own
 * database; Preview NEVER migrates (a PR must not change a shared remote schema), so the caller passes migrations off for it.
 * migrationPlan refuses a D1 declared on both the production and the non-production side.
 */
export function planMigration(cfg, delivery, { accountId = '', binding = '', devEnv = 'dev', migrationsDirAbs = (d) => d } = {}) {
  const d = assertDelivery(delivery);
  if (d.target === 'preview') throw new Error('a Preview never migrates a remote D1 (remote schema changes from an arbitrary PR are not allowed)');
  const p = migrationPlan(cfg, engineTarget(d), { binding, devEnv, migrationsDirAbs });
  return { id: `${accountId}/${p.databaseId}`, databaseId: p.databaseId, databaseName: p.databaseName, binding: p.binding };
}

/**
 * Build-environment URL isolation: the build env of a target must not carry another target's public URL.
 * `urls` = { kind: url } of every known target URL; `env` = the parsed KEY=VALUE text of THIS kind's build env.
 * Returns the offending lines (empty = isolated).
 */
export function foreignUrlsInBuildEnv(self, buildEnvText, urls) {
  const origin = (u) => { try { return new URL(u).origin; } catch { return ''; } };
  const foreign = Object.entries(urls).filter(([k, u]) => k !== self && u && origin(u)).map(([k, u]) => [k, origin(u)]);
  const bad = [];
  for (const line of String(buildEnvText ?? '').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const key = t.split('=')[0];
    for (const [k, o] of foreign) if (t.includes(o)) bad.push(`${key} contains the ${k} URL (${o})`);
  }
  return bad;
}

/** Compare an asserted target URL with the published one (case/trailing-slash insensitive). */
export const sameUrl = (a, b) => String(a).replace(/\/+$/, '').toLowerCase() === String(b).replace(/\/+$/, '').toLowerCase();

// ---------------------------------------------------------------------------------------------------------------------
// Canonical target URL, resolved BEFORE any build (Preview kinds). Cloudflare documents the Preview URL of a named Preview
// as `<preview-name>-<worker-name>.<subdomain>.workers.dev` (workers.dev) or `<preview-name>.<custom domain>` (custom
// domain). The Workers subdomain API reports the host part as `preview_url_suffix` (leading "-" for workers.dev), so the URL
// is `https://<preview-name><suffix>`. Nothing is guessed: no suffix and no explicit URL means the build must not start.
// ---------------------------------------------------------------------------------------------------------------------
export const PREVIEW_SUFFIX_RE = /^[-.][a-z0-9]([a-z0-9.-]*[a-z0-9])?$/;

/** Normalise a canonical URL to its origin; only an https origin (no path/query/credentials) is a valid app URL. */
export function canonicalOrigin(url, what = 'URL') {
  let u;
  try { u = new URL(String(url)); } catch { throw new Error(`${what} '${url}' is not a valid URL`); }
  if (u.protocol !== 'https:') throw new Error(`${what} must be https: ${u.protocol}//${u.host}`);
  if (u.username || u.password || u.search || u.hash || (u.pathname && u.pathname !== '/')) {
    throw new Error(`${what} must be a bare origin (no path, query, fragment or credentials): ${url}`);
  }
  return u.origin;
}

export function previewUrlFromSuffix(previewName, suffix) {
  if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(String(previewName))) throw new Error(`unsafe Preview name '${previewName}'`);
  if (!PREVIEW_SUFFIX_RE.test(String(suffix))) throw new Error(`Preview URL suffix '${suffix}' is invalid (expected e.g. -<worker>.<subdomain>.workers.dev or .<custom domain>)`);
  const host = `${previewName}${suffix}`;
  const label = host.split('.')[0];
  if (label.length > 63) throw new Error(`Preview hostname label '${label}' is ${label.length} chars (> 63): the Preview name plus the Worker name is too long`);
  return `https://${host}`;
}

/**
 * Resolve the canonical URL of a native-branch / native-dev target. `explicit` = caller-declared URL (may be empty),
 * `suffix` = caller-declared preview_url_suffix, `apiSuffix` = Cloudflare-reported preview_url_suffix of the host Worker.
 * Throws when nothing can be resolved, when the two suffix sources disagree, or when an explicit URL contradicts the derived one.
 */
export function resolveCanonicalUrl({ previewName, explicit = '', suffix = '', apiSuffix = '' }) {
  if (suffix && apiSuffix && suffix !== apiSuffix) {
    throw new Error(`preview_url_suffix '${suffix}' contradicts the suffix Cloudflare reports for the host Worker ('${apiSuffix}')`);
  }
  const useSuffix = suffix || apiSuffix;
  const derived = useSuffix ? previewUrlFromSuffix(previewName, useSuffix) : '';
  const declared = explicit ? canonicalOrigin(explicit, 'declared app URL') : '';
  if (declared && derived && !sameUrl(declared, derived)) {
    throw new Error(`declared app URL ${declared} differs from the URL Cloudflare will serve Preview '${previewName}' at (${derived}); the build would embed the wrong URL`);
  }
  const url = declared || derived;
  if (!url) {
    throw new Error(`cannot resolve the canonical URL of Preview '${previewName}' before the build: Cloudflare did not report a preview_url_suffix for the host Worker and neither an explicit app URL nor preview_url_suffix was given (set preview_url_suffix, or app_url_shared_dev_native_preview for Native Dev)`);
  }
  return { url, source: declared ? (derived ? 'declared+derived' : 'declared') : (suffix ? 'preview_url_suffix' : 'cloudflare') };
}

/**
 * Build-env origin consistency where it is deterministic: variables that name THIS application's own callback / redirect /
 * origin / site / app URL must be same-origin with the target URL. (An IdP issuer is another service and cannot be verified
 * here; foreignUrlsInBuildEnv rejects it only when it is another target's URL of this application.)
 */
const SELF_URL_KEY = /(CALLBACK|REDIRECT_URI|REDIRECT_URL|APP_ORIGIN|APP_URL|SITE_URL|SELF_URL|PUBLIC_ORIGIN)/i;
export function checkBuildEnvOrigins(buildEnvText, targetUrl) {
  if (!targetUrl) return [];
  const origin = new URL(targetUrl).origin;
  const bad = [];
  for (const line of String(buildEnvText ?? '').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const eq = t.indexOf('=');
    const key = t.slice(0, eq);
    const value = t.slice(eq + 1).trim();
    if (!SELF_URL_KEY.test(key) || !/^https?:\/\//i.test(value)) continue;
    let o = '';
    try { o = new URL(value).origin; } catch { bad.push(`${key} is not a valid URL`); continue; }
    if (o !== origin) bad.push(`${key}=${o} is not the origin of this target (${origin})`);
  }
  return bad;
}

// ---------------------------------------------------------------------------------------------------------------------
// Delivery descriptor: ONE versioned, validated record per run, produced by the Plan job and consumed by the delivery workflow and every
// engine stage. Pure data: never a secret, never a URL (URLs are resolved against Cloudflare before each build); validated at generation
// AND at every consumer (a trust boundary).
// ---------------------------------------------------------------------------------------------------------------------
export const DESCRIPTOR_VERSION = 2;
export const PHASES = ['all', 'deploy', 'verify'];
const DESCRIPTOR_KEYS = ['v', 'target', 'driver', 'slot', 'engine_target', 'environment', 'deploy_group', 'rollback_group', 'd1_expected_id', 'owns_migration', 'smoke_mode', 'phase'];

/**
 * Concurrency identity of the delivery. The stable Worker Dev keeps the shared-Dev group - keyed by repository and the shared-D1 scope (or
 * the app) - for Deploy AND Rollback, so it serialises with every other app using the same physical D1. Everything else gets an
 * uncontended per-run group (the run-level group already serialises shared state).
 */
export function concurrencyGroups(delivery, { runId, repo = '', app, lockScope = '' }) {
  const slot = slotOf(delivery);
  if (slot === 'persistent-dev') {
    const g = `cfv2-dev-${repo}-${lockScope || app}`;
    return { deploy_group: g, rollback_group: g };
  }
  return { deploy_group: `cfv2-leg-${runId}-${slot}`, rollback_group: `cfv2-leg-${runId}-${slot}-rollback` };
}

export function targetDescriptor({ delivery, app, runId, repo = '', lockScope = '', d1ExpectedId = '', smokeMode = 'full', phase = 'all' }) {
  assertDelivery(delivery);
  return validateDescriptor({
    v: DESCRIPTOR_VERSION, target: delivery.target, driver: delivery.driver, slot: slotOf(delivery), engine_target: engineTarget(delivery),
    environment: environmentName(app, delivery), ...concurrencyGroups(delivery, { runId, repo, app, lockScope }),
    d1_expected_id: d1ExpectedId, owns_migration: Boolean(d1ExpectedId), smoke_mode: smokeMode, phase,
  });
}

/** Strict schema check (throws; returns the descriptor). Unknown/missing keys, cross-target values and forged groups all reject. */
export function validateDescriptor(d) {
  if (d === null || typeof d !== 'object' || Array.isArray(d)) throw new Error('delivery descriptor must be an object');
  const keys = Object.keys(d);
  const extra = keys.filter((k) => !DESCRIPTOR_KEYS.includes(k));
  const missing = DESCRIPTOR_KEYS.filter((k) => !keys.includes(k));
  if (extra.length || missing.length) throw new Error(`delivery descriptor keys: unexpected [${extra}] missing [${missing}]`);
  if (d.v !== DESCRIPTOR_VERSION) throw new Error(`delivery descriptor version ${d.v} is not supported (expected ${DESCRIPTOR_VERSION})`);
  const delivery = assertDelivery({ target: d.target, driver: d.driver });
  if (d.slot !== slotOf(delivery) || d.engine_target !== engineTarget(delivery)) throw new Error(`delivery descriptor: ${d.target}/${d.driver} does not run as slot '${d.slot}' / engine target '${d.engine_target}'`);
  const app = String(d.environment).slice(0, String(d.environment).lastIndexOf('/'));
  if (!app || /\s/.test(app) || d.environment !== environmentName(app, delivery)) throw new Error(`delivery descriptor: environment '${d.environment}' does not belong to ${d.target}/${d.driver}`);
  if (typeof d.deploy_group !== 'string' || typeof d.rollback_group !== 'string') throw new Error('delivery descriptor: concurrency groups must be strings');
  const legit = d.slot === 'persistent-dev'
    ? /^cfv2-dev-\S+-\S+$/.test(d.deploy_group) && d.rollback_group === d.deploy_group
    : new RegExp(`^cfv2-leg-\\d+-${d.slot}$`).test(d.deploy_group) && d.rollback_group === `${d.deploy_group}-rollback`;
  if (!legit) throw new Error(`delivery descriptor: concurrency groups '${d.deploy_group}' / '${d.rollback_group}' do not belong to ${d.target}/${d.driver}`);
  if (typeof d.d1_expected_id !== 'string' || !/^[\w-]*$/.test(d.d1_expected_id)) throw new Error('delivery descriptor: d1_expected_id is not a database id');
  if (d.owns_migration !== Boolean(d.d1_expected_id)) throw new Error('delivery descriptor: owns_migration contradicts d1_expected_id');
  if (d.target === 'preview' && d.d1_expected_id) throw new Error('delivery descriptor: a Preview never migrates a D1');
  if (!SMOKE_MODES.includes(d.smoke_mode)) throw new Error(`delivery descriptor: unknown smoke mode '${d.smoke_mode}'`);
  if (!PHASES.includes(d.phase)) throw new Error(`delivery descriptor: unknown phase '${d.phase}'`);
  return d;
}

/** Descriptor of a run. `d1ExpectedId` = the planned physical D1 (empty without migrations or for a Preview). */
export function buildDescriptor({ delivery, app, runId, repo = '', lockScope = '', d1ExpectedId = '', smokeMode = 'full', smokeOverrides = '', phase = 'all' }) {
  return targetDescriptor({ delivery, app, runId, repo, lockScope, d1ExpectedId, smokeMode: resolveSmokeMode(delivery.target, smokeMode, smokeOverrides), phase });
}

/** Parse + validate a descriptor arriving from a workflow output / phase_state - a trust boundary. */
export function parseDescriptor(text) {
  let d;
  try { d = JSON.parse(String(text)); } catch (e) { throw new Error(`delivery descriptor is not valid JSON: ${e.message}`); }
  return validateDescriptor(d);
}

// ---------------------------------------------------------------------------------------------------------------------
// Migration gate (pure). Runs in the engine before any build or publish of the Delivery and is unit-tested here. (The pre-mutation
// validation lives in the single Migration job: its steps run in order, so a failed check stops the job before the migrate step.)
// ---------------------------------------------------------------------------------------------------------------------
/** Marker of the Migration job's read-only precheck proving that the planned D1 has NOTHING pending (see migrationPrecheck below). */
export const VERIFIED_NO_PENDING = 'verified_no_pending';

/** Strict parse of `wrangler d1 execute --json` output (a JSON array with one `{success, results}` entry per statement). Throws on anything else. */
export function parseD1Rows(text) {
  let j;
  try { j = JSON.parse(String(text)); } catch { throw new Error('the D1 query did not return JSON'); }
  if (!Array.isArray(j) || j.length !== 1 || j[0] === null || typeof j[0] !== 'object' || j[0].success !== true || !Array.isArray(j[0].results)) {
    throw new Error('the D1 query result is not a single successful statement result');
  }
  return j[0].results;
}

/**
 * Migration files of a flat migrations_dir, the way Wrangler discovers them (`${migrations_dir}/*.sql`: exact file name, regular files,
 * no dotfiles - any name, spaces and Unicode included). `entries` = [{ name, file }] from readdir (`file` = a regular file, not a symlink or
 * directory). An entry that ends in .sql (any case) but that this rule cannot place with certainty - a directory, a symlink, a dotfile, `.SQL` -
 * is returned in `ambiguous`: different Wrangler versions treat those differently, so the caller must NOT conclude "nothing pending"
 * (it runs the full path). Nothing is silently discarded; only names that cannot end in `.sql` are not migrations.
 */
export function classifyMigrationEntries(entries) {
  const files = []; const ambiguous = [];
  for (const { name, file } of entries) {
    if (!/\.sql$/i.test(name)) continue;
    if (file && name.endsWith('.sql') && !name.startsWith('.')) files.push(name); else ambiguous.push(name);
  }
  return { files: files.sort(), ambiguous: ambiguous.sort() };
}

/**
 * Read-only migration precheck decision (pure). `files` = the .sql names of the migrations directory, `tableExists` = the migrations table
 * exists on the remote D1, `applied` = its `name` column. A migration is pending when its file is not recorded as applied; a missing table
 * means everything is pending. This is the same rule `wrangler d1 migrations apply` uses (file name vs. recorded name). Throws on malformed rows
 * (fail closed - never read as "nothing pending").
 */
export function pendingMigrations({ files, tableExists, applied = [] }) {
  if (!Array.isArray(files) || !files.length) throw new Error('the migrations directory has no .sql files');
  if (!tableExists) return [...files].sort();
  const done = new Set();
  for (const r of applied) {
    if (r === null || typeof r !== 'object' || typeof r.name !== 'string' || !r.name) throw new Error('the migrations table holds a row without a name');
    done.add(r.name);
  }
  return files.filter((f) => !done.has(f)).sort();
}

/**
 * Publish stage: the result of THE Migration job the delivery depends on. Fails closed unless that job succeeded and reports EITHER an
 * APPLIED migration on exactly this delivery's D1, OR a read-only precheck that VERIFIED nothing was pending on that D1 for exactly this
 * commit (`precheck: verified_no_pending`, bound to the slot, the D1 id and `runSha`). A skipped, failed or stale-skipped Migration job never
 * carries either. A stale-skipped migration (`stale` true) is NEVER authorization: nothing was applied, and the branch may point at this
 * commit again by the time the Delivery runs (A -> B -> A). Returns the validated migration result, or {} when the delivery has no D1
 * migration (descriptor.d1_expected_id empty). A verified no-pending result is normalised to `migration: none, schema_changed: false, d1_scan: none`.
 */
export function resolveMigrationUsed({ descriptor, result = '', json = '', runSha = '' }) {
  const d = validateDescriptor(descriptor);
  if (!d.d1_expected_id) return {};
  const label = `${d.target}/${d.driver}`;
  if (result !== 'success') throw new Error(`the D1 migration ${label} depends on finished '${result || 'missing'}'; nothing is published`);
  let m;
  try { m = JSON.parse(json); } catch { throw new Error(`the migration result for ${label} is missing or not valid JSON; nothing is published`); }
  if (m === null || typeof m !== 'object' || Array.isArray(m)) throw new Error(`the migration result for ${label} is malformed; nothing is published`);
  if (m.stale === true || m.stale === 'true') throw new Error(`the migration for ${label} was skipped because its run became stale; it does not authorize a publish (nothing is published)`);
  const noPending = m.precheck === VERIFIED_NO_PENDING;
  if (m.migration !== 'applied' && !noPending) throw new Error(`the migration result for ${label} reports no applied migration and no verified-empty precheck; nothing is published`);
  if (m.kind !== d.slot) throw new Error(`the migration result belongs to another delivery than ${label}; refusing`);
  if (m.d1_id !== d.d1_expected_id) throw new Error(`the migration result is for another D1 than ${label}'s (${d.d1_expected_id}); refusing`);
  if (!noPending) return m;
  if (m.migration === 'applied') throw new Error(`the migration result for ${label} claims both an applied migration and a verified-empty precheck; refusing`);
  if (!runSha || m.precheck_sha !== runSha) throw new Error(`the verified-empty precheck for ${label} belongs to commit '${m.precheck_sha || 'unknown'}', not this run's ${runSha || 'unknown'}; nothing is published`);
  return { ...m, migration: 'none', schema_changed: 'false', d1_scan: 'none' };
}


// ---------------------------------------------------------------------------------------------------------------------
// Verification and recovery decisions (pure). The engine only gathers facts (levels, versions, the live version) and
// performs the effects; WHAT those facts mean lives here, unit-tested.
// ---------------------------------------------------------------------------------------------------------------------
/** Only a content-level smoke verifies a deployment; Access liveness or a skipped smoke never does. */
export const isContentLevel = (level) => level === 'content' || level === 'content-sha';

export function verificationOutcome(level) {
  if (isContentLevel(level)) return { verified: true, kind: 'content' };
  return { verified: false, kind: level === 'skipped' ? 'skipped' : 'liveness' };
}

/**
 * The ONE D1 compatibility rule for restoring older Worker code (Production rollback and non-Production recovery alike):
 * after an applied schema change the older code must be attested (d1_rollback_compat) AND the applied migrations provably
 * additive. D1 itself is never reverted. null = allowed.
 */
export function d1SchemaBlock({ d1Pending = false, d1Compat = '', d1Scan = '' }) {
  if (!d1Pending) return null;
  if (d1Compat !== 'expand-contract') return { code: 'd1_unattested', why: "This run applied D1 migrations and d1_rollback_compat is not 'expand-contract': compatibility of the previous Worker version with the new schema is unproven." };
  if (d1Scan !== 'additive') return { code: 'd1_not_additive', why: `This run applied D1 migrations that are not provably additive (scan: ${d1Scan || 'unknown'}); the previous Worker version may not work on the new schema.` };
  return null;
}

/**
 * Non-Production recovery (Native Branch / Native Dev / Persistent Dev): may the last known-good code be redeployed?
 * Fails closed on everything it cannot prove, using the migration result of THIS pipeline.
 *   d1Migrations false            -> allowed (no D1 involved)
 *   migration result missing/bad  -> blocked (state of D1 unknown)
 *   result for another D1         -> blocked
 *   schema_changed not true/false -> blocked; false -> allowed; true -> d1SchemaBlock
 * `migration` = the parsed hand-off of the Migration job (or null). null = allowed.
 */
export function nonProdRecoveryBlock({ d1Migrations = false, migration = null, expectedId = '', d1Compat = '' }) {
  if (!d1Migrations) return null;
  const verifiedEmpty = migration !== null && typeof migration === 'object' && migration.precheck === VERIFIED_NO_PENDING && migration.migration === 'none';
  if (migration === null || typeof migration !== 'object' || Array.isArray(migration) || (migration.migration !== 'applied' && !verifiedEmpty)) {
    return { code: 'migration_result_missing', why: 'D1 migrations are enabled but this pipeline handed over no valid applied-migration result: the D1 schema state is unknown, so older code cannot be proven compatible.' };
  }
  if (!expectedId || migration.d1_id !== expectedId) {
    return { code: 'd1_identity_mismatch', why: `The migration result is for D1 '${migration.d1_id || 'unknown'}', not this target's D1 '${expectedId || 'unknown'}'.` };
  }
  const changed = String(migration.schema_changed ?? '');
  if (changed !== 'true' && changed !== 'false') return { code: 'migration_result_invalid', why: `The migration result has no valid schema_changed value ('${changed || 'missing'}').` };
  return d1SchemaBlock({ d1Pending: changed === 'true', d1Compat, d1Scan: String(migration.d1_scan ?? '') });
}

/**
 * Production automatic rollback: why it must NOT happen (null = allowed so far). Checked in this order, fail closed.
 * `live` = the version Cloudflare reports as live; pass undefined to skip that last, observation-based check.
 * A Worker rollback never reverts D1: after an applied migration the previous version must be PROVEN to cope.
 */
export function prodRollbackBlock({ policy, previous = '', current = '', worker = '', d1Pending = false, d1Compat = '', d1Scan = '', live }) {
  if (policy !== 'automatic') return { code: 'policy', why: `production_rollback_policy is '${policy}'.` };
  if (!previous) return { code: 'no_previous_version', why: 'No previous Worker version id was recorded (first deployment, or the deployment list was unreadable); there is nothing safe to roll back to.' };
  if (!current || !worker) return { code: 'no_identity', why: 'The deployed version id or Worker name was not handed over.' };
  if (previous === current) return { code: 'same_version', why: 'The previous and the new version id are identical.' };
  const d1 = d1SchemaBlock({ d1Pending, d1Compat, d1Scan });
  if (d1) return d1;
  if (live !== undefined && live !== current) {
    return { code: 'live_version_changed', why: `The live version is '${live || 'unknown'}', not the version \`${current}\` this run deployed; refusing to replace someone else's deployment.` };
  }
  return null;
}

/**
 * Final verdict of the Rollback job. restored_verified only when the restored deployment was re-smoked at CONTENT level.
 * Production: facts = rolled back? post-rollback smoke? level. Preview/Dev: known-good found? recovered? re-verified? level.
 */
export function rollbackVerdict({ target, rbResult = '', rbVerify = '', rbLevel = '', goodSha = '', recovered = '', verified = '', smokeLevel = '' }) {
  let result;
  let level = '';
  if (target === 'prod') {
    if (rbResult === 'rolled_back') {
      if (rbVerify === 'success') { result = isContentLevel(rbLevel) ? 'restored_verified' : 'restored_unverified'; level = rbLevel; } else result = 'restored_unhealthy';
    } else result = rbResult === 'blocked' ? 'blocked' : 'failed';
  } else if (rbResult === 'blocked') result = 'blocked';
  else if (!goodSha) result = 'no_known_good';
  else if (recovered === 'success' && verified === 'success') { result = isContentLevel(smokeLevel) ? 'restored_verified' : 'restored_unverified'; level = smokeLevel; } else result = 'failed';
  return { result, level, ok: result === 'restored_verified' };
}

// ---------------------------------------------------------------------------------------------------------------------
// Run-level lock identity (the workflow's top-level `concurrency.group`). GitHub evaluates that expression BEFORE any job, so it can only
// use data available at scheduling time. This is its JavaScript twin (the workflow YAML is the tested twin of this rule).
//   main push                    -> cfv2-prod-<d1_lock_scope|app>
//   manual Dev promotion         -> cfv2-nonprod-d1-<scope> with d1_migrations, else cfv2-shared-dev-<app> (never the Production group)
//   same-repo PR with Preview    -> cfv2-<app>-pr-<number>
//   everything else              -> a private per-run group (never queues behind a deploy)
// For a verify call the YAML applies the same rule to the DEPLOY call's inputs (phase_state.inputs), never to a caller-supplied string.
// ---------------------------------------------------------------------------------------------------------------------
export function runLockGroup({ event, ref, inputs, runId, isFork = false, prNumber = '' }) {
  const s = (k) => (inputs?.[k] == null ? '' : String(inputs[k]));
  const on = (k) => inputs?.[k] === true || inputs?.[k] === 'true';
  const scope = s('d1_lock_scope') || s('app_name');
  if (event === 'workflow_dispatch' && s('dispatch_target') === 'dev') return on('d1_migrations') ? `cfv2-nonprod-d1-${scope}` : `cfv2-shared-dev-${s('app_name')}`;
  if (event === 'push' && ref === 'refs/heads/main') return `cfv2-prod-${scope}`;
  if (event === 'pull_request' && !isFork && s('preview_policy') === 'pr' && prNumber !== '') return `cfv2-${s('app_name')}-pr-${prNumber}`;
  return `cfv2-run-${runId}`;
}

// ---------------------------------------------------------------------------------------------------------------------
// Deferred verification (pipeline_phase=verify): the descriptor of the DEPLOY call is the source of truth. The verify call receives only
// phase_state; it must never re-derive deployment-time policy (smoke mode, lock scope, D1) from its own inputs. The only change allowed is
// the phase transition deploy -> verify.
// ---------------------------------------------------------------------------------------------------------------------
const plainObject = (o) => o !== null && typeof o === 'object' && !Array.isArray(o);

/** Descriptor for a verify call, from the validated phase_state of the deploy call. `repo` = this run's repository. */
export function restoreDescriptor(state, { repo, runId = '0', event = '', ref = '', isFork = false, prNumber = '', prAction = '' }) {
  if (!plainObject(state)) throw new Error('phase_state is not an object');
  if (!plainObject(state.inputs) || !state.inputs.app_name) throw new Error('phase_state.inputs.app_name is missing: cannot bind the hand-off to an application');
  const app = String(state.inputs.app_name);
  // The handoff's run-level group must be exactly what the deploy call's own inputs imply here (same rule as the workflow's top-level
  // concurrency expression). A `cfv2-run-<id>` fallback group carries the deploy run's id, which only has to be numeric.
  const expectedGroup = runLockGroup({ event, ref, inputs: state.inputs, runId, isFork, prNumber });
  if (expectedGroup.startsWith('cfv2-run-') ? !/^cfv2-run-\d+$/.test(String(state.group ?? '')) : state.group !== expectedGroup) {
    throw new Error(`phase_state.group '${state.group}' is not the lock identity the deploy inputs imply ('${expectedGroup}'); refusing to verify under another lock`);
  }
  const d = validateDescriptor(state.descriptor);
  // The descriptor must be exactly the delivery the deploy call's own inputs plan for THIS event (a swapped target / driver is rejected here).
  const planned = resolveDelivery({
    event, ref, isFork, prAction, previewPolicy: String(state.inputs.preview_policy ?? '') || 'none', devDriver: String(state.inputs.dev_driver ?? '') || 'none',
    dispatchTarget: String(state.inputs.dispatch_target ?? ''), approvedDevRef: String(state.inputs.approved_dev_ref ?? ''),
  });
  if (!planned || planned.target !== d.target || planned.driver !== d.driver) throw new Error(`descriptor ${d.target}/${d.driver} is not the delivery the deploy inputs plan for this event (${planned ? `${planned.target}/${planned.driver}` : 'none'})`);
  if (d.phase !== 'deploy') throw new Error(`descriptor is in phase '${d.phase}'; only a deploy-phase descriptor can be verified`);
  if (d.environment !== environmentName(app, d)) throw new Error(`descriptor belongs to another application (${d.environment}, expected ${environmentName(app, d)})`);
  if (d.slot === 'persistent-dev') { // the Dev lock is the shared-D1 group of THIS repository and the deploy call's own scope / app
    const want = concurrencyGroups(d, { runId, repo, app, lockScope: state.inputs.d1_lock_scope == null ? '' : String(state.inputs.d1_lock_scope) });
    if (d.deploy_group !== want.deploy_group || d.rollback_group !== want.rollback_group) {
      throw new Error(`descriptor carries lock '${d.deploy_group}' / '${d.rollback_group}', expected '${want.deploy_group}' for repository ${repo}, application ${app} and the deploy call's d1_lock_scope`);
    }
  }
  if (state.d1 != null && String(state.d1) !== d.d1_expected_id) throw new Error(`descriptor binds D1 '${d.d1_expected_id}' but the hand-off recorded '${state.d1}'`);
  return validateDescriptor({ ...d, phase: 'verify' });
}

// ---------------------------------------------------------------------------------------------------------------------
// Engine initialisation (pure): every check of `engine.sh init`, evaluated once over the validated inputs. Throws (fail closed, BEFORE any
// mutation) with the message the operator sees. Credentials stay a shell check.
// ---------------------------------------------------------------------------------------------------------------------
const STAGES = ['build', 'precheck', 'migrate', 'publish', 'verify', 'rollback'];

export function validateInit({ inputs, target, kind = '', stage = 'all', d1Expect = '', descriptor = '', callInputs = '', event, ref, isFork = false, prAction = '', previewSecrets = '' }) {
  if (inputs === null || typeof inputs !== 'object' || Array.isArray(inputs)) throw new Error('inputs_json is not an object');
  const s = (k) => (inputs[k] == null ? '' : String(inputs[k]));
  if (!descriptor) throw new Error('a delivery descriptor is required');
  const d = parseDescriptor(descriptor);
  const delivery = { target: d.target, driver: d.driver };
  if (!['prod', 'preview', 'dev'].includes(target) || d.engine_target !== target) throw new Error(`delivery descriptor engine target '${d.engine_target}' differs from the stage's '${target}'`);
  if (kind && d.slot !== kind) throw new Error(`delivery descriptor slot '${d.slot}' differs from the stage's '${kind}'`);
  if (d.d1_expected_id !== d1Expect) throw new Error("delivery descriptor D1 id differs from the stage's expected D1");
  if (!STAGES.includes(stage)) throw new Error(`bad engine stage: ${stage}`);
  if (d.target === 'preview' && ['precheck', 'migrate'].includes(stage)) throw new Error('a Preview never migrates a remote D1');
  if (!/^[A-Za-z0-9_-]*$/.test(d1Expect)) throw new Error('malformed expected D1 id');
  if (callInputs) { // deferred verification: the verify call's identity inputs must equal the deploy call's
    let c;
    try { c = JSON.parse(callInputs); } catch { c = null; }
    if (c === null || typeof c !== 'object' || Array.isArray(c)) throw new Error('call inputs are not an object');
    for (const k of ['app_name', 'app_path', 'production_rollback_policy']) {
      if ((c[k] == null ? '' : String(c[k])) !== s(k)) throw new Error(`verify call input '${k}' differs from the deploy call that produced the hand-off; refusing to verify another deployment`);
    }
  }
  if (!['automatic', 'manual', 'disabled'].includes(s('production_rollback_policy'))) throw new Error('bad production_rollback_policy');
  // The descriptor must be what THIS event and these inputs plan: no phantom delivery. (A deferred verify call has its own event: its
  // descriptor was already tied to the deploy call's inputs by restoreDescriptor.)
  if (d.phase !== 'verify') {
    const planned = resolveDelivery({
      event, ref, isFork, prAction, previewPolicy: s('preview_policy') || 'none', devDriver: s('dev_driver') || 'none',
      dispatchTarget: s('dispatch_target'), approvedDevRef: s('approved_dev_ref'),
    });
    if (!planned || planned.target !== d.target || planned.driver !== d.driver) throw new Error(`delivery mismatch (this event plans '${planned ? `${planned.target}/${planned.driver}` : 'nothing'}', engine '${d.target}/${d.driver}'); refusing to deploy`);
  }
  return { slot: d.slot, delivery, stage, target, smokeMode: d.smoke_mode, d1Expect, ...validateTargetConfig({ inputs, delivery, previewSecrets }) };
}

/** Per-target requirements and the validated KEY=VALUE env data each target builds/smokes with (a build env never falls back to another target's). */
function validateTargetConfig({ inputs, delivery, previewSecrets }) {
  const s = (k) => (inputs[k] == null ? '' : String(inputs[k]));
  const on = (k) => s(k) === 'true';
  const { target, driver } = delivery;
  if (target === 'dev' && driver === 'worker') {
    if (!s('dev_worker_name')) throw new Error('A Dev Worker requires dev_worker_name (the Dev Worker identity is always verified)');
    if (!s('app_url_dev')) throw new Error('A Dev Worker requires app_url_dev (a Dev deployment without a probe is not verified)');
  }
  const buildCmd = target === 'dev' ? (s('dev_build_cmd') || s('build_cmd')) : s('build_cmd');
  if (!buildCmd) throw new Error(`no build command for target ${target}`);
  if (on('d1_migrations') && target === 'dev' && !s('dev_build_cmd')) throw new Error('Dev with d1_migrations requires dev_build_cmd: the Dev build must succeed BEFORE the Dev D1 is migrated');
  const buildEnvText = s({ production: 'build_env_prod', preview: 'build_env_preview', dev: 'build_env_dev' }[target]);
  const bad = foreignUrlsInBuildEnv(target, buildEnvText, { production: s('app_url_prod'), dev: s('app_url_dev') });
  if (bad.length) throw new Error(`build environment of ${target}: ${bad.join('; ')}; each target must be built with its own URLs`);
  const parse = (name, text) => {
    const r = parseEnvLines(text, { reserved: true });
    if (r.errors.length) throw new Error(`${name}: ${r.errors.join('; ')}`);
    return r.pairs;
  };
  return {
    buildEnv: parse('CFV2_BUILD_ENV', buildEnvText), smokeEnv: parse('CFV2_SMOKE_ENV', s('smoke_env')),
    previewEnv: target !== 'production' && driver === 'native-preview' && previewSecrets ? parse('CFV2_PS', previewSecrets) : null,
  };
}

/** Historical persistence slots (internal; see the header). */
export const SLOTS = Object.values(SLOT);
export const assertSlot = (slot) => {
  if (!SLOTS.includes(slot)) throw new Error(`Unknown delivery slot '${slot}' (expected ${SLOTS.join(' | ')})`);
  return slot;
};
