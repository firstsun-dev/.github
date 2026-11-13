// Build artifact contract (OpenSpec simplify-cf-ci-stages-and-dev-delivery, G1 review P0): the UNPRIVILEGED Build job produces a self-contained,
// pre-bundled artifact; the privileged Migrate / Publish jobs only VERIFY and PUBLISH it. They never install the application's dependencies,
// never run its build, and never execute anything from the artifact on the runner: it is inert data (a rewritten Wrangler config, a Worker
// bundle, static assets, a manifest) that Cloudflare - not the runner - will run, bounded by the audited bindings.
//
//   artifact/
//     manifest.json   identity record (see MANIFEST_KEYS)
//     wrangler.json   the audited config with `main` -> worker/<bundle>, `assets.directory` -> assets/<n>, no_bundle: true, `build` removed
//     worker/         `wrangler deploy --dry-run --outdir` output (bundle, extra modules)
//     assets/<n>/     copies of the static asset directories
//
// What is trusted: only the expectations the PRIVILEGED job derives itself (Plan descriptor, GitHub context, caller inputs, event payload).
// The manifest is evidence the Build job claims - it is cross-checked against those expectations, never used to select a target, worker name or
// credential. The digest detects corruption / mix-ups in transit; it is NOT provenance against a malicious Build job (which could rewrite both).
// The defence against a malicious Build is therefore (a) no credentials there, (b) the privileged job re-audits the packaged config against the
// caller's trusted allow / deny lists and structural rules below, and (c) the worker name / target are fixed by the privileged side.
import { createHash } from 'node:crypto';
import { cpSync, existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, realpathSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { basename, dirname, join, relative, resolve, sep } from 'node:path';

export const MANIFEST_VERSION = 1;
export const MANIFEST_KEYS = [
  'v', 'repository', 'app', 'target', 'driver', 'slot', 'run_id', 'run_attempt', 'source_sha', 'tested_sha', 'artifact_sha', 'target_url',
  'config_mode', 'worker_name', 'files_digest',
];
const SHA_RE = /^[0-9a-f]{40}$/;
/** Config keys with local side effects or that select behaviour of the publisher host: never allowed in a packaged config (root or any environment). */
export const FORBIDDEN_KEYS = ['build', 'site', 'containers', 'alias', 'tsconfig', 'find_additional_modules', 'base_dir', 'dev'];
const ALLOWED_TOP = (f) => f === 'manifest.json' || f === 'wrangler.json' || /^worker\//.test(f) || /^assets\//.test(f);
const SAFE_REL = /^[A-Za-z0-9._@+-]+(\/[A-Za-z0-9._@+-]+)*$/;

/** Every regular file below dir (sorted relative POSIX paths). Symlinks, sockets and other special files are refused: an artifact is plain files only. */
export function listFiles(dir) {
  const out = [];
  const walk = (d) => {
    for (const e of readdirSync(d, { withFileTypes: true })) {
      const p = join(d, e.name);
      const st = lstatSync(p);
      if (st.isSymbolicLink()) throw new Error(`artifact contains a symbolic link: ${relative(dir, p)}`);
      if (st.isDirectory()) walk(p);
      else if (st.isFile()) out.push(relative(dir, p).split(sep).join('/'));
      else throw new Error(`artifact contains a special file: ${relative(dir, p)}`);
    }
  };
  walk(dir);
  return out.sort();
}

/** sha256 over the sorted (path, file sha256) list of every file except manifest.json. */
export function filesDigest(dir) {
  const h = createHash('sha256');
  for (const f of listFiles(dir)) {
    if (f === 'manifest.json') continue;
    h.update(`${f}\0${createHash('sha256').update(readFileSync(join(dir, f))).digest('hex')}\n`);
  }
  return h.digest('hex');
}

const clone = (o) => JSON.parse(JSON.stringify(o));
/** Config sections that can carry `main` / `assets` / `build`: the root and every named environment. */
const sections = (cfg) => [cfg, ...Object.values(cfg.env ?? {}).filter((e) => e && typeof e === 'object')];

/**
 * Rewrite a (source or adapter-generated) Wrangler config so it publishes pre-bundled files only. `configDir` = directory the original paths
 * are relative to. Returns { config, assetDirs } where assetDirs = [{ from: absolute dir, to: 'assets/<n>' }]. Throws on a path that escapes
 * the config's workspace or a missing asset directory. The `build` command (arbitrary shell) and `site` are dropped.
 */
export function rewriteConfig(cfg, { configDir, bundleEntry, workspace }) {
  const c = clone(cfg);
  const assetDirs = [];
  const inside = (abs, what) => {
    const rel = relative(workspace, abs);
    if (rel.startsWith('..') || resolve(abs) === resolve('/')) throw new Error(`${what} '${abs}' is outside the application workspace`);
  };
  for (const s of sections(c)) {
    delete s.build;
    delete s.site;
    if (typeof s.main === 'string') s.main = `worker/${bundleEntry}`;
    if (s.assets && typeof s.assets === 'object' && typeof s.assets.directory === 'string') {
      const abs = resolve(configDir, s.assets.directory);
      inside(abs, 'assets.directory');
      let i = assetDirs.findIndex((a) => a.from === abs);
      if (i < 0) { assetDirs.push({ from: abs, to: `assets/${assetDirs.length}` }); i = assetDirs.length - 1; }
      s.assets.directory = `./${assetDirs[i].to}`;
    }
  }
  c.no_bundle = true;
  return { config: c, assetDirs };
}

/** Pick the bundle entry `wrangler deploy --dry-run --outdir` wrote for `main`. */
export function bundleEntryFor(outdir, main) {
  const names = readdirSync(outdir).filter((n) => /\.(m?js)$/.test(n) && !n.endsWith('.map'));
  const stem = basename(String(main)).replace(/\.(m?[jt]sx?|cts|mts)$/, '');
  const hit = names.find((n) => n.replace(/\.(m?js)$/, '') === stem);
  if (hit) return hit;
  if (names.length === 1) return names[0];
  throw new Error(`cannot tell which file of the bundle (${names.join(', ') || 'none'}) is the Worker entry for main '${main}'`);
}

/**
 * The audit / publish mode is DERIVED from the packaged config by the privileged side, never taken from the manifest: only a Dev Worker whose config keeps its
 * named Wrangler environment is `source` (audited and published through `--env <devEnv>`); everything else is flat (`effective`).
 */
export const configMode = (cfg, { target, driver, devEnv = 'dev' }) => (target === 'dev' && driver === 'worker' && cfg?.env?.[devEnv] && typeof cfg.env[devEnv] === 'object' ? 'source' : 'effective');

/** Assemble artifact/ from the audited config, the dry-run bundle and the asset directories, then write the manifest. */
export function packageArtifact({ cfg, configDir, workspace, bundleDir, dest, manifest, effectiveMain }) {
  if (!existsSync(bundleDir)) throw new Error(`no bundle at ${bundleDir}: the dry-run build produced nothing`);
  const entry = bundleEntryFor(bundleDir, effectiveMain);
  const { config, assetDirs } = rewriteConfig(cfg, { configDir, bundleEntry: entry, workspace });
  rmSync(dest, { recursive: true, force: true });
  mkdirSync(dest, { recursive: true });
  cpSync(bundleDir, join(dest, 'worker'), { recursive: true });
  rmSync(join(dest, 'worker', 'README.md'), { force: true });
  for (const a of assetDirs) {
    if (!existsSync(a.from) || !statSync(a.from).isDirectory()) throw new Error(`assets directory ${a.from} does not exist after the build`);
    cpSync(a.from, join(dest, a.to), { recursive: true, dereference: false });
  }
  writeFileSync(join(dest, 'wrangler.json'), `${JSON.stringify(config, null, 2)}\n`);
  listFiles(dest); // refuses symlinks / special files copied from the build tree
  const m = { v: MANIFEST_VERSION, ...manifest, files_digest: filesDigest(dest) };
  validateManifestShape(m);
  writeFileSync(join(dest, 'manifest.json'), `${JSON.stringify(m, null, 2)}\n`);
  return m;
}

export function validateManifestShape(m) {
  if (m === null || typeof m !== 'object' || Array.isArray(m)) throw new Error('artifact manifest must be an object');
  const keys = Object.keys(m);
  const extra = keys.filter((k) => !MANIFEST_KEYS.includes(k));
  const missing = MANIFEST_KEYS.filter((k) => !keys.includes(k));
  if (extra.length || missing.length) throw new Error(`artifact manifest keys: unexpected [${extra}] missing [${missing}]`);
  if (m.v !== MANIFEST_VERSION) throw new Error(`artifact manifest version ${m.v} is not supported`);
  for (const k of ['source_sha', 'tested_sha', 'artifact_sha']) if (!SHA_RE.test(String(m[k]))) throw new Error(`artifact manifest ${k} is not a 40-hex commit`);
  if (!['source', 'effective'].includes(m.config_mode)) throw new Error(`artifact manifest config_mode '${m.config_mode}' is not source | effective`);
  if (!/^[0-9a-f]{64}$/.test(String(m.files_digest))) throw new Error('artifact manifest files_digest is not a sha256');
  return m;
}

/**
 * Privileged-side verification. `expect` is derived by the PRIVILEGED job from its own trusted data (descriptor, GitHub context, inputs, event):
 *   { repository, app, target, driver, slot, runId, runAttempt, sourceSha, testedSha, targetUrl, workerName (expected name or '' to skip, e.g. prod
 *     takes the audited source name), allowMissingUrl }
 * Throws on the first violation - the artifact is then never published. Returns { manifest, config } for the audits that follow.
 */
export function verifyArtifact(dir, expect) {
  if (!existsSync(join(dir, 'manifest.json'))) throw new Error(`no artifact manifest in ${dir}`);
  const files = listFiles(dir); // also rejects symlinks / special files
  const m = validateManifestShape(JSON.parse(readFileSync(join(dir, 'manifest.json'), 'utf8')));
  const eq = (field, got, want) => { if (String(got) !== String(want)) throw new Error(`artifact ${field} '${got}' is not the expected '${want}'; refusing to publish`); };
  eq('repository', m.repository, expect.repository);
  eq('app', m.app, expect.app);
  eq('target', m.target, expect.target);
  eq('driver', m.driver, expect.driver);
  eq('slot', m.slot, expect.slot);
  eq('run_id', m.run_id, expect.runId);
  eq('run_attempt', m.run_attempt, expect.runAttempt);
  eq('source_sha', m.source_sha, expect.sourceSha);
  eq('tested_sha', m.tested_sha, expect.testedSha);
  eq('artifact_sha', m.artifact_sha, expect.testedSha); // the code that was built is exactly the code that passed CI
  eq('target_url', m.target_url, expect.targetUrl);
  if (m.files_digest !== filesDigest(dir)) throw new Error('artifact content does not match its recorded digest (corrupted or modified); refusing to publish');
  const cfg = JSON.parse(readFileSync(join(dir, 'wrangler.json'), 'utf8'));
  const problems = structuralProblems(cfg, files, expect);
  if (problems.length) throw new Error(`artifact configuration rejected: ${problems.join('; ')}`);
  return { manifest: m, config: cfg, mode: configMode(cfg, { target: expect.target, driver: expect.driver, devEnv: expect.devEnv }) };
}

/** Rules every packaged config must satisfy, whatever the target (the target-specific resource audit runs after this). */
export function structuralProblems(cfg, files, expect = {}) {
  const bad = [];
  for (const f of files) if (!ALLOWED_TOP(f) || f.split('/').some((seg) => seg.startsWith('.'))) bad.push(`unexpected file '${f}' (only manifest.json, wrangler.json, worker/**, assets/** without hidden files)`);
  if (cfg === null || typeof cfg !== 'object' || Array.isArray(cfg)) return ['wrangler.json is not an object'];
  if (cfg.no_bundle !== true) bad.push('no_bundle must be true (the publisher never bundles)');
  const names = [];
  for (const s of sections(cfg)) {
    for (const k of FORBIDDEN_KEYS) if (k in s) bad.push(`config key '${k}' is not allowed in a packaged config`);
    if (typeof s.main === 'string') {
      names.push(s.main);
      if (!/^worker\//.test(s.main) || s.main.includes('..') || !SAFE_REL.test(s.main) || !files.includes(s.main)) bad.push(`main '${s.main}' is not a bundled file under worker/`);
    }
    if (s.assets && typeof s.assets === 'object' && s.assets.directory !== undefined) {
      const d = String(s.assets.directory).replace(/^\.\//, '');
      if (!/^assets\/\d+$/.test(d)) bad.push(`assets.directory '${s.assets.directory}' is not an artifact assets directory`);
    }
  }
  if (typeof cfg.main !== 'string') bad.push('the config has no bundled main');
  if (expect.workerName !== undefined && expect.workerName !== '' && cfg.name !== expect.workerName) bad.push(`Worker name '${cfg.name}' is not the expected '${expect.workerName}'`);
  return bad;
}
