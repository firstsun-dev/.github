#!/usr/bin/env bash
# Pipeline self-tests: three independent suites that run in parallel, one verdict. Verbose output goes to $LOGDIR, only summaries print.
#   test.sh static   actionlint (when installed), shellcheck, workflow / action structure (check-workflows.py)
#   test.sh unit     pure logic: task parsing, event routing, stage selection, target planning, descriptor, gate verdicts, CLI contract
#   test.sh safety   provider-side-effect-free engine scenarios (stub wrangler / gh / curl): audit, build-before-publish, migration, smoke, ledger
#   test.sh full     all three, in parallel (the default; this is what the `Pipeline Gate` job aggregates job by job)
# What none of this proves - GitHub's real scheduling, permissions, reusable-workflow calls and Cloudflare itself - is covered by the real-runner
# canary on cf-v2-scratch-caller, not by emulation here.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
LOGDIR="${LOGDIR:-$(mktemp -d)}"; mkdir -p "$LOGDIR"
need() { command -v "$1" >/dev/null 2>&1 || { echo "FAIL missing tool '$1': install it (this suite cannot pass without it); nothing was run" >&2; exit 127; }; }

run() { # run <label> <cmd...> : one line verdict, tail of the log on failure
  local label="$1"; shift
  local t0=$SECONDS rc=0
  "$@" >"$LOGDIR/${label// /_}.log" 2>&1 || rc=$?
  if [ "$rc" -eq 0 ]; then echo "ok   $label ($((SECONDS - t0))s)"; else echo "FAIL $label ($((SECONDS - t0))s)"; tail -n 25 "$LOGDIR/${label// /_}.log"; fi
  return "$rc"
}
static() {
  local rc=0
  run "workflow structure" python3 scripts/cf-preview/check-workflows.py || rc=1
  if command -v shellcheck >/dev/null 2>&1; then
    run "shellcheck scripts" shellcheck -x -S warning -P SCRIPTDIR actions/cf-preview/*.sh scripts/cf-preview/*.sh || rc=1
    run "shellcheck actions" python3 scripts/cf-preview/shellcheck-actions.py || rc=1
  else echo "FAIL missing tool 'shellcheck'" >&2; rc=1; fi
  if command -v actionlint >/dev/null 2>&1; then
    run "actionlint" actionlint -color .github/workflows/_cf-worker-v2.yml .github/workflows/_cf-worker-ci.yml .github/workflows/_cf-worker-delivery.yml .github/workflows/_cf-worker-lifecycle.yml .github/workflows/_cf-worker-preview-gc.yml .github/workflows/cf-worker-v2-ci.yml || rc=1
  else echo "FAIL missing tool 'actionlint' (install it; CI does)" >&2; rc=1; fi
  return "$rc"
}
unit() {
  local rc=0
  run "node unit" node --test scripts/cf-preview/lib.test.mjs scripts/cf-preview/delivery.test.mjs scripts/cf-preview/stages.test.mjs scripts/cf-preview/cli.test.mjs scripts/cf-preview-gc/gc.test.mjs || rc=1
  run "parse-env" scripts/cf-preview/test-parse-env.sh || rc=1
  return "$rc"
}
safety() {
  local rc=0
  for t in deploy migrate smoke probe gh; do run "$t" "scripts/cf-preview/test-$t.sh" || rc=1; done
  return "$rc"
}

mode="${1:-full}"
case "$mode" in static|unit|safety|full) ;; *) echo "usage: $0 static|unit|safety|full" >&2; exit 2;; esac
need python3; need node; need jq; need bash
case "$mode" in
  full) # parallel suites, ONE verdict: a suite that failed OR did not report fails the run (fail closed)
    pids=(); names=(static unit safety)
    for n in "${names[@]}"; do "$n" >"$LOGDIR/suite-$n.out" 2>&1 & pids+=($!); done
    rc=0
    for i in "${!names[@]}"; do
      wait "${pids[$i]}" || rc=1
      echo "== ${names[$i]}"; cat "$LOGDIR/suite-${names[$i]}.out"
    done;;
  *) "$mode"; rc=$?;;
esac
if [ "$rc" -eq 0 ]; then echo "PASSED ($mode)"; else echo "FAILED ($mode)"; fi
echo "logs: $LOGDIR"
exit "$rc"
