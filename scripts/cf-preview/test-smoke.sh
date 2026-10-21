#!/usr/bin/env bash
# smoke.sh with a stub curl: fail-closed verification (no URL, redirects, 4xx/5xx, SHA mismatch, Access policy).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT; mkdir "$T/bin"
cat > "$T/bin/curl" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  *"-w"*) printf '%s' "${STUB_OUT:-200 }";;
  *) [ "${STUB_SHA_FAIL:-}" = 1 ] && exit 22; printf '%s' "${STUB_SHA_BODY:-}";;
esac
STUB
chmod +x "$T/bin/curl"
export PATH="$T/bin:$PATH" TOOLS_DIR="$ROOT/actions/cf-preview" PROBE_ATTEMPTS=1 PROBE_SLEEP=0 SHA_ATTEMPTS=1 SHA_SLEEP=0 EXPECTED_SHA=abcdef1234567
fail=0; ok(){ echo "PASS: $1"; }; bad(){ echo "FAIL: $1"; fail=1; }
S="$ROOT/actions/cf-preview/smoke.sh"
run() { DEPLOY_TARGET="${DEPLOY_TARGET:-prod}" TARGET_URL="${TARGET_URL-https://x.example}" "$S" 2>/dev/null; }

[ "$(run)" = smoke_level=content ] && ok "2xx -> content verified" || bad "2xx"
TARGET_URL='' run >/dev/null && bad "empty URL accepted" || ok "empty URL fails"
for o in '301 https://x.example/en/' '401 ' '403 ' '404 ' '500 ' '000 ' '302 https://evil.example/x'; do
  STUB_OUT="$o" run >/dev/null && bad "'$o' accepted" || ok "'$o' fails"
done
ACC='302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x'
STUB_OUT="$ACC" run >/dev/null && bad "Access accepted without approval" || ok "Access redirect fails when the target is not approved (default none)"
STUB_OUT="$ACC" ACCESS_TARGETS=preview run >/dev/null && bad "Access approved for preview only accepted for prod" || ok "approval is per target (preview approval does not cover prod)"
[ "$(STUB_OUT="$ACC" ACCESS_TARGETS="preview prod" run)" = smoke_level=liveness ] && ok "approved Access target -> liveness only" || bad "approved access"
[ "$(DEPLOY_TARGET=preview STUB_OUT="$ACC" ACCESS_TARGETS=preview run)" = smoke_level=liveness ] && ok "preview Access approval -> liveness" || bad "preview access"
SHA_PATH=/v STUB_SHA_BODY=abcdef1 run >/dev/null && ok "SHA endpoint matches" || bad "sha match"
SHA_PATH=/v STUB_SHA_BODY=zzzzzzz run >/dev/null && bad "SHA mismatch accepted" || ok "SHA mismatch fails"
SHA_PATH=/v STUB_SHA_FAIL=1 run >/dev/null && bad "missing SHA endpoint ignored" || ok "configured SHA endpoint missing fails"
SMOKE_CMD='test "$TARGET_URL" = https://x.example' run >/dev/null && ok "smoke_cmd receives TARGET_URL" || bad "smoke cmd env"
SMOKE_CMD='exit 3' run >/dev/null && bad "failing smoke_cmd accepted" || ok "failing smoke_cmd fails"
exit $fail
