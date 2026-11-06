#!/usr/bin/env bash
# Stand-in for `wrangler` while a caller's dev_deploy_cmd runs (publish.sh installs it first on PATH AND over the project's
# node_modules/.bin/wrangler, so `wrangler`, `npx wrangler`, `pnpm exec wrangler` and package scripts all reach it).
# It enforces the publish-only contract BEFORE the real Wrangler can change anything:
#   - only publish-related subcommands (deploy / versions / deployments / whoami / version); everything else is refused
#   - a mutating call must pass exactly one --config and it must be the audited config; no --env / -e, no --name
#   - at the moment of the call, the audited config and the audited artifact must still be byte-identical to what build.sh audited
#     (a command that rebuilt first is refused before the upload, not detected afterwards)
# Facts for publish.sh: $CFV2_GUARD_DIR/blocked (refused, nothing changed) and /mutating (the real Wrangler was invoked).
# Env: CFV2_REAL_WRANGLER CFV2_GUARD_DIR CF_AUDITED_CONFIG CFV2_STATE_DIR CFV2_TOOLS_DIR
set -uo pipefail
: "${CFV2_REAL_WRANGLER:?}" "${CFV2_GUARD_DIR:?}" "${CF_AUDITED_CONFIG:?}" "${CFV2_STATE_DIR:?}" "${CFV2_TOOLS_DIR:?}"
refuse() { # <code> <why>
  printf 'code=%s\n' "$1" > "$CFV2_GUARD_DIR/blocked"
  echo "::error::dev_deploy_cmd refused before anything was published ($1): $2" >&2
  exit 87
}
G="$(node "$CFV2_TOOLS_DIR/cli.mjs" wrangler-guard --config "$CF_AUDITED_CONFIG" -- "$@")" || refuse guard_error "the publish guard could not evaluate the Wrangler call"
CODE="$(printf '%s\n' "$G" | sed -n 's/^block_code=//p')"
[ -z "$CODE" ] || refuse "$CODE" "$(printf '%s\n' "$G" | sed -n 's/^block_why=//p')"
# Only calls that can change the Worker need the integrity proof; read-only ones (whoami, deployments list, --version) pass.
case "${1:-}" in
  deploy|versions)
    [ "${1:-}" != versions ] || case "${2:-}" in upload|deploy) ;; *) exec "$CFV2_REAL_WRANGLER" "$@";; esac
    [ "$(sha256sum "$CF_AUDITED_CONFIG" | cut -d' ' -f1)" = "$(cat "$CFV2_STATE_DIR/config.sha")" ] || refuse config_changed "the audited Wrangler config differs from what was audited."
    NOW="$(node "$CFV2_TOOLS_DIR/cli.mjs" artifact-digest --config "$CF_AUDITED_CONFIG" | sed -n 's/^artifact_digest=//p')"
    [ -n "$NOW" ] && [ "$NOW" = "$(cat "$CFV2_STATE_DIR/artifact.sha")" ] || refuse artifact_changed "the deployable artifact differs from the one that was built and audited (the command rebuilt or replaced it)."
    : > "$CFV2_GUARD_DIR/mutating"
    ;;
esac
exec "$CFV2_REAL_WRANGLER" "$@"
