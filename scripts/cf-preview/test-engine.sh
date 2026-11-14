#!/usr/bin/env bash
# Engine boundary tests (no provider, no GitHub): which stage may hold Cloudflare credentials, and the PR freshness contract (tested == deployed revision).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin"
fail=0; ok(){ echo "PASS: $1"; }; bad(){ echo "FAIL: $1"; fail=1; }
TOOLS="$ROOT/actions/cf-preview"; ENGINE="$TOOLS/engine.sh"; GUARD="$TOOLS/guard.sh"

# ---- credential boundary of the engine stages ---------------------------------------------------------------------------------------------------
DESC="$(node --input-type=module -e "import {targetDescriptor} from '$TOOLS/delivery.mjs'; console.log(JSON.stringify(targetDescriptor({delivery:{target:'preview',driver:'native-preview'},app:'blog',runId:1})))")"
echo '{"app_name":"blog","build_cmd":"true","production_rollback_policy":"manual","preview_policy":"pr"}' > "$T/in.json"
init() { # <stage> [env...]: runs engine init the way the action does
  local stage="$1"; shift
  env -i PATH="$PATH" HOME="$T" RUNNER_TEMP="$T/rt" STATE_DIR="$T/s-$stage" TOOLS_DIR="$TOOLS" INPUTS_JSON="$(cat "$T/in.json")" ENGINE_TARGET=preview ENGINE_KIND=native-branch ENGINE_STAGE="$stage" \
    ENGINE_DESCRIPTOR="$DESC" ENGINE_D1_EXPECT= GITHUB_EVENT_NAME=pull_request GITHUB_REF=refs/pull/4/merge CFV2_PR_ACTION=synchronize "$@" "$ENGINE" init >/dev/null 2>"$T/err"
}
init build && ok "build: initialises without credentials" || { bad "build: initialises without credentials"; cat "$T/err"; }
init build CLOUDFLARE_API_TOKEN=t CLOUDFLARE_ACCOUNT_ID=a && bad "build: a Cloudflare credential is refused" || { grep -q "must not receive Cloudflare credentials" "$T/err" && ok "build: a Cloudflare credential is refused"; }
init build CLOUDFLARE_API_TOKEN=t && bad "build: a lone token is refused" || ok "build: a lone token is refused"
init verify CLOUDFLARE_ACCOUNT_ID=a && bad "verify (smoke): credentials refused" || ok "verify (smoke): credentials refused"
init app-smoke CLOUDFLARE_API_TOKEN=t && bad "app-smoke: a Cloudflare credential is refused" || ok "app-smoke: a Cloudflare credential is refused"
init app-smoke && ok "app-smoke: initialises without credentials" || { bad "app-smoke: initialises without credentials"; cat "$T/err"; }
init publish CLOUDFLARE_API_TOKEN=t CLOUDFLARE_ACCOUNT_ID=a && ok "publish: credentials required and accepted" || { bad "publish: accepted with credentials"; cat "$T/err"; }
init publish && bad "publish: refused without credentials" || ok "publish: refused without credentials"
init all CLOUDFLARE_API_TOKEN=t CLOUDFLARE_ACCOUNT_ID=a && bad "the single-job 'all' stage is gone" || ok "the single-job 'all' stage is gone"

# ---- PR freshness: the merge candidate that passed CI is what is deployed; a newer synchronization owns the Preview -------------------------------
cat > "$T/bin/gh" <<'STUB'
#!/usr/bin/env bash
[ "${GH_FAIL:-}" = 1 ] && exit 1
printf '%s\t%s\t%s\n' "${PR_STATE:-open}" "${PR_HEAD:-aaaa}" "${PR_MERGE-bbbb}"
STUB
chmod +x "$T/bin/gh"
fresh() { PATH="$T/bin:$PATH" GUARD_RETRY_SLEEP=0 GITHUB_REPOSITORY=o/r PR_NUMBER=4 BRANCH=feat RUN_SHA=bbbb SOURCE_SHA=aaaa "$@" "$GUARD" fresh 2>/dev/null; }
[ "$(fresh env)" = stale=false ] && ok "PR fresh: open, same head, same merge candidate" || bad "PR fresh: current run"
[ "$(fresh env PR_HEAD=cccc)" = stale=true ] && ok "PR stale: a newer synchronization moved the head" || bad "PR stale: head moved"
[ "$(fresh env PR_MERGE=dddd)" = stale=true ] && ok "PR stale: the base moved (different merge candidate)" || bad "PR stale: merge candidate moved"
[ "$(fresh env PR_MERGE=)" = stale=true ] && ok "PR stale: merge candidate not computed yet fails closed" || bad "PR stale: no merge candidate"
[ "$(fresh env PR_STATE=closed)" = stale=true ] && ok "PR stale: a closed PR never publishes" || bad "PR stale: closed"
fresh env GH_FAIL=1 >/dev/null && bad "PR fresh: an unreadable PR fails closed (non-zero)" || ok "PR fresh: an unreadable PR fails closed (non-zero)"
# A -> B -> A: head returns to the old value, but GitHub computes a new merge candidate, so the old run (old merge SHA) is still stale.
[ "$(fresh env PR_HEAD=aaaa PR_MERGE=eeee)" = stale=true ] && ok "PR stale: A -> B -> A (same head, newer merge candidate) is superseded" || bad "PR stale: A->B->A"

# ---- a Preview of an app with d1_migrations has NO source checkout in the privileged job: audit-d1 must not read it ------------------------------------
echo '{"app_name":"blog","build_cmd":"true","production_rollback_policy":"manual","preview_policy":"pr","d1_migrations":true}' > "$T/in.json"
init publish CLOUDFLARE_API_TOKEN=t CLOUDFLARE_ACCOUNT_ID=a || { bad "preview+d1_migrations init"; cat "$T/err"; }
env -i PATH="$PATH" HOME="$T" RUNNER_TEMP="$T/rt" STATE_DIR="$T/s-publish" TOOLS_DIR="$TOOLS" GITHUB_WORKSPACE="$T/no-checkout" GITHUB_EVENT_NAME=pull_request GITHUB_REF=refs/pull/4/merge \
  GITHUB_SHA=bbbb PR_NUMBER=4 PR_HEAD_REF=feat PR_HEAD_SHA=aaaa "$ENGINE" audit-d1 >/dev/null 2>"$T/err" && ok "audit-d1: a Preview with d1_migrations needs no source checkout" || { bad "audit-d1: Preview without checkout"; cat "$T/err"; }

# ---- the Build stage end to end through the engine (regression: the first real-runner canary found the manifest identity was not exported) -----------
mkdir -p "$T/ws/src" "$T/bin2"
printf 'name = "blog"\nmain = "src/index.js"\ncompatibility_date = "2025-01-01"\n[previews]\n' > "$T/ws/wrangler.toml"; echo 'export default {}' > "$T/ws/src/index.js"
cat > "$T/bin2/pnpm" <<'STUB'
#!/usr/bin/env bash
while [ $# -gt 0 ]; do [ "$1" = --outdir ] && { mkdir -p "$2"; echo 'export default {}' > "$2/index.js"; }; shift; done
STUB
chmod +x "$T/bin2/pnpm"
echo '{"app_name":"blog","build_cmd":"true","production_rollback_policy":"manual","preview_policy":"pr"}' > "$T/in.json"
PRENV=(GITHUB_EVENT_NAME=pull_request GITHUB_REF=refs/pull/4/merge GITHUB_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb PR_NUMBER=4 PR_HEAD_REF=feat PR_HEAD_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa GITHUB_REPOSITORY=o/r GITHUB_RUN_ID=9 GITHUB_RUN_ATTEMPT=2)
benv() { env -i PATH="$T/bin2:$PATH" HOME="$T" RUNNER_TEMP="$T/rt" STATE_DIR="$T/s-b" TOOLS_DIR="$TOOLS" GITHUB_WORKSPACE="$T/ws" "${PRENV[@]}" "$@"; }
benv INPUTS_JSON="$(cat "$T/in.json")" ENGINE_TARGET=preview ENGINE_KIND=native-branch ENGINE_STAGE=build ENGINE_DESCRIPTOR="$DESC" ENGINE_D1_EXPECT= ENGINE_ARTIFACT_DIR="$T/art" \
  ENGINE_TARGET_URL=https://pr-4-blog.x.workers.dev CFV2_PR_ACTION=synchronize "$ENGINE" init >/dev/null 2>"$T/err" || { bad "build init"; cat "$T/err"; }
benv "$ENGINE" build >/dev/null 2>"$T/err" && ok "build stage: produces the artifact" || { bad "build stage: produces the artifact"; cat "$T/err"; }
node -e 'const m=require(process.argv[1]);const ok=m.source_sha==="a".repeat(40)&&m.tested_sha==="b".repeat(40)&&m.artifact_sha==="b".repeat(40)&&m.run_id==="9"&&m.run_attempt==="2"&&m.target==="preview"&&m.slot==="native-branch"&&m.target_url==="https://pr-4-blog.x.workers.dev";process.exit(ok?0:1)' "$T/art/manifest.json" \
  && ok "build stage: the manifest records source=PR head, tested=artifact=merge candidate, run id/attempt, target, URL" || { bad "build stage manifest identity"; cat "$T/art/manifest.json"; }

# ---- Preview Smoke boundary (G1 review P0): the PR-controlled smoke command runs ONLY in app-smoke, with no credential; the Smoke job never runs it -------------
mkdir -p "$T/curlbin"
printf '#!/usr/bin/env bash\nprintf "200 "\n' > "$T/curlbin/curl"; chmod +x "$T/curlbin/curl"
printf '#!/usr/bin/env bash\nexit 0\n' > "$T/curlbin/gh"; chmod +x "$T/curlbin/gh"
SMK='{"app_name":"blog","build_cmd":"true","production_rollback_policy":"manual","preview_policy":"pr","smoke_cmd":"env > $MARK; touch $MARK.ran","smoke_env":"CF_ACCESS_CLIENT_ID=cid\nCF_ACCESS_CLIENT_SECRET=csecret\nPROBE_HEADERS=X-Probe: psecret\nAPP_FLAG=visible"}'
echo "$SMK" > "$T/in.json"
HANDOFF='{"published":"true","kind":"native-branch","url":"https://pr-4-blog.x.workers.dev","preview_name":"pr-4","published_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","ledger_id":"7"}'
senv() { env -i PATH="$T/curlbin:$PATH" HOME="$T" RUNNER_TEMP="$T/rt" STATE_DIR="$T/s-$1" TOOLS_DIR="$TOOLS" GITHUB_WORKSPACE="$T/ws" "${PRENV[@]}" MARK="$T/mark" PROBE_ATTEMPTS=1 PROBE_SLEEP=0 \
  NO_PROXY='*' "${@:2}"; }
for st in app-smoke verify; do
  senv "$st" INPUTS_JSON="$SMK" ENGINE_TARGET=preview ENGINE_KIND=native-branch ENGINE_STAGE="$st" ENGINE_DESCRIPTOR="$DESC" ENGINE_D1_EXPECT= CFV2_PR_ACTION=synchronize "$ENGINE" init >/dev/null 2>"$T/err" || { bad "$st init"; cat "$T/err"; }
  senv "$st" HANDOFF_DEPLOY="$HANDOFF" "$ENGINE" hydrate >/dev/null 2>"$T/err" || { bad "$st hydrate"; cat "$T/err"; }
done
rm -f "$T"/mark*
senv app-smoke GH_TOKEN=ghsecret SMOKE_USERNAME=u SMOKE_PASSWORD=p CLOUDFLARE_API_TOKEN=cf PREVIEW_SECRETS=ps PUBLISHED_URL=https://pr-4-blog.x.workers.dev "$ENGINE" app-smoke >/dev/null 2>"$T/err" \
  && [ -f "$T/mark.ran" ] && ok "app-smoke: runs the application smoke command" || { bad "app-smoke: runs the application smoke command"; cat "$T/err"; }
if grep -qE 'ghsecret|=u$|=p$|=cf$|=ps$|csecret|cid|psecret|CF_ACCESS|PROBE_HEADERS|SMOKE_(USERNAME|PASSWORD)|GH_TOKEN|CLOUDFLARE' "$T/mark"; then bad "app-smoke: the PR command must see NO credential"; grep -E 'secret|CF_ACCESS|SMOKE_|GH_TOKEN|CLOUDFLARE|PROBE' "$T/mark" | sed 's/=.*/=<redacted>/'; else ok "app-smoke: the PR command sees no GitHub / Cloudflare / smoke credential, Access token or PROBE_HEADERS"; fi
grep -q '^APP_FLAG=visible' "$T/mark" && grep -q '^TARGET_URL=https://pr-4-blog' "$T/mark" && ok "app-smoke: TARGET_URL and ordinary smoke_env data are provided" || bad "app-smoke: TARGET_URL / smoke_env data"
rm -f "$T"/mark*
senv verify APP_SMOKE_RESULT=failure PUBLISHED_URL=https://pr-4-blog.x.workers.dev "$ENGINE" smoke >/dev/null 2>"$T/err" && bad "smoke: a failed app-smoke must fail the Preview" || { grep -q "application smoke command did not pass" "$T/err" && ok "smoke: a Preview whose app-smoke did not succeed is not verified"; }
senv verify APP_SMOKE_RESULT= PUBLISHED_URL=https://pr-4-blog.x.workers.dev "$ENGINE" smoke >/dev/null 2>/dev/null && bad "smoke: a missing app-smoke result must fail closed" || ok "smoke: a missing / skipped app-smoke result fails closed"
senv verify APP_SMOKE_RESULT=success CF_ACCESS_CLIENT_ID= PUBLISHED_URL=https://pr-4-blog.x.workers.dev "$ENGINE" smoke 2>"$T/err" | grep -q '^smoke_level=content$' && [ ! -e "$T/mark.ran" ] \
  && ok "smoke: with a passing app-smoke the credential-bearing job verifies by probe and NEVER runs the PR command" || { bad "smoke: Preview verifies without running the PR command"; cat "$T/err"; }

[ "$fail" = 0 ] && echo "ALL PASSED" || { echo "FAILURES"; exit 1; }
