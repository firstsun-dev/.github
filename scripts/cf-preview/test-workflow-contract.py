#!/usr/bin/env python3
"""Static contract checks for _cf-worker-v2.yml (no GitHub needed).

Regression for the heaven-www incident: a consumer without unit/integration/E2E commands has those
jobs *skipped*; GitHub's implicit success() then skipped every downstream job that lacked always()
(Preview and even Production) while the run stayed green.
"""
import sys
import yaml

jobs = yaml.safe_load(open(".github/workflows/_cf-worker-v2.yml"))["jobs"]
fail = 0


def check(ok, msg):
    global fail
    print(("PASS: " if ok else "FAIL: ") + msg)
    if not ok:
        fail = 1


def needs_of(name):
    n = jobs[name].get("needs", [])
    return [n] if isinstance(n, str) else list(n or [])


def ancestors(name, seen=None):
    seen = set() if seen is None else seen
    for n in needs_of(name):
        if n not in seen:
            seen.add(n)
            ancestors(n, seen)
    return seen


# jobs that are skipped by design for consumers that do not define the matching command
optional = {j for j in jobs if j.startswith("test-")}

for name, job in jobs.items():
    if ancestors(name) & optional and name != "test":
        cond = str(job.get("if", ""))
        check("always()" in cond, f"{name}: has an optional (skippable) test ancestor -> `if` must use always()")
        for n in needs_of(name):
            check(f"needs.{n}.result" in cond, f"{name}: gates explicitly on needs.{n}.result")

check("always()" in str(jobs["test"].get("if", "")), "test gate runs even when optional test jobs are skipped")
check("failure" in str(jobs["test"]["steps"][0].get("run", "")), "test gate fails on failed/cancelled test jobs")
check("action == 'production'" in str(jobs["production"].get("if", "")), "production job only on the production action")
check("action == 'preview'" in str(jobs["preview"].get("if", "")), "preview job only on the preview action")
env = yaml.dump(jobs["preview"].get("env", {}))
check("secrets.CLOUDFLARE_API_TOKEN" not in env, "preview job never receives the Production token")
sys.exit(fail)
