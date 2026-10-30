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
doc = yaml.safe_load(open(WF))
jobs = doc["jobs"]
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
    d.update({"migration": "skipped"})
    d.update(over)
    return res(**d)


GREEN = vres()
MAIN, FEAT = "refs/heads/main", "refs/heads/feat/x"
text_all = open(WF).read()

# ---------------------------------------------------------------- 1. topology: Gate -> Plan -> Migration -> Deploy -> Smoke -> Rollback -> Release
DELIVERY = ["migration", "deploy", "smoke", "rollback"]
MATRIX_JOBS = ["migration", "deploy", "smoke", "rollback"]
check(set(jobs) == set(VALIDATION) | {"gate", "plan"} | set(DELIVERY) | {"release", "config-guard", "lifecycle", "persistent-dev"},
      "job set: six validation jobs, Validation Gate, Delivery plan, Migration, Deploy, Smoke, Rollback, Release + config-guard / lifecycle / manual persistent-dev handlers (no per-target job trios)")
for gone in ("dev-deploy", "dev-verify", "dev-rollback", "ci", "test", "prepare", "migrate", "test-gate", "preview", "production", "verify", "revert"):
    check(gone not in jobs, f"obsolete job '{gone}' is gone (every target kind runs through the SAME matrix jobs)")
check([jobs[j]["name"] for j in VALIDATION] == [JOB_NAME[j] for j in VALIDATION], "validation jobs are named Lint, Typecheck, Unit Tests, Integration Tests, Build Validation, E2E")
check(jobs["gate"]["name"] == "Validation Gate" and jobs["plan"]["name"] == "Delivery plan", "Validation Gate and Delivery plan are named jobs")


def needs_of(name):
    n = jobs[name].get("needs", [])
    return [n] if isinstance(n, str) else list(n or [])


for j in VALIDATION:
    check(needs_of(j) == [], f"{j}: no needs (independent, schedulable immediately)")
check(set(needs_of("gate")) == set(VALIDATION), "Validation Gate needs exactly the six validation jobs")
check(needs_of("plan") == ["gate"], "Delivery plan needs only the Validation Gate")
check(set(needs_of("migration")) == {"plan"}, "Migration needs the plan (which itself is behind the gate)")
check(set(needs_of("deploy")) == {"gate", "plan", "migration"}, "Deploy needs the Gate, the plan and the Migration stage - and NOTHING from another target's Deploy / Smoke / Rollback")
check(set(needs_of("smoke")) == {"plan", "deploy"}, "Smoke needs the plan and Deploy")
check(set(needs_of("rollback")) == {"plan", "migration", "deploy", "smoke"}, "Rollback needs plan, Migration (D1 state), Deploy (what to restore) and Smoke (why)")
check(set(needs_of("release")) == {"plan", "deploy", "smoke", "rollback"}, "Release needs plan, Deploy, Smoke and Rollback; Dev kinds never gate it")
for j in ("migration", "deploy", "smoke", "rollback"):
    check("strategy" in jobs[j] and jobs[j]["strategy"].get("fail-fast") is False, f"{j}: matrix over the enabled delivery kinds, fail-fast off (one target's failure never cancels another)")
    check("fromJSON(needs.plan.outputs." in str(jobs[j]["strategy"]["matrix"]), f"{j}: the matrix is the plan's data (no phantom per-target jobs)")
check(len(text_all.splitlines()) <= 1400, f"the workflow stays thin ({len(text_all.splitlines())} lines)")

# ---------------------------------------------------------------- 2. one deployment implementation
ENGINE_USE = "./.cf-tools/actions/cf-deploy"
engine_jobs = {n: [s for s in j.get("steps", []) if s.get("uses") == ENGINE_USE] for n, j in jobs.items()}
check({n for n, s in engine_jobs.items() if s} == set(DELIVERY) | {"persistent-dev"} and all(len(s) == 1 for s in engine_jobs.values() if s),
      "Migration, Deploy, Smoke, Rollback and the manual persistent Dev each call the SAME delivery engine action exactly once (no second deployment implementation)")
eng = {n: s[0] for n, s in engine_jobs.items() if s}
check({n: eng[n]["with"]["stage"] for n in DELIVERY} == {"migration": "migrate", "deploy": "publish", "smoke": "verify", "rollback": "rollback"}, "each delivery job selects its engine stage (migrate / publish / verify / rollback)")
check(all(eng[n]["with"]["target"] == "${{ matrix.target }}" and eng[n]["with"]["kind"] == "${{ matrix.kind }}" for n in DELIVERY), "the engine target and kind come from the plan's matrix leg (one validated source)")
check(eng["persistent-dev"]["with"]["target"] == "dev" and eng["persistent-dev"]["with"]["mode"] == "manual", "manual persistent Dev binds target=dev, mode=manual")
VERIFY_BLOB = "${{ inputs.pipeline_phase == 'verify' && toJSON(fromJSON(inputs.phase_state).inputs) || toJSON(inputs) }}"
check(all(eng[n]["with"]["inputs_json"] in ("${{ toJSON(inputs) }}", VERIFY_BLOB) for n in eng) and all(eng[n]["with"]["inputs_json"] == VERIFY_BLOB for n in ("smoke", "rollback")),
      "engine inputs are passed as one JSON data blob (the verify call reads the DEPLOY call's inputs)")
IMPL = re.compile(r"wrangler|migrate\.sh|publish\.sh|build\.sh|engine\.sh|deploy\.sh|smoke\.sh|d1 migrations|versions (upload|deploy)")
for n, j in jobs.items():
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

mig_if = lambda **kw: runs("migration", scenario(**dict(dict(event="push", ref=FEAT, needs={"plan": {"result": "success", "outputs": {"has_migrations": "true"}}}), **kw)))
check(mig_if() and not mig_if(needs={"plan": {"result": "success", "outputs": {"has_migrations": "false"}}}), "Migration: only when the plan contains a D1 to migrate (no-D1 targets: nothing to do, never blocks)")
check(not mig_if(needs={"plan": {"result": "failure", "outputs": {"has_migrations": "true"}}}) and not mig_if(inputs={"pipeline_phase": "verify"}), "Migration: never after a failed plan or in a verify call")

dep_if = lambda **kw: runs("deploy", scenario(**dict(dict(event="push", ref=FEAT, needs={"gate": {"result": "success", "outputs": {}}, "plan": {"result": "success", "outputs": {"has_targets": "true"}}, "migration": {"result": "skipped", "outputs": {}}}), **kw)))
check(dep_if() and dep_if(ref=MAIN), "Deploy: runs when the gate passed and the plan has targets")
check(not dep_if(needs={"gate": {"result": "failure", "outputs": {}}, "plan": {"result": "success", "outputs": {"has_targets": "true"}}}), "Deploy: blocked by a failed Validation Gate")
check(not dep_if(needs={"gate": {"result": "success", "outputs": {}}, "plan": {"result": "success", "outputs": {"has_targets": "false"}}}), "Deploy: no targets -> no Deploy")
check(not dep_if(needs={"gate": {"result": "success", "outputs": {}}, "plan": {"result": "failure", "outputs": {"has_targets": "true"}}}), "Deploy: a failed plan (invalid configuration) deploys nothing")
check(not dep_if(inputs={"pipeline_phase": "verify"}), "Deploy: never in a verify call")
# the migration of THIS kind's D1 gates its own delivery (step), not other kinds
dep_steps = jobs["deploy"]["steps"]
check("mx_" in str(dep_steps[0]) and "d1_migrations" in str(dep_steps[0]) and dep_steps[0].get("id") == "dep", "Deploy: the first step requires the migration LEG of this kind's D1 to have succeeded (per-D1 gating, not a global needs)")

smoke_if = lambda **kw: runs("smoke", scenario(**dict(dict(event="push", ref=FEAT, needs={"plan": {"result": "success", "outputs": {"has_targets": "true"}}}), **kw)))
check(smoke_if() and smoke_if(inputs={"pipeline_phase": "verify"}) and not smoke_if(inputs={"pipeline_phase": "deploy"}), "Smoke: runs in all and verify phases, never in the deploy phase")
rb_if = lambda sm="failure", **kw: runs("rollback", scenario(**dict(dict(event="push", ref=FEAT, needs={"plan": {"result": "success", "outputs": {"has_targets": "true"}}, "smoke": {"result": sm, "outputs": {}}}), **kw)))
check(rb_if() and not rb_if("success") and not rb_if("skipped") and not rb_if("cancelled"), "Rollback: only after a FAILED Smoke (conditional, never on the happy path)")
check(not rb_if(inputs={"pipeline_phase": "deploy"}), "Rollback: never in the deploy phase")
rgo = jobs["rollback"]["steps"][0]
check(rgo["id"] == "go" and "SMOKE_STATUS" in rgo["run"] and "POLICY_OK" in rgo["run"] and "gate_blocked" in rgo["run"] and "published" in rgo["run"],
      "Rollback leg guard: smoke failed + this run published + no blocked cross-service gate + (Production only) policy automatic")
check("matrix.kind != 'production' ||" in rgo["env"]["POLICY_OK"] and "production_rollback_policy == 'automatic'" in rgo["env"]["POLICY_OK"], "Rollback: the Production policy applies to Production only; Preview / Dev recovery is target-owned")
for k in ("production", "native-branch", "native-dev", "persistent-dev"):
    check(jobs["deploy"]["outputs"].get("h_" + k.replace("-", "_")) and jobs["smoke"]["outputs"].get("s_" + k.replace("-", "_")) and jobs["rollback"]["outputs"].get("rb_" + k.replace("-", "_")) and jobs["migration"]["outputs"].get("mj_" + k.replace("-", "_")),
          f"keyed per-kind hand-off outputs exist for {k} (matrix outputs never collide)")


def smoke_out(verified="true"):
    return {"plan": {"result": "success", "outputs": {}}, "deploy": {"result": "success", "outputs": {}},
            "smoke": {"result": "success", "outputs": {"s_production": json.dumps({"deployed_and_verified": verified})}}, "rollback": {"result": "skipped", "outputs": {}}}


import json  # noqa: E402
rel_ok = lambda **kw: scenario(**dict(dict(event="push", ref=MAIN, inputs={"release_cmd": "pnpm release"}, needs=smoke_out()), **kw))
check(runs("release", rel_ok()), "Release: runs after a verified main Production deployment (Smoke success, Rollback skipped)")
check(not runs("release", rel_ok(needs=smoke_out("false"))), "Release: Production published but NOT content-verified (liveness, smoke_mode=skip) never releases")
check(not runs("release", rel_ok(needs=dict(smoke_out(), smoke={"result": "failure", "outputs": {"s_production": json.dumps({"deployed_and_verified": "false"})}}, rollback={"result": "success", "outputs": {}}))), "Release: a failed smoke blocks Release even when the rollback SUCCEEDED")
check(not runs("release", rel_ok(needs=dict(smoke_out(), rollback={"result": "success", "outputs": {}}))), "Release: any rollback that ran blocks Release")
check(not runs("release", rel_ok(needs=dict(smoke_out(), smoke={"result": "skipped", "outputs": {}}))), "Release: a skipped Smoke never releases")
check(not runs("release", rel_ok(needs=dict(smoke_out(), smoke={"result": "success", "outputs": {"s_native_dev": json.dumps({"deployed_and_verified": "true"})}}))), "Release: a verified Native Dev / Dev / Preview can never release Production")
check(not runs("release", rel_ok(ref=FEAT)), "Release: main only")
check(not runs("release", rel_ok(inputs={"release_cmd": ""})), "Release: optional (no release_cmd, no job)")
check(not runs("release", rel_ok(inputs={"release_cmd": "x", "dispatch_target": "persistent-dev"})), "Release: never from a persistent-dev dispatch")
check(not runs("release", rel_ok(inputs={"release_cmd": "x", "pipeline_phase": "deploy"})), "Release: never in the deploy phase")

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
SECRETS = {"CLOUDFLARE_API_TOKEN": "PROD-TOKEN", "CLOUDFLARE_ACCOUNT_ID": "PROD-ACCT",
           "PREVIEW_SECRETS": "K=v"}
env = jobs["deploy"]["env"]
for ref, label in ((MAIN, "main"), (FEAT, "a feature branch")):
    c = scenario("push", ref, secrets=SECRETS)
    check(render(env["CLOUDFLARE_API_TOKEN"], c) == "PROD-TOKEN" and render(env["CLOUDFLARE_ACCOUNT_ID"], c) == "PROD-ACCT",
          f"Deploy: {label} uses the account credentials")
    c = scenario("push", ref, secrets={k: v for k, v in SECRETS.items() if k != "CLOUDFLARE_API_TOKEN"})
    check(render(env["CLOUDFLARE_API_TOKEN"], c) == "", f"Deploy: missing account token stays EMPTY on {label} ")
check(not any("PREVIEW_API_TOKEN" in k or "PREVIEW_ACCOUNT_ID" in k for k in doc[True]["workflow_call"]["secrets"]), "workflow_call declares no Preview-specific Cloudflare credentials")
check("CLOUDFLARE_DEV" not in yaml.dump(jobs["deploy"]), "Deploy never references the Dev credentials")
for n in ("migration", "rollback"):
    check(jobs[n]["env"]["CLOUDFLARE_API_TOKEN"] == env["CLOUDFLARE_API_TOKEN"] and jobs[n]["env"]["CLOUDFLARE_ACCOUNT_ID"] == env["CLOUDFLARE_ACCOUNT_ID"],
          f"{n}: the same credential selection as Deploy (the account credentials)")
check("CLOUDFLARE" not in yaml.dump(jobs["smoke"]) and "secrets.CLOUDFLARE" not in yaml.dump(jobs["smoke"]), "Smoke Tests: NO Cloudflare credentials of any kind (it only probes the published URL)")
check(set(jobs["smoke"]["env"]) == {"GH_TOKEN", "HANDOFF"}, "Smoke Tests: the job env holds the GitHub token and the (data-only) hand-off, nothing else")
LEG = {"production": {"kind": "production", "target": "prod", "environment": "app/prod"}, "native-branch": {"kind": "native-branch", "target": "preview", "environment": "app/preview"},
       "native-dev": {"kind": "native-dev", "target": "preview", "environment": "app/native-dev"}, "persistent-dev": {"kind": "persistent-dev", "target": "dev", "environment": "app/dev"}}


def with_leg(kind, ref, secrets):
    c = scenario("push", ref, secrets=secrets)
    c["matrix"] = LEG[kind]
    return c


for n in DELIVERY:
    c = with_leg("production", MAIN, SECRETS)
    check(render(eng[n]["with"]["preview_secrets"], c) == "" and "PREVIEW_SECRETS" not in c.secret_reads, f"{n}: Preview-only secrets are never read for the Production leg")
    for k in ("native-branch", "native-dev"):
        check(render(eng[n]["with"]["preview_secrets"], with_leg(k, FEAT, SECRETS)) == "K=v", f"{n}: the {k} leg passes the Preview-only secrets")
    c = with_leg("persistent-dev", FEAT, SECRETS)
    check(render(eng[n]["with"]["preview_secrets"], c) == "" and "PREVIEW_SECRETS" not in c.secret_reads, f"{n}: persistent Dev never receives the Preview-only secrets")
for n in ("deploy", "migration", "rollback"):
    c = with_leg("persistent-dev", FEAT, SECRETS)
    check(render(jobs[n]["env"]["CLOUDFLARE_API_TOKEN"], c) == "PROD-TOKEN" and render(jobs[n]["env"]["CLOUDFLARE_ACCOUNT_ID"], c) == "PROD-ACCT"
          and not {"CLOUDFLARE_PREVIEW_API_TOKEN", "CLOUDFLARE_PREVIEW_ACCOUNT_ID"} & set(c.secret_reads), f"{n}: every kind (incl. Native Dev / Dev) uses the account credentials, never Preview credentials")
    c = with_leg("native-dev", FEAT, {k: v for k, v in SECRETS.items() if k != "CLOUDFLARE_API_TOKEN"})
    check(render(jobs[n]["env"]["CLOUDFLARE_API_TOKEN"], c) == "", f"{n}: a missing account token stays EMPTY (no fallback)")
ptxt = yaml.dump(jobs["persistent-dev"])
check("secrets.CLOUDFLARE_API_TOKEN" in ptxt and "secrets.CLOUDFLARE_ACCOUNT_ID" in ptxt and "CLOUDFLARE_PREVIEW" not in ptxt and "PREVIEW_SECRETS" not in ptxt, "persistent-dev reuses account credentials, not Preview secrets")
check(not any(k in jobs[n].get("env", {}) for n in jobs for k in ("TARGET", "BUILD_ENV", "SMOKE_ENV", "DEV_ENV", "EXPECTED_SHA", "RUN_SHA")),
      "no job exports TARGET / BUILD_ENV / DEV_ENV / EXPECTED_SHA ...: the delivery target is bound by the engine action input, not by environment")
c = scenario("push", MAIN)
for n in ("migration", "deploy", "rollback"):
    check(all(render(jobs[n]["environment"]["name"], with_leg(k, MAIN if k == "production" else FEAT, {})) == LEG[k]["environment"] for k in LEG),
          f"{n}: the GitHub Environment is the plan's per-kind name (<app>/prod, <app>/preview, <app>/native-dev, <app>/dev): Production keeps its protected Environment")
check("environment" not in jobs["smoke"], "Smoke Tests: no Environment (no protection prompt, no credentials)")
check(jobs["deploy"]["environment"]["url"] == "${{ steps.engine.outputs.url }}", "Deploy: the job shows the actual deployed URL (environment.url)")
check(jobs["persistent-dev"]["environment"] == "${{ inputs.app_name }}/dev", "manual persistent Dev runs in the <app>/dev Environment (same as the automatic persistent-dev leg)")
check(all(j.get("permissions", {}).get("contents", "read") == "read" for n, j in jobs.items() if n != "release"), "no job except Release can write repository contents")
check(jobs["migration"].get("permissions", {}).get("deployments") in ("read", "write") and jobs["migration"]["permissions"].get("deployments") != "write", "Migration can READ (not write) Deployments: the Preview identity guard reads the ledger owner on branch pushes (HTTP 403 otherwise)")
check("contents: write" not in yaml.dump({k: jobs[k] for k in jobs if k != "release"}) and not any(w in text_all for w in ("lock.sh", "cfv2-locks", "refs/cfv2")), "no custom Git-ref lock and no content-write permission outside Release")
for name in VALIDATION + ["lifecycle"]:
    text = yaml.dump(jobs[name])
    check("secrets." not in text and "CLOUDFLARE" not in text, f"{name}: no secrets and no Cloudflare credentials of any kind")
check("secrets.SMOKE_PASSWORD" not in yaml.dump({k: jobs[k] for k in VALIDATION}) and eng["smoke"]["with"]["smoke_password"] == "${{ secrets.SMOKE_PASSWORD }}" and "smoke_password" not in eng["deploy"]["with"] and "smoke_password" not in eng["migration"]["with"], "smoke credentials reach only the engine actions that smoke (Smoke Tests, Rollback re-smoke, Dev)")
for n in ("migration", "deploy", "smoke", "rollback", "persistent-dev"):
    cks = [s for s in jobs[n]["steps"] if str(s.get("uses", "")).startswith("actions/checkout")]
    check(cks and all(s["with"].get("persist-credentials") is False for s in cks), f"{n}: checkouts do not persist the GitHub token into the workspace")

# ---------------------------------------------------------------- 6. cross-repository reusable workflow + local composite action references
action_uses = [(n, i, s) for n, j in jobs.items() for i, s in enumerate(j.get("steps", [])) if str(s.get("uses", "")).startswith("./")]
check(action_uses and all("${{" not in s["uses"] and s["uses"].startswith("./.cf-tools/actions/") for _, _, s in action_uses),
      "every local action reference is a STATIC `./.cf-tools/actions/<name>` path (uses: cannot take an expression; `./` alone would resolve against the CALLER's workspace)")
for n, i, s in action_uses:
    name = s["uses"].split("/actions/", 1)[1]
    prior = [p for p in jobs[n]["steps"][:i] if p.get("with", {}).get("path") == ".cf-tools"]
    sp = prior[-1]["with"].get("sparse-checkout", "") if prior else ""
    check(pathlib.Path(f"actions/{name}/action.yml").exists(), f"{n}: referenced action actions/{name}/action.yml exists in this repository")
    check(len(prior) == 1 and prior[0]["with"]["repository"] == "firstsun-dev/.github" and prior[0]["with"]["ref"] == "${{ inputs.tools_ref }}" and f"actions/{name}" in sp,
          f"{n}: firstsun-dev/.github is checked out at tools_ref into .cf-tools, including actions/{name}, BEFORE `uses: {s['uses']}`")
    first_ck = next(i_ for i_, p in enumerate(jobs[n]["steps"]) if str(p.get("uses", "")).startswith("actions/checkout"))
    check(jobs[n]["steps"][first_ck].get("with", {}).get("path") is None or n == "persistent-dev", f"{n}: the app checkout (workspace root) happens before the tooling checkout")
_ci = open("actions/cf-ci-check/action.yml").read()
check("rm -rf .cf-tools" not in _ci and "'.cf-tools/actions/cf-ci-check/action.yml' -delete" in _ci,
      "cf-ci-check never deletes its own action.yml (the runner re-reads it for the post step; Blog run 39722027184 failed that way)")
check(all("cf-preview" in (s["with"].get("sparse-checkout", "") if s.get("with") else "") for n in DELIVERY for s in jobs[n]["steps"] if s.get("with", {}).get("path") == ".cf-tools"), "every delivery job fetches the engine scripts (actions/cf-preview) together with the action")
pins = [s["uses"] for j in jobs.values() for s in j.get("steps", []) if str(s.get("uses", "")).startswith("firstsun-dev/.github/")]
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
uploads = [(j, s) for j, job in jobs.items() for s in job.get("steps", []) if "upload-artifact" in str(s.get("uses", ""))]
check("download-artifact" not in text_all and sorted(j for j, _ in uploads) == ["e2e", "smoke"], "no build artifact hand-off between jobs; the only uploads are the failure-only E2E and smoke reports")
for j_, u in uploads:
    check(u.get("if") == "failure()" and u.get("continue-on-error") is True and 0 < u["with"]["retention-days"] <= 7, f"{j_} report: failure-only, never fails the job, short retention")
check(not any(k in inputs_def for k in ("preview_build_cmd", "preview_provision_cmd", "preview_migrate_cmd", "preview_cleanup_cmd", "preview_smoke_cmd", "preview_smoke_env", "verify_cmd", "migrate_cmd_prod", "migrate_cmd_preview")), "obsolete v2 inputs are removed; no custom migration command exists (the target D1 check cannot be bypassed)")

# ---------------------------------------------------------------- 8. injection hygiene (workflow AND actions)
INJECT = re.compile(r"\$\{\{[^}]*(github\.event\.|github\.head_ref|inputs\.\w*_(cmd|env)\b|inputs\.(build_cmd|sha_path|cmd|inputs_json|ci_env|preview_secrets))")
for jname, job in jobs.items():
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
check(astep("Apply pending D1 migrations")["run"].endswith("| tee -a \"$GITHUB_OUTPUT\"") and astep("Prepare")["env"]["PREPARE_LEDGER"] == "${{ inputs.stage != 'migrate' }}", "engine: the Migration stage opens no ledger entry; Deploy owns it")
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
for j in ("migration", "smoke") + tuple(VALIDATION):
    check("concurrency" not in jobs[j], f"{j}: no job-level concurrency (a second, nested group could deadlock with the workflow-level one)")
def jg(n, kind, scope="", app="app"):
    c = scenario(inputs={"app_name": app, "d1_lock_scope": scope})
    c["matrix"] = LEG[kind]
    return render(jobs[n]["concurrency"]["group"], c)


for n in ("deploy", "rollback"):
    check(jobs[n]["concurrency"]["cancel-in-progress"] is False, f"{n}: job-level groups never cancel in-flight work")
    check(jg(n, "persistent-dev", "shared-db", "blog") == jg("persistent-dev", "persistent-dev", "shared-db", "wiki") if False else jg(n, "persistent-dev", "shared-db", "blog") == "cfv2-dev-o/r-shared-db", f"{n}: the persistent-dev leg joins the legacy Dev group keyed by the shared-D1 scope")
    check(jg(n, "persistent-dev", "", "blog") != jg(n, "persistent-dev", "", "wiki"), f"{n}: without a scope the Dev group is keyed by app")
    check(jg(n, "native-dev").startswith("cfv2-leg-") and jg(n, "production").startswith("cfv2-leg-") and jg(n, "native-dev") != jg(n, "native-branch"), f"{n}: other kinds get uncontended per-leg groups (no cross-target waiting)")
check(jobs["persistent-dev"]["concurrency"]["group"] == "cfv2-dev-${{ github.repository }}-${{ inputs.d1_lock_scope != '' && inputs.d1_lock_scope || inputs.app_name }}", "manual persistent Dev uses the same job-level group (automatic and manual Dev never overlap)")
check(all(not g(r, {"d1_migrations": True}).startswith("cfv2-dev-") for r in (MAIN, FEAT)), "workflow-level groups can never equal the job-level Dev group")
ND_ON = {"preview_on_branch_push": False, "shared_dev_native_preview_on_branch_push": True}
check(g(FEAT, ND_ON) == "cfv2-shared-dev-app" == g("refs/heads/other", ND_ON), "group: Native Dev (fixed target `dev`) serializes ALL branches of the app on one group")
check(g(FEAT, dict(ND_ON, d1_migrations=True)) == "cfv2-nonprod-d1-app", "group: Native Dev + D1 migrations -> the shared non-production D1 group")
check(g(MAIN, ND_ON) == "cfv2-prod-app", "group: Production is never serialized behind Native Dev")
check("CANNOT lock a D1 shared with ANOTHER repository" in inputs_def["d1_lock_scope"]["description"], "the repository-scoped limit of GitHub concurrency is documented on the d1_lock_scope input")

# ---------------------------------------------------------------- 11. interface preserved / Hybrid / lifecycle / retention
check(all(k in inputs_def for k in ("app_url_dev", "dev_deploy_cmd", "dev_smoke_cmd", "build_env_dev", "dev_build_cmd", "dev_wrangler_env", "dev_worker_name", "dev_binding_allow", "dev_deny_resources")), "interface: Dev URL, deploy/smoke commands, build env, Wrangler env, identity and resource-policy inputs exist")
check(not {"CLOUDFLARE_DEV_API_TOKEN", "CLOUDFLARE_DEV_ACCOUNT_ID"} & set(doc[True]["workflow_call"]["secrets"]), "interface: dedicated Dev secrets removed")
check({"deploy_url", "deploy_target", "version_id", "dev_url"} <= set(doc[True]["workflow_call"]["outputs"]), "outputs: deploy_url, deploy_target, version_id, dev_url are preserved (existing consumers)")
OUTS = doc[True]["workflow_call"]["outputs"]
check(set(OUTS) == {"deploy_url", "deploy_target", "version_id", "dev_url", "deploy_verified_url", "deploy_published_unverified", "dev_verified_url", "dev_published_unverified",
                    "deploy_verified", "deploy_smoke_level", "previous_version_id", "migration_status", "rollback_result", "deploy_gate_blocked", "phase_state",
                    "native_dev_url", "native_dev_verified_url", "native_dev_published_unverified", "native_dev_verified", "native_dev_smoke_level"}, "outputs: every pre-existing output is kept; the new ones are Native Dev additions only")
check(all("jobs.deploy.outputs.h_production" in OUTS[k]["value"] and "h_native_branch" in OUTS[k]["value"] for k in ("deploy_url", "deploy_target", "version_id", "previous_version_id")) and OUTS["deploy_url"]["value"].rstrip().endswith(".url }}")
      and "h_native_dev" not in OUTS["deploy_url"]["value"] and "h_persistent_dev" not in OUTS["deploy_url"]["value"],
      "outputs: deploy_* keep their meaning (the Production / per-branch Preview delivery) - never Native Dev or persistent Dev")
check("h_persistent_dev" in OUTS["dev_url"]["value"] and "h_native_dev" in OUTS["native_dev_url"]["value"], "outputs: dev_* stay persistent Dev; native_dev_* are Native Dev")
check(all("jobs.smoke.outputs.s_" in OUTS[k]["value"] and "jobs.deploy" not in OUTS[k]["value"] for k in ("deploy_verified_url", "deploy_verified", "native_dev_verified", "dev_verified_url")), "outputs: *_verified come from the Smoke job only (the only place a deployment becomes verified)")
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
all_run = "\n".join(s_.get("run", "") for j in jobs.values() for s_ in j.get("steps", [])) + "\n".join(s.get("run", "") for s in ASTEPS)
check("wrangler delete" not in all_run, "no job deletes a Worker; branch deletion retains the Shared Dev Worker and Native Previews")
check(not re.search(r"CLOUDFLARE_DEV|dev-deploy|shared_dev_on_branch_push|app_url_dev|dev_deploy_cmd", yaml.dump(jobs["lifecycle"])), "branch-delete lifecycle never references Dev")

# ---------------------------------------------------------------- pipeline_phase: deferred verification (opt-in, default unchanged)
import json as _json  # noqa: E402

PINPUTS = doc[True]["workflow_call"]["inputs"]
check(PINPUTS["pipeline_phase"]["default"] == "all" and PINPUTS["phase_state"]["default"] == "{}" and PINPUTS["upstream_gate"]["default"] == "",
      "pipeline_phase: opt-in - the default is `all` (the unchanged single call), phase_state defaults to an empty object, upstream_gate to no gate")
check(all(i["default"] == "" for i in [PINPUTS["upstream_gate"]]) and not any(i.get("required") for n, i in PINPUTS.items() if n in ("pipeline_phase", "phase_state", "upstream_gate")),
      "pipeline_phase: no new required input (existing callers keep working)")


def state(published="true", target="prod", dev_published="false", group="cfv2-prod-app"):
    return _json.dumps({"inputs": dict(DEFAULT_INPUTS), "deploy": {"published": published, "target": target, "url": "https://x.example"},
                        "migration": {}, "dev": {"published": dev_published, "url": "https://dev.example"}, "group": group})


def vp(job, phase="verify", **kw):
    inputs = dict(kw.pop("inputs", {}), pipeline_phase=phase)
    inputs.setdefault("phase_state", state())
    return runs(job, scenario(**dict(dict(event="push", ref=MAIN, inputs=inputs, needs=kw.pop("needs", {})), **kw)))


PS = lambda kinds=("production",), **extra: json.dumps(dict({"inputs": {"app_name": "app"}, "kinds": list(kinds), "deploy": {}, "migration": {}, "group": "g"}, **extra))
vp_ = lambda job, phase="verify", needs=None, inputs=None, ref=MAIN: runs(job, scenario("push", ref, dict({"pipeline_phase": phase, "phase_state": PS()}, **(inputs or {})), needs or {}))
PLAN_OK = {"plan": {"result": "success", "outputs": {"has_targets": "true"}}}
for job in ("smoke", "rollback", "release"):
    check(not runs(job, scenario("push", MAIN, {"pipeline_phase": "deploy", "release_cmd": "x"}, dict(PLAN_OK, smoke={"result": "failure", "outputs": {}}))), f"deploy phase: {job} never runs in the deploy call (verification belongs to the verify call)")
check(runs("smoke", scenario("push", MAIN, {"pipeline_phase": "all"}, PLAN_OK)), "all phase (default): Smoke runs exactly as before")
check(runs("deploy", scenario("push", MAIN, {"pipeline_phase": "deploy"}, dict(GREEN, gate={"result": "success", "outputs": {}}, plan=PLAN_OK["plan"]))), "deploy phase: Migration and Deploy run independently of the other Worker (no needs on it)")
for job in VALIDATION + ["gate", "migration", "deploy", "config-guard", "lifecycle", "persistent-dev"]:
    ctxv = scenario("push", MAIN, {"pipeline_phase": "verify", "phase_state": PS(), "deployment_strategy": "production-only", "shared_dev_on_branch_push": True, "d1_migrations": True}, GREEN)
    check(not runs(job, ctxv), f"verify phase: {job} never runs (no CI, Migration, Deploy or publish in the verify call)")
check(vp_("smoke", needs=PLAN_OK), "verify phase: Smoke runs from the hand-off even though Deploy is not part of this call")
check(not vp_("smoke", needs={"plan": {"result": "success", "outputs": {"has_targets": "false"}}}), "verify phase: no kinds in the hand-off -> nothing to verify (fail closed)")
check(not vp_("smoke", phase="deploy", needs=PLAN_OK), "deploy phase: no smoke")
check(vp_("rollback", needs=dict(PLAN_OK, smoke={"result": "failure", "outputs": {}})) and not vp_("rollback", needs=dict(PLAN_OK, smoke={"result": "success", "outputs": {}})), "verify phase: a failed Smoke triggers the rollback leg guard; a passed one does not")
check(vp_("release", inputs={"release_cmd": "pnpm release"}, needs=smoke_out()) and not vp_("release", inputs={"release_cmd": "pnpm release"}, needs=smoke_out("false")), "verify phase: Release runs only from a content-verified Production smoke")
check(doc[True]["workflow_call"]["outputs"]["phase_state"]["value"].count("toJSON(inputs)") == 1 and '"kinds"' in doc[True]["workflow_call"]["outputs"]["phase_state"]["value"], "phase_state hands over the inputs once, the enabled kinds, the per-kind Deploy / Migration outputs and the concurrency group")
grp_expr = re.search(r"concurrency:\n  group: \$\{\{ (.*) \}\}\n  queue: max", text_all).group(1)
base_expr = grp_expr.split("|| (", 1)[1].rsplit(")", 1)[0]
check(grp_expr.startswith("inputs.pipeline_phase == 'verify' && fromJSON(inputs.phase_state).group || (") and base_expr in doc[True]["workflow_call"]["outputs"]["phase_state"]["value"],
      "concurrency: the verify call joins the SAME group the deploy call held (handed over in phase_state, computed from the same expression)")
# engine / action wiring
gate = astep("Cross-service gate")
check(gate["id"] == "gate" and "inputs.stage == 'verify'" in gate["if"] and "'success'" in gate["if"] and "'skipped'" in gate["if"] and "exit 1" in gate["run"] and aidx("Cross-service gate") < aidx("Stale-SHA guard (start)")
      and DEPLOY_ACTION["outputs"]["gate_blocked"]["value"].startswith("${{ steps.gate.outputs.blocked"),
      "engine: the cross-service gate runs before any smoke, accepts only success | skipped, fails the job otherwise and reports gate_blocked")
for n in ("smoke", "rollback"):
    w = eng[n]["with"]
    check("call_inputs_json" in w and (n == "rollback" or "upstream_gate" in w), f"{n}: passes its own inputs (identity check) to the engine")
check(not any("secrets." in str(st.get("with", {}).get(k, "")) and "CLOUDFLARE" in str(st.get("with", {}).get(k, "")) for st in jobs["smoke"]["steps"] for k in st.get("with", {})) and "CLOUDFLARE_API_TOKEN" not in yaml.dump(jobs["smoke"]),
      "the Smoke job (every kind) never receives Cloudflare credentials")
sys.exit(fail)
