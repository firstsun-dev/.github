#!/usr/bin/env bash
# CF Worker v2 delivery engine. ONE implementation of
#   Build -> Config audit -> Migration (optional) -> Publish -> Smoke -> Recovery
# for every target (prod | preview | dev) and both modes (auto = branch/main push, manual = persistent Dev dispatch).
# actions/cf-deploy/action.yml calls one phase per step; every phase reads the workflow inputs from the validated
# inputs.json written by `init`, so no workflow text is interpolated into a script.
#
# Stages (ENGINE_STAGE, set once in `init`): `all` = every phase in ONE job (Shared Dev, persistent Dev); the Production /
# Preview path is split across jobs that each re-run `init` (same inputs, same target binding) and exchange only
# validated DATA through `hydrate` - never runner files: migrate (Migration job), publish (Deploy job), verify (Smoke
# job, no Cloudflare credentials) and rollback (Rollback job).
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
    T="${ENGINE_TARGET:?}"; M="${ENGINE_MODE:-auto}"; STAGE="${ENGINE_STAGE:-all}"
    case "$T" in prod|preview|dev) ;; *) die "bad engine target: $T";; esac
    case "$STAGE" in all|migrate|publish|verify|rollback) ;; *) die "bad engine stage: $STAGE";; esac
    # Production / Preview are always split; Shared Dev and persistent Dev keep the single-job engine.
    if [ "$T" = dev ]; then [ "$STAGE" = all ] || die "Shared Dev runs as one job (stage all), got '$STAGE'"
    else [ "$STAGE" != all ] || die "$T must run as split stages (migrate/publish/verify/rollback), not 'all'"; fi
    printf '%s' "$STAGE" > "$S/stage"
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
    # The Smoke job (stage verify) never receives Cloudflare credentials: it only probes the published URL.
    if [ "$STAGE" != verify ]; then
      [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ] || die "$T credentials not provided; refusing to fall back to other credentials"
    elif [ -n "${CLOUDFLARE_API_TOKEN:-}${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
      die "the Smoke stage must not receive Cloudflare credentials"
    fi
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
    # The Migration job runs the identity / preflight checks but opens no ledger entry (the Deploy job owns it).
    if [ "$TARGET" != prod ] && [ "${PREPARE_LEDGER:-true}" = true ]; then
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
    PENDING="$(printf '%s\n' "$OUT" | sed -n 's/^pending=//p')"
    printf '%s' "$PENDING" > "$S/migration"
    echo "schema_changed=$PENDING"
    echo "migration=applied";;

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
    COMMIT_MSG=""; [ -z "${GITHUB_EVENT_PATH:-}" ] || [ ! -f "$GITHUB_EVENT_PATH" ] || COMMIT_MSG="$(jq -r '.head_commit.message // ""' "$GITHUB_EVENT_PATH")"
    APP_URL="$(target_url)" OUT_FILE="$S/preview.json" APP_VERSION="$(inp app_version)" COMMIT_MSG="$COMMIT_MSG" \
      WORKER_NAME="$(inp preview_worker_name)" PUBLISH_CMD="$([ "$TARGET" = dev ] && inp dev_deploy_cmd || true)" \
      "$TOOLS_DIR/publish.sh"
    # Reached only when publish.sh succeeded: the hand-off of this deployment to the Smoke / Rollback jobs.
    echo "published=true"; echo "published_sha=$RUN_SHA"; echo "ledger_id=$LEDGER_ID"; echo "preview_name=$PREVIEW_NAME"
    echo "worker_name=$AUDITED_NAME";;

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
    H_PENDING="$(val "$M" schema_changed '^(true|false)?$')"
    H_SCAN="$(val "$M" d1_scan '^(none|additive|non-additive|unknown)?$')"
    H_MIG="$(val "$M" migration '^(applied|failed|none)?$')"
    H_LEVEL="$(val "$K" smoke_level '^(liveness|content|content-sha)?$')"
    [ "$H_PUB" = true ] || die "the Deploy job did not report a published deployment; nothing to verify or roll back"
    [ -z "$H_PUBSHA" ] || [ "$H_PUBSHA" = "$RUN_SHA" ] || die "hand-off was published from $H_PUBSHA, this run is $RUN_SHA; refusing to act on another deployment"
    [ "$TARGET" = prod ] || [ -n "$H_URL" ] || die "$TARGET hand-off has no published URL"
    printf '%s' "$H_LEDGER" > "$S/ledger_id"; printf '%s' "$H_PNAME" > "$S/preview_name"
    case "$TARGET" in preview) printf '%s' "$APP_NAME/preview/$H_PNAME" > "$S/ledger_env";; esac
    [ "$TARGET" != preview ] || [ -n "$H_PNAME" ] || die "preview hand-off has no Preview identity"
    setenv PUBLISHED_URL "$H_URL"; setenv VERSION_ID "$H_VER"; setenv PREVIOUS_VERSION_ID "$H_PREV"
    setenv AUDITED_WORKER "$H_WORKER"; setenv D1_PENDING "$H_PENDING"; setenv D1_SCAN "$H_SCAN"; setenv HANDOFF_SMOKE_LEVEL "$H_LEVEL"
    echo "hydrated: target=$TARGET url=${H_URL:-<none>} version=${H_VER:-?} previous=${H_PREV:-?} migration=${H_MIG:-?}/schema_changed=${H_PENDING:-?}/${H_SCAN:-?}" >&2
    echo "published=true"; echo "url=$H_URL"; echo "version_id=$H_VER"; echo "previous_version_id=$H_PREV"; echo "worker_name=$H_WORKER"
    echo "ledger_id=$H_LEDGER"; echo "preview_name=$H_PNAME"; echo "published_sha=$RUN_SHA";;

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
    "$TOOLS_DIR/gh.sh" mark-env-deployment "$APP_NAME/$TARGET" "$RUN_SHA" failure "Smoke failed — UNVERIFIED (served $RUN_SHA)" || echo "::warning::could not annotate the Environment deployment as unverified" >&2;;

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
    # parse-env writes no file for an empty build env: that is a valid (empty) environment, not an error.
    export CFV2_REBIND_ENV; CFV2_REBIND_ENV="$(rd build.env)"
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
    [ "$POLICY" = automatic ] || blocked policy "production_rollback_policy is '$POLICY'."
    [ -n "$PREV" ] || blocked no_previous_version "No previous Worker version id was recorded (first deployment, or the deployment list was unreadable); there is nothing safe to roll back to."
    [ -n "$NEW" ] && [ -n "$W" ] || blocked no_identity "The deployed version id or Worker name was not handed over."
    [ "$PREV" != "$NEW" ] || blocked same_version "The previous and the new version id are identical."
    if [ "${D1_PENDING:-}" = true ]; then
      # A Worker rollback never reverts D1. This run changed the schema: the previous version must be PROVEN to cope.
      [ "$(inp d1_rollback_compat)" = expand-contract ] || blocked d1_unattested "This run applied D1 migrations and d1_rollback_compat is not 'expand-contract': compatibility of the previous Worker version with the new schema is unproven."
      [ "${D1_SCAN:-}" = additive ] || blocked d1_not_additive "This run applied D1 migrations that are not provably additive (scan: ${D1_SCAN:-unknown}); the previous Worker version may not work on the new schema."
    fi
    RB="$S/rollback"; mkdir -p "$RB"; node -e 'const [n,f]=process.argv.slice(1);require("fs").writeFileSync(f,JSON.stringify({name:n,compatibility_date:"2025-01-01"}))' "$W" "$RB/wrangler.json"
    LIVE="$(cd "$APP_DIR" && pnpm exec wrangler deployments list --config "$RB/wrangler.json" --json 2>/dev/null \
      | jq -r 'sort_by(.created_on) | reverse | .[0].versions | max_by(.percentage // 0) | .version_id // empty' 2>/dev/null || echo "")"
    # Never roll back a deployment that is no longer ours (someone deployed out of band): fail closed.
    [ "$LIVE" = "$NEW" ] || blocked live_version_changed "The live version is '${LIVE:-unknown}', not the version \`$NEW\` this run deployed; refusing to replace someone else's deployment."
    if (cd "$APP_DIR" && pnpm exec wrangler versions deploy "$PREV@100" --config "$RB/wrangler.json" --message "Revert: smoke failed on $RUN_SHA" --yes) >&2; then
      echo "rollback_result=rolled_back"; echo "rollback_reason=rolled back to $PREV"
    else
      echo "rollback_result=failed"; echo "rollback_reason=wrangler versions deploy failed"
      summary "### 🚨 Automatic rollback to \`$PREV\` FAILED — Production may serve the unverified version \`$NEW\`. Roll back manually: \`$MANUAL\`."
    fi;;

  prod-rollback-verify) # smoke the RESTORED deployment: previous commit when resolvable, else "no longer serves the failed commit"
    ctx
    RB="$S/rollback"; PREV_SHA=""
    VIEW="$(cd "$APP_DIR" && pnpm exec wrangler versions view "${PREVIOUS_VERSION_ID:?}" --config "$RB/wrangler.json" --json 2>/dev/null || true)"
    PREV_SHA="$(printf '%s' "$VIEW" | jq -r '.annotations["workers/message"] // .message // empty' 2>/dev/null | grep -oE '^\[[0-9a-f]{7}\]' | tr -d '[]' | head -1 || true)"
    [ -z "$PREV_SHA" ] || PREV_SHA="$(cd "$APP_DIR" && git rev-parse --verify --quiet "$PREV_SHA^{commit}" 2>/dev/null || echo "$PREV_SHA")"
    echo "previous_commit=$PREV_SHA"
    TARGET_URL="$(target_url)" DEPLOY_TARGET=prod TEST_ENV=prod EXPECTED_SHA="$PREV_SHA" ABSENT_SHA="$RUN_SHA" SMOKE_CMD="$(target_smoke_cmd)" SMOKE_CWD="$APP_DIR" SHA_PATH="$(inp sha_path)" \
      PROBE_PATH="$(inp probe_path)" PROBE_EXPECT_STATUS="$(inp probe_expect_status)" ACCESS_TARGETS="$(inp access_protected_targets)" \
      SMOKE_ENV_FILE="$S/smoke.env" PREVIEW_ENV_FILE="$S/preview.env" NO_PROXY='*' "$TOOLS_DIR/smoke.sh";;

  rollback-finish) # final, target-independent verdict of the Rollback job (ok=true only when restored AND content-verified)
    ctx
    R=""; LEVEL=""
    if [ "$TARGET" = prod ]; then
      case "${RB_RESULT:-}" in
        rolled_back)
          if [ "${RB_VERIFY:-}" = success ]; then
            case "${RB_LEVEL:-}" in
              content|content-sha) R=restored_verified; LEVEL="$RB_LEVEL";;
              *) R=restored_unverified; LEVEL="${RB_LEVEL:-}";;
            esac
          else R=restored_unhealthy; fi;;
        blocked) R=blocked;;
        *) R=failed;;
      esac
      case "$R" in
        restored_verified)
          summary "### ♻️ Production rolled back to \`${PREVIOUS_VERSION_ID:-?}\` and verified ($LEVEL). The run stays **FAILED** for $RUN_SHA and Release is blocked. D1 migrations (if any) were NOT reverted."
          "$TOOLS_DIR/gh.sh" mark-env-deployment "$APP_NAME/$TARGET" "$RUN_SHA" inactive "Rolled back to ${PREVIOUS_VERSION_ID:-previous} after a failed smoke" || true;;
        restored_unverified) summary "### ⚠️ Production rolled back to \`${PREVIOUS_VERSION_ID:-?}\` but only liveness could be observed (${LEVEL:-no level}): the restored deployment is NOT content-verified. Run FAILED.";;
        restored_unhealthy) summary "### 🚨 Production was rolled back to \`${PREVIOUS_VERSION_ID:-?}\` but the post-rollback smoke FAILED — Production may be unhealthy. Investigate now. Run FAILED.";;
      esac
    else
      if [ -z "${GOOD_SHA:-}" ]; then R=no_known_good
      elif [ "${RECOVERED:-}" = success ] && [ "${VERIFIED:-}" = success ]; then
        case "${SMOKE_LEVEL:-}" in content|content-sha) R=restored_verified; LEVEL="$SMOKE_LEVEL";; *) R=restored_unverified; LEVEL="${SMOKE_LEVEL:-}";; esac
      else R=failed; fi
    fi
    echo "result=$R"; echo "level=$LEVEL"; echo "ok=$([ "$R" = restored_verified ] && echo true || echo false)";;

  cleanup)
    rm -rf "${STATE_DIR:-${RUNNER_TEMP:-/tmp}/cfv2}" 2>/dev/null || true;;

  *) die "unknown engine phase: $PHASE";;
esac
