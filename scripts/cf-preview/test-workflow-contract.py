#!/usr/bin/env python3
"""Static + evaluated contract checks for _cf-worker-v2.yml and its composite actions (no GitHub needed).

Job `if:` conditions and credential `env:` expressions are EVALUATED by a small GitHub-expression
interpreter over an event matrix, so the DAG/gating/secret-isolation claims are exercised, not just grepped.
The composite actions (actions/cf-deploy, actions/cf-ci-check) are parsed and checked structurally; their
behaviour is executed by test-jobs.py. What this cannot prove (needs a real run): GitHub's own scheduling,
Environment protection rules, runner behaviour. See docs/cf-worker-v2-refactor.md "Verified vs not verified".
"""
import json
import os
import pathlib
import re
import sys
import yaml

WF = ".github/workflows/_cf-worker-v2.yml"
DWF = ".github/workflows/_cf-worker-delivery.yml"
CWF = ".github/workflows/_cf-worker-ci.yml"
LWF = ".github/workflows/_cf-worker-lifecycle.yml"
doc = yaml.safe_load(open(WF))
ddoc = yaml.safe_load(open(DWF))
cdoc = yaml.safe_load(open(CWF))
ldoc = yaml.safe_load(open(LWF))
jobs = doc["jobs"]
djobs = ddoc["jobs"]
cjobs = cdoc["jobs"]            # CI: six validation jobs + Validation Gate
ljobs = ldoc["jobs"]
mjob = djobs["manual"]  # the manual persistent Dev dispatch lives in the delivery workflow (one `persistent-dev` delivery job)
inputs_def = doc[True]["workflow_call"]["inputs"]
fail = 0


def check(ok, msg):
    global fail
    print(("PASS: " if ok else "FAIL: ") + msg)
    if not ok:
        fail = 1


sys.path.insert(0, "scripts/cf-preview")
from ghexpr import Ctx, ev, render, strip_expr  # noqa: E402


def action(name):
    return yaml.safe_load(open(f"actions/{name}/action.yml"))


DEPLOY_ACTION, CI_ACTION = action("cf-deploy"), action("cf-ci-check")
ASTEPS = DEPLOY_ACTION["runs"]["steps"]
astep = lambda name: next(s for s in ASTEPS if s.get("name", "").startswith(name))
aidx = lambda name: next(i for i, s in enumerate(ASTEPS) if s.get("name", "").startswith(name))


# ---------------------------------------------------------------- scenario helpers
DEFAULT_INPUTS = {
    "app_name": "app", "deployment_strategy": "production-preview", "preview_on_branch_push": True,
    "shared_dev_on_branch_push": False, "d1_migrations": False, "d1_lock_scope": "", "dispatch_target": "", "persistent_dev_ref": "",
    "test_e2e_cmd": "pnpm e2e", "lint_cmd": "pnpm lint", "typecheck_cmd": "pnpm check", "test_unit_cmd": "pnpm unit",
    "test_integration_cmd": "pnpm integ", "build_cmd": "pnpm build", "ci_env": "", "use_mailpit": True,
    "release_cmd": "", "build_env_prod": "A=prod", "build_env_preview": "A=preview", "build_env_dev": "A=dev",
}


def scenario(event="push", ref="refs/heads/feat/x", inputs=None, needs=None, secrets=None, head_repo="o/r", ref_type=None, ev_ref_type=""):
    ref_type = ref_type or ("tag" if ref.startswith("refs/tags/") else "branch")
    return Ctx({
        "github": {
            "event_name": event, "ref": ref, "ref_type": ref_type, "ref_name": ref.split("/", 2)[-1],
            "repository": "o/r",
            "event": {"ref_type": ev_ref_type, "pull_request": {"head": {"repo": {"full_name": head_repo}, "ref": "x"}}},
        },
        "inputs": dict(DEFAULT_INPUTS, **(inputs or {})),
        "needs": needs or {},
    }, secrets)


def runs(job, ctx):
    return bool(ev(strip_expr(jobs[job]["if"]) or jobs[job]["if"], ctx))


def res(**kw):
    return {k: {"result": v, "outputs": {}} for k, v in kw.items()}


# Six independent validation jobs; optional ones are controlled by their command input (build_cmd is required).
VALIDATION = ["lint", "typecheck", "unit", "integration", "build", "e2e"]
CMD_INPUT = {"lint": "lint_cmd", "typecheck": "typecheck_cmd", "unit": "test_unit_cmd", "integration": "test_integration_cmd", "e2e": "test_e2e_cmd"}
JOB_NAME = {"lint": "Lint", "typecheck": "Typecheck", "unit": "Unit Tests", "integration": "Integration Tests", "build": "Build Validation", "e2e": "E2E"}


def vres(**over):
    """results of all six validation jobs (success unless overridden) + Migration (skipped: d1_migrations is false by default)"""
    d = {j: "success" for j in VALIDATION}
    d.update(over)
    return res(**d)


GREEN = vres()
MAIN, FEAT = "refs/heads/main", "refs/heads/feat/x"
text_all = open(WF).read()

# ---------------------------------------------------------------- 1. topology: Gate -> Plan -> ONE Migration job -> Delivery (per kind) -> Release
KINDS = ["production", "native-branch", "native-dev", "persistent-dev"]
KK = lambda k: k.replace("-", "_")
CALL_JOBS = {f"delivery_{KK(k)}" for k in KINDS}
check(set(jobs) == {"ci", "plan", "migration"} | CALL_JOBS | {"release", "config-guard", "lifecycle"},
      "public job set: CI call, Plan, ONE Migrate job, one Delivery CALL per kind (reusable workflow; persistent-dev covers the automatic AND the manual-dispatch delivery), Release + config-guard / lifecycle handlers")
check(set(cjobs) == set(VALIDATION) | {"gate"}, "the CI workflow has exactly the six validation jobs and the Validation Gate")
check(set(djobs) == {"deploy", "smoke", "rollback", "manual"}, "the internal delivery workflow has exactly: deploy, smoke, rollback, manual (ONE target per call; `manual` = the protected persistent Dev dispatch; no Preflight / Migration path)")
check(set(ljobs) == {"lifecycle"} and not os.path.exists(".github/workflows/_cf-worker-persistent-dev.yml"), "the event handler is one small workflow (Preview lifecycle); the separate manual persistent Dev workflow is gone")
for gone in ("dev-deploy", "dev-verify", "dev-rollback", "test", "prepare", "migrate", "test-gate", "preview", "production", "verify", "revert", "deploy", "smoke", "rollback", "preflight", "gate") + tuple(VALIDATION) + tuple(f"{s_}_{KK(k)}" for s_ in ("preflight", "migration") for k in KINDS):
    check(gone not in jobs, f"obsolete / matrix / inlined job '{gone}' is gone from the public workflow (validation lives in _cf-worker-ci.yml; no matrix output merging, no Preflight stage, no per-kind Migration call)")
check(sum(1 for j in jobs.values() if j.get("name") == "Migrate") == 1 and len(re.findall(r"^  migration[\w_-]*:", text_all, re.M)) == 1, "exactly ONE Migration job definition exists in the public pipeline")
check(sum(len(re.findall(r"^  (?:migration|preflight)[\w_-]*:", open(f).read(), re.M)) for f in (CWF, DWF, LWF)) == 0, "no other workflow of the pipeline defines a Migration or Preflight job")
check([cjobs[j]["name"] for j in VALIDATION] == [JOB_NAME[j] for j in VALIDATION], "validation jobs are named Lint, Typecheck, Unit Tests, Integration Tests, Build Validation, E2E")
check(cjobs["gate"]["name"] == "Validation Gate" and jobs["ci"]["name"] == "CI" and jobs["plan"]["name"] == "Plan" and jobs["migration"]["name"] == "Migrate", "CI, Validation Gate, Plan and Migrate are named jobs")
# Displayed check = `<caller job> / <name>...`; consumers call this workflow from a job named `CD`, so names carry no repeated words.
check({k: jobs["delivery_" + KK(k)]["name"] for k in KINDS} == {k: k for k in KINDS}, "check names: each Delivery call is named just its kind (production / native-branch / native-dev / persistent-dev)")
check([djobs[j]["name"] for j in ("deploy", "smoke", "rollback", "manual")] == ["Deploy", "Smoke Tests", "Revert on Verify Failure", "manual"], "check names: inner delivery jobs stay Deploy / Smoke Tests / Revert on Verify Failure, plus manual")
check((jobs["config-guard"]["name"], jobs["lifecycle"]["name"], jobs["release"]["name"]) == ("Guard", "lifecycle", "Release"), "check names: Guard, lifecycle, Release")
check(ljobs["lifecycle"]["name"] == "ledger", "check names: the handler inner job (ledger) never repeats its caller's name")
_names = [j["name"] for j in jobs.values()]
check(sorted(n for n in set(_names) if _names.count(n) > 1) == [], "check names: every public job name is unique - the engine has exactly ONE `persistent-dev` job (automatic DAG delivery and manual dispatch)")
check(not any(re.search(r"Delivery \(|Preview lifecycle|Database Migration|Config guard|Persistent Dev \(Hybrid\)|Delivery plan", str(j.get("name", ""))) for d in (jobs, cjobs, djobs, ljobs) for j in d.values()), "check names: none of the old verbose names remain in any v2 workflow")
check(jobs["ci"]["uses"] == "$/.github/workflows/_cf-worker-ci.yml", "CI is one STATIC `uses: $/.github/workflows/_cf-worker-ci.yml` call (this repository at the running commit)")
check("strategy" not in jobs["ci"], "no matrix on the CI call (E2E variants are the only matrix, inside the CI workflow)")


def needs_of(name, js=None):
    n = (js or jobs)[name].get("needs", [])
    return [n] if isinstance(n, str) else list(n or [])


for j in VALIDATION:
    check(needs_of(j, cjobs) == [], f"{j}: no needs (independent and parallel with the other five checks - no accidental serial dependency)")
check(set(needs_of("gate", cjobs)) == set(VALIDATION), "Validation Gate needs exactly the six validation jobs")
check(needs_of("ci") == [] and needs_of("plan") == ["ci"], "CI has no needs; the Plan needs only the CI call")
check(needs_of("migration") == ["plan"], "Migrate needs exactly the plan (which itself requires a passed CI) - and NO Deploy / Smoke / Rollback")
for k in KINDS:
    check(set(needs_of(f"delivery_{KK(k)}")) == {"plan", "migration"}, f"delivery_{KK(k)}: the plan and THE migration only")
check(not any(n.startswith("delivery_") for k in KINDS for n in needs_of(f"delivery_{KK(k)}")), "P2-A: NO Delivery call waits for another kind's Deploy / Smoke / Rollback")
check(not any(n.startswith("delivery_") for n in needs_of("migration")), "the Migration job never waits for any Delivery call")
check(set(needs_of("release")) == {"delivery_production"}, "Release reads ONLY the Production delivery call; Dev kinds can never gate it")
check(all("strategy" not in jobs[j] for j in CALL_JOBS | {"ci", "plan", "migration", "release"}), "P1-A: no matrix anywhere in the delivery graph (outputs are never merged across legs)")
check(all("strategy" not in j for j in djobs.values()), "P1-A: no matrix in the internal delivery workflow either")
check(set(needs_of("smoke", djobs)) == {"deploy"} and set(needs_of("rollback", djobs)) == {"deploy", "smoke"} and needs_of("deploy", djobs) == [], "delivery workflow: Smoke needs only THIS call's Deploy; Rollback needs this call's Deploy and Smoke")
DELIVERY_WF = "$/.github/workflows/_cf-worker-delivery.yml"
check(all(jobs[j]["uses"] == DELIVERY_WF for j in CALL_JOBS), "every per-kind call is a STATIC `uses: $/.github/workflows/_cf-worker-delivery.yml` of the SAME internal workflow (one delivery implementation)")
check(not any("uses" in j for d_ in (djobs, cjobs, ljobs) for j in d_.values()), "nesting depth: the delivery workflow calls no further reusable workflow (caller -> _cf-worker-v2 -> _cf-worker-delivery = 3 levels, limit 10)")
LEG = {"production": {"kind": "production", "target": "prod", "environment": "app/prod"}, "native-branch": {"kind": "native-branch", "target": "preview", "environment": "app/preview"},
       "native-dev": {"kind": "native-dev", "target": "preview", "environment": "app/native-dev"}, "persistent-dev": {"kind": "persistent-dev", "target": "dev", "environment": "app/dev"}}
for k in KINDS:
    w = jobs[f"delivery_{KK(k)}"]["with"]
    check("stage" not in w and "kind" not in w, f"delivery_{KK(k)}: no stage input (the delivery workflow only delivers) and the kind is only ever read from the descriptor")
    DESC_EXPR = "${{ toJSON(fromJSON(needs.plan.outputs.descriptors)['" + k + "']) }}" if k != "persistent-dev" else "${{ needs.plan.outputs.descriptors && toJSON(fromJSON(needs.plan.outputs.descriptors)['persistent-dev']) || '{}' }}"  # persistent-dev also serves the manual dispatch (no Plan)
    check(w["descriptor"] == DESC_EXPR and not set(w) & {"target", "environment", "d1_expected_id", "d1_owner_kind", "deploy_group", "rollback_group"},
          f"delivery_{KK(k)}: ONE target descriptor of the plan replaces the per-call target / environment / D1 / owner / concurrency-group copies")
    check(w["migration_result"] == "${{ needs.migration.result }}" and w["migration_json"] == "${{ needs.migration.outputs.migration_json }}" and not set(w) & {"kinds", "peers", "own_migration_result", "own_migration_json", "owner_migration_result", "owner_migration_json"},
          f"delivery_{KK(k)}: receives the ONE migration result (no peer / owner / per-kind migration routing)")
RANK = {"none": 0, "read": 1, "write": 2}
CALLED = {"ci": cjobs, "lifecycle": ljobs, **{c: djobs for c in CALL_JOBS}}
for n, j in jobs.items():
    if "uses" not in j:
        continue
    need = {}
    for dj in CALLED[n].values():  # the ceiling a caller must grant is the maximum over ALL jobs of the called workflow, even ones skipped by `if`
        for p_, v in {"contents": "read", **dj.get("permissions", {})}.items():
            need[p_] = max(need.get(p_, 0), RANK[v])
    cp = {"contents": "read", **j.get("permissions", {})}  # the workflow-level default is contents: read
    check(all(RANK[cp.get(p_, "none")] >= r_ for p_, r_ in need.items()),
          f"{n}: the calling job's permissions cover EVERY job of the called workflow (GitHub validates the ceiling for all jobs, including ones skipped by `if` - a lower ceiling is a startup_failure; found by the real-runner scratch run)")
check(len(text_all.splitlines()) <= 780, f"the public workflow stays thin ({len(text_all.splitlines())} lines)")

# ---------------------------------------------------------------- 2. one deployment implementation
ENGINE_USE = "$/actions/cf-deploy"
engine_jobs = {n: [s for s in j.get("steps", []) if s.get("uses") == ENGINE_USE] for n, j in {**{f"d.{k}": v for k, v in djobs.items()}, **jobs}.items()}
check({n for n, s in engine_jobs.items() if s} == {"d.deploy", "d.smoke", "d.rollback", "d.manual", "migration"} and all(len(s) == 1 for n, s in engine_jobs.items() if s and n != "migration") and len(engine_jobs["migration"]) == 3,
      "the four delivery-workflow jobs (deploy, smoke, rollback and the manual persistent Dev) each call the SAME delivery engine action exactly once; the Migration job calls it for the owner's migration and for each non-owner target's validation (no second deployment implementation)")
eng = {n.replace("d.", ""): s[0] for n, s in engine_jobs.items() if s and n != "migration"}
AUTO_JOBS = ("deploy", "smoke", "rollback")
check({n: eng[n]["with"]["stage"] for n in AUTO_JOBS} == {"deploy": "publish", "smoke": "verify", "rollback": "rollback"} and "stage" not in eng["manual"]["with"], "each automatic delivery job selects its engine stage (publish / verify / rollback); the manual job runs the engine's default one-job stage (all)")
MIG_STEPS = engine_jobs["migration"]
check([s["with"]["stage"] for s in MIG_STEPS] == ["preflight", "preflight", "migrate"] and MIG_STEPS[-1].get("id") == "migrate",
      "the Migration job validates every non-owner target first (stage preflight: build + effective-config audit + exact D1 binding), then migrates ONCE as its LAST engine step (stage migrate)")
check(not any(s["with"].get("kinds") or s["with"].get("peers") for s in MIG_STEPS), "the Migration job has no peer-result bookkeeping: step order is the guarantee")
check(all(eng[n]["with"]["target"] == "${{ fromJSON(inputs.descriptor).target }}" and eng[n]["with"]["kind"] == "${{ fromJSON(inputs.descriptor).kind }}" and eng[n]["with"]["descriptor"] == "${{ inputs.descriptor }}" for n in AUTO_JOBS), "the engine target comes from the call's validated descriptor, kind from the static input; the engine re-validates the descriptor")
check(all(re.fullmatch(r"\$\{\{ fromJSON\(needs\.plan\.outputs\.descriptors\)\[(fromJSON\(needs\.plan\.outputs\.migration\)\.kind|'[a-z-]+')\]\.target \}\}", s["with"]["target"]) and "toJSON(fromJSON(needs.plan.outputs.descriptors)[" in s["with"]["descriptor"] for s in MIG_STEPS),
      "Migration job: every engine step takes its target and descriptor from the plan's validated descriptors (the owner's, or the named non-owner's)")
check(eng["manual"]["with"]["target"] == "dev" and eng["manual"]["with"]["mode"] == "manual" and eng["manual"]["with"]["inputs_json"] == "${{ inputs.inputs_json }}" and "descriptor" not in eng["manual"]["with"], "manual persistent Dev binds target=dev, mode=manual (no descriptor: Plan does not run for a dispatch)")
check(all(eng[n]["with"]["inputs_json"] == "${{ inputs.inputs_json }}" for n in AUTO_JOBS) and all(eng[n]["with"]["d1_expected_id"] == "${{ fromJSON(inputs.descriptor).d1_expected_id }}" for n in AUTO_JOBS) and all(s["with"]["inputs_json"] == "${{ toJSON(inputs) }}" and s["with"]["d1_expected_id"] for s in MIG_STEPS),
      "engine inputs are passed as one JSON data blob; every stage binds the plan's D1 id (preflight, migrate, publish, verify, rollback)")
VERIFY_BLOB = "${{ inputs.pipeline_phase == 'verify' && toJSON(fromJSON(inputs.phase_state).inputs) || toJSON(inputs) }}"
check(all(jobs[f"delivery_{KK(k)}"]["with"]["inputs_json"] == VERIFY_BLOB for k in KINDS), "delivery calls: the verify call reads the DEPLOY call's inputs")
IMPL = re.compile(r"wrangler|migrate\.sh|publish\.sh|build\.sh|engine\.sh|deploy\.sh|smoke\.sh|d1 migrations|versions (upload|deploy)")
for n, j in {**{f"d.{k}": v for k, v in djobs.items()}, **jobs}.items():
    runs_txt = "\n".join(ln for s in j.get("steps", []) for ln in s.get("run", "").splitlines() if "echo" not in ln)
    check(not IMPL.search(runs_txt), f"{n}: no deployment implementation inline in YAML")

# ---------------------------------------------------------------- 3. gating over the event matrix
# CI call: the event/skip rules live in ONE place (the call job's `if`)
check(runs("ci", scenario("push", FEAT)) and runs("ci", scenario("push", MAIN)), "ci: runs on feature and main pushes")
check(not runs("ci", scenario("delete", FEAT, ev_ref_type="branch")), "ci: not on branch delete")
check(not runs("ci", scenario("pull_request", "refs/pull/1/merge", head_repo="o/r")), "ci: a same-repo PR is covered by the push run of the same SHA (no duplicate CI)")
check(runs("ci", scenario("pull_request", "refs/pull/1/merge", head_repo="fork/r")), "ci: fork PR runs (credential-free)")
check(not runs("ci", scenario("workflow_dispatch", "refs/heads/dev", {"dispatch_target": "persistent-dev"})), "ci: not on the persistent-dev dispatch")
check(not runs("ci", scenario("push", FEAT, {"pipeline_phase": "verify"})), "ci: not in a verify call (it never re-validates)")
check(runs("ci", scenario("workflow_dispatch", FEAT)), "ci: runs on a regular workflow_dispatch")
# Inside the CI workflow the only per-job switch is "is the command configured"
cruns = lambda name, **inp: bool(ev(strip_expr(cjobs[name].get("if", "true")) or cjobs[name].get("if", "true"), Ctx({"inputs": dict({"lint_cmd": "x", "typecheck_cmd": "x", "test_unit_cmd": "x", "test_integration_cmd": "x", "test_e2e_cmd": "x", "build_cmd": "x"}, **inp)}, {})))
for name, inp in CMD_INPUT.items():
    check(not cruns(name, **{inp: ""}), f"{name}: intentionally skipped when {inp} is empty")
    check(cruns(name), f"{name}: runs when {inp} is configured")
check("if" not in cjobs["build"], "build: build_cmd is required, never optional")
check(all(not any(k in cj for k in ("strategy",)) for n, cj in cjobs.items() if n != "e2e") and "matrix" in cjobs["e2e"]["strategy"] and cjobs["e2e"]["strategy"].get("fail-fast") is False, "only E2E has a matrix (variants, fail-fast off)")
gate_step = cjobs["gate"]["steps"][0]
check(cjobs["gate"]["if"] == "always()", "Validation Gate: always() - it runs (and fails) even when a job it needs failed or was cancelled")
check(all(f"{v.upper()}_CONFIGURED" in gate_step["env"] for v in VALIDATION) and 'success' in gate_step["run"] and 'skipped' in gate_step["run"], "Validation Gate: configured jobs must succeed; unconfigured ones must be skipped (fail closed otherwise)")
check(all(f"needs.{v}.result" in gate_step["env"][v.upper()] for v in VALIDATION), "Validation Gate reads the result of every validation job")
check(cjobs["gate"]["outputs"]["validation"] == "${{ steps.gate.outputs.validation }}" and 'echo "validation=passed" >> "$GITHUB_OUTPUT"' in gate_step["run"] and gate_step["run"].index("exit 1") < gate_step["run"].index("validation=passed"), "Validation Gate: `validation=passed` is written only after every check passed")
check(cdoc[True]["workflow_call"]["outputs"]["validation"]["value"] == "${{ jobs.gate.outputs.validation }}" and "concurrency" not in cdoc, "CI workflow exposes the gate's verdict and has no workflow-level concurrency (the public workflow's group would deadlock)")
# Gate semantics over every outcome, executed by the real script
import subprocess
def gate_run(unconfigured=(), **o):
    import tempfile
    env = {"PATH": "/usr/bin:/bin"}
    for v in VALIDATION:
        env[v.upper()] = o.get(v, "success")
        env[f"{v.upper()}_CONFIGURED"] = "false" if v in unconfigured else "true"
    out = tempfile.NamedTemporaryFile("w+", delete=False)
    env["GITHUB_OUTPUT"] = out.name
    r = subprocess.run(["bash", "-eo", "pipefail", "-c", gate_step["run"]], env=env, capture_output=True, text=True)
    return r.returncode, open(out.name).read().strip()
check(gate_run() == (0, "validation=passed"), "Validation Gate (executed): all six configured and green -> passed")
for res_ in ("failure", "cancelled", "skipped"):
    check(all(gate_run(**{v: res_})[0] == 1 and gate_run(**{v: res_})[1] == "" for v in VALIDATION), f"Validation Gate (executed): a configured job that finished '{res_}' fails the gate and writes no verdict")
check(gate_run(("e2e",), e2e="skipped") == (0, "validation=passed") and gate_run(("e2e",), e2e="success")[0] == 1, "Validation Gate (executed): an UNCONFIGURED job must be skipped (an unexpected run is fail-closed too)")

ci_ok = lambda **kw: dict(dict(event="push", ref=FEAT, needs={"ci": {"result": "success", "outputs": {"validation": "passed"}}}), **kw)
plan_if = lambda **kw: runs("plan", scenario(**dict(ci_ok(), **kw)))
check(plan_if() and plan_if(ref=MAIN), "plan: runs after a passed CI on feature and main pushes")
check(plan_if(event="workflow_dispatch"), "plan: runs on a workflow_dispatch after a passed CI")
for bad in ("failure", "cancelled", "skipped"):
    check(not plan_if(needs={"ci": {"result": bad, "outputs": {"validation": "passed"}}}), f"plan: blocked when the CI call is {bad}")
check(not plan_if(needs={"ci": {"result": "success", "outputs": {"validation": ""}}}) and not plan_if(needs={"ci": {"result": "success", "outputs": {}}}), "plan: a green CI call without the gate's `validation=passed` verdict is still blocked (fail closed)")
check(not plan_if(event="pull_request", ref="refs/pull/1/merge") and not plan_if(event="delete") and not plan_if(ref="refs/tags/v1"), "plan: never for PRs, deletes or tags")
check(plan_if(inputs={"pipeline_phase": "verify", "phase_state": "{}"}, needs={"ci": {"result": "skipped", "outputs": {}}}), "plan: a verify call plans from the hand-off, not from CI")
# ---------------------------------------------------------------- call-job gating (plan outputs are the only per-kind switch)
# Routing stub of the plan's descriptor: the call jobs only ROUTE it (delivery.test.mjs proves its content).
DESC_FIX = {"production": ("prod", "app/prod"), "native-branch": ("preview", "app/preview"), "native-dev": ("preview", "app/native-dev"), "persistent-dev": ("dev", "app/dev")}


def desc(k, d1="", owner="", repo="o/r", app="app", scope=""):
    g = f"cfv2-dev-{repo}-{scope or app}" if k == "persistent-dev" else f"cfv2-leg-1-{k}"
    return {"v": 1, "kind": k, "target": DESC_FIX[k][0], "environment": DESC_FIX[k][1], "deploy_group": g, "rollback_group": g if k == "persistent-dev" else g + "-rollback",
            "d1_expected_id": d1, "d1_owner_kind": owner, "owns_migration": bool(d1 and owner == k), "smoke_mode": "full", "phase": "all"}


def pl(kinds=("native_branch",), d1=False, owner=None, result="success", **extra):
    """Routing stub of the plan job's outputs. With d1 the kinds share ONE D1; `owner` (default: the first kind) is the migration owner."""
    o = {"has_targets": str(bool(kinds)).lower(), "kinds": json.dumps([k.replace("_", "-") for k in kinds]), "has_migrations": str(bool(d1 and kinds)).lower()}
    for k in ("production", "native_branch", "native_dev", "persistent_dev"):
        o[f"on_{k}"] = str(k in kinds).lower()
    own = (owner or (kinds[0] if kinds else "")).replace("_", "-")
    o["descriptors"] = json.dumps({k.replace("_", "-"): desc(k.replace("_", "-"), "DB1" if d1 else "", own if d1 else "") for k in kinds})
    o["migration"] = json.dumps({"kind": own, "environment": DESC_FIX[own][1], "d1_id": "DB1", "kinds": [k.replace("_", "-") for k in kinds]}) if d1 and kinds else ""
    o.update(extra)
    return {"result": result, "outputs": o}


G_OK = {"result": "success", "outputs": {}}
import json  # noqa: E402
mig_if = lambda kinds=("native_branch",), d1=True, plan_result="success", **kw: runs("migration", scenario("push", kw.pop("ref", FEAT), dict({"d1_migrations": d1}, **kw.pop("inputs", {})), {"plan": pl(kinds, d1, result=plan_result)}))
check(mig_if() and mig_if(("production",), ref=MAIN), "Migration: ONE job runs for a main push (Production D1) and for a feature push (Preview/Dev D1) when d1_migrations is on")
check(mig_if(("native_branch", "native_dev")) and mig_if(("native_branch", "persistent_dev")), "Migration: Native Branch + Native Dev (or persistent Dev) sharing one D1 still run ONE migration job")
check(not mig_if(d1=False), "Migration: D1-less app / d1_migrations=false -> skipped, and Delivery proceeds (see below)")
check(not mig_if(inputs={"pipeline_phase": "verify"}), "Migration: never in a verify call (deferred verification never repeats a migration)")
check(not mig_if(plan_result="failure") and not mig_if(plan_result="skipped") and not mig_if(plan_result="cancelled"), "Migration: needs a successful plan - the plan only runs after a passed CI, so a failed CI, a failed or an invalid plan mutates nothing")
check(not runs("migration", scenario("pull_request", "refs/pull/1/merge", {"d1_migrations": True}, {"plan": {"result": "skipped", "outputs": {}}})) and not runs("migration", scenario("delete", FEAT, {"d1_migrations": True}, {"plan": {"result": "skipped", "outputs": {}}})),
      "Migration: no migration on a PR or a lifecycle-only (delete) event")
check(not runs("migration", scenario("push", FEAT, {"d1_migrations": True}, {"plan": pl((), True)})), "Migration: nothing enabled (CI-only run) -> no migration")
mj = jobs["migration"]
env_expr = mj["environment"]["name"]
check(render(env_expr, scenario("push", MAIN, {"d1_migrations": True}, {"plan": pl(("production",), True)})) == "app/prod"
      and render(env_expr, scenario("push", FEAT, {"d1_migrations": True}, {"plan": pl(("native_branch", "native_dev"), True)})) == "app/preview"
      and render(env_expr, scenario("push", FEAT, {"d1_migrations": True}, {"plan": pl(("native_dev",), True)})) == "app/native-dev",
      "Migration: runs in the GitHub Environment of the plan's migration owner (<app>/prod keeps its approval protection; Production approval cannot be bypassed)")
check(mj["permissions"] == {"contents": "read", "deployments": "read"}, "Migration: read-only token (deployments read for the Preview identity guard)")
check(mj["outputs"]["migration_json"] == "${{ toJSON(steps.migrate.outputs) }}" and [x.get("id") for x in mj["steps"] if x.get("id") in ("migrate", "result")] == ["migrate", "result"] and mj["outputs"]["migration_status"] == "${{ steps.result.outputs.status }}", "Migration: ONE deterministic result - the outputs of its single `migrate` step, and an always-run status step (applied | none | failed)")
MS = mj["steps"]
vsteps = [x for x in MS if x.get("with", {}).get("stage") == "preflight"]
check(all("fromJSON(needs.plan.outputs.migration).kind != '" in x["if"] for x in vsteps) and {x["with"]["kind"] for x in vsteps} == {"native-dev", "persistent-dev"} and MS.index(vsteps[-1]) < MS.index(MS[-1]),
      "Migration: the non-owner targets are validated BEFORE the owner's migrate step; the owner itself is validated inside its migrate step (build, effective-config audit, D1 binding, stale guard, then the one mutation)")
check(all(isinstance(x.get("uses"), str) and not x.get("run") for x in MS if x.get("with", {}).get("stage") in ("preflight", "migrate")), "Migration: validation and migration are engine calls, no inline shell")
ACT_STEPS = DEPLOY_ACTION["runs"]["steps"]
gate_i = next(n for n, x in enumerate(ACT_STEPS) if x.get("id") == "migration_gate")
first_work = next(n for n, x in enumerate(ACT_STEPS) if "Stale-SHA guard (start)" in x.get("name", ""))
check(gate_i < first_work and ACT_STEPS[gate_i]["if"] == "inputs.stage == 'publish'" and "migration-gate" in ACT_STEPS[gate_i]["run"],
      "Migration gate: runs in the engine right after init - before the stale guard, setup, build and publish - for the publish stage only")
# P1 (PR #89 review): a stale-skipped migration never authorizes a publish - executed through the REAL engine gate command (cli.mjs migration-gate)
def gate_cli(result, mj, d1="true", kind="native-branch", owner="native-branch", d1id="DB1"):
    d = subprocess.run(["node", "-e", "import('./actions/cf-preview/delivery.mjs').then(m=>console.log(JSON.stringify(m.targetDescriptor({kind:process.argv[1],app:'app',runId:1,repo:'o/r',d1ExpectedId:process.argv[2],ownerKind:process.argv[3]}))))", kind, d1id, owner], capture_output=True, text=True, check=True).stdout.strip()
    env = {"PATH": "/usr/bin:/bin:" + __import__("os").path.dirname(__import__("shutil").which("node") or "/usr/bin/node"), "DESCRIPTOR": d, "D1_MIGRATIONS": d1, "MIGRATION_RESULT": result, "MIGRATION_JSON": mj}
    r = subprocess.run(["node", "actions/cf-preview/cli.mjs", "migration-gate", "--kind", kind], env=env, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)
import os
import subprocess  # noqa: E402
APPLIED = json.dumps({"migration": "applied", "kind": "native-branch", "d1_id": "DB1"})
check(gate_cli("success", APPLIED)[0] == 0, "Migration Gate (executed): a succeeded Migration job with an applied migration of the planned owner on the planned D1 authorizes the publish")
check(gate_cli("skipped", "", d1="false")[0] == 0, "Migration Gate (executed): d1_migrations=false needs no migration result")
for what, args in (("stale=true", ("success", json.dumps({"stale": True}))), ("stale='true'", ("success", json.dumps({"stale": "true"}))),
                   ("stale=true with otherwise valid metadata", ("success", json.dumps({"migration": "applied", "kind": "native-branch", "d1_id": "DB1", "stale": "true"}))),
                   ("migration none", ("success", json.dumps({"migration": "none", "kind": "native-branch", "d1_id": "DB1"}))),
                   ("empty result", ("success", "")), ("invalid JSON", ("success", "{x")), ("owner mismatch", ("success", json.dumps({"migration": "applied", "kind": "persistent-dev", "d1_id": "DB1"}))),
                   ("D1 mismatch", ("success", json.dumps({"migration": "applied", "kind": "native-branch", "d1_id": "OTHER"}))),
                   ("Migration job failed", ("failure", APPLIED)), ("Migration job cancelled", ("cancelled", APPLIED)), ("Migration job skipped while required", ("skipped", APPLIED))):
    rc, out = gate_cli(*args)
    check(rc != 0, f"Migration Gate (executed): {what} blocks the publish")
check("stale" in gate_cli("success", json.dumps({"stale": "true"}))[1], "Migration Gate (executed): the stale refusal says why")
_gi = aidx("Migration gate")
check(all(_gi < aidx(n) for n in ("Stale-SHA guard (start)", "Setup dependencies", "Prepare", "Build and audit", "Publish the audited artifact", "Smoke the exact deployed URL")), "Migration Gate: runs before the stale guard, set-up, prepare, build, publish and smoke of the publish stage - a fresh branch tip can never substitute for it")
dl_if = lambda k, kinds=None, plan_result="success", inputs=None, mig="skipped", **kw: runs(f"delivery_{KK(k)}", scenario("push", kw.pop("ref", FEAT), inputs or {}, {"plan": pl(kinds or (KK(k),), False, result=plan_result), "migration": {"result": mig, "outputs": {}}}))
check(dl_if("native-branch") and dl_if("production", ref=MAIN) and dl_if("native-dev") and dl_if("persistent-dev"), "Delivery: runs when the plan succeeded and ITS kind is enabled - a SKIPPED migration (d1_migrations=false) never blocks it")
check(all(dl_if(k, mig="success", ref=MAIN if k == "production" else FEAT) for k in KINDS), "Delivery: runs after a successful migration")
check(not any(dl_if(k, mig=m, ref=MAIN if k == "production" else FEAT) for k in KINDS for m in ("failure", "cancelled")), "Delivery: a failed or cancelled Migration prevents EVERY dependent deployment")
check(not dl_if("native-dev", kinds=("native_branch",)) and not dl_if("production", kinds=("native_branch",), ref=FEAT), "Delivery: a disabled kind creates no delivery (no phantom paths)")
check(not dl_if("native-branch", plan_result="failure") and not dl_if("native-branch", plan_result="skipped"), "Delivery: blocked by a failed or skipped plan (a failed CI skips the plan; invalid configuration fails it: nothing deploys)")
check(dl_if("native-branch", inputs={"pipeline_phase": "verify"}), "Delivery: a verify call plans from the hand-off, not from CI (and the migration is skipped there: it already ran in the deploy call)")
pub = next(x for x in djobs["deploy"]["steps"] if x.get("id") == "engine")["with"]
check(all(pub[k] == "${{ inputs." + k + " }}" for k in ("migration_result", "migration_json")) and djobs["deploy"]["outputs"]["migration_used"] == "${{ steps.engine.outputs.migration_used }}"
      and "run" not in djobs["deploy"]["steps"][0],
      "Deploy: the ONE migration result is checked by the engine gate against the descriptor's owner and D1 (delivery.test.mjs covers every refusal)")
sm_if = lambda inputs, deploy_res="success": bool(ev(djobs["smoke"]["if"], Ctx({"inputs": dict({"phase": "all"}, **inputs), "needs": {"deploy": {"result": deploy_res, "outputs": {}}}}, {})))
check(sm_if({}) and sm_if({"phase": "verify"}) and not sm_if({"phase": "deploy"}) and sm_if({}, "failure"), "Smoke: runs in all and verify phases (even when Deploy failed: nothing published -> smoke_status none), never in the deploy phase")
rb_if = lambda status, inputs=None: bool(ev(djobs["rollback"]["if"], Ctx({"inputs": dict({"phase": "all", "descriptor": json.dumps(desc("native-branch")), "inputs_json": "{}"}, **(inputs or {})), "needs": {"smoke": {"result": "x", "outputs": {"smoke_status": status}}}}, {})))
check(rb_if("failure") and not rb_if("success") and not rb_if("none") and not rb_if("") and not rb_if("failure", {"phase": "deploy"}), "Rollback: only after THIS call's Smoke FAILED (conditional, never on the happy path, never in the deploy phase)")
rgo = djobs["rollback"]["steps"][0]
check(rgo["id"] == "go" and "POLICY_OK" in rgo["run"] and "gate_blocked" in rgo["run"] and "published" in rgo["run"],
      "Rollback guard: this run published + no blocked cross-service gate + (Production only) policy automatic")
check("fromJSON(inputs.descriptor).kind != 'production' ||" in rgo["env"]["POLICY_OK"] and "production_rollback_policy == 'automatic'" in rgo["env"]["POLICY_OK"], "Rollback: the Production policy applies to Production only; Preview / Dev recovery is target-owned")


def dout(kind, **kw):
    return {"result": "success", "outputs": dict({"handoff": "{}", "smoke_json": json.dumps({"deployed_and_verified": "true"}), "rollback_result": ""}, **kw)}


rel_ok = lambda **kw: scenario(**dict(dict(event="push", ref=MAIN, inputs={"release_cmd": "pnpm release"}, needs={"delivery_production": dout("production")}), **kw))
check(runs("release", rel_ok()), "Release: runs after a verified main Production delivery (call success, no rollback)")
check(not runs("release", rel_ok(needs={"delivery_production": dout("production", smoke_json=json.dumps({"deployed_and_verified": "false"}))})), "Release: Production published but NOT content-verified (liveness, smoke_mode=skip) never releases")
check(not runs("release", rel_ok(needs={"delivery_production": dict(dout("production"), result="failure")})), "Release: a failed delivery (failed smoke) blocks Release even when the rollback SUCCEEDED")
check(not runs("release", rel_ok(needs={"delivery_production": dout("production", rollback_result="restored_verified")})), "Release: any rollback that ran blocks Release")
check(not runs("release", rel_ok(needs={"delivery_production": {"result": "skipped", "outputs": {}}})), "Release: a skipped delivery never releases")
check(not runs("release", rel_ok(ref=FEAT)), "Release: main only")
check(not runs("release", rel_ok(inputs={"release_cmd": ""})), "Release: optional (no release_cmd, no job)")
check(not runs("release", rel_ok(inputs={"release_cmd": "x", "dispatch_target": "persistent-dev"})), "Release: never from a persistent-dev dispatch")
check(not runs("release", rel_ok(inputs={"release_cmd": "x", "pipeline_phase": "deploy"})), "Release: never in the deploy phase")
check("delivery_native" not in jobs["release"]["if"] and "delivery_persistent" not in jobs["release"]["if"], "Release: the condition never reads a Preview / Dev delivery")

check(inputs_def["production_rollback_policy"]["default"] == "manual" and inputs_def["d1_rollback_compat"]["default"] == "unknown", "defaults stay conservative: rollback policy manual, D1 compatibility unknown")
check(runs("lifecycle", scenario("delete", FEAT, ev_ref_type="branch")) and not runs("lifecycle", scenario("delete", FEAT, ev_ref_type="tag")), "lifecycle: handles branch delete only")
check(runs("lifecycle", scenario("pull_request", "refs/pull/1/merge", head_repo="o/r")) and not runs("lifecycle", scenario("pull_request", "refs/pull/1/merge", head_repo="fork/r")), "lifecycle: same-repo PR comments only")
check(not runs("lifecycle", scenario("push", FEAT)), "lifecycle: not on push")
dev_ok = dict(inputs={"dispatch_target": "persistent-dev", "deployment_strategy": "hybrid", "persistent_dev_ref": "refs/heads/dev"})
SKIP_PM = {"plan": {"result": "skipped", "outputs": {}}, "migration": {"result": "skipped", "outputs": {}}}  # Plan / Migration never run for a dispatch
check(runs("delivery_persistent_dev", scenario("workflow_dispatch", "refs/heads/dev", needs=SKIP_PM, **dev_ok)) and not runs("delivery_persistent_dev", scenario("workflow_dispatch", "refs/heads/other", needs=SKIP_PM, **dev_ok))
      and not runs("delivery_persistent_dev", scenario("push", "refs/heads/dev", needs=SKIP_PM, **dev_ok)), "persistent Dev: the manual entry runs on the approved ref via dispatch only, never on push")
check(not runs("delivery_persistent_dev", scenario("workflow_dispatch", "refs/heads/dev", needs=SKIP_PM, inputs=dict(dev_ok["inputs"], deployment_strategy="production-only")))
      and not runs("delivery_persistent_dev", scenario("workflow_dispatch", "refs/heads/dev", needs=SKIP_PM, inputs=dict(dev_ok["inputs"], persistent_dev_ref="")))
      and not runs("delivery_persistent_dev", scenario("workflow_dispatch", "refs/heads/dev", needs=SKIP_PM, inputs=dict(dev_ok["inputs"], pipeline_phase="verify"))), "persistent Dev manual entry: hybrid strategy, a non-empty persistent_dev_ref and a non-verify phase are required")
pd_with = {k: render(v, scenario("workflow_dispatch", "refs/heads/dev", needs=SKIP_PM, **dev_ok)) for k, v in jobs["delivery_persistent_dev"]["with"].items() if k in ("manual", "descriptor")}
check(pd_with == {"manual": "true", "descriptor": "{}"}, "persistent Dev manual entry: the delivery call gets manual=true and an empty descriptor (no Plan ran)")
auto_with = {k: render(v, scenario("push", FEAT, needs={"plan": pl(("persistent_dev",), False), "migration": {"result": "skipped", "outputs": {}}})) for k, v in jobs["delivery_persistent_dev"]["with"].items() if k == "manual"}
check(auto_with == {"manual": "false"}, "persistent Dev automatic entry (push): manual=false")
BAD = {"deployment_strategy": "production-only", "shared_dev_on_branch_push": True}
check(runs("config-guard", scenario("push", MAIN, BAD, GREEN)) and runs("config-guard", scenario("push", FEAT, dict(BAD, shared_dev_on_branch_push=False, shared_dev_native_preview_on_branch_push=True), GREEN)),
      "Config guard: production-only + (persistent Dev | Native Dev) is reported")
check(runs("config-guard", scenario("push", FEAT, {"shared_dev_on_branch_push": True, "shared_dev_native_preview_on_branch_push": True}, GREEN)), "Config guard: persistent Dev + Native Dev together (conflicting automatic Dev channels) is reported")
check(not runs("config-guard", scenario("push", MAIN, {"deployment_strategy": "production-only", "preview_on_branch_push": True}, GREEN)), "Config guard: production-only + default Preview=true stays accepted")
# ---------------------------------------------------------------- 4. public interface: additive, defaults unchanged
check(inputs_def["preview_on_branch_push"]["default"] is True and inputs_def["shared_dev_on_branch_push"]["default"] is False, "interface: existing defaults unchanged (Preview true, persistent Dev false)")
check(inputs_def["shared_dev_native_preview_on_branch_push"]["default"] is False and inputs_def["build_env_shared_dev_native_preview"]["default"] == "" and inputs_def["smoke_mode"]["default"] == "full" and inputs_def["smoke_mode_overrides"]["default"] == "{}",
      "interface: new inputs are opt-in (Native Dev off, smoke_mode full, no overrides)")
wc_out = doc[True]["workflow_call"]["outputs"]
LEGACY_OUT = {"deploy_url", "deploy_verified_url", "deploy_published_unverified", "deploy_verified", "deploy_smoke_level", "deploy_target", "version_id", "previous_version_id",
              "migration_status", "rollback_result", "dev_url", "dev_verified_url", "dev_published_unverified", "deploy_gate_blocked", "phase_state"}
check(LEGACY_OUT <= set(wc_out), "interface: every pre-existing output is still produced")
check({"native_dev_url", "native_dev_verified_url", "native_dev_published_unverified", "native_dev_verified", "native_dev_smoke_level"} <= set(wc_out), "interface: Native Dev outputs added")
LEGACY_IN = {"tools_ref", "app_name", "app_path", "app_version", "deployment_strategy", "preview_on_branch_push", "shared_dev_on_branch_push", "preview_worker_name", "wrangler_config", "lint_cmd", "typecheck_cmd", "test_unit_cmd",
             "test_integration_cmd", "build_cmd", "ci_env", "use_mailpit", "test_e2e_cmd", "test_e2e_setup_cmd", "test_e2e_build_cmd", "test_e2e_variants", "test_e2e_env", "build_env_prod", "build_env_preview", "build_env_dev",
             "smoke_cmd", "smoke_env", "access_protected_targets", "probe_path", "probe_expect_status", "sha_path", "preview_host_check", "preview_binding_allow", "preview_deny_resources", "app_url_prod",
             "production_rollback_policy", "d1_migrations", "d1_binding", "d1_lock_scope", "d1_rollback_compat", "release_cmd", "persistent_dev_ref", "dev_deploy_cmd", "dev_smoke_cmd", "app_url_dev", "dev_build_cmd",
             "dev_wrangler_env", "dev_binding_allow", "dev_deny_resources", "dev_worker_name", "dispatch_target", "pipeline_phase", "phase_state", "upstream_gate", "runner"}
check(LEGACY_IN <= set(inputs_def), "interface: every pre-existing input is still accepted (no caller breaks)")
check(all("default" in inputs_def[k] or inputs_def[k].get("required") for k in inputs_def), "interface: every input has a default or is required")
# ---------------------------------------------------------------- 5. credentials, Environments, permissions
SECRETS = {"CLOUDFLARE_API_TOKEN": "PROD-TOKEN", "CLOUDFLARE_ACCOUNT_ID": "PROD-ACCT", "PREVIEW_SECRETS": "K=v"}
check(not any("PREVIEW_API_TOKEN" in k or "PREVIEW_ACCOUNT_ID" in k for k in doc[True]["workflow_call"]["secrets"]), "workflow_call declares no Preview-specific Cloudflare credentials")
check(set(ddoc[True]["workflow_call"]["secrets"]) == {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "PREVIEW_SECRETS", "SMOKE_USERNAME", "SMOKE_PASSWORD"} and all(not v.get("required") for v in ddoc[True]["workflow_call"]["secrets"].values()),
      "delivery workflow: secrets are declared explicitly (never `secrets: inherit`) and none is required")
check(all("inherit" not in str(jobs[j].get("secrets", "")) for j in CALL_JOBS), "calls pass secrets explicitly, never `secrets: inherit`")
for n in ("deploy", "rollback"):
    env = djobs[n]["env"]
    c = Ctx({"secrets": SECRETS, "github": {}}, SECRETS)
    check(render(env["CLOUDFLARE_API_TOKEN"], c) == "PROD-TOKEN" and render(env["CLOUDFLARE_ACCOUNT_ID"], c) == "PROD-ACCT", f"{n}: the account credentials")
    c = Ctx({"secrets": {}, "github": {}}, {})
    check(render(env["CLOUDFLARE_API_TOKEN"], c) == "", f"{n}: a missing account token stays EMPTY (no fallback)")
menv = jobs["migration"]["env"]
c = Ctx({"secrets": SECRETS, "github": {"token": "T"}}, SECRETS)
check(render(menv["CLOUDFLARE_API_TOKEN"], c) == "PROD-TOKEN" and render(menv["CLOUDFLARE_ACCOUNT_ID"], c) == "PROD-ACCT" and render(menv["GH_TOKEN"], c) == "T", "migration: the account credentials and the job token")
check(render(menv["CLOUDFLARE_API_TOKEN"], Ctx({"secrets": {}, "github": {}}, {})) == "", "migration: a missing account token stays EMPTY (no fallback)")
check("CLOUDFLARE" not in yaml.dump(djobs["smoke"]) and "secrets.CLOUDFLARE" not in yaml.dump(djobs["smoke"]), "Smoke Tests: NO Cloudflare credentials of any kind (it only probes the published URL)")
check(set(djobs["smoke"]["env"]) == {"GH_TOKEN", "HANDOFF"}, "Smoke Tests: the job env holds the GitHub token and the (data-only) hand-off, nothing else")
check("CLOUDFLARE_DEV" not in yaml.dump(djobs), "the delivery workflow never references the Dev credentials")
for k in KINDS:
    sec = jobs[f"delivery_{KK(k)}"]["secrets"]
    check(set(sec) == {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "PREVIEW_SECRETS", "SMOKE_USERNAME", "SMOKE_PASSWORD"}, f"delivery_{KK(k)}: passes exactly the secrets its stages need")
check("SMOKE" not in yaml.dump(jobs["migration"]), "migration: never receives the smoke credentials")
for n in AUTO_JOBS:
    w = eng[n]["with"]
    for k in KINDS:
        c = Ctx({"inputs": {"kind": k, "descriptor": json.dumps(desc(k))}}, SECRETS)
        got = render(w["preview_secrets"], c)
        check((got == "K=v") == (k in ("native-branch", "native-dev")) and (k in ("native-branch", "native-dev") or "PREVIEW_SECRETS" not in c.secret_reads), f"{n}: Preview-only secrets reach {k} only if it is a Preview kind")
ptxt = yaml.dump(mjob)
check("secrets.CLOUDFLARE_API_TOKEN" in ptxt and "secrets.CLOUDFLARE_ACCOUNT_ID" in ptxt and "CLOUDFLARE_PREVIEW" not in ptxt and "PREVIEW_SECRETS" not in ptxt, "manual persistent Dev reuses account credentials, not Preview secrets")
check(not any(k in j.get("env", {}) for j in list(jobs.values()) + list(djobs.values()) for k in ("TARGET", "BUILD_ENV", "SMOKE_ENV", "DEV_ENV", "EXPECTED_SHA", "RUN_SHA")),
      "no job exports TARGET / BUILD_ENV / DEV_ENV / EXPECTED_SHA ...: the delivery target is bound by the engine action input, not by environment")
ENVEXPR = "${{ fromJSON(inputs.descriptor).environment }}"
check(all(djobs[n]["environment"]["name"] == ENVEXPR for n in ("deploy", "rollback")), "deploy / rollback run in the descriptor's per-kind GitHub Environment (<app>/prod keeps its protection)")
check(all(render(ENVEXPR, Ctx({"inputs": {"descriptor": json.dumps(desc(k))}}, SECRETS)) == DESC_FIX[k][1] for k in KINDS), "descriptor environments are <app>/prod | preview | native-dev | dev")
check("environment" not in djobs["smoke"], "Smoke: no Environment (no protection prompt; it mutates nothing)")
check(djobs["deploy"]["environment"]["url"] == "${{ steps.engine.outputs.url }}", "Deploy: the job shows the actual deployed URL (environment.url)")
check(mjob["environment"] == "${{ fromJSON(inputs.inputs_json).app_name }}/dev" and render(mjob["environment"], Ctx({"inputs": {"inputs_json": json.dumps({"app_name": "app"})}}, {})) == DESC_FIX["persistent-dev"][1], "manual persistent Dev runs in the <app>/dev Environment (same as the automatic persistent-dev kind)")
allj = {**jobs, **{f"d.{k}": v for k, v in djobs.items()}, **{f"ci.{k}": v for k, v in cjobs.items()}, **{f"l.{k}": v for k, v in ljobs.items()}, }
check(all(j.get("permissions", {}).get("contents", "read") == "read" for n, j in allj.items() if n != "release"), "no job except Release can write repository contents")
check(jobs["migration"]["permissions"].get("deployments") == "read", "Migration can READ (not write) Deployments: the Preview identity guard reads the ledger owner")
check(ddoc.get("permissions") == {"contents": "read"} and "concurrency" not in ddoc, "delivery workflow: read-only default token and NO workflow-level concurrency (the public workflow's group would deadlock)")
check("contents: write" not in yaml.dump({k: allj[k] for k in allj if k != "release"}) and not any(w in text_all for w in ("lock.sh", "cfv2-locks", "refs/cfv2")), "no custom Git-ref lock and no content-write permission outside Release")
for name, job_ in [(f"ci.{v}", cjobs[v]) for v in VALIDATION + ["gate"]] + [("lifecycle", ljobs["lifecycle"])]:
    text = yaml.dump(job_)
    check("secrets." not in text and "CLOUDFLARE" not in text, f"{name}: no secrets and no Cloudflare credentials of any kind")
check(not ({"secrets"} & set(cdoc[True]["workflow_call"])) and not ({"secrets"} & set(ldoc[True]["workflow_call"])) and "secrets" not in jobs["ci"] and "secrets" not in jobs["lifecycle"], "the CI and lifecycle calls pass NO secrets at all (credential-free by construction)")
check("secrets.SMOKE_PASSWORD" not in yaml.dump(cjobs) and eng["smoke"]["with"]["smoke_password"] == "${{ secrets.SMOKE_PASSWORD }}" and "smoke_password" not in eng["deploy"]["with"] and not any("smoke_password" in x["with"] for x in MIG_STEPS),
      "smoke credentials reach only the engine actions that smoke (Smoke Tests, Rollback re-smoke, Dev)")
for n in ("deploy", "smoke", "rollback", "manual", "migration"):
    cks = [s for s in (djobs.get(n) or jobs[n])["steps"] if str(s.get("uses", "")).startswith("actions/checkout")]
    check(cks and all(s["with"].get("persist-credentials") is False for s in cks), f"{n}: checkouts do not persist the GitHub token into the workspace")
check(all(s["with"].get("persist-credentials") is False for s in mjob["steps"] if str(s.get("uses", "")).startswith("actions/checkout")), "manual persistent Dev: the checkout does not persist the GitHub token")

# ---------------------------------------------------------------- 6. cross-repository reusable workflow + local composite action references
USES_ALL = [(n, i, s_["uses"]) for n, j in allj.items() for i, s_ in enumerate(j.get("steps", [])) if "uses" in s_] + [(n, -1, j["uses"]) for n, j in allj.items() if "uses" in j]
SELF = [(n, i, u) for n, i, u in USES_ALL if u.startswith("$/")]
check(SELF and all("${{" not in u and "@" not in u for _, _, u in SELF), "every `$/` reference is STATIC and carries no @ref suffix (uses: cannot take an expression; the ref is always the running commit)")
check(all(pathlib.Path(u[2:]).exists() and (pathlib.Path(u[2:] + "/action.yml").exists() or pathlib.Path(u[2:]).suffix == ".yml") for _, _, u in SELF), "every `$/` target exists in this repository (actions/<name>/action.yml or a workflow file)")
check(not any(u.startswith("./") or u.startswith("firstsun-dev/.github") for _, _, u in USES_ALL), "no `./` (resolves against the CALLER's workspace) and no pinned `firstsun-dev/.github/...@ref` reference remains in the pipeline")
CHECKOUTS = [(n, s_) for n, j in allj.items() for s_ in j.get("steps", []) if str(s_.get("uses", "")).startswith("actions/checkout")]
check(CHECKOUTS and not any("repository" in s_.get("with", {}) or s_.get("with", {}).get("path") == ".cf-tools" for _, s_ in CHECKOUTS if s_.get("with", {}).get("path") != "recover"), "no job checks out firstsun-dev/.github: the only checkouts are the application at the run SHA (and the recovery checkout of the last good SHA)")
ALLTXT = "".join(open(f).read() for f in (WF, DWF, CWF, LWF, ".github/workflows/_cf-worker-preview-gc.yml", "actions/cf-deploy/action.yml", "actions/cf-ci-check/action.yml", "actions/cf-tools/action.yml"))
check(".cf-tools" not in re.sub(r"#.*", "", ALLTXT).replace("`.cf-tools`", ""), "no `.cf-tools` workspace path remains in any workflow or action")
check(all(pathlib.Path(f"actions/{n}/action.yml").exists() for n in ("cf-deploy", "cf-ci-check", "cf-tools", "setup", "mailpit")), "all referenced shared actions exist")
check(not any(f"{k}: ${{{{ inputs.tools_ref }}}}" in ALLTXT for k in ("ref", "tools_ref")), "tools_ref is not forwarded anywhere (the tooling is the running commit by construction)")
check(inputs_def["tools_ref"].get("required") is not True and inputs_def["tools_ref"]["default"] == "" and "tools_ref" not in ddoc[True]["workflow_call"]["inputs"], "tools_ref stays ACCEPTED for existing callers but is optional and ignored")
check(set(ddoc[True]["workflow_call"]["inputs"]) >= {"descriptor", "inputs_json", "migration_result", "migration_json"} and "stage" not in ddoc[True]["workflow_call"]["inputs"] and not set(ddoc[True]["workflow_call"]["inputs"]) & {"kind", "target", "environment", "deploy_group", "rollback_group", "d1_expected_id", "d1_owner_kind"}, "delivery workflow: the descriptor, inputs and the one migration result are its interface")
_ci = open("actions/cf-ci-check/action.yml").read()
check("rm -rf" not in _ci and "-delete" not in _ci, "cf-ci-check deletes nothing (nothing from this repository is in the app's workspace)")
CT = action("cf-tools")
check(CT["runs"]["using"] == "composite" and "$GITHUB_ACTION_PATH/../cf-preview" in CT["runs"]["steps"][0]["run"] and 'TOOLS_DIR=' in CT["runs"]["steps"][0]["run"], "cf-tools locates actions/cf-preview relative to itself and exports TOOLS_DIR")
check(all(re.search(r"GITHUB_ACTION_PATH/\.\./cf-preview/", x.get("run", "")) for x in ASTEPS if "engine.sh" in x.get("run", "")), "cf-deploy finds the engine scripts next to itself ($GITHUB_ACTION_PATH/../cf-preview), independent of any checkout path")
for n, i, u in SELF:
    if u.endswith("cf-tools"):
        j = allj[n]
        prev = [x for x in j["steps"][:i] if str(x.get("uses", "")).startswith("actions/setup-node")]
        w = j["steps"][i].get("with", {})
        check(w.get("install") != "true" or prev, f"{n}: cf-tools with install: true is preceded by setup-node")
check(inputs_def["tools_ref"]["description"].lstrip().startswith("DEPRECATED"), "tools_ref is documented as deprecated")
# Every call passes exactly the inputs the called workflow declares (an unknown `with` key or a missing required input is a startup_failure on GitHub)
for n, called_doc in (("ci", cdoc), ("lifecycle", ldoc), *[(c, ddoc) for c in sorted(CALL_JOBS)]):
    decl = called_doc[True]["workflow_call"]["inputs"]
    given = jobs[n].get("with", {})
    check(set(given) <= set(decl) and all(k in given for k, v in decl.items() if v.get("required")), f"{n}: its `with:` keys are declared inputs of the called workflow and every required input is supplied")
    sec_decl = set(called_doc[True]["workflow_call"].get("secrets", {}))
    check(set(jobs[n].get("secrets", {})) <= sec_decl, f"{n}: only secrets the called workflow declares are passed")
check(all(jobs["ci"]["with"][k] == "${{ inputs." + k + " }}" for k in cdoc[True]["workflow_call"]["inputs"]), "ci: every CI input is wired 1:1 from the SAME-named public input (no renamed or re-derived command)")
ALINT = yaml.safe_load(open(".github/actionlint.yaml"))["paths"]
check(sorted(len(v["ignore"]) for v in ALINT.values()) == [1, 2] and all("queue" in x for x in ALINT[WF]["ignore"]) and all("$/" in x or "\\$/" in x for x in ALINT[".github/workflows/_cf-worker-*.yml"]["ignore"]),
      "actionlint: exactly three message ignores - `concurrency.queue` and the two `$/` formats - everything else is still linted")
check("2.336.0" in text_all, "the public workflow documents the minimum runner version for `$/` (2.336.0)")

# ---------------------------------------------------------------- 7. validation jobs use the common CI action
CMDS = {"lint": "lint_cmd", "typecheck": "typecheck_cmd", "unit": "test_unit_cmd", "integration": "test_integration_cmd", "build": "build_cmd"}
for j, inp in CMDS.items():
    steps = cjobs[j]["steps"]
    ci = [s for s in steps if s.get("uses") == "$/actions/cf-ci-check"]
    t = yaml.dump(cjobs[j])
    others = [i for k, i in CMDS.items() if k != j] + ["test_e2e_cmd"]
    check(len(ci) == 1 and ci[0]["with"]["cmd"] == "${{ inputs." + inp + " }}" and not any(f"inputs.{o}" in t for o in others), f"{j}: runs {inp} exactly once through actions/cf-ci-check, and no other job's command")
    check(ci[0]["with"]["title"] == JOB_NAME[j], f"{j}: the check summary carries its own name")
    check(not any("playwright" in yaml.dump(s_).lower() for s_ in steps) and "upload-artifact" not in t and "download-artifact" not in t, f"{j}: installs no browsers and uses no artifacts")
    check(cjobs[j].get("continue-on-error") is None, f"{j}: failures are never hidden by continue-on-error")
    check(steps[0].get("uses") == "actions/checkout@v6" and steps[1].get("uses") == "$/actions/cf-ci-check", f"{j}: checks out the app, then runs the CI action (nothing else)")
ci_steps = CI_ACTION["runs"]["steps"]
check(ci_steps[0]["name"].startswith("Import validation env") and "parse-env.mjs\" TARGET_BUILD_ENV" in ci_steps[0]["run"] and ci_steps[0]["run"].index("TARGET_BUILD_ENV") < ci_steps[0]["run"].index("CI_ENV"),
      "cf-ci-check imports the target build env, then ci_env, through the safe importer (precedence preserved)")
ck = next(s for s in ci_steps if s.get("id") == "check")
check(ck["env"]["CMD"] == "${{ inputs.cmd }}" and ck["run"] == 'bash -c "$CMD"' and "continue-on-error" not in ck, "cf-ci-check runs its one command via env (never interpolated) and never hides a failure")
check(any("GITHUB_STEP_SUMMARY" in s.get("run", "") and s.get("if") == "always()" for s in ci_steps), "cf-ci-check always writes a short Step Summary")
check(any(s.get("uses", "").startswith("$/actions/mailpit") and s.get("if") == "inputs.use_mailpit == 'true'" for s in ci_steps) and
      all(("use_mailpit" in str(next(s for s in cjobs[j]["steps"] if s.get("uses") == "$/actions/cf-ci-check")["with"])) == (j in ("unit", "integration")) for j in CMDS),
      "Mailpit starts only for Unit and Integration tests")
check("build_env_prod" in ci_steps[0]["env"]["TARGET_BUILD_ENV"] and "build_env_preview" in ci_steps[0]["env"]["TARGET_BUILD_ENV"], "validation jobs use the Production build env on main and the Preview build env elsewhere")
e2e_text = yaml.dump(cjobs["e2e"])
check("test_e2e_build_cmd" in e2e_text and "test_e2e_env" in e2e_text and "inputs.build_cmd" not in e2e_text, "E2E builds with its own test build command and env")
uploads = [(j, s) for j, job in allj.items() for s in job.get("steps", []) if "upload-artifact" in str(s.get("uses", ""))]
check("download-artifact" not in text_all + open(DWF).read() and sorted(j for j, _ in uploads) == ["ci.e2e", "d.smoke"], "no build artifact hand-off between jobs; the only uploads are the failure-only E2E and smoke reports")
for j_, u in uploads:
    check(u.get("if") == "failure()" and u.get("continue-on-error") is True and 0 < u["with"]["retention-days"] <= 7, f"{j_} report: failure-only, never fails the job, short retention")
check(not any(k in inputs_def for k in ("preview_build_cmd", "preview_provision_cmd", "preview_migrate_cmd", "preview_cleanup_cmd", "preview_smoke_cmd", "preview_smoke_env", "verify_cmd", "migrate_cmd_prod", "migrate_cmd_preview")), "obsolete v2 inputs are removed; no custom migration command exists (the target D1 check cannot be bypassed)")

# ---------------------------------------------------------------- 8. injection hygiene (workflow AND actions)
INJECT = re.compile(r"\$\{\{[^}]*(github\.event\.|github\.head_ref|inputs\.\w*_(cmd|env)\b|inputs\.(build_cmd|sha_path|cmd|inputs_json|ci_env|preview_secrets))")
for jname, job in allj.items():
    for s in job.get("steps", []):
        check(not INJECT.search(s.get("run", "")), f"{jname}/{s.get('name', s.get('id', '?'))}: no event/caller-command text interpolated into the script")
for aname, a in (("cf-deploy", DEPLOY_ACTION), ("cf-ci-check", CI_ACTION)):
    for s in a["runs"]["steps"]:
        check("${{" not in s.get("run", ""), f"{aname}/{s.get('name', '?')}: no `${{{{ }}}}` interpolation inside run scripts (values arrive through env)")

# ---------------------------------------------------------------- 9. delivery engine action: ONE ordered pipeline
need = ["Install tooling deps", "Initialise", "Stale-SHA guard (start)", "Setup dependencies", "Prepare", "Build and audit", "Validate the exact migration target",
        "Stale-SHA guard (before migration)", "Apply pending D1 migrations", "Stale-SHA guard (after migration", "Publish the audited artifact", "Smoke the exact deployed URL", "Mark deployed and verified"]
order = [aidx(n) for n in need]
check(order == sorted(order) and len(set(order)) == len(order),
      "engine order: install tooling deps FIRST (cli.mjs needs smol-toml) -> init -> stale -> setup -> prepare -> BUILD+audit -> D1 target check -> stale -> MIGRATE -> stale -> PUBLISH -> smoke -> verified")
check("node_modules/smol-toml" in astep("Install tooling deps")["run"] and "env.CFV2_DEPS != 'done'" in astep("Setup dependencies")["if"] and 'setenv CFV2_DEPS "done"' in open("actions/cf-preview/engine.sh").read(),
      "engine: several engine calls in ONE job (the Migration job) install the tooling and the application dependencies once")
check(aidx("Build and audit") < aidx("Apply pending D1 migrations"), "no schema migration runs before the deployable build and the target config were validated")
check(aidx("Validate the exact migration target") < aidx("Apply pending D1 migrations") < aidx("Publish the audited artifact"), "the exact D1 target is validated before the migration, which precedes the publish")
mig = astep("Apply pending D1 migrations")
check("engine.sh\" migrate" in mig["run"] and mig["env"]["ENGINE_TARGET"] == "${{ inputs.target }}", "Migration is ONE explicit engine step, bound to the composite target input (not to application env)")
check(sum(1 for s in ASTEPS if 'engine.sh" migrate' in s.get("run", "")) == 1, "the migration is invoked from exactly one step of the engine")
STAGE_IF = lambda name: astep(name)["if"]
check("inputs.stage == 'all' || inputs.stage == 'deploy' || inputs.stage == 'migrate'" in STAGE_IF("Apply pending D1 migrations") and "publish" not in STAGE_IF("Apply pending D1 migrations").replace("before publish", ""),
      "engine: migrations run only in stage migrate (Migration job) or all (Shared Dev), never in the Deploy / Smoke / Rollback stages")
check("inputs.stage == 'all' || inputs.stage == 'deploy' || inputs.stage == 'publish'" in STAGE_IF("Publish the audited artifact") and "inputs.stage == 'all' || inputs.stage == 'deploy' || inputs.stage == 'publish'" in STAGE_IF("Stale-SHA guard (after migration"),
      "engine: publishing (and its last stale check) runs only in stage publish (Deploy job) or all (Shared Dev)")
for n in ("Build and audit", "Validate the exact migration target", "Prepare"):
    check("inputs.stage != 'verify' && inputs.stage != 'rollback'" in STAGE_IF(n), f"engine: '{n}' never runs in the Smoke / Rollback stages")
check("inputs.stage == 'verify'" in STAGE_IF("Smoke the exact deployed URL") and "inputs.stage == 'all' && steps.publish.outcome == 'success'" in STAGE_IF("Smoke the exact deployed URL"),
      "engine: smoke runs in stage verify, or after a publish of the same job (stage all)")
check("inputs.stage == 'verify' || inputs.stage == 'rollback'" in STAGE_IF("Hand-off") and "HANDOFF_DEPLOY" in astep("Hand-off")["env"], "engine: the Smoke / Rollback stages hydrate the validated hand-off from job outputs (not from runner files)")
check(astep("Apply pending D1 migrations")["run"].endswith("| tee -a \"$GITHUB_OUTPUT\"") and astep("Prepare")["env"]["PREPARE_LEDGER"] == "${{ inputs.stage != 'migrate' && inputs.stage != 'preflight' }}", "engine: the Preflight and Migration stages open no ledger entry; Deploy owns it")
for n in ("Setup dependencies", "Prepare", "Build and audit", "Validate the exact migration target", "Stale-SHA guard (before migration)", "Apply pending D1 migrations", "Stale-SHA guard (after migration", "Publish the audited artifact", "Smoke the exact deployed URL", "Mark deployed and verified"):
    check("env.STALE != 'true'" in astep(n)["if"], f"engine: '{n}' is skipped for a superseded run")
check("guard before-migration" in astep("Stale-SHA guard (before migration)")["run"] and "guard before-publish" in astep("Stale-SHA guard (after migration")["run"], "engine: the branch tip is re-checked immediately before the migration AND again immediately before the publish")
check(not any(st.get("name", "").startswith("Shared-D1 guard") for st in ASTEPS) and "d1-guard" not in open("actions/cf-preview/engine.sh").read(), "engine: the old Shared-D1 guard is gone - a D1 shared by several targets is migrated ONCE by the Migration stage and each delivery waits for that leg")
pub = astep("Publish the audited artifact")
check("engine.sh\" publish" in pub["run"] and pub["env"]["ENGINE_TARGET"] == "${{ inputs.target }}" and "build" not in pub["run"], "engine: publish is publish-only (the build stage is a separate, earlier step)")
ver = astep("Mark deployed and verified")
check("steps.smoke.outcome == 'success'" in ver["if"] and "env.STALE != 'true'" in ver["if"] and "steps.smoke.outcome" in astep("Smoke the exact deployed URL")["id"] + "steps.smoke.outcome" and astep("Smoke the exact deployed URL")["if"].count("steps.publish.outcome == 'success'") == 1,
      "engine: verified only after a smoke that itself ran only against a published deployment (stage all: same-job publish; stage verify: the Deploy job's hand-off)")
check(DEPLOY_ACTION["outputs"]["deployed_and_verified"]["value"].endswith("|| 'false' }}") and "steps.verified" in DEPLOY_ACTION["outputs"]["deployed_and_verified"]["value"], "engine reports verified only from the final marker step")
check(sorted(s["name"] for s in ASTEPS if s.get("continue-on-error") is True) == sorted([astep("Recovery - rebuild")["name"], astep("Recovery - verify")["name"], astep("Rollback - re-run")["name"]]),
      "engine: continue-on-error is used ONLY on the recovery / re-smoke steps whose outcome the verdict step reads; every other failure stays visible")
check("Production recovery" not in " ".join(s.get("name", "") for s in ASTEPS) and "prod-recover" not in yaml.dump(ASTEPS), "engine: the old in-job Production recovery step is gone (replaced by the Rollback stage)")
rb = astep("Rollback - restore")
check("prod-rollback" in rb["run"] and "inputs.stage == 'rollback'" in rb["if"] and "inputs.target == 'prod'" in rb["if"], "engine: Production rollback exists only in stage rollback for target prod")
check("rolled_back" in astep("Rollback - re-run")["if"] and "prod-rollback-verify" in astep("Rollback - re-run")["run"], "engine: the restored Production is smoked again, and only after a rollback actually happened")
fin = astep("Rollback - fail unless")
check("steps.rbfinal.outputs.ok != 'true'" in fin["if"] and "exit 1" in fin["run"], "engine: the Rollback job stays RED unless the previous deployment was restored AND content-verified")
check(aidx("Rollback - restore") < aidx("Rollback - re-run") < aidx("Rollback - verdict") < aidx("Rollback - fail unless"), "engine: rollback -> re-smoke -> verdict -> fail-unless-verified")
rf = astep("Recovery - find")
check("inputs.mode == 'auto'" in rf["if"] and "inputs.target != 'prod'" in rf["if"] and "steps.publish.outcome == 'success' && steps.smoke.outcome == 'failure'" in rf["if"] and "inputs.stage == 'rollback' && inputs.target != 'prod'" in rf["if"],
      "engine: automatic Preview/Dev recovery only in auto mode, only after a published URL failed smoke (Shared Dev: same job; Preview: the Rollback job), never for Production")
check("recover-run" in astep("Recovery - rebuild")["run"] and "migrate" not in astep("Recovery - rebuild")["run"], "engine: recovery rebuilds, audits and republishes the last good SHA and never migrates (D1 is never rolled back)")
check(astep("Recovery - verify")["env"]["GOOD_SHA"] == "${{ steps.good.outputs.sha }}" and "recover-verify" in astep("Recovery - verify")["run"], "engine: the restored deployment is verified against the LAST-GOOD sha")
check(astep("Recovery - checkout")["with"]["path"] == "recover" and astep("Recovery - checkout")["with"]["persist-credentials"] is False, "engine: the last-good tree is checked out beside the failed one, without persisting the token")
check(aidx("Failed before or during publish") < aidx("Published but smoke failed") < aidx("Recovery - find") < aidx("Recovery - record") < aidx("Rollback - restore"), "engine: failure reporting precedes recovery; recovery is recorded accurately")
check("preview_mutated" in str(astep("Failed before or during publish")["env"]) and "failure()" in astep("Failed before or during publish")["if"], "engine: 'unchanged' is only claimed when no mutation happened")
check(astep("Remove staged env files")["if"] == "always()", "engine: staged Preview/smoke env files are always removed")
check(aidx("Remove staged env files") == len(ASTEPS) - 1, "engine: cleanup is the last step")
flat = yaml.dump(ASTEPS)
check("CLOUDFLARE" not in flat and "secrets." not in yaml.dump(DEPLOY_ACTION), "engine: contains no credentials; the caller job owns which Cloudflare credentials exist")
check(set(DEPLOY_ACTION["inputs"]) >= {"target", "stage", "mode", "inputs_json", "preview_secrets", "smoke_username", "smoke_password", "deploy_outputs", "migration_outputs", "smoke_outputs"}, "engine interface: target, stage, mode, inputs_json, the secret-bearing inputs and the job hand-off blobs")
check(DEPLOY_ACTION["inputs"]["stage"]["default"] == "all", "engine: stage defaults to all (Shared Dev / persistent Dev callers are unchanged)")

# ---------------------------------------------------------------- 10. concurrency: the identity is the shared RESOURCE
cc = doc["concurrency"]
check(cc.get("queue") == "max" and "cancel-in-progress" not in cc, "workflow-level concurrency uses queue: max (no cancel-in-progress, which GitHub rejects together)")
g = lambda ref, inputs=None, event="push": render(cc["group"], scenario(event=event, ref=ref, inputs=dict({"app_name": "app"}, **(inputs or {}))))
check(g(MAIN) == "cfv2-prod-app", "group: main -> cfv2-prod-<app>")
check(g(FEAT, {"d1_migrations": True}) == "cfv2-nonprod-d1-app" == g("refs/heads/other", {"d1_migrations": True}), "group: ALL branches of a D1 app share one non-production D1 group (the group is the shared resource, not the branch)")
check(g(FEAT, {"d1_migrations": True}) != g(MAIN, {"d1_migrations": True}), "group: non-production D1 and Production never serialize each other")
check(g(FEAT, {"d1_migrations": True, "d1_lock_scope": "shared-db"}) == "cfv2-nonprod-d1-shared-db", "group: d1_lock_scope names the physical shared D1")
check(g(FEAT, {"d1_migrations": True, "d1_lock_scope": "shared-db", "app_name": "blog"}) == g(FEAT, {"d1_migrations": True, "d1_lock_scope": "shared-db", "app_name": "wiki"}),
      "REGRESSION: two apps with the same d1_lock_scope but DIFFERENT app_name get the SAME group (app_name no longer splits a shared D1)")
check(g(MAIN, {"d1_migrations": True, "d1_lock_scope": "shared-db", "app_name": "blog"}) == g(MAIN, {"d1_migrations": True, "d1_lock_scope": "shared-db", "app_name": "wiki"}) != g(FEAT, {"d1_migrations": True, "d1_lock_scope": "shared-db"}),
      "group: apps sharing a Production D1 also share a Production group, distinct from the non-production one")
check(g(FEAT, {"d1_migrations": True, "app_name": "blog"}) != g(FEAT, {"d1_migrations": True, "app_name": "wiki"}), "group: without d1_lock_scope each app keeps its own D1 group")
check(g(FEAT, {"shared_dev_on_branch_push": True}) == "cfv2-shared-dev-app" == g("refs/heads/other", {"shared_dev_on_branch_push": True}), "group: all branches of a non-D1 app with Shared Dev queue on one group (no concurrent Dev deploys)")
check(g(FEAT, {"shared_dev_on_branch_push": True, "d1_migrations": True}) == "cfv2-nonprod-d1-app", "group: a D1 app keeps the one shared D1 group (covers Preview and Dev migrations)")
check(g(FEAT) != g("refs/heads/other") and g(FEAT).startswith("cfv2-app-preview-"), "group: D1-less, Dev-less branches stay independent")
OFF = {"preview_on_branch_push": False, "shared_dev_on_branch_push": False, "d1_migrations": True}
check(g(FEAT, OFF).startswith("cfv2-app-preview-") and g(FEAT, OFF) != g("refs/heads/other", OFF), "group: CI-only runs (both channels off) of different branches are NOT serialized on the shared D1 group even with d1_migrations")
check(g(FEAT, dict(OFF, preview_on_branch_push=True)) == "cfv2-nonprod-d1-app" == g("refs/heads/other", dict(OFF, preview_on_branch_push=True)), "group: Native Preview on + D1 migrations -> shared non-production D1 group")
check(g(FEAT, dict(OFF, shared_dev_on_branch_push=True)) == "cfv2-nonprod-d1-app", "group: Shared Dev on + D1 migrations -> shared non-production D1 group")
check(g(FEAT, dict(OFF, preview_on_branch_push=True, shared_dev_on_branch_push=True)) == g(FEAT, dict(OFF, shared_dev_on_branch_push=True)), "group: both channels on share ONE D1 group (Preview/Dev migration serialization kept)")
check(g(FEAT, dict(OFF, deployment_strategy="production-only")).startswith("cfv2-app-preview-"), "group: production-only has no non-production channel -> no shared D1 serialization")
check(g(MAIN, OFF) == "cfv2-prod-app" and not g(MAIN, OFF).startswith("cfv2-nonprod"), "group: Production lock stays separate from the non-production lock")
check(g(FEAT, dict(OFF, shared_dev_on_branch_push=True, d1_migrations=False)) == "cfv2-shared-dev-app", "group: Shared Dev without a D1 keeps the one-Dev-Worker group")
check(g(MAIN, {"shared_dev_on_branch_push": True}) == "cfv2-prod-app", "group: Production is never serialized behind Dev")
check(g("refs/pull/1/merge", event="pull_request").startswith("cfv2-run-") and g("refs/heads/x", event="delete").startswith("cfv2-run-"), "group: PR / delete runs get a private group")
check(g("refs/heads/dev", {"dispatch_target": "persistent-dev"}, "workflow_dispatch").startswith("cfv2-run-"), "group: persistent-dev dispatch does not queue behind deploys")
check(g("refs/tags/v1", event="push").startswith("cfv2-run-"), "group: tag pushes get a private group")
check("github.workflow" not in cc["group"] and all(g(r).startswith("cfv2-") for r in (MAIN, FEAT)), "group has a static prefix and never uses github.workflow (no caller/callee deadlock)")
import subprocess  # noqa: E402


def js_group(scope, app):
    """Deploy/rollback group the REAL descriptor builder (delivery.mjs) assigns persistent-dev."""
    code = "import('./actions/cf-preview/delivery.mjs').then(m=>console.log(m.targetDescriptor({kind:'persistent-dev',app:process.argv[1],runId:1,repo:'o/r',lockScope:process.argv[2]}).deploy_group))"
    return subprocess.run(["node", "-e", code, app, scope], capture_output=True, text=True, check=True).stdout.strip()


check(djobs["deploy"]["concurrency"]["group"] == "${{ fromJSON(inputs.descriptor).deploy_group }}" and djobs["rollback"]["concurrency"]["group"] == "${{ fromJSON(inputs.descriptor).rollback_group }}", "delivery jobs take their concurrency groups from the descriptor")
manual_group = lambda scope, app: render(mjob["concurrency"]["group"], Ctx({"github": {"repository": "o/r"}, "inputs": {"inputs_json": json.dumps({"app_name": app, "d1_lock_scope": scope})}}, {}))
for scope, app in (("shared-db", "blog"), ("", "blog"), ("", "wiki")):
    check(js_group(scope, app) == manual_group(scope, app), f"persistent-dev: the descriptor's Deploy/Rollback group equals the manual dispatch's group (scope={scope or '-'}, app={app}) - one serialisation domain per shared D1")
check(js_group("", "blog") != js_group("", "wiki"), "without a scope the Dev group is keyed by app")
for n in ("deploy", "rollback"):
    check(djobs[n]["concurrency"]["cancel-in-progress"] is False, f"{n}: job-level groups never cancel in-flight work")
for j in ("smoke", "migration") + tuple(VALIDATION):
    check("concurrency" not in (djobs.get(j) or jobs.get(j) or cjobs.get(j)), f"{j}: no job-level concurrency (a second, nested group could deadlock with the workflow-level one)")
check(mjob["concurrency"]["group"] == "cfv2-dev-${{ github.repository }}-${{ fromJSON(inputs.inputs_json).d1_lock_scope != '' && fromJSON(inputs.inputs_json).d1_lock_scope || fromJSON(inputs.inputs_json).app_name }}" and mjob["concurrency"]["cancel-in-progress"] is False, "manual persistent Dev uses the same job-level group (automatic and manual Dev never overlap)")
check(all(not g(r, {"d1_migrations": True}).startswith("cfv2-dev-") for r in (MAIN, FEAT)), "workflow-level groups can never equal the job-level Dev group")
ND_ON = {"preview_on_branch_push": False, "shared_dev_native_preview_on_branch_push": True}
check(g(FEAT, ND_ON) == "cfv2-shared-dev-app" == g("refs/heads/other", ND_ON), "group: Native Dev (fixed target `dev`) serializes ALL branches of the app on one group")
check(g(FEAT, dict(ND_ON, d1_migrations=True)) == "cfv2-nonprod-d1-app", "group: Native Dev + D1 migrations -> the shared non-production D1 group")
check(g(MAIN, ND_ON) == "cfv2-prod-app", "group: Production is never serialized behind Native Dev")
check("CANNOT lock a D1 shared with ANOTHER repository" in inputs_def["d1_lock_scope"]["description"], "the repository-scoped limit of GitHub concurrency is documented on the d1_lock_scope input")

# ---------------------------------------------------------------- 11. interface preserved / Hybrid / lifecycle / retention
check(all(k in inputs_def for k in ("app_url_dev", "dev_deploy_cmd", "dev_smoke_cmd", "build_env_dev", "dev_build_cmd", "dev_wrangler_env", "dev_worker_name", "dev_binding_allow", "dev_deny_resources")), "interface: Dev URL, deploy/smoke commands, build env, Wrangler env, identity and resource-policy inputs exist")
check(inputs_def["preview_url_suffix"]["default"] == "", "interface: preview_url_suffix is optional (Cloudflare-reported suffix by default)")
check(not {"CLOUDFLARE_DEV_API_TOKEN", "CLOUDFLARE_DEV_ACCOUNT_ID"} & set(doc[True]["workflow_call"]["secrets"]), "interface: dedicated Dev secrets removed")
OUTS = doc[True]["workflow_call"]["outputs"]
check({"deploy_url", "deploy_target", "version_id", "dev_url"} <= set(OUTS), "outputs: deploy_url, deploy_target, version_id, dev_url are preserved (existing consumers)")
check(set(OUTS) == {"deploy_url", "deploy_target", "version_id", "dev_url", "deploy_verified_url", "deploy_published_unverified", "dev_verified_url", "dev_published_unverified",
                    "deploy_verified", "deploy_smoke_level", "previous_version_id", "migration_status", "rollback_result", "deploy_gate_blocked", "phase_state",
                    "native_dev_url", "native_dev_verified_url", "native_dev_published_unverified", "native_dev_verified", "native_dev_smoke_level"}, "outputs: every pre-existing output is kept; the new ones are Native Dev additions only")
check(all("jobs.delivery_production.outputs." in OUTS[k]["value"] and "jobs.delivery_native_branch.outputs." in OUTS[k]["value"] for k in ("deploy_url", "deploy_verified", "version_id", "previous_version_id", "deploy_verified_url"))
      and not any("native_dev" in OUTS[k]["value"] or "persistent_dev" in OUTS[k]["value"] for k in ("deploy_url", "deploy_target", "version_id", "previous_version_id", "deploy_verified", "deploy_verified_url", "deploy_smoke_level")),
      "outputs: deploy_* keep their meaning (the Production / per-branch Preview delivery) - never Native Dev or persistent Dev")
check("delivery_persistent_dev" in OUTS["dev_url"]["value"] and "delivery_native_dev" in OUTS["native_dev_url"]["value"] and "delivery_native_dev" not in OUTS["dev_url"]["value"], "outputs: dev_* stay persistent Dev; native_dev_* are Native Dev")
check(all("outputs.smoke_json" in OUTS[k]["value"] and ".handoff" not in OUTS[k]["value"] for k in ("deploy_verified_url", "deploy_verified", "native_dev_verified", "dev_verified_url")), "outputs: *_verified come from the Smoke result only (the only place a deployment becomes verified)")
check(all(re.search(r"jobs\.plan\.outputs\.on_\w+ == 'true'", OUTS[k]["value"]) for k in ("deploy_url", "deploy_verified", "rollback_result")) and "jobs.migration.outputs.migration_status" in OUTS["migration_status"]["value"], "P1-A: every per-kind public output SELECTS its source by the plan (on_<kind>); migration_status is the ONE Migration job's result")
pd = mjob
MANUAL_IF = "(!cancelled() && inputs.pipeline_phase != 'verify' && github.event_name == 'workflow_dispatch' && inputs.dispatch_target == 'persistent-dev' && inputs.deployment_strategy == 'hybrid' && inputs.persistent_dev_ref != '' && github.ref == inputs.persistent_dev_ref)"
check(jobs["delivery_persistent_dev"]["if"].endswith(" || " + MANUAL_IF) and mjob["if"] == "inputs.manual" and pd["environment"] == "${{ fromJSON(inputs.inputs_json).app_name }}/dev",
      "persistent-dev keeps the approved-ref gate and protected Environment")
check(mjob["permissions"] == {"contents": "read", "deployments": "write"}, "manual persistent Dev keeps its narrower permissions (contents read, deployments write; no pull-requests write)")
check(all("!inputs.manual" in djobs[n]["if"] for n in AUTO_JOBS), "automatic deploy / smoke / rollback never run for the manual dispatch")
check(all(not ev(djobs[n]["if"], Ctx({"inputs": {"phase": "all", "manual": True}, "needs": {"deploy": {"result": "success", "outputs": {}}, "smoke": {"result": "failure", "outputs": {"smoke_status": "failure"}}}}, {})) for n in AUTO_JOBS)
      and ev(mjob["if"], Ctx({"inputs": {"manual": True}}, {})) and not ev(mjob["if"], Ctx({"inputs": {"manual": False}}, {})), "delivery: manual=true runs ONLY the manual job; manual=false never runs it")
check("recovery" not in yaml.dump(mjob).lower() and "rollback" not in yaml.dump(mjob).lower(), "manual persistent Dev has no automatic recovery step")
legacy = [x for x in pd["steps"] if x.get("if", "").startswith("fromJSON(inputs.inputs_json).dev_build_cmd == ''")]
engine_side = [x for x in pd["steps"] if x.get("if") == "fromJSON(inputs.inputs_json).dev_build_cmd != ''"]
check(len(legacy) == 3 and len(engine_side) == 1 and all("gh.sh" not in x.get("run", "") for x in legacy), "persistent-dev: legacy (dev_build_cmd empty) and engine (set) paths are mutually exclusive; legacy never writes the known-good ledger")
check(next(x for x in pd["steps"] if x.get("name") == "Deploy persistent Dev")["env"]["CMD"] == "${{ fromJSON(inputs.inputs_json).dev_deploy_cmd }}", "persistent-dev manual dispatch (legacy contract) still runs dev_deploy_cmd")
check("shared_dev_on_branch_push" not in ptxt, "persistent-dev does not depend on the automatic Shared Dev switch")
check("wrangler preview delete" not in "\n".join(s.get("run", "").replace("\\`wrangler preview delete", "") for s in ljobs["lifecycle"]["steps"] if "echo" not in s.get("run", "")), "branch delete never executes `wrangler preview delete` (Native Preview is retained)")
retire = next(s for s in ljobs["lifecycle"]["steps"] if "Retire GitHub ledger" in s.get("name", ""))
check('gh.sh" retire' in retire["run"] and "Branch exists again" in retire["run"] and "Name is owned by" in retire["run"], "branch delete retires only this branch's ledger (and not if recreated / owned by another ref)")
all_run = "\n".join(s_.get("run", "") for j in allj.values() for s_ in j.get("steps", [])) + "\n".join(s.get("run", "") for s in ASTEPS)
check("wrangler delete" not in all_run, "no job deletes a Worker; branch deletion retains the Shared Dev Worker and Native Previews")
check(not re.search(r"CLOUDFLARE_DEV|dev-deploy|shared_dev_on_branch_push|app_url_dev|dev_deploy_cmd", yaml.dump(ljobs["lifecycle"])), "branch-delete lifecycle never references Dev")

# ---------------------------------------------------------------- pipeline_phase: deferred verification (opt-in, default unchanged)
PINPUTS = doc[True]["workflow_call"]["inputs"]
check(PINPUTS["pipeline_phase"]["default"] == "all" and PINPUTS["phase_state"]["default"] == "{}" and PINPUTS["upstream_gate"]["default"] == "",
      "pipeline_phase: opt-in - the default is `all` (the unchanged single call), phase_state defaults to an empty object, upstream_gate to no gate")
check(not any(i.get("required") for n, i in PINPUTS.items() if n in ("pipeline_phase", "phase_state", "upstream_gate")), "pipeline_phase: no new required input (existing callers keep working)")
PS = lambda kinds=("production",), **extra: json.dumps(dict({"inputs": {"app_name": "app"}, "kinds": list(kinds), "deploy": {}, "migration": {}, "d1": {}, "group": "g"}, **extra))
for k in KINDS:
    kk = KK(k)
    w = jobs[f"delivery_{kk}"]["with"]
    c = lambda ph, deploy=None: scenario("push", MAIN, {"pipeline_phase": ph, "phase_state": json.dumps({"inputs": {"app_name": "app", "d1_migrations": True}, "kinds": [k], "deploy": {f"h_{kk}": deploy or ""}, "migration": {f"mj_{kk}": "MJ"}, "d1": {kk: "DB9"}, "group": "g"}), "upstream_gate": "failure"})
    check(render(w["handoff_json"], c("verify", '{"published":"true"}')) == '{"published":"true"}' and render(w["handoff_json"], c("deploy")) == "" and render(w["handoff_json"], c("all")) == "", f"{k}: handoff_json comes from phase_state ONLY for this kind and ONLY in a verify call")
    check(render(w["migration_json_in"], c("verify")) == "MJ", f"{k}: the migration result is this kind's own entry of phase_state (its D1 id travels in the plan's descriptor)")
    check(render(w["upstream_gate"], c("verify")) == "failure" and render(w["upstream_gate"], c("all")) == "" and render(w["phase"], c("verify")) == "verify", f"{k}: upstream_gate is forwarded only in a verify call")
PLAN_OK = {"gate": {"result": "skipped", "outputs": {}}, "plan": pl(("production",)), "migration": {"result": "skipped", "outputs": {}}}
check(runs("delivery_production", scenario("push", MAIN, {"pipeline_phase": "verify", "phase_state": PS()}, PLAN_OK)), "verify phase: the delivery call runs from the hand-off even though Gate / Deploy are not part of this call")
check(runs("delivery_production", scenario("push", MAIN, {"pipeline_phase": "deploy"}, {"gate": G_OK, "plan": pl(("production",)), "migration": {"result": "skipped", "outputs": {}}})), "deploy phase: the delivery call runs (deploy only), independent of the other Worker")
check(not runs("delivery_production", scenario("push", MAIN, {"pipeline_phase": "verify", "phase_state": PS()}, {"gate": {"result": "skipped", "outputs": {}}, "plan": pl(()), "migration": {"result": "skipped", "outputs": {}}})), "verify phase: no kinds in the hand-off -> nothing to verify (fail closed)")
dctx = lambda **i: Ctx({"inputs": dict({"phase": "all"}, **i), "needs": {"deploy": {"result": "success", "outputs": {}}, "smoke": {"result": "failure", "outputs": {"smoke_status": "failure"}}}}, {})
check(not ev(djobs["smoke"]["if"], dctx(phase="deploy")) and not ev(djobs["rollback"]["if"], dctx(phase="deploy")) and ev(djobs["deploy"]["if"], dctx(phase="deploy")) and not ev(djobs["deploy"]["if"], dctx(phase="verify")),
      "pipeline_phase: deploy phase = deploy only; verify phase = smoke + rollback only (no publish)")
for job in ["ci", "migration", "config-guard", "lifecycle"]:
    ctxv = scenario("push", MAIN, {"pipeline_phase": "verify", "phase_state": PS(), "deployment_strategy": "production-only", "shared_dev_on_branch_push": True, "d1_migrations": True}, dict(GREEN, plan=pl(("production",), True)))
    check(not runs(job, ctxv), f"verify phase: {job} never runs (no CI, Migration, Deploy or publish in the verify call)")
check(runs("release", scenario("push", MAIN, {"release_cmd": "x", "pipeline_phase": "verify"}, {"delivery_production": dout("production")})) and not runs("release", scenario("push", MAIN, {"release_cmd": "x", "pipeline_phase": "verify"}, {"delivery_production": dout("production", smoke_json=json.dumps({"deployed_and_verified": "false"}))})), "verify phase: Release runs only from a content-verified Production smoke")
ps_val = OUTS["phase_state"]["value"]
check(ps_val.count("toJSON(inputs)") == 1 and '"kinds"' in ps_val and '"group"' in ps_val and '"descriptors"' in ps_val and '"d1"' not in ps_val and all(f"jobs.delivery_{KK(k)}.outputs.handoff" in ps_val and f"jobs.delivery_{KK(k)}.outputs.migration_used" in ps_val for k in KINDS),
      "phase_state hands over the inputs once, the enabled kinds, each kind's OWN deploy / migration result, the concurrency group and the target descriptors (which already carry each D1 id: no second per-kind D1 map)")
check("jobs.plan.outputs.group" in ps_val and ps_val.count("github.ref") == 0 and "cfv2-prod" not in ps_val, "phase_state takes the lock identity from the Plan's canonical calculation - the lock expression is NOT duplicated into the output")
grp_expr = re.search(r"concurrency:\n  group: \$\{\{ (.*) \}\}\n  queue: max", text_all).group(1)
VPFX = "inputs.pipeline_phase == 'verify' && (fromJSON(inputs.phase_state).group || format('cfv2-run-{0}', github.run_id)) || "
check(grp_expr.startswith(VPFX) and "phase_state" not in grp_expr[len(VPFX):] and grp_expr.count("fromJSON") == 1,
      "concurrency: ONE scheduling-time expression for the deploy call (no copy for verify); a verify call schedules under the group its deploy call recorded")
import itertools  # noqa: E402


def lock_cases():
    base = {"app_name": "blog", "deployment_strategy": "hybrid", "preview_on_branch_push": True, "shared_dev_on_branch_push": False, "shared_dev_native_preview_on_branch_push": False, "d1_migrations": False, "d1_lock_scope": "", "dispatch_target": ""}
    for event, ref in (("push", "refs/heads/main"), ("push", "refs/heads/feat/x"), ("workflow_dispatch", "refs/heads/feat/x"), ("workflow_dispatch", "refs/heads/dev"), ("pull_request", "refs/pull/1/merge"), ("push", "refs/tags/v1")):
        for strat, pre, sd, nd, d1, scope, dt in itertools.product(("hybrid", "production-only"), (True, False), (True, False), (True, False), (True, False), ("", "shared-db"), ("", "persistent-dev")):
            if not (event == "workflow_dispatch" or dt == ""):
                continue
            yield event, ref, dict(base, deployment_strategy=strat, preview_on_branch_push=pre, shared_dev_on_branch_push=sd, shared_dev_native_preview_on_branch_push=nd, d1_migrations=d1, d1_lock_scope=scope, dispatch_target=dt)


cases = list(lock_cases())
GRP = "${{ " + grp_expr + " }}"
yaml_deploy = [render(GRP, scenario(e, r, dict(i, pipeline_phase="all"))) for e, r, i in cases]
yaml_verify = None  # filled below, once the canonical groups are known
js_code = ("import('./actions/cf-preview/delivery.mjs').then(async m=>{let t='';for await(const c of process.stdin)t+=c;const c=JSON.parse(t);console.log(JSON.stringify(c.map(([e,r,i])=>m.runLockGroup({event:e,ref:r,inputs:i,runId:'1'}))))})")
js_groups = json.loads(subprocess.run(["node", "-e", js_code], input=json.dumps(cases), capture_output=True, text=True, check=True).stdout)
norm = lambda g: re.sub(r"^cfv2-run-.*$", "cfv2-run-N", g)
check(len(cases) > 200 and [norm(g) for g in yaml_deploy] == [norm(g) for g in js_groups], f"concurrency: the workflow expression and delivery.mjs runLockGroup agree on all {len(cases)} event/ref/input scenarios (deploy call)")
yaml_verify = [render(GRP, scenario(e, r, {"app_name": "other", "d1_lock_scope": "ignored", "pipeline_phase": "verify", "phase_state": json.dumps({"inputs": i, "group": g})})) for (e, r, i), g in zip(cases, js_groups)]
check(yaml_verify == js_groups, "concurrency: a verify call schedules under exactly the canonical group its deploy call recorded - its own app_name / d1_lock_scope are ignored")
check(render(GRP, scenario("push", MAIN, {"pipeline_phase": "verify", "phase_state": "{}"})).startswith("cfv2-run-"), "concurrency: a verify call without a recorded group falls back to a private group (the Plan then fails it before any mutation)")
check(len({g for g in yaml_deploy if g.startswith("cfv2-prod-shared-db")}) == 1 and any(g == "cfv2-nonprod-d1-shared-db" for g in yaml_verify), "concurrency: the shared-D1 scope selects the lock in verify mode too")
def forged(group, inputs):  # the REAL plan-side validation (restoreDescriptors) over a forged / wrong recorded group
    code = ("import('./actions/cf-preview/delivery.mjs').then(m=>{try{m.restoreDescriptors({inputs:JSON.parse(process.argv[1]),kinds:['production'],group:process.argv[2],deploy:{},migration:{}},{repo:'o/r',runId:'7',event:'push',ref:'refs/heads/main'});console.log('accepted')}catch(e){console.log('rejected:'+e.message)}})")
    return subprocess.run(["node", "-e", code, json.dumps(inputs), group], capture_output=True, text=True, check=True).stdout.strip()
check(all(forged(g, {"app_name": "app"}).startswith("rejected:phase_state.group") or forged(g, {"app_name": "app"}).startswith("rejected:") for g in ("attacker-group", "cfv2-nonprod-d1-app", "cfv2-prod-other", "")) and "lock identity" in forged("cfv2-nonprod-d1-app", {"app_name": "app"}),
      "concurrency: a forged or wrong recorded group is rejected by the Plan (restoreDescriptors) BEFORE any mutation - it can only ever cost a failed verify run")
gate = astep("Cross-service gate")
check(gate["id"] == "gate" and "inputs.stage == 'verify'" in gate["if"] and "'success'" in gate["if"] and "'skipped'" in gate["if"] and "exit 1" in gate["run"] and aidx("Cross-service gate") < aidx("Stale-SHA guard (start)")
      and DEPLOY_ACTION["outputs"]["gate_blocked"]["value"].startswith("${{ steps.gate.outputs.blocked"),
      "engine: the cross-service gate runs before any smoke, accepts only success | skipped, fails the job otherwise and reports gate_blocked")
for n in ("smoke", "rollback"):
    w = eng[n]["with"]
    check("call_inputs_json" in w and (n == "rollback" or "upstream_gate" in w), f"{n}: passes its own inputs (identity check) to the engine")
check("CLOUDFLARE_API_TOKEN" not in yaml.dump(djobs["smoke"]) and not any("secrets.CLOUDFLARE" in str(st.get("with", {})) for st in djobs["smoke"]["steps"]), "the Smoke job (every kind) never receives Cloudflare credentials")
ENGINE_SH = open("actions/cf-preview/engine.sh").read()
check(ENGINE_SH.count('"$TOOLS_DIR/build.sh"') == 1 and ENGINE_SH.count('"$TOOLS_DIR/publish.sh"') == 1 and ENGINE_SH.count("run_build ") == 2 and ENGINE_SH.count("run_publish ") == 2,
      "engine: ONE audited build primitive and ONE publish primitive, shared by the normal run and the recovery rebuild (recovery only supplies its state dir, rebound env/version, no host re-check)")

# ---------------------------------------------------------------- superseded Production deployments are not "success + blank URL"
# GitHub appends `success` to the Environment deployment when the Deploy job ends, even when the stale guard skipped every
# publish step (hand-off: stale=true, published unset). The Smoke job (which runs after the Deploy job ended) must then
# append `inactive` to THAT deployment - for Production only, never for a published or failed deployment.
import tempfile as _tf
SUP = next((st for st in djobs["smoke"]["steps"] if st.get("name", "").startswith("Mark a superseded")), None)
check(SUP is not None, "Smoke: a step marks a superseded Production deployment inactive")
def sup_run(handoff, kind="production", env_name="app/prod", sha="abc1234"):
    if SUP is None:
        return None
    d = _tf.mkdtemp()
    open(f"{d}/gh", "w").write('#!/usr/bin/env bash\necho "gh $*" >> "$LOG"\ncase "$*" in *"deployments?environment"*) echo 77;; esac\n')
    os.chmod(f"{d}/gh", 0o755)
    env = {"PATH": f"{d}:/usr/bin:/bin", "LOG": f"{d}/log", "HANDOFF": handoff, "GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": sha, "KIND": kind, "ENV_NAME": env_name,
           "GITHUB_RUN_ID": "1", "GITHUB_SERVER_URL": "https://github.com", "GH_TOKEN": "x"}
    ran = bool(ev(SUP.get("if", "true"), Ctx({"steps": {"go": {"outputs": {"go": "false"}}}, "inputs": {"descriptor": json.dumps({"kind": kind, "environment": env_name})}}, {})))
    if not ran:
        return "skipped"
    r = subprocess.run(["bash", "-eo", "pipefail", "-c", SUP["run"]], env=env, capture_output=True, text=True)
    return r.returncode, open(f"{d}/log").read() if os.path.exists(f"{d}/log") else ""
if SUP is not None:
    rc_, log_ = sup_run('{"stale":"true","kind":"production"}')
    check(rc_ == 0 and "deployments?environment=app%2Fprod&sha=abc1234" in log_ and "deployments/77/statuses" in log_ and "state=inactive" in log_ and "Superseded" in log_, "Smoke (executed): a superseded Production run appends `inactive` to its newest Environment deployment at this SHA")
    check(sup_run('{"stale":"false","published":"true"}') in ("skipped",) or "statuses" not in (sup_run('{"stale":"false","published":"true"}')[1]), "Smoke (executed): a fresh run never marks its deployment inactive")
    check("statuses" not in sup_run('{}')[1], "Smoke (executed): an empty hand-off marks nothing")
    check(sup_run('{"stale":"true"}', kind="native-branch", env_name="app/preview") in ("skipped",) or "statuses" not in sup_run('{"stale":"true"}', kind="native-branch", env_name="app/preview")[1], "Smoke: Preview kinds keep their behaviour (only Production is touched)")
    check(djobs["smoke"]["permissions"].get("deployments") == "write" and "secrets" not in yaml.dump(SUP), "Smoke: the step needs only deployments:write and uses no secrets")


sys.exit(fail)
