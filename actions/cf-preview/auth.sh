#!/usr/bin/env bash
# Source this file. ONE mechanism for the HTTP request headers that authenticate smoke traffic (probe + sha_path).
#   setup_auth_headers   sets AUTH_ARGS (curl args), AUTH_CONFIGURED (true when a Cloudflare Access service token pair
#                        is configured) and, when any header exists, AUTH_FILE (0600 temp file, removed on exit).
# Sources, combined: CF_ACCESS_CLIENT_ID + CF_ACCESS_CLIENT_SECRET (standard Access service token; BOTH or neither)
# and the legacy PROBE_HEADERS (newline separated "Name: value").
# Headers are handed to curl as `-H @file`, never as argv, so the secret is not visible in `ps` or in logs, and the
# values are registered as masked. No redirect is ever followed with them (callers do not use -L).
setup_auth_headers() {
  AUTH_ARGS=(); AUTH_FILE=""; AUTH_CONFIGURED=false
  local id="${CF_ACCESS_CLIENT_ID:-}" sec="${CF_ACCESS_CLIENT_SECRET:-}"
  if [ -n "$id" ] || [ -n "$sec" ]; then
    if [ -z "$id" ] || [ -z "$sec" ]; then
      echo "::error::CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET must be configured together (only one is set); refusing to fall back to anonymous access" >&2
      return 1
    fi
    case "$id$sec" in *$'\n'*|*$'\r'*) echo "::error::CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET must be single-line values" >&2; return 1;; esac
    echo "::add-mask::$id" >&2; echo "::add-mask::$sec" >&2
    AUTH_CONFIGURED=true
  fi
  [ "$AUTH_CONFIGURED" = true ] || [ -n "${PROBE_HEADERS:-}" ] || return 0
  AUTH_FILE="$(umask 077; mktemp)"
  # shellcheck disable=SC2064
  trap "rm -f '$AUTH_FILE'" EXIT
  {
    [ "$AUTH_CONFIGURED" != true ] || printf 'CF-Access-Client-Id: %s\nCF-Access-Client-Secret: %s\n' "$id" "$sec"
    printf '%s\n' "${PROBE_HEADERS:-}" | sed '/^[[:space:]]*$/d'
  } > "$AUTH_FILE"
  # shellcheck disable=SC2034  # read by the sourcing script
  AUTH_ARGS=(-H "@$AUTH_FILE")
}
