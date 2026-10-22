#!/usr/bin/env bash
# Tests gh.sh ledger logic against a stub `gh` (no network).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT; mkdir "$T/bin"
cat > "$T/bin/gh" <<'STUB'
#!/usr/bin/env bash
echo "gh $*" >> "$STUB_LOG"
a="$*"
case "$a" in
  *"deployments?environment"*"per_page=30"*" -q "*) printf '3:cccc\n2:bbbb\n1:aaaa\n';;
  *"deployments?environment"*"per_page=30"*) printf '%s' "$LP_JSON";;
  *"/statuses?per_page=1"*"environment_url"*) id="${a#*deployments/}"; id="${id%%/*}"; eval "echo \"\${LP_ST_$id:-failure }\"";;
  *"payload.branch_ref"*) echo "${OWNER_REF:-}";;
  *"deployments?environment"*"per_page=100"*) printf '11\n12\n';;
  *"deployments/3/statuses"*) echo failure;;
  *"deployments/2/statuses"*) echo success;;
  *"deployments/1/statuses"*) echo success;;
  *"pulls?state=open"*) echo 7;;
  *"issues/7/comments"*"--paginate"*) [ "${HAS_COMMENT:-}" = 1 ] && echo 99 || true;;
esac
STUB
chmod +x "$T/bin/gh"
export PATH="$T/bin:$PATH" STUB_LOG="$T/log" GITHUB_REPOSITORY=o/r GH_TOKEN=x
fail=0; ok(){ echo "PASS: $1"; }; bad(){ echo "FAIL: $1"; fail=1; }
G="$ROOT/actions/cf-preview/gh.sh"

[ "$("$G" last-good "app/preview/x" 3)" = bbbb ] && ok "last-good skips current + failed, returns newest success" || bad "last-good"
[ -z "$("$G" last-good "app/preview/x" 3 | grep -x cccc || true)" ] && ok "never returns failed deployment" || bad "returned failed"

# ---- last-good-preview: identity-bound lookup (repo, app, branch ref, HOST Worker, success, https URL) ----
dep() { printf '{"id":%s,"sha":"%s","payload":{"repo":"o/r","branch_ref":"refs/heads/feat/a","host_worker":"%s","worker":"www"%s}}' "$1" "$2" "$3" "${4:-}"; }
LP_JSON="[$(dep 5 newest-wrong-host blog-preview),$(dep 4 right-host-failed blog),$(dep 3 right-host-nourl blog),$(dep 2 right-host-good blog),$(dep 1 older-good blog)]"
export LP_JSON
LP_ST_5="success https://feat-a-blog-preview.x.workers.dev Serving s5 (content)" LP_ST_4="failure https://u.example.com" LP_ST_3="success " LP_ST_2="success https://feat-a-blog.x.workers.dev Serving s2 (content-sha)" LP_ST_1="success https://old.example.com Serving s1 (content)"
export LP_ST_5 LP_ST_4 LP_ST_3 LP_ST_2 LP_ST_1
LP() { "$G" last-good-preview "www/preview/feat-a" "${1:-0}" refs/heads/feat/a "${2:-blog}" www; }
[ "$(LP 0)" = "right-host-good https://feat-a-blog.x.workers.dev" ] && ok "last-good-preview: skips another host Worker, failed, and URL-less deployments" || bad "last-good-preview basic: $(LP 0)"
[ "$(LP 2)" = "older-good https://old.example.com" ] && ok "last-good-preview: honours the excluded (current) deployment" || bad "exclude"
[ "$(LP 0 blog-preview)" = "newest-wrong-host https://feat-a-blog-preview.x.workers.dev" ] && ok "last-good-preview: same branch hosted by blog-preview resolves only to blog-preview records" || bad "other host"
# Verification level: only content-level success is a known-good deployment (liveness-only / legacy records are not).
LP_ST_2="success https://feat-a-blog.x.workers.dev Serving s2 (liveness)"
[ "$(LP 0)" = "older-good https://old.example.com" ] && ok "last-good-preview: a liveness-only success is NOT known-good" || bad "liveness eligible: $(LP 0)"
LP_ST_2="success https://feat-a-blog.x.workers.dev"
[ "$(LP 0)" = "older-good https://old.example.com" ] && ok "last-good-preview: a legacy success without a verification marker is NOT known-good" || bad "legacy success eligible"
LP_ST_2="success https://feat-a-blog.x.workers.dev Serving s2 (content-sha)"; export LP_ST_2
dev() { printf '{"id":%s,"sha":"%s","payload":{"repo":"o/r","dev_worker":"blog-preview","worker":"blog"}}' "$1" "$2"; }
LP_JSON="[$(dev 5 dev-liveness),$(dev 4 dev-good)]"
LP_ST_5="success https://dev.example.com Published s5; unverified (liveness)" LP_ST_4="success https://dev.example.com Serving s4 (content-sha)"; export LP_ST_5 LP_ST_4
[ "$("$G" last-good-dev "blog/dev/shared" 0 blog-preview blog)" = "dev-good https://dev.example.com" ] && ok "last-good-dev: Dev liveness-only is not recorded as content-verified last-good" || bad "dev liveness eligible"
LP_JSON="[$(dep 5 newest-wrong-host blog-preview),$(dep 4 right-host-failed blog),$(dep 3 right-host-nourl blog),$(dep 2 right-host-good blog),$(dep 1 older-good blog)]"; export LP_JSON LP_ST_5
LP_ST_5="success https://feat-a-blog-preview.x.workers.dev Serving s5 (content)"; export LP_ST_5
[ -z "$(LP 0 someone-else)" ] && ok "last-good-preview: unknown host Worker has no eligible deployment" || bad "unknown host"
[ -z "$("$G" last-good-preview "www/preview/feat-a" 0 refs/heads/other blog www)" ] && ok "last-good-preview: another branch ref is not eligible" || bad "branch"
[ -z "$("$G" last-good-preview "www/preview/feat-a" 0 refs/heads/feat/a blog other-app)" ] && ok "last-good-preview: another application is not eligible" || bad "app"
LP_JSON="[{\"id\":2,\"sha\":\"legacy\",\"payload\":{\"branch_ref\":\"refs/heads/feat/a\",\"worker\":\"www\"}}]"
[ -z "$(LP 0)" ] && ok "last-good-preview: legacy records without host_worker/repo are never eligible" || bad "legacy"

: > "$T/log"; "$G" comment feat/a www https://u.dev abc1234 >/dev/null
grep -q 'issues/7/comments -f body' "$T/log" && ok "creates comment when none" || bad "create"
grep -q 'head=o:feat/a' "$T/log" && ok "matches exact head branch of same owner" || bad "head match"
: > "$T/log"; HAS_COMMENT=1 "$G" comment feat/a www https://u.dev abc1234 >/dev/null
grep -q 'issues/comments/99 -X PATCH' "$T/log" && ok "idempotent: updates existing comment" || bad "update"
grep -q 'issues/7/comments -f' "$T/log" && bad "duplicate comment created" || ok "no duplicate comment"

: > "$T/log"; "$G" retire "app/preview/x" >/dev/null
grep -q 'deployments/.*/statuses -f state=inactive' "$T/log" && ok "retire marks deployments inactive" || bad "retire inactive"
grep -q -- '-X DELETE' "$T/log" && bad "retire must not delete deployments" || ok "retire never deletes deployments"

[ "$(OWNER_REF=refs/heads/feat/a "$G" owner app/preview/feat-a)" = refs/heads/feat/a ] && ok "owner returns the owning full ref" || bad "owner"
[ -z "$("$G" owner app/preview/new)" ] && ok "owner is empty for a new name" || bad "owner empty"
exit $fail
