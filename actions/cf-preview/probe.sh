#!/usr/bin/env bash
# Liveness probe for a Preview URL. Prints "probe=ok" or "probe=access-protected" on success.
#   2xx                                         -> ok (content is reachable)
#   3xx -> *.cloudflareaccess.com login         -> access-protected (URL exists and is NOT public;
#                                                  content cannot be verified without a service token)
#   anything else (3xx elsewhere, 4xx, 5xx, 000) -> retried, then failure
# `curl -f` is deliberately NOT used: it treats every 3xx as success.
# Env: TARGET_URL [PROBE_ATTEMPTS=8] [PROBE_SLEEP=5] [CF_ACCESS_CLIENT_ID + CF_ACCESS_CLIENT_SECRET | PROBE_HEADERS, see auth.sh]
set -euo pipefail
: "${TARGET_URL:?}"
attempts="${PROBE_ATTEMPTS:-8}"; pause="${PROBE_SLEEP:-5}"
# shellcheck source=auth.sh
. "${TOOLS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}/auth.sh"
setup_auth_headers || exit 1
hdr=("${AUTH_ARGS[@]}")
last=""
for ((i = 1; i <= attempts; i++)); do
  out="$(curl -s -o /dev/null --max-time 20 "${hdr[@]}" -w '%{http_code} %{redirect_url}' "$TARGET_URL/" || true)"
  code="${out%% *}"; loc="${out#* }"; last="$code"
  if [[ "$code" =~ ^2[0-9][0-9]$ ]]; then echo "probe=ok"; exit 0; fi
  if [[ "$code" =~ ^30[0-9]$ && "$loc" =~ ^https://[a-z0-9.-]+\.cloudflareaccess\.com/ ]]; then
    echo "probe=access-protected"; exit 0
  fi
  [ "$i" -lt "$attempts" ] && sleep "$pause"
done
echo "::error::$TARGET_URL never returned 2xx or an Access login redirect (last status: ${last:-none})" >&2
exit 1
