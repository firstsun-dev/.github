#!/usr/bin/env bash
# Behavioural test of the two engine stages build.sh (build + audit, nothing remote) and publish.sh (publish ONLY the
# audited config) using a stub `pnpm` (no Cloudflare access needed): all targets, build-before-publish ordering,
# effective-config audit, same-artifact contract.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/app"
cat > "$T/bin/pnpm" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
[ "$1 $2" = "exec wrangler" ] || exit 0
case "$3" in
  preview) [ "${STUB_FAIL:-}" = 1 ] && exit 9
           if [ -n "${STUB_PREVIEW_RAW+x}" ]; then printf '%s' "$STUB_PREVIEW_RAW"; else echo '{"preview":{"urls":["https://stable.example.workers.dev"]}}'; fi;;
  deployments) echo '[{"created_on":"2024-01-01","versions":[{"version_id":"prev-1","percentage":100}]}]';;
  versions) if [ "$4" = upload ]; then echo "Worker Version ID: new-2"; fi;;
esac
STUB
chmod +x "$T/bin/pnpm"
export PATH="$T/bin:$PATH" STUB_LOG="$T/log" TOOLS_DIR="$ROOT/actions/cf-preview" APP_DIR="$T/app" OUT_FILE="$T/out.json" GITHUB_SHA=abcdef1234567890 STATE_DIR="$T/state"
fail=0; ok(){ echo "PASS: $1"; }; bad(){ echo "FAIL: $1"; fail=1; }
printf 'name = "x"\n[previews]\n' > "$T/app/wrangler.toml"
B="$ROOT/actions/cf-preview/build.sh"; P="$ROOT/actions/cf-preview/publish.sh"
# D = the delivery sequence the engine runs: build + audit first, then publish ONLY what was audited. Stdout is publish.sh's.
export B P
D() { rm -rf "$STATE_DIR"; "$B" >/dev/null || return $?; "$P"; }

# ---- preview ----
TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=true D 2>/dev/null | grep -qx 'url=https://stable.example.workers.dev' && ok "preview: extracts stable URL" || bad "preview: extracts stable URL"
grep -Eq 'preview --name feat-a --config [^ ]+wrangler.toml --json' "$T/log" && ok "preview: uses wrangler preview --name --config <audited config> --json" || bad "preview: wrangler args"

: > "$T/log"
TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=false D >/dev/null 2>&1 && bad "preview: build failure must fail" || ok "preview: build failure fails"
grep -q 'preview' "$T/log" && bad "preview ran after failed build" || ok "preview: failed build never touches the existing Preview"

TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=true STUB_FAIL=1 D >/dev/null 2>&1 && bad "wrangler failure must fail" || ok "preview: wrangler failure fails"
TARGET=preview PREVIEW_NAME='feat/a;rm -rf' BUILD_CMD=true D >/dev/null 2>&1 && bad "unsafe name accepted" || ok "preview: unsafe name rejected"
TARGET=preview BUILD_CMD=true D >/dev/null 2>&1 && bad "missing PREVIEW_NAME accepted" || ok "preview: PREVIEW_NAME required"

: > "$T/log"
TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=true WORKER_NAME=blog-preview D >/dev/null 2>&1
grep -Eq 'preview --name feat-a --worker-name blog-preview --config [^ ]+ --json' "$T/log" && ok "preview: passes --worker-name when set" || bad "worker-name passthrough"
: > "$T/log"
TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=true D >/dev/null 2>&1
grep -q -- '--worker-name' "$T/log" && bad "worker-name leaked when unset" || ok "preview: no --worker-name when unset"
TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=true WORKER_NAME='x; rm -rf /' D >/dev/null 2>&1 && bad "unsafe worker name accepted" || ok "preview: unsafe worker name rejected"

# Effective (adapter-generated) config is audited: it adds an unapproved service binding the source lacks.
: > "$T/log"
BAD_BUILD='mkdir -p .wrangler/deploy dist && echo "{\"configPath\":\"../../dist/wrangler.json\"}" > .wrangler/deploy/config.json && echo "{\"name\":\"x\",\"services\":[{\"binding\":\"S\",\"service\":\"prod\"}],\"previews\":{}}" > dist/wrangler.json'
TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD="$BAD_BUILD" D >/dev/null 2>&1 && bad "bad effective Preview config accepted" || ok "preview: effective config with unapproved binding is rejected"
grep -q 'preview --name' "$T/log" && bad "published despite bad effective config" || ok "preview: nothing published when effective audit fails"
rm -rf "$T/app/.wrangler" "$T/app/dist"
GOOD_BUILD='mkdir -p .wrangler/deploy dist && echo "{\"configPath\":\"../../dist/wrangler.json\"}" > .wrangler/deploy/config.json && echo "{\"name\":\"x\",\"previews\":{}}" > dist/wrangler.json'
TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD="$GOOD_BUILD" D >/dev/null 2>&1 && ok "preview: clean effective config accepted" || bad "clean effective config rejected"
rm -rf "$T/app/.wrangler" "$T/app/dist"

# ---- the empty-URL incident (blog run 39708495349): `wrangler preview --json` without a usable URL ----
# Root cause: `echo "url=$(node cli.mjs extract-url | sed ...)"` made the extractor's failure invisible (the
# pipeline status is sed's, echo's is 0) and published `url=` as a successful output, so Smoke ran with no URL.
incident() { # <label> <raw wrangler stdout>
  : > "$T/log"
  local out rc=0
  out="$(TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=true STUB_PREVIEW_RAW="$2" D 2>"$T/err")" || rc=$?
  [ "$rc" != 0 ] && ok "$1: publish fails" || bad "$1: publish must fail (rc=$rc)"
  echo "$out" | grep -q '^url=' && bad "$1: reported a url= line" || ok "$1: no url= line is emitted (nothing for Smoke to run against)"
  echo "$out" | grep -qx 'preview_mutated=true' && ok "$1: reports that the Preview may have been modified" || bad "$1: preview_mutated missing"
}
incident "empty preview.urls" '{"preview":{"urls":[]}}'
grep -q 'EMPTY `preview.urls`' "$T/err" && grep -q 'Preview URLs' "$T/err" && ok "empty preview.urls: actionable error explains how to enable Preview URLs" || bad "empty urls diagnostic"
incident "missing urls property" '{"preview":{}}'
incident "missing preview object" '{"other":1}'
incident "malformed JSON" 'this is not json'
incident "empty wrangler output" ''
incident "non-string URL" '{"preview":{"urls":[null]}}'
incident "empty-string URL" '{"preview":{"urls":[""]}}'
incident "http URL" '{"preview":{"urls":["http://x.example.com"]}}'
incident "relative URL" '{"preview":{"urls":["/just/a/path"]}}'
grep -q 'AKIA\|secret' "$T/err" && bad "diagnostics leaked payload content" || ok "diagnostics do not echo the Wrangler payload"
# a child parser that exits nonzero without printing anything must fail the parent
mkdir -p "$T/badtools"; ln -sf "$ROOT/actions/cf-preview/envfile.sh" "$T/badtools/envfile.sh"
printf "import { spawnSync } from 'node:child_process';\nif (process.argv[2] === 'extract-url') process.exit(3);\nprocess.exit(spawnSync('node', ['%s/actions/cf-preview/cli.mjs', ...process.argv.slice(2)], { stdio: 'inherit' }).status);\n" "$ROOT" > "$T/badtools/cli.mjs"
ln -sf "$ROOT/actions/cf-preview/parse-env.mjs" "$T/badtools/parse-env.mjs"
: > "$T/log"
OUT="$(TOOLS_DIR="$T/badtools" TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=true D 2>/dev/null)" && bad "silent failing parser accepted" || ok "child extractor exiting nonzero makes publish.sh fail"
echo "$OUT" | grep -q '^url=' && bad "url= emitted after a failed parser" || ok "no empty/guessed URL after a failed parser"
# valid output still yields exactly one clean url
OUT="$(TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD=true D 2>/dev/null)"
[ "$(echo "$OUT" | grep -c '^url=')" = 1 ] && echo "$OUT" | grep -qx 'url=https://stable.example.workers.dev' && ok "valid payload: exactly one https url= line" || bad "valid url output"

# ---- Preview host readiness (read-only Cloudflare API stub on localhost) ----
cat > "$T/api.mjs" <<'JS'
import http from 'node:http';
const mode = process.env.API_MODE;
const s = http.createServer((req, res) => {
  if (mode === 'down') { res.statusCode = 500; return res.end('{}'); }
  res.setHeader('content-type', 'application/json');
  res.end(JSON.stringify({ success: true, result: { enabled: false, previews_enabled: mode === 'enabled' } }));
}).listen(0, '127.0.0.1', () => console.log(s.address().port));
JS
host_case() { # <mode> -> sets API port, runs deploy, echoes rc
  API_MODE="$1" node "$T/api.mjs" > "$T/port" & local pid=$!
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$T/port" ] && break; sleep 0.3; done
  rm -f "$T/built"; : > "$T/log"
  local rc=0
  CF_API_BASE="http://127.0.0.1:$(cat "$T/port")" CLOUDFLARE_API_TOKEN=t CLOUDFLARE_ACCOUNT_ID=a HOST_WORKER=blog \
    TARGET=preview PREVIEW_NAME=feat-a BUILD_CMD="touch $T/built" D >/dev/null 2>"$T/err" || rc=$?
  kill $pid 2>/dev/null; wait $pid 2>/dev/null || true; : > "$T/port"
  return $rc
}
host_case disabled && bad "previews_enabled=false must refuse" || ok "host check: previews_enabled=false refuses the publish"
[ -e "$T/built" ] && bad "built despite no Preview host" || ok "host check: refusal happens before the build and before any wrangler call"
grep -q 'preview --name' "$T/log" && bad "published despite no Preview host" || ok "host check: wrangler preview never runs"
grep -q 'previews_enabled=false' "$T/err" && grep -q 'never changes the Worker automatically' "$T/err" && ok "host check: actionable error, no automatic change" || bad "host check diagnostic"
host_case enabled && grep -q 'preview --name' "$T/log" && ok "host check: previews_enabled=true proceeds" || bad "host check enabled"
host_case down && grep -q 'preview --name' "$T/log" && ok "host check: unreadable API only warns (strict post-publish validation still applies)" || bad "host check unknown"
PREVIEW_HOST_CHECK=false host_case disabled && grep -q 'preview --name' "$T/log" && ok "host check: preview_host_check=false (custom-domain hosting) skips the flag; URL still validated after publish" || bad "host check opt-out"

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
OUT="$(TARGET=prod BUILD_CMD=true APP_URL=https://app.example.com COMMIT_MSG=$'feat: thing\nbody' APP_VERSION=1.2.3 D 2>/dev/null)"
echo "$OUT" | grep -qx 'url=https://app.example.com' && ok "prod: reports app_url_prod" || bad "prod url"
echo "$OUT" | grep -qx 'version_id=new-2' && ok "prod: records new version id" || bad "prod version id"
echo "$OUT" | grep -qx 'previous_version_id=prev-1' && ok "prod: records previous version id for recovery" || bad "prod previous version"
grep -q 'versions deploy new-2@100' "$T/log" && ok "prod: deploys the uploaded version at 100%" || bad "prod deploy"
grep -q -- '--var COMMIT_HASH:abcdef1' "$T/log" && ok "prod: tags the commit" || bad "prod commit tag"
grep -q -- 'preview' "$T/log" && bad "prod ran wrangler preview" || ok "prod: never runs wrangler preview"

: > "$T/log"
TARGET=prod BUILD_CMD=false D >/dev/null 2>&1 && bad "prod build failure must fail" || ok "prod: build failure fails"
grep -q 'versions' "$T/log" && bad "prod published after failed build" || ok "prod: failed build never touches Production"

BAD_PROD='mkdir -p .wrangler/deploy dist && echo "{\"configPath\":\"../../dist/wrangler.json\"}" > .wrangler/deploy/config.json && echo "{\"name\":\"x\",\"d1_databases\":[{\"binding\":\"DB\",\"database_name\":\"prev-db\",\"database_id\":\"PREV-ID\"}]}" > dist/wrangler.json'
: > "$T/log"
TARGET=prod BUILD_CMD="$BAD_PROD" D >/dev/null 2>&1 && bad "prod bound Preview D1 accepted" || ok "prod: effective config binding a Preview resource is rejected"
grep -q 'versions' "$T/log" && bad "prod published despite audit failure" || ok "prod: nothing published when effective audit fails"
TARGET=other BUILD_CMD=true D >/dev/null 2>&1 && bad "bad TARGET accepted" || ok "unknown TARGET rejected"

# ---- the same-artifact contract: publish.sh publishes ONLY what build.sh audited ----
rm -rf "$STATE_DIR" "$T/app/.wrangler" "$T/app/dist"; : > "$T/log"
TARGET=prod "$P" >/dev/null 2>&1 && bad "publish without a build/audit accepted" || ok "publish.sh refuses to run when nothing was built and audited"
grep -q 'versions' "$T/log" && bad "published without audit" || ok "publish.sh: no Wrangler call without an audited config"
TARGET=prod BUILD_CMD=true "$B" >/dev/null 2>&1; echo '# tampered' >> "$T/app/wrangler.toml"
: > "$T/log"; TARGET=prod "$P" >/dev/null 2>&1 && bad "config changed after audit accepted" || ok "publish.sh refuses a config that changed between audit and publish"
grep -q 'versions' "$T/log" && bad "published a changed config" || ok "publish.sh: a changed config is never published"
sed -i '/# tampered/d' "$T/app/wrangler.toml"
# the effective (adapter-generated) config is what gets published, explicitly (--config), with no named env applied on top
EFF='mkdir -p .wrangler/deploy dist && echo "{\"configPath\":\"../../dist/wrangler.json\"}" > .wrangler/deploy/config.json && echo "{\"name\":\"x\",\"previews\":{}}" > dist/wrangler.json'
: > "$T/log"; TARGET=prod BUILD_CMD="$EFF" CLOUDFLARE_ENV=dev D >/dev/null 2>&1
grep -Eq 'versions upload --config [^ ]*dist/wrangler.json' "$T/log" && ok "prod: publishes the audited effective config explicitly (--config), not the source" || bad "effective config not passed explicitly"
grep -q -- '--env' "$T/log" && bad "named env applied on top of the effective config" || ok "an ambient CLOUDFLARE_ENV is neutralised: the audited config is published as is"
rm -rf "$T/app/.wrangler" "$T/app/dist"
# ROLLBACK_TO re-publishes a previous version with the SAME audited config
: > "$T/log"; TARGET=prod BUILD_CMD=true D >/dev/null 2>&1; : > "$T/log"
TARGET=prod ROLLBACK_TO=prev-1 "$P" >/dev/null 2>&1 && grep -Eq 'versions deploy prev-1@100 --config [^ ]+wrangler.toml' "$T/log" && ok "prod rollback uses the audited config" || bad "rollback config"
TARGET=prod ROLLBACK_TO='x;rm -rf /' "$P" >/dev/null 2>&1 && bad "unsafe rollback id accepted" || ok "unsafe rollback version id rejected"
TARGET=preview PREVIEW_NAME=feat-a ROLLBACK_TO=prev-1 "$P" >/dev/null 2>&1 && bad "ROLLBACK_TO accepted for preview" || ok "ROLLBACK_TO is Production only"

# ---- Shared Dev (target=dev): identity audit in build.sh; publish-only default; caller publish command is held to the contract ----
cat > "$T/app/wrangler.toml" <<'TOML'
name = "x"
[[queues.producers]]
binding = "Q"
queue = "prod-queue"
[env.dev]
name = "x-dev"
[[env.dev.queues.producers]]
binding = "Q"
queue = "dev-queue"
TOML
DEVV=(TARGET=dev DEV_ENV=dev DEV_WORKER=x-dev APP_URL=https://dev.example.com)
: > "$T/log"; env "${DEVV[@]}" BUILD_CMD=true bash -c "$(declare -f D); D" >/dev/null 2>&1 && grep -Eq 'deploy --config [^ ]+wrangler.toml --env dev' "$T/log" && ok "dev: default publish is wrangler deploy --config <audited> --env dev" || bad "dev default publish"
: > "$T/log"; env "${DEVV[@]}" DEV_WORKER=other BUILD_CMD=true bash -c "$(declare -f D); D" >/dev/null 2>&1 && bad "wrong Worker identity accepted" || ok "dev: a config for another Worker identity is rejected before publish"
grep -q 'deploy' "$T/log" && bad "published wrong identity" || ok "dev: nothing published after a failed audit"
sed -i 's/queue = "dev-queue"/queue = "prod-queue"/' "$T/app/wrangler.toml"
: > "$T/log"; env "${DEVV[@]}" BUILD_CMD=true bash -c "$(declare -f D); D" >/dev/null 2>&1 && bad "Production queue in Dev accepted" || ok "dev: a Production Queue binding fails the audit"
grep -q 'deploy' "$T/log" && bad "published with a Production queue" || ok "dev: nothing published with a Production Queue binding"
python3 - "$T/app/wrangler.toml" <<'PY'
import sys; p = sys.argv[1]; s = open(p).read(); i = s.index("[env.dev]"); open(p, "w").write(s[:i] + s[i:].replace("prod-queue", "dev-queue"))
PY
: > "$T/log"; env "${DEVV[@]}" BUILD_CMD=true PUBLISH_CMD='echo "custom publish $CF_AUDITED_CONFIG" >> "$STUB_LOG"' bash -c "$(declare -f D); D" >/dev/null 2>&1 && grep -q 'custom publish .*wrangler.toml' "$T/log" && ok "dev: the caller's publish command runs with CF_AUDITED_CONFIG" || bad "dev custom publish"
: > "$T/log"; env "${DEVV[@]}" BUILD_CMD=true PUBLISH_CMD='sed -i "s/x-dev/x-evil/" "$CF_AUDITED_CONFIG"' bash -c "$(declare -f D); D" >/dev/null 2>&1 && bad "publish command modifying the audited config accepted" || ok "dev: a publish command that modifies the audited config fails the run"
sed -i 's/x-evil/x-dev/' "$T/app/wrangler.toml"
mkdir -p "$T/app/dist"; echo orig > "$T/app/dist/index.js"
sed -i 's/^name = "x"$/name = "x"\nmain = "dist\/index.js"/' "$T/app/wrangler.toml"
: > "$T/log"; env "${DEVV[@]}" BUILD_CMD=true PUBLISH_CMD='echo rebuilt > dist/index.js' bash -c "$(declare -f D); D" >/dev/null 2>&1 && bad "publish command replacing the artifact accepted" || ok "dev: a publish command that rebuilds/replaces the audited artifact fails the run"
exit $fail
