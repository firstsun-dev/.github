#!/usr/bin/env bash
# Build + publish one branch Native Preview. Used for normal publish AND recovery.
# Env: APP_DIR PREVIEW_NAME BUILD_CMD TOOLS_DIR OUT_FILE [WRANGLER_CONFIG] [PREVIEW_ENV_FILE]
# Prints "preview_url=<url>" on success. Any failure exits non-zero BEFORE
# `wrangler preview` runs if build fails, so the existing Preview is untouched.
set -euo pipefail
: "${APP_DIR:?}" "${PREVIEW_NAME:?}" "${BUILD_CMD:?}" "${TOOLS_DIR:?}" "${OUT_FILE:?}"
[[ "$PREVIEW_NAME" =~ ^br-[a-z0-9-]+-[0-9a-f]{10}$ ]] || { echo "::error::refusing unsafe preview name: $PREVIEW_NAME"; exit 1; }

cd "$APP_DIR"
if [ -n "${PREVIEW_ENV_FILE:-}" ] && [ -f "$PREVIEW_ENV_FILE" ]; then
  set -a; # shellcheck disable=SC1090
  . "$PREVIEW_ENV_FILE"; set +a
fi
export CLOUDFLARE_ENV=""   # Previews are not a named wrangler env
bash -c "$BUILD_CMD" >&2

# Audit the EFFECTIVE config too: adapters (e.g. Astro) emit a redirected config at build time
# (.wrangler/deploy/config.json -> dist/.../wrangler.json) that can add bindings the source lacks.
if [ -f .wrangler/deploy/config.json ]; then
  EFFECTIVE="$(node -e 'const c=require("path").resolve(".wrangler/deploy",JSON.parse(require("fs").readFileSync(".wrangler/deploy/config.json","utf8")).configPath);console.log(c)')"
  node "$TOOLS_DIR/cli.mjs" preflight --config "$EFFECTIVE" --allow "${PREVIEW_ALLOW:-}" --deny "${PREVIEW_DENY:-}" >&2
fi

# Stderr (progress logs) must not pollute the JSON on stdout.
pnpm exec wrangler preview --name "$PREVIEW_NAME" --json > "$OUT_FILE"
node "$TOOLS_DIR/cli.mjs" extract-url --file "$OUT_FILE"
