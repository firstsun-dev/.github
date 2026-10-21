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
check(set(jobs) == set(VALIDATION) | {"migrate", "deploy", "release", "lifecycle", "persistent-dev"},
      "job set is exactly the six validation jobs, Migration, Deploy, Release + lifecycle/persistent-dev handlers")
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
check(sum(1 for j in jobs if "deploy" in j or "publish" in j) == 1, "exactly one Deploy job implementation (no per-target deploy jobs)")

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

sys.exit(fail)
