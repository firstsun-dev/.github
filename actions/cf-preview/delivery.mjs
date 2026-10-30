// Pure decision logic of the target-driven delivery architecture (OpenSpec unify-cf-worker-v2-delivery-targets).
// One normalised vocabulary for the four delivery kinds; no I/O. Imported by cli.mjs, unit-tested in lib.test.mjs.
import { classifyEvent, migrationPlan, previewIdentity, STRATEGIES } from './lib.mjs';

export const DELIVERY_KINDS = ['production', 'native-branch', 'native-dev', 'persistent-dev'];
export const SMOKE_MODES = ['full', 'basic', 'skip'];
/** Fixed name of the stable Native Dev Preview. Reserved: no branch Preview may ever take it. */
export const NATIVE_DEV_NAME = 'dev';
export const RESERVED_PREVIEW_NAMES = [NATIVE_DEV_NAME];

/** Engine "target class" (config source / credentials / wrangler command family) of each kind. */
export const ENGINE_TARGET = { production: 'prod', 'native-branch': 'preview', 'native-dev': 'preview', 'persistent-dev': 'dev' };
/** Back-compat: the kind an engine target implies when the caller passes none. */
export const DEFAULT_KIND = { prod: 'production', preview: 'native-branch', dev: 'persistent-dev' };

export function assertKind(kind) {
  if (!DELIVERY_KINDS.includes(kind)) throw new Error(`Unknown delivery kind '${kind}' (expected ${DELIVERY_KINDS.join(' | ')})`);
  return kind;
}

/** smoke_mode_overrides: JSON object keyed by delivery kind; values full|basic|skip. Strict: unknown key/value rejects. */
export function parseSmokeOverrides(text) {
  const s = String(text ?? '').trim();
  if (!s) return {};
  let o;
  try { o = JSON.parse(s); } catch (e) { throw new Error(`smoke_mode_overrides is not valid JSON: ${e.message}`); }
  if (o === null || typeof o !== 'object' || Array.isArray(o)) throw new Error('smoke_mode_overrides must be a JSON object keyed by delivery kind');
  for (const [k, v] of Object.entries(o)) {
    if (!DELIVERY_KINDS.includes(k)) throw new Error(`smoke_mode_overrides: unknown delivery kind '${k}' (expected ${DELIVERY_KINDS.join(' | ')})`);
    if (!SMOKE_MODES.includes(v)) throw new Error(`smoke_mode_overrides.${k}: unknown smoke mode '${v}' (expected ${SMOKE_MODES.join(' | ')})`);
  }
  return o;
}

export function resolveSmokeMode(kind, smokeMode = 'full', overridesText = '') {
  assertKind(kind);
  const base = smokeMode === '' || smokeMode == null ? 'full' : smokeMode;
  if (!SMOKE_MODES.includes(base)) throw new Error(`Unknown smoke_mode '${base}' (expected ${SMOKE_MODES.join(' | ')})`);
  return parseSmokeOverrides(overridesText)[kind] ?? base;
}

/**
 * Enabled delivery kinds for one event, in a stable order. Exactly the enabled targets - a disabled channel creates no
 * phantom path. `persistentDevDispatch` = the protected manual dispatch (a separate, non-automatic path).
 * Rejects (throws) the combinations the spec forbids instead of silently dropping them.
 */
export function resolveDeliveryTargets({
  event, ref, strategy, previewOnPush = true, sharedDevOnPush = false, sharedDevNativeOnPush = false, dispatchTarget = '', devRef = '',
}) {
  if (!STRATEGIES.includes(strategy)) throw new Error(`Unknown deployment_strategy: ${strategy}`);
  if (strategy === 'production-only' && (sharedDevOnPush || sharedDevNativeOnPush)) {
    throw new Error('deployment_strategy: production-only cannot be combined with shared_dev_on_branch_push / shared_dev_native_preview_on_branch_push (a production-only Worker must not expose a Dev deployment)');
  }
  if (sharedDevOnPush && sharedDevNativeOnPush) {
    throw new Error('shared_dev_on_branch_push (separate persistent Dev Worker) and shared_dev_native_preview_on_branch_push (named Native Preview "dev") are conflicting automatic Dev channels; enable only one');
  }
  if (!String(ref).startsWith('refs/heads/')) return [];
  const r = classifyEvent({ event, ref, strategy, devRef, dispatchTarget });
  if (r.action === 'production') return ['production'];
  if (r.action === 'persistent-dev') return ['persistent-dev'];
  const automatic = event === 'push' || (event === 'workflow_dispatch' && dispatchTarget !== 'persistent-dev');
  if (r.action !== 'preview' || !automatic) return [];
  const kinds = [];
  if (previewOnPush) kinds.push('native-branch');
  if (sharedDevNativeOnPush) kinds.push('native-dev');
  if (sharedDevOnPush) kinds.push('persistent-dev');
  return kinds;
}

/** Native branch Preview identity that can never take a reserved name (the stable Native Dev Preview owns `dev`). */
export function branchPreviewIdentity(args, { reserveNativeDev = false } = {}) {
  const id = previewIdentity(args);
  if (reserveNativeDev && RESERVED_PREVIEW_NAMES.includes(id.name)) return { ...id, name: `b-${id.name}` };
  return id;
}

/**
 * Migration plan across the enabled targets: ONE execution per distinct physical D1 (`account/database_id`).
 * `cfg` is the SOURCE Wrangler config; `kinds` the enabled kinds. Production D1 must never be reachable from a
 * non-production kind (migrationPlan already refuses a D1 declared on both sides; this re-checks the final identities).
 * Owner = the first member in OWNER_ORDER; the others wait for the owner's result.
 */
const OWNER_ORDER = ['native-branch', 'native-dev', 'persistent-dev', 'production'];
export function planMigrations(cfg, kinds, { accountId = '', binding = '', devEnv = 'dev', migrationsDirAbs = (d) => d } = {}) {
  const groups = new Map();
  for (const kind of kinds) {
    assertKind(kind);
    const p = migrationPlan(cfg, ENGINE_TARGET[kind], { binding, devEnv, migrationsDirAbs });
    const id = `${accountId}/${p.databaseId}`;
    if (!groups.has(id)) groups.set(id, { id, databaseId: p.databaseId, databaseName: p.databaseName, binding: p.binding, members: [] });
    groups.get(id).members.push(kind);
  }
  const out = [...groups.values()].map((g) => {
    g.members.sort((a, b) => OWNER_ORDER.indexOf(a) - OWNER_ORDER.indexOf(b));
    return { ...g, owner: g.members[0] };
  });
  const prod = out.find((g) => g.members.includes('production'));
  if (prod && prod.members.length > 1) {
    throw new Error(`Production D1 ${prod.databaseName} is also mapped to ${prod.members.filter((m) => m !== 'production').join(', ')}; Preview/Dev must never migrate or bind Production D1`);
  }
  return out;
}

/** GitHub Environment of each kind (protection rules / deployment history). Production and Preview names are unchanged. */
export const ENVIRONMENT_SUFFIX = { production: 'prod', 'native-branch': 'preview', 'native-dev': 'native-dev', 'persistent-dev': 'dev' };
export const environmentName = (app, kind) => `${app}/${ENVIRONMENT_SUFFIX[assertKind(kind)]}`;

/** Ledger environment (GitHub Deployments) used for known-good lookup. Native Dev has ONE fixed ledger per app. */
export function ledgerEnv(app, kind, previewName = '') {
  switch (assertKind(kind)) {
    case 'native-branch': return `${app}/preview/${previewName}`;
    case 'native-dev': return `${app}/native-dev/${NATIVE_DEV_NAME}`;
    case 'persistent-dev': return `${app}/dev/shared`;
    default: return '';
  }
}

/**
 * Build-environment URL isolation: the build env of a target must not carry another target's public URL.
 * `urls` = { kind: url } of every known target URL; `env` = the parsed KEY=VALUE text of THIS kind's build env.
 * Returns the offending lines (empty = isolated).
 */
export function foreignUrlsInBuildEnv(kind, buildEnvText, urls) {
  const origin = (u) => { try { return new URL(u).origin; } catch { return ''; } };
  const foreign = Object.entries(urls).filter(([k, u]) => k !== kind && u && origin(u)).map(([k, u]) => [k, origin(u)]);
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
// Target descriptor: ONE versioned, validated record per enabled kind, produced by the Delivery plan job and consumed by the
// thin per-kind workflow calls (and by every engine stage). It replaces the per-field `with:` copies (engine target, GitHub
// Environment, concurrency groups, expected D1, migration owner, smoke policy). Pure data: never a secret, never a URL
// (URLs are resolved against Cloudflare before each build), validated at generation AND at every consumer.
// ---------------------------------------------------------------------------------------------------------------------
export const DESCRIPTOR_VERSION = 1;
export const PHASES = ['all', 'deploy', 'verify'];
const DESCRIPTOR_KEYS = ['v', 'kind', 'target', 'environment', 'deploy_group', 'rollback_group', 'd1_expected_id', 'd1_owner_kind', 'owns_migration', 'smoke_mode', 'phase'];

/**
 * Concurrency identity of a kind. Production / Native Branch / Native Dev get uncontended per-run groups (no cross-target
 * waiting). persistent-dev keeps the LEGACY Shared Dev group - keyed by repository and the shared-D1 scope (or the app) - for
 * Deploy AND Rollback, so it serialises with the manual dispatch and with every other app using the same physical D1.
 */
export function concurrencyGroups(kind, { runId, repo = '', app, lockScope = '' }) {
  if (assertKind(kind) === 'persistent-dev') {
    const g = `cfv2-dev-${repo}-${lockScope || app}`;
    return { deploy_group: g, rollback_group: g };
  }
  return { deploy_group: `cfv2-leg-${runId}-${kind}`, rollback_group: `cfv2-leg-${runId}-${kind}-rollback` };
}

export function targetDescriptor({ kind, app, runId, repo = '', lockScope = '', d1ExpectedId = '', ownerKind = '', smokeMode = 'full', phase = 'all' }) {
  assertKind(kind);
  return validateDescriptor({
    v: DESCRIPTOR_VERSION, kind, target: ENGINE_TARGET[kind], environment: environmentName(app, kind),
    ...concurrencyGroups(kind, { runId, repo, app, lockScope }),
    d1_expected_id: d1ExpectedId, d1_owner_kind: ownerKind, owns_migration: Boolean(d1ExpectedId && ownerKind === kind),
    smoke_mode: smokeMode, phase,
  });
}

/** Strict schema check (throws; returns the descriptor). Unknown/missing keys, cross-kind values and forged groups all reject. */
export function validateDescriptor(d) {
  if (d === null || typeof d !== 'object' || Array.isArray(d)) throw new Error('target descriptor must be an object');
  const keys = Object.keys(d);
  const extra = keys.filter((k) => !DESCRIPTOR_KEYS.includes(k));
  const missing = DESCRIPTOR_KEYS.filter((k) => !keys.includes(k));
  if (extra.length || missing.length) throw new Error(`target descriptor keys: unexpected [${extra}] missing [${missing}]`);
  if (d.v !== DESCRIPTOR_VERSION) throw new Error(`target descriptor version ${d.v} is not supported (expected ${DESCRIPTOR_VERSION})`);
  assertKind(d.kind);
  if (d.target !== ENGINE_TARGET[d.kind]) throw new Error(`target descriptor: kind ${d.kind} runs on engine target ${ENGINE_TARGET[d.kind]}, not ${d.target}`);
  const app = String(d.environment).slice(0, String(d.environment).lastIndexOf('/'));
  if (!app || /\s/.test(app) || d.environment !== environmentName(app, d.kind)) throw new Error(`target descriptor: environment '${d.environment}' does not belong to kind ${d.kind}`);
  if (typeof d.deploy_group !== 'string' || typeof d.rollback_group !== 'string') throw new Error('target descriptor: concurrency groups must be strings');
  const legit = d.kind === 'persistent-dev'
    ? /^cfv2-dev-\S+-\S+$/.test(d.deploy_group) && d.rollback_group === d.deploy_group
    : new RegExp(`^cfv2-leg-\\d+-${d.kind}$`).test(d.deploy_group) && d.rollback_group === `${d.deploy_group}-rollback`;
  if (!legit) throw new Error(`target descriptor: concurrency groups '${d.deploy_group}' / '${d.rollback_group}' do not belong to kind ${d.kind}`);
  if (typeof d.d1_expected_id !== 'string' || !/^[\w-]*$/.test(d.d1_expected_id)) throw new Error('target descriptor: d1_expected_id is not a database id');
  if (d.d1_owner_kind !== '') assertKind(d.d1_owner_kind);
  if (d.owns_migration !== Boolean(d.d1_expected_id && d.d1_owner_kind === d.kind)) throw new Error('target descriptor: owns_migration contradicts the migration owner');
  if (!SMOKE_MODES.includes(d.smoke_mode)) throw new Error(`target descriptor: unknown smoke mode '${d.smoke_mode}'`);
  if (!PHASES.includes(d.phase)) throw new Error(`target descriptor: unknown phase '${d.phase}'`);
  return d;
}

/**
 * All descriptors of a run, keyed by kind (enabled kinds only, stable order). `groups` = planMigrations() result of the run
 * (owner/members per physical D1); `d1ByKind` = { kind: id } override for the deferred verify phase (comes from phase_state).
 */
export function buildDescriptors({ kinds, app, runId, repo = '', lockScope = '', groups = [], d1ByKind = null, smokeMode = 'full', smokeOverrides = '', phase = 'all' }) {
  const out = {};
  for (const kind of DELIVERY_KINDS.filter((k) => kinds.includes(k))) {
    const g = groups.find((m) => m.members.includes(kind));
    out[kind] = targetDescriptor({
      kind, app, runId, repo, lockScope, d1ExpectedId: d1ByKind ? String(d1ByKind[kind] ?? '') : (g ? g.databaseId : ''), ownerKind: g ? g.owner : '',
      smokeMode: resolveSmokeMode(kind, smokeMode, smokeOverrides), phase,
    });
  }
  return out;
}

/** Parse + validate a descriptor (or a map of them) arriving from a workflow output / phase_state - a trust boundary. */
export function parseDescriptor(text, expectKind = '') {
  let d;
  try { d = JSON.parse(String(text)); } catch (e) { throw new Error(`target descriptor is not valid JSON: ${e.message}`); }
  validateDescriptor(d);
  if (expectKind && d.kind !== expectKind) throw new Error(`target descriptor is for ${d.kind}, not ${expectKind}`);
  return d;
}

// ---------------------------------------------------------------------------------------------------------------------
// Migration gates (pure). Both used to be inline bash/jq in the delivery workflow; they now run in the engine, before any
// build or publish, and are unit-tested here.
// ---------------------------------------------------------------------------------------------------------------------
/**
 * Migrate stage: NO D1 may be migrated while any enabled kind is unvalidated. `peers` = { kind: result of that kind's
 * preflight }. Anything but `success` - including a missing entry - blocks.
 */
export function checkPeerPreflights(kinds, peers) {
  const failures = [];
  for (const k of kinds) {
    assertKind(k);
    const r = peers && typeof peers === 'object' ? (peers[k] ?? 'missing') : 'missing';
    if (r !== 'success') failures.push(`preflight of ${k} finished '${r}'; no D1 is migrated while an enabled target is unvalidated`);
  }
  return failures;
}

/**
 * Publish stage: the migration result THIS kind's deployment depends on - its own, or the shared-D1 owner's (native-branch is
 * the only owner a sibling can delegate to). Fails closed unless that migration succeeded and reports an applied migration of
 * the planned owner on exactly this kind's D1 (a stale-skipped migration is accepted: nothing was applied, nothing is published).
 * Returns the (validated, normalised) migration result object, or {} when the app has no D1 migrations.
 */
export function resolveMigrationUsed({ d1Migrations, kind, ownerKind = '', expectedId = '', ownResult = '', ownJson = '', ownerResult = '', ownerJson = '' }) {
  if (!d1Migrations) return {};
  assertKind(kind);
  let result;
  let json;
  if (ownerKind === kind) { result = ownResult; json = ownJson; } else if (ownerKind === 'native-branch') { result = ownerResult; json = ownerJson; } else {
    throw new Error(`no migration owner is planned for ${kind} (owner '${ownerKind || 'none'}'); nothing is published`);
  }
  if (result !== 'success') throw new Error(`the D1 migration ${kind} depends on (owner ${ownerKind}) finished '${result || 'missing'}'; nothing is published`);
  let m;
  try { m = JSON.parse(json); } catch { m = {}; }
  if (m === null || typeof m !== 'object' || Array.isArray(m)) m = {};
  if (m.stale !== true && m.stale !== 'true') {
    if (m.migration !== 'applied') throw new Error(`the migration result for ${kind} reports no applied migration; nothing is published`);
    if (m.kind !== ownerKind) throw new Error(`the migration result belongs to another kind than the planned owner ${ownerKind}; refusing`);
    if (m.d1_id !== expectedId) throw new Error(`the migration result is for another D1 than ${kind}'s (${expectedId}); refusing`);
  }
  return m;
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
 * Production automatic rollback: why it must NOT happen (null = allowed so far). Checked in this order, fail closed.
 * `live` = the version Cloudflare reports as live; pass undefined to skip that last, observation-based check.
 * A Worker rollback never reverts D1: after an applied migration the previous version must be PROVEN to cope.
 */
export function prodRollbackBlock({ policy, previous = '', current = '', worker = '', d1Pending = false, d1Compat = '', d1Scan = '', live }) {
  if (policy !== 'automatic') return { code: 'policy', why: `production_rollback_policy is '${policy}'.` };
  if (!previous) return { code: 'no_previous_version', why: 'No previous Worker version id was recorded (first deployment, or the deployment list was unreadable); there is nothing safe to roll back to.' };
  if (!current || !worker) return { code: 'no_identity', why: 'The deployed version id or Worker name was not handed over.' };
  if (previous === current) return { code: 'same_version', why: 'The previous and the new version id are identical.' };
  if (d1Pending) {
    if (d1Compat !== 'expand-contract') return { code: 'd1_unattested', why: "This run applied D1 migrations and d1_rollback_compat is not 'expand-contract': compatibility of the previous Worker version with the new schema is unproven." };
    if (d1Scan !== 'additive') return { code: 'd1_not_additive', why: `This run applied D1 migrations that are not provably additive (scan: ${d1Scan || 'unknown'}); the previous Worker version may not work on the new schema.` };
  }
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
  } else if (!goodSha) result = 'no_known_good';
  else if (recovered === 'success' && verified === 'success') { result = isContentLevel(smokeLevel) ? 'restored_verified' : 'restored_unverified'; level = smokeLevel; } else result = 'failed';
  return { result, level, ok: result === 'restored_verified' };
}

// ---------------------------------------------------------------------------------------------------------------------
// Deferred verification (pipeline_phase=verify): the descriptors of the DEPLOY call are the source of truth. The verify call
// receives only phase_state; it must never re-derive deployment-time policy (smoke mode, lock scope, owner, D1) from its own
// inputs. The only change allowed is the phase transition deploy -> verify.
// ---------------------------------------------------------------------------------------------------------------------
const keyOf = (k) => k.replace(/-/g, '_');
const plainObject = (o) => o !== null && typeof o === 'object' && !Array.isArray(o);

/**
 * Descriptors for a verify call, from the validated phase_state of the deploy call. `repo` = this run's repository.
 * - With `state.descriptors`: parsed, validated and cross-checked against the rest of the hand-off (kinds, app, repository,
 *   D1 ids, migration owner); phase must be `deploy`; the result is identical except phase = `verify`.
 * - Legacy hand-off (no `descriptors`): reconstructed ONLY from the deploy call's own `state.inputs` (never the verify call's);
 *   anything ambiguous or missing fails closed.
 */
export function restoreDescriptors(state, { repo, runId = '0' }) {
  if (!plainObject(state)) throw new Error('phase_state is not an object');
  if (!Array.isArray(state.kinds) || !state.kinds.length) throw new Error('phase_state names no delivery kind');
  const kinds = DELIVERY_KINDS.filter((k) => state.kinds.includes(k));
  if (kinds.length !== state.kinds.length || new Set(state.kinds).size !== state.kinds.length) throw new Error('phase_state.kinds contains an unknown or duplicate delivery kind');
  if (!plainObject(state.inputs) || !state.inputs.app_name) throw new Error('phase_state.inputs.app_name is missing: cannot bind the hand-off to an application');
  const app = String(state.inputs.app_name);
  const d1Of = (k) => (plainObject(state.d1) && state.d1[keyOf(k)] != null ? String(state.d1[keyOf(k)]) : null);
  const ownerOf = (k) => {
    const raw = plainObject(state.migration) ? state.migration[`mj_${keyOf(k)}`] : null;
    if (!raw) return null;
    const m = typeof raw === 'string' ? (() => { try { return JSON.parse(raw); } catch { return null; } })() : raw;
    return plainObject(m) && m.kind ? String(m.kind) : null;
  };

  if (state.descriptors === undefined) {
    const s = state.inputs;
    return buildDescriptors({
      kinds, app, runId, repo, lockScope: s.d1_lock_scope == null ? '' : String(s.d1_lock_scope),
      d1ByKind: Object.fromEntries(kinds.map((k) => [k, d1Of(k) ?? ''])),
      smokeMode: s.smoke_mode ? String(s.smoke_mode) : 'full', smokeOverrides: s.smoke_mode_overrides == null ? '' : String(s.smoke_mode_overrides), phase: 'verify',
    });
  }

  if (!plainObject(state.descriptors)) throw new Error('phase_state.descriptors is not an object');
  const have = Object.keys(state.descriptors).sort().join(',');
  if (have !== [...kinds].sort().join(',')) throw new Error(`phase_state.descriptors [${have}] do not match phase_state.kinds [${kinds}]`);
  const out = {};
  for (const k of kinds) {
    const d = validateDescriptor(state.descriptors[k]);
    if (d.kind !== k) throw new Error(`descriptor filed under ${k} is for ${d.kind}`);
    if (d.phase !== 'deploy') throw new Error(`descriptor for ${k} is in phase '${d.phase}'; only a deploy-phase descriptor can be verified`);
    if (d.environment !== environmentName(app, k)) throw new Error(`descriptor for ${k} belongs to another application (${d.environment}, expected ${environmentName(app, k)})`);
    if (k === 'persistent-dev' && !d.deploy_group.startsWith(`cfv2-dev-${repo}-`)) throw new Error(`descriptor for persistent-dev belongs to another repository (${d.deploy_group})`);
    const d1 = d1Of(k);
    if (d1 !== null && d1 !== d.d1_expected_id) throw new Error(`descriptor for ${k} binds D1 '${d.d1_expected_id}' but the hand-off recorded '${d1}'`);
    const owner = ownerOf(k);
    if (owner !== null && owner !== d.d1_owner_kind) throw new Error(`descriptor for ${k} names migration owner '${d.d1_owner_kind}' but the migration result belongs to '${owner}'`);
    out[k] = validateDescriptor({ ...d, phase: 'verify' });
  }
  return out;
}
