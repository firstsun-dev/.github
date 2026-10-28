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
# probe_path / probe_expect_status
cat > "$T/bin/curl" <<'STUB'
#!/usr/bin/env bash
for a in "$@"; do case "$a" in https://*) printf '%s\n' "$a" >> "${STUB_URLS:?}";; esac; done
printf '%s' "${STUB_OUT:-000 }"
STUB
export STUB_URLS="$T/urls"; : > "$STUB_URLS"
STUB_OUT='200 ' "$P" >/dev/null 2>&1; [ "$(tail -1 "$STUB_URLS")" = "https://x.example/" ] && ok "default path is /" || bad "default path"
PROBE_PATH=/v1/notify STUB_OUT='200 ' "$P" >/dev/null 2>&1; [ "$(tail -1 "$STUB_URLS")" = "https://x.example/v1/notify" ] && ok "PROBE_PATH is requested" || bad "PROBE_PATH"
[ "$(PROBE_PATH=/v1/notify PROBE_EXPECT_STATUS=405 STUB_OUT='405 ' "$P" 2>/dev/null)" = "probe=ok" ] && ok "expected 405 -> ok" || bad "expect 405"
PROBE_EXPECT_STATUS=405 STUB_OUT='200 ' "$P" >/dev/null 2>&1 && bad "200 accepted when 405 expected" || ok "other status fails when one is expected"
PROBE_EXPECT_STATUS=405 STUB_OUT='404 ' "$P" >/dev/null 2>&1 && bad "404 accepted when 405 expected" || ok "404 fails when 405 expected"
[ "$(PROBE_EXPECT_STATUS=405 STUB_OUT='302 https://firstsun.cloudflareaccess.com/cdn-cgi/access/login/h?kid=1' "$P" 2>/dev/null)" = "probe=access-protected" ] && ok "Access redirect still recognised with expect" || bad "access with expect"
PROBE_PATH='v1/notify' STUB_OUT='200 ' "$P" >/dev/null 2>&1 && bad "path without leading / accepted" || ok "probe_path must start with /"
PROBE_PATH='/a b' STUB_OUT='200 ' "$P" >/dev/null 2>&1 && bad "path with space accepted" || ok "probe_path rejects unsafe characters"
PROBE_EXPECT_STATUS=2xx STUB_OUT='200 ' "$P" >/dev/null 2>&1 && bad "non-numeric expect accepted" || ok "probe_expect_status must be a 3-digit code"
exit $fail
