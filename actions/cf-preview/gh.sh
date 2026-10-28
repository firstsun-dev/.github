#!/usr/bin/env bash
# GitHub Deployment ledger + PR feedback for branch Previews. Needs GH_TOKEN, GITHUB_REPOSITORY.
# Subcommands: start | status | mark-env-deployment | newest-id | last-good | last-good-preview | comment | environments | owner | retire | purge
set -euo pipefail
R="${GITHUB_REPOSITORY:?}"
cmd="${1:?}"; shift

# scan_good <env> <exclude-id> [jq --arg ...] -- <jq select filter>: newest matching deployment whose latest
# status is success with a valid https environment_url; prints "<sha> <url>".
scan_good() {
  local env="$1" exclude="$2" jqargs=()
  shift 2
  while [ "$1" != -- ]; do jqargs+=("$1"); shift; done
  shift
  local filter="$1" list id sha row st url
  list="$(gh api "repos/$R/deployments?environment=$(printf %s "$env" | jq -sRr @uri)&per_page=30")" || exit 1
  while read -r id sha; do
    [ -n "$id" ] || continue
    [ "$id" = "$exclude" ] && continue
    row="$(gh api "repos/$R/deployments/$id/statuses?per_page=1" -q '.[0] | "\(.state) \(.environment_url) \(.description)"')"
    st="${row%% *}"; rest="${row#* }"; url="${rest%% *}"; desc="${rest#* }"
    # Known-good = success AND content-level verification recorded by the engine ("Serving <sha> (content|content-sha)").
    # Liveness-only and legacy records without the marker are never eligible.
    if [ "$st" = success ] && [[ "$url" =~ ^https://[^[:space:]]+$ ]] && [[ "$desc" =~ \((content|content-sha)\) ]]; then echo "$sha $url"; return 0; fi
  done < <(printf '%s' "$list" | jq -r "${jqargs[@]}" ".[] | $filter | \"\\(.id) \\(.sha)\"")
}

case "$cmd" in
  start) # <env> <sha> <payload-json> [description] -> deployment id
    gh api "repos/$R/deployments" -X POST --input - -q .id <<JSON
{"ref":"$2","environment":"$1","description":"${4:-Native Preview}","auto_merge":false,"required_contexts":[],"transient_environment":true,"production_environment":false,"payload":$3}
JSON
    ;;
  status) # <id> <state> [url] [description]
    gh api "repos/$R/deployments/$1/statuses" -f state="$2" -f environment_url="${3:-}" -f log_url="${GITHUB_SERVER_URL:-https://github.com}/$R/actions/runs/${GITHUB_RUN_ID:-0}" -f description="${4:-}" >/dev/null
    ;;
  mark-env-deployment) # <env> <sha> <state> <description> : append a status to the NEWEST deployment of <env> at <sha>
    # (the one GitHub created for the Deploy job, which only says "published"). Fails when there is none.
    id="$(gh api "repos/$R/deployments?environment=$(printf %s "$1" | jq -sRr @uri)&sha=$2&per_page=1" -q '.[0].id // empty')"
    [ -n "$id" ] || { echo "no deployment of $1 at $2" >&2; exit 1; }
    gh api "repos/$R/deployments/$id/statuses" -f state="$3" -f log_url="${GITHUB_SERVER_URL:-https://github.com}/$R/actions/runs/${GITHUB_RUN_ID:-0}" -f description="$4" >/dev/null
    ;;
  newest-id) # <env> -> id of the NEWEST deployment of <env> (any state), or nothing
    gh api "repos/$R/deployments?environment=$(printf %s "$1" | jq -sRr @uri)&per_page=1" -q '.[0].id // empty'
    ;;
  last-good) # <env> [exclude-id] -> sha of newest deployment whose latest status is success
    for row in $(gh api "repos/$R/deployments?environment=$(printf %s "$1" | jq -sRr @uri)&per_page=30" -q '.[] | "\(.id):\(.sha)"'); do
      id="${row%%:*}"; sha="${row#*:}"
      [ "$id" = "${2:-}" ] && continue
      st="$(gh api "repos/$R/deployments/$id/statuses?per_page=1" -q '.[0].state // empty')"
      if [ "$st" = "success" ]; then echo "$sha"; exit 0; fi
    done
    ;;
  last-good-preview) # <env> <exclude-id> <branch_ref> <host_worker> <app> -> "<sha> <url>" of the newest SUCCESSFUL deployment
    # whose recorded identity matches exactly: repository, application, branch ref, HOST Worker (a Preview hosted
    # under another Worker, e.g. blog-preview vs blog, is a different Preview even with the same environment
    # string) and a valid https URL on its latest status. Legacy records without host_worker are never eligible.
    scan_good "$1" "${2:-}" --arg repo "$R" --arg ref "$3" --arg host "$4" --arg app "$5" -- \
      'select(.payload.repo == $repo and .payload.branch_ref == $ref and .payload.host_worker == $host and .payload.worker == $app)'
    ;;
  last-good-dev) # <env> <exclude-id> <dev_worker> <app> -> "<sha> <url>" of the newest verified Shared Dev deployment
    # (any branch; Dev is one shared Worker) recorded for THIS Dev Worker with a valid URL.
    scan_good "$1" "${2:-}" --arg repo "$R" --arg dev "$3" --arg app "$4" -- \
      'select(.payload.repo == $repo and .payload.dev_worker == $dev and .payload.worker == $app)'
    ;;
  comment) # <branch> <app_name> <url> <sha> [note] : only same-repo PRs from this exact head branch
    owner="${R%%/*}"
    marker="<!-- cf-preview:$2 -->"
    body="$marker
**Preview ($2)**: $3
Commit: \`$4\` · branch \`$1\` · Preview is branch-owned and persists after this PR closes.${5:+
**$5**}"
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
  owner) # <env> -> full branch ref that owns this Preview name (newest ledger payload), or nothing
    gh api "repos/$R/deployments?environment=$(printf %s "$1" | jq -sRr @uri)&per_page=1" -q '.[0].payload.branch_ref // empty'
    ;;
  retire) # <env> : mark every deployment of this environment inactive (kept as history)
    for id in $(gh api "repos/$R/deployments?environment=$(printf %s "$1" | jq -sRr @uri)&per_page=100" --paginate -q '.[].id'); do
      gh api "repos/$R/deployments/$id/statuses" -f state=inactive -f description="Branch deleted; Preview kept" >/dev/null || true
    done
    ;;
  purge) # <env> : mark inactive and delete all deployments of this environment
    for id in $(gh api "repos/$R/deployments?environment=$(printf %s "$1" | jq -sRr @uri)&per_page=100" --paginate -q '.[].id'); do
      gh api "repos/$R/deployments/$id/statuses" -f state=inactive >/dev/null || true
      gh api "repos/$R/deployments/$id" -X DELETE >/dev/null || true
    done
    ;;
  *) echo "unknown subcommand $cmd" >&2; exit 2;;
esac
