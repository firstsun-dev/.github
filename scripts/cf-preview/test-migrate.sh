#!/usr/bin/env bash
# Behavioural test of migrate.sh with a stub `pnpm`/wrangler: target D1 selection, pending-only, no-op skip.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/app/db/migrations" && echo "select 1;" > "$T/app/db/migrations/0001.sql"
cat > "$T/bin/pnpm" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
# remember which database the config handed to wrangler names
for i in "$@"; do [ -f "$i" ] && grep -o '"database_id": "[^"]*"' "$i" >> "$STUB_LOG"; done
[ "$3" = "--version" ] && { echo "${STUB_WRANGLER_VERSION:-4.140.0}"; exit 0; }
[ "$3 $4 $5" = "d1 migrations list" ] && { [ "${STUB_PENDING:-1}" = 1 ] && echo "Migrations to be applied: 0002.sql" || echo "No migrations to apply!"; [ "${STUB_LIST_FAIL:-}" = 1 ] && exit 3; }
[ "$3 $4" = "d1 execute" ] && {
  [ "${STUB_PRECHECK_FAIL:-}" = 1 ] && exit 4
  case "$*" in *sqlite_master*) [ "${STUB_NO_TABLE:-}" = 1 ] && echo '[{"results":[],"success":true}]' || echo '[{"results":[{"name":"d1_migrations"}],"success":true}]';;
    *) echo "[{\"results\":[${STUB_ROWS-{\"name\":\"0001.sql\"\}}],\"success\":true}]";; esac; exit 0; }
[ "$3 $4 $5" = "d1 migrations apply" ] && [ "${STUB_APPLY_FAIL:-}" = 1 ] && exit 7
exit 0
STUB
chmod +x "$T/bin/pnpm"
cat > "$T/app/wrangler.toml" <<'TOML'
name = "x"
[[d1_databases]]
binding = "DB"
database_name = "prod-db"
database_id = "PROD-ID"
migrations_dir = "db/migrations"
[previews]
[[previews.d1_databases]]
binding = "DB"
database_name = "prev-db"
database_id = "PREV-ID"
migrations_dir = "db/migrations"
[env.dev]
[[env.dev.d1_databases]]
binding = "DB"
database_name = "dev-db"
database_id = "DEV-ID"
migrations_dir = "db/migrations"
[[env.qa.d1_databases]]
binding = "DB"
database_name = "qa-db"
database_id = "QA-ID"
migrations_dir = "db/migrations"
TOML
export PATH="$T/bin:$PATH" STUB_LOG="$T/log" TOOLS_DIR="$ROOT/actions/cf-preview" APP_DIR="$T/app"
fail=0; ok(){ echo "PASS: $1"; }; bad(){ echo "FAIL: $1"; fail=1; }
M="$ROOT/actions/cf-preview/migrate.sh"

: > "$T/log"; OUT="$(TARGET=prod "$M" 2>/dev/null)"
echo "$OUT" | grep -qx 'pending=true' && ok "prod: applies pending migrations" || bad "prod pending"
grep -q '"database_id": "PROD-ID"' "$T/log" && ! grep -q 'PREV-ID\|DEV-ID' "$T/log" && ok "prod: wrangler sees ONLY the Production D1" || bad "prod D1 isolation"
grep -q 'migrations apply DB --remote' "$T/log" && ok "prod: uses remote migrations apply" || bad "prod apply"
# ---- D1 compatibility facts for a later Worker rollback (pending file names come from the list output, SQL from migrations_dir)
echo "select 1;" > "$T/app/db/migrations/0002.sql"
: > "$T/log"; OUT="$(TARGET=prod "$M" 2>/dev/null)"
echo "$OUT" | grep -qx 'd1_scan=non-additive' && ok "pending non-additive SQL (0002.sql) -> d1_scan=non-additive" || bad "scan non-additive ($OUT)"
echo "CREATE TABLE t (id INTEGER);" > "$T/app/db/migrations/0002.sql"
: > "$T/log"; OUT="$(TARGET=prod "$M" 2>/dev/null)"
echo "$OUT" | grep -qx 'd1_scan=additive' && ok "pending additive SQL -> d1_scan=additive" || bad "scan additive ($OUT)"
rm "$T/app/db/migrations/0002.sql"
: > "$T/log"; OUT="$(TARGET=prod "$M" 2>/dev/null)"
echo "$OUT" | grep -qx 'd1_scan=unknown' && ok "a pending file that cannot be read -> d1_scan=unknown (fail closed), migration still applies" || bad "scan unknown ($OUT)"
echo "$OUT" | grep -qx 'pending=true' || bad "unknown scan must not stop the migration"
: > "$T/log"; OUT="$(STUB_PENDING=0 TARGET=prod "$M" 2>/dev/null)"
echo "$OUT" | grep -qx 'd1_scan=none' && ok "nothing pending -> d1_scan=none" || bad "scan none ($OUT)"

: > "$T/log"; OUT="$(TARGET=preview "$M" 2>/dev/null)"
grep -q '"database_id": "PREV-ID"' "$T/log" && ! grep -q 'PROD-ID\|DEV-ID' "$T/log" && ok "preview: targets previews.d1_databases, not Production and not env.dev" || bad "preview D1 selection"
echo "$OUT" | grep -qx 'd1_name=prev-db' && ok "preview: reports the Preview database" || bad "preview d1_name"
grep -o '"migrations_dir": "[^"]*"' "$T/app/.wrangler/migrate.preview.json" | grep -q "$T/app/db/migrations" && ok "preview: same project-owned migrations_dir as prod" || bad "migrations_dir"

: > "$T/log"; OUT="$(TARGET=dev "$M" 2>/dev/null)"
grep -q '"database_id": "DEV-ID"' "$T/log" && ! grep -q 'PROD-ID\|PREV-ID' "$T/log" && ok "dev: targets env.dev.d1_databases, not Production and not previews" || bad "dev D1 selection"
echo "$OUT" | grep -qx 'd1_name=dev-db' && ok "dev: reports the Dev database" || bad "dev d1_name"
: > "$T/log"; DEV_ENV=qa TARGET=dev "$M" >/dev/null 2>&1
grep -q '"database_id": "QA-ID"' "$T/log" && ! grep -q 'DEV-ID' "$T/log" && ok "dev: DEV_ENV selects the named Wrangler environment" || bad "DEV_ENV"
DEV_ENV=missing TARGET=dev "$M" >/dev/null 2>&1 && bad "dev without env D1 accepted (Production fallback?)" || ok "dev: an environment without D1 fails closed (no top-level/Production fallback)"

: > "$T/log"; OUT="$(STUB_PENDING=0 TARGET=preview "$M" 2>/dev/null)"
echo "$OUT" | grep -qx 'pending=false' && ok "no pending migration: succeeds as a no-op" || bad "no-op skip"
grep -q 'migrations apply' "$T/log" && bad "apply ran with nothing pending" || ok "no pending migration: apply not run"

STUB_APPLY_FAIL=1 TARGET=prod "$M" >/dev/null 2>&1 && bad "failed apply must fail the job" || ok "apply failure fails Migration (blocks Deploy)"
STUB_LIST_FAIL=1 STUB_PENDING=0 TARGET=prod "$M" >/dev/null 2>&1 && bad "failed list must fail" || ok "cannot read migration state: fails closed"

# ---- MIGRATE_MODE=precheck: read-only (SELECT only, JSON parsed), never `list` / `apply`
: > "$T/log"; OUT="$(MIGRATE_MODE=precheck TARGET=preview D1_EXPECT=PREV-ID "$M" 2>/dev/null)"
echo "$OUT" | grep -qx 'pending=false' && echo "$OUT" | grep -qx 'd1_id=PREV-ID' && ok "precheck: every migration file recorded -> pending=false, bound to the Preview D1 id" || bad "precheck none ($OUT)"
grep -q 'migrations apply\|migrations list' "$T/log" && bad "precheck ran list/apply" || ok "precheck never runs migrations list (it creates the table) or apply"
[ "$(grep -c 'd1 execute' "$T/log")" = 2 ] && ! grep 'd1 execute' "$T/log" | grep -qi 'insert\|update\|create\|delete\|drop\|alter' && ok "precheck: exactly two read-only SELECTs" || bad "precheck queries"
grep -q '"database_id": "PREV-ID"' "$T/log" && ! grep -q 'PROD-ID\|DEV-ID' "$T/log" && ok "precheck: reads ONLY the target's D1" || bad "precheck D1 isolation"
echo "select 1;" > "$T/app/db/migrations/0002.sql"
OUT="$(MIGRATE_MODE=precheck TARGET=preview "$M" 2>/dev/null)"; echo "$OUT" | grep -qx 'pending=true' && ok "precheck: an unrecorded file (earlier failed run) -> pending=true" || bad "precheck pending ($OUT)"
rm "$T/app/db/migrations/0002.sql"
OUT="$(STUB_NO_TABLE=1 MIGRATE_MODE=precheck TARGET=preview "$M" 2>/dev/null)"; echo "$OUT" | grep -qx 'pending=true' && ok "precheck: no migrations table -> pending=true" || bad "precheck no table ($OUT)"
echo "select 1;" > "$T/app/db/migrations/0002_add status.sql"; echo "select 1;" > "$T/app/db/migrations/0003_調整.sql"
OUT="$(MIGRATE_MODE=precheck TARGET=preview "$M" 2>/dev/null)"; echo "$OUT" | grep -qx 'pending=true' && ok "precheck: unrecorded files with a space / Unicode name -> pending=true" || bad "precheck odd names ($OUT)"
OUT="$(STUB_ROWS='{"name":"0001.sql"},{"name":"0002_add status.sql"},{"name":"0003_調整.sql"}' MIGRATE_MODE=precheck TARGET=preview "$M" 2>/dev/null)"; echo "$OUT" | grep -qx 'pending=false' && ok "precheck: all files recorded (space / Unicode names included) -> pending=false" || bad "precheck odd names applied ($OUT)"
rm "$T/app/db/migrations/0002_add status.sql" "$T/app/db/migrations/0003_調整.sql"
mkdir "$T/app/db/migrations/x.sql"; OUT="$(MIGRATE_MODE=precheck TARGET=preview "$M" 2>/dev/null)"; echo "$OUT" | grep -qx 'pending=true' && ok "precheck: an entry it cannot classify (directory x.sql) -> pending=true, full path" || bad "precheck ambiguous ($OUT)"
rmdir "$T/app/db/migrations/x.sql"
MIGRATE_MODE=precheck TARGET=preview D1_EXPECT=OTHER-ID "$M" >/dev/null 2>&1 && bad "precheck accepted a foreign D1" || ok "precheck: the config's D1 differs from the plan's -> fails before any query"
STUB_PRECHECK_FAIL=1 MIGRATE_MODE=precheck TARGET=preview "$M" >/dev/null 2>&1 && bad "precheck failure read as none" || ok "precheck: a failing query exits non-zero (never read as nothing pending)"
STUB_ROWS='"x"' MIGRATE_MODE=precheck TARGET=preview "$M" >/dev/null 2>&1 && bad "malformed rows read as none" || ok "precheck: malformed rows fail closed"
cp "$T/app/wrangler.toml" "$T/wr.bak"; sed -i 's|^migrations_dir = "db/migrations"$|&\nmigrations_pattern = "db/migrations/*.sql"|' "$T/app/wrangler.toml"
: > "$T/log"; OUT="$(MIGRATE_MODE=precheck TARGET=preview "$M" 2>/dev/null)"
echo "$OUT" | grep -qx 'pending=true' && ! grep -q 'd1 execute' "$T/log" && ok "precheck: a custom migrations_pattern -> conservative pending=true before any remote read" || bad "precheck pattern ($OUT)"
cp "$T/wr.bak" "$T/app/wrangler.toml"
# Worker without D1 binding for the target: fail closed (the job only exists when d1_migrations is true)
printf 'name = "x"\n' > "$T/app/wrangler.toml"
TARGET=prod "$M" >/dev/null 2>&1 && bad "no D1 accepted" || ok "target without D1 binding fails closed"
STUB_WRANGLER_VERSION=4.100.0 TARGET=prod "$M" >/dev/null 2>&1 && bad "old Wrangler accepted" || ok "Wrangler older than the supported minimum is refused before any remote command"
# There is no command override: a MIGRATE_CMD in the environment is ignored, never executed
: > "$T/log"; MIGRATE_CMD='echo custom-ran >> "$STUB_LOG"' TARGET=preview "$M" >/dev/null 2>&1 || true
grep -q custom-ran "$T/log" && bad "custom migrate command ran" || ok "no custom migrate command can replace or bypass the target check"
# migration settings are preserved; directory is validated
cat > "$T/app/wrangler.toml" <<'TOML'
name = "x"
[[d1_databases]]
binding = "DB"
database_name = "prod-db"
database_id = "PROD-ID"
migrations_table = "my_migrations"
migrations_pattern = "*.sql"
TOML
mkdir -p "$T/app/migrations" && echo "select 1;" > "$T/app/migrations/0001.sql"
TARGET=prod "$M" >/dev/null 2>&1 && ok "default migrations dir is used when none is configured" || bad "default dir"
grep -q '"migrations_table": "my_migrations"' "$T/app/.wrangler/migrate.prod.json" && grep -q '"migrations_pattern": "\*.sql"' "$T/app/.wrangler/migrate.prod.json" && ok "migrations_table / migrations_pattern are preserved" || bad "migration settings dropped"
rm -rf "$T/app/migrations"; mkdir "$T/app/migrations"
TARGET=prod "$M" >/dev/null 2>&1 && bad "dir without .sql accepted" || ok "migration directory without .sql files fails closed"
rm -rf "$T/app/migrations"
TARGET=prod "$M" >/dev/null 2>&1 && bad "missing dir accepted" || ok "missing migration directory fails closed"
TARGET=staging "$M" >/dev/null 2>&1 && bad "bad TARGET accepted" || ok "unknown TARGET rejected"
exit $fail
