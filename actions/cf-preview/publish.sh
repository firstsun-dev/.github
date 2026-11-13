#!/usr/bin/env bash
# PUBLISH stage of the single delivery engine. Publishes ONLY the config build.sh audited (--config is always
# explicit); it never builds. Prints key=value lines on stdout (progress goes to stderr).
#   TARGET=preview -> `wrangler preview --name <slug> --json`              -> url=<stable Preview URL>
#   TARGET=prod    -> `wrangler versions upload` + `versions deploy @100`   -> url=<APP_URL> version_id= previous_version_id=
#   TARGET=dev     -> `wrangler deploy` of the audited config (the engine is the only publisher) -> url=<APP_URL>
#   ROLLBACK_TO=<version id> (prod only): `wrangler versions deploy <id>@100` with the same audited config.
# Env: TARGET ARTIFACT_DIR TOOLS_DIR STATE_DIR [APP_DIR unused] [OUT_FILE] [WRANGLER_CONFIG] [APP_URL] [APP_VERSION] [COMMIT_MSG]
#      preview: PREVIEW_NAME [WORKER_NAME]   dev: [DEV_ENV]
set -euo pipefail
: "${TARGET:?}" "${TOOLS_DIR:?}" "${STATE_DIR:?}"
case "$TARGET" in prod|preview|dev) ;; *) echo "::error::bad TARGET: $TARGET" >&2; exit 1;; esac
DEV_ENV="${DEV_ENV:-dev}"
[ -s "$STATE_DIR/config.path" ] || { echo "::error::nothing was built/audited for this target (no $STATE_DIR/config.path); refusing to publish" >&2; exit 1; }
CONFIG_PATH="$(cat "$STATE_DIR/config.path")"
MODE="$(cat "$STATE_DIR/config.mode")"
# The audited config must be byte-identical at publish time.
[ "$(sha256sum "$CONFIG_PATH" | cut -d' ' -f1)" = "$(cat "$STATE_DIR/config.sha")" ] || { echo "::error::the audited Wrangler config changed between audit and publish; refusing to publish" >&2; exit 1; }

TOOLS_DIR="$(cd "$TOOLS_DIR" && pwd)"  # absolute before `cd` (a relative path breaks for nested paths)
# Publish ONLY the artifact: its files are the sole input, no application source, no node_modules, no build. The credential-bearing job runs the
# pinned Wrangler of this repository's tooling against the packaged (already bundled) config.
: "${ARTIFACT_DIR:?}"
[ -d "$ARTIFACT_DIR" ] || { echo "::error::no artifact at $ARTIFACT_DIR; refusing to publish" >&2; exit 1; }
WRANGLER="${WRANGLER:-$TOOLS_DIR/node_modules/.bin/wrangler}"
[ "$CONFIG_PATH" = "$(cd "$ARTIFACT_DIR" && pwd)/wrangler.json" ] || [ "$CONFIG_PATH" = "$ARTIFACT_DIR/wrangler.json" ] || { echo "::error::the audited config is not the artifact's wrangler.json; refusing to publish" >&2; exit 1; }
# Re-verify immediately before the remote call (identity from the privileged side's own env, see engine.sh artifact_env).
node "$TOOLS_DIR/cli.mjs" verify-artifact --dir "$ARTIFACT_DIR" >/dev/null
cd "$ARTIFACT_DIR"
export CLOUDFLARE_ENV=""   # the audited config is already flattened; a named env must never be applied on top
CFG_ARGS=(--config "$CONFIG_PATH")
# A SOURCE config still has named environments: select the audited one explicitly (never via ambient env).
[ "$TARGET" = dev ] && [ "$MODE" = source ] && CFG_ARGS+=(--env "$DEV_ENV")

if [ -n "${ROLLBACK_TO:-}" ]; then
  [ "$TARGET" = prod ] || { echo "::error::ROLLBACK_TO is Production only" >&2; exit 1; }
  [[ "$ROLLBACK_TO" =~ ^[A-Za-z0-9-]+$ ]] || { echo "::error::unsafe version id: $ROLLBACK_TO" >&2; exit 1; }
  "$WRANGLER" versions deploy "$ROLLBACK_TO@100" "${CFG_ARGS[@]}" --message "Revert: smoke failed on ${GITHUB_SHA:-unknown}" --yes >&2
  echo "rolled_back_to=$ROLLBACK_TO"
  exit 0
fi

SHORT_SHA="${GITHUB_SHA:-0000000}"; SHORT_SHA="${SHORT_SHA::7}"
MSG="[$SHORT_SHA] $(printf '%s' "${COMMIT_MSG:-}" | head -1 | cut -c1-80)"

if [ "$TARGET" = preview ]; then
  : "${PREVIEW_NAME:?}" "${OUT_FILE:?}"
  [[ "$PREVIEW_NAME" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || { echo "::error::refusing unsafe preview name: $PREVIEW_NAME" >&2; exit 1; }
  : "${WORKER_NAME:?the host Worker of a Preview is set by the privileged side}"
  [[ "$WORKER_NAME" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || { echo "::error::bad preview host Worker: $WORKER_NAME" >&2; exit 1; }
  WORKER_ARGS=(--worker-name "$WORKER_NAME")
  # Stderr (progress logs) must not pollute the JSON on stdout.
  "$WRANGLER" preview --name "$PREVIEW_NAME" "${WORKER_ARGS[@]}" "${CFG_ARGS[@]}" --json > "$OUT_FILE"
  # From here on the Preview may already have been modified: a failure below is never "existing Preview unchanged".
  echo "preview_mutated=true"
  # The extractor runs on its own so its exit status is NOT swallowed by a pipeline or command substitution
  # inside `echo` (incident: `echo "url=$(extract | sed ...)"` printed `url=` and exited 0).
  if ! EXTRACTED="$(node "$TOOLS_DIR/cli.mjs" extract-url --file "$OUT_FILE")"; then
    printf '%s\n' "$EXTRACTED" | sed -n 's/^::error:://p' | head -5 | cut -c1-600 | sed 's/^/::error::/' >&2
    echo "::error::Preview publish returned no usable URL; refusing to report a deployment (Preview state is unverified)" >&2
    exit 1
  fi
  PREVIEW_URL="$(printf '%s\n' "$EXTRACTED" | sed -n 's/^preview_url=//p')"
  [[ "$PREVIEW_URL" =~ ^https://[^[:space:]]+$ ]] || { echo "::error::Preview URL missing or not https after extraction" >&2; exit 1; }
  # Pre-resolved URL (Native Dev): the published URL must be exactly the one the artifact was BUILT for.
  if [ -n "${EXPECTED_URL:-}" ] && ! node -e 'const n=(u)=>u.replace(/\/+$/,"").toLowerCase();process.exit(n(process.argv[1])===n(process.argv[2])?0:1)' "$PREVIEW_URL" "$EXPECTED_URL"; then
    echo "::error::published Preview URL $PREVIEW_URL differs from the URL this artifact was built for ($EXPECTED_URL); the build embeds the wrong URL" >&2
    exit 1
  fi
  echo "url=$PREVIEW_URL"
  exit 0
fi

if [ "$TARGET" = dev ]; then
  echo "publish_started=true"
  "$WRANGLER" deploy "${CFG_ARGS[@]}" --message "$MSG" >&2
  echo "url=${APP_URL:-}"
  exit 0
fi

echo "publish_started=true"
PREV_ID="$("$WRANGLER" deployments list "${CFG_ARGS[@]}" --json 2>/dev/null \
  | jq -r 'sort_by(.created_on) | reverse | .[0].versions | max_by(.percentage // 0) | .version_id // empty' 2>/dev/null || echo "")"
UP="$("$WRANGLER" versions upload "${CFG_ARGS[@]}" --message "$MSG" --var COMMIT_HASH:"$SHORT_SHA" --var VERSION:"${APP_VERSION:-}")"
echo "$UP" >&2
VID="$(printf '%s\n' "$UP" | grep 'Worker Version ID:' | awk '{print $NF}')"
[ -n "$VID" ] || { echo "::error::no Worker Version ID" >&2; exit 1; }
"$WRANGLER" versions deploy "$VID@100" "${CFG_ARGS[@]}" --message "$MSG" --yes >&2
echo "url=${APP_URL:-}"
echo "version_id=$VID"
echo "previous_version_id=$PREV_ID"
