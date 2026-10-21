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
    out="$(node "${TOOLS_DIR:?}/cli.mjs" identity --repo "$GITHUB_REPOSITORY" --worker "$APP_NAME" --host-worker "${HOST_WORKER:-$APP_NAME}" --ref "$BRANCH_REF")"
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
    tip="$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/$BRANCH" -q .object.sha)" || { echo "::error::cannot resolve the tip of branch $BRANCH (deleted or API error); refusing to continue" >&2; exit 1; }
    [ -n "$tip" ] || { echo "::error::empty branch tip for $BRANCH" >&2; exit 1; }
    if [ "$tip" = "$RUN_SHA" ]; then echo "stale=false"; else echo "::notice::Branch tip $tip != $RUN_SHA; a newer push owns this target." >&2; echo "stale=true"; fi
    ;;
  *) echo "usage: guard.sh identity|fresh" >&2; exit 2;;
esac
