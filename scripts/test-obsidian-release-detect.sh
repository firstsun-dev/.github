#!/usr/bin/env bash
# Exercises the "release_setup" step of .github/workflows/obsidian-plugin-ci.yml
# against throwaway project directories (no network, no GitHub runner needed).
#
# Usage: ./scripts/test-obsidian-release-detect.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW="$REPO_ROOT/.github/workflows/obsidian-plugin-ci.yml"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

python3 -I - "$WORKFLOW" "$WORK/step.sh" <<'PY'
import sys, yaml
wf = yaml.safe_load(open(sys.argv[1]))
steps = wf["jobs"]["release-metadata"]["steps"]
step = next(s for s in steps if s.get("id") == "release_setup")
open(sys.argv[2], "w").write(step["run"])
PY

fail=0

# check <name> <expected> <setup-command>   (setup runs inside a fresh project dir)
check() {
  local name="$1" expected="$2" setup="$3" dir out
  dir="$(mktemp -d -p "$WORK")"; out="$WORK/out"; : > "$out"
  ( cd "$dir" && eval "$setup" && GITHUB_OUTPUT="$out" bash -e "$WORK/step.sh" >/dev/null )
  local got; got="$(sed -n 's/^configured=//p' "$out")"
  if [ "$got" = "$expected" ]; then echo "ok   $name -> $got"; else echo "FAIL $name: expected $expected, got '$got'"; fail=1; fi
}

check "no package.json, no config"        false ':'
check "plain package.json"                false 'echo "{\"name\":\"x\"}" > package.json'
check "semantic-release in devDeps"       true  'echo "{\"devDependencies\":{\"semantic-release\":\"^25\"}}" > package.json'
check "semantic-release in deps"          true  'echo "{\"dependencies\":{\"semantic-release\":\"^25\"}}" > package.json'
check "release key in package.json"       true  'echo "{\"release\":{\"branches\":[\"main\"]}}" > package.json'
check ".releaserc.json only"              true  'echo "{\"name\":\"x\"}" > package.json; echo "{}" > .releaserc.json'
check "release.config.mjs only"           true  'echo "{\"name\":\"x\"}" > package.json; echo "export default {}" > release.config.mjs'
check "plugin dep only, no core, no config" false 'echo "{\"devDependencies\":{\"@semantic-release/git\":\"^10\"}}" > package.json'

exit "$fail"
