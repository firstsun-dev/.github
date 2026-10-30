#!/usr/bin/env bash
# CF Worker v2 test profiles. One entrypoint for CI and agents; verbose output goes to $LOGDIR, only summaries print.
#   test.sh fast   static contract + unit + stub-based shell tests (~10s)
#   test.sh chain  real-shell delivery-chain scenarios, groups run in parallel (the expensive part)
#   test.sh full   fast, then chain - chain is NOT started when fast failed (the merge gate needs both green)
# Test seam (used by test-testsh.sh): CF_V2_TEST_FAST_CMD / CF_V2_TEST_CHAIN_CMD replace the real fast/chain command.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
LOGDIR="${LOGDIR:-$(mktemp -d)}"; mkdir -p "$LOGDIR"
rc=0
run() { # run <label> <cmd...>
  local label="$1"; shift
  local t0=$SECONDS
  if "$@" >"$LOGDIR/${label// /_}.log" 2>&1; then echo "ok   $label ($((SECONDS - t0))s)"
  else echo "FAIL $label ($((SECONDS - t0))s)"; tail -n 25 "$LOGDIR/${label// /_}.log"; rc=1; fi
}
need() { command -v "$1" >/dev/null 2>&1 || { echo "FAIL missing tool '$1': install it (this suite cannot pass without it); nothing was run" >&2; exit 127; }; }
fast() {
  if [ -n "${CF_V2_TEST_FAST_CMD:-}" ]; then run "fast (override)" bash -c "$CF_V2_TEST_FAST_CMD"; return; fi
  run "workflow contract" python3 scripts/cf-preview/test-workflow-contract.py
  run "node unit + cli" node --test scripts/cf-preview/lib.test.mjs scripts/cf-preview/delivery.test.mjs scripts/cf-preview/cli.test.mjs
  for t in deploy migrate smoke parse-env probe gh testsh; do run "$t" "scripts/cf-preview/test-$t.sh"; done
  run "gc unit" node --test scripts/cf-preview-gc/gc.test.mjs
}
chain() {
  if [ -n "${CF_V2_TEST_CHAIN_CMD:-}" ]; then run "delivery chain (override)" bash -c "$CF_V2_TEST_CHAIN_CMD"; return; fi
  run "delivery chain groups" python3 scripts/cf-preview/run-job-groups.py --logdir "$LOGDIR/groups"
}
mode="${1:-full}"
case "$mode" in fast|chain|full) ;; *) echo "usage: $0 fast|chain|full" >&2; exit 2;; esac
if [ -z "${CF_V2_TEST_FAST_CMD:-}${CF_V2_TEST_CHAIN_CMD:-}" ]; then need python3; need node; need jq; need bash; fi
case "$mode" in
  fast) fast;;
  chain) chain;;
  full)
    fast
    if [ "$rc" -ne 0 ]; then echo "SKIP delivery chain: the Fast profile failed, so the expensive Chain profile was not started (Full = FAILED)"; else chain; fi;;
esac
if [ "$rc" -eq 0 ]; then echo "PASSED ($mode)"; else echo "FAILED ($mode)"; fi
echo "logs: $LOGDIR"
exit $rc
