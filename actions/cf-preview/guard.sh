#!/usr/bin/env bash
# Side-effect-free preconditions shared by Migration and Deploy. Needs GH_TOKEN, GITHUB_REPOSITORY, TOOLS_DIR.
#   guard.sh identity   Preview name from the branch ref; fails when a DIFFERENT full branch ref owns it in the
#                       ledger. Prints PREVIEW_NAME= / ENV_NAME=.
#   guard.sh fresh      Prints stale=false when the branch tip is RUN_SHA; stale=true when superseded; FAILS when
#                       the tip cannot be resolved (deleted branch, API error): never mistaken for "fresh".
# Env: BRANCH BRANCH_REF RUN_SHA APP_NAME [HOST_WORKER]
set -euo pipefail
case "${1:?}" in
  identity)
    : "${BRANCH_REF:?}" "${APP_NAME:?}"
    out="$(node "${TOOLS_DIR:?}/cli.mjs" identity --repo "$GITHUB_REPOSITORY" --worker "$APP_NAME" --host-worker "${HOST_WORKER:-$APP_NAME}" --ref "$BRANCH_REF" --pr "${PR_NUMBER:-}")"
    name="$(printf '%s\n' "$out" | sed -n 's/^preview_name=//p')"
    [[ "$name" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || { echo "::error::unsafe Preview name: $name" >&2; exit 1; }
    env_name="$APP_NAME/preview/$name"
    # Names are hashless: two branches can normalise to the same name ("Feat/A" vs "feat-a"). The ledger records
    # which full ref owns a name; refuse BEFORE anything remote (migration, publish) is touched.
    owner="$("$TOOLS_DIR/gh.sh" owner "$env_name")"
    if [ -n "$owner" ] && [ "$owner" != "$BRANCH_REF" ]; then
      echo "::error::Preview name for $BRANCH_REF is already used by $owner ($env_name). Rename the branch, or retire that Preview and its ledger first; refusing to overwrite it." >&2
      exit 1
    fi
    echo "PREVIEW_NAME=$name"; echo "ENV_NAME=$env_name"
    ;;
  fresh)
    : "${BRANCH:?}" "${RUN_SHA:?}"
    if [ -n "${PR_NUMBER:-}" ]; then
      # A PR Preview delivers the revision that PASSED CI (the merge candidate RUN_SHA). It is still current only while the PR is open, its head is still the
      # one this run was triggered for (SOURCE_SHA) and GitHub still computes the same merge candidate (base unchanged). Anything else - including a
      # merge commit that is not computed yet - fails closed: a newer synchronization owns the Preview.
      : "${SOURCE_SHA:?}"
      # GitHub recomputes the merge candidate asynchronously after a head / base update: while the head is still ours and the PR open, retry before declaring stale.
      attempt=0
      while :; do
        row="$(gh api "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER" -q '[.state, .head.sha, (.merge_commit_sha // "")] | @tsv')" || { echo "::error::cannot read pull request #$PR_NUMBER; refusing to continue" >&2; exit 1; }
        state="$(printf '%s' "$row" | cut -f1)"; head="$(printf '%s' "$row" | cut -f2)"; merge="$(printf '%s' "$row" | cut -f3)"
        if [ "$state" = open ] && [ "$head" = "$SOURCE_SHA" ] && [ "$merge" = "$RUN_SHA" ]; then echo "stale=false"; exit 0; fi
        attempt=$((attempt + 1))
        if [ "$state" = open ] && [ "$head" = "$SOURCE_SHA" ] && [ "$attempt" -lt 3 ]; then sleep "${GUARD_RETRY_SLEEP:-4}"; continue; fi
        break
      done
      echo "::notice::PR #$PR_NUMBER is $state at head $head / merge ${merge:-none}; this run tested head $SOURCE_SHA / merge $RUN_SHA - a newer synchronization owns the Preview." >&2; echo "stale=true"
      exit 0
    fi
    tip="$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/$BRANCH" -q .object.sha)" || { echo "::error::cannot resolve the tip of branch $BRANCH (deleted or API error); refusing to continue" >&2; exit 1; }
    [ -n "$tip" ] || { echo "::error::empty branch tip for $BRANCH" >&2; exit 1; }
    if [ "$tip" = "$RUN_SHA" ]; then echo "stale=false"; else echo "::notice::Branch tip $tip != $RUN_SHA; a newer push owns this target." >&2; echo "stale=true"; fi
    ;;
  *) echo "usage: guard.sh identity|fresh" >&2; exit 2;;
esac
