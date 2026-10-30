#!/usr/bin/env python3
"""Runs the scenario groups of test-jobs.py as independent processes (each group = one `# ====` section).

Every scenario builds its own fresh workspaces, so groups share no mutable state; the shared prelude (imports, helper
`def`s and constant assignments of ALL sections) is executed in each group process, but only that group's scenarios.
Usage: run-job-groups.py [--list] [--group SUBSTR]... [--jobs N] [--logdir DIR]
Prints one summary line per group and only the FAIL lines of failing groups; full logs go to --logdir."""
import ast
import concurrent.futures as cf
import os
import pathlib
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
SRC = (HERE / "test-jobs.py").read_text()
SAFE_CALLS = {"dict", "set", "list", "tuple", "frozenset", "len", "sorted", "str", "open", "int"}
SAFE_ATTRS = {"safe_load", "get", "items", "split", "read_text", "loads", "dumps", "keys", "values", "join", "strip"}


def _names(node):
    bound = {a.arg for n in ast.walk(node) if isinstance(n, ast.Lambda) for a in n.args.args}
    bound |= {t.id for n in ast.walk(node) if isinstance(n, ast.comprehension) for t in ast.walk(n.target) if isinstance(t, ast.Name)}
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)} - bound


def _targets(node):
    ts = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {n.id for t in ts for n in ast.walk(t) if isinstance(n, ast.Name)}


def _is_setup(node, defined):
    """Shared prelude: imports, defs, and constant-ish assignments built only from names that are themselves prelude
    (scenario results such as `r = deploy()` and anything derived from them are scenario statements)."""
    if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)):
        defined.update(a.asname or a.name.split(".")[0] for a in getattr(node, "names", []))
        if hasattr(node, "name"):
            defined.add(node.name)
        return True
    if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
        calls = [c for c in ast.walk(node.value) if isinstance(c, ast.Call)]
        safe = all((isinstance(c.func, ast.Name) and (c.func.id in SAFE_CALLS or c.func.id in defined and c.func.id[0].isupper()))
                   or (isinstance(c.func, ast.Attribute) and c.func.attr in SAFE_ATTRS) for c in calls)
        if safe and _names(node.value) <= defined | set(dir(__builtins__)) | SAFE_CALLS:
            defined.update(_targets(node))
            return True
    return False


def sections():
    lines = SRC.splitlines()
    marks = [i + 1 for i, l in enumerate(lines) if l.startswith("# =====")]
    tree = ast.parse(SRC)
    bounds = marks + [len(lines) + 2]
    out = []
    for n, start in enumerate(marks):
        title = lines[start - 1].lstrip("# =").strip()
        body = [s for s in tree.body if start <= s.lineno < bounds[n + 1]]
        out.append((title, body))
    pre = [s for s in tree.body if not marks or s.lineno < marks[0]]
    return pre, out


def build(pre, secs, idx):
    defined = set()
    keep = []
    for s in pre:
        _is_setup(s, defined)
    for n, (_, body) in enumerate(secs):
        for s in body:
            if _is_setup(s, defined) or n == idx:
                keep.append(s)
    code = ast.unparse(ast.fix_missing_locations(ast.Module(body=list(pre) + keep, type_ignores=[])))
    # every group process writes its own JOBRUNNER_PERF report (the file's own write_perf_report() call sits in the last section only)
    return code + "\nimport atexit as _atexit\n_atexit.register(write_perf_report)\n"


def main():
    a = sys.argv[1:]
    pre, secs = sections()
    if "--list" in a:
        for n, (t, _) in enumerate(secs):
            print(n, t)
        return 0
    pick = [a[i + 1] for i, x in enumerate(a) if x == "--group"]
    jobs = int(a[a.index("--jobs") + 1]) if "--jobs" in a else (os.cpu_count() or 2)
    logdir = pathlib.Path(a[a.index("--logdir") + 1]) if "--logdir" in a else pathlib.Path(tempfile.mkdtemp(prefix="jobgroups-"))
    logdir.mkdir(parents=True, exist_ok=True)
    chosen = [n for n, (t, _) in enumerate(secs) if not pick or any(p.lower() in t.lower() for p in pick)]
    tmp = logdir / "src"
    tmp.mkdir(exist_ok=True)

    def run(n):
        f = tmp / f"group{n}.py"
        code = build(pre, secs, n)
        f.write_text(code)
        t0 = time.time()
        env = dict(os.environ)
        if env.get("JOBRUNNER_PERF"):
            env["JOBRUNNER_PERF"] = str(logdir / f"perf{n}.json")
        p = subprocess.run([sys.executable, str(f)], cwd=HERE.parents[1], capture_output=True, text=True, env=env)
        (logdir / f"group{n}.log").write_text(p.stdout + p.stderr)
        return n, p.returncode, time.time() - t0, p.stdout, p.stderr

    t0 = time.time()
    bad = 0
    with cf.ThreadPoolExecutor(jobs) as ex:
        for n, rc, dt, out, err in ex.map(run, chosen):
            ok, fl = out.count("PASS:"), out.count("FAIL:")
            print(f"{'ok  ' if rc == 0 and not fl else 'FAIL'} group {n} {secs[n][0][:60]!r}: {ok} pass, {fl} fail, {dt:.0f}s")
            if rc or fl:
                bad = 1
                print("\n".join(l for l in out.splitlines() if l.startswith("FAIL")) or err[-800:])
    print(f"total {time.time() - t0:.0f}s wall, logs in {logdir}")
    return bad


if __name__ == "__main__":
    sys.exit(main())
