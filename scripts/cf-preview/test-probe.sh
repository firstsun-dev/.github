#!/usr/bin/env bash
# Behavioural test of probe.sh with a stub curl (no network).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT; mkdir "$T/bin"
cat > "$T/bin/curl" <<'STUB'
#!/usr/bin/env bash
for a in "$@"; do case "$a" in @*) [ -z "${STUB_LOG:-}" ] || cat "${a#@}" >> "$STUB_LOG";; esac; done
printf '%s' "${STUB_OUT:-000 }"
STUB
chmod +x "$T/bin/curl"
export PATH="$T/bin:$PATH" TARGET_URL=https://x.example PROBE_ATTEMPTS=2 PROBE_SLEEP=0
fail=0; ok(){ echo "PASS: $1"; }; bad(){ echo "FAIL: $1"; fail=1; }
P="$ROOT/actions/cf-preview/probe.sh"

[ "$(STUB_OUT='200 ' "$P" 2>/dev/null)" = "probe=ok" ] && ok "200 -> ok" || bad "200"
[ "$(STUB_OUT='204 ' "$P" 2>/dev/null)" = "probe=ok" ] && ok "204 -> ok" || bad "204"
[ "$(STUB_OUT='302 https://firstsun.cloudflareaccess.com/cdn-cgi/access/login/h?kid=1' "$P" 2>/dev/null)" = "probe=access-protected" ] && ok "302 to Access -> access-protected" || bad "access"
STUB_OUT='302 https://evil.example/login' "$P" >/dev/null 2>&1 && bad "302 elsewhere accepted" || ok "302 to a non-Access host fails"
STUB_OUT='302 https://cloudflareaccess.com.evil.example/x' "$P" >/dev/null 2>&1 && bad "lookalike Access host accepted" || ok "lookalike host fails"
STUB_OUT='301 https://x.example/en/' "$P" >/dev/null 2>&1 && bad "same-site 301 accepted" || ok "plain redirect is not a pass"
STUB_OUT='404 ' "$P" >/dev/null 2>&1 && bad "404 accepted" || ok "404 fails"
STUB_OUT='500 ' "$P" >/dev/null 2>&1 && bad "500 accepted" || ok "500 fails"
STUB_OUT='000 ' "$P" >/dev/null 2>&1 && bad "connection failure accepted" || ok "no response fails"
export STUB_LOG="$T/log"; : > "$STUB_LOG"
CF_ACCESS_CLIENT_ID=i1 CF_ACCESS_CLIENT_SECRET=s1 STUB_OUT='200 ' "$P" >/dev/null 2>&1; grep -q 'CF-Access-Client-Id: i1' "$STUB_LOG" && grep -q 'CF-Access-Client-Secret: s1' "$STUB_LOG" && ok "probe sends both Access service token headers" || bad "token headers"
CF_ACCESS_CLIENT_ID=i1 STUB_OUT='200 ' "$P" >/dev/null 2>&1 && bad "partial credentials accepted" || ok "probe: partial credentials fail"
exit $fail
