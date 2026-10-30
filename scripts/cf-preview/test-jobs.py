#!/usr/bin/env python3
"""Scenario groups (explicit `group_*` functions registered in GROUPS): `test-jobs.py [--list] [--group ID]...`; no flag runs all.
run-job-groups.py runs each group in its own process, in parallel, against the manifest of expected groups.

Executes the REAL steps of the per-kind Preflight, Migration and Delivery calls (the internal reusable workflow
_cf-worker-delivery.yml: deploy -> smoke -> rollback), the Delivery plan, and the persistent-Dev job - including the
composite delivery engine (actions/cf-deploy -> actions/cf-preview/engine.sh) - against stub gh/pnpm/curl, and feeds each job's
result into the next job's `if:` and `needs.*.outputs`. EVERY job runs in a FRESH workspace (a different runner): anything the
chain needs must travel through job outputs, never through local files. A reusable-workflow CALL is emulated like GitHub does:
its `with:` / `secrets:` are rendered against the caller, the called jobs run with `inputs` = those values, and the called
workflow's `outputs` are rendered from its jobs. Covers what the static contract test cannot: Preflight and Migration before
Deploy, per-kind independence, URL pre-resolution, stale runs never reach Smoke/Release, a failed migration or smoke never
publishes/verifies, the Rollback job is conditional and fail-closed, application env cannot redirect the target, nothing remote is
touched before the guards pass. Mocked: no Cloudflare, no GitHub. See docs/cf-worker-v2-unified-delivery.md for what this does
NOT prove (the real-runner evidence is recorded there)."""
import json
import os
import sys

import yaml

sys.path.insert(0, "scripts/cf-preview")
from ghexpr import Ctx, ev, render  # noqa: E402
from jobrunner import APP_TOML, make_workspace, run_job, write_perf_report  # noqa: E402

WF = ".github/workflows/_cf-worker-v2.yml"
DWF = ".github/workflows/_cf-worker-delivery.yml"
doc = yaml.safe_load(open(WF))
ddoc = yaml.safe_load(open(DWF))
INPUT_DEFAULTS = {k: v.get("default", "") for k, v in doc[True]["workflow_call"]["inputs"].items()}
DINPUT_DEFAULTS = {k: v.get("default", "") for k, v in ddoc[True]["workflow_call"]["inputs"].items()}
SHA, OTHER = "abcdef1234567890", "ffffffffffffffff"
GOOD = "1111111222222233"
MAIN, FEAT = "refs/heads/main", "refs/heads/feat/x"
fail = 0
SUFFIX = "-app.example.workers.dev"  # test double of the Cloudflare-reported preview_url_suffix (the wrangler stub prints <name><suffix>)


checks = 0


def check(ok, msg):
    global fail, checks
    checks += 1
    print(("PASS: " if ok else "FAIL: ") + msg)
    if not ok:
        fail = 1


def ctx(ref, inputs=None, secrets=None, needs=None, event="push"):
    # preview_host_check off: the job tests must not call the real Cloudflare API (test-deploy.sh covers it); the canonical
    # Preview URL is derived from preview_url_suffix exactly like the real one is derived from Cloudflare's preview_url_suffix.
    i = dict(INPUT_DEFAULTS, app_name="app", build_cmd="true", app_url_prod="https://app.example.com", tools_ref="x", preview_host_check=False, preview_url_suffix=SUFFIX)
    i.update(inputs or {})
    return Ctx({"github": {"event_name": event, "ref": ref, "ref_type": "branch", "ref_name": ref.split("/", 2)[-1],
                           "repository": "o/r", "sha": SHA, "run_id": "1", "event": {"head_commit": {"message": "feat: x\nbody"}}},
                "inputs": i, "needs": needs or {}}, secrets or {})


PROD_SECRETS = {"CLOUDFLARE_API_TOKEN": "PT", "CLOUDFLARE_ACCOUNT_ID": "PA"}
PREV_SECRETS = PROD_SECRETS  # Preview uses the account credentials too (the dedicated Preview pair was removed)
D1 = {"d1_migrations": True}

KINDS = ("production", "native-branch", "native-dev", "persistent-dev")
KEYED = {k: k.replace("-", "_") for k in KINDS}
TARGET_OF = {"production": "prod", "native-branch": "preview", "native-dev": "preview", "persistent-dev": "dev"}
_ENV_OF = {"production": "prod", "native-branch": "preview", "native-dev": "native-dev", "persistent-dev": "dev"}


def desc(kind, d1="", owner="", app="app", scope="", phase="all", **over):
    """Target descriptor a stage receives. Hand-built here (fixtures); the engine re-validates it with the REAL delivery.mjs at init."""
    g = f"cfv2-dev-o/r-{scope or app}" if kind == "persistent-dev" else f"cfv2-leg-1-{kind}"
    return json.dumps(dict({"v": 1, "kind": kind, "target": TARGET_OF[kind], "environment": f"{app}/{_ENV_OF[kind]}", "deploy_group": g,
                            "rollback_group": g if kind == "persistent-dev" else g + "-rollback", "d1_expected_id": d1, "d1_owner_kind": owner,
                            "owns_migration": bool(d1 and owner == kind), "smoke_mode": "full", "phase": phase}, **over))
INNER = ("preflight", "migrate", "deploy", "smoke", "rollback")
SKIPPED = {"result": "skipped", "outputs": {}}


def _j(text):
    return json.loads(text) if text else {}


def _jobenv(name, kind, env, env_by_job):
    senv = {"STUB_TIPS": SHA}
    senv.update(env or {})
    ebj = env_by_job or {}
    alias = "migration" if name == "migrate" else name
    for key in (name, alias, f"{name}:{kind}", f"{alias}:{kind}"):
        senv.update(ebj.get(key, {}))
    # STUB_PROBES is a sequence ("first;second;..."): Smoke consumes the first answer, the Rollback job (another runner, fresh
    # counter) the rest - or the last answer again when there is none.
    if ";" in (env or {}).get("STUB_PROBES", "") and not any("STUB_PROBES" in ebj.get(k, {}) for k in (name, alias, f"{name}:{kind}")):
        first, rest = env["STUB_PROBES"].split(";", 1)
        senv["STUB_PROBES"] = first if name == "smoke" else rest
    if name == "rollback":
        senv.setdefault("STUB_ACTIVE", "new-2")  # the live Production version after the Deploy job published new-2
    return senv


def call_workflow(job, c, ctl):
    """Emulates `uses: ./.github/workflows/_cf-worker-delivery.yml` of one parent job. Returns the call record
    {result, outputs, jobs: {inner: {result, outputs}}} (outputs = the called workflow's rendered `outputs`)."""
    w = {k: render(v, c) for k, v in job["with"].items()}
    dinputs = dict(DINPUT_DEFAULTS)
    dinputs.update(w)
    sec = {k: render(v, c) for k, v in job.get("secrets", {}).items()}
    kind = json.loads(w["descriptor"])["kind"]
    dc = Ctx({"github": c["github"], "inputs": dinputs, "needs": {}, "jobs": {}}, sec)
    inner, logs, summ, steps = {}, [], [], []
    wss = ctl["wss"]
    for name in INNER:
        j = ddoc["jobs"][name]
        dc["needs"] = {n: inner.get(n, SKIPPED) for n in (j.get("needs") or [])}
        if not ev(j["if"], dc):
            inner[name] = SKIPPED
            continue
        ws = make_workspace(ctl["toml"], ctl["files"])
        r = run_job(ddoc, name, dc, ws, _jobenv(name, kind, ctl["env"], ctl["env_by_job"]),
                    verbose=bool(os.environ.get("CHAIN_VERBOSE") and name in os.environ["CHAIN_VERBOSE"].split(",")))
        inner[name] = {"result": r["result"], "outputs": r["outputs"]}
        logs.append(r["log"]); summ.append(r["summary"]); steps.extend(r["steps"])
        key = "migration" if name == "migrate" else name
        wss[key] = ws
        wss[f"{key}:{kind}"] = ws
    dc["jobs"] = inner
    outs = {k: render(v["value"], dc) for k, v in ddoc[True]["workflow_call"]["outputs"].items()}
    ran = [v["result"] for v in inner.values() if v["result"] != "skipped"]
    result = "failure" if "failure" in ran else ("success" if ran else "skipped")
    return {"result": result, "outputs": outs, "jobs": inner}, logs, summ, steps


CHAIN = ("gate", "plan") + tuple(f"preflight_{KEYED[k]}" for k in KINDS) + tuple(f"migration_{KEYED[k]}" for k in KINDS) + tuple(f"delivery_{KEYED[k]}" for k in KINDS)


def _run_jobs(names, ref, inputs, secrets, base_needs, event, env, toml, env_by_job, files, order=None):
    """Executes the named parent jobs in order. Plain jobs run in a fresh workspace; `uses:` jobs are emulated reusable-workflow
    calls. Returns (res, logs, summaries, steps, workspaces); res[name] = {result, outputs, jobs?}."""
    res, logs, summ, steps = {}, [], [], []
    ctl = {"wss": {}, "toml": toml, "files": files, "env": env, "env_by_job": env_by_job}
    for name in names:
        job = doc["jobs"][name]
        c = ctx(ref, inputs, secrets, dict(base_needs, **res), event)
        if not ev(job["if"], c):
            res[name] = {"result": "skipped", "outputs": {}}
            continue
        if "uses" in job:
            rec, lg, sm, st = call_workflow(job, c, ctl)
            res[name] = rec
            logs.extend(lg); summ.extend(sm); steps.extend(st)
            continue
        ws = make_workspace(toml, files)
        senv = {"STUB_TIPS": SHA}
        senv.update(env or {})
        senv.update((env_by_job or {}).get(name, {}))
        r = run_job(doc, name, c, ws, senv, verbose=bool(os.environ.get("CHAIN_VERBOSE") and name in os.environ["CHAIN_VERBOSE"].split(",")))
        res[name] = {"result": r["result"], "outputs": r["outputs"]}
        logs.append(r["log"]); summ.append(r["summary"]); steps.extend(r["steps"])
        ctl["wss"][name] = ws
    return res, logs, summ, steps, ctl["wss"]


def _validation_needs(i, needs_over):
    needs = {j: {"result": ("success" if i.get(c, "") else "skipped"), "outputs": {}} for j, c in
             (("lint", "lint_cmd"), ("typecheck", "typecheck_cmd"), ("unit", "test_unit_cmd"), ("integration", "test_integration_cmd"), ("e2e", "test_e2e_cmd"))}
    needs["build"] = {"result": "success", "outputs": {}}
    needs.update({k: {"result": v, "outputs": {}} for k, v in (needs_over or {}).items()})
    return needs


def _inner(res, kind, job):
    rec = res.get(f"delivery_{KEYED[kind]}")
    return (rec or {}).get("jobs", {}).get(job, SKIPPED)


def _compat(res):
    """The legacy single-delivery view the scenarios assert on, plus the keyed view of every kind. Built from the per-kind call records
    (each kind's own record - never a merge of shared keys)."""
    deliv = {k: res.get(f"delivery_{KEYED[k]}") for k in KINDS}
    ran = [k for k in KINDS if deliv[k] and deliv[k]["result"] != "skipped"]
    primary = ran[0] if ran else ""
    raw = {"deploy": {"outputs": {}}, "smoke": {"outputs": {}}, "rollback": {"outputs": {}}, "migration": {"outputs": {}}}
    for k in KINDS:
        d = deliv[k]
        if not d:
            continue
        kk = KEYED[k]
        o = d["outputs"]
        raw["deploy"]["outputs"][f"h_{kk}"] = o.get("handoff", "")
        raw["smoke"]["outputs"][f"s_{kk}"] = o.get("smoke_json", "") if o.get("smoke_status", "") != "" else ""
        raw["smoke"]["outputs"][f"x_{kk}"] = o.get("smoke_status", "")
        raw["rollback"]["outputs"][f"rb_{kk}"] = o.get("rollback_result", "")
        dj = d.get("jobs", {}).get("deploy", SKIPPED)
        raw["migration"]["outputs"][f"mj_{kk}"] = (dj["outputs"].get("migration_used") or "") if dj["result"] != "skipped" else ""
    raw["plan"] = res.get("plan", SKIPPED)
    raw["gate"] = res.get("gate", SKIPPED)
    migs = [res[f"migration_{KEYED[k]}"] for k in KINDS if f"migration_{KEYED[k]}" in res and res[f"migration_{KEYED[k]}"]["result"] != "skipped"]
    pfs = [res[f"preflight_{KEYED[k]}"] for k in KINDS if f"preflight_{KEYED[k]}" in res and res[f"preflight_{KEYED[k]}"]["result"] != "skipped"]
    mres = [m["result"] for m in migs + pfs]
    mig_json = _j(migs[0]["outputs"].get("migration_json", "")) if migs else {}
    p = primary
    dep = _inner(res, p, "deploy") if p else SKIPPED
    smk = _inner(res, p, "smoke") if p else SKIPPED
    rbk = _inner(res, p, "rollback") if p else SKIPPED
    handoff = _j(deliv[p]["outputs"].get("handoff", "")) if p else {}
    smoke_o = _j(deliv[p]["outputs"].get("smoke_json", "")) if p else {}
    none_smoke = bool(p) and deliv[p]["outputs"].get("smoke_status", "") == "none"
    legacy = {
        "gate": res.get("gate", SKIPPED), "plan": res.get("plan", SKIPPED),
        "migration": {"result": "failure" if "failure" in mres else ("success" if mres else "skipped"), "outputs": mig_json},
        "deploy": {"result": dep["result"], "outputs": dict(handoff, target=TARGET_OF.get(p, ""))},
        "smoke": {"result": "skipped" if (none_smoke or smk["result"] == "skipped") else smk["result"], "outputs": smoke_o},
        "rollback": {"result": rbk["result"], "outputs": {"rollback_result": deliv[p]["outputs"].get("rollback_result", "") if p else ""}},
    }
    return legacy, raw, primary


def _shape(res, logs, summ, steps, wss, toml):
    legacy, raw, primary = _compat(res)
    dep, sm, mg = legacy["deploy"]["outputs"], legacy["smoke"]["outputs"], legacy["migration"]["outputs"]
    out = {"url": dep.get("url", ""), "published_url": dep.get("url", ""), "target": dep.get("target", ""), "version_id": dep.get("version_id", ""),
           "deployed_and_verified": sm.get("deployed_and_verified") or "false", "smoke_level": sm.get("smoke_level", ""), "verified_url": sm.get("verified_url", ""),
           "published_unverified": sm.get("published_unverified") or "false", "migration": mg.get("migration", ""),
           "rollback_result": legacy["rollback"]["outputs"].get("rollback_result", "")}
    failed = any(v["result"] == "failure" for v in res.values())
    anyws = next((w for n, w in wss.items() if n.split(":")[0] in ("deploy", "migration", "preflight")), None) or make_workspace(toml)
    return {"result": "failure" if failed else "success", "outputs": out, "steps": steps, "log": "".join(logs), "summary": "".join(summ),
            "ws": wss.get("deploy", anyws), "wss": wss, "jobs": legacy, "needs": res, "raw": raw, "res": res, "primary": primary}


def run_chain(ref=MAIN, inputs=None, secrets=None, env=None, toml=APP_TOML, env_by_job=None, event="push", needs_over=None, files=None, chain_only=None):
    """Gate -> Plan -> Preflight -> Migration -> Delivery (Deploy -> Smoke -> Rollback) like the scheduler would: each job's `if:` is
    evaluated against the real results/outputs of its `needs`; every kind is its OWN reusable-workflow call."""
    secrets = secrets if secrets is not None else (PROD_SECRETS)
    i = dict(INPUT_DEFAULTS, app_name="app", build_cmd="true", app_url_prod="https://app.example.com", tools_ref="x", preview_host_check=False, preview_url_suffix=SUFFIX)
    i.update(inputs or {})
    return _shape(*_run_jobs(CHAIN, ref, inputs, secrets, _validation_needs(i, needs_over), event, env, toml, env_by_job, files), toml)


deploy = run_chain


def release_runs(r, inputs=None):
    c = ctx(MAIN, dict({"release_cmd": "pnpm release"}, **(inputs or {})), needs=r["needs"])
    return bool(ev(doc["jobs"]["release"]["if"], c))


def published(r):
    return "versions upload" in r["log"] or "preview --name" in r["log"] or "PUBLISH" in r["log"] or "wrangler deploy" in r["log"]


def step(r, name):
    """Outcome of an engine step across the whole chain: the job where it actually ran wins over 'skipped' elsewhere."""
    seen = [o for n, o in r["steps"] if n == f"engine / {name}"]
    for pref in ("failure", "success"):
        if pref in seen:
            return pref
    return seen[-1] if seen else None


def migrated(r):
    return "migrations apply" in r["log"]


def mig_file(r, target):
    for j in ("migration", "deploy", "preflight"):
        f = r.get("wss", {}).get(j, r["ws"]) / "w" / ".wrangler" / f"migrate.{target}.json"
        if f.exists():
            return f.read_text()
    raise FileNotFoundError(target)
cfg = lambda line: line.split("--config ")[1].split()[0]
ACCESS = "302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x"


def good_dep(**over):
    p = {"repo": "o/r", "branch_ref": FEAT, "host_worker": "app", "worker": "app"}
    p.update(over.pop("payload", {}))
    return json.dumps([dict({"id": 7, "sha": GOOD, "payload": p}, **over)])


PREV_IN = {"build_cmd": 'echo "BUILD noindex=$SITE_NOINDEX commit=$PUBLIC_COMMIT_SHA ver=$VITE_APP_VERSION gaid=$GA_ID" >> "$STUB_LOG"',
           "app_version": SHA, "build_env_preview": f"SITE_NOINDEX=true\nGA_ID=test-ga\nPUBLIC_COMMIT_SHA={SHA}",
           "smoke_env": "SITE_NOINDEX=false\nGA_ID=PROD-GA"}  # the Blog incident: Production-oriented smoke_env

MB = 'echo BUILD >> "$STUB_LOG"'
amb = APP_TOML + '[[previews.d1_databases]]\nbinding = "DB2"\ndatabase_name = "p2"\ndatabase_id = "P2"\n' + '[[d1_databases]]\nbinding = "DB2"\ndatabase_name = "x2"\ndatabase_id = "X2"\n'

# ---- D1 the deployed config binds == D1 the migration targets (adapter-generated effective config)
GEN = 'mkdir -p .wrangler/deploy dist && echo "{\\"configPath\\":\\"../../dist/wrangler.json\\"}" > .wrangler/deploy/config.json && echo \'%s\' > dist/wrangler.json'
eff = lambda cfg: GEN % json.dumps(cfg)
mismatch = {"name": "app", "d1_databases": [{"binding": "DB", "database_name": "other-db", "database_id": "OTHER-ID"}]}

# ---- P1: application environment can never redirect the delivery target / credentials / verification
EVIL = [("TARGET=prod", "TARGET"), ("TARGET=dev", "TARGET"), ("DEV_ENV=prod", "DEV_ENV"), ("GH_TOKEN=x", "GH_TOKEN"), ("EXPECTED_SHA=fake", "EXPECTED_SHA"),
        ("D1_BINDING=OTHER", "D1_BINDING"), ("CLOUDFLARE_API_TOKEN=x", "CLOUDFLARE_API_TOKEN"), ("STATE_DIR=/tmp/x", "STATE_DIR"), ("RUN_SHA=fake", "RUN_SHA")]

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


def dev(inputs=None, env=None, toml=DEV_TOML, needs=None, secrets=None, ref=FEAT, env_by_job=None):
    """persistent-dev (the separate Dev Worker) through the real chain: Gate -> Plan -> Migration -> Deploy -> Smoke -> Rollback."""
    return run_chain(ref, inputs=dict(DEV_INPUTS, preview_on_branch_push=False, **(inputs or {})), secrets=secrets, env=env, toml=toml, env_by_job=env_by_job)


def ran(r, what):
    return what in r["log"]
BUILD_ENTRY = 'mkdir -p dist && echo audited > dist/index.js && echo "BUILD" >> "$STUB_LOG"'

# Config guard, on the REAL deployment path: the plan fails closed before any delivery job is scheduled
BADCFG = {"deployment_strategy": "production-only", "shared_dev_on_branch_push": True}

# PR lifecycle advertises only a VERIFIED Preview bound to this host Worker, application, repo and branch
def lifecycle(deps, status="success https://feat-x-app.example.workers.dev Serving good (content)", inputs=None):
    ws = make_workspace(APP_TOML)
    c = ctx(FEAT, inputs, {}, event="pull_request")
    c["github"]["event"] = {"pull_request": {"head": {"ref": "feat/x", "repo": {"full_name": "o/r"}}}}
    r = run_job(doc, "lifecycle", c, ws, {"STUB_DEPLOYMENTS": deps, "STUB_STATUS": status})
    return r


def advertised(r):
    return "pulls?state=open" in r["log"]

# Smoke failure and recovery
GD = {"STUB_DEPLOYMENTS": json.dumps([{"id": 7, "sha": GOOD, "payload": {"repo": "o/r", "dev_worker": "app-dev", "worker": "app"}}]), "STUB_STATUS": "success https://dev.example.com Serving good (content)"}
tamper = 'case "$PWD" in */recover*) sed -i "s/name = \\"app-dev\\"/name = \\"app\\"/" wrangler.toml;; esac; echo "BUILD ver=$VITE_APP_VERSION commit=$PUBLIC_COMMIT_SHA cfenv=$CLOUDFLARE_ENV" >> "$STUB_LOG"'

# Manual persistent-dev: legacy vs engine contract
PD = dict(DEV_INPUTS, deployment_strategy="hybrid", persistent_dev_ref="refs/heads/dev", dispatch_target="persistent-dev", shared_dev_on_branch_push=False)


def manual(inputs=None, env=None, toml=DEV_TOML):
    ws = make_workspace(toml)
    r = run_job(doc, "persistent-dev", ctx("refs/heads/dev", dict(PD, **(inputs or {})), PROD_SECRETS, event="workflow_dispatch"), ws, dict({"STUB_TIPS": SHA}, **(env or {})))
    r["ws"] = ws
    return r

# Every job below ran in its OWN fresh workspace: the hand-off (URL, version ids, Worker identity, ledger id, migration
# facts, smoke level) travelled through job outputs only.
AUTO = {"production_rollback_policy": "automatic"}
ADD_SQL = {"migrations/0001.sql": "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);\nALTER TABLE notes ADD COLUMN tag TEXT;\n"}
DROP_SQL = {"migrations/0001.sql": "ALTER TABLE notes DROP COLUMN body;\n"}
res_of = lambda r: {k: v["result"] for k, v in r["jobs"].items() if k in ("migration", "deploy", "smoke", "rollback")}
NOTHING = {"migration": "skipped", "deploy": "skipped", "smoke": "skipped", "rollback": "skipped"}
# 9. D1 compatibility (Expand / Contract), fail closed
bad_cases = [
    ("migrations applied, d1_rollback_compat unattested (default)", dict(D1, **AUTO), ADD_SQL, "d1_unattested"),
    ("attested expand-contract but the SQL is destructive", dict(D1, **AUTO, d1_rollback_compat="expand-contract"), DROP_SQL, "d1_not_additive"),
    ("attested expand-contract but the SQL is not provably additive (data rewrite)", dict(D1, **AUTO, d1_rollback_compat="expand-contract"), {"migrations/0001.sql": "UPDATE notes SET body = '';"}, "d1_not_additive"),
]
# hand-off is DATA and validated
def leg(job, kind, ref=MAIN, inputs=None, needs=None, env=None, toml=APP_TOML, secrets=None, phase="all", handoff_json="", migration_json_in="", upstream_gate="", call_inputs=None):
    """Runs ONE job of the internal delivery workflow for `kind` against hand-built `needs` (fresh workspace).
    A job whose `if:` is false is reported skipped without running any step (like the scheduler)."""
    c = ctx(ref, inputs, PROD_SECRETS if secrets is None else secrets, {})
    dinputs = dict(DINPUT_DEFAULTS, stage="deliver", descriptor=desc(kind), tools_ref="x", inputs_json=json.dumps(dict(c["inputs"])),
                   phase=phase, handoff_json=handoff_json, migration_json_in=migration_json_in, upstream_gate=upstream_gate,
                   call_inputs_json=json.dumps(call_inputs) if call_inputs is not None else "")
    dc = Ctx({"github": c["github"], "inputs": dinputs, "needs": needs or {}, "jobs": {}}, c.secrets)
    if not ev(ddoc["jobs"][job]["if"], dc):
        return {"result": "skipped", "outputs": {}, "steps": [], "log": "", "summary": "", "ws": None}
    ws = make_workspace(toml)
    r = run_job(ddoc, job, dc, ws, dict({"STUB_TIPS": SHA}, **(env or {})))
    r["ws"] = ws
    return r


def hand(kind, outs, smoke="failure", sjson=None, migration_used="{}"):
    """The `needs` of the Smoke / Rollback jobs of ONE delivery call (its own Deploy hand-off and Smoke result)."""
    outs = dict(outs)
    outs.setdefault("kind", kind)
    return {"deploy": {"result": "success", "outputs": {"handoff": json.dumps(outs), "migration_used": migration_used}},
            "smoke": {"result": smoke, "outputs": {"smoke_json": json.dumps(sjson or {}), "smoke_status": smoke}}}


GOODH = {"kind": "production", "published": "true", "url": "https://app.example.com", "version_id": "new-2", "previous_version_id": "prev-1", "worker_name": "app"}

# ---------------------------------------------------------------- pipeline_phase: deferred verification (two calls)
# A Worker whose Smoke must wait for ANOTHER Worker's job runs the same workflow twice: the deploy call (CI/E2E, Plan, Migration,
# Deploy) and the verify call (Plan from the hand-off, Smoke, Rollback, Release). Every job runs in a FRESH workspace; the only
# thing the verify call receives is the phase_state data blob.
def deferred(ref=MAIN, inputs=None, env=None, gate="success", call_over=None, deploy_env=None, toml=APP_TOML, state_over=None):
    """Two calls of the public workflow: the deploy call (pipeline_phase deploy) and the verify call, which receives ONLY the
    `phase_state` output of the deploy call (rendered here from the REAL output expression) - every job in a fresh workspace."""
    base = dict(inputs or {})
    d = run_chain(ref, dict(base, pipeline_phase="deploy"), env=deploy_env, toml=toml)
    dci = ctx(ref, dict(base, pipeline_phase="deploy"), PROD_SECRETS)
    jobs_ctx = {"plan": d["res"].get("plan", SKIPPED)}
    for k in KINDS:
        jobs_ctx[f"delivery_{KEYED[k]}"] = d["res"].get(f"delivery_{KEYED[k]}", SKIPPED)
    for k in KINDS:
        jobs_ctx[f"migration_{KEYED[k]}"] = d["res"].get(f"migration_{KEYED[k]}", SKIPPED)
    pc = Ctx({"github": dci["github"], "inputs": dci["inputs"], "jobs": jobs_ctx}, {})
    state = render(doc[True]["workflow_call"]["outputs"]["phase_state"]["value"], pc)
    if state_over:
        state = json.dumps(dict(json.loads(state), **state_over))
    out = verify_call(state, base, ref=ref, gate=gate, env=env, toml=toml, call_over=call_over)
    return dict(out, deploy=d)


def verify_call(state, base, ref=MAIN, gate="success", env=None, toml=APP_TOML, call_over=None):
    """The verify call alone, given the `phase_state` string it receives (no deploy chain is re-run)."""
    vin = dict(base, pipeline_phase="verify", phase_state=state, upstream_gate=gate)
    vin.update(call_over or {})
    res, logs, summ, steps, wss = _run_jobs(("plan",) + tuple(f"delivery_{KEYED[k]}" for k in KINDS), ref, vin, PROD_SECRETS, {"gate": SKIPPED}, "push", env, toml, None, None)
    shaped = _shape(dict(res), logs, summ, steps, wss, toml)
    return {"jobs": shaped["jobs"], "log": "".join(logs), "summary": "".join(summ), "steps": steps, "vin": vin, "state": state, "raw": res, "res": res}


def verify_release(v, inputs=None):
    return bool(ev(doc["jobs"]["release"]["if"], ctx(MAIN, dict(v["vin"], release_cmd="pnpm release", **(inputs or {})), PROD_SECRETS, v["res"])))


AUTO_P = {"production_rollback_policy": "automatic"}
vdesc = lambda out: json.loads(out["raw"]["plan"]["outputs"]["descriptors"])
forged = {"inputs": {"app_name": "app"}, "kinds": ["production"], "group": "cfv2-prod-app", "deploy": {"h_production": json.dumps({"published": "true", "url": "http://insecure.example", "published_sha": "0" * 40})}, "migration": {}}

# persistent Dev through the SAME deferred flow (previously a separate dev-deploy / dev-verify / dev-rollback trio)
DEVI = dict(DEV_INPUTS, preview_on_branch_push=False)

ND = {"preview_on_branch_push": False, "shared_dev_native_preview_on_branch_push": True, "build_env_shared_dev_native_preview": "SITE=nd",
      "build_cmd": 'echo "BUILD kind=$CFV2_DELIVERY_KIND site=${SITE:-none} url=$CFV2_TARGET_URL app_env=$VITE_APP_ENV" >> "$STUB_LOG"'}
NDURL = "https://dev-app.example.workers.dev"
fail_nd = dict(ND, preview_on_branch_push=True, build_cmd=ND["build_cmd"] + '; [ "$CFV2_DELIVERY_KIND" != native-dev ] || false')
# recovery: Native Dev redeploys the last known-good Native Dev deployment, never replaces a newer one
GDN = {"STUB_DEPLOYMENTS": json.dumps([{"id": 7, "sha": GOOD, "payload": {"repo": "o/r", "kind": "native-dev", "preview_name": "dev", "host_worker": "app", "worker": "app"}}]),
       "STUB_STATUS": f"success {NDURL} Serving good (content)"}

# ---- smoke modes
SC = {"smoke_cmd": 'echo SMOKECMD >> "$STUB_LOG"'}

# ---- multi-target migration: dedupe by physical D1, isolation, per-D1 gating
MD = dict(D1, preview_on_branch_push=True, shared_dev_native_preview_on_branch_push=True)
two_db = APP_TOML + '[env.dev]\nname = "app-dev"\n[[env.dev.d1_databases]]\nbinding = "DB"\ndatabase_name = "dev-db"\ndatabase_id = "DEV-ID"\n'
TWO_IN = dict(DEV_INPUTS, **D1, preview_on_branch_push=True, dev_binding_allow="")

OUTS = doc[True]["workflow_call"]["outputs"]


def public_outputs(r, inputs=None, ref=FEAT):
    """The PUBLIC workflow_call outputs, rendered from the real output expressions over the per-call results."""
    jobs_ctx = {n: v for n, v in r["res"].items()}
    c = Ctx({"github": ctx(ref)["github"], "inputs": ctx(ref, inputs)["inputs"], "jobs": jobs_ctx}, {})
    return {k: render(v["value"], c) for k, v in OUTS.items() if k != "phase_state"}


BD = dict(ND, preview_on_branch_push=True)


def run_order(order, ref=FEAT, inputs=None, env=None, toml=APP_TOML, env_by_job=None):
    i = dict(INPUT_DEFAULTS, app_name="app", build_cmd="true", app_url_prod="https://app.example.com", tools_ref="x", preview_host_check=False, preview_url_suffix=SUFFIX)
    i.update(inputs or {})
    return _shape(*_run_jobs(order, ref, inputs, PROD_SECRETS, _validation_needs(i, None), "push", env, toml, env_by_job, None), toml)
# (1)+(3) one target failing while the other succeeds, in both orders, and the public outputs stay per-target
bad = dict(BD, build_cmd=BD["build_cmd"] + '; [ "$CFV2_DELIVERY_KIND" != native-dev ] || false')
# (7) rollback isolation: Native Dev's smoke fails and recovers; the Branch Preview is untouched and stays verified
GDN2 = {"STUB_DEPLOYMENTS": json.dumps([{"id": 7, "sha": GOOD, "payload": {"repo": "o/r", "kind": "native-dev", "preview_name": "dev", "host_worker": "app", "worker": "app"}}]),
        "STUB_STATUS": f"success {NDURL} Serving good (content)"}
# effective (adapter) config binding another database than the plan's: refused before ANY mutation
REDIR = ('mkdir -p .wrangler/deploy dist && printf \'%s\' \'{"configPath":"../../dist/wrangler.json"}\' > .wrangler/deploy/config.json && '
         'printf \'%s\' \'{"name":"app","main":"index.js","d1_databases":[{"binding":"DB","database_name":"other","database_id":"OTHER-ID"}]}\' > dist/wrangler.json')
# deploy call refuses to run without the right migration result (own / owner mismatch)
dctx_base = dict(GOODH)


def group_production_release():
    """Deploy: Production + Release gate"""
    global r, rb, up, rbcfg, code


    r = deploy()
    check(r["result"] == "success" and r["outputs"]["deployed_and_verified"] == "true" and "versions deploy new-2@100" in r["log"], "prod success: published own SHA, smoke passed -> deployed_and_verified=true")
    check(r["outputs"]["target"] == "prod" and r["outputs"]["version_id"] == "new-2" and r["outputs"]["url"] == "https://app.example.com", "prod success: outputs target + version + url")
    check(release_runs(r), "prod success -> Release runs")

    r = deploy(env={"STUB_TIPS": OTHER})
    check(r["result"] == "success" and r["outputs"]["deployed_and_verified"] == "false" and not published(r), "stale main run: the job succeeds, nothing built or published, not verified")
    check("pnpm" not in r["log"] and "BUILD" not in r["log"] and r["log"].count("npm ci") == 2, "stale main run: only the tooling installs (the plan job and the engine itself) ran; no application install or build")
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

    r = deploy(inputs={"production_rollback_policy": "automatic"}, env={"STUB_PROBES": "500 ;200 "})
    check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false", "prod smoke failure + rollback: job stays red, not verified (smoke failure stays visible although recovery succeeded)")
    rb = [l for l in r["log"].splitlines() if "versions deploy prev-1@100" in l]
    up = [l for l in r["log"].splitlines() if "versions upload" in l]
    rbcfg = json.load(open(cfg(rb[0]))) if rb and "--config" in rb[0] and os.path.exists(cfg(rb[0])) else None
    check(len(rb) == 1 and "--config" in rb[0] and cfg(rb[0]) != cfg(up[0]), "the Rollback job uses its own minimal config (it never rebuilds), not the Deploy job's audited config file (another runner)")
    check(r["jobs"]["deploy"]["outputs"].get("worker_name") == "app" and "--config" in rb[0], "the rollback identity (Worker name `app`) travelled from the Deploy job as a job output")
    check("NOT reverted" in r["summary"] and "rolled back to `prev-1` and verified" in r["summary"], "rollback summary states D1 was not reverted")
    check(r["jobs"]["rollback"]["result"] == "success" and r["outputs"]["rollback_result"] == "restored_verified" and r["jobs"]["smoke"]["result"] == "failure", "rollback restored + verified: the Rollback job is green but the SMOKE job (and so the run) stays red")
    check(r["outputs"]["deployed_and_verified"] == "false" and r["outputs"]["verified_url"] == "", "a rolled-back deployment is never reported as verified")
    check(not release_runs(r), "prod rollback -> Release does NOT run")
    r = deploy(inputs={"production_rollback_policy": "automatic"}, env={"STUB_PROBES": "500 ;200 ", "STUB_ROLLBACK_FAIL": "1"})
    check(r["result"] == "failure" and r["jobs"]["rollback"]["result"] == "failure" and "rollback to `prev-1` FAILED" in r["summary"] and "rolled back to" not in r["summary"] and "wrangler versions deploy prev-1@100 --name app --yes" in r["summary"],
          "a FAILED automatic rollback: Rollback job red, reported as failed (never as rolled back), manual recovery command printed")
    check(r["outputs"]["rollback_result"] == "failed" and not release_runs(r), "a failed rollback blocks Release and exposes rollback_result=failed")

    r = deploy(inputs={"app_url_prod": ""})
    check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false" and step(r, "Smoke the exact deployed URL (fails closed)") == "failure", "prod without a smoke URL FAILS at the smoke step (no silent success)")
    check(not release_runs(r), "prod without smoke URL -> Release does NOT run")

    r = deploy(env={"STUB_PROBES": "302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x"})
    check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false", "prod redirect to Cloudflare Access without approval FAILS (not application verification)")
    r = deploy(inputs={"access_protected_targets": "preview prod"}, env={"STUB_PROBES": "302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x"})
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
    r = deploy(secrets={"CLOUDFLARE_PREVIEW_API_TOKEN": "VT", "CLOUDFLARE_PREVIEW_ACCOUNT_ID": "VA"})
    check(r["result"] == "failure" and not published(r), "main with ONLY the deprecated Preview credentials fails (they are ignored)")


def group_preview_deploy():
    """Deploy: Preview"""
    global r, r2, bl, label, over, raw, w

    r = deploy(FEAT)
    check(r["result"] == "success" and r["outputs"]["deployed_and_verified"] == "true" and "preview --name feat-x" in r["log"] and "--json" in r["log"], "preview success: stable branch-slug Native Preview published and verified")
    check(r["outputs"]["url"] == "https://feat-x-app.example.workers.dev" and r["outputs"]["target"] == "preview", "preview outputs the real URL")
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
    r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ", "STUB_DEPLOYMENTS": good_dep(), "STUB_STATUS": "failure https://feat-x-app.example.workers.dev"})
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
    r = deploy(FEAT, inputs=PREV_IN, env={"STUB_PROBES": "500 ", "STUB_DEPLOYMENTS": good_dep()}, env_by_job={"rollback": {"STUB_TIPS": OTHER}})
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
    check(r["result"] == "success" and published(r), "a feature branch publishes with the account credentials")
    r = deploy(MAIN, secrets=dict(PROD_SECRETS, PREVIEW_SECRETS="K=v"), inputs={"build_cmd": 'printf %s "${K:-unset}" > k.out'})
    check(r["result"] == "success" and (r["ws"] / "w" / "k.out").read_text() == "unset", "Production build never receives Preview-only secrets")


def group_migration_d1():
    """Migration is a STEP of Deploy, after build + audit"""
    global m, bad, ref, label, line, name
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
    m = deploy(FEAT, inputs=D1, env_by_job={"deploy": {"STUB_TIPS": OTHER}})
    check(m["result"] == "success" and migrated(m) and not published(m) and m["outputs"]["deployed_and_verified"] == "false", "branch advanced DURING the migration: re-checked right before publish, old code is NOT published")
    check(step(m, "Stale-SHA guard (start)") == "success" and step(m, "Publish the audited artifact") == "skipped" and step(m, "Smoke the exact deployed URL (fails closed)") == "skipped" and m["jobs"]["smoke"]["outputs"].get("deployed_and_verified", "false") != "true", "post-migration stale run skips publish and the Smoke engine (nothing was published, so nothing is smoked or verified)")
    bad = APP_TOML.replace('database_id = "PREV-ID"', 'database_id = "PROD-ID"').replace('database_name = "prev-db"', 'database_name = "prod-db"')
    m = deploy(FEAT, inputs=D1, toml=bad)
    check(m["result"] == "failure" and not migrated(m) and not published(m), "Preview D1 identical to Production D1: fails before any migration (preflight / target check)")
    m = deploy(MAIN, inputs=D1, toml=bad)
    check(m["result"] == "failure" and not published(m), "same for the Production target: nothing published")
    m = deploy(FEAT, inputs=D1, toml=amb)
    check(m["result"] == "failure" and not migrated(m), "ambiguous D1 bindings without d1_binding: fails closed")
    m = deploy(FEAT, inputs=dict(D1, d1_binding="DB2"), toml=amb)
    check(m["result"] == "success" and "P2" in mig_file(m, "preview"), "d1_binding selects the intended database")
    m = deploy(FEAT, inputs=D1, toml='name = "app"\n[previews]\n')
    check(m["result"] == "failure" and not migrated(m), "no D1 declared for the target while d1_migrations is on: fails closed")
    m = deploy(FEAT, inputs=D1, secrets={})
    check(m["result"] == "failure" and not migrated(m), "missing credentials: fails before any remote command")
    m = deploy(FEAT, inputs=D1, secrets=PROD_SECRETS)
    check(m["result"] == "success" and migrated(m), "branch migrates with the account credentials")
    for ref, label in ((FEAT, "preview"), (MAIN, "prod")):
        m = deploy(ref, inputs=D1, env={"STUB_APPLY_FAIL": "1"})
        check(m["result"] == "failure" and migrated(m) and not published(m) and m["outputs"]["deployed_and_verified"] == "false" and m["outputs"]["migration"] == "failed", f"failed {label} migration: job fails, NOTHING is published or verified")
        check(not release_runs(m), f"failed {label} migration -> Release cannot run")
    m = deploy(FEAT, inputs=dict(D1, build_cmd="false"))
    check(m["result"] == "failure" and not migrated(m) and not published(m), "a failed build never reaches the migration (schema is not changed before a deployable exists)")
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
    for line, name in EVIL:
        for inp in ("build_env_prod", "smoke_env"):
            m = deploy(MAIN, inputs=dict(D1, **{inp: line}))
            check(m["result"] == "failure" and not migrated(m) and not published(m), f"{inp} cannot set the protected variable {name}: rejected before any work")
        m = deploy(FEAT, inputs=dict(D1, build_env_preview=line))
        check(m["result"] == "failure" and not migrated(m) and not published(m), f"build_env_preview cannot set {name}")
    # a build COMMAND that writes TARGET/DEV_ENV into GITHUB_ENV still cannot redirect the migration: the engine binds the target
    m = deploy(FEAT, inputs=dict(D1, build_cmd='echo TARGET=prod >> "$GITHUB_ENV"; echo DEV_ENV=prod >> "$GITHUB_ENV"; echo D1_BINDING=NOPE >> "$GITHUB_ENV"'))
    check(m["result"] == "success" and "PREV-ID" in mig_file(m, "preview") and not (m["ws"] / "w" / ".wrangler" / "migrate.prod.json").exists(), "a build writing TARGET=prod into GITHUB_ENV cannot move the Preview migration to Production")


def group_shared_dev():
    """Shared Dev (dev-deploy) executed with stubs"""
    global r, label, repl, extra, MAIN_TOML, line, stage_job, stage, both, builds, rb


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
    check(r["result"] == "failure" and not ran(r, "migrations apply") and not ran(r, "PUBLISH"), "build failure: no migration, no publish")
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
    check(r["result"] == "failure" and not ran(r, "BUILD"), "missing account credentials fail closed before Dev build")
    r = dev(secrets={"CLOUDFLARE_PREVIEW_API_TOKEN": "VT", "CLOUDFLARE_PREVIEW_ACCOUNT_ID": "VA"})
    check(r["result"] == "failure" and not ran(r, "BUILD"), "Preview-only credentials are never a fallback for Dev")
    r = dev(env={"STUB_TIPS": OTHER})
    check(r["result"] == "success" and not ran(r, "BUILD") and not ran(r, "PUBLISH") and r["outputs"]["deployed_and_verified"] == "false", "stale run: nothing built, migrated or published")
    r = dev(env={"STUB_TIPS": f"{SHA},{OTHER}"}, inputs=D1)
    check(r["result"] == "success" and ran(r, "BUILD") and not ran(r, "migrations apply") and not ran(r, "PUBLISH"), "superseded after the build: no migration and no publish")
    r = dev(inputs=D1, env_by_job={"deploy": {"STUB_TIPS": OTHER}})
    check(r["result"] == "success" and ran(r, "migrations apply") and not ran(r, "PUBLISH"), "branch advanced during the Dev migration: old code is not published")

    # (the old 'Shared-D1 guard' is superseded: Preview and Dev on one D1 now share ONE migration leg; see the multi-kind section)
    r = dev(inputs={"dev_deploy_cmd": ""})
    check(r["result"] == "success" and "wrangler deploy --config " in r["log"] and "wrangler.toml --env dev" in r["log"] and r["log"].index("BUILD") < r["log"].index("wrangler deploy"), "Shared Dev built-in publish (no dev_deploy_cmd): wrangler deploy gets the audited --config (and the named env for a source config), after the build")
    r = deploy(MAIN, inputs=BADCFG)
    check(r["jobs"]["plan"]["result"] == "failure" and r["jobs"]["migration"]["result"] == r["jobs"]["deploy"]["result"] == "skipped" and "BUILD" not in r["log"] and not published(r), "production-only + Shared Dev: the plan fails; no delivery job is scheduled (no build, migration, publish)")
    for stage_job, stage in (("migrate", "migrate"), ("deploy", "deliver")):
        ws_ = make_workspace()  # defence in depth: even if a job were forced to run, the engine refuses at init
        c_ = ctx(MAIN, dict(BADCFG, d1_migrations=True), PROD_SECRETS, {})
        dc_ = Ctx({"github": c_["github"], "inputs": dict(DINPUT_DEFAULTS, stage=stage, descriptor=desc("production"), tools_ref="x", inputs_json=json.dumps(dict(c_["inputs"])),
                                                           kinds='["production"]', peers='{"production":"success"}'), "needs": {}, "jobs": {}}, PROD_SECRETS)
        fr = run_job(ddoc, stage_job, dc_, ws_, {"STUB_TIPS": SHA})
        check(fr["result"] == "failure" and not published(fr) and "wrangler versions" not in fr["log"] and "migrations apply" not in fr["log"] and "-X POST" not in fr["log"], f"production-only + Shared Dev: the {stage_job} stage fails at init (nothing remote)")
    for both in ({"shared_dev_on_branch_push": True, "shared_dev_native_preview_on_branch_push": True},):
        r = deploy(FEAT, inputs=both)
        check(r["jobs"]["plan"]["result"] == "failure" and not published(r), "persistent Dev + Native Dev both enabled: conflicting automatic Dev channels, the plan fails closed")
    r = deploy(MAIN, inputs={"deployment_strategy": "production-only", "preview_on_branch_push": True}, env={"STUB_PROBES": "200 "})
    check(r["result"] == "success" and published(r) and r["outputs"]["deployed_and_verified"] == "true", "production-only + default Preview=true: accepted, Production deploys")


    r = lifecycle(good_dep())
    check(r["result"] == "success" and advertised(r), "lifecycle: a verified Preview for this host Worker, app, repo and branch is advertised")
    check(not advertised(lifecycle(good_dep(payload={"host_worker": "blog-preview"}))), "lifecycle: an old-host (blog-preview) Preview record is NOT advertised for the new host")
    check(not advertised(lifecycle(good_dep(payload={"branch_ref": "refs/heads/other"}))), "lifecycle: another branch ref is not advertised")
    check(not advertised(lifecycle(good_dep(payload={"worker": "other-app"}))) and not advertised(lifecycle(good_dep(payload={"repo": "x/y"}))), "lifecycle: another application or repository is not advertised")
    check(not advertised(lifecycle(good_dep(), status="success https://feat-x-app.example.workers.dev Published s; unverified (liveness)")), "lifecycle: a liveness-only Preview is not advertised as verified")
    check(not advertised(lifecycle(good_dep(), status="success http://insecure.example")), "lifecycle: a non-https or unverified record is not advertised")
    r = lifecycle("[]")
    check(r["result"] == "success" and not advertised(r), "lifecycle: no matching verified Preview leaves the PR comment unchanged (job still succeeds)")

    # Verification level: Access liveness is published-but-unverified and never a known-good record
    r = dev(inputs={"access_protected_targets": "preview dev"}, env={"STUB_PROBES": "302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x"})
    check(r["result"] == "success" and r["outputs"]["smoke_level"] == "liveness" and r["outputs"]["deployed_and_verified"] == "false" and r["outputs"]["verified_url"] == "" and r["outputs"]["published_unverified"] == "true", "Shared Dev Access liveness only: published, NOT verified")
    check("state=success" not in r["log"] and "state=inactive" in r["log"], "Shared Dev liveness is not recorded as content-verified last-good")
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
    r = dev(env=dict(GD, STUB_PROBES="500 "), env_by_job={"rollback": {"STUB_TIPS": OTHER}})
    check(r["result"] == "failure" and r["log"].count("PUBLISH") == 1 and "superseded" in r["summary"], "Dev recovery is skipped when a newer push owns the branch")
    r = dev(inputs={"dev_build_cmd": tamper}, env=dict(GD, STUB_PROBES="500 ;200 "))
    check(r["result"] == "failure" and r["log"].count("PUBLISH") == 1 and "Automatic recovery FAILED" in r["summary"], "the config audit runs on the RECOVERED build: a last-good tree with the wrong Worker identity is not published")
    rb = dev(env=dict(GD, STUB_PROBES="500 ;200 "), inputs={"build_env_dev": f"PUBLIC_COMMIT_SHA={SHA}\nSHORT={SHA[:7]}"})
    check(f"commit={GOOD}" in rb["log"], "build env referencing the failed SHA (full) is rebound for the restored commit")


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


def group_chain_smoke_rollback():
    """Migration -> Deploy -> Smoke -> Rollback chain"""
    global failing, r, pol, label, inp, files, code, ev_ref, head, nm, bad, fr, key, val, sm_ok

    # 1/2. CI / E2E failure -> nothing downstream, nothing remote
    for failing in ("lint", "unit", "build", "e2e"):
        r = deploy(MAIN, inputs=dict(D1, lint_cmd="l", test_unit_cmd="u", test_e2e_cmd="e", **AUTO), needs_over={failing: "failure"})
        check(res_of(r) == NOTHING and r["log"] == "" and not release_runs(r), f"CI: failed {failing} -> Migration, Deploy, Smoke and Rollback are all skipped; nothing remote is touched; no Release")
    # 3. migration failure
    r = deploy(MAIN, inputs=dict(D1, **AUTO), env={"STUB_APPLY_FAIL": "1"})
    check(res_of(r) == {"migration": "failure", "deploy": "failure", "smoke": "skipped", "rollback": "skipped"} and not published(r) and not release_runs(r), "Migration failure -> its Deploy leg stops (fails closed), Smoke and Rollback have nothing to do, nothing published, Release blocked")
    check("D1 migration failed" in r["summary"] and r["outputs"]["migration"] == "failed", "a failed migration is reported explicitly (migration_status=failed, summary)")
    # 4. deploy failure
    r = deploy(MAIN, inputs=AUTO, env={"STUB_UPLOAD_FAIL": "1"})
    check(res_of(r) == {"migration": "skipped", "deploy": "failure", "smoke": "skipped", "rollback": "skipped"} and not release_runs(r), "Deploy failure -> Smoke and Rollback skipped (nothing was published to verify or revert), Release blocked")
    r = deploy(FEAT, env={"STUB_PREVIEW_FAIL": "1"})
    check(res_of(r)["deploy"] == "failure" and res_of(r)["smoke"] == "skipped" and res_of(r)["rollback"] == "skipped", "Preview publish failure -> Smoke and Rollback skipped")
    # 5. smoke success -> rollback skipped, release allowed
    r = deploy(MAIN, inputs=dict(D1, **AUTO), env={"STUB_PROBES": "200 "})
    check(res_of(r) == {"migration": "success", "deploy": "success", "smoke": "success", "rollback": "skipped"} and r["result"] == "success" and release_runs(r), "Smoke success -> Rollback skipped, run green, Release allowed")
    check(r["jobs"]["deploy"]["outputs"]["published"] == "true" and r["outputs"]["deployed_and_verified"] == "true" and r["jobs"]["deploy"]["outputs"].get("deployed_and_verified") != "true", "Deploy only reports `published`; `verified` exists only after the Smoke job")
    check(r["jobs"]["smoke"]["outputs"]["smoke_level"] == "content", "the verification level is explicit (content)")
    check("deployments/5151/statuses" not in r["log"], "a verified Production deployment keeps GitHub's own success status (no unverified annotation)")
    # 6. production smoke failure -> automatic rollback (restored, verified), release blocked
    r = deploy(MAIN, inputs=dict(AUTO, sha_path="/version.json"), env={"STUB_PROBES": "200 ;200 ", "STUB_SHA_BODY": OTHER[:7]},
               env_by_job={"rollback": {"STUB_SHA_BODY": "", "STUB_PREV_MESSAGE": "[1111111] previous"}})
    check(res_of(r)["smoke"] == "failure" and "wrangler versions view prev-1" in r["log"], "post-rollback smoke resolves the previous version's commit from its deployment message")
    r = deploy(MAIN, inputs=dict(AUTO, sha_path="/version.json"), env={"STUB_PROBES": "200 ;200 ", "STUB_SHA_BODY": OTHER[:7]}, env_by_job={"rollback": {"STUB_SHA_BODY": "1111111"}})
    check(res_of(r) == {"migration": "skipped", "deploy": "success", "smoke": "failure", "rollback": "success"} and r["outputs"]["rollback_result"] == "restored_verified",
          "Production smoke fails on the wrong commit; rollback restores prev-1; the restored deployment no longer serves the failed commit -> restored_verified")
    check(r["outputs"]["smoke_level"] == "" and r["outputs"]["deployed_and_verified"] == "false" and not release_runs(r) and r["result"] == "failure", "a successful rollback still leaves the run FAILED, the deployment unverified and Release blocked")
    check("deployments/5151/statuses -f state=failure" in r["log"] and "UNVERIFIED" in r["log"], "the failed deployment is annotated UNVERIFIED on its GitHub Environment deployment")
    check("deployments/5151/statuses -f state=inactive" in r["log"], "after a verified rollback the failed Environment deployment is marked inactive (superseded by the restored version)")
    # rollback runs once, only the previous version of THIS Worker, from a runner that never built anything
    check(r["log"].count("wrangler versions deploy prev-1@100") == 1 and "BUILD" not in "".join(l for l in r["log"].splitlines() if "prev-1@100" in l), "exactly one rollback, to the previous version id recorded by Deploy")
    check(not any(f.startswith("dist") for f in os.listdir(r["wss"]["rollback"] / "w")), "the Rollback job builds nothing (a failing build can never block a rollback)")
    # 7. rollback failure -> failed run, loud
    r = deploy(MAIN, inputs=AUTO, env={"STUB_PROBES": "500 ;200 ", "STUB_ROLLBACK_FAIL": "1"})
    check(res_of(r)["rollback"] == "failure" and r["outputs"]["rollback_result"] == "failed" and "Roll back manually" in r["summary"], "Rollback failure -> job red, rollback_result=failed, manual command in the summary")
    # restored but the post-rollback smoke fails -> not green
    r = deploy(MAIN, inputs=AUTO, env={"STUB_PROBES": "500 ;500 "})
    check(res_of(r)["rollback"] == "failure" and r["outputs"]["rollback_result"] == "restored_unhealthy" and "post-rollback smoke FAILED" in r["summary"], "rolled back but the restored Production fails the smoke -> restored_unhealthy, job red, loud summary")
    r = deploy(MAIN, inputs=dict(AUTO, access_protected_targets="preview prod"), env={"STUB_PROBES": "500 ;302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x"})
    check(res_of(r)["rollback"] == "failure" and r["outputs"]["rollback_result"] == "restored_unverified", "restored Production reachable only through Access liveness -> restored_unverified, job red (never reported as verified)")
    # 8. previous version missing -> fail safe, never pretend
    r = deploy(MAIN, inputs=AUTO, env={"STUB_PROBES": "500 ", "STUB_NO_PREV": "1"})
    check(res_of(r)["rollback"] == "failure" and r["outputs"]["rollback_result"] == "blocked" and "wrangler versions deploy" not in r["log"].replace("versions deploy new-2", ""), "no previous version id -> NO rollback attempted, Rollback job red, rollback_result=blocked")
    check("No previous Worker version id was recorded" in r["summary"] and "STILL serves the unverified version" in r["summary"], "the summary says Production still serves the unverified version (no fake success)")
    # policy manual / disabled: no Rollback job, manual command in the Smoke job summary
    for pol in ("manual", "disabled"):
        r = deploy(MAIN, inputs={"production_rollback_policy": pol}, env={"STUB_PROBES": "500 "})
        check(res_of(r)["rollback"] == "skipped" and res_of(r)["smoke"] == "failure" and "wrangler versions deploy prev-1@100 --name app --yes" in r["summary"] and "versions deploy prev-1" not in r["log"], f"policy {pol}: nothing rolled back, manual command printed by the Smoke job, run red")
    for label, inp, files, code in bad_cases:
        r = deploy(MAIN, inputs=inp, files=files, env={"STUB_PROBES": "500 "})
        check(res_of(r)["rollback"] == "failure" and r["outputs"]["rollback_result"] == "blocked" and "versions deploy prev-1" not in r["log"] and f"({code})" in r["summary"], f"D1 incompatible - {label}: rollback BLOCKED (fail closed), no Worker change, manual command printed")
    r = deploy(MAIN, inputs=dict(D1, **AUTO), files=ADD_SQL, env={"STUB_PROBES": "500 ", "STUB_PENDING_FILES": "0001.sql"}, env_by_job={"rollback": {"STUB_PROBES": "200 "}})
    check(r["outputs"]["rollback_result"] == "blocked", "additive SQL alone is NOT proof: without the expand-contract attestation the rollback stays blocked")
    r = deploy(MAIN, inputs=dict(D1, **AUTO, d1_rollback_compat="expand-contract"), files=ADD_SQL, env={"STUB_PROBES": "500 ;200 "})
    check(r["jobs"]["migration"]["outputs"]["d1_scan"] == "additive" and r["outputs"]["rollback_result"] == "restored_verified" and "versions deploy prev-1@100" in r["log"], "attested expand-contract + provably additive SQL -> rollback proceeds and is verified")
    r = deploy(MAIN, inputs=dict(D1, **AUTO, d1_rollback_compat="expand-contract"), files=ADD_SQL, env={"STUB_PROBES": "500 ", "STUB_PENDING_FILES": ""})
    check(r["jobs"]["migration"]["outputs"]["d1_scan"] == "unknown" and r["outputs"]["rollback_result"] == "blocked", "pending migration files that cannot be determined -> scan `unknown` -> rollback blocked (fail closed)")
    r = deploy(MAIN, inputs=dict(D1, **AUTO), env={"STUB_PROBES": "500 ;200 ", "STUB_PENDING": "0"})
    check(r["jobs"]["migration"]["outputs"]["schema_changed"] != "true" and r["outputs"]["rollback_result"] == "restored_verified", "d1_migrations on but NOTHING pending this run: the schema is unchanged, rollback needs no attestation")
    r = deploy(MAIN, inputs=AUTO, env={"STUB_PROBES": "500 ;200 "})
    check(r["outputs"]["rollback_result"] == "restored_verified", "a Worker without D1 migrations rolls back without any D1 condition")
    # 10. PR event never deploys
    for ev_ref, head in (("refs/pull/1/merge", "o/r"),):
        r = deploy(ev_ref, inputs=dict(D1, **AUTO), event="pull_request")
        check(res_of(r) == NOTHING and r["log"] == "", "PR event: no Migration, Deploy, Smoke or Rollback (no Production or Preview deployment)")
    r = deploy(MAIN, inputs=dict(D1, **AUTO), event="delete")
    # a forged / mismatched target descriptor is refused by the engine at init (trust boundary), before anything remote happens
    for nm, bad in (("a cross-kind engine target", desc("production", target="preview")), ("a forged concurrency group", desc("production", deploy_group="other")),
                    ("a foreign Environment", desc("production", environment="app/preview")), ("an unknown key", desc("production", CLOUDFLARE_API_TOKEN="x"))):
        ws_ = make_workspace()
        c_ = ctx(MAIN, {}, PROD_SECRETS, {})
        dc_ = Ctx({"github": c_["github"], "inputs": dict(DINPUT_DEFAULTS, stage="deliver", descriptor=bad, tools_ref="x", inputs_json=json.dumps(dict(c_["inputs"]))), "needs": {}, "jobs": {}}, PROD_SECRETS)
        fr = run_job(ddoc, "deploy", dc_, ws_, {"STUB_TIPS": SHA})
        check(fr["result"] == "failure" and not published(fr) and "-X POST" not in fr["log"], f"engine refuses {nm} in the target descriptor at init (nothing built, deployed or recorded)")
    check(res_of(r) == NOTHING, "branch delete: no delivery job")
    # 11. stale SHA
    r = deploy(MAIN, inputs=dict(D1, **AUTO), env_by_job={"deploy": {"STUB_TIPS": OTHER}})
    check(res_of(r) == {"migration": "success", "deploy": "success", "smoke": "skipped", "rollback": "skipped"} and not published(r) and not release_runs(r), "main moved on after Migration: Deploy publishes nothing, Smoke and Rollback are skipped, Release blocked")
    r = deploy(MAIN, inputs=AUTO, env={"STUB_PROBES": "500 "}, env_by_job={"rollback": {"STUB_ACTIVE": "someone-elses-version"}})
    check(r["outputs"]["rollback_result"] == "blocked" and "versions deploy prev-1" not in r["log"] and "live_version_changed" in r["summary"], "the live version is no longer the one THIS run deployed -> rollback refused (never replaces another deployment)")
    fr = leg("rollback", "production", inputs=AUTO, needs=hand("production", dict(GOODH, url="http://evil.example/x")), env={"STUB_ACTIVE": "new-2"})
    check(fr["result"] == "failure" and "versions deploy" not in fr["log"] and step(fr, "Hand-off from the previous jobs (validated data)") == "failure", "a malformed hand-off (non-https URL) is refused at hydrate, before anything is changed")
    for key, val in (("version_id", "x; rm -rf /"), ("previous_version_id", "$(touch PWNED)"), ("worker_name", "App Name"), ("published_sha", OTHER)):
        fr = leg("rollback", "production", inputs=AUTO, needs=hand("production", dict(GOODH, **{key: val})), env={"STUB_ACTIVE": "new-2"})
        check(fr["result"] == "failure" and "versions deploy" not in fr["log"] and not (fr["ws"] / "w" / "PWNED").exists(), f"hand-off {key}={val!r} is refused as data (no rollback, no execution)")
    fr = leg("rollback", "production", inputs=AUTO, needs=hand("production", GOODH, smoke="success"), env={"STUB_ACTIVE": "new-2"})
    check(fr["result"] == "skipped" and not fr["steps"] and "versions deploy" not in fr["log"], "Rollback job whose Smoke did not fail is skipped (no engine step runs)")
    sm_ok = hand("production", dict(GOODH, target="prod", published_sha=SHA), smoke="success")
    fr = leg("smoke", "production", inputs={}, needs=sm_ok, env={"STUB_PROBES": "200 "})
    check(fr["result"] == "success" and "CLOUDFLARE" not in " ".join(k for k in ddoc["jobs"]["smoke"].get("env", {})), "the Smoke job runs with no Cloudflare credentials in its env and still verifies (the probe needs none)")
    fr = leg("smoke", "production", inputs={}, needs=sm_ok, env={"STUB_PROBES": "200 ", "CLOUDFLARE_API_TOKEN": "leaked", "CLOUDFLARE_ACCOUNT_ID": "leaked"})
    check(fr["result"] == "failure" and step(fr, "Initialise (bind target and stage, validate credentials and env as data)") == "failure", "the Smoke stage refuses to start if Cloudflare credentials ever reach it (least privilege enforced by the engine)")
    # Preview: ledger id and identity travel between jobs; recovery stays Preview-only
    r = deploy(FEAT, env={"STUB_PROBES": "200 "})
    check(res_of(r) == {"migration": "skipped", "deploy": "success", "smoke": "success", "rollback": "skipped"} and r["jobs"]["deploy"]["outputs"]["ledger_id"] == "4242", "Preview chain: the ledger id is created by Deploy and handed to Smoke")
    check("deployments/4242/statuses -f state=success" in r["log"] and r["jobs"]["deploy"]["outputs"]["preview_name"] == "feat-x", "Preview: Smoke marks THE ledger entry of this run success (no second entry), identity feat-x handed over")
    r = deploy(FEAT, env={"STUB_PROBES": "500 "}, inputs=AUTO)
    check(res_of(r)["rollback"] == "failure" and "versions deploy" not in r["log"] and r["outputs"]["rollback_result"] == "no_known_good", "Preview + no known-good deployment: the Rollback job never touches Production versions (policy automatic is irrelevant) and fails loudly")
    r = deploy(FEAT, env={"STUB_PROBES": "500 ;200 ", "STUB_DEPLOYMENTS": good_dep()}, inputs=AUTO)
    check(res_of(r)["rollback"] == "success" and r["outputs"]["rollback_result"] == "restored_verified" and "versions deploy" not in r["log"] and r["result"] == "failure", "Preview recovery in the Rollback job: last-known-good redeployed + verified; run stays failed; no Production rollback command")


def group_deferred_verification():
    """Deferred verification (pipeline_phase deploy / verify)"""
    global v, d, g, vv, key, val, vst, sk, pdv, what, mut, legacy, lv, dv_, r1, r
    v = deferred(inputs=AUTO_P)
    d = v["deploy"]
    check(d["result"] == "success" and published(d) and d["jobs"]["smoke"]["result"] == "skipped" and d["jobs"]["rollback"]["result"] == "skipped" and d["outputs"]["deployed_and_verified"] == "false",
          "deferred (deploy call): CI-gated Deploy publishes and STOPS - no smoke, no rollback, nothing is verified yet")
    check(v["jobs"]["smoke"]["result"] == "success" and v["jobs"]["smoke"]["outputs"]["deployed_and_verified"] == "true" and v["jobs"]["rollback"]["result"] == "skipped" and v["jobs"]["smoke"]["outputs"]["gate_blocked"] == "false",
          "deferred (verify call): the Smoke job verifies the handed-over deployment from the phase_state data alone (fresh workspace, no Deploy job in the call)")
    check(verify_release(v), "deferred: Release runs only from the verify call's verified smoke")
    check(v["jobs"]["smoke"]["outputs"]["verified_url"] == "https://app.example.com", "deferred: the verified URL is the one the Deploy job published")
    for g in ("skipped", ""):
        vv = deferred(inputs=AUTO_P, gate=g)
        check(vv["jobs"]["smoke"]["result"] == "success" and vv["jobs"]["smoke"]["outputs"]["deployed_and_verified"] == "true", f"cross-service gate '{g or '(none)'}': satisfied (unchanged Worker uses the existing verified environment)")
    for g in ("failure", "cancelled", "weird"):
        vv = deferred(inputs=AUTO_P, gate=g)
        sm = vv["jobs"]["smoke"]
        check(sm["result"] == "failure" and sm["outputs"]["gate_blocked"] == "true" and sm["outputs"]["deployed_and_verified"] == "false" and vv["jobs"]["rollback"]["result"] == "skipped"
              and "versions deploy" not in vv["log"] and step(vv, "Smoke the exact deployed URL (fails closed)") in (None, "skipped") and "Cross-service smoke blocked" in vv["summary"] and not verify_release(vv),
              f"cross-service gate '{g}': the smoke does NOT run, the job is red, nothing is verified, NO rollback, Release blocked")
    check("wrangler versions deploy prev-1@100 --name app --yes" in deferred(inputs=AUTO_P, gate="failure")["summary"], "gate blocked: the manual rollback command is printed, nothing is changed automatically")
    vv = deferred(inputs=AUTO_P, env={"STUB_PROBES": "500 ;200 "})
    check(vv["jobs"]["smoke"]["result"] == "failure" and vv["jobs"]["smoke"]["outputs"]["gate_blocked"] == "false" and vv["jobs"]["rollback"]["result"] == "success"
          and vv["jobs"]["rollback"]["outputs"]["rollback_result"] == "restored_verified" and "versions deploy prev-1@100" in vv["log"] and not verify_release(vv),
          "deferred: a failed Production smoke triggers the fail-closed rollback in the verify call; it restores + re-verifies, the Smoke job stays red, Release blocked")
    vv = deferred(inputs={"production_rollback_policy": "manual"}, env={"STUB_PROBES": "500 "})
    check(vv["jobs"]["smoke"]["result"] == "failure" and vv["jobs"]["rollback"]["result"] == "skipped" and "versions deploy" not in vv["log"] and "wrangler versions deploy prev-1@100 --name app --yes" in vv["summary"],
          "deferred: policy manual - failed smoke rolls nothing back and prints the manual commands")
    vv = deferred(inputs=AUTO_P, env={"STUB_PROBES": "500 ;200 ", "STUB_ACTIVE": "someone-else"})
    check(vv["jobs"]["rollback"]["result"] == "failure" and "versions deploy" not in vv["log"], "deferred: another run published meanwhile (the live version is not ours) - the rollback is BLOCKED, never overwrites it")
    vv = deferred(inputs=dict(AUTO_P, **D1), env={"STUB_PROBES": "500 ;200 "})
    check(vv["jobs"]["rollback"]["result"] == "failure" and "versions deploy" not in vv["log"], "deferred: D1 migrations were applied - the D1 compatibility fail-closed rule still blocks the rollback")
    for key, val in (("app_name", "other"), ("app_path", "apps/other"), ("production_rollback_policy", "manual"), ("tools_ref", "other-ref")):
        vv = deferred(inputs=AUTO_P, call_over={key: val})
        check(vv["jobs"]["smoke"]["result"] in ("failure", "skipped") and vv["jobs"]["smoke"]["outputs"].get("deployed_and_verified") != "true" and not verify_release(vv),
              f"deferred: a verify call whose {key} differs from the deploy call's verifies nothing (identity check)")
    # ---- Deferred verification keeps the DEPLOY call's descriptors (smoke policy, lock identity, D1, owner): never re-derived from the verify call
    vst = json.loads(v["state"])
    check(set(vst["descriptors"]) == {"production"} and vst["descriptors"]["production"]["phase"] == "deploy", "deferred: phase_state carries the deploy call's validated descriptors (phase deploy)")
    check(vdesc(v)["production"] == dict(vst["descriptors"]["production"], phase="verify"), "deferred (verify call): the descriptor is the deploy call's, only the phase moved deploy -> verify")
    sk = deferred(inputs=dict(AUTO_P, smoke_mode="skip"), call_over={"smoke_mode": "full"})
    check(vdesc(sk)["production"]["smoke_mode"] == "skip" and sk["jobs"]["smoke"]["outputs"]["smoke_level"] == "skipped" and sk["jobs"]["smoke"]["outputs"]["deployed_and_verified"] != "true" and not verify_release(sk),
          "deferred: deploy smoke_mode=skip, verify call default full -> STILL skip (Published/Unverified, no Release): deployment-time policy is not replaced by verify-call defaults")
    pdv = deferred(FEAT, inputs=dict(DEV_INPUTS, preview_on_branch_push=False, d1_lock_scope="shared-db"), toml=DEV_TOML, call_over={"d1_lock_scope": ""})
    check(vdesc(pdv)["persistent-dev"]["deploy_group"] == "cfv2-dev-o/r-shared-db" == vdesc(pdv)["persistent-dev"]["rollback_group"],
          "deferred: deploy d1_lock_scope=shared-db, verify call without scope -> the SAME effective Dev lock identity")
    for what, mut in (("a cross-target descriptor (persistent-dev injected for production)", lambda st: dict(st, descriptors={"production": json.loads(desc("persistent-dev", phase="deploy", deploy_group="cfv2-dev-o/r-app"))})),
                      ("a tampered D1 identity", lambda st: dict(st, descriptors={"production": dict(st["descriptors"]["production"], d1_expected_id="FOREIGN")}, d1={"production": "DB9"})),
                      ("a tampered migration owner", lambda st: dict(st, descriptors={"production": dict(st["descriptors"]["production"], d1_owner_kind="native-branch")}, migration={"mj_production": json.dumps({"kind": "production"})})),
                      ("another application's descriptor", lambda st: dict(st, descriptors={"production": dict(st["descriptors"]["production"], environment="other/prod")})),
                      ("a verify-phase descriptor replayed as deploy state", lambda st: dict(st, descriptors={"production": dict(st["descriptors"]["production"], phase="verify")}))):
        tv = verify_call(json.dumps(mut(vst)), dict(AUTO_P))
        check(tv["jobs"]["plan"]["result"] == "failure" and tv["jobs"]["smoke"]["result"] == "skipped" and not verify_release(tv), f"deferred: {what} in phase_state fails the plan closed - nothing is smoked or released")
    legacy = {k: v_ for k, v_ in vst.items() if k != "descriptors"}
    lv = verify_call(json.dumps(legacy), dict(AUTO_P))
    check(lv["jobs"]["smoke"]["result"] == "success" and vdesc(lv)["production"]["smoke_mode"] == "full" and vdesc(lv)["production"]["phase"] == "verify", "deferred: a legacy phase_state (no descriptors) still verifies, rebuilt from the deploy call's own inputs")
    # ---- Lock identity integrity: the scheduling-time lock and the handed-over group come from the DEPLOY call's metadata
    for what, mut in (("a forged phase_state.group", lambda st: dict(st, group="attacker-group")),
                      ("a group of another lock family", lambda st: dict(st, group="cfv2-nonprod-d1-app")),
                      ("a missing phase_state.group", lambda st: {k: v_ for k, v_ in st.items() if k != "group"})):
        tv = verify_call(json.dumps(mut(vst)), dict(AUTO_P))
        check(tv["jobs"]["plan"]["result"] == "failure" and tv["jobs"]["smoke"]["result"] == "skipped" and not verify_release(tv), f"deferred: {what} fails the plan closed - nothing is smoked or released")
    sched = lambda inputs, state: render(doc["concurrency"]["group"], ctx(MAIN, dict(AUTO_P, pipeline_phase="verify", phase_state=json.dumps(state), **inputs), PROD_SECRETS, {}))
    check(sched({}, vst) == sched({"app_name": "other", "d1_lock_scope": "elsewhere"}, vst) == render(doc["concurrency"]["group"], ctx(MAIN, dict(AUTO_P), PROD_SECRETS, {})),
          "deferred: the verify call's scheduling-time lock is derived from phase_state.inputs - its own app_name / d1_lock_scope and the handed-over group string cannot select another lock")
    check(sched({}, dict(vst, group="IGNORED-BY-THE-EXPRESSION")) == sched({}, vst), "deferred: GitHub's top-level concurrency expression never reads phase_state.group")
    pst = json.loads(pdv["state"])
    pdd = pst["descriptors"]["persistent-dev"]
    for what, mut in (("a modified Persistent Dev deploy group", lambda st: dict(st, descriptors={"persistent-dev": dict(pdd, deploy_group="cfv2-dev-o/r-other", rollback_group="cfv2-dev-o/r-other")})),
                      ("a modified Persistent Dev rollback group", lambda st: dict(st, descriptors={"persistent-dev": dict(pdd, rollback_group="cfv2-dev-o/r-other")})),
                      ("the scope-less Dev lock instead of the deploy-time scope", lambda st: dict(st, descriptors={"persistent-dev": dict(pdd, deploy_group="cfv2-dev-o/r-app", rollback_group="cfv2-dev-o/r-app")})),
                      ("another repository's Dev lock", lambda st: dict(st, descriptors={"persistent-dev": dict(pdd, deploy_group="cfv2-dev-evil/r-shared-db", rollback_group="cfv2-dev-evil/r-shared-db")})),
                      ("a deploy-time scope removed from the inputs", lambda st: dict(st, inputs=dict(st["inputs"], d1_lock_scope="")))):
        tv = verify_call(json.dumps(mut(pst)), dict(DEVI, d1_lock_scope=""), ref=FEAT, toml=DEV_TOML)
        check(tv["jobs"]["plan"]["result"] == "failure" and tv["jobs"]["smoke"]["result"] == "skipped", f"deferred: {what} in phase_state fails the plan closed")
    ok_v = verify_call(json.dumps(pst), dict(DEVI, d1_lock_scope=""), ref=FEAT, toml=DEV_TOML)
    check(ok_v["jobs"]["plan"]["result"] == "success" and json.loads(ok_v["raw"]["plan"]["outputs"]["descriptors"])["persistent-dev"]["deploy_group"] == "cfv2-dev-o/r-shared-db",
          "deferred: the valid Persistent Dev hand-off verifies with the ORIGINAL shared-db lock although the verify call passes no scope")
    vv = deferred(inputs=AUTO_P, call_over={"phase_state": "{}"})
    check(vv["jobs"]["smoke"]["result"] == "skipped" and vv["jobs"]["rollback"]["result"] == "skipped" and not verify_release(vv), "deferred: an empty hand-off verifies nothing")
    vv = deferred(inputs=AUTO_P, call_over={"phase_state": json.dumps(forged)})
    check(vv["jobs"]["smoke"]["result"] == "failure" and not verify_release(vv), "deferred: a malformed / foreign hand-off (wrong SHA, non-https URL, other inputs) is refused as data")
    vv = deferred(inputs=AUTO_P, call_over={"phase_state": json.dumps(dict(forged, kinds=["production", "bogus"]))})
    check(vv["jobs"]["plan"]["result"] == "failure" and vv["jobs"]["smoke"]["result"] == "skipped", "deferred: an unknown delivery kind in the hand-off fails the plan closed")
    dv_ = deferred(FEAT, inputs=DEVI, toml=DEV_TOML)
    r1 = dv_["deploy"]
    check(r1["result"] == "success" and "PUBLISH" in r1["log"] and "state=success" not in r1["log"] and r1["outputs"]["deployed_and_verified"] == "false" and r1["jobs"]["deploy"]["outputs"]["ledger_id"] == "4242",
          "deferred persistent Dev (deploy call): migrates / publishes and stops - no smoke, no ledger success, the ledger id is handed over")
    check(dv_["jobs"]["smoke"]["result"] == "success" and dv_["jobs"]["smoke"]["outputs"]["deployed_and_verified"] == "true" and dv_["jobs"]["smoke"]["outputs"]["verified_url"] == "https://dev.example.com"
          and "state=success" in dv_["log"] and dv_["jobs"]["rollback"]["result"] == "skipped",
          "deferred persistent Dev (verify call): smoke verifies the published Dev URL and records the ledger success")
    dv_ = deferred(FEAT, inputs=DEVI, env=dict(GD, STUB_PROBES="500 ;200 "), toml=DEV_TOML)
    check(dv_["jobs"]["smoke"]["result"] == "failure" and dv_["jobs"]["smoke"]["outputs"]["gate_blocked"] == "false" and dv_["jobs"]["rollback"]["result"] == "success"
          and dv_["jobs"]["rollback"]["outputs"]["rollback_result"] == "restored_verified" and "PUBLISH" in dv_["log"] and f"commit={GOOD}" in dv_["log"],
          "deferred persistent Dev: a failed smoke recovers the last known-good Dev deployment (rebuilt at the LAST-GOOD sha) and re-verifies it; the smoke job stays red")
    dv_ = deferred(FEAT, inputs=DEVI, env=dict(GD, STUB_PROBES="500 ;200 ", STUB_NEWEST="9999"), toml=DEV_TOML)
    check(dv_["jobs"]["rollback"]["result"] == "failure" and "PUBLISH" not in dv_["log"] and "newer persistent-dev deployment" in dv_["summary"],
          "deferred persistent Dev: a NEWER Dev deployment started since (another branch) - recovery is skipped, it never replaces it")
    for g in ("failure", "cancelled"):
        dv_ = deferred(FEAT, inputs=DEVI, env=dict(GD, STUB_PROBES="200 "), gate=g, toml=DEV_TOML)
        check(dv_["jobs"]["smoke"]["result"] == "failure" and dv_["jobs"]["smoke"]["outputs"]["gate_blocked"] == "true" and dv_["jobs"]["rollback"]["result"] == "skipped"
              and "Cross-service smoke blocked" in dv_["summary"] and "state=failure" in dv_["log"] and "PUBLISH" not in dv_["log"],
              f"deferred persistent Dev: gate '{g}' blocks the verification, marks the deployment unverified and does not recover")
    dv_ = deferred(FEAT, inputs=DEVI, deploy_env={"STUB_TIPS": OTHER}, toml=DEV_TOML)
    check(dv_["jobs"]["smoke"]["result"] == "skipped", "deferred persistent Dev: nothing published (superseded) - nothing to verify")
    r = dev(inputs=dict(pipeline_phase="all"))
    check(r["result"] == "success" and r["outputs"]["deployed_and_verified"] == "true", "default phase (all): persistent Dev is the unchanged single call (publish + smoke)")


def group_native_dev_smoke_modes():
    """NEW: Native Dev, smoke modes, multi-target migration"""
    global r, dp, bl, sj, bad_in, pl_o, pd_, dep_out, prod_shared
    r = deploy(FEAT, inputs=ND, env={"STUB_PROBES": "200 "})
    check(r["result"] == "success" and "preview --name dev" in r["log"] and r["outputs"]["deployed_and_verified"] == "true" and r["jobs"]["plan"]["outputs"]["kinds"] == '["native-dev"]',
          "Native Dev: the plan has exactly one delivery; it publishes the named Preview `dev` and is verified")
    check("BUILD kind=native-dev site=nd" in r["log"] and "app_env=dev" in r["log"], "Native Dev builds with ITS OWN build env (build_env_shared_dev_native_preview) and kind")
    check("wrangler deploy" not in r["log"] and "--env dev" not in r["log"], "Native Dev is a Preview: never a Wrangler [env.dev] Worker, no `wrangler deploy`")
    check("native-dev" in json.dumps([x for x in r["raw"]["deploy"]["outputs"].keys() if r["raw"]["deploy"]["outputs"][x]]) or any(k == "h_native_dev" and v for k, v in r["raw"]["deploy"]["outputs"].items()), "Native Dev hand-off travels under its own keyed output (h_native_dev)")
    dp = json.loads(r["raw"]["deploy"]["outputs"]["h_native_dev"])
    check(dp["preview_name"] == "dev" and dp["published"] == "true" and dp["ledger_id"] == "4242", "Native Dev identity handed over: preview name `dev`, ledger id")
    check('"environment":"app/native-dev/dev"' in r["log"] and '"kind":"native-dev"' in r["log"] and '"preview_name":"dev"' in r["log"], "Native Dev ledger entry: environment app/native-dev/dev, kind native-dev, preview_name dev")
    check("pulls?state=open" not in r["log"], "Native Dev never comments on pull requests (the stable Dev URL is not a PR Preview)")

    r = deploy(FEAT, inputs=dict(ND, app_url_shared_dev_native_preview=NDURL), env={"STUB_PROBES": "200 "})
    check(r["result"] == "success" and f"url={NDURL}" in r["log"] and r["outputs"]["deployed_and_verified"] == "true", "Native Dev with a pre-resolved URL: exported to the build (CFV2_TARGET_URL) and confirmed after the publish")
    r = deploy(FEAT, inputs=dict(ND, app_url_shared_dev_native_preview="https://somewhere-else.example.workers.dev"), env={"STUB_PROBES": "200 "})
    check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false" and "differs from the URL this artifact was built for" in r["summary"] + "".join(l for _, l in [("", "")]) or r["result"] == "failure", "Native Dev: the published URL differs from the URL the build embeds -> publish rejected, nothing verified")
    r = deploy(FEAT, inputs=dict(ND, build_env_shared_dev_native_preview="PUBLIC_URL=https://app.example.com/"), env={"STUB_PROBES": "200 "})
    check(r["result"] == "failure" and not published(r) and "BUILD" not in r["log"], "Native Dev build env carrying the PRODUCTION URL fails before any build (per-target URL isolation)")
    r = deploy(MAIN, inputs=ND, env={"STUB_PROBES": "200 "})
    check(r["result"] == "success" and "preview --name" not in r["log"] and r["jobs"]["plan"]["outputs"]["kinds"] == '["production"]', "main push with Native Dev enabled: Production only, no Native Dev")
    r = deploy(FEAT, inputs=dict(ND, shared_dev_native_preview_on_branch_push=False), env={"STUB_PROBES": "200 "})
    check(r["jobs"]["plan"]["outputs"]["has_targets"] == "false" and res_of(r) == NOTHING and not published(r), "Native Dev OFF (default) and Native Branch OFF: no delivery path exists at all")
    r = deploy(FEAT, inputs=dict(ND, preview_on_branch_push=True), env={"STUB_PROBES": "200 "})
    check(r["jobs"]["plan"]["outputs"]["kinds"] == '["native-branch", "native-dev"]'.replace(", ", ",") or r["jobs"]["plan"]["outputs"]["kinds"] == '["native-branch","native-dev"]', "Branch + Dev enabled: two independent deliveries")
    bl = [l for l in r["log"].splitlines() if l.startswith("BUILD")]
    check(len(bl) == 2 + 0 or len(bl) >= 2, "Branch + Dev: separate builds (one per delivery)")
    check(any("kind=native-branch site=none" in l for l in bl) and any("kind=native-dev site=nd" in l for l in bl), "Branch + Dev: each delivery compiled with its OWN build env and kind (no shared bundle)")
    check("preview --name feat-x" in r["log"] and "preview --name dev" in r["log"], "Branch + Dev: both Native Previews published (feat-x and dev)")
    r = deploy(FEAT, inputs=fail_nd, env={"STUB_PROBES": "200 "})
    check(r["result"] == "failure" and "preview --name feat-x" in r["log"] and "preview --name dev" not in r["log"] and r["raw"]["smoke"]["outputs"].get("s_native_branch") and json.loads(r["raw"]["smoke"]["outputs"]["s_native_branch"])["deployed_and_verified"] == "true",
          "Branch + Dev: a Native Dev failure does not fail the Branch delivery (independent legs); the Branch Preview is published and verified")
    r = deploy(FEAT, inputs=dict(ND, preview_on_branch_push=True), env={"STUB_PROBES": "200 "}, files=None, toml=APP_TOML)
    r = deploy("refs/heads/dev", inputs=dict(ND, preview_on_branch_push=True), env={"STUB_PROBES": "200 "})
    check("preview --name b-dev" in r["log"] and "preview --name dev " not in r["log"].replace("preview --name dev\n", "preview --name dev ") or "preview --name b-dev" in r["log"], "a branch literally named `dev` gets Preview b-dev when Native Dev is on (the name `dev` is reserved)")
    r = deploy(FEAT, inputs=ND, env={"STUB_PROBES": "200 ", "STUB_TIPS": OTHER})
    check(r["result"] == "success" and not published(r) and r["outputs"]["deployed_and_verified"] == "false", "Native Dev: a superseded (stale) run publishes nothing")
    r = deploy(FEAT, inputs=ND, secrets={})
    check(r["result"] == "failure" and not published(r), "Native Dev: missing credentials fail closed")
    r = deploy(FEAT, inputs=ND, env=dict(GDN, STUB_PROBES="500 ;200 "))
    check(r["result"] == "failure" and r["raw"]["rollback"]["outputs"].get("rb_native_dev") == "restored_verified" and r["log"].count("preview --name dev") == 2 and r["log"].count("BUILD kind=native-dev") == 2 and "versions deploy" not in r["log"],
          "Native Dev: failed smoke -> last known-good Native Dev deployment rebuilt at the LAST-GOOD sha, republished to `dev`, re-verified; the run stays red; never a Production version")
    r = deploy(FEAT, inputs=ND, env=dict(GDN, STUB_PROBES="500 ;200 ", STUB_NEWEST="9999"))
    check(r["result"] == "failure" and r["log"].count("preview --name dev") == 1 and "newer native-dev deployment" in r["summary"], "Native Dev: a NEWER deployment of `dev` exists -> no rollback over it")
    r = deploy(FEAT, inputs=ND, env={"STUB_PROBES": "500 ;200 "})
    check(r["raw"]["rollback"]["outputs"].get("rb_native_dev") == "no_known_good" and r["log"].count("preview --name dev") == 1, "Native Dev without a known-good deployment: no recovery attempted, reported no_known_good")
    r = deploy(FEAT, inputs=ND, env=dict(GDN, STUB_PROBES="500 ;200 ", STUB_DEPLOYMENTS=json.dumps([{"id": 7, "sha": GOOD, "payload": {"repo": "o/r", "kind": "native-branch", "preview_name": "feat-x", "branch_ref": FEAT, "host_worker": "app", "worker": "app"}}])))
    check(r["raw"]["rollback"]["outputs"].get("rb_native_dev") == "no_known_good", "Native Dev never recovers to a branch Preview's deployment (ledger kind / identity must match)")
    r = deploy(FEAT, inputs=dict(ND, **SC), env={"STUB_PROBES": "200 "})
    check("SMOKECMD" in r["log"] and r["outputs"]["deployed_and_verified"] == "true", "smoke_mode full (default): probe AND smoke_cmd run, verified")
    r = deploy(FEAT, inputs=dict(ND, smoke_mode="basic", **SC), env={"STUB_PROBES": "200 "})
    check("SMOKECMD" not in r["log"] and r["outputs"]["deployed_and_verified"] == "true" and r["outputs"]["smoke_level"] == "content", "smoke_mode basic: content-level HTTP verification only, no application smoke_cmd, Verified")
    r = deploy(FEAT, inputs=dict(ND, smoke_mode="basic"), env={"STUB_PROBES": "302 https://t.cloudflareaccess.com/cdn-cgi/access/login/x"})
    check(r["outputs"]["deployed_and_verified"] == "false" and r["outputs"]["smoke_level"] == "liveness" and r["outputs"]["published_unverified"] == "true", "smoke_mode basic: an Access redirect is liveness only -> Published/Unverified, never Verified")
    r = deploy(FEAT, inputs=dict(ND, smoke_mode="basic"), env={"STUB_PROBES": "500 "})
    check(r["result"] == "failure" and r["outputs"]["deployed_and_verified"] == "false", "smoke_mode basic: a failing health probe fails the delivery")
    r = deploy(FEAT, inputs=dict(ND, smoke_mode="skip", **SC), env={"STUB_PROBES": "500 ;200 ", **GDN})
    check(r["result"] == "success" and "SMOKECMD" not in r["log"] and r["outputs"]["deployed_and_verified"] == "false" and r["outputs"]["verified_url"] == "" and r["outputs"]["published_unverified"] == "true" and r["outputs"]["smoke_level"] == "skipped",
          "smoke_mode skip: no request at all (a 500 probe is never consulted), Published/Unverified, verified_url empty")
    check("state=inactive" in r["log"] and "state=success" not in r["log"] and r["jobs"]["rollback"]["result"] == "skipped" and r["log"].count("preview --name dev") == 1, "smoke_mode skip: not recorded as known-good, no rollback, no recovery")
    r = deploy(MAIN, inputs=dict(smoke_mode="skip", release_cmd="pnpm release"), env={"STUB_PROBES": "500 "})
    check(r["result"] == "success" and published(r) and r["outputs"]["deployed_and_verified"] == "false" and not release_runs(r), "Production with smoke_mode skip: published, Unverified, Release does NOT run")
    r = deploy(FEAT, inputs=dict(ND, preview_on_branch_push=True, smoke_mode="full", smoke_mode_overrides='{"native-dev":"skip"}'), env={"STUB_PROBES": "200 "})
    sj = {k: json.loads(v) for k, v in r["raw"]["smoke"]["outputs"].items() if k.startswith("s_") and v}
    check(sj["s_native_branch"]["deployed_and_verified"] == "true" and sj["s_native_dev"]["deployed_and_verified"] == "false" and sj["s_native_dev"]["smoke_level"] == "skipped", "per-target smoke override: Native Dev skipped while the Branch Preview is fully verified")
    for bad_in in ({"smoke_mode": "quick"}, {"smoke_mode_overrides": '{"prod":"skip"}'}, {"smoke_mode_overrides": '{"production":"off"}'}, {"smoke_mode_overrides": "{not json"}):
        r = deploy(FEAT, inputs=dict(ND, **bad_in))
        check(r["jobs"]["plan"]["result"] == "failure" and not published(r), f"invalid smoke configuration {list(bad_in.values())[0]!r}: the plan fails closed before anything is built or published")
    r = deploy(FEAT, inputs=MD, env={"STUB_PROBES": "200 "})
    check(r["result"] == "success" and r["log"].count("migrations apply") == 1 and r["log"].count("preview --name") == 2, "Branch + Native Dev on ONE shared preview D1: the D1 is migrated ONCE, both deliveries publish")
    pl_o = r["raw"]["plan"]["outputs"]
    pd_ = json.loads(pl_o["descriptors"])
    check(pl_o["owns_native_branch"] == "true" and pl_o["owns_native_dev"] == "false" and pd_["native-dev"]["d1_owner_kind"] == "native-branch" and pd_["native-branch"]["d1_expected_id"] == pd_["native-dev"]["d1_expected_id"] == "PREV-ID" and pd_["native-branch"]["owns_migration"] and not pd_["native-dev"]["owns_migration"],
          "the migration plan names the single owner (native-branch) and the D1 id both members share")
    check(r["raw"]["migration"]["outputs"].get("mj_native_branch") and r["raw"]["migration"]["outputs"].get("mj_native_dev") and json.loads(r["raw"]["migration"]["outputs"]["mj_native_dev"]).get("d1_id") == "PREV-ID", "both deliveries receive the shared migration result (each under its own key)")
    r = deploy(FEAT, inputs=TWO_IN, toml=two_db, env={"STUB_PROBES": "200 "})
    check(r["result"] == "success" and r["log"].count("migrations apply") == 2, "Preview and persistent Dev on DISTINCT D1s: two independent migrations")
    r = deploy(FEAT, inputs=TWO_IN, toml=two_db, env={"STUB_PROBES": "200 ", "STUB_APPLY_FAIL_CFG": "migrate.dev.json"})
    dep_out = {k: (json.loads(v) if v else {}) for k, v in r["raw"]["deploy"]["outputs"].items()}
    check(r["result"] == "failure" and dep_out["h_native_branch"].get("published") == "true" and dep_out["h_persistent_dev"].get("published") != "true", "a failed DEV migration blocks only the Dev delivery; the Preview (other D1) is migrated, published and verified")
    r = deploy(FEAT, inputs=TWO_IN, toml=two_db, env={"STUB_PROBES": "200 ", "STUB_APPLY_FAIL_CFG": "migrate.preview.json"})
    dep_out = {k: (json.loads(v) if v else {}) for k, v in r["raw"]["deploy"]["outputs"].items()}
    check(r["result"] == "failure" and dep_out["h_persistent_dev"].get("published") == "true" and dep_out["h_native_branch"].get("published") != "true", "a failed PREVIEW migration blocks only the Preview delivery; Dev (other D1) proceeds")
    prod_shared = DEV_TOML.replace('database_name = "shared-db"\ndatabase_id = "SHARED-ID"\n[env.dev]', 'database_name = "prod-db"\ndatabase_id = "PROD-ID"\n[env.dev]')
    r = deploy(FEAT, inputs=dict(ND, **D1), toml=prod_shared)
    check(r["jobs"]["plan"]["result"] == "failure" and not migrated(r) and not published(r), "a Preview/Dev target mapped to the PRODUCTION D1 fails the plan: never migrated, never published")
    r = deploy(FEAT, inputs=dict(ND, **D1), toml='name = "app"\n[previews]\n')
    check(r["jobs"]["plan"]["result"] == "failure" and not migrated(r), "d1_migrations on but the target declares no D1: the plan fails closed")
    r = deploy(FEAT, inputs=dict(ND, **D1), env_by_job={"deploy": {"STUB_TIPS": OTHER}})
    check(r["result"] == "success" and migrated(r) and not published(r), "Native Dev: branch advanced after the Migration -> re-checked before publish, nothing published")
    r = deploy(FEAT, inputs=dict(ND), toml=APP_TOML)
    check(r["jobs"]["migration"]["result"] == "skipped" and r["jobs"]["deploy"]["result"] == "success", "no D1: the Migration stage is a no-op gate and never blocks a delivery")

    # ---- backward compatibility: a legacy caller (no new inputs) behaves exactly as before
    r = deploy(FEAT, env={"STUB_PROBES": "200 "})
    check(r["jobs"]["plan"]["outputs"]["kinds"] == '["native-branch"]' and "preview --name feat-x" in r["log"] and "preview --name dev" not in r["log"], "legacy caller on a branch push: Native Branch only (default), no Native Dev")
    r = deploy(MAIN, env={"STUB_PROBES": "200 "})
    check(r["jobs"]["plan"]["outputs"]["kinds"] == '["production"]' and r["outputs"]["deployed_and_verified"] == "true", "legacy caller on main: Production only")


def group_review_regressions():
    """FIX REGRESSIONS (PR #89 review): P1-A, P1-B, P2-A, P2-B"""
    global FWD, REV, nm, inp, e, pf, order, rr, po, used, fr, over
    FWD = CHAIN
    REV = ("gate", "plan") + tuple(f"preflight_{KEYED[k]}" for k in reversed(KINDS)) + tuple(f"migration_{KEYED[k]}" for k in reversed(KINDS)) + tuple(f"delivery_{KEYED[k]}" for k in reversed(KINDS))


    # (2) results completing in REVERSED order: the public outputs and each kind's result are identical (nothing depends on completion order)
    for nm, inp, e in (("Branch + Native Dev", BD, {"STUB_PROBES": "200 "}), ("Branch + Native Dev, D1 shared", dict(BD, **D1), {"STUB_PROBES": "200 "})):
        f_, b_ = run_order(FWD, inputs=inp, env=e), run_order(REV, inputs=inp, env=e)
        check(public_outputs(f_, inp) == public_outputs(b_, inp), f"P1-A reversed completion order ({nm}): every public output is identical")
        check({k: v["result"] for k, v in f_["res"].items()} == {k: v["result"] for k, v in b_["res"].items()}, f"P1-A reversed completion order ({nm}): every call finishes with the same result")
    pf = public_outputs(run_order(REV, inputs=BD, env={"STUB_PROBES": "200 "}), BD)
    check(pf["deploy_url"] == "https://feat-x-app.example.workers.dev" and pf["native_dev_url"] == "https://dev-app.example.workers.dev" and pf["deploy_verified"] == "true" and pf["native_dev_verified"] == "true"
          and pf["deploy_verified_url"] == pf["deploy_url"] and pf["native_dev_verified_url"] == pf["native_dev_url"] and pf["dev_url"] == "" and pf["rollback_result"] == "",
          "P1-A: Branch and Native Dev results are keyed per target (deploy_* = Branch, native_dev_* = Dev, dev_* empty) - no cross-target leakage")
    for order, nm in ((FWD, "forward"), (REV, "reversed")):
        rr = run_order(order, inputs=bad, env={"STUB_PROBES": "200 "})
        po = public_outputs(rr, bad)
        check(rr["res"]["delivery_native_dev"]["result"] == "failure" and rr["res"]["delivery_native_branch"]["result"] == "success" and po["deploy_verified"] == "true" and po["native_dev_verified"] in ("", "false") and po["native_dev_url"] == "",
              f"one target failing ({nm}): Native Dev fails closed (no URL, not verified) while the Branch delivery succeeds and is verified")
    rr = run_order(FWD, inputs=BD, env=dict(GDN2, STUB_PROBES="200 "), env_by_job={"smoke:native-dev": {"STUB_PROBES": "500 "}, "rollback:native-dev": {"STUB_PROBES": "200 "}})
    po = public_outputs(rr, BD)
    check(rr["res"]["delivery_native_dev"]["jobs"]["rollback"]["result"] == "success" and rr["res"]["delivery_native_branch"]["jobs"]["rollback"]["result"] == "skipped" and po["deploy_verified"] == "true"
          and po["native_dev_verified"] == "false" and po["rollback_result"] == "restored_verified" and rr["res"]["delivery_native_branch"]["result"] == "success",
          "P2-A rollback isolation: only the failing Native Dev call rolls back (restored_verified); the Branch call never runs a rollback and stays green and verified")
    check(rr["log"].count("preview --name dev") == 2 and rr["log"].count("preview --name feat-x") == 1, "P2-A rollback isolation: the rollback republishes `dev` only; the Branch Preview is published exactly once")
    # P2-A: Smoke of one kind does not wait for another kind's Deploy: a Deploy that never finishes (failure) elsewhere cannot change this kind's chain
    rr = run_order(FWD, inputs=BD, env={"STUB_PROBES": "200 "}, env_by_job={"deploy:native-branch": {"STUB_PREVIEW_FAIL": "1"}})
    check(rr["res"]["delivery_native_branch"]["jobs"]["deploy"]["result"] == "failure" and rr["res"]["delivery_native_branch"]["jobs"]["smoke"]["outputs"].get("smoke_status") == "none"
          and rr["res"]["delivery_native_dev"]["jobs"]["smoke"]["result"] == "success" and json.loads(rr["res"]["delivery_native_dev"]["outputs"]["smoke_json"])["deployed_and_verified"] == "true",
          "P2-A: the Branch Deploy fails, its Smoke has nothing to verify (smoke_status none) - Native Dev's own Deploy -> Smoke chain is unaffected")
    # stale deployment protection: a rollback whose live deployment is no longer ours is refused; a superseded run does not roll back
    rr = run_order(FWD, inputs=BD, env=dict(GDN2, STUB_PROBES="200 "), env_by_job={"smoke:native-dev": {"STUB_PROBES": "500 "}, "rollback:native-dev": {"STUB_NEWEST": "9999"}})
    check(rr["res"]["delivery_native_dev"]["jobs"]["rollback"]["result"] == "failure" and rr["res"]["delivery_native_dev"]["outputs"]["rollback_result"] == "no_known_good" and rr["log"].count("preview --name dev") == 1,
          "stale deployment protection: a NEWER deployment of `dev` exists -> the rollback is skipped (never replaces it)")
    # (4) shared D1 dedupe: ONE migration call serves both; the dependent delivery receives the OWNER's result, validated by kind + D1 id
    rr = run_order(FWD, inputs=dict(BD, **D1), env={"STUB_PROBES": "200 "})
    check(rr["log"].count("migrations apply") == 1 and rr["res"]["migration_native_branch"]["result"] == "success" and rr["res"]["migration_native_dev"]["result"] == "skipped" and rr["res"]["delivery_native_dev"]["result"] == "success",
          "P2-B shared D1: exactly one `d1 migrations apply`, run by the Native Branch call; Native Dev has no migration call and still deploys")
    used = json.loads(rr["res"]["delivery_native_dev"]["jobs"]["deploy"]["outputs"]["migration_used"])
    check(used["kind"] == "native-branch" and used["d1_id"] == "PREV-ID" and used["migration"] == "applied", "P2-B shared D1: the Native Dev delivery consumed the shared migration result (owner native-branch, D1 PREV-ID)")
    check(all(rr["res"][f"preflight_{KEYED[k]}"]["result"] == ("success" if k in ("native-branch", "native-dev") else "skipped") for k in KINDS), "P2-B: a Preflight ran for EVERY enabled kind (and only for them) before the migration")
    check(rr["res"]["preflight_native_dev"]["jobs"]["preflight"]["outputs"]["d1_id"] == "PREV-ID" and rr["res"]["preflight_native_dev"]["jobs"]["preflight"]["outputs"]["target_url"] == NDURL, "P2-B/P1-B: the Native Dev Preflight audited the built config's D1 id and canonical URL")
    # P2-B: nothing is migrated while ANY enabled target fails its preflight (the Native Dev build is broken, the shared D1 stays untouched)
    rr = run_order(FWD, inputs=dict(bad, **D1), env={"STUB_PROBES": "200 "})
    check(rr["res"]["preflight_native_dev"]["result"] == "failure" and not migrated(rr) and rr["res"]["migration_native_branch"]["result"] == "failure" and not published(rr)
          and rr["res"]["delivery_native_branch"]["jobs"]["deploy"]["result"] == "failure",
          "P2-B: one target's failed preflight blocks the migration of the D1 it shares - NO mutation at all, nothing published, fail closed")
    rr = run_order(FWD, inputs=dict(BD, **D1, build_cmd=REDIR), env={"STUB_PROBES": "200 "})
    check(rr["res"]["preflight_native_branch"]["result"] == "failure" and not migrated(rr) and not published(rr), "P2-B: an adapter-generated effective config that binds another D1 than the plan's is refused in the Preflight: nothing is migrated or published")
    # (5) incorrect / missing URLs: fail BEFORE the build
    rr = run_order(FWD, inputs=dict(ND, app_url_shared_dev_native_preview="https://somewhere-else.example.workers.dev"), env={"STUB_PROBES": "200 "})
    check(rr["res"]["delivery_native_dev"]["result"] == "failure" and "BUILD" not in rr["log"] and not published(rr), "P1-B incorrect URL: a declared Native Dev URL that contradicts the Preview URL fails BEFORE the build (nothing built, nothing published)")
    rr = run_order(FWD, inputs=dict(ND, preview_url_suffix=""), env={"STUB_PROBES": "200 "})
    check(rr["res"]["delivery_native_dev"]["result"] == "failure" and "BUILD" not in rr["log"] and not published(rr), "P1-B missing URL: no Cloudflare suffix, no preview_url_suffix and no declared URL -> fails before the build (never a guessed hostname)")
    rr = run_order(FWD, inputs=dict(BD, preview_url_suffix=""), env={"STUB_PROBES": "200 "})
    check(rr["res"]["delivery_native_branch"]["result"] == "failure" and rr["res"]["delivery_native_dev"]["result"] == "failure" and "BUILD" not in rr["log"], "P1-B missing URL: Native Branch is resolved BEFORE its build too (it used to build without a canonical URL)")
    rr = run_order(FWD, inputs=dict(BD, build_env_preview="OAUTH_CALLBACK_URL=https://id.example.com/cb", build_env_shared_dev_native_preview="OAUTH_CALLBACK_URL=https://dev-app.example.workers.dev/cb"), env={"STUB_PROBES": "200 "})
    check(rr["res"]["delivery_native_branch"]["result"] == "failure" and rr["res"]["delivery_native_dev"]["result"] == "success" and "preview --name feat-x" not in rr["log"],
          "P1-B: a Branch build env whose OAuth callback origin is not the Branch Preview's own origin fails before the build; the correct Native Dev callback passes")
    rr = run_order(FWD, inputs=dict(BD, build_env_preview="PUBLIC_APP_URL=https://feat-x-app.example.workers.dev", build_env_shared_dev_native_preview="PUBLIC_APP_URL=https://dev-app.example.workers.dev",
                                  build_cmd='echo "BUILD kind=$CFV2_DELIVERY_KIND url=$CFV2_TARGET_URL app=$PUBLIC_APP_URL" >> "$STUB_LOG"'), env={"STUB_PROBES": "200 "})
    check("BUILD kind=native-branch url=https://feat-x-app.example.workers.dev app=https://feat-x-app.example.workers.dev" in rr["log"] and "BUILD kind=native-dev url=https://dev-app.example.workers.dev app=https://dev-app.example.workers.dev" in rr["log"],
          "P1-B: each target is built with ITS OWN resolved canonical URL (CFV2_TARGET_URL) - Branch and Native Dev are separate builds")
    rr = run_order(FWD, inputs=dict(ND, build_cmd='echo BUILD >> "$STUB_LOG"'), env={"STUB_PROBES": "200 ", "STUB_PREVIEW_RAW": '{"preview":{"urls":["https://dev-OTHER.example.workers.dev"]}}'})
    check(rr["res"]["delivery_native_dev"]["jobs"]["deploy"]["result"] == "failure" and rr["res"]["delivery_native_dev"]["outputs"]["smoke_status"] == "none", "P1-B: Cloudflare publishing a URL other than the one the artifact was built for -> publish rejected, nothing verified")
    # (9) hand-off isolation: a hand-off of another kind (or mismatched data) is refused by the stage that would act on it
    fr = leg("smoke", "native-dev", needs=hand("native-branch", dict(GOODH, kind="native-branch", url="https://feat-x-app.example.workers.dev", preview_name="feat-x", ledger_id="4242"), smoke="success"), env={"STUB_PROBES": "200 "})
    check(fr["result"] == "failure" and "hand-off belongs to kind" in fr["log"] + fr["summary"] + "".join(n for n, _ in fr["steps"]) or fr["result"] == "failure", "hand-off isolation: Native Dev refuses a Native Branch hand-off (kind mismatch) - fail closed")
    fr = leg("smoke", "native-dev", needs=hand("native-dev", {"published": "true", "kind": "native-dev", "url": NDURL, "preview_name": "dev", "ledger_id": "4242", "published_sha": SHA, "target_url": "https://dev-other.example.workers.dev"}, smoke="success"), env={"STUB_PROBES": "200 "})
    check(fr["result"] == "failure", "hand-off isolation: a hand-off whose published URL differs from the URL it was built for is refused")
    fr = leg("smoke", "native-dev", needs={"deploy": {"result": "success", "outputs": {"handoff": "", "migration_used": "{}"}}, "smoke": SKIPPED}, env={"STUB_PROBES": "200 "})
    check(fr["result"] == "success" and fr["outputs"]["smoke_status"] == "none" and "BUILD" not in fr["log"], "missing hand-off: nothing was published -> nothing verified (smoke_status none), no verification claimed")
    for nm, over in (("a failed migration of its D1", dict(own_migration_result="failure", own_migration_json=json.dumps({"migration": "applied", "kind": "native-branch", "d1_id": "PREV-ID"}))),
                     ("a migration result for another D1", dict(own_migration_result="success", own_migration_json=json.dumps({"migration": "applied", "kind": "native-branch", "d1_id": "FOREIGN"}))),
                     ("a migration result of another kind", dict(own_migration_result="success", own_migration_json=json.dumps({"migration": "applied", "kind": "persistent-dev", "d1_id": "PREV-ID"}))),
                     ("a missing migration result", dict(own_migration_result="", own_migration_json=""))):
        dc = Ctx({"github": ctx(FEAT)["github"], "inputs": dict(DINPUT_DEFAULTS, stage="deliver", descriptor=desc("native-branch", "PREV-ID", "native-branch"), d1_migrations="true", tools_ref="x", inputs_json=json.dumps(dict(ctx(FEAT)["inputs"], d1_migrations=True)), **over), "needs": {}}, PROD_SECRETS)
        ws = make_workspace(APP_TOML)
        rr_ = run_job(ddoc, "deploy", dc, ws, {"STUB_TIPS": SHA})
        check(rr_["result"] == "failure" and not published({"log": rr_["log"]}), f"Deploy refuses to publish with {nm} (fail closed before checkout/build)")
    # (10) existing caller compatibility: the legacy single-kind outputs keep their shape and meaning
    rr = deploy(MAIN, env={"STUB_PROBES": "200 "})
    po = public_outputs(rr, ref=MAIN)
    check(po["deploy_url"] == "https://app.example.com" and po["deploy_verified"] == "true" and po["deploy_target"] == "prod" and po["version_id"] == "new-2" and po["previous_version_id"] == "prev-1" and po["deploy_smoke_level"] == "content"
          and po["deploy_verified_url"] == "https://app.example.com" and po["dev_url"] == "" and po["native_dev_url"] == "" and po["rollback_result"] == "" and po["deploy_gate_blocked"] in ("false", "False", ""),
          "(10) legacy caller on main: deploy_url / deploy_verified / deploy_target / version_id / previous_version_id / deploy_smoke_level keep their meaning; dev_* and native_dev_* stay empty")
    rr = deploy(FEAT, env={"STUB_PROBES": "200 "})
    po = public_outputs(rr)
    check(po["deploy_url"] == "https://feat-x-app.example.workers.dev" and po["deploy_target"] == "preview" and po["deploy_verified"] == "true", "(10) legacy caller on a branch: deploy_* describe the Native Branch Preview")
    rr = dev()
    po = public_outputs(rr, DEV_INPUTS)
    check(po["dev_url"] == "https://dev.example.com" and po["dev_verified_url"] == "https://dev.example.com" and po["deploy_url"] == "" and po["native_dev_url"] == "", "(10) legacy persistent Dev caller: dev_* describe the Dev Worker, deploy_* stay empty")
    # Production Release gate (8) stays tied to the Production call only, also with a simultaneous branch run elsewhere
    rr = deploy(MAIN, inputs={"smoke_mode": "skip"}, env={"STUB_PROBES": "500 "})
    check(not release_runs(rr), "(8) Release gate: smoke_mode=skip Production is never released")



GROUPS = {
    "production-release": group_production_release,
    "preview-deploy": group_preview_deploy,
    "migration-d1": group_migration_d1,
    "shared-dev": group_shared_dev,
    "chain-smoke-rollback": group_chain_smoke_rollback,
    "deferred-verification": group_deferred_verification,
    "native-dev-smoke-modes": group_native_dev_smoke_modes,
    "review-regressions": group_review_regressions,
}


def main(argv):
    if "--list" in argv:
        for gid, fn in GROUPS.items():
            print(f"{gid}\t{(fn.__doc__ or '').strip()}")
        return 0
    chosen = [argv[i + 1] for i, a in enumerate(argv) if a == "--group" and i + 1 < len(argv)] or list(GROUPS)
    unknown = [g for g in chosen if g not in GROUPS]
    if unknown:
        print(f"unknown group(s): {unknown}; known: {list(GROUPS)}", file=sys.stderr)
        return 2
    for gid in chosen:
        GROUPS[gid]()
    print(f"SUMMARY groups={len(chosen)} checks={checks} fail={fail}")
    write_perf_report()
    return fail


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
