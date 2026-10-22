#!/usr/bin/env bash
# smoke.sh with a stub curl: fail-closed verification (no URL, redirects, 4xx/5xx, SHA mismatch, Access policy).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT; mkdir "$T/bin"
cat > "$T/bin/curl" <<'STUB'
#!/usr/bin/env bash
# Records which request carried which header file contents (and whether a secret leaked into argv).
kind=sha; case "$*" in *"-w"*) kind=probe;; esac
for a in "$@"; do case "$a" in @*) h="$(cat "${a#@}")";; esac; done
[ -z "${STUB_LOG:-}" ] || { printf '%s|%s|%s\n' "$kind" "$(printf '%s' "${h:-}" | tr '\n' ';')" "$*" >> "$STUB_LOG"; }
if [ -n "${STUB_NEED_TOKEN:-}" ] && ! printf '%s' "${h:-}" | grep -q "CF-Access-Client-Secret: $STUB_NEED_TOKEN"; then
  case "$*" in *"-w"*) printf '%s' "302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x";; *) exit 22;; esac
  exit 0
fi
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
[ "$(SHA_PATH=/v STUB_SHA_BODY=abcdef1 run)" = smoke_level=content-sha ] && ok "matching sha_path -> content-sha" || bad "content-sha level"
[ "$(STUB_OUT="$ACC" ACCESS_TARGETS=prod SMOKE_CMD=true SHA_PATH=/v STUB_SHA_BODY=abcdef1 run)" = smoke_level=liveness ] && ok "Access + passing smoke_cmd/sha stays liveness (no unauthenticated content claim)" || bad "access upgraded"
[ "$(PROBE_HEADERS='CF-Access-Client-Id: x' STUB_OUT='200 ' ACCESS_TARGETS=prod SHA_PATH=/v STUB_SHA_BODY=abcdef1 run)" = smoke_level=content-sha ] && ok "authenticated probe of an Access-protected target -> content-sha" || bad "authenticated"
# ---- Cloudflare Access service token (CF_ACCESS_CLIENT_ID/SECRET) and legacy PROBE_HEADERS ----
export STUB_LOG="$T/curl.log"; : > "$STUB_LOG"
TOKEN_ENV=(CF_ACCESS_CLIENT_ID=cid-123 CF_ACCESS_CLIENT_SECRET=sek-456)
out="$(env "${TOKEN_ENV[@]}" STUB_NEED_TOKEN=sek-456 ACCESS_TARGETS=prod SHA_PATH=/v STUB_SHA_BODY=abcdef1 STUB_OUT='200 ' DEPLOY_TARGET=prod TARGET_URL=https://x.example "$S" 2>"$T/err")"
[ "$out" = smoke_level=content-sha ] && ok "service token: authenticated 2xx + matching SHA -> content-sha" || bad "service token content-sha ($out)"
grep -q '^probe|CF-Access-Client-Id: cid-123;CF-Access-Client-Secret: sek-456|' "$STUB_LOG" && ok "both service token headers forwarded to the initial probe" || bad "probe headers"
grep -q '^sha|CF-Access-Client-Id: cid-123;CF-Access-Client-Secret: sek-456|' "$STUB_LOG" && ok "both service token headers forwarded to the SHA endpoint" || bad "sha headers"
if grep -q -e 'sek-456' <<< "$(cut -d'|' -f3 "$STUB_LOG")" || grep -v '^::add-mask::' "$T/err" | grep -q 'sek-456' || grep -q 'sek-456' <<< "$out"; then bad "secret leaked into argv/stderr/stdout"; else ok "service token secret never appears in curl argv, logs or outputs"; fi
grep -q '::add-mask::sek-456' "$T/err" && ok "secret is registered as masked" || bad "mask"
# Access redirect without authentication stays liveness; an approved target is required
[ "$(STUB_OUT="$ACC" ACCESS_TARGETS=prod run)" = smoke_level=liveness ] && ok "Access redirect without authentication remains liveness" || bad "anon liveness"
# partial credentials fail with an actionable message, never anonymous
for partial in CF_ACCESS_CLIENT_ID=only-id CF_ACCESS_CLIENT_SECRET=only-secret; do
  env "$partial" ACCESS_TARGETS=prod STUB_OUT='200 ' DEPLOY_TARGET=prod TARGET_URL=https://x.example "$S" >/dev/null 2>"$T/err" && bad "partial credentials accepted ($partial)" || { grep -q 'must be configured together' "$T/err" && ok "partial credentials fail ($partial%%=*)" || bad "partial message"; }
done
# invalid credentials never verify: Access keeps redirecting even approved targets -> failure; a 403 -> failure
env "${TOKEN_ENV[@]}" STUB_NEED_TOKEN=other ACCESS_TARGETS=prod DEPLOY_TARGET=prod TARGET_URL=https://x.example "$S" >/dev/null 2>"$T/err" && bad "invalid token accepted" || { grep -q 'service token is invalid' "$T/err" && ok "invalid service token (still redirected) fails, not liveness" || bad "invalid msg"; }
env "${TOKEN_ENV[@]}" STUB_OUT='403 ' ACCESS_TARGETS=prod DEPLOY_TARGET=prod TARGET_URL=https://x.example "$S" >/dev/null 2>&1 && bad "403 with token accepted" || ok "403 with a token fails"
env "${TOKEN_ENV[@]}" STUB_SHA_BODY=zzzzzzz SHA_PATH=/v DEPLOY_TARGET=prod TARGET_URL=https://x.example "$S" >/dev/null 2>&1 && bad "SHA mismatch accepted with token" || ok "SHA mismatch still fails with a token"
# legacy PROBE_HEADERS keeps working (probe and sha), including several headers
: > "$STUB_LOG"
[ "$(PROBE_HEADERS=$'X-A: 1\nX-B: 2' SHA_PATH=/v STUB_SHA_BODY=abcdef1 run)" = smoke_level=content-sha ] && grep -q '^sha|X-A: 1;X-B: 2|' "$STUB_LOG" && grep -q '^probe|X-A: 1;X-B: 2|' "$STUB_LOG" && ok "legacy PROBE_HEADERS (multiple) still sent to probe and SHA endpoint" || bad "legacy PROBE_HEADERS"
# ---- exact SHA comparison (no substring matching) ----
FULL=0123456789abcdef0123456789abcdef01234567
sha() { EXPECTED_SHA="$FULL" SHA_PATH=/v STUB_SHA_BODY="$1" run; }
[ "$(sha "{\"sha\":\"$FULL\"}")" = smoke_level=content-sha ] && ok "full SHA equal -> content-sha" || bad "full equal"
sha "{\"sha\":\"${FULL:0:7}\"}" >/dev/null && ok "abbreviated SHA that is a prefix of the expected one is still accepted (compat)" || bad "abbrev compat"
sha "${FULL:0:12}" >/dev/null && ok "12-char abbreviation accepted" || bad "abbrev 12"
sha "{\"sha\":\"${FULL:0:7}fffffffffffffffffffffffffffffffff\"}" >/dev/null && bad "same first 7 chars but different full SHA accepted" || ok "full SHA differing after 7 chars fails (was a false positive)"
sha "{\"sha\":\"${FULL:0:7}fffffffffffffffffffffffffffffffff\",\"short\":\"${FULL:0:7}\"}" >/dev/null && bad "abbreviated fallback beat a mismatching full SHA" || ok "a mismatching full SHA is not rescued by an abbreviated field"
sha "build-x${FULL}y" >/dev/null && bad "substring inside a larger word accepted" || ok "SHA embedded in a longer word is not a match"
sha "${FULL}0123" >/dev/null && bad "longer hex run accepted" || ok "longer hex run (not a SHA) is not a match"
sha "{\"sha\":\"$(printf '%s' "$FULL" | tr a-f A-F)\"}" >/dev/null && ok "case-insensitive equality" || bad "case"
sha "${FULL:0:6}" >/dev/null && bad "6-char value accepted" || ok "too-short value fails"
sha "{\"sha\":\"7777777\"}" >/dev/null && bad "other abbreviated SHA accepted" || ok "non-prefix abbreviated SHA fails"
exit $fail
