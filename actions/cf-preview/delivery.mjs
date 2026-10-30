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
