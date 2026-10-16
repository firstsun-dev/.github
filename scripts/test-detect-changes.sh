#!/usr/bin/env bash
# Exercises the "match" step of .github/workflows/_detect-changes.yml against
# throwaway git repositories (no network, no GitHub runner needed).
#
# Usage: ./scripts/test-detect-changes.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW="$REPO_ROOT/.github/workflows/_detect-changes.yml"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

python3 -I - "$WORKFLOW" "$WORK/step.sh" <<'PY'
import sys, yaml
wf = yaml.safe_load(open(sys.argv[1]))
steps = wf["jobs"]["detect"]["steps"]
step = next(s for s in steps if s.get("id") == "match")
open(sys.argv[2], "w").write(step["run"])
PY

fail=0
DB=$'db/**'
APP=$'f/**\nwmill.yaml'
CFG=$'secrets.*.map\nvariables*.map'
ZERO=0000000000000000000000000000000000000000

repo="$WORK/repo"
git init -q "$repo"
cd "$repo"
git config user.email t@t; git config user.name t
mkdir -p db f/x
echo 1 > db/a.sql; echo 1 > f/x/s.py; echo 1 > secrets.a.map; echo 1 > README.md
git add -A; git commit -qm base; BASE=$(git rev-parse HEAD)

commit() { git add -A; git commit -qm "$1"; git rev-parse HEAD; }

# run <base> <head>  -> prints "db app cfg resolved"
run() {
  local out="$WORK/out"; : > "$out"
  if ! GITHUB_OUTPUT="$out" BASE_SHA="$1" HEAD_SHA="$2" DB_PATTERNS="$DB" \
      APP_PATTERNS="$APP" CONFIG_PATTERNS="$CFG" bash "$WORK/step.sh" >"$WORK/log" 2>&1; then
    echo "ERROR"; return 0
  fi
  local g; g() { sed -n "s/^$1=//p" "$out"; }
  echo "$(g db_changed) $(g app_changed) $(g config_changed) $(g base_resolved)"
}

check() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then echo "PASS: $desc"; else
    echo "FAIL: $desc — expected '$expected', got '$actual'"; fail=1; fi
}

echo 2 > f/x/s.py; H=$(commit app)
check "app-only change"            "false true false true" "$(run "$BASE" "$H")"
echo 2 > secrets.a.map; H2=$(commit cfg)
check "config-only change"         "false false true true" "$(run "$H" "$H2")"
echo 2 > db/a.sql; echo 3 > f/x/s.py; H3=$(commit dbapp)
check "db + app change"            "true true false true" "$(run "$H2" "$H3")"
echo 9 > README.md; H4=$(commit docs)
check "unrelated change"           "false false false true" "$(run "$H3" "$H4")"
git rm -q secrets.a.map; H5=$(commit del)
check "deleted config file"        "false false true true" "$(run "$H4" "$H5")"
git mv f/x/s.py README2.txt; H6=$(commit rename-out)
check "rename out of app path"     "false true false true" "$(run "$H5" "$H6")"
check "empty base fails safe"      "true true true false" "$(run "" "$H6")"
check "all-zero base fails safe"   "true true true false" "$(run "$ZERO" "$H6")"
check "unknown base fails safe"    "true true true false" "$(run "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef" "$H6")"
check "empty head is an error"     "ERROR" "$(run "$BASE" "")"
check "unknown head is an error"   "ERROR" "$(run "$BASE" "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")"
check "same sha: nothing changed"  "false false false true" "$(run "$H6" "$H6")"

exit $fail
