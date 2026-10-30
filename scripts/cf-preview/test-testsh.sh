#!/usr/bin/env bash
# test.sh profile semantics (stubbed fast/chain commands): Full fails fast, a failing Chain fails Full, missing tooling is loud.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
fail=0
check() { if [ "$1" = 0 ]; then echo "PASS: $2"; else echo "FAIL: $2"; fail=1; fi; }
T=scripts/cf-preview/test.sh
D="$(mktemp -d)"
marker="$D/chain-ran"
CHAIN_OK="touch $marker"
CHAIN_BAD="touch $marker; exit 3"

rm -f "$marker"; out="$(LOGDIR="$D/l1" CF_V2_TEST_FAST_CMD=true CF_V2_TEST_CHAIN_CMD="$CHAIN_OK" "$T" full 2>&1)"; rc=$?
check "$([ $rc = 0 ] && [ -e "$marker" ] && echo 0 || echo 1)" "Full: Fast passes -> Chain runs -> success"

rm -f "$marker"; out="$(LOGDIR="$D/l2" CF_V2_TEST_FAST_CMD="echo boom; exit 1" CF_V2_TEST_CHAIN_CMD="$CHAIN_OK" "$T" full 2>&1)"; rc=$?
check "$([ $rc != 0 ] && [ ! -e "$marker" ] && echo 0 || echo 1)" "Full: Fast fails -> Chain is NOT started, exit code non-zero"
check "$(printf '%s' "$out" | grep -q 'SKIP delivery chain: the Fast profile failed' && printf '%s' "$out" | grep -q 'boom' && printf '%s' "$out" | grep -q 'logs: ' && echo 0 || echo 1)" "Full: the skip reason, the failure excerpt and the log directory are reported"
check "$([ -s "$D/l2/fast_(override).log" ] && echo 0 || echo 1)" "Full: the failing Fast log is preserved"

rm -f "$marker"; out="$(LOGDIR="$D/l3" CF_V2_TEST_FAST_CMD=true CF_V2_TEST_CHAIN_CMD="$CHAIN_BAD" "$T" full 2>&1)"; rc=$?
check "$([ $rc != 0 ] && [ -e "$marker" ] && echo 0 || echo 1)" "Full: Chain fails -> Full fails"

rm -f "$marker"; LOGDIR="$D/l4" CF_V2_TEST_FAST_CMD="exit 1" CF_V2_TEST_CHAIN_CMD="$CHAIN_OK" "$T" chain >/dev/null 2>&1; rc=$?
check "$([ $rc = 0 ] && [ -e "$marker" ] && echo 0 || echo 1)" "standalone chain: runs regardless of Fast"
LOGDIR="$D/l5" CF_V2_TEST_FAST_CMD="exit 1" CF_V2_TEST_CHAIN_CMD="$CHAIN_OK" "$T" fast >/dev/null 2>&1; rc=$?
check "$([ $rc != 0 ] && echo 0 || echo 1)" "standalone fast: failure propagates"

# missing tooling: no false success (a PATH without python3/node/jq)
mkdir -p "$D/bin"; for t in bash dirname mktemp mkdir tail cat env; do ln -sf "$(command -v $t)" "$D/bin/$t" 2>/dev/null; done
out="$(LOGDIR="$D/l6" PATH="$D/bin" "$(command -v bash)" "$T" fast 2>&1)"; rc=$?
check "$([ $rc != 0 ] && printf '%s' "$out" | grep -q "missing tool" && ! printf '%s' "$out" | grep -q '^PASSED' && echo 0 || echo 1)" "missing tooling: clear error, non-zero, no PASSED line"
"$T" bogus >/dev/null 2>&1; check "$([ $? = 2 ] && echo 0 || echo 1)" "unknown mode: usage error"
rm -rf "$D"
exit $fail
