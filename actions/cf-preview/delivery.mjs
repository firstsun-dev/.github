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
