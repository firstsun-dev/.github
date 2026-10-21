#!/usr/bin/env python3
"""Executes the REAL steps of the Migration and Deploy jobs (stub gh/pnpm/curl) and feeds the results into the
next job's `if:`. Covers result propagation the static contract test cannot: stale runs never reach Release,
smoke/rollback keep Release closed, nothing remote is touched before the pre-migration guards pass.
Mocked: no Cloudflare, no GitHub. See docs/cf-worker-v2-refactor.md for what this does NOT prove."""
import sys

import yaml

sys.path.insert(0, "scripts/cf-preview")
from ghexpr import Ctx, ev  # noqa: E402
from jobrunner import APP_TOML, make_workspace, run_job  # noqa: E402

WF = ".github/workflows/_cf-worker-v2.yml"
doc = yaml.safe_load(open(WF))
INPUT_DEFAULTS = {k: v.get("default", "") for k, v in doc[True]["workflow_call"]["inputs"].items()}
SHA, OTHER = "abcdef1234567890", "ffffffffffffffff"
MAIN, FEAT = "refs/heads/main", "refs/heads/feat/x"
fail = 0


def check(ok, msg):
    global fail
    print(("PASS: " if ok else "FAIL: ") + msg)
    if not ok:
        fail = 1


def ctx(ref, inputs=None, secrets=None, needs=None, event="push"):
    i = dict(INPUT_DEFAULTS, app_name="app", build_cmd="true", app_url_prod="https://app.example.com", tools_ref="x")
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
r = deploy(FEAT, env={"STUB_PROBES": "500 ;200 ", "STUB_LASTGOOD": "goodsha"})
check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false", "preview smoke failure: job red, not verified")
check(r["log"].count("preview --name feat-x") == 2, "preview recovery redeploys the last good SHA")
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

sys.exit(fail)
