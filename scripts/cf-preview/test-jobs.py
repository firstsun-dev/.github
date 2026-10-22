#!/usr/bin/env python3
"""Executes the REAL steps of the Migration and Deploy jobs (stub gh/pnpm/curl) and feeds the results into the
next job's `if:`. Covers result propagation the static contract test cannot: stale runs never reach Release,
smoke/rollback keep Release closed, nothing remote is touched before the pre-migration guards pass.
Mocked: no Cloudflare, no GitHub. See docs/cf-worker-v2-refactor.md for what this does NOT prove."""
import json
import sys

import yaml

sys.path.insert(0, "scripts/cf-preview")
from ghexpr import Ctx, ev  # noqa: E402
from jobrunner import APP_TOML, make_workspace, run_job  # noqa: E402

WF = ".github/workflows/_cf-worker-v2.yml"
doc = yaml.safe_load(open(WF))
INPUT_DEFAULTS = {k: v.get("default", "") for k, v in doc[True]["workflow_call"]["inputs"].items()}
SHA, OTHER = "abcdef1234567890", "ffffffffffffffff"
GOOD = "1111111222222233"
MAIN, FEAT = "refs/heads/main", "refs/heads/feat/x"
fail = 0


def check(ok, msg):
    global fail
    print(("PASS: " if ok else "FAIL: ") + msg)
    if not ok:
        fail = 1


def ctx(ref, inputs=None, secrets=None, needs=None, event="push"):
    # preview_host_check off: the job tests must not call the real Cloudflare API (test-deploy.sh covers it).
    i = dict(INPUT_DEFAULTS, app_name="app", build_cmd="true", app_url_prod="https://app.example.com", tools_ref="x", preview_host_check=False)
    i.update(inputs or {})
    return Ctx({"github": {"event_name": event, "ref": ref, "ref_type": "branch", "ref_name": ref.split("/", 2)[-1],
                           "repository": "o/r", "sha": SHA, "event": {"head_commit": {"message": "feat: x\nbody"}}},
                "inputs": i, "needs": needs or {}}, secrets or {})


PROD_SECRETS = {"CLOUDFLARE_API_TOKEN": "PT", "CLOUDFLARE_ACCOUNT_ID": "PA"}
PREV_SECRETS = {"CLOUDFLARE_PREVIEW_API_TOKEN": "VT", "CLOUDFLARE_PREVIEW_ACCOUNT_ID": "VA"}


def deploy(ref=MAIN, inputs=None, secrets=None, env=None, toml=APP_TOML, needs=None):
    ws = make_workspace(toml)
    r = run_job(doc, "deploy", ctx(ref, inputs, secrets if secrets is not None else (PROD_SECRETS if ref == MAIN else PREV_SECRETS), needs),
                ws, dict({"STUB_TIPS": SHA}, **(env or {})))
    r["ws"] = ws
    return r


def migrate(ref=MAIN, inputs=None, secrets=None, env=None, toml=APP_TOML):
    ws = make_workspace(toml)
    r = run_job(doc, "migrate", ctx(ref, dict({"d1_migrations": True}, **(inputs or {})), secrets if secrets is not None else (PROD_SECRETS if ref == MAIN else PREV_SECRETS)),
                ws, dict({"STUB_TIPS": SHA}, **(env or {})))
    r["ws"] = ws
    return r


def release_runs(r, inputs=None):
    c = ctx(MAIN, dict({"release_cmd": "pnpm release"}, **(inputs or {})), needs={"deploy": {"result": r["result"], "outputs": r["outputs"]}})
    return bool(ev(doc["jobs"]["release"]["if"], c))


def deploy_runs(m, e2e="success", inputs=None, ref=FEAT, **others):
    needs = {j: {"result": others.get(j, "success")} for j in ("lint", "typecheck", "unit", "integration", "build")}
    needs.update({"e2e": {"result": e2e}, "migrate": {"result": m["result"]}})
    c = ctx(ref, dict({"d1_migrations": True, "lint_cmd": "l", "typecheck_cmd": "t", "test_unit_cmd": "u", "test_integration_cmd": "i", "test_e2e_cmd": "e"}, **(inputs or {})), needs=needs)
    return bool(ev(doc["jobs"]["deploy"]["if"], c))


def published(r):
    return "versions upload" in r["log"] or "preview --name" in r["log"]


# ======================================================================= Deploy: Production + Release gate
r = deploy()
check(r["result"] == "success" and r["outputs"]["deployed_and_verified"] == "true" and "versions deploy new-2@100" in r["log"], "prod success: published own SHA, smoke passed -> deployed_and_verified=true")
check(r["outputs"]["target"] == "prod" and r["outputs"]["version_id"] == "new-2", "prod success: outputs target + version")
check(release_runs(r), "prod success -> Release runs")

r = deploy(env={"STUB_TIPS": OTHER})
check(r["result"] == "success", "stale main run: the job itself succeeds (nothing to do)")
check(r["outputs"]["deployed_and_verified"] == "false" and not published(r), "stale main run: nothing published, deployed_and_verified=false")
check(not release_runs(r), "REGRESSION stale main run -> Release does NOT run although Deploy succeeded")

r = deploy(env={"STUB_TIPS": f"{SHA},{OTHER}"})
check(r["result"] == "success" and not published(r) and r["outputs"]["deployed_and_verified"] == "false", "superseded between start and publish: publish skipped, not verified")
check(not release_runs(r), "skipped publish -> Release does NOT run")

r = deploy(env={"STUB_TIPS": "GONE"})
check(r["result"] == "failure" and not published(r) and r["outputs"]["deployed_and_verified"] == "false", "branch tip unresolvable (deleted / API error): fails closed, nothing published")

r = deploy(env={"STUB_PROBES": "500 "})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false" and "versions deploy new-2@100" in r["log"], "prod smoke failure (policy manual): job red, not verified")
check("versions deploy prev-1@100" not in r["log"], "policy manual: no automatic rollback")
check(not release_runs(r), "prod smoke failure -> Release does NOT run")

r = deploy(inputs={"production_rollback_policy": "automatic"}, env={"STUB_PROBES": "500 "})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false", "prod smoke failure + rollback: job stays red, not verified")
rb = [l for l in r["log"].splitlines() if "versions deploy prev-1@100" in l]
up = [l for l in r["log"].splitlines() if "versions upload" in l]
check(len(rb) == 1 and "--config wrangler.toml" in rb[0] and "--config wrangler.toml" in up[0], "rollback uses the same Wrangler config as the publish")
check("NOT reverted" in r["summary"], "rollback summary states D1 was not reverted")
check(not release_runs(r), "prod rollback -> Release does NOT run")

r = deploy(inputs={"app_url_prod": ""})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false" and ("Smoke — exact deployed URL (fails closed)", "failure") in r["steps"], "prod without a smoke URL FAILS at the smoke step (no silent success)")
check(not release_runs(r), "prod without smoke URL -> Release does NOT run")

r = deploy(env={"STUB_PROBES": "302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x"})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false", "prod redirect to Cloudflare Access without approval FAILS (not application verification)")
r = deploy(inputs={"access_protected_targets": "preview prod"}, env={"STUB_PROBES": "302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x"})
check(r["result"] == "success" and r["outputs"]["smoke_level"] == "liveness" and r["outputs"]["deployed_and_verified"] == "true", "explicitly approved Access-protected prod: verified at LIVENESS level only")
for code in ("401 ", "403 ", "404 ", "500 ", "302 https://evil.example/x", "000 "):
    r = deploy(env={"STUB_PROBES": code})
    check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false", f"prod probe '{code.strip()}' fails and is not verified")
r = deploy(inputs={"sha_path": "/version.json"}, env={"STUB_SHA_BODY": "ffffff"})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false", "SHA endpoint serving a different commit fails")
r = deploy(inputs={"sha_path": "/version.json"}, env={"STUB_SHA_FAIL": "1"})
check(r["result"] == "failure", "configured SHA endpoint missing (404) fails instead of being ignored")
r = deploy(inputs={"sha_path": "/version.json"}, env={"STUB_SHA_BODY": '{"sha":"abcdef1"}'})
check(r["result"] == "success" and r["outputs"]["deployed_and_verified"] == "true", "SHA endpoint serving this commit passes")

r = deploy(inputs={"build_cmd": "false"})
check(r["result"] == "failure" and not published(r) and r["outputs"]["deployed_and_verified"] == "false", "failed build never publishes")
r = deploy(secrets={})
check(r["result"] == "failure" and not published(r), "missing Production credentials fail closed (no fallback)")
r = deploy(secrets={"CLOUDFLARE_PREVIEW_API_TOKEN": "VT", "CLOUDFLARE_PREVIEW_ACCOUNT_ID": "VA"})
check(r["result"] == "failure" and not published(r), "main with ONLY Preview credentials fails (never uses them for Production)")

# ======================================================================= Deploy: Preview
r = deploy(FEAT)
check(r["result"] == "success" and r["outputs"]["deployed_and_verified"] == "true" and "preview --name feat-x" in r["log"] and "--json" in r["log"], "preview success: stable branch-slug Native Preview published and verified")
check(r["outputs"]["url"] == "https://stable.example.workers.dev" and r["outputs"]["target"] == "preview", "preview outputs the real URL")
check("deployments -X POST" in r["log"] or "-X POST" in r["log"], "preview records a ledger deployment")
r2 = deploy(FEAT, env={"STUB_OWNER_REF": "refs/heads/Feat/X"})
check(r2["result"] == "failure" and not published(r2), "branch-name collision (ledger owned by another ref): fails before publish")
r2 = deploy(FEAT, env={"STUB_OWNER_REF": FEAT})
check(r2["result"] == "success", "ledger owned by THIS branch ref: same branch updates its Preview")
r = deploy(FEAT, env={"STUB_PROBES": "302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x"})
check(r["result"] == "success" and r["outputs"]["smoke_level"] == "liveness", "Access-protected Preview (default approval): liveness reported, not content")
def good_dep(**over):
    p = {"repo": "o/r", "branch_ref": FEAT, "host_worker": "app", "worker": "app"}
    p.update(over.pop("payload", {}))
    return json.dumps([dict({"id": 7, "sha": GOOD, "payload": p}, **over)])


PREV_IN = {"build_cmd": 'echo "BUILD noindex=$SITE_NOINDEX commit=$PUBLIC_COMMIT_SHA ver=$VITE_APP_VERSION gaid=$GA_ID" >> "$STUB_LOG"',
           "app_version": SHA, "build_env_preview": f"SITE_NOINDEX=true\nGA_ID=test-ga\nPUBLIC_COMMIT_SHA={SHA}",
           "smoke_env": "SITE_NOINDEX=false\nGA_ID=PROD-GA"}  # the Blog incident: Production-oriented smoke_env
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ;200 ", "STUB_DEPLOYMENTS": good_dep()})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false", "preview smoke failure: job red, not verified")
check(r["log"].count("preview --name feat-x") == 2, "preview recovery redeploys the last good deployment")
bl = [l for l in r["log"].splitlines() if l.startswith("BUILD")]
check(len(bl) == 2 and bl[0] == f"BUILD noindex=true commit={SHA} ver={SHA} gaid=test-ga", "the normal Preview build uses the Preview build env (smoke_env cannot leak into it)")
check(len(bl) == 2 and bl[1] == f"BUILD noindex=true commit={GOOD} ver={GOOD} gaid=test-ga", "recovery rebuild restores the Preview build env and rebinds commit/version to the LAST-GOOD sha (not the smoke_env values)")
check("restored to last good commit" in r["summary"] and "content-level" in r["summary"] and "NOT reverted" in r["summary"], "recovery verified at content level is reported as such, D1 not reverted")
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ", "STUB_DEPLOYMENTS": good_dep(payload={"host_worker": "blog-preview"})})
check(r["result"] == "failure" and r["log"].count("preview --name feat-x") == 1 and "No prior verified deployment on host Worker" in r["summary"], "a last-good deployment hosted by a DIFFERENT Worker is not eligible for recovery")
for label, over in (("legacy record without host_worker", {"payload": {"host_worker": None}}), ("different branch", {"payload": {"branch_ref": "refs/heads/other"}}),
                    ("different application", {"payload": {"worker": "other-app"}}), ("different repository", {"payload": {"repo": "x/y"}})):
    dep = json.loads(good_dep(**over))
    if label.startswith("legacy"):
        del dep[0]["payload"]["host_worker"]
    r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ", "STUB_DEPLOYMENTS": json.dumps(dep)})
    check(r["log"].count("preview --name feat-x") == 1 and "No prior verified deployment" in r["summary"], f"recovery ineligible: {label}")
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ", "STUB_DEPLOYMENTS": good_dep(), "STUB_STATUS": "failure https://stable.example.workers.dev"})
check(r["log"].count("preview --name feat-x") == 1, "recovery ineligible: the candidate's latest status is not success")
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ", "STUB_DEPLOYMENTS": good_dep(), "STUB_STATUS": "success "})
check(r["log"].count("preview --name feat-x") == 1, "recovery ineligible: no valid recorded Preview URL")
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ;200 ", "STUB_DEPLOYMENTS": good_dep(), "STUB_STATUS": "success https://other.example.workers.dev"})
check(r["result"] == "failure" and "RECOVERY" not in r["summary"] and "recovery FAILED" in r["summary"].replace("Automatic recovery FAILED", "recovery FAILED"), "recovery whose URL differs from the recorded last-good URL is refused and reported as failed")
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ;500 ", "STUB_DEPLOYMENTS": good_dep()})
check(r["result"] == "failure" and "Automatic recovery FAILED" in r["summary"] and "restored to last good" not in r["summary"], "recovery smoke failure stays visible and is never reported as recovered")
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ;302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x", "STUB_DEPLOYMENTS": good_dep()})
check(r["result"] == "failure" and "NOT verified" in r["summary"] and "restored to last good" not in r["summary"], "Access liveness after recovery is NOT reported as verified recovery")
r = deploy(FEAT, inputs=dict(PREV_IN, sha_path="/version.json"), env={"STUB_PROBES": "500 ;200 ", "STUB_DEPLOYMENTS": good_dep(), "STUB_SHA_BODY": SHA[:7]})
check(r["result"] == "failure" and "restored to last good" not in r["summary"] and "Automatic recovery FAILED" in r["summary"], "recovery serving the FAILED commit's identity (sha_path) is not accepted")
r = deploy(FEAT, inputs=dict(PREV_IN, sha_path="/version.json"), env={"STUB_PROBES": "500 ;200 ", "STUB_DEPLOYMENTS": good_dep(), "STUB_SHA_BODY": GOOD[:7]})
check("restored to last good commit" in r["summary"], "recovery serving the last-good commit identity is verified")
# the empty-URL incident, executed through the real Deploy steps
for raw, label in (('{"preview":{"urls":[]}}', "empty preview.urls"), ('{"preview":{}}', "missing urls"), ("garbage", "malformed JSON"), ("", "no output")):
    r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PREVIEW_RAW": raw, "STUB_DEPLOYMENTS": good_dep()})
    st = dict(r["steps"])
    check(r["result"] == "failure" and st.get("Build, validate effective Wrangler config, publish") == "failure", f"{label}: Publish fails")
    check(st.get("Smoke — exact deployed URL (fails closed)") == "skipped" and r["outputs"]["deployed_and_verified"] == "false" and r["outputs"]["url"] == "", f"{label}: Smoke never runs against an empty URL and nothing is reported as deployed")
    check(r["log"].count("preview --name feat-x") == 1 and "no usable URL" in r["summary"] and "unverified" in r["summary"], f"{label}: no recovery is attempted and the summary says the Preview state is unverified")
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PREVIEW_FAIL": "1", "STUB_DEPLOYMENTS": good_dep()})
check(r["result"] == "failure" and "unchanged" in r["summary"] and r["log"].count("preview --name feat-x") == 1, "wrangler preview failure before any mutation: 'unchanged', no recovery")
r = deploy(FEAT, inputs=dict(PREV_IN, build_cmd="false"), env={"STUB_DEPLOYMENTS": good_dep()})
check(r["result"] == "failure" and "preview --name" not in r["log"] and "unchanged" in r["summary"], "build failure: nothing published, no recovery")
r = deploy(FEAT, env={"STUB_PREVIEW_FAIL": "1"})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false", "preview publish failure: not verified")
r = deploy(FEAT, secrets=dict(PREV_SECRETS, PREVIEW_SECRETS="EVIL=$(touch PWNED)`touch PWNED2`;touch PWNED3 \"q\" 'x'\nPLAIN=a b=c"),
           inputs={"build_cmd": 'printf %s "$EVIL" > evil.out && printf %s "$PLAIN" > plain.out'})
w = r["ws"] / "w"
check(r["result"] == "success" and not any((w / n).exists() for n in ("PWNED", "PWNED2", "PWNED3")), "Preview secret with $() / backticks / ; is NOT executed")
check((w / "evil.out").read_text() == "$(touch PWNED)`touch PWNED2`;touch PWNED3 \"q\" 'x'" and (w / "plain.out").read_text() == "a b=c", "Preview secret values reach the build process literally")
check(not (r["ws"] / "tmp" / "preview.env").exists(), "staged Preview secrets are removed at the end of the job")
r = deploy(FEAT, secrets=dict(PREV_SECRETS, PREVIEW_SECRETS="CLOUDFLARE_API_TOKEN=evil"))
check(r["result"] == "failure" and not published(r), "Preview secrets cannot override Cloudflare credentials")
r = deploy(FEAT, secrets={"CLOUDFLARE_API_TOKEN": "PT", "CLOUDFLARE_ACCOUNT_ID": "PA"})
check(r["result"] == "failure" and not published(r), "a feature branch with ONLY Production credentials fails (never uses them)")

# ======================================================================= Migration: pre-migration guards + propagation
def touched(r):
    return "migrations" in r["log"]


m = migrate(MAIN)
check(m["result"] == "success" and "d1 migrations apply DB --remote" in m["log"] and "PROD-ID" in open(m["ws"] / "w" / ".wrangler" / "migrate.prod.json").read(), "prod migration applies to the Production D1")
check(deploy_runs(m, ref=MAIN), "Migration success -> Deploy runs")
m = migrate(FEAT)
check(m["result"] == "success" and "PREV-ID" in open(m["ws"] / "w" / ".wrangler" / "migrate.preview.json").read() and "PROD-ID" not in open(m["ws"] / "w" / ".wrangler" / "migrate.preview.json").read(), "preview migration applies to previews.d1_databases only")
m = migrate(FEAT, env={"STUB_PENDING": "0"})
check(m["result"] == "success" and "migrations apply" not in m["log"] and m["outputs"]["pending"] == "false", "nothing pending: success without applying")

m = migrate(FEAT, env={"STUB_OWNER_REF": "refs/heads/Feat/X"})
check(m["result"] == "failure" and not touched(m), "name collision: second branch fails BEFORE any remote migration")
check(not deploy_runs(m), "collision -> Deploy blocked")
m = migrate(FEAT, env={"STUB_TIPS": OTHER})
check(m["result"] == "success" and m["outputs"]["superseded"] == "true" and not touched(m), "stale branch: no migration applied")
m = migrate(FEAT, env={"STUB_TIPS": "GONE"})
check(m["result"] == "failure" and not touched(m), "deleted branch: fails closed, no migration")
bad = APP_TOML.replace('database_id = "PREV-ID"', 'database_id = "PROD-ID"').replace('database_name = "prev-db"', 'database_name = "prod-db"')
m = migrate(FEAT, toml=bad)
check(m["result"] == "failure" and not touched(m), "Preview D1 identical to Production D1: fails before any migration (preflight / target check)")
m = migrate(MAIN, toml=bad)
check(m["result"] == "failure" and not touched(m), "same for the Production target")
amb = APP_TOML + '[[previews.d1_databases]]\nbinding = "DB2"\ndatabase_name = "p2"\ndatabase_id = "P2"\n'
amb = amb.replace('[[d1_databases]]\nbinding = "DB"', '[[d1_databases]]\nbinding = "DB"') + '[[d1_databases]]\nbinding = "DB2"\ndatabase_name = "x2"\ndatabase_id = "X2"\n'
m = migrate(FEAT, toml=amb)
check(m["result"] == "failure" and not touched(m), "ambiguous D1 bindings without d1_binding: fails closed")
m = migrate(FEAT, toml=amb, inputs={"d1_binding": "DB2"})
check(m["result"] == "success" and "P2" in open(m["ws"] / "w" / ".wrangler" / "migrate.preview.json").read(), "d1_binding selects the intended database")
m = migrate(FEAT, toml='name = "app"\n[previews]\n')
check(m["result"] == "failure" and not touched(m), "no D1 declared for the target while d1_migrations is on: fails closed")
m = migrate(FEAT, secrets={})
check(m["result"] == "failure" and not touched(m), "missing credentials: fails before any remote command")
m = migrate(FEAT, secrets={"CLOUDFLARE_API_TOKEN": "PT", "CLOUDFLARE_ACCOUNT_ID": "PA"})
check(m["result"] == "failure" and not touched(m), "branch with ONLY Production credentials fails")
m = migrate(FEAT, env={"STUB_APPLY_FAIL": "1"})
check(m["result"] == "failure", "failed migration -> job fails")
check(not deploy_runs(m) and not deploy_runs(m, ref=MAIN), "failed migration BLOCKS Deploy (preview and prod)")
m = migrate(MAIN, env={"STUB_APPLY_FAIL": "1"})
check(not deploy_runs(m, ref=MAIN), "failed Production migration BLOCKS Production Deploy")
check(not deploy_runs({"result": "success"}, e2e="skipped", inputs={"test_e2e_cmd": "pnpm e2e"}), "a configured E2E that never ran blocks Deploy")
check(deploy_runs({"result": "success"}, e2e="skipped", inputs={"test_e2e_cmd": ""}), "intentionally disabled E2E does not block Deploy")

# ======================================================================= Shared Dev (dev-deploy) executed with stubs
DEV_SECRETS = {"CLOUDFLARE_DEV_API_TOKEN": "DT", "CLOUDFLARE_DEV_ACCOUNT_ID": "DA"}
DEV_TOML = '''name = "app"
[[d1_databases]]
binding = "DB"
database_name = "prod-db"
database_id = "PROD-ID"
[[r2_buckets]]
binding = "ASSETS"
bucket_name = "prod-assets"
[previews]
[[previews.d1_databases]]
binding = "DB"
database_name = "shared-db"
database_id = "SHARED-ID"
[env.dev]
name = "app-dev"
[[env.dev.d1_databases]]
binding = "DB"
database_name = "shared-db"
database_id = "SHARED-ID"
[[env.dev.r2_buckets]]
binding = "ASSETS"
bucket_name = "prod-assets"
'''
DEV_INPUTS = {
    "shared_dev_on_branch_push": True, "app_url_dev": "https://dev.example.com", "dev_worker_name": "app-dev",
    "app_version": SHA, "build_env_dev": f"PUBLIC_COMMIT_SHA={SHA}\nSITE=dev", "dev_wrangler_env": "dev",
    "dev_build_cmd": 'echo "BUILD ver=$VITE_APP_VERSION commit=$PUBLIC_COMMIT_SHA cfenv=$CLOUDFLARE_ENV" >> "$STUB_LOG"',
    "dev_deploy_cmd": 'echo "PUBLISH" >> "$STUB_LOG"',
    "dev_binding_allow": "r2_buckets:ASSETS=prod-assets",
}


def dev(inputs=None, env=None, toml=DEV_TOML, needs=None, secrets=None, ref=FEAT):
    ws = make_workspace(toml)
    n = needs if needs is not None else {"migrate": {"result": "skipped"}}
    r = run_job(doc, "dev-deploy", ctx(ref, dict(DEV_INPUTS, **(inputs or {})), DEV_SECRETS if secrets is None else secrets, n),
                ws, dict({"STUB_TIPS": SHA}, **(env or {})))
    r["ws"] = ws
    return r


def ran(r, what):
    return what in r["log"]


r = dev()
check(r["result"] == "success" and r["outputs"]["deployed_and_verified"] == "true" and r["outputs"]["url"] == "https://dev.example.com", "Shared Dev success: built, audited, published, smoked, verified at app_url_dev")
check(ran(r, "BUILD ver=" + SHA + " commit=" + SHA + " cfenv=dev") and ran(r, "PUBLISH"), "Shared Dev builds with the Dev build env and CLOUDFLARE_ENV=dev_wrangler_env")
check("state=success" in r["log"] and "d1 migrations" not in r["log"], "Shared Dev success is recorded in the ledger only after the smoke; no migration for a D1-less config")
r = dev(inputs={"d1_migrations": True})
check(r["result"] == "success" and "d1 migrations apply DB --remote" in r["log"] and "SHARED-ID" in open(r["ws"] / "w" / ".wrangler" / "migrate.dev.json").read() and "PROD-ID" not in open(r["ws"] / "w" / ".wrangler" / "migrate.dev.json").read(),
      "Shared Dev migrates the Dev D1 (env.dev), never the Production D1")
b_i, m_i, p_i = r["log"].index("BUILD"), r["log"].index("migrations apply"), r["log"].index("PUBLISH")
check(b_i < m_i < p_i, "order in the executed job: build -> migration -> publish")

r = dev(inputs={"dev_build_cmd": "false", "d1_migrations": True})
check(r["result"] == "failure" and not ran(r, "migrations apply") and not ran(r, "PUBLISH") and "unchanged" in r["summary"], "build failure: no migration, no publish, explicit 'Dev unchanged' outcome")
r = dev(toml=DEV_TOML.replace('name = "app-dev"', 'name = "app"'), inputs={"d1_migrations": True})
check(r["result"] == "failure" and ran(r, "BUILD") and not ran(r, "migrations apply") and not ran(r, "PUBLISH"), "audit failure (wrong Worker identity): no migration, no publish")
r = dev(inputs={"d1_migrations": True}, env={"STUB_APPLY_FAIL": "1"})
check(r["result"] == "failure" and ran(r, "BUILD") and not ran(r, "PUBLISH"), "migration failure prevents publish")
r = dev(inputs={"dev_binding_allow": ""})
check(r["result"] == "failure" and not ran(r, "PUBLISH") and not ran(r, "migrations apply"), "unapproved Production R2 sharing fails the audit before anything is published")
r = dev(inputs={"dev_binding_allow": "r2_buckets:ASSETS=another-bucket"})
check(r["result"] == "failure" and not ran(r, "PUBLISH"), "allowlist naming a different bucket does not approve the shared one")
r = dev(inputs={"dev_binding_allow": "r2_buckets:ASSETS=prod-assets", "dev_deny_resources": "prod-assets"})
check(r["result"] == "failure" and not ran(r, "PUBLISH"), "dev_deny_resources beats dev_binding_allow")
r = dev(inputs={"preview_deny_resources": "prod-assets"})
check(r["result"] == "success", "a Preview-only denylist does not affect the approved Dev share")
r = dev(inputs={"d1_migrations": True, "dev_build_cmd": ""})
check(r["result"] == "failure" and not ran(r, "PUBLISH") and not ran(r, "migrations apply"), "d1_migrations without dev_build_cmd fails closed")
r = dev(secrets={})
check(r["result"] == "failure" and not ran(r, "BUILD"), "missing Dev credentials fail closed before the build")
r = dev(secrets={"CLOUDFLARE_API_TOKEN": "PT", "CLOUDFLARE_ACCOUNT_ID": "PA", "CLOUDFLARE_PREVIEW_API_TOKEN": "VT"})
check(r["result"] == "failure" and not ran(r, "BUILD"), "Production/Preview credentials are never a fallback for Dev")
r = dev(env={"STUB_TIPS": OTHER})
check(r["result"] == "success" and not ran(r, "BUILD") and not ran(r, "PUBLISH") and r["outputs"]["deployed_and_verified"] == "false", "stale run: nothing built, migrated or published")
r = dev(env={"STUB_TIPS": f"{SHA},{OTHER}"}, inputs={"d1_migrations": True})
check(r["result"] == "success" and ran(r, "BUILD") and not ran(r, "migrations apply") and not ran(r, "PUBLISH"), "superseded after the build: no migration and no publish")

# Shared-D1 guard
r = dev(inputs={"d1_migrations": True}, needs={"migrate": {"result": "failure"}})
check(r["result"] == "failure" and not ran(r, "BUILD") and not ran(r, "migrations apply") and not ran(r, "PUBLISH"), "Preview Migration failed on the SHARED D1: Dev builds, migrates and publishes nothing")
r = dev(inputs={"d1_migrations": True}, needs={"migrate": {"result": "cancelled"}})
check(r["result"] == "failure" and not ran(r, "PUBLISH"), "...also when the Preview Migration was cancelled")
distinct = DEV_TOML.replace('database_name = "shared-db"\ndatabase_id = "SHARED-ID"\n[env.dev]', 'database_name = "preview-only-db"\ndatabase_id = "PREVIEW-ONLY"\n[env.dev]')
assert distinct != DEV_TOML
r = dev(inputs={"d1_migrations": True}, needs={"migrate": {"result": "failure"}}, toml=distinct)
check(r["result"] == "success" and ran(r, "PUBLISH"), "Preview Migration failed but the D1s differ: Shared Dev is independent and deploys")
r = dev(inputs={"d1_migrations": True}, needs={"migrate": {"result": "success"}})
check(r["result"] == "success", "Preview Migration succeeded on the shared D1: Dev proceeds")

# Smoke failure and recovery
r = dev(env={"STUB_PROBES": "500 ;200 ", "STUB_LASTGOOD": GOOD})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false" and "unverified" in r["summary"], "smoke failure: job red, deployment marked unverified")
builds = [l for l in r["log"].splitlines() if l.startswith("BUILD")]
check(len(builds) == 2 and builds[1] == f"BUILD ver={GOOD} commit={GOOD} cfenv=dev", "recovery builds the LAST-GOOD commit with its own version and rebound build env (not the failed SHA)")
check(SHA not in builds[1] and r["log"].count("PUBLISH") == 2 and "fully" not in r["summary"] and "Run remains FAILED" in r["summary"] and "identity NOT verified" in r["summary"], "recovery redeploys; without sha_path it says commit identity was NOT verified; run stays red")
r = dev(inputs={"sha_path": "/version.json"}, env={"STUB_PROBES": "500 ;200 ", "STUB_LASTGOOD": GOOD, "STUB_SHA_BODY": GOOD[:7]})
check(r["result"] == "failure" and "commit identity verified at /version.json" in r["summary"], "with sha_path the recovery verifies the restored commit identity")
r = dev(inputs={"sha_path": "/version.json"}, env={"STUB_PROBES": "500 ;200 ", "STUB_LASTGOOD": GOOD, "STUB_SHA_BODY": SHA[:7]})
check(r["result"] == "failure" and "commit identity verified" not in r["summary"], "recovery serving the wrong commit is NOT reported as verified")
r = dev(env={"STUB_PROBES": "500 ;500 ", "STUB_LASTGOOD": GOOD})
check(r["result"] == "failure" and "recovery FAILED" in r["summary"], "recovery failure stays visible")
r = dev(env={"STUB_PROBES": "500 "})
check(r["result"] == "failure" and r["log"].count("PUBLISH") == 1 and "manual recovery required" in r["summary"], "no known-good commit: no recovery attempted, manual recovery required")
# Dev recovery regression coverage (executed)
r = dev(env={"STUB_PROBES": "500 ;200 ", "STUB_LASTGOOD": GOOD})
check("versions upload" not in r["log"] and "versions deploy" not in r["log"] and "wrangler preview" not in r["log"] and "preview --name" not in r["log"], "Dev recovery never takes the Production or Preview publish path")
check("state=success" not in r["log"], "a failed Dev deployment whose recovery succeeded is still never recorded as verified")
r = dev(env={"STUB_PROBES": "500 ;500 ", "STUB_LASTGOOD": GOOD})
check("state=success" not in r["log"] and "recovery FAILED" in r["summary"], "no verified deployment is recorded after a failed recovery")
tamper = 'case "$PWD" in */recover*) sed -i "s/name = \\"app-dev\\"/name = \\"app\\"/" wrangler.toml;; esac; echo "BUILD ver=$VITE_APP_VERSION commit=$PUBLIC_COMMIT_SHA cfenv=$CLOUDFLARE_ENV" >> "$STUB_LOG"'
r = dev(inputs={"dev_build_cmd": tamper}, env={"STUB_PROBES": "500 ;200 ", "STUB_LASTGOOD": GOOD})
check(r["result"] == "failure" and r["log"].count("PUBLISH") == 1 and "recovery FAILED" in r["summary"], "the config audit runs on the RECOVERED build: a last-good tree with the wrong Worker identity is not published")
rb = dev(env={"STUB_PROBES": "500 ;200 ", "STUB_LASTGOOD": GOOD}, inputs={"build_env_dev": f"PUBLIC_COMMIT_SHA={SHA}\nSHORT={SHA[:7]}"})
check(f"commit={GOOD}" in rb["log"], "build env referencing the failed SHA (full) is rebound for the restored commit")

# Manual persistent-dev: legacy vs split contract
PD = dict(DEV_INPUTS, deployment_strategy="hybrid", persistent_dev_ref="refs/heads/dev", dispatch_target="persistent-dev", shared_dev_on_branch_push=False)


def manual(inputs=None, env=None, toml=DEV_TOML):
    ws = make_workspace(toml)
    r = run_job(doc, "persistent-dev", ctx("refs/heads/dev", dict(PD, **(inputs or {})), DEV_SECRETS, event="workflow_dispatch"), ws, dict({"STUB_TIPS": SHA}, **(env or {})))
    r["ws"] = ws
    return r


r = manual(inputs={"dev_build_cmd": "", "dev_smoke_cmd": 'echo SMOKE >> "$STUB_LOG"'})
check(r["result"] == "success" and ran(r, "PUBLISH") and ran(r, "SMOKE") and not ran(r, "BUILD") and "-X POST" not in r["log"] and "statuses" not in r["log"], "legacy persistent-dev: unchanged (deploy + optional smoke), never written to the known-good ledger")
r = manual()
check(r["result"] == "success" and r["log"].index("BUILD") < r["log"].index("PUBLISH") and "state=success" in r["log"], "split contract: manual run builds first, publishes, smokes and is recorded verified")
r = manual(inputs={"dev_build_cmd": "false"})
check(r["result"] == "failure" and not ran(r, "PUBLISH") and "state=success" not in r["log"], "split contract: manual build failure publishes nothing and records nothing as good")
r = manual(env={"STUB_PROBES": "500 "})
check(r["result"] == "failure" and ran(r, "PUBLISH") and "state=success" not in r["log"] and "state=failure" in r["log"], "split contract: a manual deploy whose smoke fails is NEVER recorded as known-good")
r = manual(inputs={"dev_worker_name": ""})
check(r["result"] == "failure" and not ran(r, "PUBLISH"), "split contract: manual run also requires the Worker identity")
r = manual(toml=DEV_TOML.replace('name = "app-dev"', 'name = "app"'))
check(r["result"] == "failure" and not ran(r, "PUBLISH"), "split contract: manual run applies the same config audit")

sys.exit(fail)
