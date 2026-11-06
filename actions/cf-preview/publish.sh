#!/usr/bin/env bash
# PUBLISH stage of the single delivery engine. Publishes ONLY the config build.sh audited (--config is always
# explicit); it never builds. Prints key=value lines on stdout (progress goes to stderr).
#   TARGET=preview -> `wrangler preview --name <slug> --json`              -> url=<stable Preview URL>
#   TARGET=prod    -> `wrangler versions upload` + `versions deploy @100`   -> url=<APP_URL> version_id= previous_version_id=
#   TARGET=dev     -> `wrangler deploy` (built in) or the caller's PUBLISH_CMD -> url=<APP_URL>
#   ROLLBACK_TO=<version id> (prod only): `wrangler versions deploy <id>@100` with the same audited config.
# Env: TARGET APP_DIR TOOLS_DIR STATE_DIR [OUT_FILE] [WRANGLER_CONFIG] [APP_URL] [APP_VERSION] [COMMIT_MSG]
#      preview: PREVIEW_NAME [WORKER_NAME]   dev: [PUBLISH_CMD] [DEV_ENV]
set -euo pipefail
: "${TARGET:?}" "${APP_DIR:?}" "${TOOLS_DIR:?}" "${STATE_DIR:?}"
case "$TARGET" in prod|preview|dev) ;; *) echo "::error::bad TARGET: $TARGET" >&2; exit 1;; esac
DEV_ENV="${DEV_ENV:-dev}"
[ -s "$STATE_DIR/config.path" ] || { echo "::error::nothing was built/audited for this target (no $STATE_DIR/config.path); refusing to publish" >&2; exit 1; }
CONFIG_PATH="$(cat "$STATE_DIR/config.path")"
MODE="$(cat "$STATE_DIR/config.mode")"
# The audited config must be byte-identical at publish time.
[ "$(sha256sum "$CONFIG_PATH" | cut -d' ' -f1)" = "$(cat "$STATE_DIR/config.sha")" ] || { echo "::error::the audited Wrangler config changed between audit and publish; refusing to publish" >&2; exit 1; }

TOOLS_DIR="$(cd "$TOOLS_DIR" && pwd)"  # absolute before `cd "$APP_DIR"` (a relative path breaks for nested app_path)
cd "$APP_DIR"
export CLOUDFLARE_ENV=""   # the audited config is already flattened; a named env must never be applied on top
CFG_ARGS=(--config "$CONFIG_PATH")
# A SOURCE config still has named environments: select the audited one explicitly (never via ambient env).
[ "$TARGET" = dev ] && [ "$MODE" = source ] && CFG_ARGS+=(--env "$DEV_ENV")

if [ -n "${ROLLBACK_TO:-}" ]; then
  [ "$TARGET" = prod ] || { echo "::error::ROLLBACK_TO is Production only" >&2; exit 1; }
  [[ "$ROLLBACK_TO" =~ ^[A-Za-z0-9-]+$ ]] || { echo "::error::unsafe version id: $ROLLBACK_TO" >&2; exit 1; }
  pnpm exec wrangler versions deploy "$ROLLBACK_TO@100" "${CFG_ARGS[@]}" --message "Revert: smoke failed on ${GITHUB_SHA:-unknown}" --yes >&2
  echo "rolled_back_to=$ROLLBACK_TO"
  exit 0
fi

SHORT_SHA="${GITHUB_SHA:-0000000}"; SHORT_SHA="${SHORT_SHA::7}"
MSG="[$SHORT_SHA] $(printf '%s' "${COMMIT_MSG:-}" | head -1 | cut -c1-80)"

if [ "$TARGET" = preview ]; then
  : "${PREVIEW_NAME:?}" "${OUT_FILE:?}"
  [[ "$PREVIEW_NAME" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || { echo "::error::refusing unsafe preview name: $PREVIEW_NAME" >&2; exit 1; }
  WORKER_ARGS=()
  if [ -n "${WORKER_NAME:-}" ]; then
    [[ "$WORKER_NAME" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || { echo "::error::bad preview_worker_name: $WORKER_NAME" >&2; exit 1; }
    WORKER_ARGS=(--worker-name "$WORKER_NAME")
  fi
  # Stderr (progress logs) must not pollute the JSON on stdout.
  pnpm exec wrangler preview --name "$PREVIEW_NAME" "${WORKER_ARGS[@]}" "${CFG_ARGS[@]}" --json > "$OUT_FILE"
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
  if [ -z "${PUBLISH_CMD:-}" ]; then
    pnpm exec wrangler deploy "${CFG_ARGS[@]}" --message "$MSG" >&2
  else
    # The caller's publish command is PUBLISH-ONLY. It runs with the audited config exported and every Wrangler call it makes goes through
    # wrangler-guard.sh, which refuses (before the real Wrangler runs) any non-publish subcommand, any --env / --name / non-audited --config,
    # and any call made after the audited config or artifact changed. The post-run checks below stay as the second, independent layer.
    GUARD="$STATE_DIR/guard"; rm -rf "$GUARD"; mkdir -p "$GUARD/bin"
    REAL_BIN="$PWD/node_modules/.bin/wrangler"; PROJECT_BIN_MOVED=""
    cp "$TOOLS_DIR/wrangler-guard.sh" "$GUARD/bin/wrangler"; chmod +x "$GUARD/bin/wrangler"
    if [ -e "$REAL_BIN" ] || [ -L "$REAL_BIN" ]; then
      mv "$REAL_BIN" "$REAL_BIN.cfv2-real"; cp "$GUARD/bin/wrangler" "$REAL_BIN"; PROJECT_BIN_MOVED=1; REAL_WRANGLER="$REAL_BIN.cfv2-real"
    else
      REAL_WRANGLER="$(PATH="${PATH//$GUARD\/bin:/}" command -v wrangler || echo /nonexistent/wrangler)"
    fi
    restore_bin() { [ -z "$PROJECT_BIN_MOVED" ] || { rm -f "$REAL_BIN"; mv "$REAL_BIN.cfv2-real" "$REAL_BIN"; PROJECT_BIN_MOVED=""; }; }
    trap restore_bin EXIT
    CENV=""; [ "$MODE" != source ] || CENV="$DEV_ENV"   # a flattened audited config never takes a named env; a SOURCE config's audited env is DEV_ENV
    set +e
    PATH="$GUARD/bin:$PATH" CFV2_REAL_WRANGLER="$REAL_WRANGLER" CFV2_GUARD_DIR="$GUARD" CFV2_STATE_DIR="$STATE_DIR" CFV2_TOOLS_DIR="$TOOLS_DIR" \
      CF_AUDITED_CONFIG="$CONFIG_PATH" CLOUDFLARE_ENV="$CENV" bash -eo pipefail -c "$PUBLISH_CMD" >&2
    RC=$?
    set -e
    restore_bin
    if [ "$RC" -ne 0 ]; then
      if [ -f "$GUARD/blocked" ] && [ ! -f "$GUARD/mutating" ]; then
        # Refused by the guard before any Wrangler call could change the Worker.
        echo "publish_blocked=true"
        echo "::error::dev_deploy_cmd was refused before any Wrangler publish call was made ($(sed -n 's/^code=//p' "$GUARD/blocked")); Dev was not changed by Wrangler" >&2
      fi
      exit 1
    fi
    [ -f "$GUARD/mutating" ] || echo "::warning::dev_deploy_cmd made no Wrangler publish call; nothing was published through the audited config" >&2
    # Integrity after the fact: from here on a failure means the Worker MAY already have been replaced - never "unchanged".
    integrity_fail() { echo "publish_integrity_failed=true"; echo "::error::$1" >&2; exit 1; }
    [ "$(sha256sum "$CONFIG_PATH" | cut -d' ' -f1)" = "$(cat "$STATE_DIR/config.sha")" ] || integrity_fail "the publish command modified the audited Wrangler config; Dev is potentially published but UNVERIFIED"
    NOW="$(node "$TOOLS_DIR/cli.mjs" artifact-digest --config "$CONFIG_PATH" | sed -n 's/^artifact_digest=//p')"
    [ "$NOW" = "$(cat "$STATE_DIR/artifact.sha")" ] || integrity_fail "the publish command rebuilt or replaced the audited artifact; Dev is potentially published from an unaudited build and UNVERIFIED"
  fi
  echo "url=${APP_URL:-}"
  exit 0
fi

echo "publish_started=true"
PREV_ID="$(pnpm exec wrangler deployments list "${CFG_ARGS[@]}" --json 2>/dev/null \
  | jq -r 'sort_by(.created_on) | reverse | .[0].versions | max_by(.percentage // 0) | .version_id // empty' 2>/dev/null || echo "")"
UP="$(pnpm exec wrangler versions upload "${CFG_ARGS[@]}" --message "$MSG" --var COMMIT_HASH:"$SHORT_SHA" --var VERSION:"${APP_VERSION:-}")"
echo "$UP" >&2
VID="$(printf '%s\n' "$UP" | grep 'Worker Version ID:' | awk '{print $NF}')"
[ -n "$VID" ] || { echo "::error::no Worker Version ID" >&2; exit 1; }
pnpm exec wrangler versions deploy "$VID@100" "${CFG_ARGS[@]}" --message "$MSG" --yes >&2
echo "url=${APP_URL:-}"
echo "version_id=$VID"
echo "previous_version_id=$PREV_ID"
