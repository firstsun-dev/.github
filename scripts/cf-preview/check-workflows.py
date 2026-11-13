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
WF = {p: yaml.safe_load(open(p)) for p in sorted(glob.glob(".github/workflows/_cf-worker-*.yml")) if not p.endswith(("_cf-worker-template.yml", "_cf-worker-preview-gc.yml"))}
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

for e in errors:
    print("FAIL", e)
print(f"workflow structure: {len(WF)} workflows, {'FAILED' if errors else 'clean'}")
sys.exit(1 if errors else 0)
