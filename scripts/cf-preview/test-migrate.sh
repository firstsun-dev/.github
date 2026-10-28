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
