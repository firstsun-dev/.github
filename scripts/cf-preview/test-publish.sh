#!/usr/bin/env bash
# Behavioural test of publish.sh using a stub `pnpm` (no Cloudflare access needed).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/app"
cat > "$T/bin/pnpm" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
[ "$1 $2" = "exec wrangler" ] && [ "$3" = "preview" ] || exit 0
[ "${STUB_FAIL:-}" = 1 ] && exit 9
echo '{"preview":{"urls":["https://stable.example.workers.dev"]}}'
STUB
chmod +x "$T/bin/pnpm"
export PATH="$T/bin:$PATH" STUB_LOG="$T/log" TOOLS_DIR="$ROOT/actions/cf-preview" APP_DIR="$T/app" OUT_FILE="$T/out.json"
fail=0; ok(){ echo "PASS: $1"; }; bad(){ echo "FAIL: $1"; fail=1; }

PREVIEW_NAME=feat-a BUILD_CMD=true "$ROOT/actions/cf-preview/publish.sh" 2>/dev/null | grep -qx 'preview_url=https://stable.example.workers.dev' && ok "extracts stable URL" || bad "extracts stable URL"
grep -q 'preview --name feat-a --json' "$T/log" && ok "uses wrangler preview --name --json" || bad "wrangler args"

: > "$T/log"
PREVIEW_NAME=feat-a BUILD_CMD=false "$ROOT/actions/cf-preview/publish.sh" >/dev/null 2>&1 && bad "build failure must fail" || ok "build failure fails"
grep -q 'preview' "$T/log" && bad "preview ran after failed build" || ok "failed build never touches Preview"

PREVIEW_NAME=feat-a BUILD_CMD=true STUB_FAIL=1 "$ROOT/actions/cf-preview/publish.sh" >/dev/null 2>&1 && bad "wrangler failure must fail" || ok "wrangler failure fails"
PREVIEW_NAME='feat/a;rm -rf' BUILD_CMD=true "$ROOT/actions/cf-preview/publish.sh" >/dev/null 2>&1 && bad "unsafe name accepted" || ok "unsafe name rejected"

: > "$T/log"
PREVIEW_NAME=feat-a BUILD_CMD=true WORKER_NAME=blog-preview "$ROOT/actions/cf-preview/publish.sh" >/dev/null 2>&1
grep -q 'preview --name feat-a --worker-name blog-preview --json' "$T/log" && ok "passes --worker-name when set" || bad "worker-name passthrough"
: > "$T/log"
PREVIEW_NAME=feat-a BUILD_CMD=true "$ROOT/actions/cf-preview/publish.sh" >/dev/null 2>&1
grep -q -- '--worker-name' "$T/log" && bad "worker-name leaked when unset" || ok "no --worker-name when unset"
PREVIEW_NAME=feat-a BUILD_CMD=true WORKER_NAME='x; rm -rf /' "$ROOT/actions/cf-preview/publish.sh" >/dev/null 2>&1 && bad "unsafe worker name accepted" || ok "unsafe worker name rejected"
exit $fail
