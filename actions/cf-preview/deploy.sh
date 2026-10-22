#!/usr/bin/env bash
# Build + validate + publish for ONE target (the single Deploy implementation). Used for the normal
# publish AND for Preview recovery. Prints key=value lines on stdout (progress logs go to stderr).
#   TARGET=preview -> `wrangler preview --name <slug> --json`            -> url=<stable Preview URL>
#   TARGET=prod    -> `wrangler versions upload` + `versions deploy @100` -> url=<APP_URL_PROD> version_id= previous_version_id=
#   ROLLBACK_TO=<version id> (prod only): no build; `wrangler versions deploy <id>@100` with the SAME Wrangler
#   config/Worker identity resolution as the publish (config flag or the adapter's .wrangler/deploy redirect).
# Env: TARGET APP_DIR TOOLS_DIR [BUILD_CMD] [OUT_FILE] [WRANGLER_CONFIG=wrangler.toml]
#      preview: PREVIEW_NAME [WORKER_NAME] [PREVIEW_ALLOW] [PREVIEW_DENY] [PREVIEW_ENV_FILE]
#      prod:    [APP_URL_PROD] [COMMIT_MSG] [APP_VERSION]
# The build runs BEFORE any Cloudflare mutation, so a failed build or failed binding audit leaves the
# live Worker / existing Preview untouched.
set -euo pipefail
: "${TARGET:?}" "${APP_DIR:?}" "${TOOLS_DIR:?}"
case "$TARGET" in prod|preview) ;; *) echo "::error::bad TARGET: $TARGET" >&2; exit 1;; esac
CFG="${WRANGLER_CONFIG:-wrangler.toml}"

if [ "$TARGET" = preview ]; then
  : "${PREVIEW_NAME:?}"
  [[ "$PREVIEW_NAME" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || { echo "::error::refusing unsafe preview name: $PREVIEW_NAME" >&2; exit 1; }
  # Read-only: refuse BEFORE the build and before any mutation when the host Worker has no Preview URL host
  # (previews_enabled=false). Opt out (custom-domain Preview hosting) with PREVIEW_HOST_CHECK=false; an unreadable
  # answer only warns, strict post-publish URL validation below still applies.
  if [ "${PREVIEW_HOST_CHECK:-true}" != false ] && [ -n "${HOST_WORKER:-}" ]; then
    node "$TOOLS_DIR/cli.mjs" preview-host-check --worker "$HOST_WORKER" >&2
  fi
fi

cd "$APP_DIR"
# shellcheck source=envfile.sh
. "$TOOLS_DIR/envfile.sh"
export CLOUDFLARE_ENV=""   # neither Production nor Native Preview is a named wrangler env

# Same config resolution for publish and rollback: the adapter's redirect wins, else the source config.
resolve_cfg() {
  EFFECTIVE="$(node "$TOOLS_DIR/cli.mjs" effective-config --dir . | sed -n 's/^effective_config=//p')"
  CFG_ARGS=()
  if [ -z "$EFFECTIVE" ] && [ -f "$CFG" ]; then CFG_ARGS=(--config "$CFG"); fi
}

if [ -n "${ROLLBACK_TO:-}" ]; then
  [ "$TARGET" = prod ] || { echo "::error::ROLLBACK_TO is Production only" >&2; exit 1; }
  [[ "$ROLLBACK_TO" =~ ^[A-Za-z0-9-]+$ ]] || { echo "::error::unsafe version id: $ROLLBACK_TO" >&2; exit 1; }
  resolve_cfg
  pnpm exec wrangler versions deploy "$ROLLBACK_TO@100" "${CFG_ARGS[@]}" --message "Revert: smoke failed on ${GITHUB_SHA:-unknown}" --yes >&2
  echo "rolled_back_to=$ROLLBACK_TO"
  exit 0
fi
: "${BUILD_CMD:?}" "${OUT_FILE:?}"
# Preview-only secrets are data (validated, exported through quoted variables), never sourced as shell.
[ "$TARGET" != preview ] || load_env_file "${PREVIEW_ENV_FILE:-}"
bash -c "$BUILD_CMD" >&2

# Validate the EFFECTIVE config: adapters (e.g. Astro) emit a redirected config at build time
# (.wrangler/deploy/config.json -> dist/.../wrangler.json) that can add bindings the source lacks.
resolve_cfg
if [ -n "$EFFECTIVE" ]; then
  if [ "$TARGET" = preview ]; then
    node "$TOOLS_DIR/cli.mjs" preflight --config "$EFFECTIVE" --allow "${PREVIEW_ALLOW:-}" --deny "${PREVIEW_DENY:-}" >&2
  else
    node "$TOOLS_DIR/cli.mjs" audit-prod --effective "$EFFECTIVE" --source "$CFG" >&2
  fi
fi

if [ "$TARGET" = preview ]; then
  # Optional: host the Preview under another existing Worker (production Worker keeps Preview URLs off).
  WORKER_ARGS=()
  if [ -n "${WORKER_NAME:-}" ]; then
    [[ "$WORKER_NAME" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || { echo "::error::bad preview_worker_name: $WORKER_NAME" >&2; exit 1; }
    WORKER_ARGS=(--worker-name "$WORKER_NAME")
  fi
  # Stderr (progress logs) must not pollute the JSON on stdout.
  pnpm exec wrangler preview --name "$PREVIEW_NAME" "${WORKER_ARGS[@]}" "${CFG_ARGS[@]}" --json > "$OUT_FILE"
  # From here on the Preview may already have been modified: tell the workflow, so a failure below is never
  # reported as "existing Preview unchanged" and never triggers a recovery that has no URL to verify.
  echo "preview_mutated=true"
  # The extractor runs on its own so its exit status is NOT swallowed by a pipeline or command substitution
  # inside `echo` (the incident: `echo "url=$(extract | sed ...)"` printed `url=` and exited 0).
  if ! EXTRACTED="$(node "$TOOLS_DIR/cli.mjs" extract-url --file "$OUT_FILE")"; then
    printf '%s\n' "$EXTRACTED" | sed -n 's/^::error:://p' | head -5 | cut -c1-600 | sed 's/^/::error::/' >&2
    echo "::error::Preview publish returned no usable URL; refusing to report a deployment (Preview state is unverified)" >&2
    exit 1
  fi
  PREVIEW_URL="$(printf '%s\n' "$EXTRACTED" | sed -n 's/^preview_url=//p')"
  [[ "$PREVIEW_URL" =~ ^https://[^[:space:]]+$ ]] || { echo "::error::Preview URL missing or not https after extraction" >&2; exit 1; }
  echo "url=$PREVIEW_URL"
  exit 0
fi

SHORT_SHA="${GITHUB_SHA:-0000000}"; SHORT_SHA="${SHORT_SHA::7}"
MSG="[$SHORT_SHA] $(printf '%s' "${COMMIT_MSG:-}" | head -1 | cut -c1-80)"
PREV_ID="$(pnpm exec wrangler deployments list "${CFG_ARGS[@]}" --json 2>/dev/null \
  | jq -r 'sort_by(.created_on) | reverse | .[0].versions | max_by(.percentage // 0) | .version_id // empty' 2>/dev/null || echo "")"
UP="$(pnpm exec wrangler versions upload "${CFG_ARGS[@]}" --message "$MSG" --var COMMIT_HASH:"$SHORT_SHA" --var VERSION:"${APP_VERSION:-}")"
echo "$UP" >&2
VID="$(printf '%s\n' "$UP" | grep 'Worker Version ID:' | awk '{print $NF}')"
[ -n "$VID" ] || { echo "::error::no Worker Version ID" >&2; exit 1; }
pnpm exec wrangler versions deploy "$VID@100" "${CFG_ARGS[@]}" --message "$MSG" --yes >&2
echo "url=${APP_URL_PROD:-}"
echo "version_id=$VID"
echo "previous_version_id=$PREV_ID"
