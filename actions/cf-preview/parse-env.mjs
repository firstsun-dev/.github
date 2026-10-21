// Safe import of caller-supplied KEY=VALUE lines into $GITHUB_ENV. Dependency-free on purpose
// (the CI/E2E jobs only fetch this file, they do not install tooling deps).
// The input arrives through an environment variable, never interpolated into a shell script.
import { appendFileSync, readFileSync, realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
// Credentials, runner/Actions internals and variables that change how the shell or Node loads code.
const FORBIDDEN = /^(GITHUB_|RUNNER_|ACTIONS_|INPUT_|CLOUDFLARE_|LD_|DYLD_|GIT_|NPM_CONFIG_|PNPM_|BASH_|PATH$|HOME$|SHELL$|ENV$|IFS$|PS4$|CDPATH$|NODE_OPTIONS$|NODE_PATH$|NODE_EXTRA_CA_CERTS$|SMOKE_USERNAME$|SMOKE_PASSWORD$)/;

export function parseEnvLines(text) {
  const pairs = [];
  const errors = [];
  String(text ?? '').split('\n').forEach((raw, i) => {
    const line = raw.replace(/\r$/, '');
    if (!line.trim() || line.trimStart().startsWith('#')) return;
    const eq = line.indexOf('=');
    const name = eq > 0 ? line.slice(0, eq) : '';
    if (!NAME_RE.test(name)) { errors.push(`line ${i + 1}: expected NAME=VALUE with NAME matching [A-Za-z_][A-Za-z0-9_]*`); return; }
    if (FORBIDDEN.test(name)) { errors.push(`line ${i + 1}: ${name} may not be set from caller env input`); return; }
    const value = line.slice(eq + 1);
    // eslint-disable-next-line no-control-regex
    if (/[\u0000-\u0008\u000b-\u001f\u007f]/.test(value)) { errors.push(`line ${i + 1}: ${name} contains control characters`); return; }
    pairs.push([name, value]);
  });
  return { pairs, errors };
}

/** Entry points (argv): `<VAR> [<file>] [--mask]` import env text from $VAR into a file (default $GITHUB_ENV);
 *  `--emit0 <file>` re-validate a file written by the first form and print NUL-separated NAME=VALUE pairs. */
// Compare real paths: a symlinked checkout must not turn the importer into a silent no-op.
if (process.argv[1] && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url))) {
  const args = process.argv.slice(2);
  const mask = args.includes('--mask');
  const rest = args.filter((a) => a !== '--mask');
  if (rest[0] === '--emit0') {
    const { pairs, errors } = parseEnvLines(readFileSync(rest[1], 'utf8'));
    if (errors.length) { for (const e of errors) console.error(`::error::${rest[1]}: ${e}`); process.exit(1); }
    process.stdout.write(pairs.map(([k, v]) => `${k}=${v}\0`).join(''));
  } else {
    const [varName, target] = rest;
    const { pairs, errors } = parseEnvLines(process.env[varName]);
    if (errors.length) {
      for (const e of errors) console.log(`::error::${varName}: ${e}`);
      process.exit(1);
    }
    // Mask before anything can print a value. One KEY=VALUE per line: values cannot contain a newline,
    // so no heredoc delimiter is needed. New files are owner-only.
    if (mask) for (const [, v] of pairs) if (v) console.log(`::add-mask::${v}`);
    if (pairs.length) appendFileSync(target ?? process.env.GITHUB_ENV, pairs.map(([k, v]) => `${k}=${v}`).join('\n') + '\n', { mode: 0o600 });
    console.log(`imported ${pairs.length} variable(s) from ${varName}: ${pairs.map(([k]) => k).join(', ')}`);
  }
}
