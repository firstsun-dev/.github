#!/usr/bin/env bash
# Behavioural test of deploy.sh (the single Deploy implementation) using a stub `pnpm`
# (no Cloudflare access needed): both targets, build-before-publish ordering, effective-config audit.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/app"
cat > "$T/bin/pnpm" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
[ "$1 $2" = "exec wrangler" ] || exit 0
case "$3" in
  preview) [ "${STUB_FAIL:-}" = 1 ] && exit 9; echo '{"preview":{"urls":["https://stable.example.workers.dev"]}}';;
  deployments) echo '[{"created_on":"2024-01-01","versions":[{"version_id":"prev-1","percentage":100}]}]';;
  versions) if [ "$4" = upload ]; then echo "Worker Version ID: new-2"; fi;;
esac
STUB
chmod +x "$T/bin/pnpm"
export PATH="$T/bin:$PATH" STUB_LOG="$T/log" TOOLS_DIR="$ROOT/actions/cf-preview" APP_DIR="$T/app" OUT_FILE="$T/out.json" GITHUB_SHA=abcdef1234567890
fail=0; ok(){ echo "PASS: $1"; }; bad(){ echo "FAIL: $1"; fail=1; }
D="$ROOT/actions/cf-preview/deploy.sh"

# ---- preview ----
TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=true "$D" 2>/dev/null | grep -qx 'url=https://stable.example.workers.dev' && ok "preview: extracts stable URL" || bad "preview: extracts stable URL"
grep -q 'preview --name feat-a --json' "$T/log" && ok "preview: uses wrangler preview --name --json" || bad "preview: wrangler args"

: > "$T/log"
TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=false "$D" >/dev/null 2>&1 && bad "preview: build failure must fail" || ok "preview: build failure fails"
grep -q 'preview' "$T/log" && bad "preview ran after failed build" || ok "preview: failed build never touches the existing Preview"

TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=true STUB_FAIL=1 "$D" >/dev/null 2>&1 && bad "wrangler failure must fail" || ok "preview: wrangler failure fails"
TARGET=preview PREVIEW_NAME='feat/a;rm -rf' BUILD_CMD=true "$D" >/dev/null 2>&1 && bad "unsafe name accepted" || ok "preview: unsafe name rejected"
TARGET=preview BUILD_CMD=true "$D" >/dev/null 2>&1 && bad "missing PREVIEW_NAME accepted" || ok "preview: PREVIEW_NAME required"

: > "$T/log"
TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=true WORKER_NAME=blog-preview "$D" >/dev/null 2>&1
grep -q 'preview --name feat-a --worker-name blog-preview --json' "$T/log" && ok "preview: passes --worker-name when set" || bad "worker-name passthrough"
: > "$T/log"
TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=true "$D" >/dev/null 2>&1
grep -q -- '--worker-name' "$T/log" && bad "worker-name leaked when unset" || ok "preview: no --worker-name when unset"
TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=true WORKER_NAME='x; rm -rf /' "$D" >/dev/null 2>&1 && bad "unsafe worker name accepted" || ok "preview: unsafe worker name rejected"

# Effective (adapter-generated) config is audited: it adds an unapproved service binding the source lacks.
: > "$T/log"
BAD_BUILD='mkdir -p .wrangler/deploy dist && echo "{\"configPath\":\"../../dist/wrangler.json\"}" > .wrangler/deploy/config.json && echo "{\"name\":\"x\",\"services\":[{\"binding\":\"S\",\"service\":\"prod\"}],\"previews\":{}}" > dist/wrangler.json'
TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD="$BAD_BUILD" "$D" >/dev/null 2>&1 && bad "bad effective Preview config accepted" || ok "preview: effective config with unapproved binding is rejected"
grep -q 'preview --name' "$T/log" && bad "published despite bad effective config" || ok "preview: nothing published when effective audit fails"
rm -rf "$T/app/.wrangler" "$T/app/dist"
GOOD_BUILD='mkdir -p .wrangler/deploy dist && echo "{\"configPath\":\"../../dist/wrangler.json\"}" > .wrangler/deploy/config.json && echo "{\"name\":\"x\",\"previews\":{}}" > dist/wrangler.json'
TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD="$GOOD_BUILD" "$D" >/dev/null 2>&1 && ok "preview: clean effective config accepted" || bad "clean effective config rejected"
rm -rf "$T/app/.wrangler" "$T/app/dist"

# ---- prod ----
cat > "$T/app/wrangler.toml" <<'TOML'
name = "x"
[[d1_databases]]
binding = "DB"
database_name = "prod-db"
database_id = "PROD-ID"
[previews]
[[previews.d1_databases]]
binding = "DB"
database_name = "prev-db"
database_id = "PREV-ID"
TOML
: > "$T/log"
OUT="$(TARGET=prod BUILD_CMD=true APP_URL_PROD=https://app.example.com COMMIT_MSG=$'feat: thing\nbody' APP_VERSION=1.2.3 "$D" 2>/dev/null)"
echo "$OUT" | grep -qx 'url=https://app.example.com' && ok "prod: reports app_url_prod" || bad "prod url"
echo "$OUT" | grep -qx 'version_id=new-2' && ok "prod: records new version id" || bad "prod version id"
echo "$OUT" | grep -qx 'previous_version_id=prev-1' && ok "prod: records previous version id for recovery" || bad "prod previous version"
grep -q 'versions deploy new-2@100' "$T/log" && ok "prod: deploys the uploaded version at 100%" || bad "prod deploy"
grep -q -- '--var COMMIT_HASH:abcdef1' "$T/log" && ok "prod: tags the commit" || bad "prod commit tag"
grep -q -- 'preview' "$T/log" && bad "prod ran wrangler preview" || ok "prod: never runs wrangler preview"

: > "$T/log"
TARGET=prod BUILD_CMD=false "$D" >/dev/null 2>&1 && bad "prod build failure must fail" || ok "prod: build failure fails"
grep -q 'versions' "$T/log" && bad "prod published after failed build" || ok "prod: failed build never touches Production"

BAD_PROD='mkdir -p .wrangler/deploy dist && echo "{\"configPath\":\"../../dist/wrangler.json\"}" > .wrangler/deploy/config.json && echo "{\"name\":\"x\",\"d1_databases\":[{\"binding\":\"DB\",\"database_name\":\"prev-db\",\"database_id\":\"PREV-ID\"}]}" > dist/wrangler.json'
: > "$T/log"
TARGET=prod BUILD_CMD="$BAD_PROD" "$D" >/dev/null 2>&1 && bad "prod bound Preview D1 accepted" || ok "prod: effective config binding a Preview resource is rejected"
grep -q 'versions' "$T/log" && bad "prod published despite audit failure" || ok "prod: nothing published when effective audit fails"
TARGET=other BUILD_CMD=true "$D" >/dev/null 2>&1 && bad "bad TARGET accepted" || ok "unknown TARGET rejected"
exit $fail
