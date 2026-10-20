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
  *"deployments?environment"*"per_page=30"*) printf '3:cccc\n2:bbbb\n1:aaaa\n';;
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

[ "$("$G" last-good "app/preview/br-x" 3)" = bbbb ] && ok "last-good skips current + failed, returns newest success" || bad "last-good"
[ -z "$("$G" last-good "app/preview/br-x" 3 | grep -x cccc || true)" ] && ok "never returns failed deployment" || bad "returned failed"

: > "$T/log"; "$G" comment feat/a www https://u.dev abc1234 >/dev/null
grep -q 'issues/7/comments -f body' "$T/log" && ok "creates comment when none" || bad "create"
grep -q 'head=o:feat/a' "$T/log" && ok "matches exact head branch of same owner" || bad "head match"
: > "$T/log"; HAS_COMMENT=1 "$G" comment feat/a www https://u.dev abc1234 >/dev/null
grep -q 'issues/comments/99 -X PATCH' "$T/log" && ok "idempotent: updates existing comment" || bad "update"
grep -q 'issues/7/comments -f' "$T/log" && bad "duplicate comment created" || ok "no duplicate comment"
exit $fail
