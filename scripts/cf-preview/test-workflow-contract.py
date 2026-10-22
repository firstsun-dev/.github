#!/usr/bin/env python3
"""Static + evaluated contract checks for _cf-worker-v2.yml (no GitHub needed).

Job `if:` conditions and credential `env:` expressions are EVALUATED by a small GitHub-expression
interpreter over an event matrix, so the DAG/gating/secret-isolation claims are exercised, not just grepped.
What this cannot prove (needs a real run): GitHub's own scheduling, Environment protection rules, runner
behaviour. See docs/cf-worker-v2-refactor.md "Verified vs not verified".
"""
import re
import sys
import yaml

WF = ".github/workflows/_cf-worker-v2.yml"
doc = yaml.safe_load(open(WF))
jobs = doc["jobs"]
fail = 0


def check(ok, msg):
    global fail
    print(("PASS: " if ok else "FAIL: ") + msg)
    if not ok:
        fail = 1


sys.path.insert(0, "scripts/cf-preview")
from ghexpr import Ctx, ev, render, strip_expr  # noqa: E402


# ---------------------------------------------------------------- scenario helpers
DEFAULT_INPUTS = {
    "app_name": "app", "deployment_strategy": "production-preview", "preview_on_branch_push": True,
    "d1_migrations": False, "dispatch_target": "", "persistent_dev_ref": "", "test_e2e_cmd": "pnpm e2e",
    "lint_cmd": "pnpm lint", "typecheck_cmd": "pnpm check", "test_unit_cmd": "pnpm unit", "test_integration_cmd": "pnpm integ",
    "build_cmd": "pnpm build", "ci_env": "", "use_mailpit": True,
    "release_cmd": "", "build_env_prod": "A=prod", "build_env_preview": "A=preview",
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
CORE = VALIDATION + ["migrate", "deploy", "release"]


def vres(migrate="success", **over):
    """results of all six validation jobs (success unless overridden) + migrate"""
    d = {j: "success" for j in VALIDATION}
    d.update(over)
    return res(migrate=migrate, **d)


GREEN = vres(migrate="skipped")

# ---------------------------------------------------------------- 1. topology
check(set(jobs) == set(VALIDATION) | {"migrate", "deploy", "dev-deploy", "release", "config-guard", "lifecycle", "persistent-dev"},
      "job set is exactly the six validation jobs, Migration, Deploy, Shared Dev, Release + config-guard/lifecycle/persistent-dev handlers")
check("ci" not in jobs, "the former combined CI job is gone (no combined-CI requirement remains)")
check([jobs[j]["name"] for j in VALIDATION] == [JOB_NAME[j] for j in VALIDATION], "validation jobs are named Lint, Typecheck, Unit Tests, Integration Tests, Build Validation, E2E")
for gone in ("plan", "test", "test-unit", "test-integration", "test-e2e", "prepare", "preview", "production",
             "production-verify", "production-recovery", "pr-link", "cleanup"):
    check(gone not in jobs, f"obsolete job '{gone}' is gone")


def needs_of(name):
    n = jobs[name].get("needs", [])
    return [n] if isinstance(n, str) else list(n or [])


for j in VALIDATION:
    check(needs_of(j) == [], f"{j}: no needs (independent, schedulable immediately; no job waits for another validation job)")
check(set(needs_of("migrate")) == set(VALIDATION), "Migration needs all six validation jobs")
check(set(needs_of("deploy")) == set(VALIDATION) | {"migrate"}, "Deploy needs all six validation jobs and Migration")
check(needs_of("release") == ["deploy"], "Release needs Deploy")
check(sum(1 for j in jobs if j in ("deploy", "deploy-prod", "deploy-preview", "publish")) == 1, "exactly one Native Preview/Production Deploy job implementation (no per-target deploy jobs)")

# ---------------------------------------------------------------- 2. gating over the event matrix
MAIN, FEAT = "refs/heads/main", "refs/heads/feat/x"
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

for name in ("migrate", "deploy"):
    d1 = {"d1_migrations": True}
    needs_ok = vres()
    base = lambda **kw: scenario(**dict(dict(event="push", ref=FEAT, inputs=d1, needs=needs_ok), **kw))
    check(runs(name, base()), f"{name}: runs for a trusted feature push")
    check(runs(name, base(ref=MAIN)), f"{name}: runs for a trusted main push")
    check(not runs(name, base(event="pull_request", ref="refs/pull/1/merge", head_repo="o/r")), f"{name}: never on a same-repo PR")
    check(not runs(name, base(event="pull_request", ref="refs/pull/1/merge", head_repo="fork/r")), f"{name}: never on a fork PR (no remote migration / deploy / credentials)")
    check(not runs(name, base(event="delete", ref=FEAT)), f"{name}: never on branch delete")
    check(not runs(name, base(ref="refs/tags/v1.0.0")), f"{name}: never on a tag push")
    check(not runs(name, scenario("workflow_dispatch", "refs/heads/dev", {"d1_migrations": True, "dispatch_target": "persistent-dev"}, needs_ok)), f"{name}: persistent-dev dispatch is not a prod/preview deploy")
    check(not runs(name, base(inputs=dict(d1, deployment_strategy="production-only"))), f"{name}: production-only Worker has no Preview migrate/deploy on feature branches")
    check(runs(name, base(ref=MAIN, inputs=dict(d1, deployment_strategy="production-only"))), f"{name}: production-only Worker still deploys main")
    check(not runs(name, base(inputs=dict(d1, preview_on_branch_push=False))), f"{name}: preview_on_branch_push=false skips feature-branch delivery")
    for v in VALIDATION:
        for bad in ("failure", "cancelled", "skipped"):
            check(not runs(name, scenario("push", FEAT, d1, vres(**{v: bad}))), f"{name}: blocked when configured {v} is {bad} (a configured job that skipped is NOT validation)")
        if v in CMD_INPUT:
            check(runs(name, scenario("push", FEAT, dict(d1, **{CMD_INPUT[v]: ""}), vres(**{v: "skipped"}))), f"{name}: an intentionally disabled {v} ({CMD_INPUT[v]} empty) does not block")
            check(not runs(name, scenario("push", FEAT, dict(d1, **{CMD_INPUT[v]: ""}), vres(**{v: "failure"}))), f"{name}: a failed {v} blocks even if its command is empty")
    check(not runs(name, scenario("push", FEAT, dict(d1, build_cmd=""), vres(build="skipped"))), f"{name}: Build Validation skipped never passes (build_cmd is required)")
    check(runs(name, scenario("push", FEAT, dict(d1, **{c: "" for c in CMD_INPUT.values()}), vres(**{v: "skipped" for v in CMD_INPUT}))), f"{name}: Worker with only a build command (all optional checks disabled) passes")

# migrate itself only exists for D1 Workers
check(not runs("migrate", scenario("push", FEAT, {"d1_migrations": False}, vres())), "Migration: skipped for D1-less Workers")
check(runs("deploy", scenario("push", FEAT, {"d1_migrations": False}, GREEN)), "Deploy: D1-less Worker deploys with Migration skipped")
check(runs("deploy", scenario("push", MAIN, {"d1_migrations": False}, GREEN)), "Deploy: D1-less Worker deploys Production with Migration skipped")
for bad in ("failure", "cancelled"):
    check(not runs("deploy", scenario("push", FEAT, {"d1_migrations": True}, vres(migrate=bad))), f"Deploy: BLOCKED when Migration is {bad} (preview)")
    check(not runs("deploy", scenario("push", MAIN, {"d1_migrations": True}, vres(migrate=bad))), f"Deploy: BLOCKED when Migration is {bad} (prod)")
check(not runs("deploy", scenario("push", FEAT, {"d1_migrations": True}, vres(migrate="skipped"))),
      "Deploy: a Migration that was skipped although d1_migrations is on does NOT unblock Deploy")

def dep_needs(result="success", target="prod", verified="true"):
    return {"deploy": {"result": result, "outputs": {"target": target, "deployed_and_verified": verified}}}


rel_ok = lambda **kw: scenario(**dict(dict(event="push", ref=MAIN, inputs={"release_cmd": "pnpm release"}, needs=dep_needs()), **kw))
check(not runs("release", rel_ok(needs=dep_needs(verified="false"))), "Release: a Deploy that succeeded WITHOUT deploying+verifying (stale/skipped publish) never releases")
check(not runs("release", rel_ok(needs={"deploy": {"result": "success", "outputs": {"target": "prod"}}})), "Release: missing verified output never releases")
check(runs("release", rel_ok()), "Release: runs after a successful main Production deploy")
check(not runs("release", rel_ok(ref=FEAT, needs=dep_needs(target="preview"))), "Release: main only")
check(not runs("release", rel_ok(needs=dep_needs(result="failure"))), "Release: never after a failed Deploy")
check(not runs("release", rel_ok(inputs={"release_cmd": ""})), "Release: optional (no release_cmd, no job)")

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
check(runs("deploy", scenario("push", FEAT, {"deployment_strategy": "hybrid"}, GREEN)), "Hybrid: feature pushes get Native Previews, not persistent Dev")

# ---------------------------------------------------------------- 3. credential selection (evaluated)
SECRETS = {"CLOUDFLARE_API_TOKEN": "PROD-TOKEN", "CLOUDFLARE_ACCOUNT_ID": "PROD-ACCT",
           "CLOUDFLARE_PREVIEW_API_TOKEN": "PREVIEW-TOKEN", "CLOUDFLARE_PREVIEW_ACCOUNT_ID": "PREVIEW-ACCT",
           "CLOUDFLARE_DEV_API_TOKEN": "DEV-TOKEN"}
for name in ("migrate", "deploy"):
    env = jobs[name]["env"]
    c = scenario("push", MAIN, secrets=SECRETS)
    tok = render(env["CLOUDFLARE_API_TOKEN"], c)
    check(tok == "PROD-TOKEN" and "CLOUDFLARE_PREVIEW_API_TOKEN" not in c.secret_reads, f"{name}: main gets ONLY the Production token (preview secret never read)")
    c = scenario("push", FEAT, secrets=SECRETS)
    tok = render(env["CLOUDFLARE_API_TOKEN"], c)
    acct = render(env["CLOUDFLARE_ACCOUNT_ID"], c)
    check(tok == "PREVIEW-TOKEN" and acct == "PREVIEW-ACCT" and "CLOUDFLARE_API_TOKEN" not in c.secret_reads and "CLOUDFLARE_ACCOUNT_ID" not in c.secret_reads,
          f"{name}: a feature branch gets ONLY Preview credentials (Production secrets never read)")
    c = scenario("push", MAIN, secrets={k: v for k, v in SECRETS.items() if k != "CLOUDFLARE_API_TOKEN"})
    check(render(env["CLOUDFLARE_API_TOKEN"], c) == "", f"{name}: missing Production token stays EMPTY on main (no fallback to the Preview token)")
    c = scenario("push", FEAT, secrets={k: v for k, v in SECRETS.items() if k != "CLOUDFLARE_PREVIEW_API_TOKEN"})
    check(render(env["CLOUDFLARE_API_TOKEN"], c) == "", f"{name}: missing Preview token stays EMPTY on a branch (no fallback to Production)")
    check("CLOUDFLARE_DEV_API_TOKEN" not in yaml.dump(jobs[name]), f"{name}: never references the persistent Dev token")
    check(render(env["TARGET"], scenario("push", MAIN)) == "prod" and render(env["TARGET"], scenario("push", FEAT)) == "preview", f"{name}: TARGET is prod only for main")
# target-specific inputs never fall through to the other target when empty
c = scenario("push", MAIN, {"build_env_prod": ""}, secrets=SECRETS)
check(render(jobs["deploy"]["env"]["BUILD_ENV"], c) == "", "Deploy: empty build_env_prod does not fall back to build_env_preview")
c = scenario("push", MAIN, secrets=SECRETS)
check(render(jobs["deploy"]["env"]["BUILD_ENV"], c) == "A=prod", "Deploy: main uses build_env_prod")
c = scenario("push", FEAT, secrets=SECRETS)
check(render(jobs["deploy"]["env"]["BUILD_ENV"], c) == "A=preview", "Deploy: a branch uses build_env_preview")

c = scenario("push", MAIN)
check(render(jobs["deploy"]["environment"]["name"], c) == "app/prod", "Deploy: main uses the protected <app>/prod GitHub Environment")
check(render(jobs["migrate"]["environment"], c) == "app/prod", "Migration: main also runs in the protected <app>/prod Environment")
c = scenario("push", FEAT)
check(render(jobs["deploy"]["environment"]["name"], c) == "app/preview", "Deploy: branches use <app>/preview")
check("preview_url" not in str(jobs["deploy"]["environment"]["url"]) and "steps.publish.outputs.url" in str(jobs["deploy"]["environment"]["url"]), "Deploy: the job shows the actual deployed URL (environment.url)")

# ---------------------------------------------------------------- 4. secret isolation / fork safety (static)
for name in VALIDATION + ["lifecycle"]:
    text = yaml.dump(jobs[name])
    check("secrets." not in text and "CLOUDFLARE" not in text, f"{name}: no secrets and no Cloudflare credentials of any kind")
check("secrets.CLOUDFLARE_API_TOKEN" not in yaml.dump(jobs["persistent-dev"]) and "CLOUDFLARE_DEV_API_TOKEN" in yaml.dump(jobs["persistent-dev"]), "persistent-dev uses only the Dev token")
check("secrets.SMOKE_PASSWORD" in yaml.dump(jobs["deploy"]) and "secrets.SMOKE_PASSWORD" not in yaml.dump({k: jobs[k] for k in VALIDATION + ["migrate"]}),
      "smoke credentials reach only the Deploy smoke step")
smoke_step = next(s for s in jobs["deploy"]["steps"] if s.get("id") == "smoke")
check("SMOKE_PASSWORD" in smoke_step.get("env", {}), "smoke credentials are scoped to the smoke step env")
checkouts = [s for s in jobs["deploy"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout")]
check(checkouts and all(s["with"].get("persist-credentials") is False for s in checkouts), "Deploy checkouts do not persist the GitHub token into the workspace")

# no untrusted/event-controlled text and no caller command/env text interpolated into shell
INJECT = re.compile(r"\$\{\{[^}]*(github\.event\.|github\.head_ref|inputs\.\w*_(cmd|env)\b|inputs\.(build_cmd|sha_path))")
for jname, job in jobs.items():
    for s in job.get("steps", []):
        run = s.get("run", "")
        check(not INJECT.search(run), f"{jname}/{s.get('name', s.get('id', '?'))}: no event/caller-command text interpolated into the script")

# ---------------------------------------------------------------- 5. builds and artifacts
text_all = open(WF).read()
check("download-artifact" not in text_all, "no job downloads a build artifact (no artifact-based hand-off)")
uploads = [(j, s) for j, job in jobs.items() for s in job.get("steps", []) if "upload-artifact" in str(s.get("uses", ""))]
check(len(uploads) == 1 and uploads[0][0] == "e2e", "the only upload is the optional E2E report")
u = uploads[0][1]
check(u.get("if") == "failure()" and u.get("continue-on-error") is True and 0 < u["with"]["retention-days"] <= 3,
      "E2E report: failure-only, never fails the job, retention <= 3 days")
e2e_text = yaml.dump(jobs["e2e"])
deploy_text = yaml.dump({"steps": jobs["deploy"]["steps"], "env": jobs["deploy"]["env"]})  # the build/steps, not the gating `if`
check("test_e2e_build_cmd" in e2e_text and "test_e2e_env" in e2e_text and "inputs.build_cmd" not in e2e_text, "E2E builds with its own test build command and env")
check("inputs.build_cmd" in deploy_text and "test_e2e" not in deploy_text and "ci_env" not in deploy_text, "Deploy builds independently with build_cmd + target env (no E2E/CI inputs)")
build_text = yaml.dump(jobs["build"])
check("inputs.build_cmd" in build_text and "ci_env" in build_text and "test_e2e" not in build_text, "Build Validation builds with build_cmd + ci_env (nothing uploaded)")
# each job runs exactly its own command, and only that one
CMDS = {"lint": "lint_cmd", "typecheck": "typecheck_cmd", "unit": "test_unit_cmd", "integration": "test_integration_cmd", "build": "build_cmd"}
for j, inp in CMDS.items():
    t = yaml.dump(jobs[j])
    others = [i for k, i in CMDS.items() if k != j] + ["test_e2e_cmd"]
    check(f"inputs.{inp}" in t and not any(f"inputs.{o}" in t.replace(f"inputs.{inp}", "") for o in others), f"{j}: executes {inp} and no other job's command")
    run_steps = [s_ for s_ in jobs[j]["steps"] if s_.get("id") == "check"]
    check(len(run_steps) == 1 and run_steps[0]["env"]["CMD"] == "${{ inputs." + inp + " }}" and run_steps[0]["run"] == 'bash -c "$CMD"', f"{j}: runs {inp} exactly once, via env (not interpolated)")
    names_ = [s_.get("uses", "") for s_ in jobs[j]["steps"]]
    check(sum(1 for n in names_ if n.startswith("actions/checkout")) == 2 and any("actions/setup@" in n for n in names_), f"{j}: has its own checkout and dependency setup")
    check(not any("playwright" in yaml.dump(s_).lower() for s_ in jobs[j]["steps"]), f"{j}: installs no browsers (Playwright is E2E-only)")
    check("upload-artifact" not in t and "download-artifact" not in t, f"{j}: no artifacts")
    check("GITHUB_STEP_SUMMARY" in t, f"{j}: writes a short Step Summary")
    check(jobs[j].get("continue-on-error") is None and all(s_.get("continue-on-error") is not True for s_ in jobs[j]["steps"] if s_.get("id") == "check"), f"{j}: failures are never hidden by continue-on-error")
for j in ("lint", "typecheck", "build"):
    check("mailpit" not in yaml.dump(jobs[j]), f"{j}: does not start Mailpit")
for j in ("unit", "integration", "e2e"):
    check("mailpit" in yaml.dump(jobs[j]), f"{j}: starts Mailpit when use_mailpit is set")
check(all(any(str(s_.get("name", "")).startswith("Import validation env") for s_ in jobs[j]["steps"]) for j in CMDS) and
      all("parse-env.mjs\" TARGET_BUILD_ENV" in yaml.dump(jobs[j]) and "parse-env.mjs\" CI_ENV" in yaml.dump(jobs[j]) for j in CMDS),
      "validation jobs import target build env then ci_env through the safe importer (precedence preserved)")
check("build_env_prod" in deploy_text and "build_env_preview" in deploy_text, "Deploy injects target-specific build env")
check(not any(k in doc[True]["workflow_call"]["inputs"] for k in ("preview_build_cmd", "preview_provision_cmd", "preview_migrate_cmd", "preview_cleanup_cmd", "preview_smoke_cmd", "preview_smoke_env", "verify_cmd", "test_e2e_variants_legacy")),
      "obsolete v2 inputs are removed (no compatibility shims)")

# ---------------------------------------------------------------- 6. migration safety + serialization
mig_steps = {s.get("id") or s.get("name"): s for s in jobs["migrate"]["steps"]}
mig_text, dep_text = yaml.dump(jobs["migrate"]), yaml.dump(jobs["deploy"])
check(not any(w in text_all for w in ("lock.sh", "cfv2-locks", "refs/cfv2")) and "contents: write" not in yaml.dump({k: jobs[k] for k in ("migrate", "deploy")}),
      "the custom Git-ref lock, its refs and its write permissions are gone")
check(all(j.get("permissions", {}).get("contents", "read") == "read" for j in [jobs["migrate"], jobs["deploy"]] + [jobs[v] for v in VALIDATION]), "validation/Migration/Deploy jobs are read-only on repository contents")
check("migrate_cmd_prod" not in doc[True]["workflow_call"]["inputs"] and "migrate_cmd_preview" not in doc[True]["workflow_call"]["inputs"] and "MIGRATE_CMD" not in text_all,
      "no custom migration command exists: the target D1 check cannot be bypassed")
cc = doc["concurrency"]
check(cc.get("queue") == "max" and "cancel-in-progress" not in cc, "workflow-level concurrency uses queue: max (no cancel-in-progress, which GitHub rejects together)")
GROUP = lambda **kw: render(cc["group"], scenario(**kw))
g = lambda ref, inputs=None, event="push": GROUP(event=event, ref=ref, inputs=dict({"app_name": "app"}, **(inputs or {})))
check(g(MAIN) == "cfv2-app-prod", "group: main -> <app>-prod")
check(g(FEAT, {"d1_migrations": True}) == "cfv2-app-preview-d1-app" == g("refs/heads/other", {"d1_migrations": True}),
      "group: ALL branches of a D1 app share the Preview-D1 group (the group is the shared resource, not the branch)")
check(g(FEAT, {"d1_migrations": True}) != g(MAIN, {"d1_migrations": True}), "group: Preview D1 and Production do not serialize each other")
check(g(FEAT, {"d1_migrations": True, "d1_lock_scope": "heaven-id-db"}) == "cfv2-app-preview-d1-heaven-id-db", "group: d1_lock_scope names the physical shared Preview D1")
check(g(FEAT) != g("refs/heads/other") and g(FEAT).startswith("cfv2-app-preview-"), "group: D1-less branches do not queue behind each other")
check(g("refs/pull/1/merge", event="pull_request").startswith("cfv2-run-") and g("refs/heads/x", event="delete").startswith("cfv2-run-"), "group: PR / delete runs get a private group")
check(g("refs/heads/dev", {"dispatch_target": "persistent-dev"}, "workflow_dispatch").startswith("cfv2-run-"), "group: persistent-dev dispatch does not queue behind deploys")
check(g("refs/tags/v1", event="push").startswith("cfv2-run-"), "group: tag pushes get a private group")
check(all(g(r).startswith("cfv2-") for r in (MAIN, FEAT)) and "github.workflow" not in cc["group"], "group has a static prefix and never uses github.workflow (no caller/callee deadlock)")
for j in ["migrate", "deploy"] + VALIDATION:
    check("concurrency" not in jobs[j], f"{j}: no job-level concurrency (a second, nested group could deadlock with the workflow-level one)")

# every precondition that needs no remote mutation precedes the first remote command (Migration)
order = [s.get("name", "") for s in jobs["migrate"]["steps"]]
apply_i = order.index("Apply pending migrations (this target's D1 only)")
for must in ("Verify trusted target and credentials (fail closed)", "Preview identity and ledger ownership (collision fails here)",
             "Preflight source config — Preview binding / auth isolation", "Confirm the exact migration target (read-only config check)",
             "Stale-SHA guard (branch must still be at this commit)"):
    check(order.index(must) < apply_i, f"Migration: '{must}' runs before the remote migration")
check([s.get("name") for s in jobs["migrate"]["steps"] if "migrate.sh" in s.get("run", "")] == ["Apply pending migrations (this target's D1 only)"], "migrate.sh is invoked only by the Apply step")
check(mig_steps["apply"]["if"] == "steps.fresh.outputs.stale != 'true'", "a superseded run never applies a migration")
check("guard.sh\" fresh" in mig_steps["fresh"]["run"], "Migration branch-tip check fails closed (guard.sh fresh)")
check(jobs["migrate"]["steps"][order.index("Preview identity and ledger ownership (collision fails here)")]["if"] == "env.TARGET == 'preview'", "ledger collision check runs for Preview targets")
dep_runs = [s for s in jobs["deploy"]["steps"]]
guards = [s for s in dep_runs if "Stale-SHA guard" in s.get("name", "")]
pub_i = dep_runs.index(next(s for s in dep_runs if s.get("id") == "publish"))
check(len(guards) == 2 and all(dep_runs.index(x) < pub_i for x in guards), "Deploy checks for a superseded SHA at the start and again right before publish")
ident_i = dep_runs.index(next(s for s in dep_runs if s.get("id") == "identity"))
check(ident_i < pub_i, "Deploy re-checks Preview ownership before publishing")
check(all("env.STALE != 'true'" in str(s.get("if", "")) for s in dep_runs if s.get("id") in ("publish", "smoke", "verified")), "a stale run neither publishes, smokes nor verifies")

# ---------------------------------------------------------------- 7. preview identity, effective config, smoke, recovery
ident = next(s for s in jobs["deploy"]["steps"] if s.get("id") == "identity")
check("guard.sh\" identity" in ident["run"] and "HOST_WORKER" in jobs["deploy"]["env"], "Preview identity (stable branch slug on the hosting Worker) and ledger ownership come from guard.sh")
names = [s.get("name", "") for s in jobs["deploy"]["steps"]]
check(names.index("Preflight source config — Preview binding / auth isolation") < names.index("Build, validate effective Wrangler config, publish"), "source bindings are validated before the build; effective (generated) config inside deploy.sh before publish")
check(any("smoke" in n.lower() for n in names) and any("Preview recovery" in n for n in names) and any("Production recovery" in n for n in names), "smoke + Preview and Production recovery steps exist")
prod_rec = next(s for s in jobs["deploy"]["steps"] if s.get("name", "").startswith("Production recovery"))
check("failure()" in prod_rec["if"] and "steps.smoke.outcome == 'failure'" in prod_rec["if"] and "exit 1" in prod_rec["run"], "Production recovery only after a failed smoke and keeps the run red")
check("NOT reverted" in prod_rec["run"] and "never" in prod_rec["name"].lower(), "Production recovery never claims to roll back D1")
check("wrangler preview delete" not in "\n".join(s.get("run", "").replace("\\`wrangler preview delete", "") for s in jobs["lifecycle"]["steps"] if "echo" not in s.get("run", "")),
      "branch delete never executes `wrangler preview delete` (Native Preview is retained)")
retire = next(s for s in jobs["lifecycle"]["steps"] if "Retire GitHub ledger" in s.get("name", ""))
check('gh.sh" retire' in retire["run"] and "Branch exists again" in retire["run"] and "Name is owned by" in retire["run"], "branch delete retires only this branch's ledger (and not if recreated / owned by another ref)")
pdev = yaml.dump(jobs["persistent-dev"])
check("CLOUDFLARE_DEV_API_TOKEN" in pdev and jobs["persistent-dev"]["environment"] == "${{ inputs.app_name }}/dev", "Hybrid Dev uses its own token and protected Environment")
check("cron" not in deploy_text.lower() or "consumer" not in deploy_text.lower(), "Deploy makes no Cron/Queue consumer claims for Native Previews")

# ---------------------------------------------------------------- 8. Shared Dev channel (independent of Native Preview)
DEVJOB = "dev-deploy"
inputs_def = doc[True]["workflow_call"]["inputs"]
check(inputs_def["preview_on_branch_push"]["default"] is True and inputs_def["shared_dev_on_branch_push"]["default"] is False,
      "interface: preview_on_branch_push is preserved; shared_dev_on_branch_push is new and defaults to false (no surprise Dev deployments)")
check(all(k in inputs_def for k in ("app_url_dev", "dev_deploy_cmd", "dev_smoke_cmd", "build_env_dev", "dev_build_cmd", "dev_wrangler_env", "dev_worker_name")), "interface: Dev URL, deploy/smoke commands, Dev build env, Wrangler env and Worker identity inputs exist")
check("CLOUDFLARE_DEV_API_TOKEN" in doc[True]["workflow_call"]["secrets"] and "CLOUDFLARE_DEV_ACCOUNT_ID" in doc[True]["workflow_call"]["secrets"], "interface: Dev credentials are the existing CLOUDFLARE_DEV_* secrets")
check(set(needs_of(DEVJOB)) == set(VALIDATION) | {"migrate", "deploy"}, "Shared Dev needs the six validation jobs (+ Migration/Deploy for ordering only)")
check(jobs["release"]["if"].count("dev-deploy") == 0 and needs_of("release") == ["deploy"], "Release depends on Production Deploy only (Shared Dev never gates or triggers it)")
check("needs.migrate.result" not in jobs[DEVJOB]["if"] and "needs.deploy.result" not in jobs[DEVJOB]["if"], "Shared Dev does not read the Preview Migration/Deploy results (they order it, never gate it)")

# 8.1 four toggle combinations (+ main, + validation-only)
def combo(feature, dev, ref=FEAT, d1=False):
    inp = {"preview_on_branch_push": feature, "shared_dev_on_branch_push": dev, "d1_migrations": d1}
    needs = vres(migrate="skipped" if not (d1 and feature) else "success")
    return runs("deploy", scenario("push", ref, inp, needs)), runs(DEVJOB, scenario("push", ref, inp, needs)), runs("migrate", scenario("push", ref, inp, needs))
check(combo(True, True)[:2] == (True, True), "toggles: feature=true shared_dev=true -> both deploy")
check(combo(True, False)[:2] == (True, False), "toggles: feature=true shared_dev=false -> Native Preview only")
check(combo(False, True)[:2] == (False, True), "toggles: feature=false shared_dev=true -> Shared Dev only")
check(combo(False, False)[:2] == (False, False), "toggles: feature=false shared_dev=false -> validation only")
check(combo(False, True, d1=True)[2] is False and combo(True, True, d1=True)[2] is True, "Preview Migration follows the Preview switch only; Shared Dev has its own migration step")
check(combo(True, True, MAIN)[:2] == (True, False) and combo(False, False, MAIN)[0] is True, "main follows the Production path whatever the switches say; Shared Dev never runs on main")
check(runs("lint", scenario("push", FEAT, {"preview_on_branch_push": False, "shared_dev_on_branch_push": False})), "validation runs with both channels disabled")

# 8.2 gating
sd = lambda **kw: scenario(**dict(dict(event="push", ref=FEAT, inputs={"shared_dev_on_branch_push": True}, needs=vres(migrate="skipped")), **kw))
check(runs(DEVJOB, sd(ref="refs/heads/other/branch")), "Shared Dev: any branch deploys the same Dev")
check(not runs(DEVJOB, sd(event="pull_request", ref="refs/pull/1/merge", head_repo="o/r")) and not runs(DEVJOB, sd(event="pull_request", ref="refs/pull/1/merge", head_repo="fork/r")), "Shared Dev: never on a PR (same-repo or fork)")
check(not runs(DEVJOB, sd(event="delete")) and not runs(DEVJOB, sd(ref="refs/tags/v1.0.0")), "Shared Dev: never on branch delete (the Dev Worker is retained) or a tag")
check(not runs(DEVJOB, sd(event="workflow_dispatch", ref="refs/heads/dev", inputs={"shared_dev_on_branch_push": True, "dispatch_target": "persistent-dev"})), "Shared Dev: the manual persistent-dev dispatch is a different path")
check(not runs(DEVJOB, sd(inputs={"shared_dev_on_branch_push": True, "deployment_strategy": "production-only"})), "Shared Dev: production-only Worker never deploys Dev")
check(runs(DEVJOB, sd(inputs={"shared_dev_on_branch_push": True, "deployment_strategy": "hybrid"})), "Shared Dev: also available with the hybrid strategy")
for v in VALIDATION:
    for bad in ("failure", "cancelled", "skipped"):
        check(not runs(DEVJOB, sd(needs=vres(migrate="skipped", **{v: bad}))), f"Shared Dev: blocked when configured {v} is {bad}")
    if v in CMD_INPUT:
        check(runs(DEVJOB, sd(inputs={"shared_dev_on_branch_push": True, CMD_INPUT[v]: ""}, needs=vres(migrate="skipped", **{v: "skipped"}))), f"Shared Dev: an intentionally disabled {v} does not block")
for r in ("failure", "cancelled", "skipped", "success"):
    check(runs(DEVJOB, sd(needs=vres(migrate=r, deploy=r))), f"Shared Dev: Preview Migration/Deploy {r} neither blocks nor is required (optional channel skips do not block unrelated work)")
check(not runs("deploy", scenario("push", FEAT, {"preview_on_branch_push": True}, vres(migrate="skipped", build="failure"))), "a failed validation still blocks the Preview Deploy (unchanged)")

# 8.3 config guard
check(runs("config-guard", scenario("push", FEAT, {"deployment_strategy": "production-only", "shared_dev_on_branch_push": True})) and runs("config-guard", scenario("push", MAIN, {"deployment_strategy": "production-only", "shared_dev_on_branch_push": True})),
      "config guard: production-only + shared_dev_on_branch_push fails the run on any push")
check(not runs("config-guard", scenario("push", FEAT, {"deployment_strategy": "production-only"})) and not runs("config-guard", scenario("push", FEAT, {"shared_dev_on_branch_push": True})), "config guard: silent for valid configurations")
check("exit 1" in jobs["config-guard"]["steps"][0]["run"] and "secrets." not in yaml.dump(jobs["config-guard"]), "config guard fails closed and holds no secrets")

# 8.4 concurrency: one Dev Worker => one queue across ALL branches
check(g(FEAT, {"shared_dev_on_branch_push": True}) == "cfv2-app-shared-dev" == g("refs/heads/other", {"shared_dev_on_branch_push": True}), "group: all branches of a non-D1 app with Shared Dev queue on one group (no concurrent Dev deploys)")
check(g(FEAT, {"shared_dev_on_branch_push": True, "d1_migrations": True}) == "cfv2-app-preview-d1-app" == g("refs/heads/other", {"shared_dev_on_branch_push": True, "d1_migrations": True}), "group: a D1 app keeps the one shared D1 group (covers Preview and Dev migrations)")
check(g(FEAT, {"shared_dev_on_branch_push": False}) != g("refs/heads/other", {"shared_dev_on_branch_push": False}), "group: without Shared Dev, D1-less branches stay independent")
check(g(MAIN, {"shared_dev_on_branch_push": True}) == "cfv2-app-prod", "group: Production is never serialized behind Dev")
dj = jobs[DEVJOB]
check(dj["concurrency"] == jobs["persistent-dev"]["concurrency"] and dj["concurrency"]["cancel-in-progress"] is False and dj["concurrency"]["group"].startswith("cfv2-dev-"),
      "Dev job-level group equals the manual persistent-dev group (automatic and manual Dev never overlap) and differs from the workflow-level group (no deadlock)")
check(all(not g_(r).startswith("cfv2-dev-") for g_ in (g,) for r in (MAIN, FEAT)), "workflow-level groups can never equal the job-level Dev group")

# 8.5 credentials / environments
for tgt, ref in (("dev", FEAT),):
    c = scenario("push", ref, secrets=SECRETS)
    check(render(dj["env"]["CLOUDFLARE_API_TOKEN"], c) == "DEV-TOKEN" and "CLOUDFLARE_PREVIEW_API_TOKEN" not in c.secret_reads and "CLOUDFLARE_API_TOKEN" not in c.secret_reads,
          "Shared Dev gets ONLY the Dev token (Preview and Production secrets never read)")
c = scenario("push", FEAT, secrets={k: v for k, v in SECRETS.items() if k != "CLOUDFLARE_DEV_API_TOKEN"})
check(render(dj["env"]["CLOUDFLARE_API_TOKEN"], c) == "", "Shared Dev: a missing Dev token stays EMPTY (no fallback)")
dtxt = yaml.dump(dj)
check("CLOUDFLARE_PREVIEW" not in dtxt and "secrets.CLOUDFLARE_API_TOKEN" not in dtxt and "secrets.CLOUDFLARE_ACCOUNT_ID" not in dtxt and "PREVIEW_SECRETS" not in dtxt, "Shared Dev never references Production/Preview credentials or Preview-only secrets")
check(dj["environment"]["name"] == "${{ inputs.app_name }}/dev" == jobs["persistent-dev"]["environment"], "Shared Dev runs in the same protected <app>/dev Environment as persistent Dev")
check(dj["environment"]["url"] == "${{ inputs.app_url_dev }}", "Shared Dev shows the stable Dev URL")
check(dj["permissions"]["contents"] == "read" and not any(k == "pull-requests" for k in dj["permissions"]), "Shared Dev is read-only on contents and writes no PR comments")
check(dj["env"]["BUILD_ENV"] == "${{ inputs.build_env_dev }}" and "build_env_preview" not in dtxt and "build_env_prod" not in dtxt, "Shared Dev builds with the Dev build env only (no fallback to Preview/Production env)")
check(render(dj["env"]["CLOUDFLARE_ENV"], scenario("push", FEAT, {"dev_wrangler_env": "dev"})) == "dev", "Shared Dev selects the named Wrangler environment")
ck = [s_ for s_ in dj["steps"] if str(s_.get("uses", "")).startswith("actions/checkout")]
check(ck and all(s_["with"].get("persist-credentials") is False for s_ in ck), "Shared Dev checkouts do not persist the GitHub token")

# 8.6 step order: nothing durable changes before it must; stale runs skip everything
dn = [s_.get("name", "") for s_ in dj["steps"]]
ix = lambda prefix: next(i for i, n in enumerate(dn) if n.startswith(prefix))
check(ix("Stale-SHA guard (start)") < ix("Dev build") < ix("Audit effective Wrangler config") < ix("Stale-SHA guard (before migration") < ix("Apply pending Dev migrations") < ix("Publish Shared Dev") < ix("Smoke"),
      "Shared Dev order: stale guard -> build -> effective-config audit -> stale guard -> migration -> publish -> smoke (a failed build never changes Dev or its D1)")
check(ix("Confirm the exact Dev migration target") < ix("Apply pending Dev migrations") and ix("Validate Shared Dev") < ix("Confirm the exact"), "Shared Dev: migration target is validated read-only before any remote mutation")
byname = {s_.get("name", ""): s_ for s_ in dj["steps"]}
for n in ("Dev build (a failure here leaves the running Dev Worker untouched)", "Audit effective Wrangler config (Dev Worker identity, no Production resources)",
          "Apply pending Dev migrations (Dev D1 only)", "Publish Shared Dev", "Smoke — Shared Dev URL (fails closed)"):
    check("env.STALE != 'true'" in byname[n]["if"], f"Shared Dev: '{n.split(' (')[0]}' is skipped for a superseded run")
check("migrate.sh" in byname["Apply pending Dev migrations (Dev D1 only)"]["run"] and "d1_migrations" in byname["Apply pending Dev migrations (Dev D1 only)"]["if"], "Shared Dev migrations reuse migrate.sh (target dev) and only run for D1 Workers")
check('--target dev' in byname["Confirm the exact Dev migration target (read-only config check)"]["run"], "Shared Dev resolves the Dev migration target explicitly (env.<dev>.d1_databases, never Production)")
check("dev-config-check" in byname["Audit effective Wrangler config (Dev Worker identity, no Production resources)"]["run"], "Shared Dev audits the adapter-generated effective Wrangler config")
smoke_d = byname["Smoke — Shared Dev URL (fails closed)"]
check(smoke_d["env"]["TARGET_URL"] == "${{ inputs.app_url_dev }}" and smoke_d["env"]["DEPLOY_TARGET"] == "dev" and smoke_d["env"]["SMOKE_CMD"] == "${{ inputs.dev_smoke_cmd }}" and "smoke.sh" in smoke_d["run"],
      "Shared Dev smoke runs the shared smoke.sh against app_url_dev with dev_smoke_cmd")
check(all(s_.get("continue-on-error") is not True for s_ in dj["steps"]) and "continue-on-error" not in dj, "Shared Dev: failures are never hidden by continue-on-error")
check(dj["outputs"]["deployed_and_verified"].endswith("|| 'false' }}") and "steps.verified" in dj["outputs"]["deployed_and_verified"], "Shared Dev reports verified only from the final marker step")
# failure outcomes
check("failure()" in byname["Dev — mark unverified"]["if"] and "steps.smoke.outcome == 'failure'" in byname["Dev — mark unverified"]["if"], "Dev smoke failure: job fails and the deployment is marked UNVERIFIED")
check("steps.publish.outcome != 'success'" in byname["Dev — failed before publish"]["if"] and "unchanged" in byname["Dev — failed before publish"]["run"], "Dev build/audit/migration failure: explicit 'Dev unchanged' outcome")
check("last-good" in byname["Dev recovery — find last known-good SHA"]["run"] and "manual recovery required" in byname["Dev recovery — find last known-good SHA"]["run"], "Dev recovery: finds the last known-good SHA or states manual recovery is required")
rec = byname["Dev recovery — redeploy last good with the full verification"]
rr = rec["run"]
check(rr.index("dev-config-check") < rr.index('bash -eo pipefail -c "$DEPLOY_CMD"') < rr.index("smoke.sh") and "Run remains FAILED" in rr and "BUILD_CMD" in rr,
      "Dev recovery: last-good tree gets build, Worker-identity/Production-resource audit, publish, then the FULL smoke; run stays red")
check(rec["env"]["EXPECTED_SHA"] == "${{ steps.good.outputs.sha }}" and rec["env"]["SHA_PATH"] == "${{ inputs.sha_path }}" and rec["env"]["SMOKE_CMD"] == "${{ inputs.dev_smoke_cmd }}" and rec["env"]["DEPLOY_TARGET"] == "dev" and rec["env"]["TARGET_URL"] == "${{ inputs.app_url_dev }}",
      "Dev recovery smoke checks the LAST-GOOD sha at sha_path and runs dev_smoke_cmd against app_url_dev")
check("Recovery FAILED" in byname["Dev recovery failed"]["run"] or "recovery FAILED" in byname["Dev recovery failed"]["run"], "Dev recovery failure is reported explicitly")
# retention
all_run = "\n".join(s_.get("run", "") for j in jobs.values() for s_ in j.get("steps", []))
check("wrangler delete" not in all_run and "wrangler preview delete" not in "\n".join(s_.get("run", "").replace("\\`wrangler preview delete", "") for s_ in jobs["lifecycle"]["steps"] if "echo" not in s_.get("run", "")), "no job deletes a Worker; branch deletion retains the Shared Dev Worker and Native Previews")
check(not re.search(r"CLOUDFLARE_DEV|dev-deploy|shared_dev|app_url_dev|dev_deploy_cmd|/dev\\b(?!/null)", yaml.dump(jobs["lifecycle"])), "branch-delete lifecycle never references Dev")
check(runs("persistent-dev", scenario("workflow_dispatch", "refs/heads/dev", **dev_ok)) and next(x for x in jobs["persistent-dev"]["steps"] if x.get("name") == "Deploy persistent Dev")["env"]["CMD"] == "${{ inputs.dev_deploy_cmd }}", "persistent-dev manual dispatch (legacy contract) still runs dev_deploy_cmd")
check("shared_dev_on_branch_push" not in yaml.dump(jobs["persistent-dev"]), "persistent-dev does not depend on the automatic Shared Dev switch")
check("playwright" not in dtxt.lower() and sum(1 for s_ in dj["steps"] if "upload-artifact" in str(s_.get("uses", ""))) == 0, "Shared Dev installs no browsers and uploads no artifacts")

# 8.7 review fixes (PR #64): shared-D1 guard, mandatory build, mandatory identity
guard_d1 = byname["Shared-D1 guard (a failed Preview migration must not be followed by a Dev migration)"]
check(dn.index(guard_d1["name"]) < ix("Start Dev ledger entry") < ix("Dev build") and dn.index(guard_d1["name"]) < ix("Apply pending Dev migrations"), "shared-D1 guard runs before the Dev build, ledger and any Dev migration")
check(guard_d1["env"]["PREVIEW_MIGRATE_RESULT"] == "${{ needs.migrate.result }}" and "d1-shared" in guard_d1["run"] and "failure" in guard_d1["run"] and "cancelled" in guard_d1["run"] and "exit 1" in guard_d1["run"],
      "a failed/cancelled Preview Migration on the D1 that Shared Dev also uses stops Dev before it mutates anything")
check("needs.migrate.result" not in dj["if"], "...while Shared Dev stays independent of the Preview channel at the job level (distinct D1 or Preview off still deploys)")
val = byname["Validate Shared Dev target, configuration and credentials (fail closed)"]
check("dev_build_cmd" in val["env"]["BUILD_CMD"] and 'D1_ON" = true' in val["run"] and "requires dev_build_cmd" in val["run"], "Shared Dev with d1_migrations requires dev_build_cmd (build precedes the Dev D1 migration)")
check("requires dev_worker_name" in val["run"], "Shared Dev requires dev_worker_name")
aud = byname["Audit effective Wrangler config (Dev Worker identity, no Production resources)"]
check(aud["if"] == "env.STALE != 'true'" and "dev-config-check" in aud["run"], "the Worker identity / resource audit is unconditional for every non-stale Shared Dev run")

check("dev_binding_allow" in inputs_def and "--allow" in aud["run"] and "--allow" in rr, "dev_binding_allow reaches both the normal audit and the recovery audit")

# 8.8 PR #64 review round 2: resource policy separation, command contract, recovery identity
dtxt_all = yaml.dump(dj)
check("preview_deny_resources" not in dtxt_all and "preview_binding_allow" not in dtxt_all and "dev_deny_resources" in dtxt_all, "Shared Dev audits use dev_deny_resources/dev_binding_allow only (never the Preview policy inputs)")
check("preview_deny_resources" not in yaml.dump(jobs["persistent-dev"]) and "dev_deny_resources" in yaml.dump(jobs["persistent-dev"]), "manual Dev audit also uses the Dev policy")
check("preview_deny_resources" in yaml.dump(jobs["deploy"]) and "dev_deny_resources" not in yaml.dump(jobs["deploy"]) and "dev_binding_allow" not in yaml.dump(jobs["deploy"]), "Native Preview keeps its own policy and ignores the Dev inputs")
check("--prod-url" in aud["run"], "the Dev audit receives the Production URL (route/host check)")
pd_steps = {x.get("name"): x for x in jobs["persistent-dev"]["steps"]}
legacy = [x for x in jobs["persistent-dev"]["steps"] if x.get("if", "").startswith("inputs.dev_build_cmd == ''")]
split = [x for x in jobs["persistent-dev"]["steps"] if "inputs.dev_build_cmd != ''" in x.get("if", "")]
check(len(legacy) == 2 and len(split) >= 8, "persistent-dev: legacy steps (dev_build_cmd empty) and split-contract steps (dev_build_cmd set) are mutually exclusive")
check(jobs["persistent-dev"]["if"] == "github.event_name == 'workflow_dispatch' && inputs.dispatch_target == 'persistent-dev' && inputs.deployment_strategy == 'hybrid' && inputs.persistent_dev_ref != '' && github.ref == inputs.persistent_dev_ref" and jobs["persistent-dev"]["environment"] == "${{ inputs.app_name }}/dev",
      "persistent-dev keeps the approved-ref gate and protected Environment")
rec_ok = pd_steps["Record verified manual deployment"]
check("steps.smoke.outcome == 'success'" in rec_ok["if"] and "steps.publish.outcome == 'success'" in rec_ok["if"], "a manual deployment is recorded verified only after publish AND smoke succeeded")
check(all("gh.sh" not in x.get("run", "") for x in legacy), "legacy manual runs never write the known-good ledger")
check("rebind-env" in rr and "--from \"$RUN_SHA\" --to \"$GOOD_SHA\"" in rr and "BUILD_ENV" in rr and "load_env_file" in rr, "recovery rebinds build env and version from the failed SHA to the last-good SHA")
check("VITE_APP_VERSION" not in rec["env"] and "APP_VERSION_IN" in rec["env"], "recovery never reuses the failed run's version value unchanged")
check("NOT verified" in rr and "NOT reverted" in rr, "recovery states when commit identity is unverified and that D1 is not reverted")

sys.exit(fail)
