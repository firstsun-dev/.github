#!/usr/bin/env python3
"""Static structure checks that actionlint cannot make for this repository's reusable-workflow pipeline (no GitHub expression evaluation).
 1. every `uses: $/<path>` is static, has no @ref and exists in this repository (actionlint does not know the self-repository syntax);
 2. every `with:` key of an internal reusable-workflow call is a declared input of the callee, and every required input is passed;
 3. every `inputs.X` / `needs.J` / `needs.J.outputs.O` / `jobs.J.outputs.O` reference resolves (declared input / existing job / declared output);
 4. fail-closed gates: a job named Gate runs under `always()` and `needs` EVERY other job of its workflow;
 5. the retired fixed-category inputs and the four-kind vocabulary do not exist in any workflow."""
import glob
import os
import re
import sys

import yaml

errors = []
WF = {p: yaml.safe_load(open(p)) for p in sorted(glob.glob(".github/workflows/_cf-worker-*.yml")) if not p.endswith("_cf-worker-preview-gc.yml")}
call = lambda d: d[True]["workflow_call"]  # noqa: E731  (PyYAML parses the key `on` as True)

for path, d in WF.items():
    declared = set(call(d).get("inputs", {}))
    jobs = d["jobs"]
    text = "\n".join(re.sub(r"(^|\s)#.*", "", l) for l in open(path).read().split("\n"))  # comments are not code
    for ref in re.findall(r"(?m)^\s*(?:-\s*)?uses:\s*(\$/\S+)\s*$", text):
        if "@" in ref:
            errors.append(f"{path}: '{ref}' must not carry a ref")
        target = ref[2:]
        if not os.path.exists(target) and not os.path.exists(f"{target}/action.yml"):
            errors.append(f"{path}: '{ref}' does not exist in this repository")
    for name in set(re.findall(r"(?<![\w.])inputs\.([A-Za-z_][\w]*)", text)):
        if name not in declared:
            errors.append(f"{path}: inputs.{name} is not a declared workflow_call input")
    for jid, job in jobs.items():
        for n in (job.get("needs") or []) if isinstance(job.get("needs"), list) else [job.get("needs")] if job.get("needs") else []:
            if n not in jobs:
                errors.append(f"{path}: job '{jid}' needs unknown job '{n}'")
        callee = job.get("uses", "")
        if callee.startswith("$/"):
            cpath = callee[2:]
            cin = call(yaml.safe_load(open(cpath)))
            cdecl = cin.get("inputs", {})
            for k in job.get("with", {}):
                if k not in cdecl:
                    errors.append(f"{path}: job '{jid}' passes '{k}' which {cpath} does not declare")
            for k, v in cdecl.items():
                if v.get("required") and k not in job.get("with", {}):
                    errors.append(f"{path}: job '{jid}' does not pass required input '{k}' of {cpath}")
            for k in job.get("secrets", {}) if isinstance(job.get("secrets"), dict) else []:
                if k not in cin.get("secrets", {}):
                    errors.append(f"{path}: job '{jid}' passes secret '{k}' which {cpath} does not declare")
    # outputs referenced through needs.<job>.outputs.<name> / jobs.<job>.outputs.<name>
    for scope, j, o in re.findall(r"\b(needs|jobs)\.([\w-]+)\.outputs\.([\w-]+)", text):
        if j not in jobs:
            continue
        job = jobs[j]
        if "uses" in job:
            outs = call(yaml.safe_load(open(job["uses"][2:]))).get("outputs", {}) if job["uses"].startswith("$/") else None
        else:
            outs = job.get("outputs", {})
        if outs is not None and o not in outs:
            errors.append(f"{path}: {scope}.{j}.outputs.{o} is not an output of job '{j}'")
    # fail-closed aggregators
    for jid, job in jobs.items():
        if job.get("name") == "Gate":
            if "always()" not in str(job.get("if", "")):
                errors.append(f"{path}: Gate job '{jid}' must run under always()")
            others = {k for k in jobs if k != jid}
            if set(job.get("needs", [])) != others:
                errors.append(f"{path}: Gate job '{jid}' must need every other job (missing {sorted(others - set(job.get('needs', [])))}, extra {sorted(set(job.get('needs', [])) - others)})")
    # retired vocabulary
    for bad in ("lint_cmd", "typecheck_cmd", "test_unit_cmd", "test_integration_cmd", "deployment_strategy", "preview_on_branch_push", "shared_dev_on_branch_push",
                "shared_dev_native_preview_on_branch_push", "persistent_dev_ref", "native_dev_url", "native-branch", "persistent-dev"):
        if re.search(rf"\b{re.escape(bad)}\b", text):
            errors.append(f"{path}: retired symbol '{bad}'")

# ---- credential boundary (G1 review P0): the job that runs application build code holds no credential; credential-bearing jobs run no application build ----
pub = WF[".github/workflows/_cf-worker-v2.yml"]["jobs"]
dlv = WF[".github/workflows/_cf-worker-delivery.yml"]["jobs"]
if "build" in pub:
    errors.append("the artifact Build job is gone: Deploy builds locally (no Actions artifact)")
CRED = re.compile(r"CLOUDFLARE|GH_TOKEN|GITHUB_TOKEN|secrets\.(?!SMOKE)|github\.token")
for wf, jid in ((pub, "migration"), (dlv, "deploy")):
    job = wf[jid]
    if job.get("env") and CRED.search(yaml.dump(job["env"])):
        errors.append(f"{jid}: credentials must be STEP-level env, never job-level env")
    if "artifact" in yaml.dump(job).replace("artifact_dir", "").replace("cfv2-artifact", "").replace("artifact-verify", ""):
        errors.append(f"{jid}: must not use GitHub Actions artifacts")
    steps = job.get("steps", [])
    if jid == "deploy" and not any((st.get("with") or {}).get("stage") == "build" for st in steps):
        errors.append("deploy: has no stage build step")
    if jid == "migration" and any((st.get("with") or {}).get("stage") == "build" for st in steps):
        errors.append("migration: must not build (the Deploy job builds the one copy)")
    for i, st in enumerate(steps):
        stage = (st.get("with") or {}).get("stage")
        if CRED.search(yaml.dump(st)) and stage not in ("precheck", "migrate", "publish"):
            errors.append(f"{jid} step {i} ({st.get('name') or st.get('id')}): credentials may only reach the precheck / migrate / publish steps, never the checkout, the build or any other step")
        if stage == "build" and "env" in st:
            errors.append(f"{jid}: the build step must have no env")
    stages = [(st.get("with") or {}).get("stage") for st in steps if (st.get("with") or {}).get("stage")]
    if stages != (["precheck", "migrate"] if jid == "migration" else ["build", "publish"]):
        errors.append(f"{jid}: unexpected stage order {stages}")
    if "environment" not in job:
        errors.append(f"{jid}: must run in the target's GitHub Environment")
_ro = str(dlv["deploy"].get("runs-on", ""))
if not ("ubuntu-24.04" in _ro and "head.repo.full_name != github.repository" in _ro and "inputs.runner" in _ro):
    errors.append("deploy builds PR code: runs-on must be hosted ubuntu-24.04 for a fork pull request, otherwise the caller's ephemeral inputs.runner")
for jid in ("migration", "delivery"):
    need = set(pub[jid].get("needs", []))
    if "ci" not in need:
        errors.append(f"public job '{jid}' must need ci (the CI Gate)")
    if "validation" not in str(pub[jid].get("if", "")):
        errors.append(f"public job '{jid}' must be conditioned on the CI Gate verdict")
for jid, job in dlv.items():
    for st in job.get("steps", []):
        w = st.get("with", {}) or {}
        blob = yaml.dump(st)
        if jid in ("deploy", "rollback") and ("pnpm" in blob or "actions/setup" in blob and "$/" in blob):
            errors.append(f"credential-bearing delivery job '{jid}' must not install application dependencies")
act = yaml.safe_load(open("actions/cf-deploy/action.yml"))["runs"]["steps"]
for st in act:
    cond = str(st.get("if", ""))
    if str(st.get("uses", "")).startswith("$/actions/setup") and ("'publish'" in cond or "'migrate'" in cond or "'precheck'" in cond):
        errors.append("the engine must not install application dependencies in the migrate / publish / precheck stages")
    if "engine.sh\" build" in str(st.get("run", "")) and "inputs.stage == 'build'" not in cond:
        errors.append("the build phase must be limited to stage build")
for st in act:
    n = str(st.get("name", "")); cond = str(st.get("if", ""))
    if n.startswith("Stale-SHA guard (start)") and ("'build'" in cond or "!= 'verify'" in cond):
        errors.append("the stale guard needs a GitHub token: it must run only for precheck / migrate / publish, never the token-less build")
    if n == "Setup dependencies" and "'rollback'" in cond:
        errors.append("the credential-bearing rollback must not install application dependencies")
for jid in ("smoke", "rollback", "deploy"):
    for st in dlv[jid].get("steps", []):
        if str(st.get("uses", "")).startswith("actions/checkout") and "head.sha" in str(st.get("with", {})):
            errors.append(f"delivery job '{jid}' must check out the tested merge candidate (github.sha), not the PR head")
names = [str(st.get("name", "")) for st in act]
iv = next((i for i, n in enumerate(names) if n.startswith("Verify the artifact")), -1)
ip = next((i for i, n in enumerate(names) if n.startswith("Prepare")), -1)
if iv < 0 or ip < 0 or iv > ip:
    errors.append("the artifact must be verified BEFORE Prepare (the ledger entry is the first remote effect)")
if "ubuntu-24.04" not in open(".github/workflows/_cf-worker-ci.yml").read():
    errors.append("the CI workflow must pin fork pull requests to the approved hosted runner")
if re.search(r"fork_runner", open(".github/workflows/_cf-worker-v2.yml").read()):
    errors.append("fork_runner is a caller-supplied runner label and must not exist")

# ---- Preview Smoke boundary (G1 review P0): PR-controlled smoke code runs ONLY in a job without any secret; the credential-bearing Smoke job never runs it ----
asmk = dlv.get("app-smoke")
if not asmk:
    errors.append("delivery workflow has no credential-free `app-smoke` job")
else:
    atext = yaml.dump(asmk)
    for bad in ("secrets.", "GH_TOKEN", "github.token", "CLOUDFLARE", "SMOKE_USERNAME", "SMOKE_PASSWORD", "PREVIEW_SECRETS", "deployments", "pull-requests"):
        if bad in atext:
            errors.append(f"app-smoke job must not reference '{bad}'")
    if "environment" in asmk:
        errors.append("app-smoke job must not use a deployment environment")
    if asmk.get("permissions") != {"contents": "read"}:
        errors.append("app-smoke job permissions must be exactly {contents: read}")
    _rs = str(asmk.get("runs-on", ""))
    if not ("ubuntu-24.04" in _rs and "head.repo.full_name != github.repository" in _rs and "inputs.runner" in _rs):
        errors.append("app-smoke job runs-on must be: hosted ubuntu-24.04 for a fork pull request (head.repo.full_name != github.repository), otherwise inputs.runner (an EPHEMERAL pool)")
    if "'preview'" not in str(asmk.get("if", "")):
        errors.append("app-smoke job must be limited to the Preview target")
sm = dlv["smoke"]
if "app-smoke" not in (sm.get("needs") or []):
    errors.append("the Smoke job must need app-smoke (it requires its result)")
for st in sm.get("steps", []):
    blob = yaml.dump(st)
    if str(st.get("uses", "")).startswith("actions/checkout") and "!= 'preview'" not in str(st.get("if", "")):
        errors.append("the Smoke job must not check out a Preview's PR code")
    if "PREVIEW_SECRETS" in blob:
        errors.append("the Smoke job must not receive PREVIEW_SECRETS")
    if "SMOKE_USERNAME" in blob and "!= 'preview'" not in blob:
        errors.append("the Smoke job must not pass smoke credentials for a Preview")
for st in act:
    cond = str(st.get("if", ""))
    if str(st.get("uses", "")).startswith("$/actions/setup") and "'verify'" in cond and "!= 'preview'" not in cond:
        errors.append("the credential-bearing verify stage must not install a Preview's dependencies")
rb = dlv["rollback"]
if "== 'production'" not in str(rb.get("if", "")) or any(str(st.get("uses", "")).startswith("$/actions/setup") for st in rb.get("steps", [])):
    errors.append("the rollback job is Production only and runs no application setup (Preview / Dev are fixed forward)")
# ---- sensitive smoke configuration contract: the SMOKE_ENV secret (never a public input, never inputs_json) reaches ONLY the trusted Smoke / Rollback jobs ----
v2 = WF[".github/workflows/_cf-worker-v2.yml"]
if "smoke_env" in call(v2).get("inputs", {}) or "smoke_vars" not in call(v2).get("inputs", {}):
    errors.append("_cf-worker-v2.yml: `smoke_env` must not be a (public) workflow input; the non-sensitive `smoke_vars` input replaces it")
if "SMOKE_ENV" not in (call(v2).get("secrets") or {}) or "SMOKE_ENV" not in (call(WF[".github/workflows/_cf-worker-delivery.yml"]).get("secrets") or {}):
    errors.append("the SMOKE_ENV secret must be declared by the v2 and delivery workflows")
for path, d in WF.items():
    for jn, job in d["jobs"].items():
        uses_it = "secrets.SMOKE_ENV" in str(job)
        in_delivery = path.endswith("_cf-worker-delivery.yml")
        if uses_it and not ((in_delivery and jn in ("smoke", "rollback")) or (path.endswith("_cf-worker-v2.yml") and jn == "delivery")):
            errors.append(f"{path}: job '{jn}' must not receive the SMOKE_ENV secret (only the trusted Smoke / Rollback jobs do)")
        if jn == "app-smoke" and in_delivery and re.search(r"secrets\.|smoke_env|smoke_username|smoke_password|preview_secrets", str(job)):
            errors.append("the app-smoke job (PR-controlled code) must not reference any secret or smoke credential")
    for ln in open(path).read().split("\n"):
        if re.search(r"smoke_env\s*:\s*\$\{\{\s*(inputs|toJSON)", ln):
            errors.append(f"{path}: smoke credentials must not be taken from a public input: {ln.strip()}")

# Smoke Report names: built from the validated descriptor (environment + slot) and run_attempt in a step of the same job, never from the Build artifact name
# (empty on a deferred verify). Two apps / slots of one run, deferred verify included, must yield distinct names.
for jn, job in dlv.items():
    steps = job.get("steps", []) if isinstance(job, dict) else []
    for st in steps:
        if str(st.get("uses", "")).startswith("actions/upload-artifact") and "smoke-report" in str(st["with"].get("name", "")) + str([x.get("run", "") for x in steps]):
            gen = [x for x in steps if x.get("id") == "report"]
            if str(st["with"]["name"]) != "${{ steps.report.outputs.name }}" or len(gen) != 1:
                errors.append(f"{jn}: the smoke report upload must use the name generated by the `report` step")
                continue
            r = gen[0]["run"]
            if "artifact_name" in r or "environment" not in r or ".slot" not in r or "ATTEMPT" not in r:
                errors.append(f"{jn}: the smoke report name must come from the descriptor (environment + slot) and run_attempt, never the Build artifact name")
            nm = lambda env, slot: f"smoke-report-{env.replace('/', '-')}-{slot}-1"  # noqa: E731  (the step's ${env//\//-} substitution)
            if len({nm("blog/preview", "native-branch"), nm("www/preview", "native-branch"), nm("blog/dev", "persistent-dev")}) != 3:
                errors.append(f"{jn}: smoke report names are not unique per app and slot")

# The engine (engine.sh ctx) requires PR_NUMBER / PR_HEAD_REF / PR_HEAD_SHA whenever the event is a pull_request. Every job that runs the delivery engine
# (`$/actions/cf-deploy`) must therefore carry them at job level: a PR that delivers to Dev (dev_on_pr) runs Migrate as well, and a job without them dies in
# `engine.sh guard start` ("PR_NUMBER: pull request number") before any migration.
for path, d in WF.items():
    for jid, job in d["jobs"].items():
        if any("/actions/cf-deploy" in str(st.get("uses", "")) for st in job.get("steps", []) if isinstance(st, dict)):
            missing = [k for k in ("PR_NUMBER", "PR_HEAD_REF", "PR_HEAD_SHA") if k not in (job.get("env") or {})]
            if missing:
                errors.append(f"{path}: job '{jid}' runs the delivery engine but does not set {', '.join(missing)} at job level")
            # ...and the stale-SHA guard of a PR run reads `pulls/<n>` with the job's own token: without pull-requests: read it dies with HTTP 403 and "cannot read pull request".
            # Only the stages the action guards (cf-deploy/action.yml: `guard start` for precheck | migrate | publish) need it; the unprivileged build and app-smoke
            # stages never run the guard and deliberately hold contents: read only.
            stages = {str((st.get("with") or {}).get("stage", "")) for st in job.get("steps", []) if isinstance(st, dict) and "/actions/cf-deploy" in str(st.get("uses", ""))}
            if stages & {"precheck", "migrate", "publish"} and (job.get("permissions") or {}).get("pull-requests") not in ("read", "write"):
                errors.append(f"{path}: job '{jid}' runs the delivery engine but has no pull-requests read permission (the PR stale guard needs it)")

# `dev_on_pr` is on by default and only means something where the application HAS a Dev: every use in the engine workflow must be qualified by
# `inputs.dev_driver != 'none'` (the JS twin is devOnPrEffective in delivery.mjs), so the YAML expressions and the routing cannot drift apart.
_v2 = open(".github/workflows/_cf-worker-v2.yml").read()
for m in re.finditer(r"inputs\.dev_on_pr", _v2):
    if not _v2[m.end():].startswith(" && inputs.dev_driver != 'none'"):
        errors.append(f"_cf-worker-v2.yml: `inputs.dev_on_pr` (offset {m.start()}) is not followed by `&& inputs.dev_driver != 'none'`: it would claim a PR delivery for an app without a Dev")

for e in errors:
    print("FAIL", e)
print(f"workflow structure: {len(WF)} workflows, {'FAILED' if errors else 'clean'}")
sys.exit(1 if errors else 0)
