#!/usr/bin/env python3
"""Static + evaluated contract checks for _cf-worker-v2.yml and its composite actions (no GitHub needed).

Job `if:` conditions and credential `env:` expressions are EVALUATED by a small GitHub-expression
interpreter over an event matrix, so the DAG/gating/secret-isolation claims are exercised, not just grepped.
The composite actions (actions/cf-deploy, actions/cf-ci-check) are parsed and checked structurally; their
behaviour is executed by test-jobs.py. What this cannot prove (needs a real run): GitHub's own scheduling,
Environment protection rules, runner behaviour. See docs/cf-worker-v2-refactor.md "Verified vs not verified".
"""
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
DEVJOB = "dev-deploy"


def vres(**over):
    """results of all six validation jobs (success unless overridden); `deploy` is the ordering dependency of Shared Dev"""
    d = {j: "success" for j in VALIDATION}
    d.update(over)
    return res(**d)


GREEN = vres()
MAIN, FEAT = "refs/heads/main", "refs/heads/feat/x"
text_all = open(WF).read()

# ---------------------------------------------------------------- 1. topology: thin workflow, one engine
check(set(jobs) == set(VALIDATION) | {"deploy", DEVJOB, "release", "config-guard", "lifecycle", "persistent-dev"},
      "job set is exactly the six validation jobs, Deploy, Shared Dev, Release + config-guard/lifecycle/persistent-dev handlers")
for gone in ("ci", "plan", "test", "test-unit", "test-integration", "test-e2e", "prepare", "migrate", "migration", "test-gate", "gate", "preview", "production",
             "production-verify", "production-recovery", "pr-link", "cleanup"):
    check(gone not in jobs, f"obsolete job '{gone}' is gone (no Prepare / Plan / Test Gate / standalone Migration job)")
check([jobs[j]["name"] for j in VALIDATION] == [JOB_NAME[j] for j in VALIDATION], "validation jobs are named Lint, Typecheck, Unit Tests, Integration Tests, Build Validation, E2E")


def needs_of(name):
    n = jobs[name].get("needs", [])
    return [n] if isinstance(n, str) else list(n or [])


for j in VALIDATION:
    check(needs_of(j) == [], f"{j}: no needs (independent, schedulable immediately; no job waits for another validation job)")
check(set(needs_of("deploy")) == set(VALIDATION), "Deploy needs exactly the six validation jobs")
check(set(needs_of(DEVJOB)) == set(VALIDATION) | {"deploy"}, "Shared Dev needs the six validation jobs (+ Deploy for ordering only)")
check(needs_of("release") == ["deploy"], "Release needs Deploy only (Shared Dev never gates or triggers it)")
check(len(text_all.splitlines()) <= 900, f"the workflow is thin ({len(text_all.splitlines())} lines; the former monolith had 1681)")

# ---------------------------------------------------------------- 2. one deployment implementation
ENGINE_USE = "./.cf-tools/actions/cf-deploy"
engine_jobs = {n: [s for s in j.get("steps", []) if s.get("uses") == ENGINE_USE] for n, j in jobs.items()}
check({n for n, s in engine_jobs.items() if s} == {"deploy", DEVJOB, "persistent-dev"} and all(len(s) == 1 for s in engine_jobs.values() if s),
      "Deploy, Shared Dev and the manual persistent Dev each call the SAME delivery engine action exactly once")
eng = {n: s[0] for n, s in engine_jobs.items() if s}
check(eng["deploy"]["with"]["target"] == "${{ github.ref == 'refs/heads/main' && 'prod' || 'preview' }}", "Deploy: ONE job, target = prod on main, preview otherwise")
check(eng[DEVJOB]["with"]["target"] == "dev" and eng["persistent-dev"]["with"]["target"] == "dev" and eng["persistent-dev"]["with"]["mode"] == "manual", "Shared Dev and persistent Dev bind target=dev; persistent Dev runs in manual mode")
check(all(eng[n]["with"]["inputs_json"] == "${{ toJSON(inputs) }}" for n in eng), "engine inputs are passed as one JSON data blob, not interpolated into scripts")
IMPL = re.compile(r"wrangler|migrate\.sh|publish\.sh|build\.sh|engine\.sh|deploy\.sh|smoke\.sh|d1 migrations|versions (upload|deploy)")
for n, j in jobs.items():
    runs_txt = "\n".join(ln for s in j.get("steps", []) for ln in s.get("run", "").splitlines() if "echo" not in ln)  # summary text may name commands
    check(not IMPL.search(runs_txt), f"{n}: no deployment implementation inline in YAML (Wrangler, migration, publish and smoke live in actions/cf-deploy + scripts)")

# ---------------------------------------------------------------- 3. gating over the event matrix
for name in VALIDATION:
    check(runs(name, scenario("push", FEAT)), f"{name}: runs on a feature push")
    check(runs(name, scenario("push", MAIN)), f"{name}: runs on a main push")
    check(not runs(name, scenario("delete", FEAT, ev_ref_type="branch")), f"{name}: not on branch delete")
    check(not runs(name, scenario("pull_request", "refs/pull/1/merge", head_repo="o/r")), f"{name}: same-repo PR is covered by the push run (no duplicate full tests)")
    check(runs(name, scenario("pull_request", "refs/pull/1/merge", head_repo="fork/r")), f"{name}: fork PR runs (credential-free)")
    check(not runs(name, scenario("workflow_dispatch", "refs/heads/dev", {"dispatch_target": "persistent-dev"})), f"{name}: not on persistent-dev dispatch")
for name, inp in CMD_INPUT.items():
    check(not runs(name, scenario("push", FEAT, {inp: ""})), f"{name}: intentionally skipped (job-level) when {inp} is empty")
    check(runs(name, scenario("push", FEAT, {inp: "x"})), f"{name}: runs when {inp} is configured")
check(runs("build", scenario("push", FEAT, {"build_cmd": ""})), "build: build_cmd is required, the job is never optional")

for name in ("deploy", DEVJOB):
    extra = {"shared_dev_on_branch_push": True} if name == DEVJOB else {}
    base = lambda **kw: scenario(**dict(dict(event="push", ref=FEAT, inputs=extra, needs=GREEN), **kw))
    check(runs(name, base()), f"{name}: runs for a trusted feature push")
    check(not runs(name, base(event="pull_request", ref="refs/pull/1/merge", head_repo="o/r")), f"{name}: never on a same-repo PR")
    check(not runs(name, base(event="pull_request", ref="refs/pull/1/merge", head_repo="fork/r")), f"{name}: never on a fork PR (no migration / deploy / credentials)")
    check(not runs(name, base(event="delete", ref=FEAT)), f"{name}: never on branch delete")
    check(not runs(name, base(ref="refs/tags/v1.0.0")), f"{name}: never on a tag push")
    check(not runs(name, scenario("workflow_dispatch", "refs/heads/dev", dict(extra, dispatch_target="persistent-dev"), GREEN)), f"{name}: persistent-dev dispatch is not an automatic deploy")
    check(runs(name, scenario("workflow_dispatch", FEAT, dict(extra, dispatch_target=""), GREEN)), f"{name}: a plain workflow_dispatch on a branch behaves like a push")
    for v in VALIDATION:
        for bad in ("failure", "cancelled", "skipped"):
            check(not runs(name, scenario("push", FEAT, extra, vres(**{v: bad}))), f"{name}: blocked when configured {v} is {bad} (a configured job that skipped is NOT validation)")
        if v in CMD_INPUT:
            check(runs(name, scenario("push", FEAT, dict(extra, **{CMD_INPUT[v]: ""}), vres(**{v: "skipped"}))), f"{name}: an intentionally disabled {v} ({CMD_INPUT[v]} empty) does not block")
            check(not runs(name, scenario("push", FEAT, dict(extra, **{CMD_INPUT[v]: ""}), vres(**{v: "failure"}))), f"{name}: a failed {v} blocks even if its command is empty")
    check(not runs(name, scenario("push", FEAT, dict(extra, build_cmd=""), vres(build="skipped"))), f"{name}: Build Validation skipped never passes (build_cmd is required)")
    check(runs(name, scenario("push", FEAT, dict(extra, **{c: "" for c in CMD_INPUT.values()}), vres(**{v: "skipped" for v in CMD_INPUT}))), f"{name}: Worker with only a build command (all optional checks disabled) passes")
check(runs("deploy", scenario("push", MAIN, needs=GREEN)), "Deploy: runs for a trusted main push")
check(not runs("deploy", scenario("push", FEAT, {"deployment_strategy": "production-only"}, GREEN)), "Deploy: production-only Worker has no Preview deploy on feature branches")
check(runs("deploy", scenario("push", MAIN, {"deployment_strategy": "production-only"}, GREEN)), "Deploy: production-only Worker still deploys main")
check(not runs("deploy", scenario("push", FEAT, {"preview_on_branch_push": False}, GREEN)), "Deploy: preview_on_branch_push=false skips feature-branch Preview")
check(runs("deploy", scenario("push", MAIN, {"preview_on_branch_push": False}, GREEN)), "Deploy: Production is main-only and independent of the Preview switch")
check(runs("deploy", scenario("push", FEAT, {"deployment_strategy": "hybrid"}, GREEN)), "Hybrid: feature pushes get Native Previews, not persistent Dev")


def dep_needs(result="success", target="prod", verified="true"):
    return {"deploy": {"result": result, "outputs": {"target": target, "deployed_and_verified": verified}}}


rel_ok = lambda **kw: scenario(**dict(dict(event="push", ref=MAIN, inputs={"release_cmd": "pnpm release"}, needs=dep_needs()), **kw))
check(not runs("release", rel_ok(needs=dep_needs(verified="false"))), "Release: a Deploy that succeeded WITHOUT deploying+verifying (stale/skipped publish) never releases")
check(not runs("release", rel_ok(needs={"deploy": {"result": "success", "outputs": {"target": "prod"}}})), "Release: missing verified output never releases")
check(runs("release", rel_ok()), "Release: runs after a successful, verified main Production deploy")
check(not runs("release", rel_ok(ref=FEAT, needs=dep_needs(target="preview"))), "Release: main only")
check(not runs("release", rel_ok(needs=dep_needs(target="preview"))), "Release: a Preview deployment can never release")
check(not runs("release", rel_ok(needs=dep_needs(result="failure"))), "Release: never after a failed Deploy")
check(not runs("release", rel_ok(inputs={"release_cmd": ""})), "Release: optional (no release_cmd, no job)")
check(not runs("release", rel_ok(inputs={"release_cmd": "x", "dispatch_target": "persistent-dev"})), "Release: never from a persistent-dev dispatch")

check(runs("lifecycle", scenario("delete", FEAT, ev_ref_type="branch")), "lifecycle: handles branch delete")
check(not runs("lifecycle", scenario("delete", FEAT, ev_ref_type="tag")), "lifecycle: ignores tag delete")
check(runs("lifecycle", scenario("pull_request", "refs/pull/1/merge", head_repo="o/r")), "lifecycle: comments on same-repo PRs")
check(not runs("lifecycle", scenario("pull_request", "refs/pull/1/merge", head_repo="fork/r")), "lifecycle: never touches fork PRs")
check(not runs("lifecycle", scenario("delete", FEAT, {"deployment_strategy": "production-only"}, ev_ref_type="branch")), "lifecycle: production-only has no Preview ledger")
check(not runs("lifecycle", scenario("push", FEAT)), "lifecycle: not on push")

dev_ok = dict(inputs={"dispatch_target": "persistent-dev", "deployment_strategy": "hybrid", "persistent_dev_ref": "refs/heads/dev"})
check(runs("persistent-dev", scenario("workflow_dispatch", "refs/heads/dev", **dev_ok)), "Hybrid: persistent Dev deploys from the approved ref via dispatch")
check(not runs("persistent-dev", scenario("workflow_dispatch", "refs/heads/other", **dev_ok)), "Hybrid: not from an unapproved ref")
check(not runs("persistent-dev", scenario("push", "refs/heads/dev", **dev_ok)), "Hybrid: a push never deploys persistent Dev")
check(not runs("persistent-dev", scenario("workflow_dispatch", "refs/heads/dev", inputs=dict(dev_ok["inputs"], deployment_strategy="production-preview"))), "Hybrid: requires the hybrid strategy")

# ---- config guard fails closed on the DEPLOYMENT path (not only a separate failing job)
BAD = {"deployment_strategy": "production-only", "shared_dev_on_branch_push": True}
check(runs("config-guard", scenario("push", MAIN, BAD, GREEN)) and runs("config-guard", scenario("push", FEAT, BAD, GREEN)), "Config guard: production-only + Shared Dev is reported")
check(not runs("deploy", scenario("push", MAIN, BAD, GREEN)) and not runs("deploy", scenario("push", FEAT, BAD, GREEN)) and not runs(DEVJOB, scenario("push", FEAT, BAD, GREEN)),
      "Config guard: production-only + Shared Dev reaches NO deployment job (no publish, no remote D1 migration, no Production version change)")
check(not runs("release", scenario("push", MAIN, dict(BAD, release_cmd="pnpm release"), {"deploy": {"result": "skipped", "outputs": {}}})), "Config guard: a skipped Deploy never releases")
check(runs("deploy", scenario("push", MAIN, {"deployment_strategy": "production-only", "preview_on_branch_push": True}, GREEN)), "Config guard: production-only + default Preview=true is still accepted and Production deploys")
check(not runs("config-guard", scenario("push", MAIN, {"deployment_strategy": "production-only", "preview_on_branch_push": True}, GREEN)), "Config guard: that backward-compatible combination is not reported")
BOTH = {"preview_on_branch_push": True, "shared_dev_on_branch_push": True}
check(runs("deploy", scenario("push", FEAT, dict(BOTH, deployment_strategy="production-preview"), GREEN)) and runs(DEVJOB, scenario("push", FEAT, dict(BOTH, deployment_strategy="production-preview"), GREEN)), "Config guard: production-preview + both channels accepted")
check(runs(DEVJOB, scenario("push", FEAT, {"deployment_strategy": "hybrid", "shared_dev_on_branch_push": True}, GREEN)) and not runs("config-guard", scenario("push", FEAT, {"deployment_strategy": "hybrid", "shared_dev_on_branch_push": True}, GREEN)), "Config guard: hybrid + Shared Dev accepted")

# ---------------------------------------------------------------- 4. the four Preview x Shared Dev combinations
def combo(feature, dev, ref=FEAT, d1=False):
    inp = {"preview_on_branch_push": feature, "shared_dev_on_branch_push": dev, "d1_migrations": d1}
    return runs("deploy", scenario("push", ref, inp, GREEN)), runs(DEVJOB, scenario("push", ref, inp, GREEN))


check(inputs_def["preview_on_branch_push"]["default"] is True and inputs_def["shared_dev_on_branch_push"]["default"] is False,
      "interface: preview_on_branch_push defaults true; shared_dev_on_branch_push defaults false (no surprise Dev deployments)")
check(combo(True, True) == (True, True), "toggles: Preview=true  Shared Dev=true  -> Preview AND Shared Dev deploy")
check(combo(True, False) == (True, False), "toggles: Preview=true  Shared Dev=false -> Preview only")
check(combo(False, True) == (False, True), "toggles: Preview=false Shared Dev=true  -> Shared Dev only")
check(combo(False, False) == (False, False), "toggles: Preview=false Shared Dev=false -> validation only")
for d1 in (False, True):
    check(combo(True, True, d1=d1) == (True, True) and combo(False, True, d1=d1) == (False, True), f"toggles stay independent with d1_migrations={d1} (migration is a step, not a job)")
check(combo(True, True, MAIN) == (True, False) and combo(False, False, MAIN)[0] is True and combo(False, True, MAIN) == (True, False), "main follows the Production path whatever the switches say; Shared Dev never runs on main")
check(runs("lint", scenario("push", FEAT, {"preview_on_branch_push": False, "shared_dev_on_branch_push": False})), "validation runs with both channels disabled")
sd = lambda **kw: scenario(**dict(dict(event="push", ref=FEAT, inputs={"shared_dev_on_branch_push": True}, needs=GREEN), **kw))
check(runs(DEVJOB, sd(ref="refs/heads/other/branch")), "Shared Dev: any branch deploys the same Dev")
check(not runs(DEVJOB, sd(inputs={"shared_dev_on_branch_push": True, "deployment_strategy": "production-only"})), "Shared Dev: production-only Worker never deploys Dev")
check(runs(DEVJOB, sd(inputs={"shared_dev_on_branch_push": True, "deployment_strategy": "hybrid"})), "Shared Dev: also available with the hybrid strategy")
for r in ("failure", "cancelled", "skipped", "success"):
    n = dict(GREEN, deploy={"result": r, "outputs": {}})
    check(runs(DEVJOB, sd(needs=n)), f"Shared Dev: Preview Deploy {r} neither blocks nor is required (it only orders the shared D1)")
check("needs.deploy.result" not in jobs[DEVJOB]["if"], "Shared Dev gate does not read Deploy's result")
check(runs("config-guard", scenario("push", FEAT, {"deployment_strategy": "production-only", "shared_dev_on_branch_push": True})) and runs("config-guard", scenario("push", MAIN, {"deployment_strategy": "production-only", "shared_dev_on_branch_push": True})),
      "config guard: production-only + shared_dev_on_branch_push fails the run on any push")
check(not runs("config-guard", scenario("push", FEAT, {"deployment_strategy": "production-only"})) and not runs("config-guard", scenario("push", FEAT, {"shared_dev_on_branch_push": True})), "config guard: silent for valid configurations")
check("exit 1" in jobs["config-guard"]["steps"][0]["run"] and "secrets." not in yaml.dump(jobs["config-guard"]), "config guard fails closed and holds no secrets")

# ---------------------------------------------------------------- 5. credentials, Environments, permissions
SECRETS = {"CLOUDFLARE_API_TOKEN": "PROD-TOKEN", "CLOUDFLARE_ACCOUNT_ID": "PROD-ACCT",
           "CLOUDFLARE_PREVIEW_API_TOKEN": "PREVIEW-TOKEN", "CLOUDFLARE_PREVIEW_ACCOUNT_ID": "PREVIEW-ACCT",
           "PREVIEW_SECRETS": "K=v"}
env = jobs["deploy"]["env"]
c = scenario("push", MAIN, secrets=SECRETS)
check(render(env["CLOUDFLARE_API_TOKEN"], c) == "PROD-TOKEN" and render(env["CLOUDFLARE_ACCOUNT_ID"], c) == "PROD-ACCT" and not {"CLOUDFLARE_PREVIEW_API_TOKEN", "CLOUDFLARE_PREVIEW_ACCOUNT_ID"} & set(c.secret_reads),
      "Deploy: main gets ONLY Production credentials (Preview secrets never read)")
c = scenario("push", FEAT, secrets=SECRETS)
check(render(env["CLOUDFLARE_API_TOKEN"], c) == "PREVIEW-TOKEN" and render(env["CLOUDFLARE_ACCOUNT_ID"], c) == "PREVIEW-ACCT" and not {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"} & set(c.secret_reads),
      "Deploy: a feature branch gets ONLY Preview credentials (Production secrets never read)")
c = scenario("push", MAIN, secrets={k: v for k, v in SECRETS.items() if k != "CLOUDFLARE_API_TOKEN"})
check(render(env["CLOUDFLARE_API_TOKEN"], c) == "", "Deploy: missing Production token stays EMPTY on main (no fallback to the Preview token)")
c = scenario("push", FEAT, secrets={k: v for k, v in SECRETS.items() if k != "CLOUDFLARE_PREVIEW_API_TOKEN"})
check(render(env["CLOUDFLARE_API_TOKEN"], c) == "", "Deploy: missing Preview token stays EMPTY on a branch (no fallback to Production)")
check("CLOUDFLARE_DEV" not in yaml.dump(jobs["deploy"]), "Deploy never references the Dev credentials")
c = scenario("push", MAIN, secrets=SECRETS)
check(render(eng["deploy"]["with"]["preview_secrets"], c) == "" and "PREVIEW_SECRETS" not in c.secret_reads, "Deploy: Preview-only secrets are never read on main")
check(render(eng["deploy"]["with"]["preview_secrets"], scenario("push", FEAT, secrets=SECRETS)) == "K=v", "Deploy: a feature branch passes the Preview-only secrets")
dj = jobs[DEVJOB]
c = scenario("push", FEAT, secrets=SECRETS)
check(render(dj["env"]["CLOUDFLARE_API_TOKEN"], c) == "PROD-TOKEN" and render(dj["env"]["CLOUDFLARE_ACCOUNT_ID"], c) == "PROD-ACCT"
      and not {"CLOUDFLARE_PREVIEW_API_TOKEN", "CLOUDFLARE_PREVIEW_ACCOUNT_ID"} & set(c.secret_reads), "Shared Dev reuses account credentials and never Preview credentials")
c = scenario("push", FEAT, secrets={k: v for k, v in SECRETS.items() if k != "CLOUDFLARE_API_TOKEN"})
check(render(dj["env"]["CLOUDFLARE_API_TOKEN"], c) == "", "Shared Dev: missing account token stays EMPTY (no Preview fallback)")
dtxt = yaml.dump(dj)
check("CLOUDFLARE_PREVIEW" not in dtxt and "PREVIEW_SECRETS" not in dtxt and "secrets.CLOUDFLARE_API_TOKEN" in dtxt and "secrets.CLOUDFLARE_ACCOUNT_ID" in dtxt, "Shared Dev reuses account credentials, not Preview secrets")
ptxt = yaml.dump(jobs["persistent-dev"])
check("secrets.CLOUDFLARE_API_TOKEN" in ptxt and "secrets.CLOUDFLARE_ACCOUNT_ID" in ptxt and "CLOUDFLARE_PREVIEW" not in ptxt and "PREVIEW_SECRETS" not in ptxt, "persistent-dev reuses account credentials, not Preview secrets")
check(not any(k in jobs[n].get("env", {}) for n in jobs for k in ("TARGET", "BUILD_ENV", "SMOKE_ENV", "DEV_ENV", "EXPECTED_SHA", "RUN_SHA")),
      "no job exports TARGET / BUILD_ENV / DEV_ENV / EXPECTED_SHA ...: the delivery target is bound by the engine action input, not by environment")
c = scenario("push", MAIN)
check(render(jobs["deploy"]["environment"]["name"], c) == "app/prod", "Deploy: main uses the protected <app>/prod GitHub Environment")
check(render(jobs["deploy"]["environment"]["name"], scenario("push", FEAT)) == "app/preview", "Deploy: branches use <app>/preview")
check(jobs["deploy"]["environment"]["url"] == "${{ steps.engine.outputs.url }}", "Deploy: the job shows the actual deployed URL (environment.url)")
check(dj["environment"]["name"] == "${{ inputs.app_name }}/dev" == jobs["persistent-dev"]["environment"], "Shared Dev runs in the same protected <app>/dev Environment as persistent Dev")
check(dj["permissions"]["contents"] == "read" and "pull-requests" not in dj["permissions"], "Shared Dev is read-only on contents and writes no PR comments")
check(all(j.get("permissions", {}).get("contents", "read") == "read" for n, j in jobs.items() if n != "release"), "no job except Release can write repository contents")
check("contents: write" not in yaml.dump({k: jobs[k] for k in jobs if k != "release"}) and not any(w in text_all for w in ("lock.sh", "cfv2-locks", "refs/cfv2")), "no custom Git-ref lock and no content-write permission outside Release")
for name in VALIDATION + ["lifecycle"]:
    text = yaml.dump(jobs[name])
    check("secrets." not in text and "CLOUDFLARE" not in text, f"{name}: no secrets and no Cloudflare credentials of any kind")
check("secrets.SMOKE_PASSWORD" not in yaml.dump({k: jobs[k] for k in VALIDATION}) and eng["deploy"]["with"]["smoke_password"] == "${{ secrets.SMOKE_PASSWORD }}", "smoke credentials reach only the engine action")
for n in ("deploy", DEVJOB, "persistent-dev"):
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
check(all("cf-preview" in (s["with"].get("sparse-checkout", "") if s.get("with") else "") for s in jobs["deploy"]["steps"] if s.get("with", {}).get("path") == ".cf-tools"), "Deploy fetches the engine scripts (actions/cf-preview) together with the action")
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
check("download-artifact" not in text_all and len(uploads) == 1 and uploads[0][0] == "e2e", "no build artifact hand-off; the only upload is the optional E2E report")
u = uploads[0][1]
check(u.get("if") == "failure()" and u.get("continue-on-error") is True and 0 < u["with"]["retention-days"] <= 3, "E2E report: failure-only, never fails the job, retention <= 3 days")
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
check(sum(1 for s in ASTEPS if "migrate" in s.get("run", "")) == 1, "the migration is invoked from exactly one step of the engine")
for n in ("Setup dependencies", "Prepare", "Build and audit", "Validate the exact migration target", "Stale-SHA guard (before migration)", "Apply pending D1 migrations", "Stale-SHA guard (after migration", "Publish the audited artifact", "Smoke the exact deployed URL", "Mark deployed and verified"):
    check("env.STALE != 'true'" in astep(n)["if"], f"engine: '{n}' is skipped for a superseded run")
check("guard before-migration" in astep("Stale-SHA guard (before migration)")["run"] and "guard before-publish" in astep("Stale-SHA guard (after migration")["run"], "engine: the branch tip is re-checked immediately before the migration AND again immediately before the publish")
check(astep("Shared-D1 guard")["if"].endswith("inputs.target == 'dev'") and "d1-guard" in astep("Shared-D1 guard")["run"], "engine: the shared-D1 guard applies to Shared Dev only")
pub = astep("Publish the audited artifact")
check("engine.sh\" publish" in pub["run"] and pub["env"]["ENGINE_TARGET"] == "${{ inputs.target }}" and "build" not in pub["run"], "engine: publish is publish-only (the build stage is a separate, earlier step)")
ver = astep("Mark deployed and verified")
check("steps.smoke.outcome == 'success'" in ver["if"] and "steps.publish.outcome == 'success'" in ver["if"], "engine: verified only after publish AND smoke succeeded in this run")
check(DEPLOY_ACTION["outputs"]["deployed_and_verified"]["value"].endswith("|| 'false' }}") and "steps.verified" in DEPLOY_ACTION["outputs"]["deployed_and_verified"]["value"], "engine reports verified only from the final marker step")
check(all(s.get("continue-on-error") is not True for s in ASTEPS), "engine: failures are never hidden by continue-on-error")
check("prod-recover" in astep("Production recovery")["run"] and "failure()" in astep("Production recovery")["if"] and "steps.smoke.outcome == 'failure'" in astep("Production recovery")["if"], "engine: Production recovery only after a failed smoke; the engine step keeps the run red")
rf = astep("Recovery - find")
check("inputs.mode == 'auto'" in rf["if"] and "inputs.target != 'prod'" in rf["if"] and "steps.publish.outcome == 'success' && steps.smoke.outcome == 'failure'" in rf["if"],
      "engine: automatic Preview/Dev recovery only in auto mode, only after a published URL failed smoke (never before a mutation or without a URL)")
check("recover-run" in astep("Recovery - rebuild")["run"] and "migrate" not in astep("Recovery - rebuild")["run"], "engine: recovery rebuilds, audits and republishes the last good SHA and never migrates (D1 is never rolled back)")
check(astep("Recovery - verify")["env"]["GOOD_SHA"] == "${{ steps.good.outputs.sha }}" and "recover-verify" in astep("Recovery - verify")["run"], "engine: the restored deployment is verified against the LAST-GOOD sha")
check(astep("Recovery - checkout")["with"]["path"] == "recover" and astep("Recovery - checkout")["with"]["persist-credentials"] is False, "engine: the last-good tree is checked out beside the failed one, without persisting the token")
check(aidx("Failed before or during publish") < aidx("Production recovery") < aidx("Recovery - find") < aidx("Recovery - record"), "engine: failure reporting precedes recovery; recovery is recorded accurately last")
check("preview_mutated" in str(astep("Failed before or during publish")["env"]) and "failure()" in astep("Failed before or during publish")["if"], "engine: 'unchanged' is only claimed when no mutation happened")
check(astep("Remove staged env files")["if"] == "always()", "engine: staged Preview/smoke env files are always removed")
check(aidx("Remove staged env files") == len(ASTEPS) - 1, "engine: cleanup is the last step")
flat = yaml.dump(ASTEPS)
check("CLOUDFLARE" not in flat and "secrets." not in yaml.dump(DEPLOY_ACTION), "engine: contains no credentials; the caller job owns which Cloudflare credentials exist")
check(set(DEPLOY_ACTION["inputs"]) >= {"target", "mode", "inputs_json", "preview_secrets", "smoke_username", "smoke_password"}, "engine interface: target, mode, inputs_json and the secret-bearing inputs")

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
for j in ["deploy"] + VALIDATION:
    check("concurrency" not in jobs[j], f"{j}: no job-level concurrency (a second, nested group could deadlock with the workflow-level one)")
jg = lambda n, scope="", app="app": render(jobs[n]["concurrency"]["group"], scenario(inputs={"app_name": app, "d1_lock_scope": scope}))
check(jobs[DEVJOB]["concurrency"] == jobs["persistent-dev"]["concurrency"] and jobs[DEVJOB]["concurrency"]["cancel-in-progress"] is False, "Shared Dev and persistent Dev share ONE job-level group (automatic and manual Dev never overlap)")
check(jg(DEVJOB, "shared-db", "blog") == jg("persistent-dev", "shared-db", "wiki") and jg(DEVJOB, "", "blog") != jg(DEVJOB, "", "wiki"), "Dev group is keyed by the shared-D1 scope when set, else by app")
check(all(not g(r, {"d1_migrations": True}).startswith("cfv2-dev-") for r in (MAIN, FEAT)) and jg(DEVJOB).startswith("cfv2-dev-"), "workflow-level groups can never equal the job-level Dev group")
check("CANNOT lock a D1 shared with ANOTHER repository" in inputs_def["d1_lock_scope"]["description"], "the repository-scoped limit of GitHub concurrency is documented on the d1_lock_scope input")

# ---------------------------------------------------------------- 11. interface preserved / Hybrid / lifecycle / retention
check(all(k in inputs_def for k in ("app_url_dev", "dev_deploy_cmd", "dev_smoke_cmd", "build_env_dev", "dev_build_cmd", "dev_wrangler_env", "dev_worker_name", "dev_binding_allow", "dev_deny_resources")), "interface: Dev URL, deploy/smoke commands, build env, Wrangler env, identity and resource-policy inputs exist")
check(not {"CLOUDFLARE_DEV_API_TOKEN", "CLOUDFLARE_DEV_ACCOUNT_ID"} & set(doc[True]["workflow_call"]["secrets"]), "interface: dedicated Dev secrets removed")
check({"deploy_url", "deploy_target", "version_id", "dev_url"} <= set(doc[True]["workflow_call"]["outputs"]), "outputs: deploy_url, deploy_target, version_id, dev_url are preserved (existing consumers)")
check(set(doc[True]["workflow_call"]["outputs"]) == {"deploy_url", "deploy_target", "version_id", "dev_url", "deploy_verified_url", "deploy_published_unverified", "dev_verified_url", "dev_published_unverified"}, "outputs: verified URLs and published-but-unverified flags are additive")
check("verified" in doc[True]["workflow_call"]["outputs"]["deploy_verified_url"]["value"] or "verified_url" in doc[True]["workflow_call"]["outputs"]["deploy_verified_url"]["value"], "outputs: deploy_verified_url comes from the engine's verified_url")
pd = jobs["persistent-dev"]
check(pd["if"] == "github.event_name == 'workflow_dispatch' && inputs.dispatch_target == 'persistent-dev' && inputs.deployment_strategy == 'hybrid' && inputs.persistent_dev_ref != '' && github.ref == inputs.persistent_dev_ref" and pd["environment"] == "${{ inputs.app_name }}/dev",
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
check(not re.search(r"CLOUDFLARE_DEV|dev-deploy|shared_dev|app_url_dev|dev_deploy_cmd", yaml.dump(jobs["lifecycle"])), "branch-delete lifecycle never references Dev")

sys.exit(fail)
