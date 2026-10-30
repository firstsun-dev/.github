#!/usr/bin/env python3
"""Static + evaluated contract checks for _cf-worker-v2.yml and its composite actions (no GitHub needed).

Job `if:` conditions and credential `env:` expressions are EVALUATED by a small GitHub-expression
interpreter over an event matrix, so the DAG/gating/secret-isolation claims are exercised, not just grepped.
The composite actions (actions/cf-deploy, actions/cf-ci-check) are parsed and checked structurally; their
behaviour is executed by test-jobs.py. What this cannot prove (needs a real run): GitHub's own scheduling,
Environment protection rules, runner behaviour. See docs/cf-worker-v2-refactor.md "Verified vs not verified".
"""
import json
import pathlib
import re
import sys
import yaml

WF = ".github/workflows/_cf-worker-v2.yml"
DWF = ".github/workflows/_cf-worker-delivery.yml"
doc = yaml.safe_load(open(WF))
ddoc = yaml.safe_load(open(DWF))
jobs = doc["jobs"]
djobs = ddoc["jobs"]
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

# ---------------------------------------------------------------- 1. topology: Gate -> Plan -> Preflight -> Migration -> Delivery (per kind) -> Release
KINDS = ["production", "native-branch", "native-dev", "persistent-dev"]
KK = lambda k: k.replace("-", "_")
CALL_JOBS = {f"{s}_{KK(k)}" for s in ("preflight", "migration", "delivery") for k in KINDS}
check(set(jobs) == set(VALIDATION) | {"gate", "plan"} | CALL_JOBS | {"release", "config-guard", "lifecycle", "persistent-dev"},
      "job set: six validation jobs, Validation Gate, Delivery plan, one Preflight / Migration / Delivery CALL per kind (reusable workflow), Release + config-guard / lifecycle / manual persistent-dev handlers")
check(set(djobs) == {"preflight", "migrate", "deploy", "smoke", "rollback"}, "the internal delivery workflow has exactly: preflight, migrate, deploy, smoke, rollback (ONE target per call)")
for gone in ("dev-deploy", "dev-verify", "dev-rollback", "ci", "test", "prepare", "migrate", "test-gate", "preview", "production", "verify", "revert", "migration", "deploy", "smoke", "rollback"):
    check(gone not in jobs, f"obsolete / matrix job '{gone}' is gone from the public workflow (no matrix output merging)")
check([jobs[j]["name"] for j in VALIDATION] == [JOB_NAME[j] for j in VALIDATION], "validation jobs are named Lint, Typecheck, Unit Tests, Integration Tests, Build Validation, E2E")
check(jobs["gate"]["name"] == "Validation Gate" and jobs["plan"]["name"] == "Delivery plan", "Validation Gate and Delivery plan are named jobs")


def needs_of(name, js=None):
    n = (js or jobs)[name].get("needs", [])
    return [n] if isinstance(n, str) else list(n or [])


for j in VALIDATION:
    check(needs_of(j) == [], f"{j}: no needs (independent, schedulable immediately)")
check(set(needs_of("gate")) == set(VALIDATION), "Validation Gate needs exactly the six validation jobs")
check(needs_of("plan") == ["gate"], "Delivery plan needs only the Validation Gate")
PREFLIGHTS = {f"preflight_{KK(k)}" for k in KINDS}
for k in KINDS:
    check(set(needs_of(f"preflight_{KK(k)}")) == {"gate", "plan"}, f"preflight_{KK(k)}: needs only the Gate and the plan (no other kind's delivery)")
    check(set(needs_of(f"migration_{KK(k)}")) == {"plan"} | PREFLIGHTS, f"migration_{KK(k)}: needs the plan and the Preflight of EVERY kind (no D1 mutation while any enabled target is unvalidated) - and NO Deploy / Smoke / Rollback")
check(set(needs_of("delivery_production")) == {"gate", "plan", "migration_production"}, "delivery_production: Gate, plan and its own D1 migration only")
check(set(needs_of("delivery_native_branch")) == {"gate", "plan", "migration_native_branch"}, "delivery_native_branch: Gate, plan and its own D1 migration only")
for k in ("native_dev", "persistent_dev"):
    check(set(needs_of(f"delivery_{k}")) == {"gate", "plan", f"migration_{k}", "migration_native_branch"}, f"delivery_{k}: Gate, plan and the migration calls of ITS D1 (own, or the shared-D1 owner) only")
check(not any(n.startswith(("delivery_", "preflight_")) for k in KINDS for n in needs_of(f"delivery_{KK(k)}")), "P2-A: NO Delivery call waits for another kind's Deploy / Smoke / Rollback (or any Preflight)")
check(not any(n.startswith("delivery_") for j in jobs if j.startswith(("preflight_", "migration_")) for n in needs_of(j)), "Preflight / Migration never wait for any Delivery call (independent stages)")
check(set(needs_of("release")) == {"delivery_production"}, "Release reads ONLY the Production delivery call; Dev kinds can never gate it")
check(all("strategy" not in jobs[j] for j in CALL_JOBS | {"gate", "plan", "release"}), "P1-A: no matrix anywhere in the delivery graph (outputs are never merged across legs)")
check(all("strategy" not in j for j in djobs.values()), "P1-A: no matrix in the internal delivery workflow either")
check(set(needs_of("smoke", djobs)) == {"deploy"} and set(needs_of("rollback", djobs)) == {"deploy", "smoke"} and needs_of("deploy", djobs) == [], "delivery workflow: Smoke needs only THIS call's Deploy; Rollback needs this call's Deploy and Smoke")
DELIVERY_WF = "./.github/workflows/_cf-worker-delivery.yml"
check(all(jobs[j]["uses"] == DELIVERY_WF for j in CALL_JOBS), "every per-kind call is a STATIC `uses: ./.github/workflows/_cf-worker-delivery.yml` of the SAME internal workflow (one delivery implementation)")
check(not any("uses" in j for j in djobs.values()), "nesting depth: the delivery workflow calls no further reusable workflow (caller -> _cf-worker-v2 -> _cf-worker-delivery = 3 levels, limit 10)")
CALL_STAGE = {"preflight": "preflight", "migration": "migrate", "delivery": "deliver"}
LEG = {"production": {"kind": "production", "target": "prod", "environment": "app/prod"}, "native-branch": {"kind": "native-branch", "target": "preview", "environment": "app/preview"},
       "native-dev": {"kind": "native-dev", "target": "preview", "environment": "app/native-dev"}, "persistent-dev": {"kind": "persistent-dev", "target": "dev", "environment": "app/dev"}}
for s, st in CALL_STAGE.items():
    for k in KINDS:
        w = jobs[f"{s}_{KK(k)}"]["with"]
        check(w["stage"] == st and w["kind"] == k, f"{s}_{KK(k)}: stage={st}, kind={k} are static literals (bound once, not from application env)")
        check(w["descriptor"] == "${{ toJSON(fromJSON(needs.plan.outputs.descriptors)['" + k + "']) }}" and not set(w) & {"target", "environment", "d1_expected_id", "d1_owner_kind", "deploy_group", "rollback_group"},
              f"{s}_{KK(k)}: ONE target descriptor of the plan replaces the per-call target / environment / D1 / owner / concurrency-group copies")
RANK = {"none": 0, "read": 1, "write": 2}
need = {}
for dj in djobs.values():
    for p_, v in dj.get("permissions", {}).items():
        need[p_] = max(need.get(p_, 0), RANK[v])
for n, j in jobs.items():
    if "uses" not in j:
        continue
    cp = j.get("permissions", {})
    check(all(RANK[cp.get(p_, "none")] >= r_ for p_, r_ in need.items()),
          f"{n}: the calling job's permissions cover EVERY job of the called workflow (GitHub validates the ceiling for all jobs, including ones skipped by `if` - a lower ceiling is a startup_failure; found by the real-runner scratch run)")
check(len(text_all.splitlines()) <= 1500, f"the workflow stays thin ({len(text_all.splitlines())} lines)")

# ---------------------------------------------------------------- 2. one deployment implementation
ENGINE_USE = "./.cf-tools/actions/cf-deploy"
engine_jobs = {n: [s for s in j.get("steps", []) if s.get("uses") == ENGINE_USE] for n, j in {**{f"d.{k}": v for k, v in djobs.items()}, **jobs}.items()}
check({n for n, s in engine_jobs.items() if s} == {"d.preflight", "d.migrate", "d.deploy", "d.smoke", "d.rollback", "persistent-dev"} and all(len(s) == 1 for s in engine_jobs.values() if s),
      "the five delivery-workflow jobs and the manual persistent Dev each call the SAME delivery engine action exactly once (no second deployment implementation)")
eng = {n.replace("d.", ""): s[0] for n, s in engine_jobs.items() if s}
check({n: eng[n]["with"]["stage"] for n in djobs} == {"preflight": "preflight", "migrate": "migrate", "deploy": "publish", "smoke": "verify", "rollback": "rollback"}, "each delivery job selects its engine stage (preflight / migrate / publish / verify / rollback)")
check(all(eng[n]["with"]["target"] == "${{ fromJSON(inputs.descriptor).target }}" and eng[n]["with"]["kind"] == "${{ inputs.kind }}" and eng[n]["with"]["descriptor"] == "${{ inputs.descriptor }}" for n in djobs), "the engine target comes from the call's validated descriptor, kind from the static input; the engine re-validates the descriptor")
check(eng["persistent-dev"]["with"]["target"] == "dev" and eng["persistent-dev"]["with"]["mode"] == "manual", "manual persistent Dev binds target=dev, mode=manual")
check(all(eng[n]["with"]["inputs_json"] == "${{ inputs.inputs_json }}" for n in djobs) and all(eng[n]["with"]["d1_expected_id"] == "${{ fromJSON(inputs.descriptor).d1_expected_id }}" for n in djobs),
      "engine inputs are passed as one JSON data blob; every stage binds the plan's D1 id (preflight, migrate, publish, verify, rollback)")
VERIFY_BLOB = "${{ inputs.pipeline_phase == 'verify' && toJSON(fromJSON(inputs.phase_state).inputs) || toJSON(inputs) }}"
check(all(jobs[f"delivery_{KK(k)}"]["with"]["inputs_json"] == VERIFY_BLOB for k in KINDS), "delivery calls: the verify call reads the DEPLOY call's inputs")
IMPL = re.compile(r"wrangler|migrate\.sh|publish\.sh|build\.sh|engine\.sh|deploy\.sh|smoke\.sh|d1 migrations|versions (upload|deploy)")
for n, j in {**{f"d.{k}": v for k, v in djobs.items()}, **jobs}.items():
    runs_txt = "\n".join(ln for s in j.get("steps", []) for ln in s.get("run", "").splitlines() if "echo" not in ln)
    check(not IMPL.search(runs_txt), f"{n}: no deployment implementation inline in YAML")

# ---------------------------------------------------------------- 3. gating over the event matrix
for name in VALIDATION:
    check(runs(name, scenario("push", FEAT)), f"{name}: runs on a feature push")
    check(runs(name, scenario("push", MAIN)), f"{name}: runs on a main push")
    check(not runs(name, scenario("delete", FEAT, ev_ref_type="branch")), f"{name}: not on branch delete")
    check(not runs(name, scenario("pull_request", "refs/pull/1/merge", head_repo="o/r")), f"{name}: same-repo PR is covered by the push run")
    check(runs(name, scenario("pull_request", "refs/pull/1/merge", head_repo="fork/r")), f"{name}: fork PR runs (credential-free)")
    check(not runs(name, scenario("workflow_dispatch", "refs/heads/dev", {"dispatch_target": "persistent-dev"})), f"{name}: not on persistent-dev dispatch")
for name, inp in CMD_INPUT.items():
    check(not runs(name, scenario("push", FEAT, {inp: ""})), f"{name}: intentionally skipped when {inp} is empty")
    check(runs(name, scenario("push", FEAT, {inp: "x"})), f"{name}: runs when {inp} is configured")
check(runs("build", scenario("push", FEAT, {"build_cmd": ""})), "build: build_cmd is required, never optional")

gate_ok = lambda **kw: scenario(**dict(dict(event="push", ref=FEAT, needs=GREEN), **kw))
check(runs("gate", gate_ok()) and runs("gate", gate_ok(ref=MAIN)), "Validation Gate: evaluated for feature and main pushes")
check(not runs("gate", gate_ok(event="pull_request", ref="refs/pull/1/merge", head_repo="o/r")) and not runs("gate", gate_ok(event="delete")), "Validation Gate: not for same-repo PRs / branch deletes")
check(not runs("gate", scenario("workflow_dispatch", "refs/heads/dev", {"dispatch_target": "persistent-dev"}, GREEN)), "Validation Gate: not for the manual persistent-dev dispatch")
check(not runs("gate", gate_ok(inputs={"pipeline_phase": "verify"})), "Validation Gate: not in a verify call")
gate_step = jobs["gate"]["steps"][0]
check(all(f"{v.upper()}_CONFIGURED" in gate_step["env"] for v in VALIDATION) and 'success' in gate_step["run"] and 'skipped' in gate_step["run"], "Validation Gate: configured jobs must succeed; unconfigured ones must be skipped (fail closed otherwise)")
check(all(f"needs.{v}.result" in gate_step["env"][v.upper()] for v in VALIDATION), "Validation Gate reads the result of every validation job")

plan_if = lambda **kw: runs("plan", scenario(**dict(dict(event="push", ref=FEAT, needs=res(gate="success")), **kw)))
check(plan_if() and plan_if(ref=MAIN), "plan: runs after a passed gate on feature and main pushes")
for bad in ("failure", "cancelled", "skipped"):
    check(not plan_if(needs=res(gate=bad)), f"plan: blocked when the Validation Gate is {bad}")
check(not plan_if(event="pull_request", ref="refs/pull/1/merge") and not plan_if(event="delete") and not plan_if(ref="refs/tags/v1"), "plan: never for PRs, deletes or tags")
check(plan_if(inputs={"pipeline_phase": "verify", "phase_state": "{}"}, needs=res(gate="skipped")), "plan: a verify call plans from the hand-off, not from the gate")
# ---------------------------------------------------------------- call-job gating (plan outputs are the only per-kind switch)
# Routing stub of the plan's descriptor: the call jobs only ROUTE it (delivery.test.mjs proves its content).
DESC_FIX = {"production": ("prod", "app/prod"), "native-branch": ("preview", "app/preview"), "native-dev": ("preview", "app/native-dev"), "persistent-dev": ("dev", "app/dev")}


def desc(k, d1="", owner="", repo="o/r", app="app", scope=""):
    g = f"cfv2-dev-{repo}-{scope or app}" if k == "persistent-dev" else f"cfv2-leg-1-{k}"
    return {"v": 1, "kind": k, "target": DESC_FIX[k][0], "environment": DESC_FIX[k][1], "deploy_group": g, "rollback_group": g if k == "persistent-dev" else g + "-rollback",
            "d1_expected_id": d1, "d1_owner_kind": owner, "owns_migration": bool(d1 and owner == k), "smoke_mode": "full", "phase": "all"}


def pl(kinds=("native_branch",), d1=False, owners=None, result="success", **extra):
    o = {"has_targets": str(bool(kinds)).lower(), "kinds": json.dumps([k.replace("_", "-") for k in kinds])}
    for k in ("production", "native_branch", "native_dev", "persistent_dev"):
        o[f"on_{k}"] = str(k in kinds).lower()
        o[f"owns_{k}"] = str(bool(d1) and (owners or {}).get(k, k == (kinds[0] if kinds else "")) is True).lower()
    o["descriptors"] = json.dumps({k.replace("_", "-"): desc(k.replace("_", "-"), "DB1" if d1 else "", ((owners or {}).get("owner_of") or kinds[0].replace("_", "-")) if d1 else "") for k in kinds})
    o.update(extra)
    return {"result": result, "outputs": o}


G_OK = {"result": "success", "outputs": {}}
import json  # noqa: E402
pf_if = lambda k, kinds=None, d1=True, gate="success", **kw: runs(f"preflight_{KK(k)}", scenario("push", kw.pop("ref", FEAT), dict({"d1_migrations": d1}, **kw.pop("inputs", {})), {"gate": {"result": gate, "outputs": {}}, "plan": pl(kinds or (KK(k),), d1)}))
check(pf_if("native-branch") and pf_if("production", ref=MAIN), "Preflight: runs for an enabled kind with d1_migrations after a passed gate")
check(not pf_if("native-branch", d1=False), "Preflight: not for D1-less apps (nothing to validate before a mutation)")
check(not pf_if("native-dev", kinds=("native_branch",)), "Preflight: only for ENABLED kinds (no phantom per-target path)")
check(not pf_if("native-branch", gate="failure") and not pf_if("native-branch", gate="skipped") and not pf_if("native-branch", inputs={"pipeline_phase": "verify"}), "Preflight: blocked by a failed Gate; never in a verify call")
mig_if = lambda k, kinds=None, d1=True, **kw: runs(f"migration_{KK(k)}", scenario("push", kw.pop("ref", FEAT), dict({"d1_migrations": d1}, **kw.pop("inputs", {})), {"plan": pl(kinds or (KK(k),), d1, **kw.pop("pk", {})), **{p: {"result": "success", "outputs": {}} for p in PREFLIGHTS}}))
check(mig_if("native-branch") and mig_if("production", ref=MAIN), "Migration: runs for the OWNER of a D1 when d1_migrations is on")
check(not mig_if("native-branch", d1=False), "Migration: D1-less app -> nothing to do, never blocks")
two = ("native_branch", "native_dev")
shared = {"owner_of": "native-branch"}
check(runs("migration_native_branch", scenario("push", FEAT, {"d1_migrations": True}, {"plan": pl(two, True, shared), **{p: G_OK for p in PREFLIGHTS}})), "Migration: the shared D1 is migrated by its OWNER (native-branch)")
check(not runs("migration_native_dev", scenario("push", FEAT, {"d1_migrations": True}, {"plan": pl(two, True, shared), **{p: G_OK for p in PREFLIGHTS}})), "P2-B: Native Dev sharing Native Branch's D1 has NO migration call of its own (one execution per physical D1)")
check(not mig_if("native-branch", inputs={"pipeline_phase": "verify"}) and not runs("migration_native_branch", scenario("push", FEAT, {"d1_migrations": True}, {"plan": pl(("native_branch",), True, result="failure"), **{p: G_OK for p in PREFLIGHTS}})), "Migration: never in a verify call or after a failed plan")
mg = jobs["migration_native_branch"]["with"]
check("needs.preflight_production.result" in mg["peers"] and "needs.preflight_persistent_dev.result" in mg["peers"] and mg["kinds"] == "${{ needs.plan.outputs.kinds }}", "Migration: hands the Preflight result of every kind + the enabled kinds to the call, which fails closed on anything but success")
mstep = djobs["migrate"]["steps"][0]
ACT_STEPS = DEPLOY_ACTION["runs"]["steps"]
gate_i = next(n for n, x in enumerate(ACT_STEPS) if x.get("id") == "migration_gate")
first_work = next(n for n, x in enumerate(ACT_STEPS) if "Stale-SHA guard (start)" in x.get("name", ""))
check(gate_i < first_work and ACT_STEPS[gate_i]["if"] == "inputs.stage == 'migrate' || inputs.stage == 'publish'" and "migration-gate" in ACT_STEPS[gate_i]["run"],
      "Migration gate: runs in the engine right after init - before the stale guard, setup, build, migration and publish - for the migrate and publish stages only")
check(eng["migrate"]["with"]["peers"] == "${{ inputs.peers }}" and eng["migrate"]["with"]["kinds"] == "${{ inputs.kinds }}" and djobs["migrate"]["steps"][0].get("uses") == "actions/checkout@v6",
      "Migration call: hands the enabled kinds and every kind's Preflight result to the engine gate (no inline shell decision in the workflow)")
dl_if = lambda k, kinds=None, gate="success", plan_result="success", inputs=None, **kw: runs(f"delivery_{KK(k)}", scenario("push", kw.pop("ref", FEAT), inputs or {}, {"gate": {"result": gate, "outputs": {}}, "plan": pl(kinds or (KK(k),), False, result=plan_result)}))
check(dl_if("native-branch") and dl_if("production", ref=MAIN) and dl_if("native-dev") and dl_if("persistent-dev"), "Delivery: runs when the gate passed and ITS kind is enabled")
check(not dl_if("native-dev", kinds=("native_branch",)) and not dl_if("production", kinds=("native_branch",), ref=FEAT), "Delivery: a disabled kind creates no delivery (no phantom paths)")
check(not dl_if("native-branch", gate="failure") and not dl_if("native-branch", gate="skipped") and not dl_if("native-branch", plan_result="failure"), "Delivery: blocked by a failed Validation Gate or a failed plan (invalid configuration deploys nothing)")
check(dl_if("native-branch", gate="skipped", inputs={"pipeline_phase": "verify"}), "Delivery: a verify call plans from the hand-off, not from the gate")
pub = next(x for x in djobs["deploy"]["steps"] if x.get("id") == "engine")["with"]
check(all(pub[k] == "${{ inputs." + k + " }}" for k in ("own_migration_result", "own_migration_json", "owner_migration_result", "owner_migration_json")) and djobs["deploy"]["outputs"]["migration_used"] == "${{ steps.engine.outputs.migration_used }}"
      and "run" not in djobs["deploy"]["steps"][0],
      "Deploy: the migration of THIS kind's D1 (own, or the shared-D1 owner's) is checked by the engine gate (per-D1 gating, never a global needs; delivery.test.mjs covers every refusal)")
sm_if = lambda inputs, deploy_res="success": bool(ev(djobs["smoke"]["if"], Ctx({"inputs": dict({"stage": "deliver", "phase": "all"}, **inputs), "needs": {"deploy": {"result": deploy_res, "outputs": {}}}}, {})))
check(sm_if({}) and sm_if({"phase": "verify"}) and not sm_if({"phase": "deploy"}) and not sm_if({"stage": "migrate"}) and sm_if({}, "failure"), "Smoke: runs in all and verify phases (even when Deploy failed: nothing published -> smoke_status none), never in the deploy phase or another stage")
rb_if = lambda status, inputs=None: bool(ev(djobs["rollback"]["if"], Ctx({"inputs": dict({"stage": "deliver", "phase": "all", "kind": "native-branch", "inputs_json": "{}"}, **(inputs or {})), "needs": {"smoke": {"result": "x", "outputs": {"smoke_status": status}}}}, {})))
check(rb_if("failure") and not rb_if("success") and not rb_if("none") and not rb_if("") and not rb_if("failure", {"phase": "deploy"}), "Rollback: only after THIS call's Smoke FAILED (conditional, never on the happy path, never in the deploy phase)")
rgo = djobs["rollback"]["steps"][0]
check(rgo["id"] == "go" and "POLICY_OK" in rgo["run"] and "gate_blocked" in rgo["run"] and "published" in rgo["run"],
      "Rollback guard: this run published + no blocked cross-service gate + (Production only) policy automatic")
check("inputs.kind != 'production' ||" in rgo["env"]["POLICY_OK"] and "production_rollback_policy == 'automatic'" in rgo["env"]["POLICY_OK"], "Rollback: the Production policy applies to Production only; Preview / Dev recovery is target-owned")


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
check(runs("persistent-dev", scenario("workflow_dispatch", "refs/heads/dev", **dev_ok)) and not runs("persistent-dev", scenario("workflow_dispatch", "refs/heads/other", **dev_ok))
      and not runs("persistent-dev", scenario("push", "refs/heads/dev", **dev_ok)), "persistent Dev: approved ref via dispatch only, never on push")
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
for n in ("preflight", "migrate", "deploy", "rollback"):
    env = djobs[n]["env"]
    c = Ctx({"secrets": SECRETS, "github": {}}, SECRETS)
    check(render(env["CLOUDFLARE_API_TOKEN"], c) == "PROD-TOKEN" and render(env["CLOUDFLARE_ACCOUNT_ID"], c) == "PROD-ACCT", f"{n}: the account credentials")
    c = Ctx({"secrets": {}, "github": {}}, {})
    check(render(env["CLOUDFLARE_API_TOKEN"], c) == "", f"{n}: a missing account token stays EMPTY (no fallback)")
check("CLOUDFLARE" not in yaml.dump(djobs["smoke"]) and "secrets.CLOUDFLARE" not in yaml.dump(djobs["smoke"]), "Smoke Tests: NO Cloudflare credentials of any kind (it only probes the published URL)")
check(set(djobs["smoke"]["env"]) == {"GH_TOKEN", "HANDOFF"}, "Smoke Tests: the job env holds the GitHub token and the (data-only) hand-off, nothing else")
check("CLOUDFLARE_DEV" not in yaml.dump(djobs), "the delivery workflow never references the Dev credentials")
for s, extra in (("preflight", False), ("migration", False), ("delivery", True)):
    for k in KINDS:
        sec = jobs[f"{s}_{KK(k)}"]["secrets"]
        check(set(sec) == {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "PREVIEW_SECRETS"} | ({"SMOKE_USERNAME", "SMOKE_PASSWORD"} if extra else set()), f"{s}_{KK(k)}: passes exactly the secrets its stage needs (smoke credentials only to Delivery)")
for n in djobs:
    w = eng[n]["with"]
    for k in KINDS:
        c = Ctx({"inputs": {"kind": k, "descriptor": json.dumps(desc(k))}}, SECRETS)
        got = render(w["preview_secrets"], c)
        check((got == "K=v") == (k in ("native-branch", "native-dev")) and (k in ("native-branch", "native-dev") or "PREVIEW_SECRETS" not in c.secret_reads), f"{n}: Preview-only secrets reach {k} only if it is a Preview kind")
ptxt = yaml.dump(jobs["persistent-dev"])
check("secrets.CLOUDFLARE_API_TOKEN" in ptxt and "secrets.CLOUDFLARE_ACCOUNT_ID" in ptxt and "CLOUDFLARE_PREVIEW" not in ptxt and "PREVIEW_SECRETS" not in ptxt, "persistent-dev reuses account credentials, not Preview secrets")
check(not any(k in j.get("env", {}) for j in list(jobs.values()) + list(djobs.values()) for k in ("TARGET", "BUILD_ENV", "SMOKE_ENV", "DEV_ENV", "EXPECTED_SHA", "RUN_SHA")),
      "no job exports TARGET / BUILD_ENV / DEV_ENV / EXPECTED_SHA ...: the delivery target is bound by the engine action input, not by environment")
ENVEXPR = "${{ fromJSON(inputs.descriptor).environment }}"
check(all(djobs[n]["environment"]["name"] == ENVEXPR for n in ("migrate", "deploy", "rollback")), "migrate / deploy / rollback run in the descriptor's per-kind GitHub Environment (<app>/prod keeps its protection)")
check(all(render(ENVEXPR, Ctx({"inputs": {"descriptor": json.dumps(desc(k))}}, SECRETS)) == DESC_FIX[k][1] for k in KINDS), "descriptor environments are <app>/prod | preview | native-dev | dev")
check("environment" not in djobs["smoke"] and "environment" not in djobs["preflight"], "Smoke and Preflight: no Environment (no protection prompt; neither mutates anything)")
check(djobs["deploy"]["environment"]["url"] == "${{ steps.engine.outputs.url }}", "Deploy: the job shows the actual deployed URL (environment.url)")
check(jobs["persistent-dev"]["environment"] == "${{ inputs.app_name }}/dev", "manual persistent Dev runs in the <app>/dev Environment (same as the automatic persistent-dev kind)")
allj = {**jobs, **{f"d.{k}": v for k, v in djobs.items()}}
check(all(j.get("permissions", {}).get("contents", "read") == "read" for n, j in allj.items() if n != "release"), "no job except Release can write repository contents")
check(djobs["migrate"]["permissions"].get("deployments") == "read" and djobs["preflight"]["permissions"].get("deployments") == "read", "Preflight / Migration can READ (not write) Deployments: the Preview identity guard reads the ledger owner")
check(ddoc.get("permissions") == {"contents": "read"} and "concurrency" not in ddoc, "delivery workflow: read-only default token and NO workflow-level concurrency (the public workflow's group would deadlock)")
check("contents: write" not in yaml.dump({k: allj[k] for k in allj if k != "release"}) and not any(w in text_all for w in ("lock.sh", "cfv2-locks", "refs/cfv2")), "no custom Git-ref lock and no content-write permission outside Release")
for name in VALIDATION + ["lifecycle"]:
    text = yaml.dump(jobs[name])
    check("secrets." not in text and "CLOUDFLARE" not in text, f"{name}: no secrets and no Cloudflare credentials of any kind")
check("secrets.SMOKE_PASSWORD" not in yaml.dump({k: jobs[k] for k in VALIDATION}) and eng["smoke"]["with"]["smoke_password"] == "${{ secrets.SMOKE_PASSWORD }}" and "smoke_password" not in eng["deploy"]["with"] and "smoke_password" not in eng["migrate"]["with"] and "smoke_password" not in eng["preflight"]["with"],
      "smoke credentials reach only the engine actions that smoke (Smoke Tests, Rollback re-smoke, Dev)")
for n in ("preflight", "migrate", "deploy", "smoke", "rollback"):
    cks = [s for s in djobs[n]["steps"] if str(s.get("uses", "")).startswith("actions/checkout")]
    check(cks and all(s["with"].get("persist-credentials") is False for s in cks), f"{n}: checkouts do not persist the GitHub token into the workspace")
check(all(s["with"].get("persist-credentials") is False for s in jobs["persistent-dev"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout") and s.get("with", {}).get("path")), "persistent-dev: tooling checkout does not persist the GitHub token")

# ---------------------------------------------------------------- 6. cross-repository reusable workflow + local composite action references
action_uses = [(n, i, s, j) for n, j in allj.items() for i, s in enumerate(j.get("steps", [])) if str(s.get("uses", "")).startswith("./")]
check(action_uses and all("${{" not in s["uses"] and s["uses"].startswith("./.cf-tools/actions/") for _, _, s, _ in action_uses),
      "every local ACTION reference is a STATIC `./.cf-tools/actions/<name>` path (uses: cannot take an expression; `./` alone would resolve against the CALLER's workspace)")
for n, i, s, j in action_uses:
    name = s["uses"].split("/actions/", 1)[1]
    prior = [p for p in j["steps"][:i] if p.get("with", {}).get("path") == ".cf-tools"]
    sp = prior[-1]["with"].get("sparse-checkout", "") if prior else ""
    check(pathlib.Path(f"actions/{name}/action.yml").exists(), f"{n}: referenced action actions/{name}/action.yml exists in this repository")
    check(len(prior) == 1 and prior[0]["with"]["repository"] == "firstsun-dev/.github" and prior[0]["with"]["ref"] == "${{ inputs.tools_ref }}" and f"actions/{name}" in sp,
          f"{n}: firstsun-dev/.github is checked out at tools_ref into .cf-tools, including actions/{name}, BEFORE `uses: {s['uses']}`")
    first_ck = next(i_ for i_, p in enumerate(j["steps"]) if str(p.get("uses", "")).startswith("actions/checkout"))
    check(j["steps"][first_ck].get("with", {}).get("path") is None or n == "persistent-dev", f"{n}: the app checkout (workspace root) happens before the tooling checkout")
check(all(jobs[j]["with"]["tools_ref"] == "${{ inputs.tools_ref }}" for j in CALL_JOBS), "tools_ref: every per-kind call forwards the caller's tools_ref (the engine is fetched at the SAME pinned ref)")
check(set(ddoc[True]["workflow_call"]["inputs"]) >= {"stage", "kind", "descriptor", "tools_ref", "inputs_json"} and not set(ddoc[True]["workflow_call"]["inputs"]) & {"target", "environment", "deploy_group", "rollback_group", "d1_expected_id", "d1_owner_kind"} and ddoc[True]["workflow_call"]["inputs"]["tools_ref"].get("required") is True, "delivery workflow: tools_ref is a required input")
_ci = open("actions/cf-ci-check/action.yml").read()
check("rm -rf .cf-tools" not in _ci and "'.cf-tools/actions/cf-ci-check/action.yml' -delete" in _ci,
      "cf-ci-check never deletes its own action.yml (the runner re-reads it for the post step; Blog run 39722027184 failed that way)")
check(all("cf-preview" in (s["with"].get("sparse-checkout", "") if s.get("with") else "") for n in djobs for s in djobs[n]["steps"] if s.get("with", {}).get("path") == ".cf-tools"), "every delivery job fetches the engine scripts (actions/cf-preview) together with the action")
pins = [s["uses"] for j in allj.values() for s in j.get("steps", []) if str(s.get("uses", "")).startswith("firstsun-dev/.github/")]
check(all(re.fullmatch(r"firstsun-dev/\.github/actions/[a-z0-9-]+@v1", u) for u in pins), "remaining remote references are the existing pinned shared actions (setup / mailpit @v1)")
check("tools_ref" in inputs_def and inputs_def["tools_ref"].get("required") is True, "tools_ref stays a required input (must equal the ref the caller pins this workflow at)")

# ---------------------------------------------------------------- 7. validation jobs use the common CI action
CMDS = {"lint": "lint_cmd", "typecheck": "typecheck_cmd", "unit": "test_unit_cmd", "integration": "test_integration_cmd", "build": "build_cmd"}
for j, inp in CMDS.items():
    steps = jobs[j]["steps"]
    ci = [s for s in steps if s.get("uses") == "./.cf-tools/actions/cf-ci-check"]
    t = yaml.dump(jobs[j])
    others = [i for k, i in CMDS.items() if k != j] + ["test_e2e_cmd"]
    check(len(ci) == 1 and ci[0]["with"]["cmd"] == "${{ inputs." + inp + " }}" and not any(f"inputs.{o}" in t for o in others), f"{j}: runs {inp} exactly once through actions/cf-ci-check, and no other job's command")
    check(ci[0]["with"]["title"] == JOB_NAME[j], f"{j}: the check summary carries its own name")
    check(not any("playwright" in yaml.dump(s_).lower() for s_ in steps) and "upload-artifact" not in t and "download-artifact" not in t, f"{j}: installs no browsers and uses no artifacts")
    check(jobs[j].get("continue-on-error") is None, f"{j}: failures are never hidden by continue-on-error")
    check("cf-ci-check" in next(s for s in steps if s.get("with", {}).get("path") == ".cf-tools")["with"]["sparse-checkout"], f"{j}: fetches the CI action it uses")
ci_steps = CI_ACTION["runs"]["steps"]
check(ci_steps[0]["name"].startswith("Import validation env") and "parse-env.mjs\" TARGET_BUILD_ENV" in ci_steps[0]["run"] and ci_steps[0]["run"].index("TARGET_BUILD_ENV") < ci_steps[0]["run"].index("CI_ENV"),
      "cf-ci-check imports the target build env, then ci_env, through the safe importer (precedence preserved)")
ck = next(s for s in ci_steps if s.get("id") == "check")
check(ck["env"]["CMD"] == "${{ inputs.cmd }}" and ck["run"] == 'bash -c "$CMD"' and "continue-on-error" not in ck, "cf-ci-check runs its one command via env (never interpolated) and never hides a failure")
check(any("GITHUB_STEP_SUMMARY" in s.get("run", "") and s.get("if") == "always()" for s in ci_steps), "cf-ci-check always writes a short Step Summary")
check(any(s.get("uses", "").startswith("firstsun-dev/.github/actions/mailpit") and s.get("if") == "inputs.use_mailpit == 'true'" for s in ci_steps) and
      all(("use_mailpit" in str(next(s for s in jobs[j]["steps"] if s.get("uses") == "./.cf-tools/actions/cf-ci-check")["with"])) == (j in ("unit", "integration")) for j in CMDS),
      "Mailpit starts only for Unit and Integration tests")
check("build_env_prod" in ci_steps[0]["env"]["TARGET_BUILD_ENV"] and "build_env_preview" in ci_steps[0]["env"]["TARGET_BUILD_ENV"], "validation jobs use the Production build env on main and the Preview build env elsewhere")
e2e_text = yaml.dump(jobs["e2e"])
check("test_e2e_build_cmd" in e2e_text and "test_e2e_env" in e2e_text and "inputs.build_cmd" not in e2e_text, "E2E builds with its own test build command and env")
uploads = [(j, s) for j, job in allj.items() for s in job.get("steps", []) if "upload-artifact" in str(s.get("uses", ""))]
check("download-artifact" not in text_all + open(DWF).read() and sorted(j for j, _ in uploads) == ["d.smoke", "e2e"], "no build artifact hand-off between jobs; the only uploads are the failure-only E2E and smoke reports")
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
manual_group = lambda scope, app: render(jobs["persistent-dev"]["concurrency"]["group"], scenario(inputs={"app_name": app, "d1_lock_scope": scope}))
for scope, app in (("shared-db", "blog"), ("", "blog"), ("", "wiki")):
    check(js_group(scope, app) == manual_group(scope, app), f"persistent-dev: the descriptor's Deploy/Rollback group equals the manual dispatch's group (scope={scope or '-'}, app={app}) - one serialisation domain per shared D1")
check(js_group("", "blog") != js_group("", "wiki"), "without a scope the Dev group is keyed by app")
for n in ("deploy", "rollback"):
    check(djobs[n]["concurrency"]["cancel-in-progress"] is False, f"{n}: job-level groups never cancel in-flight work")
for j in ("smoke", "preflight", "migrate") + tuple(VALIDATION):
    check("concurrency" not in (djobs.get(j) or jobs.get(j)), f"{j}: no job-level concurrency (a second, nested group could deadlock with the workflow-level one)")
check(jobs["persistent-dev"]["concurrency"]["group"] == "cfv2-dev-${{ github.repository }}-${{ inputs.d1_lock_scope != '' && inputs.d1_lock_scope || inputs.app_name }}", "manual persistent Dev uses the same job-level group (automatic and manual Dev never overlap)")
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
check(all(re.search(r"jobs\.plan\.outputs\.on_\w+ == 'true'", OUTS[k]["value"]) for k in ("deploy_url", "deploy_verified", "rollback_result")) and "jobs.plan.outputs.owns_" in OUTS["migration_status"]["value"], "P1-A: every per-kind public output SELECTS its source by the plan (on_<kind> / owns_<kind>), never by which string happens to be non-empty")
pd = jobs["persistent-dev"]
check(pd["if"] == "inputs.pipeline_phase != 'verify' && github.event_name == 'workflow_dispatch' && inputs.dispatch_target == 'persistent-dev' && inputs.deployment_strategy == 'hybrid' && inputs.persistent_dev_ref != '' && github.ref == inputs.persistent_dev_ref" and pd["environment"] == "${{ inputs.app_name }}/dev",
      "persistent-dev keeps the approved-ref gate and protected Environment")
legacy = [x for x in pd["steps"] if x.get("if", "").startswith("inputs.dev_build_cmd == ''")]
engine_side = [x for x in pd["steps"] if x.get("if") == "inputs.dev_build_cmd != ''"]
check(len(legacy) == 3 and len(engine_side) == 2 and all("gh.sh" not in x.get("run", "") for x in legacy), "persistent-dev: legacy (dev_build_cmd empty) and engine (set) paths are mutually exclusive; legacy never writes the known-good ledger")
check(next(x for x in pd["steps"] if x.get("name") == "Deploy persistent Dev")["env"]["CMD"] == "${{ inputs.dev_deploy_cmd }}", "persistent-dev manual dispatch (legacy contract) still runs dev_deploy_cmd")
check("shared_dev_on_branch_push" not in ptxt, "persistent-dev does not depend on the automatic Shared Dev switch")
check("wrangler preview delete" not in "\n".join(s.get("run", "").replace("\\`wrangler preview delete", "") for s in jobs["lifecycle"]["steps"] if "echo" not in s.get("run", "")), "branch delete never executes `wrangler preview delete` (Native Preview is retained)")
retire = next(s for s in jobs["lifecycle"]["steps"] if "Retire GitHub ledger" in s.get("name", ""))
check('gh.sh" retire' in retire["run"] and "Branch exists again" in retire["run"] and "Name is owned by" in retire["run"], "branch delete retires only this branch's ledger (and not if recreated / owned by another ref)")
all_run = "\n".join(s_.get("run", "") for j in allj.values() for s_ in j.get("steps", [])) + "\n".join(s.get("run", "") for s in ASTEPS)
check("wrangler delete" not in all_run, "no job deletes a Worker; branch deletion retains the Shared Dev Worker and Native Previews")
check(not re.search(r"CLOUDFLARE_DEV|dev-deploy|shared_dev_on_branch_push|app_url_dev|dev_deploy_cmd", yaml.dump(jobs["lifecycle"])), "branch-delete lifecycle never references Dev")

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
    check(render(w["migration_json_in"], c("verify")) == "MJ", f"{k}: the migration result is this kind's own entry of phase_state (its D1 id travels in the plan's descriptor, built from phase_state.d1)")
    check(render(w["upstream_gate"], c("verify")) == "failure" and render(w["upstream_gate"], c("all")) == "" and render(w["phase"], c("verify")) == "verify", f"{k}: upstream_gate is forwarded only in a verify call")
PLAN_OK = {"gate": {"result": "skipped", "outputs": {}}, "plan": pl(("production",))}
check(runs("delivery_production", scenario("push", MAIN, {"pipeline_phase": "verify", "phase_state": PS()}, PLAN_OK)), "verify phase: the delivery call runs from the hand-off even though Gate / Deploy are not part of this call")
check(runs("delivery_production", scenario("push", MAIN, {"pipeline_phase": "deploy"}, {"gate": G_OK, "plan": pl(("production",))})), "deploy phase: the delivery call runs (deploy only), independent of the other Worker")
check(not runs("delivery_production", scenario("push", MAIN, {"pipeline_phase": "verify", "phase_state": PS()}, {"gate": {"result": "skipped", "outputs": {}}, "plan": pl(())})), "verify phase: no kinds in the hand-off -> nothing to verify (fail closed)")
dctx = lambda **i: Ctx({"inputs": dict({"stage": "deliver", "phase": "all"}, **i), "needs": {"deploy": {"result": "success", "outputs": {}}, "smoke": {"result": "failure", "outputs": {"smoke_status": "failure"}}}}, {})
check(not ev(djobs["smoke"]["if"], dctx(phase="deploy")) and not ev(djobs["rollback"]["if"], dctx(phase="deploy")) and ev(djobs["deploy"]["if"], dctx(phase="deploy")) and not ev(djobs["deploy"]["if"], dctx(phase="verify")),
      "pipeline_phase: deploy phase = deploy only; verify phase = smoke + rollback only (no publish)")
for job in VALIDATION + ["gate"] + sorted(PREFLIGHTS | {f"migration_{KK(k)}" for k in KINDS}) + ["config-guard", "lifecycle", "persistent-dev"]:
    ctxv = scenario("push", MAIN, {"pipeline_phase": "verify", "phase_state": PS(), "deployment_strategy": "production-only", "shared_dev_on_branch_push": True, "d1_migrations": True}, dict(GREEN, plan=pl(("production",), True), **{p: G_OK for p in PREFLIGHTS}))
    check(not runs(job, ctxv), f"verify phase: {job} never runs (no CI, Preflight, Migration, Deploy or publish in the verify call)")
check(runs("release", scenario("push", MAIN, {"release_cmd": "x", "pipeline_phase": "verify"}, {"delivery_production": dout("production")})) and not runs("release", scenario("push", MAIN, {"release_cmd": "x", "pipeline_phase": "verify"}, {"delivery_production": dout("production", smoke_json=json.dumps({"deployed_and_verified": "false"}))})), "verify phase: Release runs only from a content-verified Production smoke")
ps_val = OUTS["phase_state"]["value"]
check(ps_val.count("toJSON(inputs)") == 1 and '"kinds"' in ps_val and '"d1"' in ps_val and all(f"jobs.delivery_{KK(k)}.outputs.handoff" in ps_val and f"jobs.delivery_{KK(k)}.outputs.migration_used" in ps_val for k in KINDS),
      "phase_state hands over the inputs once, the enabled kinds, each kind's OWN deploy / migration result, the per-kind D1 ids and the concurrency group")
grp_expr = re.search(r"concurrency:\n  group: \$\{\{ (.*) \}\}\n  queue: max", text_all).group(1)
base_expr = grp_expr.split("|| (", 1)[1].rsplit(")", 1)[0]
check(grp_expr.startswith("inputs.pipeline_phase == 'verify' && fromJSON(inputs.phase_state).group || (") and base_expr in ps_val,
      "concurrency: the verify call joins the SAME group the deploy call held (handed over in phase_state, computed from the same expression)")
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

sys.exit(fail)
