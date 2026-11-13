#!/usr/bin/env bash
# Apply pending D1 migrations for ONE target. The same project-owned SQL (migrations_dir) is applied to the
# Production, Preview or Shared Dev D1, chosen from the SOURCE Wrangler config (`d1_databases`,
# `previews.d1_databases`, `env.<DEV_ENV>.d1_databases`).
# Prints key=value lines: pending=true|false, d1_name=...
# There is NO command override: the target D1 is always derived and validated from the source config
# (explicit ids, Preview != Production, migrations dir with .sql files) before anything remote runs.
# Correctness never depends on parsing human-readable output: `list` must exit 0 (state readable, target
# reachable) and `apply` is itself pending-only; the "No migrations to apply" text only skips a no-op apply.
# MIGRATE_MODE=precheck: READ-ONLY (two SELECTs through `wrangler d1 execute --remote --json`, parsed as JSON - never the
# human-readable `migrations list` text, which also creates the migrations table). Prints pending=true|false, d1_name, d1_id and
# changes nothing. Anything it cannot prove (unsupported migration settings, unreadable state) is never "nothing pending":
# unsupported settings print pending=true (the caller then runs the full path), a failing query exits non-zero.
# Env: TARGET(prod|preview|dev) APP_DIR TOOLS_DIR [WRANGLER_CONFIG=wrangler.toml] [D1_BINDING] [DEV_ENV=dev] [MIGRATE_MODE=apply|precheck]
set -euo pipefail
: "${TARGET:?}" "${APP_DIR:?}" "${TOOLS_DIR:?}"
case "$TARGET" in prod|preview|dev) ;; *) echo "::error::bad TARGET: $TARGET" >&2; exit 1;; esac
TOOLS_DIR="$(cd "$TOOLS_DIR" && pwd)"  # absolute before `cd "$APP_DIR"` (a relative path breaks for nested app_path)
cd "$APP_DIR"

# The pinned Wrangler of this repository's tooling runs the migration (no application dependency is installed in this credential-bearing job):
# refuse versions older than this pipeline supports.
WRANGLER="${WRANGLER:-$TOOLS_DIR/node_modules/.bin/wrangler}"
VER="$("$WRANGLER" --version | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
node "$TOOLS_DIR/cli.mjs" wrangler-check --version "${VER:-unknown}" >&2

OUT="$(node "$TOOLS_DIR/cli.mjs" migration-config --config "${WRANGLER_CONFIG:-wrangler.toml}" --target "$TARGET" \
  --binding "${D1_BINDING:-}" --dev-env "${DEV_ENV:-dev}" --out ".wrangler/migrate.$TARGET.json")"
BINDING="$(printf '%s\n' "$OUT" | sed -n 's/^d1_binding=//p')"
NAME="$(printf '%s\n' "$OUT" | sed -n 's/^d1_name=//p')"
CONF="$(printf '%s\n' "$OUT" | sed -n 's/^migrate_config=//p')"
ID="$(printf '%s\n' "$OUT" | sed -n 's/^d1_id=//p')"
echo "Target $TARGET -> D1 $NAME (binding $BINDING)" >&2

if [ "${MIGRATE_MODE:-apply}" = precheck ]; then
  TABLE="$(jq -r '.d1_databases[0].migrations_table // "d1_migrations"' "$CONF")"
  if jq -e '.d1_databases[0] | has("migrations_pattern")' "$CONF" >/dev/null || ! [[ "$TABLE" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    echo "::notice::migration settings of $NAME are not covered by the read-only precheck; running the full path." >&2
    echo "pending=true"; echo "d1_name=$NAME"; echo "d1_id=$ID"; exit 0
  fi
  # Exact physical identity BEFORE the first remote read: the database the source config names must be the one the plan expects.
  [ -z "${D1_EXPECT:-}" ] || [ "$ID" = "$D1_EXPECT" ] || { echo "::error::the source config binds D1 '$ID' for target $TARGET but the plan expects '$D1_EXPECT'; refusing (nothing was read or changed)" >&2; exit 1; }
  Q="$(mktemp -d)"; trap 'rm -rf "$Q"' EXIT
  query() { "$WRANGLER" d1 execute "$BINDING" --remote --config "$CONF" --json --yes --command "$1" > "$2" || { echo "::error::cannot read the migration state of $NAME (query failed); failing closed" >&2; exit 1; }; }
  query "SELECT name FROM sqlite_master WHERE type='table' AND name='$TABLE'" "$Q/exists.json"
  APPLIED=()
  if [ "$(jq 'if type == "array" and length == 1 then .[0].results | length else -1 end' "$Q/exists.json" 2>/dev/null)" = 1 ]; then
    query "SELECT name FROM \"$TABLE\"" "$Q/applied.json"; APPLIED=(--applied "$Q/applied.json")
  fi
  R="$(node "$TOOLS_DIR/cli.mjs" migration-pending --dir "$(jq -r '.d1_databases[0].migrations_dir' "$CONF")" --exists "$Q/exists.json" "${APPLIED[@]}")" \
    || { echo "::error::the migration state of $NAME is unreadable or malformed; failing closed" >&2; exit 1; }
  printf '%s\n' "$R" | sed 's/^/::notice::precheck /' >&2
  printf '%s\n' "$R" | grep -E '^pending='; echo "d1_name=$NAME"; echo "d1_id=$ID"
  exit 0
fi

LIST="$("$WRANGLER" d1 migrations list "$BINDING" --remote --config "$CONF" 2>&1)" || { printf '%s\n' "$LIST" >&2; echo "::error::cannot list migrations on $NAME" >&2; exit 1; }
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
"$WRANGLER" d1 migrations apply "$BINDING" --remote --config "$CONF" >&2
echo "pending=true"; echo "d1_name=$NAME"; printf '%s\n' "$SCAN"
