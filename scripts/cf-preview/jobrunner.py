"""Run the REAL `run:` steps of a job from _cf-worker-v2.yml against stub gh/pnpm/curl (no network, no Cloudflare).

Evaluates step `if:`/`env:`/`${{ }}` with ghexpr, honours success()/failure() semantics, GITHUB_ENV /
GITHUB_OUTPUT propagation, step outcomes, continue-on-error and job outputs. `uses:` steps are no-ops (the
workspace is pre-populated). Test-only; this is not GitHub, see docs "Verified vs not verified".
"""
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

from ghexpr import Ctx, ev, render

ROOT = pathlib.Path(__file__).resolve().parents[2]

GH_STUB = r'''#!/usr/bin/env bash
echo "gh $*" >> "$STUB_LOG"
a="$*"
case "$a" in
  *"git/ref/heads/"*)
    n=0; [ -f "$STUB_DIR/tipn" ] && n=$(cat "$STUB_DIR/tipn"); echo $((n+1)) > "$STUB_DIR/tipn"
    IFS=, read -ra tips <<< "$STUB_TIPS"; i=$n; [ $i -ge ${#tips[@]} ] && i=$((${#tips[@]}-1))
    [ "${tips[$i]}" = GONE ] && exit 1; echo "${tips[$i]}";;
  *"payload.branch_ref"*) echo "${STUB_OWNER_REF:-}";;
  *"deployments"*"-X POST"*"--input -"*) cat >> "$STUB_LOG"; echo 4242;;
  *"per_page=30"*) [ -n "${STUB_LASTGOOD:-}" ] && echo "9:$STUB_LASTGOOD";;
  *"deployments/9/statuses"*) echo success;;
esac
exit 0
'''
PNPM_STUB = r'''#!/usr/bin/env bash
echo "pnpm $*" >> "$STUB_LOG"
[ "$1" = exec ] && [ "$2" = wrangler ] || exit 0
case "$3 $4 $5" in
  "--version  ") echo "4.140.0";;
  "preview "*) [ "${STUB_PREVIEW_FAIL:-}" = 1 ] && exit 9; echo '{"preview":{"urls":["https://stable.example.workers.dev"]}}';;
  "deployments list"*) echo '[{"created_on":"2024-01-01","versions":[{"version_id":"prev-1","percentage":100}]}]';;
  "versions upload"*) echo "Worker Version ID: new-2";;
  "d1 migrations list") [ "${STUB_PENDING:-1}" = 1 ] && echo "Migrations to be applied: 0001.sql" || echo "No migrations to apply!";;
  "d1 migrations apply") [ "${STUB_APPLY_FAIL:-}" = 1 ] && exit 7;;
esac
exit 0
'''
CURL_STUB = r'''#!/usr/bin/env bash
case "$*" in
  *"-w"*) n=0; [ -f "$STUB_DIR/proben" ] && n=$(cat "$STUB_DIR/proben"); echo $((n+1)) > "$STUB_DIR/proben"
          IFS=';' read -ra pr <<< "${STUB_PROBES:-200 }"; i=$n; [ $i -ge ${#pr[@]} ] && i=$((${#pr[@]}-1)); printf '%s' "${pr[$i]}";;
  *) [ "${STUB_SHA_FAIL:-}" = 1 ] && exit 22; printf '%s' "${STUB_SHA_BODY:-}";;
esac
'''

APP_TOML = '''name = "app"
[[d1_databases]]
binding = "DB"
database_name = "prod-db"
database_id = "PROD-ID"
[previews]
[[previews.d1_databases]]
binding = "DB"
database_name = "prev-db"
database_id = "PREV-ID"
'''


def make_workspace(toml=APP_TOML):
    ws = pathlib.Path(tempfile.mkdtemp(prefix="jobrun-"))
    (ws / "bin").mkdir()
    for name, body in (("gh", GH_STUB), ("pnpm", PNPM_STUB), ("curl", CURL_STUB)):
        (ws / "bin" / name).write_text(body)
        (ws / "bin" / name).chmod(0o755)
    (ws / "w" / "migrations").mkdir(parents=True)
    (ws / "w" / "migrations" / "0001.sql").write_text("select 1;")
    (ws / "w" / "wrangler.toml").write_text(toml)
    (ws / "w" / ".cf-tools" / "actions").mkdir(parents=True)
    (ws / "w" / ".cf-tools" / "actions" / "cf-preview").symlink_to(ROOT / "actions" / "cf-preview")
    (ws / "tmp").mkdir()
    (ws / "state").mkdir()
    return ws


def _parse_kv(path):
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
    return out


def run_job(doc, name, ctx, ws, stub_env=None, verbose=False):
    """Returns dict(result, outputs, steps, log, summary)."""
    job = doc["jobs"][name]
    w = ws / "w"
    log = ws / "stub.log"
    log.write_text("")
    base = dict(os.environ)
    base.update({
        "PATH": f"{ws / 'bin'}:{os.environ['PATH']}", "STUB_LOG": str(log), "STUB_DIR": str(ws / "state"),
        "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "1", "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_WORKSPACE": str(w), "RUNNER_TEMP": str(ws / "tmp"), "GITHUB_STEP_SUMMARY": str(ws / "summary.md"),
        "GITHUB_SHA": str(ctx["github"].get("sha", "")), "GITHUB_REF": ctx["github"]["ref"],
        "GITHUB_EVENT_NAME": ctx["github"]["event_name"], "PROBE_SLEEP": "0", "PROBE_ATTEMPTS": "1", "SHA_SLEEP": "0", "SHA_ATTEMPTS": "1",
    })
    base.update(stub_env or {})
    (ws / "state" / "tipn").unlink(missing_ok=True)
    (ws / "state" / "proben").unlink(missing_ok=True)
    ctx["github"]["workspace"] = str(w)
    ctx["runner"] = {"temp": str(ws / "tmp")}
    ctx["steps"] = {}
    ctx["env"] = {}
    jobenv_file = ws / "github_env"
    jobenv_file.write_text("")

    def cur_env():
        e = dict(base)
        e.update(ctx["env"])
        e.update(_parse_kv(jobenv_file))
        e["GITHUB_ENV"] = str(jobenv_file)
        return e

    for k, v in doc.get("env", {}).items():
        ctx["env"][k] = render(v, ctx)
    for k, v in job.get("env", {}).items():
        ctx["env"][k] = render(v, ctx)

    failed = False
    results = []
    for i, step in enumerate(job["steps"]):
        sid = step.get("id")
        ctx["env"].update(_parse_kv(jobenv_file))
        cond = step.get("if")
        if cond is None:
            go = not failed
        else:
            expr = str(cond).strip()
            m = re.fullmatch(r"\$\{\{(.*)\}\}", expr, re.S)
            inner = m.group(1).strip() if m else expr
            # GitHub wraps an `if` that has no status function in `success() &&`.
            if not re.search(r"\b(success|failure|always|cancelled)\(\)", inner):
                inner = f"success() && ({inner})"
            go = bool(ev(inner, ctx, failed))
        label = step.get("name") or sid or f"step{i}"
        if not go:
            if sid:
                ctx["steps"][sid] = {"outcome": "skipped", "conclusion": "skipped", "outputs": {}}
            results.append((label, "skipped"))
            continue
        if "uses" in step:
            if str(step["uses"]).startswith("actions/checkout") and step.get("with", {}).get("path") == "recover":
                shutil.copytree(w, ws / "w" / "recover", symlinks=True, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("recover"))
            if sid:
                ctx["steps"][sid] = {"outcome": "success", "conclusion": "success", "outputs": {}}
            results.append((label, "uses"))
            continue
        out_file = ws / f"out_{i}"
        out_file.write_text("")
        env = cur_env()
        env["GITHUB_OUTPUT"] = str(out_file)
        for k, v in step.get("env", {}).items():
            env[k] = render(v, ctx, failed)
        script = render(step["run"], ctx, failed)
        wd = pathlib.Path(render(step.get("working-directory", "."), ctx, failed))
        wd = wd if wd.is_absolute() else w / wd
        p = subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=wd, env=env, capture_output=True, text=True)
        if verbose:
            print(f"--- {label} rc={p.returncode}\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
        outputs = _parse_kv(out_file)
        outcome = "success" if p.returncode == 0 else "failure"
        if sid:
            ctx["steps"][sid] = {"outcome": outcome, "conclusion": outcome, "outputs": outputs}
        ctx["env"].update(_parse_kv(jobenv_file))
        results.append((label, outcome))
        if outcome == "failure" and not step.get("continue-on-error"):
            failed = True

    outs = {k: render(v, ctx, failed) for k, v in job.get("outputs", {}).items()}
    summary = (ws / "summary.md").read_text() if (ws / "summary.md").exists() else ""
    return {"result": "failure" if failed else "success", "outputs": outs, "steps": results, "log": log.read_text(), "summary": summary}
