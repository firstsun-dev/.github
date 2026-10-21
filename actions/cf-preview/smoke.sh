#!/usr/bin/env bash
# Smoke verification of the deployed URL. Fails closed; prints smoke_level=content|liveness on success.
#   no URL                      -> FAIL (a deployment is never "verified" without a probe)
#   2xx                         -> content (then sha_path / smoke_cmd must pass too)
#   redirect to *.cloudflareaccess.com -> liveness ONLY, and only for targets listed in ACCESS_TARGETS
#                               (explicit per-target approval; default none). Content/SHA is NOT verified.
#   anything else               -> FAIL
# Env: TARGET_URL DEPLOY_TARGET(prod|preview) EXPECTED_SHA TOOLS_DIR [SHA_PATH] [SMOKE_CMD] [ACCESS_TARGETS]
#      [PREVIEW_ENV_FILE] [SHA_ATTEMPTS=6] [SHA_SLEEP=5]
set -euo pipefail
: "${DEPLOY_TARGET:?}" "${TOOLS_DIR:?}"
[ -n "${TARGET_URL:-}" ] || { echo "::error::no URL to smoke-test ($DEPLOY_TARGET): set app_url_prod / a deploy URL; a deployment without a probe is not verified" >&2; exit 1; }
# shellcheck source=envfile.sh
. "$TOOLS_DIR/envfile.sh"
[ "$DEPLOY_TARGET" != preview ] || load_env_file "${PREVIEW_ENV_FILE:-}"

result="$("$TOOLS_DIR/probe.sh")"
echo "$result" >&2
level=content
if [ "$result" = "probe=access-protected" ]; then
  case " ${ACCESS_TARGETS:-} " in
    *" $DEPLOY_TARGET "*) ;;
    *) echo "::error::$TARGET_URL redirects to Cloudflare Access; that is not application verification and Access is not approved for target '$DEPLOY_TARGET' (access_protected_targets)" >&2; exit 1;;
  esac
  level=liveness
  export PREVIEW_ACCESS_PROTECTED=true
  echo "::notice::Target is protected by Cloudflare Access (approved for $DEPLOY_TARGET): liveness only; content/SHA NOT verified" >&2
elif [ -n "${SHA_PATH:-}" ]; then
  : "${EXPECTED_SHA:?}"
  found=0
  for ((i = 0; i < ${SHA_ATTEMPTS:-6}; i++)); do
    if curl -fsS --max-time 20 "$TARGET_URL$SHA_PATH" | grep -q "${EXPECTED_SHA:0:7}"; then found=1; break; fi
    sleep "${SHA_SLEEP:-5}"
  done
  [ "$found" = 1 ] || { echo "::error::$TARGET_URL does not serve $EXPECTED_SHA at $SHA_PATH" >&2; exit 1; }
fi
[ -z "${SMOKE_CMD:-}" ] || bash -c "$SMOKE_CMD" >&2
echo "smoke_level=$level"
