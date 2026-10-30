#!/usr/bin/env python3
"""Runs the scenario groups of test-jobs.py as independent processes (bounded parallelism).

`test-jobs.py --list` is the source of truth for what exists; MANIFEST below is the source of truth for what MUST exist and how many
checks each group has at least. The run fails when a group is missing, unexpected, ran zero checks, ran fewer checks than the
manifest says, printed no SUMMARY, or failed - a skipped or silently shrunken group can never produce a green Full result.
Each scenario builds its own fresh workspaces, so groups share no mutable state.

Usage: run-job-groups.py [--list] [--group ID]... [--jobs N] [--logdir DIR]     (default jobs: CF_V2_TEST_JOBS or min(4, CPUs))
Prints one line per group and only the FAIL lines of a failing group; full logs and perf reports go to --logdir."""
import concurrent.futures as cf
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# group id -> minimum number of checks (raise it when scenarios are added; never lower it to make a run pass)
MANIFEST = {
    "production-release": 43,
    "preview-deploy": 46,
    "migration-d1": 55,
    "shared-dev": 74,
    "chain-smoke-rollback": 54,
    "deferred-verification": 39,
    "native-dev-smoke-modes": 48,
    "review-regressions": 34,
}


def discovered():
    p = subprocess.run([sys.executable, str(HERE / "test-jobs.py"), "--list"], capture_output=True, text=True, cwd=ROOT)
    if p.returncode:
        sys.exit(f"cannot list groups: {p.stderr[-500:]}")
    return [line.split("\t")[0] for line in p.stdout.splitlines() if line.strip()]


def run_group(gid, logdir, profile):
    t0 = time.time()
    env = dict(os.environ)
    if profile:
        env["JOBRUNNER_PERF"] = str(logdir / f"perf-{gid}.json")
    p = subprocess.run([sys.executable, str(HERE / "test-jobs.py"), "--group", gid], cwd=ROOT, capture_output=True, text=True, env=env)
    (logdir / f"{gid}.log").write_text(p.stdout + p.stderr)
    m = re.search(r"^SUMMARY groups=(\d+) checks=(\d+) fail=(\d+)$", p.stdout, re.M)
    problems = []
    checks = int(m.group(2)) if m else 0
    if not m:
        problems.append("no SUMMARY line (crashed or skipped)")
    else:
        if m.group(3) != "0":
            problems.append("checks failed")
        if checks == 0:
            problems.append("zero checks executed")
        elif checks < MANIFEST.get(gid, 0):
            problems.append(f"only {checks} checks, manifest expects >= {MANIFEST[gid]}")
    if p.returncode and not problems:
        problems.append(f"exit code {p.returncode}")
    if profile and not (logdir / f"perf-{gid}.json").exists():
        problems.append("no performance report written")
    return gid, checks, time.time() - t0, problems, p


def main():
    a = sys.argv[1:]
    found = discovered()
    if "--list" in a:
        print("\n".join(found))
        return 0
    pick = [a[i + 1] for i, x in enumerate(a) if x == "--group"]
    errors = []
    if not pick:  # the full run must cover exactly the manifest
        errors += [f"expected group missing from test-jobs.py: {g}" for g in MANIFEST if g not in found]
        errors += [f"group not in the manifest (add it with its minimum check count): {g}" for g in found if g not in MANIFEST]
    unknown = [g for g in pick if g not in found]
    errors += [f"unknown group: {g}" for g in unknown]
    chosen = pick or [g for g in MANIFEST if g in found]
    jobs = int(a[a.index("--jobs") + 1]) if "--jobs" in a else int(os.environ.get("CF_V2_TEST_JOBS", min(4, os.cpu_count() or 2)))
    logdir = pathlib.Path(a[a.index("--logdir") + 1]) if "--logdir" in a else pathlib.Path(tempfile.mkdtemp(prefix="jobgroups-"))
    logdir.mkdir(parents=True, exist_ok=True)
    profile = bool(os.environ.get("JOBRUNNER_PERF"))
    for e in errors:
        print(f"FAIL {e}")
    t0 = time.time()
    total, bad = 0, bool(errors)
    with cf.ThreadPoolExecutor(jobs) as ex:
        for gid, checks, dt, problems, p in ex.map(lambda g: run_group(g, logdir, profile), [g for g in chosen if g in found]):
            total += checks
            print(f"{'FAIL' if problems else 'ok  '} {gid}: {checks} checks, {dt:.0f}s" + (f" - {'; '.join(problems)}" if problems else ""))
            if problems:
                bad = True
                print("\n".join(l for l in p.stdout.splitlines() if l.startswith("FAIL")) or p.stderr[-800:])
    print(f"{'FAILED' if bad else 'PASSED'}: {len(chosen)} groups, {total} checks, {time.time() - t0:.0f}s wall (parallelism {jobs}), logs in {logdir}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
