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

[ "$fail" = 0 ] && echo "ALL PASSED" || { echo "FAILURES"; exit 1; }
