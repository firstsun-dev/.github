#!/usr/bin/env bash
# CF Worker v2 delivery engine. ONE implementation of
#   Build -> Config audit -> Migration (optional) -> Publish -> Smoke -> Recovery
# for every target (prod | preview | dev) and both modes (auto = branch/main push, manual = persistent Dev dispatch).
# actions/cf-deploy/action.yml calls one phase per step; every phase reads the workflow inputs from the validated
# inputs.json written by `init`, so no workflow text is interpolated into a script.
#
# State ($STATE_DIR, default $RUNNER_TEMP/cfv2, 0700): inputs.json, target, mode, build.env / smoke.env /
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
inp() { jq -r --arg k "$1" '.[$k] | if . == null then "" else tostring end' "$IN"; }
summary() { printf '%s\n' "$*" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"; }
setenv() { printf '%s=%s\n' "$1" "$2" >> "${GITHUB_ENV:-/dev/null}"; }
rd() { [ -f "$S/$1" ] && cat "$S/$1" || true; }

# Engine-owned context, derived from state and the runner (never from caller env text).
ctx() {
  [ -s "$S/target" ] || die "delivery engine not initialised"
  TARGET="$(rd target)"; MODE="$(rd mode)"
  APP_NAME="$(inp app_name)"; APP_PATH="$(inp app_path)"; APP_PATH="${APP_PATH:-.}"
  APP_DIR="${APP_DIR_OVERRIDE:-${GITHUB_WORKSPACE:?}/$APP_PATH}"
  BRANCH_REF="${GITHUB_REF:?}"; BRANCH="${GITHUB_REF#refs/heads/}"; RUN_SHA="${GITHUB_SHA:?}"
  DEV_ENV="$(inp dev_wrangler_env)"; DEV_ENV="${DEV_ENV:-dev}"
  WRANGLER_CONFIG="$(inp wrangler_config)"; WRANGLER_CONFIG="${WRANGLER_CONFIG:-wrangler.toml}"
  D1_BINDING="$(inp d1_binding)"
  case "$TARGET" in
    preview) HOST_WORKER="$(inp preview_worker_name)"; HOST_WORKER="${HOST_WORKER:-$APP_NAME}";;
    dev) HOST_WORKER="$(inp dev_worker_name)";;
    *) HOST_WORKER="$APP_NAME";;
  esac
  PREVIEW_NAME="$(rd preview_name)"; ENV_NAME="$(rd ledger_env)"; LEDGER_ID="$(rd ledger_id)"
  export TARGET MODE APP_NAME APP_DIR BRANCH_REF BRANCH RUN_SHA DEV_ENV WRANGLER_CONFIG D1_BINDING HOST_WORKER PREVIEW_NAME ENV_NAME STATE_DIR="$S"
}

# Which commands/env belong to the target. Never falls back across targets (except dev_build_cmd -> build_cmd).
target_build_cmd() { case "$TARGET" in dev) local c; c="$(inp dev_build_cmd)"; echo "${c:-$(inp build_cmd)}";; *) inp build_cmd;; esac; }
target_smoke_cmd() { case "$TARGET" in dev) local c; c="$(inp dev_smoke_cmd)"; echo "${c:-$(inp smoke_cmd)}";; *) inp smoke_cmd;; esac; }
target_url() { case "$TARGET" in prod) inp app_url_prod;; dev) inp app_url_dev;; *) echo "";; esac; }

stale_check() { # sets STALE for the rest of the job when the branch moved on; FAILS when the tip cannot be resolved
  ctx
  local fresh
  fresh="$("$TOOLS_DIR/guard.sh" fresh)" || die "cannot resolve the tip of branch $BRANCH ($1); refusing to continue (fail closed)"
  if [ "$fresh" != "stale=false" ]; then
    echo "::notice::Superseded ($1): a newer push of this branch owns the target. Nothing further is built, migrated or published by this run."
    [ -z "$LEDGER_ID" ] || "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" inactive "" "Superseded by a newer push before publish; nothing deployed" || true
    setenv STALE true
    return 1
  fi
}

case "$PHASE" in
  guard)
    stale_check "${2:-guard}" || true;;

  init)
    mkdir -p "$S"; chmod 700 "$S"; umask 077
    printf '%s' "${INPUTS_JSON:?}" > "$IN"; jq -e 'type == "object"' "$IN" >/dev/null || die "inputs_json is not an object"
    T="${ENGINE_TARGET:?}"; M="${ENGINE_MODE:-auto}"
    case "$T" in prod|preview|dev) ;; *) die "bad engine target: $T";; esac
    case "$M" in auto|manual) ;; *) die "bad engine mode: $M";; esac
    [ "$M" = auto ] || [ "$T" = dev ] || die "manual mode is only for the persistent Dev target"
    printf '%s' "$T" > "$S/target"; printf '%s' "$M" > "$S/mode"
    ctx
    STRATEGY="$(inp deployment_strategy)"; POLICY="$(inp production_rollback_policy)"
    case "$STRATEGY" in production-preview|hybrid|production-only) ;; *) die "bad deployment_strategy";; esac
    case "$POLICY" in automatic|manual|disabled) ;; *) die "bad production_rollback_policy";; esac
    if [ "$STRATEGY" = production-only ] && [ "$(inp shared_dev_on_branch_push)" = true ]; then
      die "deployment_strategy: production-only cannot be combined with shared_dev_on_branch_push: true; refusing to publish or migrate anything"
    fi
    EVENT="${GITHUB_EVENT_NAME:?}"
    if [ "$M" = manual ]; then
      ACTION="$(node "$TOOLS_DIR/cli.mjs" classify --event "$EVENT" --ref "$BRANCH_REF" --strategy "$STRATEGY" --dev-ref "$(inp persistent_dev_ref)" --dispatch-target "$(inp dispatch_target)" | sed -n 's/^action=//p')"
      [ "$ACTION" = persistent-dev ] || die "manual Dev is only allowed from the approved persistent_dev_ref via dispatch (classified '$ACTION')"
    elif [ "$T" = dev ]; then
      R="$(node "$TOOLS_DIR/cli.mjs" resolve-dev --event "$EVENT" --ref "$BRANCH_REF" --strategy "$STRATEGY" --shared-dev-on-push "$(inp shared_dev_on_branch_push)" --dispatch-target "$(inp dispatch_target)" | sed -n 's/^target=//p')"
      [ "$R" = dev ] || die "target mismatch (resolved '$R', engine 'dev'); refusing to deploy"
    else
      R="$(node "$TOOLS_DIR/cli.mjs" resolve --event "$EVENT" --ref "$BRANCH_REF" --strategy "$STRATEGY" --preview-on-push "$(inp preview_on_branch_push)" --dispatch-target "$(inp dispatch_target)" | sed -n 's/^target=//p')"
      [ "$R" = "$T" ] || die "target mismatch (resolved '$R', engine '$T'); refusing to deploy"
    fi
    # Each credential belongs to exactly one target; the job passes only that one and there is no fallback.
    [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ] || die "$T credentials not provided; refusing to fall back to other credentials"
    if [ "$T" = dev ]; then
      [ -n "$(inp dev_worker_name)" ] || die "Shared Dev requires dev_worker_name (the Dev Worker identity is always verified)"
      [ -n "$(inp app_url_dev)" ] || die "Shared Dev requires app_url_dev (a Dev deployment without a probe is not verified)"
    fi
    [ -n "$(target_build_cmd)" ] || die "no build command for target $T"
    if [ "$(inp d1_migrations)" = true ] && [ "$T" = dev ] && [ -z "$(inp dev_build_cmd)" ]; then
      die "Shared Dev with d1_migrations requires dev_build_cmd: the Dev build must succeed BEFORE the Dev D1 is migrated"
    fi
    # Caller env text -> validated data files. Reserved control names are rejected (parse-env.mjs RESERVED).
    case "$T" in prod) BE="$(inp build_env_prod)";; preview) BE="$(inp build_env_preview)";; dev) BE="$(inp build_env_dev)";; esac
    CFV2_BUILD_ENV="$BE" node "$TOOLS_DIR/parse-env.mjs" CFV2_BUILD_ENV "$S/build.env" --reserved >/dev/null
    CFV2_SMOKE_ENV="$(inp smoke_env)" node "$TOOLS_DIR/parse-env.mjs" CFV2_SMOKE_ENV "$S/smoke.env" --reserved >/dev/null
    if [ "$T" = preview ] && [ -n "${PREVIEW_SECRETS:-}" ]; then
      CFV2_PS="$PREVIEW_SECRETS" node "$TOOLS_DIR/parse-env.mjs" CFV2_PS "$S/preview.env" --mask --reserved >/dev/null
    fi;;

  prepare)
    ctx
    if [ "$TARGET" != prod ]; then
      VER="$(cd "$APP_DIR" && pnpm exec wrangler --version | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
      node "$TOOLS_DIR/cli.mjs" wrangler-check --version "$VER"
    fi
    # Preview: stable branch identity + ledger ownership (a collision fails BEFORE anything remote is touched), then
    # the SOURCE binding/auth-isolation preflight. Dev: single shared ledger. Prod: no ledger.
    if [ "$TARGET" = preview ]; then
      OUT="$("$TOOLS_DIR/guard.sh" identity)"
      printf '%s' "$(printf '%s\n' "$OUT" | sed -n 's/^PREVIEW_NAME=//p')" > "$S/preview_name"
      printf '%s' "$(printf '%s\n' "$OUT" | sed -n 's/^ENV_NAME=//p')" > "$S/ledger_env"
      (cd "$APP_DIR" && node "$TOOLS_DIR/cli.mjs" preflight --config "$WRANGLER_CONFIG" --allow "$(inp preview_binding_allow)" --deny "$(inp preview_deny_resources)")
      PAYLOAD="$(jq -nc --arg ref "$BRANCH_REF" --arg n "$(rd preview_name)" --arg w "$APP_NAME" --arg h "$HOST_WORKER" --arg r "$GITHUB_REPOSITORY" '{branch_ref:$ref,preview_name:$n,worker:$w,host_worker:$h,repo:$r}')"
      DESC="Native Preview"
    elif [ "$TARGET" = dev ]; then
      printf '%s' "$APP_NAME/dev/shared" > "$S/ledger_env"
      PAYLOAD="$(jq -nc --arg ref "$BRANCH_REF" --arg w "$APP_NAME" --arg d "$HOST_WORKER" --arg r "$GITHUB_REPOSITORY" --arg m "$MODE" '{branch_ref:$ref,worker:$w,dev_worker:$d,repo:$r,mode:$m}')"
      DESC="Shared Dev ($MODE)"
    fi
    if [ "$TARGET" != prod ]; then
      ID="$("$TOOLS_DIR/gh.sh" start "$(rd ledger_env)" "$RUN_SHA" "$PAYLOAD" "$DESC")"
      printf '%s' "$ID" > "$S/ledger_id"
      "$TOOLS_DIR/gh.sh" status "$ID" in_progress "" "Deploying $RUN_SHA from $BRANCH"
    fi;;

  build)
    ctx
    BUILD_CMD="$(target_build_cmd)" BUILD_ENV_FILE="$S/build.env" APP_VERSION="$(inp app_version)" PREVIEW_ENV_FILE="$S/preview.env" \
      PREVIEW_NAME="$PREVIEW_NAME" PREVIEW_HOST_CHECK="$(inp preview_host_check)" PREVIEW_ALLOW="$(inp preview_binding_allow)" PREVIEW_DENY="$(inp preview_deny_resources)" \
      DEV_WORKER="$(inp dev_worker_name)" DEV_ALLOW="$(inp dev_binding_allow)" DEV_DENY="$(inp dev_deny_resources)" PROD_URL="$(inp app_url_prod)" \
      "$TOOLS_DIR/build.sh";;

  audit-d1)
    ctx
    [ "$(inp d1_migrations)" = true ] || exit 0
    # The D1 the DEPLOYED config binds must be the exact physical database the migration targets, and the migration
    # directory must be real: checked read-only before any remote command.
    node "$TOOLS_DIR/cli.mjs" d1-consistency --config "$(rd config.path)" --source "$APP_DIR/$WRANGLER_CONFIG" --target "$TARGET" --binding "$D1_BINDING" --dev-env "$DEV_ENV" --mode "$(rd config.mode)"
    (cd "$APP_DIR" && node "$TOOLS_DIR/cli.mjs" migration-config --config "$WRANGLER_CONFIG" --target "$TARGET" --binding "$D1_BINDING" --dev-env "$DEV_ENV" --out "$S/migrate-check.json" | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}");;

  d1-guard)
    ctx
    [ "$TARGET" = dev ] && [ "$(inp d1_migrations)" = true ] || exit 0
    SHARED="$(cd "$APP_DIR" && node "$TOOLS_DIR/cli.mjs" d1-shared --config "$WRANGLER_CONFIG" --binding "$D1_BINDING" --dev-env "$DEV_ENV" | sed -n 's/^shared=//p')"
    echo "Preview and Dev share one physical D1: $SHARED (Preview migration: ${PREVIEW_MIGRATION:-unknown}, Preview job: ${PREVIEW_JOB_RESULT:-unknown})"
    if [ "$SHARED" = true ] && { [ "${PREVIEW_MIGRATION:-}" = failed ] || [ "${PREVIEW_JOB_RESULT:-}" = cancelled ]; }; then
      summary "### ❌ Shared Dev blocked — the Preview migration failed or was cancelled on the shared D1 (state unknown); Dev and its D1 unchanged."
      die "Preview migration '${PREVIEW_MIGRATION:-?}' (job ${PREVIEW_JOB_RESULT:-?}) on the D1 that Shared Dev also uses; refusing to touch Dev. Fix the migration and push again."
    fi;;

  migrate)
    ctx
    [ "$(inp d1_migrations)" = true ] || exit 0
    # The migration target is the engine-bound target; a mismatch with the composite-bound one is fatal.
    [ "${ENGINE_TARGET:?}" = "$TARGET" ] || die "target mismatch at migration (state '$TARGET', step '$ENGINE_TARGET')"
    OUT="$(TARGET="$TARGET" APP_DIR="$APP_DIR" WRANGLER_CONFIG="$WRANGLER_CONFIG" D1_BINDING="$D1_BINDING" DEV_ENV="$DEV_ENV" "$TOOLS_DIR/migrate.sh")"
    printf '%s\n' "$OUT"
    printf '%s' "$(printf '%s\n' "$OUT" | sed -n 's/^pending=//p')" > "$S/migration"
    echo "migration=applied";;

  publish)
    ctx
    [ "${ENGINE_TARGET:?}" = "$TARGET" ] || die "target mismatch at publish"
    COMMIT_MSG=""; [ -z "${GITHUB_EVENT_PATH:-}" ] || [ ! -f "$GITHUB_EVENT_PATH" ] || COMMIT_MSG="$(jq -r '.head_commit.message // ""' "$GITHUB_EVENT_PATH")"
    APP_URL="$(target_url)" OUT_FILE="$S/preview.json" APP_VERSION="$(inp app_version)" COMMIT_MSG="$COMMIT_MSG" \
      WORKER_NAME="$(inp preview_worker_name)" PUBLISH_CMD="$([ "$TARGET" = dev ] && inp dev_deploy_cmd || true)" \
      "$TOOLS_DIR/publish.sh";;

  smoke)
    ctx
    URL="$(target_url)"; [ "$TARGET" != preview ] || URL="${PUBLISHED_URL:-}"
    TARGET_URL="$URL" DEPLOY_TARGET="$TARGET" TEST_ENV="$TARGET" EXPECTED_SHA="$RUN_SHA" SMOKE_CMD="$(target_smoke_cmd)" SMOKE_CWD="$APP_DIR" SHA_PATH="$(inp sha_path)" \
      PROBE_PATH="$(inp probe_path)" PROBE_EXPECT_STATUS="$(inp probe_expect_status)" \
      ACCESS_TARGETS="$(inp access_protected_targets)" SMOKE_ENV_FILE="$S/smoke.env" PREVIEW_ENV_FILE="$S/preview.env" NO_PROXY='*' \
      "$TOOLS_DIR/smoke.sh";;

  verified) # smoke passed: published; "verified" only at content level (liveness = Access login reachable only)
    ctx
    URL="${PUBLISHED_URL:-$(target_url)}"
    LEVEL="${SMOKE_LEVEL:-}"
    case "$LEVEL" in
      content|content-sha) V=true; NOTE="verification: $LEVEL"; LEDGER_STATE=success; LEDGER_DESC="Serving $RUN_SHA ($LEVEL)";;
      *) V=false; NOTE="PUBLISHED, NOT VERIFIED — Cloudflare Access liveness only; content and commit NOT verified"
         LEDGER_STATE=inactive; LEDGER_DESC="Published $RUN_SHA; unverified (${LEVEL:-no smoke level}); not a known-good deployment";;
    esac
    echo "deployed_and_verified=$V"; echo "smoke_level=$LEVEL"; echo "url=$URL"; echo "published_url=$URL"
    if [ "$V" = true ]; then echo "verified_url=$URL"; echo "published_unverified=false"
    else echo "verified_url="; echo "published_unverified=true"
      echo "::warning::$TARGET $URL is published but NOT content-verified (smoke_level='$LEVEL'); no Release, no known-good record. Provide an authenticated probe (smoke_env PROBE_HEADERS) for content verification." >&2
    fi
    case "$TARGET" in
      preview)
        "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" "$LEDGER_STATE" "$URL" "$LEDGER_DESC"
        "$TOOLS_DIR/gh.sh" comment "$BRANCH" "$APP_NAME" "$URL" "$RUN_SHA" "$([ "$V" = true ] || echo 'UNVERIFIED: Access liveness only; content and commit not verified')" >&2 || echo "::warning::PR comment skipped" >&2
        echo "::notice title=Preview URL ($APP_NAME)::$URL" >&2
        summary "### $([ "$V" = true ] && echo ✅ || echo ⚠️) Preview — \`$APP_NAME\` ($NOTE)"
        summary "- URL: $URL · branch \`$BRANCH\` · identity \`$PREVIEW_NAME\` · commit \`$RUN_SHA\`";;
      dev)
        "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" "$LEDGER_STATE" "$URL" "$LEDGER_DESC from $BRANCH"
        summary "### $([ "$V" = true ] && echo ✅ || echo ⚠️) Shared Dev — \`$APP_NAME\` ($NOTE)"
        summary "- URL: $URL (one Dev for ALL branches; the last verified deployment wins) · branch \`$BRANCH\` · commit \`$RUN_SHA\`";;
      prod)
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
    fi;;

  recover-find)
    ctx
    ROW=""
    case "$TARGET" in
      preview) ROW="$("$TOOLS_DIR/gh.sh" last-good-preview "$(rd ledger_env)" "$LEDGER_ID" "$BRANCH_REF" "$HOST_WORKER" "$APP_NAME" || true)";;
      dev) ROW="$("$TOOLS_DIR/gh.sh" last-good-dev "$(rd ledger_env)" "$LEDGER_ID" "$HOST_WORKER" "$APP_NAME" || true)";;
    esac
    # Never act on a superseded run: a newer push of this branch will redeploy, and recovery must not replace it.
    if [ -n "$ROW" ] && [ "$("$TOOLS_DIR/guard.sh" fresh)" != "stale=false" ]; then
      summary "### ⚠️ Recovery skipped — a newer push of this branch superseded this run; its own run owns the target."
      ROW=""
    fi
    if [ -n "$ROW" ]; then
      echo "sha=${ROW%% *}"; echo "url=${ROW#* }"
    else
      echo "sha="; echo "url="
      echo "::error::No eligible known-good $TARGET deployment (same repository, application, ${TARGET} identity and host Worker '$HOST_WORKER'). Fix forward by pushing a new commit." >&2
      summary "### ⚠️ No prior verified $TARGET deployment on host Worker \`$HOST_WORKER\` — manual recovery required: push a fix."
    fi;;

  recover-run)
    ctx
    : "${GOOD_SHA:?}" "${GOOD_URL:?}"
    # The recovered deployment is the SAME pipeline (build -> audit -> publish -> smoke) on the last-good tree with
    # its OWN identity: the target build env is restored from the validated file with the failed SHA rebound to the
    # last-good SHA (smoke-only variables are never part of it). No migration: D1 is never rolled back.
    export APP_DIR_OVERRIDE="$GITHUB_WORKSPACE/recover/$APP_PATH"
    APP_DIR="$APP_DIR_OVERRIDE"
    R="$S/recover"; mkdir -p "$R"; chmod 700 "$R"
    export CFV2_REBIND_ENV; CFV2_REBIND_ENV="$(cat "$S/build.env")"
    CFV2_REBIND_ENV="$(node "$TOOLS_DIR/cli.mjs" rebind-env --var CFV2_REBIND_ENV --from "$RUN_SHA" --to "$GOOD_SHA")"
    node "$TOOLS_DIR/parse-env.mjs" CFV2_REBIND_ENV "$R/build.env" --reserved >/dev/null
    VERSION="$(APP_VERSION_IN="$(inp app_version)" node "$TOOLS_DIR/cli.mjs" rebind-env --var APP_VERSION_IN --from "$RUN_SHA" --to "$GOOD_SHA")"
    [ -z "$(inp app_version)" ] || [ "$VERSION" != "$(inp app_version)" ] || echo "::warning::app_version is not derived from the commit SHA; recovery keeps it as configured" >&2
    (cd "$APP_DIR" && pnpm install --frozen-lockfile)
    STATE_DIR="$R" BUILD_CMD="$(target_build_cmd)" BUILD_ENV_FILE="$R/build.env" APP_VERSION="$VERSION" PREVIEW_ENV_FILE="$S/preview.env" PREVIEW_NAME="$PREVIEW_NAME" \
      PREVIEW_HOST_CHECK=false PREVIEW_ALLOW="$(inp preview_binding_allow)" PREVIEW_DENY="$(inp preview_deny_resources)" \
      DEV_WORKER="$(inp dev_worker_name)" DEV_ALLOW="$(inp dev_binding_allow)" DEV_DENY="$(inp dev_deny_resources)" PROD_URL="$(inp app_url_prod)" "$TOOLS_DIR/build.sh" >&2
    OUT="$(STATE_DIR="$R" APP_URL="$(target_url)" OUT_FILE="$R/preview.json" APP_VERSION="$VERSION" WORKER_NAME="$(inp preview_worker_name)" \
      PUBLISH_CMD="$([ "$TARGET" = dev ] && inp dev_deploy_cmd || true)" "$TOOLS_DIR/publish.sh")"
    URL="$(printf '%s\n' "$OUT" | sed -n 's/^url=//p')"
    [ -n "$URL" ] || die "recovery produced no URL"
    [ "$URL" = "$GOOD_URL" ] || die "recovered URL $URL differs from the recorded last-good URL $GOOD_URL; refusing to verify a different deployment"
    echo "url=$URL";;

  recover-verify)
    ctx
    TARGET_URL="${RECOVERED_URL:?}" DEPLOY_TARGET="$TARGET" TEST_ENV="$TARGET" EXPECTED_SHA="${GOOD_SHA:?}" SMOKE_CMD="$(target_smoke_cmd)" SMOKE_CWD="$APP_DIR" SHA_PATH="$(inp sha_path)" \
      PROBE_PATH="$(inp probe_path)" PROBE_EXPECT_STATUS="$(inp probe_expect_status)" \
      ACCESS_TARGETS="$(inp access_protected_targets)" SMOKE_ENV_FILE="$S/smoke.env" PREVIEW_ENV_FILE="$S/preview.env" NO_PROXY='*' "$TOOLS_DIR/smoke.sh";;

  recover-record)
    ctx
    if [ "${RECOVERED:-}" = success ] && [ "${VERIFIED:-}" = success ] && { [ "${SMOKE_LEVEL:-}" = content ] || [ "${SMOKE_LEVEL:-}" = content-sha ]; }; then
      "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" failure "" "Smoke failed for $RUN_SHA; $TARGET restored to $GOOD_SHA and verified" || true
      summary "### ♻️ $TARGET restored to last good commit \`$GOOD_SHA\` and verified (content-level smoke). Run remains FAILED for the bad commit. D1 migrations (if any) were NOT reverted."
    elif [ "${RECOVERED:-}" = success ] && [ "${VERIFIED:-}" = success ]; then
      "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" failure "" "Smoke failed for $RUN_SHA; $TARGET redeployed from $GOOD_SHA — Access liveness only, content NOT verified" || true
      summary "### ⚠️ $TARGET redeployed from \`$GOOD_SHA\` but only Cloudflare Access liveness was observed (${SMOKE_LEVEL:-}): content and commit identity are **NOT verified**. Run remains FAILED. D1 migrations were NOT reverted."
    else
      "$TOOLS_DIR/gh.sh" status "$LEDGER_ID" error "" "Smoke failed for $RUN_SHA; recovery to $GOOD_SHA FAILED — $TARGET UNVERIFIED" || true
      summary "### 🚨 Automatic recovery FAILED — $TARGET serves unverified code. Push a fix (deleting the branch keeps the Preview)."
    fi
    if [ "${RECOVERED:-}" = success ] && [ "${VERIFIED:-}" = success ] && [ -z "$(inp sha_path)" ]; then
      summary "- Commit identity was NOT verified (set sha_path)."
    fi;;

  prod-recover)
    ctx
    POLICY="$(inp production_rollback_policy)"
    if [ "$POLICY" = automatic ] && [ -n "${PREVIOUS_VERSION_ID:-}" ]; then
      if ROLLBACK_TO="$PREVIOUS_VERSION_ID" "$TOOLS_DIR/publish.sh" >&2; then
        summary "### ♻️ Rolled back to \`$PREVIOUS_VERSION_ID\`. D1 migrations (if any) were NOT reverted. The run stays FAILED and no Release runs."
      else
        summary "### 🚨 Automatic rollback to \`$PREVIOUS_VERSION_ID\` FAILED — Production may serve the unverified version. Roll back manually: \`wrangler versions deploy $PREVIOUS_VERSION_ID@100 --yes\`."
      fi
    else
      summary "### 🚨 Production smoke FAILED (policy: $POLICY). Manual rollback: \`wrangler versions deploy ${PREVIOUS_VERSION_ID:-<prev>}@100 --yes\` after confirming schema compatibility."
    fi
    exit 1;;   # keep the run red regardless of policy

  cleanup)
    rm -rf "${STATE_DIR:-${RUNNER_TEMP:-/tmp}/cfv2}" 2>/dev/null || true;;

  *) die "unknown engine phase: $PHASE";;
esac
