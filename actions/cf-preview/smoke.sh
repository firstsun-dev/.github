#!/usr/bin/env bash
# Smoke verification of the deployed URL. Fails closed; prints smoke_level=liveness|content|content-sha on success.
#   no URL                      -> FAIL (a deployment is never "verified" without a probe)
#   2xx                         -> content (then sha_path / smoke_cmd must pass too); content-sha when sha_path matched
#   redirect to *.cloudflareaccess.com -> liveness ONLY, and only for targets listed in ACCESS_TARGETS
#                               (explicit per-target approval; default none). Content/SHA is NOT verified, whatever
#                               smoke_cmd does: content-level verification of an Access-protected target needs an
#                               authenticated probe (CF_ACCESS_CLIENT_ID + CF_ACCESS_CLIENT_SECRET, or PROBE_HEADERS; auth.sh), which then
#                               sees the application's own 2xx.
#   anything else               -> FAIL
# Only content / content-sha count as "verified" (engine.sh); liveness is published-but-unverified.
# Env: TARGET_URL DEPLOY_TARGET(prod|preview|dev) [SMOKE_ENV_FILE] EXPECTED_SHA TOOLS_DIR [SHA_PATH] [SMOKE_CMD] [ACCESS_TARGETS]
#      [PREVIEW_ENV_FILE] [SHA_ATTEMPTS=6] [SHA_SLEEP=5]
set -euo pipefail
: "${DEPLOY_TARGET:?}" "${TOOLS_DIR:?}"
[ -n "${TARGET_URL:-}" ] || { echo "::error::no URL to smoke-test ($DEPLOY_TARGET): set app_url_prod / a deploy URL; a deployment without a probe is not verified" >&2; exit 1; }
# shellcheck source=envfile.sh
. "$TOOLS_DIR/envfile.sh"
[ "$DEPLOY_TARGET" != preview ] || load_env_file "${PREVIEW_ENV_FILE:-}"
# Smoke-only variables live in their own file and are exported ONLY here (never into GITHUB_ENV), so they cannot
# overwrite the target build environment of a later step such as a recovery rebuild.
load_env_file "${SMOKE_ENV_FILE:-}"

# Reads the SHA endpoint body on stdin; compares the served value, never a substring of the page.
# Tokens are whole alphanumeric words that are 7-40 hex characters. A full 40-char value must EQUAL EXPECTED_SHA;
# an abbreviated value (7-39 chars) is accepted only as an exact PREFIX of it, and only when the endpoint serves no full SHA.
sha_matches() {
  local exp tok full=0 full_ok=0 abbr_ok=0
  exp="$(printf '%s' "$EXPECTED_SHA" | tr 'A-F' 'a-f')"
  while IFS= read -r tok; do
    tok="$(printf '%s' "$tok" | tr 'A-F' 'a-f')"
    [[ "$tok" =~ ^[0-9a-f]{7,40}$ ]] || continue
    if [ "${#tok}" -eq 40 ]; then
      full=1; [ "$tok" = "$exp" ] && full_ok=1
    elif [[ "$exp" == "$tok"* ]]; then
      abbr_ok=1
    fi
  done < <(grep -oE '[[:alnum:]]+' || true)
  if [ "$full" = 1 ]; then [ "$full_ok" = 1 ]; else [ "$abbr_ok" = 1 ]; fi
}

result="$("$TOOLS_DIR/probe.sh")"
echo "$result" >&2
level=content
# shellcheck source=auth.sh
. "$TOOLS_DIR/auth.sh"
setup_auth_headers || exit 1   # partial service token -> actionable failure, never anonymous
hdr=("${AUTH_ARGS[@]}")
if [ "$result" = "probe=access-protected" ]; then
  if [ "$AUTH_CONFIGURED" = true ]; then
    echo "::error::$TARGET_URL still redirects to Cloudflare Access although CF_ACCESS_CLIENT_ID/SECRET were sent: the service token is invalid, expired or not allowed by the Access policy; not verified" >&2
    exit 1
  fi
  case " ${ACCESS_TARGETS:-} " in
    *" $DEPLOY_TARGET "*) ;;
    *) echo "::error::$TARGET_URL redirects to Cloudflare Access; that is not application verification and Access is not approved for target '$DEPLOY_TARGET' (access_protected_targets)" >&2; exit 1;;
  esac
  level=liveness
  export PREVIEW_ACCESS_PROTECTED=true
  echo "::notice::Target is protected by Cloudflare Access (approved for $DEPLOY_TARGET): liveness only; content/SHA NOT verified (configure an authenticated probe via smoke_env PROBE_HEADERS for content verification)" >&2
elif [ -n "${SHA_PATH:-}" ]; then
  : "${EXPECTED_SHA:?}"
  found=0
  for ((i = 0; i < ${SHA_ATTEMPTS:-6}; i++)); do
    if curl -fsS --max-time 20 "${hdr[@]}" "$TARGET_URL$SHA_PATH" | sha_matches; then found=1; break; fi
    sleep "${SHA_SLEEP:-5}"
  done
  [ "$found" = 1 ] || { echo "::error::$TARGET_URL does not serve $EXPECTED_SHA at $SHA_PATH" >&2; exit 1; }
  level=content-sha
fi
[ -z "${SMOKE_CMD:-}" ] || bash -c "$SMOKE_CMD" >&2
echo "smoke_level=$level"
