#!/usr/bin/env bash
# BUILD stage of the single delivery engine (target = prod | preview | dev). Builds the deployable, resolves the
# config that will be PUBLISHED, audits that exact config and records its fingerprint. Nothing remote is mutated, so
# a failed build or audit leaves the live Worker / Preview / Dev untouched. publish.sh later publishes only this config.
# Env: TARGET APP_DIR TOOLS_DIR STATE_DIR BUILD_CMD [BUILD_ENV_FILE] [APP_VERSION] [WRANGLER_CONFIG=wrangler.toml]
#      preview: PREVIEW_NAME [HOST_WORKER] [PREVIEW_HOST_CHECK] [PREVIEW_ENV_FILE] [PREVIEW_ALLOW] [PREVIEW_DENY]
#      dev:     DEV_ENV DEV_WORKER [DEV_ALLOW] [DEV_DENY] [PROD_URL]
# Writes $STATE_DIR/{config.path,config.mode,config.sha,artifact.sha}.
set -euo pipefail
: "${TARGET:?}" "${APP_DIR:?}" "${TOOLS_DIR:?}" "${STATE_DIR:?}" "${BUILD_CMD:?}"
case "$TARGET" in prod|preview|dev) ;; *) echo "::error::bad TARGET: $TARGET" >&2; exit 1;; esac
CFG="${WRANGLER_CONFIG:-wrangler.toml}"
DEV_ENV="${DEV_ENV:-dev}"
mkdir -p "$STATE_DIR"

if [ "$TARGET" = preview ]; then
  : "${PREVIEW_NAME:?}"
  [[ "$PREVIEW_NAME" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || { echo "::error::refusing unsafe preview name: $PREVIEW_NAME" >&2; exit 1; }
  # Read-only: refuse BEFORE the build and before any mutation when the host Worker has no Preview URL host
  # (previews_enabled=false). PREVIEW_HOST_CHECK=false opts out (custom-domain hosting); an unreadable answer only
  # warns and the strict post-publish URL validation applies.
  if [ "${PREVIEW_HOST_CHECK:-true}" != false ] && [ -n "${HOST_WORKER:-}" ]; then
    node "$TOOLS_DIR/cli.mjs" preview-host-check --worker "$HOST_WORKER" >&2
  fi
fi

cd "$APP_DIR"
# shellcheck source=envfile.sh
. "$TOOLS_DIR/envfile.sh"
# Application env is DATA scoped to this process: it is validated against the reserved control names by
# parse-env.mjs and is never written to GITHUB_ENV, so it cannot influence later stages.
[ "$TARGET" != preview ] || load_env_file "${PREVIEW_ENV_FILE:-}"
load_env_file "${BUILD_ENV_FILE:-}"
export VITE_APP_ENV="$TARGET" VITE_APP_VERSION="${APP_VERSION:-}"
[ "$TARGET" = dev ] && export CLOUDFLARE_ENV="$DEV_ENV" || export CLOUDFLARE_ENV=""

bash -eo pipefail -c "$BUILD_CMD" >&2

# The config that WILL be published: the adapter's redirect (e.g. Astro: .wrangler/deploy/config.json) wins,
# else the project's source config.
EFFECTIVE="$(node "$TOOLS_DIR/cli.mjs" effective-config --dir . | sed -n 's/^effective_config=//p')"
if [ -n "$EFFECTIVE" ]; then
  MODE=effective; CONFIG_PATH="$EFFECTIVE"
else
  MODE=source; CONFIG_PATH="$APP_DIR/$CFG"
  [ -f "$CONFIG_PATH" ] || { echo "::error::no Wrangler config: neither an adapter-generated config nor $CFG" >&2; exit 1; }
fi

case "$TARGET" in
  preview)
    # Preview bindings / auth isolation on the config that will be published.
    if [ "$MODE" = effective ]; then
      node "$TOOLS_DIR/cli.mjs" preflight --config "$CONFIG_PATH" --allow "${PREVIEW_ALLOW:-}" --deny "${PREVIEW_DENY:-}" >&2
    fi;;
  prod)
    if [ "$MODE" = effective ]; then
      node "$TOOLS_DIR/cli.mjs" audit-prod --effective "$CONFIG_PATH" --source "$APP_DIR/$CFG" >&2
    fi;;
  dev)
    # Worker identity + resource policy of the Dev config that will be published (mode=source audits env.<DEV_ENV>).
    if [ "$MODE" = effective ]; then DEV_EFFECTIVE="$CONFIG_PATH"; else DEV_EFFECTIVE=""; fi
    node "$TOOLS_DIR/cli.mjs" dev-config-check --effective "$DEV_EFFECTIVE" --source "$APP_DIR/$CFG" --worker "${DEV_WORKER:-}" \
      --deny "${DEV_DENY:-}" --dev-env "$DEV_ENV" --allow "${DEV_ALLOW:-}" --prod-url "${PROD_URL:-}" >&2;;
esac

printf '%s' "$CONFIG_PATH" > "$STATE_DIR/config.path"
printf '%s' "$MODE" > "$STATE_DIR/config.mode"
sha256sum "$CONFIG_PATH" | cut -d' ' -f1 > "$STATE_DIR/config.sha"
node "$TOOLS_DIR/cli.mjs" artifact-digest --config "$CONFIG_PATH" | sed -n 's/^artifact_digest=//p' > "$STATE_DIR/artifact.sha"
echo "config_path=$CONFIG_PATH"
echo "config_mode=$MODE"
