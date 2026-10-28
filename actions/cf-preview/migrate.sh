#!/usr/bin/env bash
# Apply pending D1 migrations for ONE target. The same project-owned SQL (migrations_dir) is applied to the
# Production, Preview or Shared Dev D1, chosen from the SOURCE Wrangler config (`d1_databases`,
# `previews.d1_databases`, `env.<DEV_ENV>.d1_databases`).
# Prints key=value lines: pending=true|false, d1_name=...
# There is NO command override: the target D1 is always derived and validated from the source config
# (explicit ids, Preview != Production, migrations dir with .sql files) before anything remote runs.
# Correctness never depends on parsing human-readable output: `list` must exit 0 (state readable, target
# reachable) and `apply` is itself pending-only; the "No migrations to apply" text only skips a no-op apply.
# Env: TARGET(prod|preview|dev) APP_DIR TOOLS_DIR [WRANGLER_CONFIG=wrangler.toml] [D1_BINDING] [DEV_ENV=dev]
set -euo pipefail
: "${TARGET:?}" "${APP_DIR:?}" "${TOOLS_DIR:?}"
case "$TARGET" in prod|preview|dev) ;; *) echo "::error::bad TARGET: $TARGET" >&2; exit 1;; esac
TOOLS_DIR="$(cd "$TOOLS_DIR" && pwd)"  # absolute before `cd "$APP_DIR"` (a relative path breaks for nested app_path)
cd "$APP_DIR"

# The project's OWN pinned Wrangler runs the migration: refuse versions older than this pipeline supports.
VER="$(pnpm exec wrangler --version | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
node "$TOOLS_DIR/cli.mjs" wrangler-check --version "${VER:-unknown}" >&2

OUT="$(node "$TOOLS_DIR/cli.mjs" migration-config --config "${WRANGLER_CONFIG:-wrangler.toml}" --target "$TARGET" \
  --binding "${D1_BINDING:-}" --dev-env "${DEV_ENV:-dev}" --out ".wrangler/migrate.$TARGET.json")"
BINDING="$(printf '%s\n' "$OUT" | sed -n 's/^d1_binding=//p')"
NAME="$(printf '%s\n' "$OUT" | sed -n 's/^d1_name=//p')"
CONF="$(printf '%s\n' "$OUT" | sed -n 's/^migrate_config=//p')"
echo "Target $TARGET -> D1 $NAME (binding $BINDING)" >&2

LIST="$(pnpm exec wrangler d1 migrations list "$BINDING" --remote --config "$CONF" 2>&1)" || { printf '%s\n' "$LIST" >&2; echo "::error::cannot list migrations on $NAME" >&2; exit 1; }
printf '%s\n' "$LIST" >&2
if printf '%s\n' "$LIST" | grep -qi 'No migrations to apply'; then
  echo "::notice::No pending migrations for $NAME; nothing applied." >&2
  echo "pending=false"; echo "d1_name=$NAME"; echo "d1_scan=none"
  exit 0
fi
# Worker rollback never reverts D1. Classify the migrations about to be applied (names read from the list output;
# only the CONSERVATIVE direction depends on that parse: no names found -> d1_scan=unknown) so a later automatic
# rollback can refuse a schema the previous Worker version may not understand.
FILES="$(printf '%s\n' "$LIST" | grep -oE '[A-Za-z0-9_.-]+\.sql' | sort -u | tr '\n' ' ' || true)"
MDIR="$(jq -r '.d1_databases[0].migrations_dir' "$CONF")"
SCAN="$(node "$TOOLS_DIR/cli.mjs" migration-scan --dir "$MDIR" --files "$FILES" 2>/dev/null)" || SCAN=$'d1_scan=unknown\nd1_scan_reason=scan failed'
pnpm exec wrangler d1 migrations apply "$BINDING" --remote --config "$CONF" >&2
echo "pending=true"; echo "d1_name=$NAME"; printf '%s\n' "$SCAN"
