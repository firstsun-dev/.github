#!/usr/bin/env bash
# CF Worker v2 test profiles. One entrypoint for CI and agents; verbose output goes to $LOGDIR, only summaries print.
#   test.sh fast   static contract + unit + stub-based shell tests (~10s)
#   test.sh chain  real-shell delivery-chain scenarios, groups run in parallel (the expensive part)
#   test.sh full   fast + chain  (the merge gate)
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
LOGDIR="${LOGDIR:-$(mktemp -d)}"; mkdir -p "$LOGDIR"
rc=0
run() { # run <label> <cmd...>
  local label="$1"; shift
  local t0=$SECONDS
  if "$@" >"$LOGDIR/${label// /_}.log" 2>&1; then echo "ok   $label ($((SECONDS - t0))s)"
  else echo "FAIL $label ($((SECONDS - t0))s)"; tail -n 25 "$LOGDIR/${label// /_}.log"; rc=1; fi
}
fast() {
  run "workflow contract" python3 scripts/cf-preview/test-workflow-contract.py
  run "lib unit" node --test scripts/cf-preview/lib.test.mjs scripts/cf-preview/delivery.test.mjs
  for t in deploy migrate smoke parse-env probe gh; do run "$t" "scripts/cf-preview/test-$t.sh"; done
  run "gc unit" node --test scripts/cf-preview-gc/gc.test.mjs
}
chain() { run "delivery chain groups" python3 scripts/cf-preview/run-job-groups.py --logdir "$LOGDIR/groups"; }
case "${1:-full}" in
  fast) fast;;
  chain) chain;;
  full) fast; chain;;
  *) echo "usage: $0 fast|chain|full" >&2; exit 2;;
esac
echo "logs: $LOGDIR"
exit $rc
