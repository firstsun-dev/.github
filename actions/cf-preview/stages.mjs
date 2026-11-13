// Pure decision logic of the stage-based CI (OpenSpec simplify-cf-ci-stages-and-dev-delivery): task parsing, event routing, gate verdict.
// No I/O except the tiny CLI at the bottom, used by _cf-worker-ci.yml. Unit-tested in scripts/cf-preview/stages.test.mjs.

export const STAGES = ['verify', 'test'];
export const MAX_TASKS = 12;
export const TASK_ID_RE = /^[a-z0-9][a-z0-9-]{0,39}$/;
const TASK_KEYS = ['id', 'cmd'];

/**
 * Strict parse of a caller-owned task list: a JSON array of {"id","cmd"}. '' / '[]' = an intentionally empty stage.
 * Rejects (throws, so the run is red BEFORE any command executes): invalid JSON, a non-array, a non-object entry, unknown keys,
 * a malformed or duplicate id, an empty command, more than MAX_TASKS tasks.
 */
export function parseTasks(text, stage = 'tasks') {
  const s = String(text ?? '').trim();
  if (!s) return [];
  let a;
  try { a = JSON.parse(s); } catch (e) { throw new Error(`${stage}: not valid JSON (${e.message})`); }
  if (!Array.isArray(a)) throw new Error(`${stage}: must be a JSON array of {"id","cmd"}`);
  if (a.length > MAX_TASKS) throw new Error(`${stage}: ${a.length} tasks exceed the limit of ${MAX_TASKS}; merge small tasks into one command`);
  const seen = new Set();
  return a.map((t, i) => {
    if (t === null || typeof t !== 'object' || Array.isArray(t)) throw new Error(`${stage}[${i}]: must be an object {"id","cmd"}`);
    const extra = Object.keys(t).filter((k) => !TASK_KEYS.includes(k));
    if (extra.length) throw new Error(`${stage}[${i}]: unsupported option(s) ${extra.join(', ')} (only id and cmd exist)`);
    if (typeof t.id !== 'string' || !TASK_ID_RE.test(t.id)) throw new Error(`${stage}[${i}]: id '${t.id}' must match ${TASK_ID_RE} (lowercase, digits, dashes)`);
    if (typeof t.cmd !== 'string' || !t.cmd.trim()) throw new Error(`${stage}[${i}] '${t.id}': cmd must be a non-empty string`);
    if (seen.has(t.id)) throw new Error(`${stage}: duplicate task id '${t.id}'`);
    seen.add(t.id);
    return { id: t.id, cmd: t.cmd };
  });
}

/**
 * Event -> CI stages. Default policy: feature push = Verify; PR and merge queue = Verify + Test on the merge candidate;
 * main push = Verify (the PR Gate already ran Test before the merge; Production delivery has its own gates); manual dispatch = Verify
 * of the exact revision; delete, tags and every other event = no CI.
 */
export function routeEvent({ event, ref = '' }) {
  switch (event) {
    case 'pull_request': return { stages: ['verify', 'test'], reason: 'PR: Verify + Test on the merge candidate' };
    case 'merge_group': return { stages: ['verify', 'test'], reason: 'merge queue: Verify + Test on the merge-group SHA' };
    case 'push':
      return String(ref).startsWith('refs/heads/')
        ? { stages: ['verify'], reason: 'push: Verify (Test runs on the PR / merge queue)' }
        : { stages: [], reason: 'push of a non-branch ref: no CI' };
    case 'workflow_dispatch':
      return String(ref).startsWith('refs/heads/') ? { stages: ['verify'], reason: 'manual dispatch: Verify of the exact revision' } : { stages: [], reason: 'dispatch on a non-branch ref: no CI' };
    default: return { stages: [], reason: `event ${event || 'unknown'}: no CI` };
  }
}

/**
 * Which build env the validation commands import (and VITE_APP_ENV): the env of the intended deployment.
 * main push = prod; manual dispatch = dev (the only manual target); everything else = preview. Never prod off main.
 */
export function ciTargetEnv({ event, ref = '' }) {
  if (event === 'push' && ref === 'refs/heads/main') return 'prod';
  if (event === 'workflow_dispatch') return 'dev';
  return 'preview';
}

/** Fork PR trust: untrusted code may only run on the explicitly named hosted runner; never on the default (privileged / self-hosted) one. */
export function runnerFor({ event, isFork, runner, forkRunner = '' }) {
  if (event === 'pull_request' && isFork) {
    if (!forkRunner) throw new Error('fork pull request: refusing to run untrusted code on the privileged runner; set fork_runner to a GitHub-hosted runner label or do not trigger CI on pull_request');
    return forkRunner;
  }
  return runner;
}

/** Everything the CI workflow needs from its inputs, validated. Throws on any invalid configuration. */
export function planCi({ event, ref, isFork = false, verifyTasks = '', testTasks = '', e2eCmd = '', runner = 'linux', forkRunner = '' }) {
  const verify = parseTasks(verifyTasks, 'verify_tasks');
  const test = parseTasks(testTasks, 'test_tasks');
  const route = routeEvent({ event, ref });
  const picked = runnerFor({ event, isFork, runner, forkRunner });
  const e2e = Boolean(String(e2eCmd).trim());
  return {
    stages: route.stages, reason: route.reason, runner: picked, target_env: ciTargetEnv({ event, ref }),
    verify, test, e2e,
    run_verify: route.stages.includes('verify') && verify.length > 0,
    run_test: route.stages.includes('test') && test.length > 0,
    run_e2e: route.stages.includes('test') && e2e,
  };
}

/**
 * Fail-closed gate. A job that is expected must have succeeded; one that is not expected must have been skipped; the plan must have
 * succeeded. Failure, cancellation, an unexpected skip, a missing result and an unexpected run are all red. Returns { passed, errors }.
 * `expected` = { verify, test, e2e } booleans from the plan; `results` = { plan, verify, test, e2e } needs.<job>.result strings.
 */
export function gateVerdict({ planOk, expected, results }) {
  const errors = [];
  if (planOk !== true) errors.push(`plan finished '${results.plan || 'missing'}' (must succeed)`);
  if (planOk === true) {
    for (const j of ['verify', 'test', 'e2e']) {
      const r = results[j] ?? '';
      if (expected[j]) { if (r !== 'success') errors.push(`${j} finished '${r || 'missing'}' (configured for this event: must succeed)`); } else if (r !== 'skipped') errors.push(`${j} finished '${r || 'missing'}' (not expected for this event: must be skipped)`);
    }
  }
  return { passed: errors.length === 0, errors };
}

// ---- CLI -------------------------------------------------------------------------------------------------------------------
// stdout = key=value records for $GITHUB_OUTPUT; diagnostics on stderr; failure = non-zero exit.
async function main() {
  const [cmd] = process.argv.slice(2);
  const env = process.env;
  const flag = (k) => env[k] === 'true';
  try {
    if (cmd === 'plan') {
      const p = planCi({
        event: env.EVENT, ref: env.REF, isFork: flag('IS_FORK'), verifyTasks: env.VERIFY_TASKS, testTasks: env.TEST_TASKS, e2eCmd: env.E2E_CMD,
        runner: env.RUNNER || 'linux', forkRunner: env.FORK_RUNNER || '',
      });
      const o = {
        stages: p.stages.join(','), runner: p.runner, target_env: p.target_env,
        run_verify: p.run_verify, run_test: p.run_test, run_e2e: p.run_e2e,
        verify_tasks: JSON.stringify(p.verify), test_tasks: JSON.stringify(p.test),
      };
      for (const [k, v] of Object.entries(o)) console.log(`${k}=${v}`);
      console.error(`CI plan: ${p.reason}; verify=${p.verify.length} test=${p.test.length} e2e=${p.e2e}`);
    } else if (cmd === 'gate') {
      const v = gateVerdict({
        planOk: env.PLAN === 'success',
        expected: { verify: flag('RUN_VERIFY'), test: flag('RUN_TEST'), e2e: flag('RUN_E2E') },
        results: { plan: env.PLAN, verify: env.VERIFY, test: env.TEST, e2e: env.E2E },
      });
      for (const e of v.errors) console.error(`::error::${e}`);
      if (!v.passed) process.exit(1);
      console.log('validation=passed');
    } else {
      console.error('usage: stages.mjs plan|gate');
      process.exit(2);
    }
  } catch (e) {
    console.error(`::error::${e.message}`);
    process.exit(1);
  }
}
if (import.meta.url === `file://${process.argv[1]}`) await main();
