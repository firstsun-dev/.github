#!/usr/bin/env bash
# Exercises the "Enforce scan policy" and "Summarize findings" steps of
# .github/workflows/_zap-scan-template.yml locally (no network, no ZAP, no runner).
#
# Usage: ./scripts/test-zap-template.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW="$REPO_ROOT/.github/workflows/_zap-scan-template.yml"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

python3 -I - "$WORKFLOW" "$WORK" <<'PY'
import sys, yaml
wf = yaml.safe_load(open(sys.argv[1]))
steps = wf["jobs"]["zap-scan"]["steps"]
for sid, out in (("policy", "policy.sh"), ("summarize", "summarize.sh")):
    s = next(s for s in steps if s.get("id") == sid)
    open(f"{sys.argv[2]}/{out}", "w").write(s["run"])
PY

fail=0
policy() { # expected_rc env mode scan url protected
  local want="$1"; shift
  local rc=0
  (cd "$WORK" && GITHUB_OUTPUT="$WORK/out" TARGET_ENV="$1" SCAN_MODE="$2" SCAN="$3" TARGET_URL="$4" PROTECTED_HOSTS="${5:-}" bash policy.sh >/dev/null 2>&1) || rc=$?
  if { [ "$want" = 0 ] && [ "$rc" -eq 0 ]; } || { [ "$want" = 1 ] && [ "$rc" -ne 0 ]; }; then echo "ok   policy $*"; else echo "FAIL policy $* (rc=$rc, wanted $want)"; fail=1; fi
}
policy 0 production baseline baseline https://id.example.com
policy 1 production full baseline https://id.example.com
policy 1 production baseline api https://id.example.com
policy 0 development full baseline https://id-dev.example.com
policy 0 development full api https://id-dev.example.com
policy 1 development full api https://id.example.com '*.example.com'
policy 1 development full baseline https://ID.example.com:8443/x '*.example.com'
policy 0 development baseline baseline https://id.example.com '*.example.com'
policy 1 prod baseline baseline https://id.example.com
policy 1 production nuke baseline https://id.example.com

# Summarize: redaction + counts
mkdir -p "$WORK/zap-out"
cat > "$WORK/zap-out/report_json.json" <<'JSON'
{"site":[{"@name":"https://x","alerts":[
 {"pluginid":"40018","name":"SQL Injection","riskcode":"3","riskdesc":"High (Medium)","confidence":"2","count":"1","solution":"<p>Use prepared statements</p>",
  "instances":[{"uri":"https://user:pw@api.example.com/a?token=SECRET123&id=7","method":"GET","param":"id","attack":"1' OR '1'='1","evidence":"Authorization: Bearer abc.def.ghi"}]},
 {"pluginid":"10021","name":"X-Content-Type-Options","riskcode":"1","riskdesc":"Low (Medium)","count":"2","solution":"x","instances":[]}]}]}
JSON
(cd "$WORK" && GITHUB_STEP_SUMMARY="$WORK/summary.md" APP=t SCAN=api SCAN_MODE=full TARGET_ENV=development ZAP_EXIT=2 bash summarize.sh >/dev/null)
if grep -qE 'SECRET123|abc\.def|user:pw' "$WORK/zap-out/report-redacted.json" "$WORK/summary.md"; then echo "FAIL redaction leaked secret"; fail=1; else echo "ok   redaction"; fi
grep -q '1 High · 0 Medium · 1 Low' "$WORK/summary.md" && echo "ok   counts" || { echo "FAIL counts"; fail=1; }
grep -q 'SQL Injection' "$WORK/summary.md" && grep -q 'id=\*' "$WORK/summary.md" && echo "ok   summary detail" || { echo "FAIL summary detail"; fail=1; }

[ -n "${DEBUG_ZAP:-}" ] && cat "$WORK/summary.md" "$WORK/zap-out/report-redacted.json"
exit $fail
