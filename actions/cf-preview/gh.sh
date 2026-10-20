#!/usr/bin/env bash
# GitHub Deployment ledger + PR feedback for branch Previews. Needs GH_TOKEN, GITHUB_REPOSITORY.
# Subcommands: start | status | last-good | comment | environments | purge
set -euo pipefail
R="${GITHUB_REPOSITORY:?}"
cmd="${1:?}"; shift

case "$cmd" in
  start) # <env> <sha> <payload-json> -> deployment id
    gh api "repos/$R/deployments" -X POST --input - -q .id <<JSON
{"ref":"$2","environment":"$1","description":"Native Preview","auto_merge":false,"required_contexts":[],"transient_environment":true,"production_environment":false,"payload":$3}
JSON
    ;;
  status) # <id> <state> [url] [description]
    gh api "repos/$R/deployments/$1/statuses" -f state="$2" -f environment_url="${3:-}" -f log_url="${GITHUB_SERVER_URL:-https://github.com}/$R/actions/runs/${GITHUB_RUN_ID:-0}" -f description="${4:-}" >/dev/null
    ;;
  last-good) # <env> [exclude-id] -> sha of newest deployment whose latest status is success
    for row in $(gh api "repos/$R/deployments?environment=$(printf %s "$1" | jq -sRr @uri)&per_page=30" -q '.[] | "\(.id):\(.sha)"'); do
      id="${row%%:*}"; sha="${row#*:}"
      [ "$id" = "${2:-}" ] && continue
      st="$(gh api "repos/$R/deployments/$id/statuses?per_page=1" -q '.[0].state // empty')"
      if [ "$st" = "success" ]; then echo "$sha"; exit 0; fi
    done
    ;;
  comment) # <branch> <app_name> <url> <sha> : only same-repo PRs from this exact head branch
    owner="${R%%/*}"
    marker="<!-- cf-preview:$2 -->"
    body="$marker
**Preview ($2)**: $3
Commit: \`$4\` · branch \`$1\` · Preview is branch-owned and persists after this PR closes."
    for pr in $(gh api "repos/$R/pulls?state=open&head=$owner:$1" -q '.[] | select(.head.repo.full_name == "'"$R"'") | .number'); do
      cid="$(gh api "repos/$R/issues/$pr/comments" --paginate -q '.[] | select(.body | startswith("'"$marker"'")) | .id' | head -1)"
      if [ -n "$cid" ]; then gh api "repos/$R/issues/comments/$cid" -X PATCH -f body="$body" >/dev/null
      else gh api "repos/$R/issues/$pr/comments" -f body="$body" >/dev/null; fi
      echo "commented on PR #$pr"
    done
    ;;
  environments) # <prefix> -> distinct deployment environments with prefix
    gh api "repos/$R/deployments?per_page=100" --paginate -q '.[].environment' | grep -F "$1" | sort -u || true
    ;;
  purge) # <env> : mark inactive and delete all deployments of this environment
    for id in $(gh api "repos/$R/deployments?environment=$(printf %s "$1" | jq -sRr @uri)&per_page=100" --paginate -q '.[].id'); do
      gh api "repos/$R/deployments/$id/statuses" -f state=inactive >/dev/null || true
      gh api "repos/$R/deployments/$id" -X DELETE >/dev/null || true
    done
    ;;
  *) echo "unknown subcommand $cmd" >&2; exit 2;;
esac
