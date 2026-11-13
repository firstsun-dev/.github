#!/usr/bin/env bash
# CF Worker v2 delivery engine. ONE implementation of
#   Build -> Config audit -> Migration (optional) -> Publish -> Smoke -> Recovery
# for every target (prod | preview | dev) and every trigger (push or dispatch: the trigger never selects a different pipeline).
# actions/cf-deploy/action.yml calls one phase per step; every phase reads the workflow inputs from the validated
# inputs.json written by `init`, so no workflow text is interpolated into a script.
#
# The delivery is (target production|preview|dev) x (driver worker|native-preview), bound once in `init` from the validated descriptor.
# DELIVERY_TARGET is the public target. TARGET is the config/credential class (prod | preview | dev): a native-preview Dev and a PR Preview
# are BOTH `preview` (wrangler preview, [previews] bindings; Dev is the fixed, protected Preview name `dev`). KIND is the internal persistence
# slot (production | native-branch | native-dev | persistent-dev) that keys the GitHub Environment and the known-good ledger; it is DERIVED
# from target x driver by delivery.mjs and never a public input. Everything delivery-specific (build env, URL, ledger, Environment, smoke
# mode) is resolved from it, never from application env.
#
# Stages (ENGINE_STAGE, set once in `init`): `all` = every phase in ONE job (Shared Dev, persistent Dev); the Production /
# Preview path is split across jobs that each re-run `init` (same inputs, same target binding) and exchange only
# validated DATA through `hydrate` - never runner files: migrate (Migration job), publish (Deploy job), verify (Smoke
# job, no Cloudflare credentials) and rollback (Rollback job). Shared Dev may opt in to the same split
# (`deploy` = migrate + publish in ONE job, then `verify` / `rollback`): a caller that must run a cross-service smoke
# AFTER another Worker's deployment (workflow input pipeline_phase) verifies the published Dev deployment later.
#
# State ($STATE_DIR, default $RUNNER_TEMP/cfv2, 0700): inputs.json, target, build.env / smoke.env /
# preview.env (validated KEY=VALUE data), config.* and artifact.sha (from build.sh), ledger_id, preview_name, ...
# Application env (build_env_*, smoke_env, preview secrets) is only ever loaded inside the process that needs it and
# can never set a reserved control variable (parse-env.mjs RESERVED). The target is bound ONCE in `init` (from the
# composite input, not from the environment) and re-read from state by every phase.
set -euo pipefail
PHASE="${1:?phase}"
# Absolute on purpose: the workflow passes it relative to the workspace root, but every stage `cd "$APP_DIR"` (a
# nested app_path such as apps/api) before using it.
TOOLS_DIR="$(cd "${TOOLS_DIR:-$(dirname "${BASH_SOURCE[0]}")}" && pwd)"
export TOOLS_DIR
S="${STATE_DIR:-${RUNNER_TEMP:?RUNNER_TEMP or STATE_DIR required}/cfv2}"
IN="$S/inputs.json"
die() { echo "::error::$*" >&2; exit 1; }
# One jq process per engine invocation: inputs.json is read into INP once (NUL-delimited, so values may hold newlines).
# `inp` runs inside $(...) subshells, so the cache must be filled in the parent shell (load_inputs) before it is used.
declare -A INP=()
load_inputs() {
  INP=(); local k v
  [ -f "$IN" ] || return 0
  while IFS= read -r -d '' k && IFS= read -r -d '' v; do INP[$k]="$v"; done \
    < <(jq -j 'if type == "object" then to_entries[] | (.key, "\u0000", (.value | if . == null then "" else tostring end), "\u0000") else empty end' "$IN" 2>/dev/null || true)
}
inp() { printf '%s' "${INP[$1]-}"; }
summary() { printf '%s\n' "$*" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"; }
setenv() { printf '%s=%s\n' "$1" "$2" >> "${GITHUB_ENV:-/dev/null}"; }
rd() { [ -f "$S/$1" ] && cat "$S/$1" || true; }

# Engine-owned context, derived from state and the runner (never from caller env text).
ctx() {
  [ -s "$S/target" ] || die "delivery engine not initialised"
  TARGET="$(rd target)"; KIND="$(rd kind)"; DELIVERY_TARGET="$(rd delivery_target)"
  [ -n "$KIND" ] && [ -n "$DELIVERY_TARGET" ] || die "delivery engine state has no delivery slot/target"
  APP_NAME="$(inp app_name)"; APP_PATH="$(inp app_path)"; APP_PATH="${APP_PATH:-.}"
  APP_DIR="${APP_DIR_OVERRIDE:-${GITHUB_WORKSPACE:?}/$APP_PATH}"
  if [ "${GITHUB_EVENT_NAME:-}" = pull_request ]; then
    # A same-repository PR Preview delivers the PR HEAD commit of the PR's branch (never the synthetic merge commit); the stale guard and the
    # ledger are keyed by that branch.
    PR_NUMBER="${PR_NUMBER:?pull request number}"; BRANCH="${PR_HEAD_REF:?pull request head ref}"; BRANCH_REF="refs/heads/$BRANCH"
    SOURCE_SHA="${PR_HEAD_SHA:?pull request head sha}"
  else
    PR_NUMBER=""; BRANCH_REF="${GITHUB_REF:?}"; BRANCH="${GITHUB_REF#refs/heads/}"; SOURCE_SHA="${GITHUB_SHA:?}"
  fi
  # Revision contract: the revision that PASSED CI (`tested`: the merge candidate for a PR, the pushed commit otherwise) is the revision that is
  # built, published and smoked (`RUN_SHA` = artifact = deployed). `SOURCE_SHA` is the PR head, kept for the stale checks and the ledger only.
  TESTED_SHA="${GITHUB_SHA:?}"; RUN_SHA="$TESTED_SHA"
  ARTIFACT_DIR="$(rd artifact_dir)"
  WRANGLER="${WRANGLER:-$TOOLS_DIR/node_modules/.bin/wrangler}"
  DEV_ENV="$(inp dev_wrangler_env)"; DEV_ENV="${DEV_ENV:-dev}"
  WRANGLER_CONFIG="$(inp wrangler_config)"; WRANGLER_CONFIG="${WRANGLER_CONFIG:-wrangler.toml}"
  D1_BINDING="$(inp d1_binding)"
  case "$TARGET" in
    preview) HOST_WORKER="$(inp preview_worker_name)"; HOST_WORKER="${HOST_WORKER:-$APP_NAME}";;
    dev) HOST_WORKER="$(inp dev_worker_name)";;
    *) HOST_WORKER="$APP_NAME";;
  esac
  PREVIEW_NAME="$(rd preview_name)"; ENV_NAME="$(rd ledger_env)"; LEDGER_ID="$(rd ledger_id)"
  [ "$KIND" != native-dev ] || PREVIEW_NAME=dev
  [ "$KIND" != native-branch ] || [ -n "$PREVIEW_NAME" ] || PREVIEW_NAME="pr-$PR_NUMBER"
  export SOURCE_SHA TESTED_SHA ARTIFACT_DIR WRANGLER
  export KIND TARGET DELIVERY_TARGET PR_NUMBER APP_NAME APP_DIR BRANCH_REF BRANCH RUN_SHA DEV_ENV WRANGLER_CONFIG D1_BINDING HOST_WORKER PREVIEW_NAME ENV_NAME STATE_DIR="$S"
}

# Which commands/env belong to the target. Never falls back across targets (except dev_build_cmd -> build_cmd).
target_build_cmd() { case "$DELIVERY_TARGET" in dev) local c; c="$(inp dev_build_cmd)"; echo "${c:-$(inp build_cmd)}";; *) inp build_cmd;; esac; }
target_smoke_cmd() { case "$DELIVERY_TARGET" in dev) local c; c="$(inp dev_smoke_cmd)"; echo "${c:-$(inp smoke_cmd)}";; *) inp smoke_cmd;; esac; }
# Declared (caller-supplied) canonical URL of the kind; Preview kinds additionally derive theirs from Cloudflare (resolve_target_url).
declared_url() {
  case "$KIND" in production) inp app_url_prod;; persistent-dev) inp app_url_dev;; native-dev) inp app_url_dev;; *) echo "";; esac
}
# The canonical URL THIS target is built for: resolved ONCE before the build (state file), else the declared one.
target_url() { if [ -s "$S/target_url" ]; then rd target_url; else declared_url; fi; }
# The canonical URL was resolved by the Plan BEFORE the build (the only Cloudflare read involved) and arrives as ENGINE_TARGET_URL. The Build re-validates it as
# an https origin and checks that the build env names no other origin as this application's own (callback / redirect / app origin). No Cloudflare access.
check_target_url() { # <build env file>
  local R
  R="$(node "$TOOLS_DIR/cli.mjs" resolve-url --kind "$KIND" --name "$PREVIEW_NAME" --host-worker "$HOST_WORKER" --explicit "$(target_url)" --host-check false --build-env "$1")" \
    || die "the planned canonical URL of $KIND is invalid for this build; nothing was built"
  printf '%s\n' "$R" | grep -v '^target_url=\|^url_source=' >&2 || true
  echo "::notice::canonical URL of $KIND for this build: $(target_url)" >&2
}
# GitHub Environment (deployment history, protection rules) of the kind. Production / Preview / Dev names are unchanged.
gh_env() { case "$KIND" in production) echo "$APP_NAME/prod";; native-branch) echo "$APP_NAME/preview";; native-dev) echo "$APP_NAME/native-dev";; persistent-dev) echo "$APP_NAME/dev";; esac; }
# Smoke commands/modes are per kind: `basic` runs the HTTP/health verification without the application smoke command.
smoke_mode() { rd smoke_mode; }
kind_smoke_cmd() { if [ "$(smoke_mode)" = basic ]; then echo ""; else target_smoke_cmd; fi; }

# BUILD (unprivileged job only): the audited build plus the packaged artifact. PUBLISH (privileged job): publishes the verified artifact; never builds.
run_build() { # <build env file> <app version> -> artifact in $ARTIFACT_DIR
  STATE_DIR="$S" BUILD_CMD="$(target_build_cmd)" BUILD_ENV_FILE="$1" APP_VERSION="$2" PREVIEW_ENV_FILE="$S/preview.env" \
    PREVIEW_NAME="$PREVIEW_NAME" PREVIEW_HOST_CHECK=false PREVIEW_ALLOW="$(inp preview_binding_allow)" PREVIEW_DENY="$(inp preview_deny_resources)" \
    DEV_WORKER="$(inp dev_worker_name)" DEV_ALLOW="$(inp dev_binding_allow)" DEV_DENY="$(inp dev_deny_resources)" PROD_URL="$(inp app_url_prod)" \
    DELIVERY_KIND="$KIND" TARGET_URL="$(target_url)" ARTIFACT_DIR="$ARTIFACT_DIR" "$TOOLS_DIR/build.sh"
}
run_publish() { # <out file> <app version> <commit message>: publishes ONLY $ARTIFACT_DIR (verified by artifact-verify, re-verified by publish.sh)
  STATE_DIR="$S" APP_URL="$(target_url)" OUT_FILE="$1" APP_VERSION="$2" COMMIT_MSG="$3" ARTIFACT_DIR="$ARTIFACT_DIR" WRANGLER="$WRANGLER" \
    WORKER_NAME="$HOST_WORKER" \
    PREVIEW_NAME="$PREVIEW_NAME" EXPECTED_URL="$([ "$TARGET" = preview ] && target_url || true)" "$TOOLS_DIR/publish.sh"
}
# The privileged side's OWN expectations (descriptor, GitHub context, inputs, event) - never the artifact's claims.
artifact_env() {
  local drv url
  drv="$(rd driver)"; url="$(target_url)"
  export CFV2_REPOSITORY="$GITHUB_REPOSITORY" CFV2_APP="$APP_NAME" CFV2_TARGET="$DELIVERY_TARGET" CFV2_DRIVER="$drv" CFV2_SLOT="$KIND" \
    CFV2_RUN_ID="${GITHUB_RUN_ID:-0}" CFV2_RUN_ATTEMPT="${GITHUB_RUN_ATTEMPT:-1}" CFV2_SOURCE_SHA="$SOURCE_SHA" CFV2_TESTED_SHA="$TESTED_SHA" CFV2_ARTIFACT_SHA="$TESTED_SHA" CFV2_TARGET_URL="$url"
}

stale_check() { # sets STALE for the rest of the job when the branch moved on; FAILS when the tip cannot be resolved
  ctx
  local fresh
  fresh="$(PR_NUMBER="$PR_NUMBER" SOURCE_SHA="$SOURCE_SHA" "$TOOLS_DIR/guard.sh" fresh)" || die "cannot resolve the current state of $BRANCH ($1); refusing to continue (fail closed)"
  if [ "$fresh" != "stale=false" ]; then
    echo "::notice::Superseded ($1): a newer push of this branch owns the target. Nothing further is built, migrated or published by this run."
    [ -z "$LEDGER_ID" ] || "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" inactive "" "Superseded by a newer push before publish; nothing deployed" || true
    setenv STALE true
    return 1
  fi
}

load_inputs
case "$PHASE" in
  guard)
    stale_check "${2:-guard}" || true;;

  init)
    # One job may run several engine invocations (the Migration job validates every target, then migrates): each starts from a clean state.
    # Only the engine's own default state dir (<runner temp>/cfv2) is ever wiped - never a path an earlier step could have redirected.
    [ "$S" != "${RUNNER_TEMP:-}/cfv2" ] || rm -rf -- "$S"
    mkdir -p "$S"; chmod 700 "$S"; umask 077
    printf '%s' "${INPUTS_JSON:?}" > "$IN"
    # ONE Node process validates everything pure (target/kind/stage/mode binding, descriptor, strategy and policy enums, enabled kind of
    # this event, per-target requirements, URL isolation, env data) and writes the validated state files. Nothing is mutated if it fails.
    node "$TOOLS_DIR/cli.mjs" init-checks --state "$S" --inputs "$IN" | { grep -v '^kind=\|^smoke_mode=' || true; }
    load_inputs
    # The Plan resolved the canonical URL (the only step that may need Cloudflare to know it) and the artifact lives in a directory the workflow downloaded.
    [ -z "${ENGINE_ARTIFACT_DIR:-}" ] || printf '%s' "$ENGINE_ARTIFACT_DIR" > "$S/artifact_dir"
    [ -z "${ENGINE_TARGET_URL:-}" ] || printf '%s' "$ENGINE_TARGET_URL" > "$S/target_url"
    # Prod, Preview and Dev all use the one account credential pair (CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID). The stages that RUN APPLICATION CODE
    # - the Build and the Smoke - never receive Cloudflare credentials; the stages that hold them (migrate, publish, rollback) never run an application build.
    case "$(rd stage)" in
      verify|build)
        if [ -n "${CLOUDFLARE_API_TOKEN:-}${CLOUDFLARE_ACCOUNT_ID:-}" ]; then die "the $(rd stage) stage must not receive Cloudflare credentials"; fi;;
      *) [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ] || die "$(rd target) credentials not provided (set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID)";;
    esac;;

  migration-gate) # publish stage, before anything is built or published: the result of THE Migration job this kind depends on
    ctx
    D1_MIGRATIONS="$(inp d1_migrations)" DESCRIPTOR="${ENGINE_DESCRIPTOR:?a target descriptor is required}" \
      node "$TOOLS_DIR/cli.mjs" migration-gate --kind "$KIND";;

  prepare)
    ctx
    setenv CFV2_DEPS "done"   # the dependencies are installed (Setup step precedes this phase): a later engine call of the same job skips the install
    if [ "$TARGET" != prod ]; then
      VER="$("$WRANGLER" --version | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
      node "$TOOLS_DIR/cli.mjs" wrangler-check --version "$VER"
    fi
    # Preview: stable branch identity + ledger ownership (a collision fails BEFORE anything remote is touched), then
    # the SOURCE binding/auth-isolation preflight. Dev: single shared ledger. Prod: no ledger.
    if [ "$KIND" = native-dev ]; then
      # The stable named Preview `dev`: fixed identity (no branch ownership), one ledger per app, shared by all branches.
      printf '%s' dev > "$S/preview_name"; printf '%s' "$APP_NAME/native-dev/dev" > "$S/ledger_env"
      PAYLOAD="$(jq -nc --arg ref "$BRANCH_REF" --arg w "$APP_NAME" --arg h "$HOST_WORKER" --arg r "$GITHUB_REPOSITORY" '{kind:"native-dev",branch_ref:$ref,preview_name:"dev",worker:$w,host_worker:$h,repo:$r}')"
      DESC="Native Dev Preview"
    elif [ "$TARGET" = preview ]; then
      OUT="$(PR_NUMBER="$PR_NUMBER" "$TOOLS_DIR/guard.sh" identity)"
      printf '%s' "$(printf '%s\n' "$OUT" | sed -n 's/^PREVIEW_NAME=//p')" > "$S/preview_name"
      printf '%s' "$(printf '%s\n' "$OUT" | sed -n 's/^ENV_NAME=//p')" > "$S/ledger_env"
      PAYLOAD="$(jq -nc --arg ref "$BRANCH_REF" --arg n "$(rd preview_name)" --arg w "$APP_NAME" --arg h "$HOST_WORKER" --arg r "$GITHUB_REPOSITORY" '{kind:"native-branch",branch_ref:$ref,preview_name:$n,worker:$w,host_worker:$h,repo:$r}')"
      DESC="Native Preview"
    elif [ "$TARGET" = dev ]; then
      printf '%s' "$APP_NAME/dev/shared" > "$S/ledger_env"
      PAYLOAD="$(jq -nc --arg ref "$BRANCH_REF" --arg w "$APP_NAME" --arg d "$HOST_WORKER" --arg r "$GITHUB_REPOSITORY" '{kind:"persistent-dev",branch_ref:$ref,worker:$w,dev_worker:$d,repo:$r}')"
      DESC="Shared Dev"
    fi
    # The Migration job runs the identity / preflight checks but opens no ledger entry (the Deploy job owns it).
    if [ "$TARGET" != prod ] && [ "${PREPARE_LEDGER:-true}" = true ]; then
      ID="$("$TOOLS_DIR/gh.sh" start "$(rd ledger_env)" "$RUN_SHA" "$PAYLOAD" "$DESC")"
      printf '%s' "$ID" > "$S/ledger_id"
      "$TOOLS_DIR/gh.sh" status "$ID" in_progress "" "Deploying $RUN_SHA from $BRANCH"
    fi;;

  build) # UNPRIVILEGED Build job: audited target build + packaged artifact. No Cloudflare credential, no ledger, no remote call.
    ctx
    [ -n "$ARTIFACT_DIR" ] || die "no artifact directory"
    check_target_url "$S/build.env"
    run_build "$S/build.env" "$(inp app_version)";;

  artifact-verify) # PRIVILEGED jobs: the artifact must be what THIS job expects, then the resource audit runs on the PACKAGED config. Nothing is built.
    ctx
    [ -d "$ARTIFACT_DIR" ] || die "no artifact at '$ARTIFACT_DIR': the Build job did not hand one over; refusing"
    artifact_env
    V="$(node "$TOOLS_DIR/cli.mjs" verify-artifact --dir "$ARTIFACT_DIR")" || die "artifact verification failed; nothing was migrated or published"
    MODE="$(printf '%s\n' "$V" | sed -n 's/^config_mode=//p')"; CFG_PATH="$ARTIFACT_DIR/wrangler.json"
    case "$TARGET" in
      preview) node "$TOOLS_DIR/cli.mjs" preflight --config "$CFG_PATH" --allow "$(inp preview_binding_allow)" --deny "$(inp preview_deny_resources)" >&2 || die "the packaged Preview config violates the resource policy";;
      prod)
        if [ "$MODE" = effective ]; then node "$TOOLS_DIR/cli.mjs" audit-prod --effective "$CFG_PATH" --source "$APP_DIR/$WRANGLER_CONFIG" --allow "$(inp preview_binding_allow)" >&2 || die "the packaged Production config violates the resource policy"; fi;;
      dev)
        if [ "$MODE" = effective ]; then DEVEFF="$CFG_PATH"; DEVSRC="$APP_DIR/$WRANGLER_CONFIG"; else DEVEFF=""; DEVSRC="$CFG_PATH"; fi
        node "$TOOLS_DIR/cli.mjs" dev-config-check --effective "$DEVEFF" --source "$DEVSRC" --worker "$(inp dev_worker_name)" --deny "$(inp dev_deny_resources)" --dev-env "$DEV_ENV" \
          --allow "$(inp dev_binding_allow)" --prod-url "$(inp app_url_prod)" >&2 || die "the packaged Dev config violates the resource policy";;
    esac
    printf '%s' "$CFG_PATH" > "$S/config.path"; printf '%s' "$MODE" > "$S/config.mode"
    sha256sum "$CFG_PATH" | cut -d' ' -f1 > "$S/config.sha"
    printf '%s\n' "$V" | sed -n 's/^artifact_digest=//p' > "$S/artifact.sha"
    echo "artifact_digest=$(rd artifact.sha)"; echo "artifact_sha=$TESTED_SHA";;

  audit-d1)
    ctx
    [ "$(inp d1_migrations)" = true ] || exit 0
    # The D1 the DEPLOYED config binds must be the exact physical database the migration targets, and the migration
    # directory must be real: checked read-only before any remote command.
    D1R="$(node "$TOOLS_DIR/cli.mjs" d1-consistency --config "$(rd config.path)" --source "$APP_DIR/$WRANGLER_CONFIG" --target "$TARGET" --binding "$D1_BINDING" --dev-env "$DEV_ENV" --mode "$(rd config.mode)")" || die "the built $KIND config does not bind the audited D1"
    printf '%s\n' "$D1R" >&2
    D1_ID="$(printf '%s\n' "$D1R" | sed -n 's/^d1_id=//p')"
    [ -n "$D1_ID" ] || die "the built $KIND config binds no D1 database id"
    # Pre-mutation / pre-publish proof: the DEPLOYED binding is the physical D1 the plan migrates (never another database).
    if [ -n "$(rd d1_expect)" ] && [ "$D1_ID" != "$(rd d1_expect)" ]; then
      die "the built $KIND config binds D1 '$D1_ID' but the plan migrates '$(rd d1_expect)'; refusing (the artifact would run against an unmigrated or foreign database)"
    fi
    printf '%s' "$D1_ID" > "$S/d1_id"
    (cd "$APP_DIR" && node "$TOOLS_DIR/cli.mjs" migration-config --config "$WRANGLER_CONFIG" --target "$TARGET" --binding "$D1_BINDING" --dev-env "$DEV_ENV" --out "$S/migrate-check.json" | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}");;

  precheck) # Migration job, BEFORE any build: read-only state of the planned owner's physical D1. Mutates nothing, builds nothing.
    ctx
    [ "$(inp d1_migrations)" = true ] || exit 0
    [ "${ENGINE_TARGET:?}" = "$TARGET" ] || die "target mismatch at the migration precheck (state '$TARGET', step '$ENGINE_TARGET')"
    [ -n "$(rd d1_expect)" ] || die "the migration precheck needs the plan's expected D1 id"
    setenv CFV2_DEPS "done"
    OUT="$(MIGRATE_MODE=precheck D1_EXPECT="$(rd d1_expect)" TARGET="$TARGET" APP_DIR="$APP_DIR" WRANGLER_CONFIG="$WRANGLER_CONFIG" D1_BINDING="$D1_BINDING" DEV_ENV="$DEV_ENV" "$TOOLS_DIR/migrate.sh")" || die "the D1 migration precheck failed; nothing was built, migrated or published (fail closed)"
    PENDING="$(printf '%s\n' "$OUT" | sed -n 's/^pending=//p')"; P_ID="$(printf '%s\n' "$OUT" | sed -n 's/^d1_id=//p')"
    case "$PENDING" in true|false) ;; *) die "the D1 migration precheck returned no verdict; failing closed";; esac
    [ "$P_ID" = "$(rd d1_expect)" ] || die "the source config binds D1 '$P_ID' for $KIND but the plan expects '$(rd d1_expect)'; refusing (nothing was read or changed beyond a SELECT)"
    # Superseded while reading: never authorizes anything (no marker is printed); the Delivery's gate then refuses the run.
    stale_check precheck || exit 0
    printf '%s' "$P_ID" > "$S/d1_id"
    if [ "$PENDING" = false ]; then
      echo "::notice::No pending D1 migrations on $P_ID (read-only precheck): the Migration job builds nothing; every Deploy still builds, audits and checks the D1 binding." >&2
      echo "precheck=verified_no_pending"; echo "precheck_sha=$RUN_SHA"; echo "schema_changed=false"; echo "d1_scan=none"
    else echo "precheck=pending"; fi;;

  migrate)
    ctx
    [ "$(inp d1_migrations)" = true ] || exit 0
    # The migration target is the engine-bound target; a mismatch with the composite-bound one is fatal.
    [ "${ENGINE_TARGET:?}" = "$TARGET" ] || die "target mismatch at migration (state '$TARGET', step '$ENGINE_TARGET')"
    OUT="$(TARGET="$TARGET" APP_DIR="$APP_DIR" WRANGLER_CONFIG="$WRANGLER_CONFIG" D1_BINDING="$D1_BINDING" DEV_ENV="$DEV_ENV" "$TOOLS_DIR/migrate.sh")"
    printf '%s\n' "$OUT"
    PENDING="$(printf '%s\n' "$OUT" | sed -n 's/^pending=//p')"
    printf '%s' "$PENDING" > "$S/migration"
    echo "schema_changed=$PENDING"
    echo "kind=$KIND"; echo "d1_id=$(rd d1_id)"
    echo "migration=applied"
    SCAN_L="$(printf '%s\n' "$OUT" | sed -n 's/^d1_scan=//p' | head -1)"
    jq -cn --arg c "$PENDING" --arg s "$SCAN_L" --arg k "$KIND" --arg i "$(rd d1_id)" '{migration:"applied",schema_changed:$c,d1_scan:$s,kind:$k,d1_id:$i}' > "$S/migration.json";;

  report) # final, always-run state report of a stage (data only)
    ctx
    echo "d1_id=$(rd d1_id)"; echo "target_url=$(rd target_url)"; echo "stale=${STALE:-false}"; echo "kind=$KIND";;

  fail-migrate) # the Migration job failed: nothing was published; the remote D1 state is whatever wrangler left
    ctx
    summary "### ❌ $TARGET D1 migration failed or was cancelled — nothing was published and the Worker is unchanged. The D1 may be partially migrated: inspect it before pushing again (migrations are never reverted automatically)."
    ;;

  publish)
    ctx
    [ "${ENGINE_TARGET:?}" = "$TARGET" ] || die "target mismatch at publish"
    # Resolve the audited Worker name BEFORE publishing: the Rollback job restores by name, so a config without a
    # safe name must stop here, while nothing is changed yet.
    AUDITED_NAME=""
    if [ "$TARGET" = prod ]; then AUDITED_NAME="$(node "$TOOLS_DIR/cli.mjs" worker-name --config "$(rd config.path)" | sed -n 's/^worker_name=//p')"; [ -n "$AUDITED_NAME" ] || die "no safe Worker name in the audited config; refusing to publish"; fi
    COMMIT_MSG=""; [ -z "${GITHUB_EVENT_PATH:-}" ] || [ ! -f "$GITHUB_EVENT_PATH" ] || COMMIT_MSG="$(jq -r '.head_commit.message // .pull_request.title // ""' "$GITHUB_EVENT_PATH")"
    artifact_env
    run_publish "$S/preview.json" "$(inp app_version)" "$COMMIT_MSG"
    # Reached only when publish.sh succeeded: the hand-off of this deployment to the Smoke / Rollback jobs.
    echo "published=true"; echo "published_sha=$RUN_SHA"; echo "artifact_digest=$(rd artifact.sha)"; echo "source_sha=$SOURCE_SHA"; echo "ledger_id=$LEDGER_ID"; echo "preview_name=$PREVIEW_NAME"
    echo "worker_name=$AUDITED_NAME"; echo "kind=$KIND"; echo "target_url=$(target_url)"; echo "d1_id=$(rd d1_id)";;

  hydrate) # Smoke / Rollback jobs: validated hand-off from the Migration / Deploy / Smoke jobs (data, never runner files)
    ctx
    D="$(printf '%s' "${HANDOFF_DEPLOY:-}" | jq -c 'if type == "object" then . else {} end' 2>/dev/null || echo '{}')"
    M="$(printf '%s' "${HANDOFF_MIGRATION:-}" | jq -c 'if type == "object" then . else {} end' 2>/dev/null || echo '{}')"
    K="$(printf '%s' "${HANDOFF_SMOKE:-}" | jq -c 'if type == "object" then . else {} end' 2>/dev/null || echo '{}')"
    val() { # <json> <key> <regex>: value must match (or be empty), else the hand-off is refused
      local v; v="$(printf '%s' "$1" | jq -r --arg k "$2" '.[$k] | if . == null then "" else tostring end')"
      [[ "$v" =~ $3 ]] || die "hand-off '$2' is malformed; refusing to continue"
      printf '%s' "$v"
    }
    H_URL="$(val "$D" url '^(https://[^[:space:]]+)?$')"
    H_VER="$(val "$D" version_id '^[A-Za-z0-9-]*$')"
    H_PREV="$(val "$D" previous_version_id '^[A-Za-z0-9-]*$')"
    H_WORKER="$(val "$D" worker_name '^([a-z0-9][a-z0-9-]{0,62})?$')"
    H_LEDGER="$(val "$D" ledger_id '^[0-9]*$')"
    H_PNAME="$(val "$D" preview_name '^([a-z0-9]+(-[a-z0-9]+)*)?$')"
    H_PUB="$(val "$D" published '^(true|false)?$')"
    H_PUBSHA="$(val "$D" published_sha '^([0-9a-f]{7,40})?$')"
    H_KIND="$(val "$D" kind '^(production|native-branch|native-dev|persistent-dev)?$')"
    H_TURL="$(val "$D" target_url '^(https://[^[:space:]]+)?$')"
    M_D1="$(val "$M" d1_id '^[A-Za-z0-9_-]*$')"
    H_PENDING="$(val "$M" schema_changed '^(true|false)?$')"
    H_SCAN="$(val "$M" d1_scan '^(none|additive|non-additive|unknown)?$')"
    H_MIG="$(val "$M" migration '^(applied|failed|none)?$')"
    H_LEVEL="$(val "$K" smoke_level '^(liveness|content|content-sha)?$')"
    [ "$H_PUB" = true ] || die "the Deploy job did not report a published deployment; nothing to verify or roll back"
    # Each kind receives ONLY its own results: a hand-off of another kind (or without a kind) is refused, fail closed.
    [ "$H_KIND" = "$KIND" ] || die "hand-off belongs to kind '${H_KIND:-<none>}', this stage is '$KIND'; refusing to act on another target's deployment"
    [ -z "$H_TURL" ] || [ "$H_URL" = "$H_TURL" ] || die "hand-off published $H_URL but the build was for $H_TURL; refusing"
    if [ -n "${HANDOFF_MIGRATION:-}" ] && [ "$HANDOFF_MIGRATION" != '{}' ]; then
      [ -z "$M_D1" ] || [ -z "$(rd d1_expect)" ] || [ "$M_D1" = "$(rd d1_expect)" ] || die "migration hand-off is for D1 '$M_D1', this kind's D1 is '$(rd d1_expect)'; refusing"
    fi
    [ -z "$H_PUBSHA" ] || [ "$H_PUBSHA" = "$RUN_SHA" ] || die "hand-off was published from $H_PUBSHA, this run is $RUN_SHA; refusing to act on another deployment"
    [ "$TARGET" = prod ] || [ -n "$H_URL" ] || die "$TARGET hand-off has no published URL"
    # The Migration job's validated result, kept for the non-Production recovery guard (recover-find). Absent when none was handed over.
    if [ -n "${HANDOFF_MIGRATION:-}" ] && [ "$HANDOFF_MIGRATION" != '{}' ]; then printf '%s' "$M" > "$S/migration.json"; else rm -f "$S/migration.json"; fi
    printf '%s' "$H_LEDGER" > "$S/ledger_id"; printf '%s' "$H_PNAME" > "$S/preview_name"
    case "$KIND" in native-branch) printf '%s' "$APP_NAME/preview/$H_PNAME" > "$S/ledger_env";; native-dev) printf '%s' "$APP_NAME/native-dev/dev" > "$S/ledger_env";; persistent-dev) printf '%s' "$APP_NAME/dev/shared" > "$S/ledger_env";; esac
    [ "$TARGET" != preview ] || [ -n "$H_PNAME" ] || die "preview hand-off has no Preview identity"
    [ "$KIND" != native-dev ] || [ "$H_PNAME" = dev ] || die "native-dev hand-off names Preview '$H_PNAME', not 'dev'; refusing to act on another Preview"
    setenv PUBLISHED_URL "$H_URL"; setenv VERSION_ID "$H_VER"; setenv PREVIOUS_VERSION_ID "$H_PREV"
    setenv AUDITED_WORKER "$H_WORKER"; setenv D1_PENDING "$H_PENDING"; setenv D1_SCAN "$H_SCAN"; setenv HANDOFF_SMOKE_LEVEL "$H_LEVEL"
    echo "hydrated: target=$TARGET url=${H_URL:-<none>} version=${H_VER:-?} previous=${H_PREV:-?} migration=${H_MIG:-?}/schema_changed=${H_PENDING:-?}/${H_SCAN:-?}" >&2
    echo "published=true"; echo "url=$H_URL"; echo "version_id=$H_VER"; echo "previous_version_id=$H_PREV"; echo "worker_name=$H_WORKER"
    echo "ledger_id=$H_LEDGER"; echo "preview_name=$H_PNAME"; echo "published_sha=$RUN_SHA";;

  gate-blocked) # a required cross-service dependency did not succeed: this deployment CANNOT be verified (fail closed)
    ctx
    URL="${PUBLISHED_URL:-$(target_url)}"
    echo "::error::Cross-service verification blocked: the required upstream job finished '${UPSTREAM_GATE:-?}'. $TARGET $URL (commit $RUN_SHA) is published but NOT verified; Release is blocked." >&2
    summary "### ❌ Cross-service smoke blocked — the upstream job it depends on finished \`${UPSTREAM_GATE:-?}\`. $TARGET \`$URL\` (commit \`$RUN_SHA\`) is **published but unverified**; Release is blocked."
    if [ "$TARGET" = prod ]; then
      summary "- No automatic rollback: the failure is not attributable to this Worker. Fix or re-run the upstream job, then re-run the verification, or roll back manually: \`wrangler versions deploy ${PREVIOUS_VERSION_ID:-<previous-version-id>}@100 --name ${AUDITED_WORKER:-<worker>} --yes\` (only after confirming the previous version runs on the CURRENT D1 schema; migration this run: schema_changed=${D1_PENDING:-?}, scan=${D1_SCAN:-?})."
    else
      [ -z "$LEDGER_ID" ] || "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" failure "$URL" "Cross-service smoke blocked (upstream ${UPSTREAM_GATE:-?}) — $TARGET UNVERIFIED" || true
      summary "- No automatic recovery: the failure is not attributable to this Worker. Fix or re-run the upstream job."
    fi
    "$TOOLS_DIR/gh.sh" mark-env-deployment "$(gh_env)" "$RUN_SHA" failure "Cross-service smoke blocked — UNVERIFIED (served $RUN_SHA)" || echo "::warning::could not annotate the Environment deployment as unverified" >&2
    echo "blocked=true";;

  smoke)
    ctx
    # smoke_mode=skip: NO request of any kind. The deployment stays Published/Unverified (the `verified` phase maps the
    # `skipped` level to unverified: no verified_url, no known-good ledger record, no Release).
    if [ "$(smoke_mode)" = skip ]; then
      echo "::notice::smoke_mode=skip for $KIND: no post-publish verification was performed; the deployment is Published/Unverified" >&2
      echo "smoke_level=skipped"; exit 0
    fi
    URL="$(target_url)"; [ "$TARGET" != preview ] || URL="${PUBLISHED_URL:-}"
    TARGET_URL="$URL" DEPLOY_TARGET="$TARGET" TEST_ENV="$TARGET" EXPECTED_SHA="$RUN_SHA" SMOKE_CMD="$(kind_smoke_cmd)" SMOKE_CWD="$APP_DIR" SHA_PATH="$(inp sha_path)" \
      PROBE_PATH="$(inp probe_path)" PROBE_EXPECT_STATUS="$(inp probe_expect_status)" \
      ACCESS_TARGETS="$(inp access_protected_targets)" SMOKE_ENV_FILE="$S/smoke.env" PREVIEW_ENV_FILE="$S/preview.env" NO_PROXY='*' \
      "$TOOLS_DIR/smoke.sh";;

  verified) # smoke passed: published; "verified" only at content level (liveness = Access login reachable only)
    ctx
    URL="${PUBLISHED_URL:-$(target_url)}"
    LEVEL="${SMOKE_LEVEL:-}"
    VO="$(node "$TOOLS_DIR/cli.mjs" verification-outcome --level "$LEVEL")"   # the ONE rule: only a content-level smoke verifies
    case "$(printf '%s\n' "$VO" | sed -n 's/^verification_kind=//p')" in
      content) V=true; NOTE="verification: $LEVEL"; LEDGER_STATE=success; LEDGER_DESC="Serving $RUN_SHA ($LEVEL)";;
      skipped) V=false; NOTE="PUBLISHED, NOT VERIFIED — smoke_mode=skip; no verification was performed"
         LEDGER_STATE=inactive; LEDGER_DESC="Published $RUN_SHA; unverified (smoke_mode=skip); not a known-good deployment";;
      *) V=false; NOTE="PUBLISHED, NOT VERIFIED — Cloudflare Access liveness only; content and commit NOT verified"
         LEDGER_STATE=inactive; LEDGER_DESC="Published $RUN_SHA; unverified (${LEVEL:-no smoke level}); not a known-good deployment";;
    esac
    echo "deployed_and_verified=$V"; echo "smoke_level=$LEVEL"; echo "url=$URL"; echo "published_url=$URL"
    if [ "$V" = true ]; then echo "verified_url=$URL"; echo "published_unverified=false"
    else echo "verified_url="; echo "published_unverified=true"
      echo "::warning::$KIND $URL is published but NOT content-verified (smoke_level='$LEVEL'); no Release, no known-good record. Provide an authenticated probe (smoke_env PROBE_HEADERS) for content verification." >&2
    fi
    case "$KIND" in
      native-dev)
        "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" "$LEDGER_STATE" "$URL" "$LEDGER_DESC from $BRANCH"
        summary "### $([ "$V" = true ] && echo ✅ || echo ⚠️) Native Dev Preview — \`$APP_NAME\` ($NOTE)"
        summary "- URL: $URL (stable Preview \`dev\`; the last verified deployment of ANY branch wins) · branch \`$BRANCH\` · commit \`$RUN_SHA\`";;
      native-branch)
        "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" "$LEDGER_STATE" "$URL" "$LEDGER_DESC"
        "$TOOLS_DIR/gh.sh" comment "$BRANCH" "$APP_NAME" "$URL" "$RUN_SHA" "$([ "$V" = true ] || echo 'UNVERIFIED: Access liveness only; content and commit not verified')" >&2 || echo "::warning::PR comment skipped" >&2
        echo "::notice title=Preview URL ($APP_NAME)::$URL" >&2
        summary "### $([ "$V" = true ] && echo ✅ || echo ⚠️) Preview — \`$APP_NAME\` ($NOTE)"
        summary "- URL: $URL · branch \`$BRANCH\` · identity \`$PREVIEW_NAME\` · commit \`$RUN_SHA\`";;
      persistent-dev)
        "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" "$LEDGER_STATE" "$URL" "$LEDGER_DESC from $BRANCH"
        summary "### $([ "$V" = true ] && echo ✅ || echo ⚠️) Shared Dev — \`$APP_NAME\` ($NOTE)"
        summary "- URL: $URL (one Dev for ALL branches; the last verified deployment wins) · branch \`$BRANCH\` · commit \`$RUN_SHA\`";;
      production)
        summary "### $([ "$V" = true ] && echo ✅ || echo ⚠️) Production — \`$APP_NAME\` ($NOTE)"
        summary "- Version: \`${VERSION_ID:-}\` · previous: \`${PREVIOUS_VERSION_ID:-}\` · rollback policy \`$(inp production_rollback_policy)\`. A Worker rollback does NOT revert D1 migrations or external side effects."
        [ "$V" = true ] || summary "- Release is **blocked**: Production was not content-verified.";;
    esac;;

  fail-prepublish) # a phase before/at publish failed
    ctx
    case "$TARGET" in
      preview)
        if [ "${PREVIEW_MUTATED:-}" = true ]; then
          "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" error "" "Publish ran but returned no usable URL; Preview UNVERIFIED" || true
          summary "### ❌ Native Preview publish returned **no usable URL** — the Preview may have been modified and is **unverified**. No automatic recovery (nothing to verify against). Check that host Worker \`$HOST_WORKER\` has Preview URLs enabled, then push again."
        else
          "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" error "" "Failed before publish; previous Preview unchanged" || true
          summary "### ❌ Failed before publish — existing Preview unchanged."
        fi;;
      dev)
        if [ "${PUBLISH_STARTED:-}" = true ]; then
          [ -z "$LEDGER_ID" ] || "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" error "" "Publish failed part-way; Dev UNVERIFIED" || true
          summary "### ❌ Shared Dev publish failed part-way — Dev may serve an unverified deployment."
        else
          [ -z "$LEDGER_ID" ] || "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" error "" "Failed before publish; Dev unchanged" || true
          summary "### ❌ Shared Dev failed before publish — the running Dev deployment is unchanged."
        fi;;
      prod) summary "### ❌ Production deployment failed before the smoke${PUBLISH_STARTED:+ (publish had started: verify the live version)}.";;
    esac;;

  unverified) # published, smoke failed
    ctx
    URL="${PUBLISHED_URL:-$(target_url)}"
    if [ "$TARGET" != prod ]; then
      "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" failure "$URL" "Smoke failed — $TARGET UNVERIFIED"
      summary "### ❌ $TARGET smoke failed — $URL is **unverified** (serves $RUN_SHA)."
    else
      POLICY="$(inp production_rollback_policy)"
      summary "### ❌ Production smoke FAILED — $URL is **unverified** (serves $RUN_SHA, version \`${VERSION_ID:-?}\`). Release is blocked."
      if [ "$POLICY" = automatic ]; then
        summary "- The Rollback job (policy \`automatic\`) decides next; it refuses an unsafe rollback and then prints the manual recovery commands."
      else
        summary "- Rollback policy is \`$POLICY\`: nothing is rolled back automatically. Manual rollback (only after confirming the previous Worker version runs on the CURRENT D1 schema; migration this run: schema_changed=${D1_PENDING:-?}, scan=${D1_SCAN:-?}):"
        summary "  \`wrangler versions deploy ${PREVIOUS_VERSION_ID:-<previous-version-id>}@100 --name ${AUDITED_WORKER:-<worker>} --yes\`"
      fi
    fi
    # The Environment deployment GitHub created for the Deploy job is "success" (it published). Record that it is NOT verified.
    "$TOOLS_DIR/gh.sh" mark-env-deployment "$(gh_env)" "$RUN_SHA" failure "Smoke failed — UNVERIFIED (served $RUN_SHA)" || echo "::warning::could not annotate the Environment deployment as unverified" >&2;;

  recover-find) # non-Production recovery. A recovery would REBUILD the last known-good revision, and no credential-bearing job may build application code:
    ctx           # it is therefore blocked (fail closed) until a verified artifact is retained for it. Production never rebuilds: it re-deploys a stored Worker VERSION.
    BC=no_retained_artifact
    BW="redeploying an older $TARGET deployment needs its retained, verified artifact; none is kept, and a credential-bearing job must not rebuild application code."
    echo "::error::Automatic recovery of $TARGET is BLOCKED ($BC): $BW" >&2
    summary "### 🛑 Automatic recovery of $TARGET **blocked** (\`$BC\`) — $TARGET serves \`$RUN_SHA\`, which FAILED its smoke, and is **UNVERIFIED**."
    summary "- Why: $BW"
    summary "- Nothing was redeployed and the D1 schema was NOT reverted. Required manual recovery: push a forward fix (preferred)."
    [ -z "$LEDGER_ID" ] || "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" failure "" "Smoke failed for $RUN_SHA; recovery blocked ($BC) — $TARGET UNVERIFIED" || true
    "$TOOLS_DIR/gh.sh" mark-env-deployment "$(gh_env)" "$RUN_SHA" failure "Smoke failed, recovery blocked ($BC) — UNVERIFIED (served $RUN_SHA)" || echo "::warning::could not annotate the Environment deployment as unverified" >&2
    echo "sha="; echo "url="; echo "blocked=true"; echo "block_code=$BC";;

  prod-rollback) # Rollback job, Production: fail-closed. Prints rollback_result=rolled_back|blocked|failed (always exits 0)
    ctx
    [ "$TARGET" = prod ] || die "prod-rollback is Production only"
    POLICY="$(inp production_rollback_policy)"; PREV="${PREVIOUS_VERSION_ID:-}"; NEW="${VERSION_ID:-}"; W="${AUDITED_WORKER:-}"
    MANUAL="wrangler versions deploy ${PREV:-<previous-version-id>}@100 --name ${W:-<worker>} --yes"
    blocked() { # <code> <why>
      echo "rollback_result=blocked"; echo "rollback_reason=$1"
      summary "### 🚨 Automatic rollback BLOCKED ($1) — Production STILL serves the unverified version \`${NEW:-?}\` ($RUN_SHA)."
      summary "- $2"
      summary "- Manual recovery, after confirming the previous version runs on the current D1 schema: \`$MANUAL\`"
      exit 0
    }
    # The decision (policy, identity, no D1 schema rollback, never over a newer/foreign deployment) is delivery.mjs prodRollbackBlock.
    guard() { # [VAR=value ...] -> refuses (exit 0 with rollback_result=blocked) when the guard says so
      local G c
      G="$(env POLICY="$POLICY" PREVIOUS="$PREV" CURRENT="$NEW" WORKER="$W" D1_PENDING="${D1_PENDING:-}" D1_COMPAT="$(inp d1_rollback_compat)" D1_SCAN="${D1_SCAN:-}" "$@" \
        node "$TOOLS_DIR/cli.mjs" rollback-guard)"
      c="$(printf '%s\n' "$G" | sed -n 's/^block_code=//p')"
      [ -z "$c" ] || blocked "$c" "$(printf '%s\n' "$G" | sed -n 's/^block_why=//p')"
    }
    guard
    RB="$S/rollback"; mkdir -p "$RB"; node -e 'const [n,f]=process.argv.slice(1);require("fs").writeFileSync(f,JSON.stringify({name:n,compatibility_date:"2025-01-01"}))' "$W" "$RB/wrangler.json"
    LIVE="$(cd "$RB" && "$WRANGLER" deployments list --config "$RB/wrangler.json" --json 2>/dev/null \
      | jq -r 'sort_by(.created_on) | reverse | .[0].versions | max_by(.percentage // 0) | .version_id // empty' 2>/dev/null || echo "")"
    # Never roll back a deployment that is no longer ours (someone deployed out of band): fail closed.
    guard LIVE="$LIVE"
    if (cd "$RB" && "$WRANGLER" versions deploy "$PREV@100" --config "$RB/wrangler.json" --message "Revert: smoke failed on $RUN_SHA" --yes) >&2; then
      echo "rollback_result=rolled_back"; echo "rollback_reason=rolled back to $PREV"
    else
      echo "rollback_result=failed"; echo "rollback_reason=wrangler versions deploy failed"
      summary "### 🚨 Automatic rollback to \`$PREV\` FAILED — Production may serve the unverified version \`$NEW\`. Roll back manually: \`$MANUAL\`."
    fi;;

  prod-rollback-verify) # smoke the RESTORED deployment: previous commit when resolvable, else "no longer serves the failed commit"
    ctx
    RB="$S/rollback"; PREV_SHA=""
    VIEW="$(cd "$RB" && "$WRANGLER" versions view "${PREVIOUS_VERSION_ID:?}" --config "$RB/wrangler.json" --json 2>/dev/null || true)"
    PREV_SHA="$(printf '%s' "$VIEW" | jq -r '.annotations["workers/message"] // .message // empty' 2>/dev/null | grep -oE '^\[[0-9a-f]{7}\]' | tr -d '[]' | head -1 || true)"
    [ -z "$PREV_SHA" ] || PREV_SHA="$(cd "$APP_DIR" && git rev-parse --verify --quiet "$PREV_SHA^{commit}" 2>/dev/null || echo "$PREV_SHA")"
    echo "previous_commit=$PREV_SHA"
    TARGET_URL="$(target_url)" DEPLOY_TARGET=prod TEST_ENV=prod EXPECTED_SHA="$PREV_SHA" ABSENT_SHA="$RUN_SHA" SMOKE_CMD="$(kind_smoke_cmd)" SMOKE_CWD="$APP_DIR" SHA_PATH="$(inp sha_path)" \
      PROBE_PATH="$(inp probe_path)" PROBE_EXPECT_STATUS="$(inp probe_expect_status)" ACCESS_TARGETS="$(inp access_protected_targets)" \
      SMOKE_ENV_FILE="$S/smoke.env" PREVIEW_ENV_FILE="$S/preview.env" NO_PROXY='*' "$TOOLS_DIR/smoke.sh";;

  rollback-finish) # final, target-independent verdict of the Rollback job (ok=true only when restored AND content-verified)
    ctx
    RV="$(env TARGET="$TARGET" RB_RESULT="${RB_RESULT:-}" RB_VERIFY="${RB_VERIFY:-}" RB_LEVEL="${RB_LEVEL:-}" GOOD_SHA="${GOOD_SHA:-}" RECOVERED="${RECOVERED:-}" VERIFIED="${VERIFIED:-}" \
      SMOKE_LEVEL="${SMOKE_LEVEL:-}" node "$TOOLS_DIR/cli.mjs" rollback-verdict)"   # delivery.mjs rollbackVerdict
    R="$(printf '%s\n' "$RV" | sed -n 's/^result=//p')"; LEVEL="$(printf '%s\n' "$RV" | sed -n 's/^level=//p')"
    if [ "$TARGET" = prod ]; then
      case "$R" in
        restored_verified)
          summary "### ♻️ Production rolled back to \`${PREVIOUS_VERSION_ID:-?}\` and verified ($LEVEL). The run stays **FAILED** for $RUN_SHA and Release is blocked. D1 migrations (if any) were NOT reverted."
          "$TOOLS_DIR/gh.sh" mark-env-deployment "$(gh_env)" "$RUN_SHA" inactive "Rolled back to ${PREVIOUS_VERSION_ID:-previous} after a failed smoke" || true;;
        restored_unverified) summary "### ⚠️ Production rolled back to \`${PREVIOUS_VERSION_ID:-?}\` but only liveness could be observed (${LEVEL:-no level}): the restored deployment is NOT content-verified. Run FAILED.";;
        restored_unhealthy) summary "### 🚨 Production was rolled back to \`${PREVIOUS_VERSION_ID:-?}\` but the post-rollback smoke FAILED — Production may be unhealthy. Investigate now. Run FAILED.";;
      esac
    fi
    echo "result=$R"; echo "level=$LEVEL"; echo "ok=$([ "$R" = restored_verified ] && echo true || echo false)";;

  cleanup)
    rm -rf "${STATE_DIR:-${RUNNER_TEMP:-/tmp}/cfv2}" 2>/dev/null || true;;

  *) die "unknown engine phase: $PHASE";;
esac
