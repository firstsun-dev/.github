#!/usr/bin/env python3
"""Executes the REAL steps of the Deploy, Shared Dev and persistent-Dev jobs — including the composite delivery
engine (actions/cf-deploy -> actions/cf-preview/engine.sh) — against stub gh/pnpm/curl, and feeds the results into
the next job's `if:`. Covers what the static contract test cannot: Migration is a step in the Deploy job and runs only
after build + audit, stale runs never reach Release, a failed migration or smoke never publishes/verifies,
application env cannot redirect the target, nothing remote is touched before the guards pass.
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
D1 = {"d1_migrations": True}


def deploy(ref=MAIN, inputs=None, secrets=None, env=None, toml=APP_TOML, needs=None):
    ws = make_workspace(toml)
    r = run_job(doc, "deploy", ctx(ref, inputs, secrets if secrets is not None else (PROD_SECRETS if ref == MAIN else PREV_SECRETS), needs),
                ws, dict({"STUB_TIPS": SHA}, **(env or {})))
    r["ws"] = ws
    return r


def release_runs(r, inputs=None):
    c = ctx(MAIN, dict({"release_cmd": "pnpm release"}, **(inputs or {})), needs={"deploy": {"result": r["result"], "outputs": r["outputs"]}})
    return bool(ev(doc["jobs"]["release"]["if"], c))


def published(r):
    return "versions upload" in r["log"] or "preview --name" in r["log"] or "PUBLISH" in r["log"] or "wrangler deploy" in r["log"]


def step(r, name):
    return dict(r["steps"]).get(f"engine / {name}")


def migrated(r):
    return "migrations apply" in r["log"]


def mig_file(r, target):
    return open(r["ws"] / "w" / ".wrangler" / f"migrate.{target}.json").read()


# ======================================================================= Deploy: Production + Release gate
r = deploy()
check(r["result"] == "success" and r["outputs"]["deployed_and_verified"] == "true" and "versions deploy new-2@100" in r["log"], "prod success: published own SHA, smoke passed -> deployed_and_verified=true")
check(r["outputs"]["target"] == "prod" and r["outputs"]["version_id"] == "new-2" and r["outputs"]["url"] == "https://app.example.com", "prod success: outputs target + version + url")
check(release_runs(r), "prod success -> Release runs")

r = deploy(env={"STUB_TIPS": OTHER})
check(r["result"] == "success" and r["outputs"]["deployed_and_verified"] == "false" and not published(r), "stale main run: the job succeeds, nothing built or published, not verified")
check("npm" not in r["log"] and "pnpm" not in r["log"], "stale main run: stopped before any dependency install or build")
check(not release_runs(r), "REGRESSION stale main run -> Release does NOT run although Deploy succeeded")

r = deploy(env={"STUB_TIPS": f"{SHA},{OTHER}"})
check(r["result"] == "success" and not published(r) and r["outputs"]["deployed_and_verified"] == "false", "superseded after the build (before migration): publish skipped, not verified")
check(not release_runs(r), "skipped publish -> Release does NOT run")

r = deploy(env={"STUB_TIPS": "GONE"})
check(r["result"] == "failure" and not published(r) and r["outputs"]["deployed_and_verified"] == "false", "branch tip unresolvable (deleted / API error): fails closed, nothing published")

r = deploy(env={"STUB_PROBES": "500 "})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false" and "versions deploy new-2@100" in r["log"], "prod smoke failure (policy manual): job red, not verified")
check("versions deploy prev-1@100" not in r["log"] and "Production smoke FAILED" in r["summary"], "policy manual: no automatic rollback, manual instructions in the summary")
check(not release_runs(r), "prod smoke failure -> Release does NOT run")

r = deploy(inputs={"production_rollback_policy": "automatic"}, env={"STUB_PROBES": "500 "})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false", "prod smoke failure + rollback: job stays red, not verified (smoke failure stays visible although recovery succeeded)")
rb = [l for l in r["log"].splitlines() if "versions deploy prev-1@100" in l]
up = [l for l in r["log"].splitlines() if "versions upload" in l]
cfg = lambda line: line.split("--config ")[1].split()[0]
check(len(rb) == 1 and "--config" in rb[0] and cfg(rb[0]) == cfg(up[0]), "rollback uses the SAME audited Wrangler config as the publish")
check("NOT reverted" in r["summary"] and "Rolled back" in r["summary"], "rollback summary states D1 was not reverted")
check(not release_runs(r), "prod rollback -> Release does NOT run")
r = deploy(inputs={"production_rollback_policy": "automatic"}, env={"STUB_PROBES": "500 ", "STUB_ROLLBACK_FAIL": "1"})
check(r["result"] == "failure" and "rollback to `prev-1` FAILED" in r["summary"] and "Rolled back" not in r["summary"], "a FAILED automatic rollback is reported as failed, never as rolled back")

r = deploy(inputs={"app_url_prod": ""})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false" and step(r, "Smoke the exact deployed URL (fails closed)") == "failure", "prod without a smoke URL FAILS at the smoke step (no silent success)")
check(not release_runs(r), "prod without smoke URL -> Release does NOT run")

r = deploy(env={"STUB_PROBES": "302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x"})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false", "prod redirect to Cloudflare Access without approval FAILS (not application verification)")
r = deploy(inputs={"access_protected_targets": "preview prod"}, env={"STUB_PROBES": "302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x"})
ACCESS = "302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x"
check(r["result"] == "success" and r["outputs"]["smoke_level"] == "liveness" and r["outputs"]["deployed_and_verified"] == "false", "approved Access-protected prod: LIVENESS only -> published but NOT verified")
check(r["outputs"]["published_unverified"] == "true" and r["outputs"]["verified_url"] == "" and r["outputs"]["published_url"] == "https://app.example.com" and r["outputs"]["url"] == "https://app.example.com", "liveness: published_url set (and legacy url kept), verified_url empty, published_unverified=true")
check(not release_runs(r), "prod Access liveness only -> Release BLOCKED")
check("Release is **blocked**" in r["summary"] and "NOT VERIFIED" in r["summary"], "liveness-only Production keeps a diagnostic summary")
r = deploy(env={"STUB_PROBES": "200 "})
check(r["outputs"]["smoke_level"] == "content" and r["outputs"]["verified_url"] == "https://app.example.com" and r["outputs"]["published_unverified"] == "false" and release_runs(r), "prod content smoke succeeds -> verified_url set, Release allowed")
r = deploy(inputs={"access_protected_targets": "preview prod", "sha_path": "/version.json", "smoke_env": "PROBE_HEADERS=CF-Access-Client-Id: x"}, env={"STUB_PROBES": "200 ", "STUB_SHA_BODY": SHA[:7]})
check(r["outputs"]["smoke_level"] == "content-sha" and r["outputs"]["deployed_and_verified"] == "true" and release_runs(r), "prod authenticated probe on an Access-protected target + matching SHA -> content-sha, Release allowed")
r = deploy(inputs={"access_protected_targets": "preview prod"}, env={"STUB_PROBES": "500 "})
check(r["outputs"]["verified_url"] == "" and r["outputs"]["deployed_and_verified"] == "false" and r["outputs"]["published_unverified"] != "true", "failed smoke -> no verified output")
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
check(r["result"] == "failure" and not published(r) and "pnpm" not in r["log"], "missing Production credentials fail closed at init (no fallback), before any work")
r = deploy(secrets=PREV_SECRETS)
check(r["result"] == "failure" and not published(r), "main with ONLY Preview credentials fails (never uses them for Production)")

# ======================================================================= Deploy: Preview
r = deploy(FEAT)
check(r["result"] == "success" and r["outputs"]["deployed_and_verified"] == "true" and "preview --name feat-x" in r["log"] and "--json" in r["log"], "preview success: stable branch-slug Native Preview published and verified")
check(r["outputs"]["url"] == "https://stable.example.workers.dev" and r["outputs"]["target"] == "preview", "preview outputs the real URL")
check("-X POST" in r["log"] and "state=success" in r["log"], "preview records a ledger deployment and marks it success only after the smoke")
r2 = deploy(FEAT, env={"STUB_OWNER_REF": "refs/heads/Feat/X"})
check(r2["result"] == "failure" and not published(r2) and "pnpm exec wrangler preview" not in r2["log"], "branch-name collision (ledger owned by another ref): fails before the build and publish")
r2 = deploy(FEAT, env={"STUB_OWNER_REF": FEAT})
check(r2["result"] == "success", "ledger owned by THIS branch ref: same branch updates its Preview")
r = deploy(FEAT, env={"STUB_PROBES": "302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x"})
check(r["result"] == "success" and r["outputs"]["smoke_level"] == "liveness" and r["outputs"]["deployed_and_verified"] == "false" and r["outputs"]["published_unverified"] == "true", "Access-protected Preview (default approval): liveness reported, published but NOT verified")
check("state=inactive" in r["log"] and "state=success" not in r["log"], "Preview liveness is never recorded as a success (known-good) ledger status")
r = deploy(FEAT, inputs={"smoke_env": "PROBE_HEADERS=CF-Access-Client-Id: x"}, env={"STUB_PROBES": "200 "})
check(r["outputs"]["smoke_level"] == "content" and r["outputs"]["deployed_and_verified"] == "true" and r["outputs"]["verified_url"] != "" and "state=success" in r["log"], "Preview with authenticated content smoke -> verified, success ledger status")


def good_dep(**over):
    p = {"repo": "o/r", "branch_ref": FEAT, "host_worker": "app", "worker": "app"}
    p.update(over.pop("payload", {}))
    return json.dumps([dict({"id": 7, "sha": GOOD, "payload": p}, **over)])


PREV_IN = {"build_cmd": 'echo "BUILD noindex=$SITE_NOINDEX commit=$PUBLIC_COMMIT_SHA ver=$VITE_APP_VERSION gaid=$GA_ID" >> "$STUB_LOG"',
           "app_version": SHA, "build_env_preview": f"SITE_NOINDEX=true\nGA_ID=test-ga\nPUBLIC_COMMIT_SHA={SHA}",
           "smoke_env": "SITE_NOINDEX=false\nGA_ID=PROD-GA"}  # the Blog incident: Production-oriented smoke_env
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ;200 ", "STUB_DEPLOYMENTS": good_dep()})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false", "preview smoke failure: job red, not verified (the failure stays visible although recovery succeeded)")
check(r["log"].count("preview --name feat-x") == 2, "preview recovery redeploys the last good deployment")
bl = [l for l in r["log"].splitlines() if l.startswith("BUILD")]
check(len(bl) == 2 and bl[0] == f"BUILD noindex=true commit={SHA} ver={SHA} gaid=test-ga", "the normal Preview build uses the Preview build env (smoke_env cannot leak into it)")
check(len(bl) == 2 and bl[1] == f"BUILD noindex=true commit={GOOD} ver={GOOD} gaid=test-ga", "recovery rebuild restores the Preview build env and rebinds commit/version to the LAST-GOOD sha (not the smoke_env values)")
check("restored to last good commit" in r["summary"] and "content-level" in r["summary"] and "NOT reverted" in r["summary"], "recovery verified at content level is reported as such, D1 not reverted")
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ", "STUB_DEPLOYMENTS": good_dep(payload={"host_worker": "blog-preview"})})
check(r["result"] == "failure" and r["log"].count("preview --name feat-x") == 1 and "No prior verified preview deployment on host Worker" in r["summary"], "a last-good deployment hosted by a DIFFERENT Worker is not eligible for recovery")
for label, over in (("legacy record without host_worker", {"payload": {"host_worker": None}}), ("different branch", {"payload": {"branch_ref": "refs/heads/other"}}),
                    ("different application", {"payload": {"worker": "other-app"}}), ("different repository", {"payload": {"repo": "x/y"}})):
    dep = json.loads(good_dep(**over))
    if label.startswith("legacy"):
        del dep[0]["payload"]["host_worker"]
    r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ", "STUB_DEPLOYMENTS": json.dumps(dep)})
    check(r["log"].count("preview --name feat-x") == 1 and "No prior verified" in r["summary"], f"recovery ineligible: {label}")
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ", "STUB_DEPLOYMENTS": good_dep(), "STUB_STATUS": "failure https://stable.example.workers.dev"})
check(r["log"].count("preview --name feat-x") == 1, "recovery ineligible: the candidate's latest status is not success")
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ", "STUB_DEPLOYMENTS": good_dep(), "STUB_STATUS": "success "})
check(r["log"].count("preview --name feat-x") == 1, "recovery ineligible: no valid recorded Preview URL")
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ;200 ", "STUB_DEPLOYMENTS": good_dep(), "STUB_STATUS": "success https://other.example.workers.dev Serving good (content)"})
check(r["result"] == "failure" and "restored to last good" not in r["summary"] and "Automatic recovery FAILED" in r["summary"], "recovery whose URL differs from the recorded last-good URL is refused and reported as failed")
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ;500 ", "STUB_DEPLOYMENTS": good_dep()})
check(r["result"] == "failure" and "Automatic recovery FAILED" in r["summary"] and "restored to last good" not in r["summary"] and "state=error" in r["log"], "recovery smoke failure stays visible, is recorded as error and is never reported as recovered")
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ;302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x", "STUB_DEPLOYMENTS": good_dep()})
check(r["result"] == "failure" and "NOT verified" in r["summary"] and "restored to last good" not in r["summary"], "Access liveness after recovery is NOT reported as verified recovery")
r = deploy(FEAT, inputs=dict(PREV_IN, sha_path="/version.json"), env={"STUB_PROBES": "500 ;200 ", "STUB_DEPLOYMENTS": good_dep(), "STUB_SHA_BODY": SHA[:7]})
check(r["result"] == "failure" and "restored to last good" not in r["summary"] and "Automatic recovery FAILED" in r["summary"], "recovery serving the FAILED commit's identity (sha_path) is not accepted")
r = deploy(FEAT, inputs=dict(PREV_IN, sha_path="/version.json"), env={"STUB_PROBES": "500 ;200 ", "STUB_DEPLOYMENTS": good_dep(), "STUB_SHA_BODY": GOOD[:7]})
check("restored to last good commit" in r["summary"], "recovery serving the last-good commit identity is verified")
# a newer push superseded this run while the smoke was failing: recovery must not replace the newer deployment
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ", "STUB_DEPLOYMENTS": good_dep(), "STUB_TIPS": f"{SHA},{SHA},{SHA},{OTHER}"})
check(r["result"] == "failure" and r["log"].count("preview --name feat-x") == 1 and "superseded" in r["summary"], "recovery is skipped when a newer push owns the branch (it never replaces a newer deployment)")
# the empty-URL incident, executed through the real engine
for raw, label in (('{"preview":{"urls":[]}}', "empty preview.urls"), ('{"preview":{}}', "missing urls"), ("garbage", "malformed JSON"), ("", "no output")):
    r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PREVIEW_RAW": raw, "STUB_DEPLOYMENTS": good_dep()})
    check(r["result"] == "failure" and step(r, "Publish the audited artifact") == "failure", f"{label}: Publish fails")
    check(step(r, "Smoke the exact deployed URL (fails closed)") == "skipped" and r["outputs"]["deployed_and_verified"] == "false" and r["outputs"]["url"] == "", f"{label}: Smoke never runs against an empty URL and nothing is reported as deployed")
    check(r["log"].count("preview --name feat-x") == 1 and "no usable URL" in r["summary"] and "unverified" in r["summary"], f"{label}: no recovery is attempted and the summary says the Preview state is unverified")
r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PREVIEW_FAIL": "1", "STUB_DEPLOYMENTS": good_dep()})
check(r["result"] == "failure" and "unchanged" in r["summary"] and r["log"].count("preview --name feat-x") == 1, "wrangler preview failure before any mutation: 'unchanged', no recovery")
r = deploy(FEAT, inputs=dict(PREV_IN, build_cmd="false"), env={"STUB_DEPLOYMENTS": good_dep()})
check(r["result"] == "failure" and "preview --name" not in r["log"] and "unchanged" in r["summary"], "build failure: nothing published, no recovery")
r = deploy(FEAT, secrets=dict(PREV_SECRETS, PREVIEW_SECRETS="EVIL=$(touch PWNED)`touch PWNED2`;touch PWNED3 \"q\" 'x'\nPLAIN=a b=c"),
           inputs={"build_cmd": 'printf %s "$EVIL" > evil.out && printf %s "$PLAIN" > plain.out'})
w = r["ws"] / "w"
check(r["result"] == "success" and not any((w / n).exists() for n in ("PWNED", "PWNED2", "PWNED3")), "Preview secret with $() / backticks / ; is NOT executed")
check((w / "evil.out").read_text() == "$(touch PWNED)`touch PWNED2`;touch PWNED3 \"q\" 'x'" and (w / "plain.out").read_text() == "a b=c", "Preview secret values reach the build process literally")
check(not (r["ws"] / "tmp" / "cfv2").exists(), "staged Preview secrets and engine state are removed at the end of the job")
r = deploy(FEAT, secrets=dict(PREV_SECRETS, PREVIEW_SECRETS="CLOUDFLARE_API_TOKEN=evil"))
check(r["result"] == "failure" and not published(r), "Preview secrets cannot override Cloudflare credentials")
r = deploy(FEAT, secrets=PROD_SECRETS)
check(r["result"] == "failure" and not published(r), "a feature branch with ONLY Production credentials fails (never uses them)")
r = deploy(MAIN, secrets=dict(PROD_SECRETS, PREVIEW_SECRETS="K=v"), inputs={"build_cmd": 'printf %s "${K:-unset}" > k.out'})
check(r["result"] == "success" and (r["ws"] / "w" / "k.out").read_text() == "unset", "Production build never receives Preview-only secrets")

# ======================================================================= Migration is a STEP of Deploy, after build + audit
MB = 'echo BUILD >> "$STUB_LOG"'
m = deploy(MAIN, inputs=dict(D1, build_cmd=MB))
check(m["result"] == "success" and "d1 migrations apply DB --remote" in m["log"] and "PROD-ID" in mig_file(m, "prod"), "prod migration applies to the Production D1")
check(m["log"].index("BUILD") < m["log"].index("migrations apply") < m["log"].index("versions upload"), "order in the executed job: build -> migration -> publish")
check(step(m, "Apply pending D1 migrations (this target only)") == "success" and m["outputs"]["migration"] == "applied", "Migration is an explicit step inside the Deploy job and reports `applied`")
m = deploy(FEAT, inputs=dict(D1, build_cmd=MB))
check(m["result"] == "success" and "PREV-ID" in mig_file(m, "preview") and "PROD-ID" not in mig_file(m, "preview") and m["log"].index("BUILD") < m["log"].index("migrations apply") < m["log"].index("preview --name"), "preview migration applies to previews.d1_databases only, after the build and before the publish")
m = deploy(FEAT, inputs=D1, env={"STUB_PENDING": "0"})
check(m["result"] == "success" and not migrated(m) and "versions" not in m["log"] and "preview --name feat-x" in m["log"], "nothing pending: success without applying, publish continues")
m = deploy(FEAT, inputs=D1, env={"STUB_OWNER_REF": "refs/heads/Feat/X"})
check(m["result"] == "failure" and not migrated(m) and not published(m), "name collision: second branch fails BEFORE any remote migration")
m = deploy(FEAT, inputs=D1, env={"STUB_TIPS": OTHER})
check(m["result"] == "success" and not migrated(m) and not published(m) and m["outputs"]["deployed_and_verified"] == "false", "stale branch: no migration applied, nothing published")
m = deploy(FEAT, inputs=D1, env={"STUB_TIPS": "GONE"})
check(m["result"] == "failure" and not migrated(m), "deleted branch: fails closed, no migration")
# P2: a push that lands DURING the (long) migration must not let the old code be published afterwards
m = deploy(FEAT, inputs=D1, env={"STUB_TIPS": f"{SHA},{SHA},{OTHER}"})
check(m["result"] == "success" and migrated(m) and not published(m) and m["outputs"]["deployed_and_verified"] == "false", "branch advanced DURING the migration: re-checked right before publish, old code is NOT published")
check(step(m, "Stale-SHA guard (after migration, before publish)") == "success" and step(m, "Publish the audited artifact") == "skipped" and step(m, "Smoke the exact deployed URL (fails closed)") == "skipped", "post-migration stale run skips publish, smoke and verification")
bad = APP_TOML.replace('database_id = "PREV-ID"', 'database_id = "PROD-ID"').replace('database_name = "prev-db"', 'database_name = "prod-db"')
m = deploy(FEAT, inputs=D1, toml=bad)
check(m["result"] == "failure" and not migrated(m) and not published(m), "Preview D1 identical to Production D1: fails before any migration (preflight / target check)")
m = deploy(MAIN, inputs=D1, toml=bad)
check(m["result"] == "failure" and not published(m), "same for the Production target: nothing published")
amb = APP_TOML + '[[previews.d1_databases]]\nbinding = "DB2"\ndatabase_name = "p2"\ndatabase_id = "P2"\n' + '[[d1_databases]]\nbinding = "DB2"\ndatabase_name = "x2"\ndatabase_id = "X2"\n'
m = deploy(FEAT, inputs=D1, toml=amb)
check(m["result"] == "failure" and not migrated(m), "ambiguous D1 bindings without d1_binding: fails closed")
m = deploy(FEAT, inputs=dict(D1, d1_binding="DB2"), toml=amb)
check(m["result"] == "success" and "P2" in mig_file(m, "preview"), "d1_binding selects the intended database")
m = deploy(FEAT, inputs=D1, toml='name = "app"\n[previews]\n')
check(m["result"] == "failure" and not migrated(m), "no D1 declared for the target while d1_migrations is on: fails closed")
m = deploy(FEAT, inputs=D1, secrets={})
check(m["result"] == "failure" and not migrated(m), "missing credentials: fails before any remote command")
m = deploy(FEAT, inputs=D1, secrets=PROD_SECRETS)
check(m["result"] == "failure" and not migrated(m), "branch with ONLY Production credentials fails")
for ref, label in ((FEAT, "preview"), (MAIN, "prod")):
    m = deploy(ref, inputs=D1, env={"STUB_APPLY_FAIL": "1"})
    check(m["result"] == "failure" and migrated(m) and not published(m) and m["outputs"]["deployed_and_verified"] == "false" and m["outputs"]["migration"] == "failed", f"failed {label} migration: job fails, NOTHING is published or verified")
    check(not release_runs(m), f"failed {label} migration -> Release cannot run")
m = deploy(FEAT, inputs=dict(D1, build_cmd="false"))
check(m["result"] == "failure" and not migrated(m) and not published(m), "a failed build never reaches the migration (schema is not changed before a deployable exists)")

# ---- D1 the deployed config binds == D1 the migration targets (adapter-generated effective config)
GEN = 'mkdir -p .wrangler/deploy dist && echo "{\\"configPath\\":\\"../../dist/wrangler.json\\"}" > .wrangler/deploy/config.json && echo \'%s\' > dist/wrangler.json'
eff = lambda cfg: GEN % json.dumps(cfg)
mismatch = {"name": "app", "d1_databases": [{"binding": "DB", "database_name": "other-db", "database_id": "OTHER-ID"}]}
m = deploy(MAIN, inputs=dict(D1, build_cmd=eff(mismatch)))
check(m["result"] == "failure" and not migrated(m) and not published(m), "prod: effective config binds a DIFFERENT D1 than the migration target -> fails before migration and publish")
m = deploy(MAIN, inputs=dict(D1, build_cmd=eff({"name": "app", "d1_databases": [{"binding": "DB", "database_name": "prod-db", "database_id": "PROD-ID"}]})))
check(m["result"] == "success" and migrated(m), "prod: effective config binding the SAME D1 migrates and publishes")
m = deploy(FEAT, inputs=dict(D1, build_cmd=eff({"name": "app", "previews": {"d1_databases": [{"binding": "DB", "database_name": "x", "database_id": "WRONG"}]}})))
check(m["result"] == "failure" and not migrated(m) and not published(m), "preview: effective config binds a different D1 -> fails before migration and publish")
m = deploy(FEAT, inputs=dict(D1, build_cmd=eff({"name": "app", "previews": {}})))
check(m["result"] == "failure" and not migrated(m), "preview: effective config without the migrated D1 binding fails")
m = deploy(MAIN, inputs={"build_cmd": eff({"name": "app", "d1_databases": [{"binding": "DB", "database_name": "prev-db", "database_id": "PREV-ID"}]})})
check(m["result"] == "failure" and not published(m), "prod: effective config binding a Preview resource is rejected even without migrations")

# ---- P1: application environment can never redirect the delivery target / credentials / verification
EVIL = [("TARGET=prod", "TARGET"), ("TARGET=dev", "TARGET"), ("DEV_ENV=prod", "DEV_ENV"), ("GH_TOKEN=x", "GH_TOKEN"), ("EXPECTED_SHA=fake", "EXPECTED_SHA"),
        ("D1_BINDING=OTHER", "D1_BINDING"), ("CLOUDFLARE_API_TOKEN=x", "CLOUDFLARE_API_TOKEN"), ("STATE_DIR=/tmp/x", "STATE_DIR"), ("RUN_SHA=fake", "RUN_SHA")]
for line, name in EVIL:
    for inp in ("build_env_prod", "smoke_env"):
        m = deploy(MAIN, inputs=dict(D1, **{inp: line}))
        check(m["result"] == "failure" and not migrated(m) and not published(m), f"{inp} cannot set the protected variable {name}: rejected before any work")
    m = deploy(FEAT, inputs=dict(D1, build_env_preview=line))
    check(m["result"] == "failure" and not migrated(m) and not published(m), f"build_env_preview cannot set {name}")
# a build COMMAND that writes TARGET/DEV_ENV into GITHUB_ENV still cannot redirect the migration: the engine binds the target
m = deploy(FEAT, inputs=dict(D1, build_cmd='echo TARGET=prod >> "$GITHUB_ENV"; echo DEV_ENV=prod >> "$GITHUB_ENV"; echo D1_BINDING=NOPE >> "$GITHUB_ENV"'))
check(m["result"] == "success" and "PREV-ID" in mig_file(m, "preview") and not (m["ws"] / "w" / ".wrangler" / "migrate.prod.json").exists(), "a build writing TARGET=prod into GITHUB_ENV cannot move the Preview migration to Production")

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
[[queues.producers]]
binding = "Q"
queue = "prod-queue"
[[services]]
binding = "AUTH"
service = "auth-prod"
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
[[env.dev.queues.producers]]
binding = "Q"
queue = "dev-queue"
[[env.dev.services]]
binding = "AUTH"
service = "auth-dev"
'''
DEV_INPUTS = {
    "shared_dev_on_branch_push": True, "app_url_dev": "https://dev.example.com", "dev_worker_name": "app-dev",
    "app_version": SHA, "build_env_dev": f"PUBLIC_COMMIT_SHA={SHA}\nSITE=dev", "dev_wrangler_env": "dev",
    "dev_build_cmd": 'echo "BUILD ver=$VITE_APP_VERSION commit=$PUBLIC_COMMIT_SHA cfenv=$CLOUDFLARE_ENV" >> "$STUB_LOG"',
    "dev_deploy_cmd": 'echo "PUBLISH cfg=$CF_AUDITED_CONFIG" >> "$STUB_LOG"',
    "dev_binding_allow": "r2_buckets:ASSETS=prod-assets services:AUTH=auth-dev",
}


def dev(inputs=None, env=None, toml=DEV_TOML, needs=None, secrets=None, ref=FEAT):
    ws = make_workspace(toml)
    n = needs if needs is not None else {"deploy": {"result": "skipped", "outputs": {}}}
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
r = dev(inputs=D1)
check(r["result"] == "success" and "d1 migrations apply DB --remote" in r["log"] and "SHARED-ID" in mig_file(r, "dev") and "PROD-ID" not in mig_file(r, "dev"), "Shared Dev migrates the Dev D1 (env.dev), never the Production D1")
check(r["log"].index("BUILD") < r["log"].index("migrations apply") < r["log"].index("PUBLISH"), "order in the executed Shared Dev job: build -> audit -> migration -> publish")
check("PUBLISH cfg=" in r["log"] and "wrangler.toml" in r["log"], "the caller's publish command receives the audited config (CF_AUDITED_CONFIG)")
r = dev(inputs={"dev_deploy_cmd": ""})
check(r["result"] == "success" and "wrangler deploy --config" in r["log"] and "--env dev" in r["log"], "default publish is `wrangler deploy --config <audited config> --env <dev env>` (no reliance on CLOUDFLARE_ENV alone)")

r = dev(inputs=dict(D1, dev_build_cmd="false"))
check(r["result"] == "failure" and not ran(r, "migrations apply") and not ran(r, "PUBLISH") and "unchanged" in r["summary"], "build failure: no migration, no publish, explicit 'Dev unchanged' outcome")
r = dev(toml=DEV_TOML.replace('name = "app-dev"', 'name = "app"'), inputs=D1)
check(r["result"] == "failure" and ran(r, "BUILD") and not ran(r, "migrations apply") and not ran(r, "PUBLISH"), "audit failure (wrong Worker identity): no migration, no publish")
r = dev(inputs=D1, env={"STUB_APPLY_FAIL": "1"})
check(r["result"] == "failure" and ran(r, "BUILD") and not ran(r, "PUBLISH") and r["outputs"]["deployed_and_verified"] == "false", "migration failure prevents publish")
r = dev(inputs={"dev_binding_allow": "services:AUTH=auth-dev"})
check(r["result"] == "failure" and not ran(r, "PUBLISH") and not ran(r, "migrations apply"), "unapproved Production R2 sharing fails the audit before anything is published")
r = dev(inputs={"dev_binding_allow": "r2_buckets:ASSETS=another-bucket services:AUTH=auth-dev"})
check(r["result"] == "failure" and not ran(r, "PUBLISH"), "allowlist naming a different bucket does not approve the shared one")
r = dev(inputs={"dev_deny_resources": "prod-assets"})
check(r["result"] == "failure" and not ran(r, "PUBLISH"), "dev_deny_resources beats dev_binding_allow")
r = dev(inputs={"preview_deny_resources": "prod-assets"})
check(r["result"] == "success", "a Preview-only denylist does not affect the approved Dev share")
# P1: Production Queue / Service / storage bindings in the Dev config
for label, repl in (("Production Queue producer", ('queue = "dev-queue"', 'queue = "prod-queue"')),
                    ("Production Service binding", ('service = "auth-dev"', 'service = "auth-prod"')),
                    ("Production D1", ('[[env.dev.d1_databases]]\nbinding = "DB"\ndatabase_name = "shared-db"\ndatabase_id = "SHARED-ID"', '[[env.dev.d1_databases]]\nbinding = "DB"\ndatabase_name = "prod-db"\ndatabase_id = "PROD-ID"'))):
    r = dev(toml=DEV_TOML.replace(*repl), inputs={"dev_binding_allow": "r2_buckets:ASSETS=prod-assets services:AUTH=auth-prod"} if "Service" in label else None)
    check(r["result"] == "failure" and ran(r, "BUILD") and not ran(r, "PUBLISH") and r["outputs"]["deployed_and_verified"] == "false", f"Dev deployment with a {label} fails the audit before publish")
r = dev(toml=DEV_TOML.replace('queue = "dev-queue"', 'queue = "prod-queue"'), inputs=D1)
check(r["result"] == "failure" and not ran(r, "migrations apply"), "a Production Queue in the Dev config also stops the Dev migration")
# Production KV / Hyperdrive / Durable Object / Secrets Store through the effective (adapter-generated) config
for label, extra in (("KV namespace", {"kv_namespaces": [{"binding": "K", "id": "PROD-KV"}]}), ("Hyperdrive", {"hyperdrive": [{"binding": "H", "id": "PROD-HD"}]}),
                     ("Durable Object from another Worker", {"durable_objects": {"bindings": [{"name": "DO", "class_name": "C", "script_name": "app"}]}}),
                     ("Queue consumer", {"queues": {"consumers": [{"queue": "prod-queue"}]}})):
    src = DEV_TOML + {"KV namespace": '[[kv_namespaces]]\nbinding = "K"\nid = "PROD-KV"\n', "Hyperdrive": '[[hyperdrive]]\nbinding = "H"\nid = "PROD-HD"\n'}.get(label, "")
    eff_cfg = dict({"name": "app-dev", "d1_databases": [{"binding": "DB", "database_name": "shared-db", "database_id": "SHARED-ID"}],
                    "r2_buckets": [{"binding": "ASSETS", "bucket_name": "prod-assets"}], "queues": {"producers": [{"binding": "Q", "queue": "dev-queue"}]},
                    "services": [{"binding": "AUTH", "service": "auth-dev"}]}, **extra)
    r = dev(toml=src, inputs={"dev_build_cmd": eff(eff_cfg)})
    check(r["result"] == "failure" and not ran(r, "PUBLISH"), f"effective Dev config with a Production {label} fails the audit")
r = dev(inputs={"dev_build_cmd": eff({"name": "app-dev", "d1_databases": [{"binding": "DB", "database_name": "shared-db", "database_id": "SHARED-ID"}],
                                        "r2_buckets": [{"binding": "ASSETS", "bucket_name": "prod-assets"}], "queues": {"producers": [{"binding": "Q", "queue": "dev-queue"}]},
                                        "services": [{"binding": "AUTH", "service": "auth-dev"}]})})
check(r["result"] == "success" and "PUBLISH cfg=" in r["log"] and "dist/wrangler.json" in r["log"], "a clean effective Dev config is audited AND is the exact config handed to the publish")

# P1: D1 consistency and same-artifact contract for Dev
r = dev(inputs=dict(D1, dev_build_cmd=eff({"name": "app-dev", "d1_databases": [{"binding": "DB", "database_name": "elsewhere", "database_id": "ELSEWHERE-ID"}],
                                             "r2_buckets": [{"binding": "ASSETS", "bucket_name": "prod-assets"}], "queues": {"producers": [{"binding": "Q", "queue": "dev-queue"}]},
                                             "services": [{"binding": "AUTH", "service": "auth-dev"}]})))
check(r["result"] == "failure" and not ran(r, "migrations apply") and not ran(r, "PUBLISH"), "Dev: deployed (effective) D1 differs from the migration target -> neither migrated nor published (no DB-A migration with a DB-B Worker)")
r = dev(inputs={"dev_deploy_cmd": 'echo PUBLISH >> "$STUB_LOG"; sed -i "s/app-dev/app-other/" "$CF_AUDITED_CONFIG"'})
check(r["result"] == "failure" and ran(r, "PUBLISH") and r["outputs"]["deployed_and_verified"] == "false" and "unverified" in r["summary"].lower() and "state=success" not in r["log"], "a publish command that MODIFIES the audited config fails the run and is never verified")
r = dev(inputs={"dev_deploy_cmd": 'echo PUBLISH >> "$STUB_LOG"; mkdir -p dist; echo rebuilt > dist/index.js; sed -i "s/^name = \\"app-dev\\"/name = \\"app-dev\\"\\nmain = \\"dist\\/index.js\\"/" wrangler.toml'})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false", "a publish command that rewrites the config to point at a replaced artifact is detected")
# the audited ARTIFACT (Worker entry), not only the config file, is protected: a publish command that rebuilds it is caught
MAIN_TOML = DEV_TOML.replace('name = "app"\n', 'name = "app"\nmain = "dist/index.js"\n', 1)
BUILD_ENTRY = 'mkdir -p dist && echo audited > dist/index.js && echo "BUILD" >> "$STUB_LOG"'
r = dev(toml=MAIN_TOML, inputs={"dev_build_cmd": BUILD_ENTRY})
check(r["result"] == "success" and r["outputs"]["deployed_and_verified"] == "true", "Worker entry built by the build stage and published untouched: verified")
r = dev(toml=MAIN_TOML, inputs={"dev_build_cmd": BUILD_ENTRY, "dev_deploy_cmd": 'echo PUBLISH >> "$STUB_LOG"; echo replaced > dist/index.js'})
check(r["result"] == "failure" and ran(r, "PUBLISH") and r["outputs"]["deployed_and_verified"] == "false" and "state=success" not in r["log"], "a publish command that replaces the audited Worker entry (config untouched) fails the run and is never verified")
# P1: build env / smoke env cannot redirect Dev
for line in ("TARGET=prod", "DEV_ENV=prod", "GH_TOKEN=x", "EXPECTED_SHA=fake"):
    r = dev(inputs=dict(D1, build_env_dev=line))
    check(r["result"] == "failure" and not ran(r, "migrations apply") and not ran(r, "PUBLISH"), f"build_env_dev cannot set {line.split('=')[0]}")
r = dev(inputs=dict(D1, smoke_env="TARGET=prod"))
check(r["result"] == "failure" and not ran(r, "migrations apply"), "smoke_env cannot set TARGET either")
r = dev(inputs=dict(D1, dev_build_cmd='echo TARGET=prod >> "$GITHUB_ENV"; echo DEV_ENV=prod >> "$GITHUB_ENV"; echo "BUILD" >> "$STUB_LOG"'))
check(r["result"] == "success" and "SHARED-ID" in mig_file(r, "dev") and not (r["ws"] / "w" / ".wrangler" / "migrate.prod.json").exists(), "a Dev build writing TARGET=prod / DEV_ENV=prod into GITHUB_ENV cannot redirect the Dev migration to Production")

r = dev(secrets={})
check(r["result"] == "failure" and not ran(r, "BUILD"), "missing Dev credentials fail closed before the build")
r = dev(secrets={"CLOUDFLARE_API_TOKEN": "PT", "CLOUDFLARE_ACCOUNT_ID": "PA", "CLOUDFLARE_PREVIEW_API_TOKEN": "VT"})
check(r["result"] == "failure" and not ran(r, "BUILD"), "Production/Preview credentials are never a fallback for Dev")
r = dev(env={"STUB_TIPS": OTHER})
check(r["result"] == "success" and not ran(r, "BUILD") and not ran(r, "PUBLISH") and r["outputs"]["deployed_and_verified"] == "false", "stale run: nothing built, migrated or published")
r = dev(env={"STUB_TIPS": f"{SHA},{OTHER}"}, inputs=D1)
check(r["result"] == "success" and ran(r, "BUILD") and not ran(r, "migrations apply") and not ran(r, "PUBLISH"), "superseded after the build: no migration and no publish")
r = dev(env={"STUB_TIPS": f"{SHA},{SHA},{OTHER}"}, inputs=D1)
check(r["result"] == "success" and ran(r, "migrations apply") and not ran(r, "PUBLISH"), "branch advanced during the Dev migration: old code is not published")

# Shared-D1 guard: Preview and Dev sharing one database
fail_dep = {"deploy": {"result": "failure", "outputs": {"migration": "failed"}}}
r = dev(inputs=D1, needs=fail_dep)
check(r["result"] == "failure" and not ran(r, "migrations apply") and not ran(r, "PUBLISH"), "Preview migration failed on the SHARED D1: Dev migrates and publishes nothing")
r = dev(inputs=D1, needs={"deploy": {"result": "cancelled", "outputs": {"migration": ""}}})
check(r["result"] == "failure" and not ran(r, "PUBLISH"), "...also when the Preview job was cancelled")
distinct = DEV_TOML.replace('database_name = "shared-db"\ndatabase_id = "SHARED-ID"\n[env.dev]', 'database_name = "preview-only-db"\ndatabase_id = "PREVIEW-ONLY"\n[env.dev]')
assert distinct != DEV_TOML
r = dev(inputs=D1, needs=fail_dep, toml=distinct)
check(r["result"] == "success" and ran(r, "PUBLISH"), "Preview migration failed but the D1s differ: Shared Dev is independent and deploys")
r = dev(inputs=D1, needs={"deploy": {"result": "failure", "outputs": {"migration": "applied"}}})
check(r["result"] == "success" and ran(r, "PUBLISH"), "Preview deploy failed AFTER a successful shared-D1 migration (e.g. smoke): Dev proceeds")
r = dev(inputs=D1, needs={"deploy": {"result": "success", "outputs": {"migration": "applied"}}})
check(r["result"] == "success", "Preview migration succeeded on the shared D1: Dev proceeds (ordered after it)")

# Preview and Dev sharing one physical D1, end to end: Preview deploy first, then Dev ordered after it
SHARED_TOML = APP_TOML.replace('database_name = "prev-db"\ndatabase_id = "PREV-ID"', 'database_name = "shared-db"\ndatabase_id = "SHARED-ID"') + \
    '[env.dev]\nname = "app-dev"\n[[env.dev.d1_databases]]\nbinding = "DB"\ndatabase_name = "shared-db"\ndatabase_id = "SHARED-ID"\n'
pv = deploy(FEAT, inputs=D1, toml=SHARED_TOML)
dv = dev(inputs=dict(D1, dev_binding_allow=""), toml=SHARED_TOML, needs={"deploy": {"result": pv["result"], "outputs": pv["outputs"]}})
check(pv["result"] == "success" and dv["result"] == "success" and "SHARED-ID" in mig_file(pv, "preview") and "SHARED-ID" in mig_file(dv, "dev") and pv["outputs"]["migration"] == "applied",
      "Preview and Dev sharing one D1 both migrate the same database, one after the other")
pf = deploy(FEAT, inputs=D1, toml=SHARED_TOML, env={"STUB_APPLY_FAIL": "1"})
df = dev(inputs=dict(D1, dev_binding_allow=""), toml=SHARED_TOML, needs={"deploy": {"result": pf["result"], "outputs": pf["outputs"]}})
check(pf["outputs"]["migration"] == "failed" and df["result"] == "failure" and not ran(df, "migrations apply") and not ran(df, "PUBLISH"), "shared D1: a failed Preview migration (job output) stops the Dev migration and publish")

r = dev(inputs={"dev_deploy_cmd": ""})
check(r["result"] == "success" and "wrangler deploy --config " in r["log"] and "wrangler.toml --env dev" in r["log"] and r["log"].index("BUILD") < r["log"].index("wrangler deploy"), "Shared Dev built-in publish (no dev_deploy_cmd): wrangler deploy gets the audited --config (and the named env for a source config), after the build")

# Config guard, on the REAL deployment path: the engine refuses before any remote command
r = deploy(MAIN, inputs={"deployment_strategy": "production-only", "shared_dev_on_branch_push": True})
check(r["result"] == "failure" and not published(r) and "wrangler versions" not in r["log"] and "migrations apply" not in r["log"] and "-X POST" not in r["log"], "production-only + Shared Dev: the engine fails at init, before build, migration, publish or any ledger write")
r = deploy(MAIN, inputs={"deployment_strategy": "production-only", "preview_on_branch_push": True}, env={"STUB_PROBES": "200 "})
check(r["result"] == "success" and published(r) and r["outputs"]["deployed_and_verified"] == "true", "production-only + default Preview=true: accepted, Production deploys")

# PR lifecycle advertises only a VERIFIED Preview bound to this host Worker, application, repo and branch
def lifecycle(deps, status="success https://stable.example.workers.dev Serving good (content)", inputs=None):
    ws = make_workspace(APP_TOML)
    c = ctx(FEAT, inputs, {}, event="pull_request")
    c["github"]["event"] = {"pull_request": {"head": {"ref": "feat/x", "repo": {"full_name": "o/r"}}}}
    r = run_job(doc, "lifecycle", c, ws, {"STUB_DEPLOYMENTS": deps, "STUB_STATUS": status})
    return r


def advertised(r):
    return "pulls?state=open" in r["log"]


r = lifecycle(good_dep())
check(r["result"] == "success" and advertised(r), "lifecycle: a verified Preview for this host Worker, app, repo and branch is advertised")
check(not advertised(lifecycle(good_dep(payload={"host_worker": "blog-preview"}))), "lifecycle: an old-host (blog-preview) Preview record is NOT advertised for the new host")
check(not advertised(lifecycle(good_dep(payload={"branch_ref": "refs/heads/other"}))), "lifecycle: another branch ref is not advertised")
check(not advertised(lifecycle(good_dep(payload={"worker": "other-app"}))) and not advertised(lifecycle(good_dep(payload={"repo": "x/y"}))), "lifecycle: another application or repository is not advertised")
check(not advertised(lifecycle(good_dep(), status="success https://stable.example.workers.dev Published s; unverified (liveness)")), "lifecycle: a liveness-only Preview is not advertised as verified")
check(not advertised(lifecycle(good_dep(), status="success http://insecure.example")), "lifecycle: a non-https or unverified record is not advertised")
r = lifecycle("[]")
check(r["result"] == "success" and not advertised(r), "lifecycle: no matching verified Preview leaves the PR comment unchanged (job still succeeds)")

# Verification level: Access liveness is published-but-unverified and never a known-good record
r = dev(inputs={"access_protected_targets": "preview dev"}, env={"STUB_PROBES": "302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x"})
check(r["result"] == "success" and r["outputs"]["smoke_level"] == "liveness" and r["outputs"]["deployed_and_verified"] == "false" and r["outputs"]["verified_url"] == "" and r["outputs"]["published_unverified"] == "true", "Shared Dev Access liveness only: published, NOT verified")
check("state=success" not in r["log"] and "state=inactive" in r["log"], "Shared Dev liveness is not recorded as content-verified last-good")

# Smoke failure and recovery
GD = {"STUB_DEPLOYMENTS": json.dumps([{"id": 7, "sha": GOOD, "payload": {"repo": "o/r", "dev_worker": "app-dev", "worker": "app"}}]), "STUB_STATUS": "success https://dev.example.com Serving good (content)"}
r = dev(env=dict(GD, STUB_PROBES="500 ;200 "))
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false" and "unverified" in r["summary"], "smoke failure: job red, deployment marked unverified")
builds = [l for l in r["log"].splitlines() if l.startswith("BUILD")]
check(len(builds) == 2 and builds[1] == f"BUILD ver={GOOD} commit={GOOD} cfenv=dev", "recovery builds the LAST-GOOD commit with its own version and rebound build env (not the failed SHA)")
check(r["log"].count("PUBLISH") == 2 and "Run remains FAILED" in r["summary"] and "Commit identity was NOT verified" in r["summary"], "recovery redeploys; without sha_path it says commit identity was NOT verified; run stays red")
r = dev(inputs={"sha_path": "/version.json"}, env=dict(GD, STUB_PROBES="500 ;200 ", STUB_SHA_BODY=GOOD[:7]))
check(r["result"] == "failure" and "restored to last good commit" in r["summary"] and "Commit identity was NOT verified" not in r["summary"], "with sha_path the recovery verifies the restored commit identity")
r = dev(inputs={"sha_path": "/version.json"}, env=dict(GD, STUB_PROBES="500 ;200 ", STUB_SHA_BODY=SHA[:7]))
check(r["result"] == "failure" and "restored to last good" not in r["summary"], "recovery serving the wrong commit is NOT reported as verified")
r = dev(env=dict(GD, STUB_PROBES="500 ;500 "))
check(r["result"] == "failure" and "recovery FAILED" in r["summary"] and "state=error" in r["log"], "recovery failure stays visible and is recorded as error")
r = dev(env={"STUB_PROBES": "500 "})
check(r["result"] == "failure" and r["log"].count("PUBLISH") == 1 and "manual recovery required" in r["summary"], "no known-good commit: no recovery attempted, manual recovery required")
r = dev(env=dict(GD, STUB_PROBES="500 ;200 "))
check("versions upload" not in r["log"] and "versions deploy" not in r["log"] and "preview --name" not in r["log"], "Dev recovery never takes the Production or Preview publish path")
check("state=success" not in r["log"], "a failed Dev deployment whose recovery succeeded is still never recorded as verified")
r = dev(env=dict(GD, STUB_PROBES="500 ", STUB_TIPS=f"{SHA},{SHA},{SHA},{OTHER}"))
check(r["result"] == "failure" and r["log"].count("PUBLISH") == 1 and "superseded" in r["summary"], "Dev recovery is skipped when a newer push owns the branch")
tamper = 'case "$PWD" in */recover*) sed -i "s/name = \\"app-dev\\"/name = \\"app\\"/" wrangler.toml;; esac; echo "BUILD ver=$VITE_APP_VERSION commit=$PUBLIC_COMMIT_SHA cfenv=$CLOUDFLARE_ENV" >> "$STUB_LOG"'
r = dev(inputs={"dev_build_cmd": tamper}, env=dict(GD, STUB_PROBES="500 ;200 "))
check(r["result"] == "failure" and r["log"].count("PUBLISH") == 1 and "Automatic recovery FAILED" in r["summary"], "the config audit runs on the RECOVERED build: a last-good tree with the wrong Worker identity is not published")
rb = dev(env=dict(GD, STUB_PROBES="500 ;200 "), inputs={"build_env_dev": f"PUBLIC_COMMIT_SHA={SHA}\nSHORT={SHA[:7]}"})
check(f"commit={GOOD}" in rb["log"], "build env referencing the failed SHA (full) is rebound for the restored commit")

# Manual persistent-dev: legacy vs engine contract
PD = dict(DEV_INPUTS, deployment_strategy="hybrid", persistent_dev_ref="refs/heads/dev", dispatch_target="persistent-dev", shared_dev_on_branch_push=False)


def manual(inputs=None, env=None, toml=DEV_TOML):
    ws = make_workspace(toml)
    r = run_job(doc, "persistent-dev", ctx("refs/heads/dev", dict(PD, **(inputs or {})), DEV_SECRETS, event="workflow_dispatch"), ws, dict({"STUB_TIPS": SHA}, **(env or {})))
    r["ws"] = ws
    return r


r = manual(inputs={"dev_build_cmd": "", "dev_smoke_cmd": 'echo SMOKE >> "$STUB_LOG"', "dev_deploy_cmd": 'echo "PUBLISH" >> "$STUB_LOG"'})
check(r["result"] == "success" and ran(r, "PUBLISH") and ran(r, "SMOKE") and not ran(r, "BUILD") and "-X POST" not in r["log"] and "statuses" not in r["log"], "legacy persistent-dev: unchanged (deploy + optional smoke), never written to the known-good ledger")
r = manual()
check(r["result"] == "success" and r["log"].index("BUILD") < r["log"].index("PUBLISH") and "state=success" in r["log"], "engine contract: manual run builds first, publishes, smokes and is recorded verified")
r = manual(inputs=D1)
check(r["result"] == "success" and r["log"].index("BUILD") < r["log"].index("migrations apply") < r["log"].index("PUBLISH") and "SHARED-ID" in mig_file(r, "dev"), "engine contract: manual run migrates the Dev D1 between build and publish")
r = manual(inputs={"dev_build_cmd": "false"})
check(r["result"] == "failure" and not ran(r, "PUBLISH") and "state=success" not in r["log"], "engine contract: manual build failure publishes nothing and records nothing as good")
r = manual(env={"STUB_PROBES": "500 ", **GD})
check(r["result"] == "failure" and ran(r, "PUBLISH") and "state=success" not in r["log"] and "state=failure" in r["log"] and r["log"].count("PUBLISH") == 1, "engine contract: a manual deploy whose smoke fails is NEVER recorded as known-good and is not auto-recovered")
r = manual(inputs={"dev_worker_name": ""})
check(r["result"] == "failure" and not ran(r, "PUBLISH"), "engine contract: manual run also requires the Worker identity")
r = manual(toml=DEV_TOML.replace('name = "app-dev"', 'name = "app"'))
check(r["result"] == "failure" and not ran(r, "PUBLISH"), "engine contract: manual run applies the same config audit")
r = manual(inputs={"persistent_dev_ref": "refs/heads/other"})
check(r["result"] == "failure" and not ran(r, "PUBLISH"), "engine contract: manual run from a ref other than persistent_dev_ref is refused by the engine too")

sys.exit(fail)
