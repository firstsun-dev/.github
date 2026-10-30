"""Run the REAL `run:` steps of a job from _cf-worker-v2.yml against stub gh/pnpm/curl (no network, no Cloudflare).

Evaluates step `if:`/`env:`/`${{ }}` with ghexpr, honours success()/failure() semantics, GITHUB_ENV /
GITHUB_OUTPUT propagation, step outcomes, continue-on-error and job outputs. A LOCAL composite action
(`uses: ./.cf-tools/actions/<name>`) is expanded: its `inputs` come from the caller's `with:`, its steps run with
their own `steps`/`failure()` scope and share the job env, GITHUB_ENV and workspace, and its `outputs` are rendered
back into the calling step. Other `uses:` steps are no-ops (the workspace is pre-populated). Test-only; this is not
GitHub, see docs "Verified vs not verified".
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

import yaml

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
  *"&sha="*"per_page=1"*) echo "${STUB_ENV_DEPLOY_ID:-5151}";;
  *"per_page=1"*"id // empty"*) echo "${STUB_NEWEST:-4242}";;
  *"per_page=30"*" -q "*) [ -n "${STUB_LASTGOOD:-}" ] && echo "9:$STUB_LASTGOOD";;
  *"per_page=30"*) printf '%s' "${STUB_DEPLOYMENTS:-[]}";;
  *"deployments/9/statuses"*) echo success;;
  *"/statuses?per_page=1"*"environment_url"*) echo "${STUB_STATUS:-success https://feat-x-app.example.workers.dev Serving good (content)}";;
esac
exit 0
'''
PNPM_STUB = r'''#!/usr/bin/env bash
echo "pnpm $*" >> "$STUB_LOG"
[ "$1" = exec ] && [ "$2" = wrangler ] || exit 0
case "$3 $4 $5" in
  "--version  ") echo "4.140.0";;
  "preview "*) [ "${STUB_PREVIEW_FAIL:-}" = 1 ] && exit 9
               if [ -n "${STUB_PREVIEW_RAW+x}" ]; then printf '%s' "$STUB_PREVIEW_RAW"; else echo '{"preview":{"urls":["https://'"$5"'-app.example.workers.dev"]}}'; fi;;
  "deployments list"*) [ "${STUB_NO_PREV:-}" = 1 ] && { echo '[]'; exit 0; }
               echo '[{"created_on":"2024-01-01","versions":[{"version_id":"'"${STUB_ACTIVE:-prev-1}"'","percentage":100}]}]';;
  "versions view"*) [ -n "${STUB_PREV_MESSAGE:-}" ] && echo '{"annotations":{"workers/message":"'"$STUB_PREV_MESSAGE"'"}}';;
  "versions upload"*) [ "${STUB_UPLOAD_FAIL:-}" = 1 ] && exit 6; echo "Worker Version ID: new-2";;
  "versions deploy prev-1@100") [ "${STUB_ROLLBACK_FAIL:-}" = 1 ] && exit 8;;
  "d1 migrations list") [ "${STUB_PENDING:-1}" = 1 ] && echo "Migrations to be applied: ${STUB_PENDING_FILES-0001.sql}" || echo "No migrations to apply!";;
  "d1 migrations apply") [ "${STUB_APPLY_FAIL:-}" = 1 ] && exit 7
               [ -n "${STUB_APPLY_FAIL_CFG:-}" ] && [[ "$*" == *"$STUB_APPLY_FAIL_CFG"* ]] && exit 7;;
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

NPM_STUB = '#!/usr/bin/env bash\necho "npm $*" >> "$STUB_LOG"\nexit 0\n'

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


def make_workspace(toml=APP_TOML, files=None):
    ws = pathlib.Path(tempfile.mkdtemp(prefix="jobrun-"))
    (ws / "bin").mkdir()
    for name, body in (("gh", GH_STUB), ("pnpm", PNPM_STUB), ("curl", CURL_STUB), ("npm", NPM_STUB)):
        (ws / "bin" / name).write_text(body)
        (ws / "bin" / name).chmod(0o755)
    (ws / "w" / "migrations").mkdir(parents=True)
    (ws / "w" / "migrations" / "0001.sql").write_text("select 1;")
    (ws / "w" / "wrangler.toml").write_text(toml)
    (ws / "w" / ".cf-tools" / "actions").mkdir(parents=True)
    for tool in ("cf-preview", "cf-deploy", "cf-ci-check"):
        (ws / "w" / ".cf-tools" / "actions" / tool).symlink_to(ROOT / "actions" / tool)
    for rel, body in (files or {}).items():
        (ws / "w" / rel).write_text(body)
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


class _Run:
    def __init__(self, ws, base, jobenv_file):
        self.ws, self.w, self.base, self.jobenv_file = ws, ws / "w", base, jobenv_file
        self.counter = 0

    def cur_env(self, ctx, extra):
        e = dict(self.base)
        e.update(ctx["env"])
        e.update(_parse_kv(self.jobenv_file))
        e["GITHUB_ENV"] = str(self.jobenv_file)
        e.update(extra)
        return e

    def steps(self, steps, ctx, extra_env=None, verbose=False):
        """Run `steps`; returns (failed, results). `ctx["steps"]` is the scope of THESE steps."""
        extra_env = extra_env or {}
        failed, results = False, []
        for step in steps:
            self.counter += 1
            sid = step.get("id")
            ctx["job"] = {"status": "failure" if failed else "success"}
            ctx["env"].update(_parse_kv(self.jobenv_file))
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
            label = step.get("name") or sid or f"step{self.counter}"
            if not go:
                if sid:
                    ctx["steps"][sid] = {"outcome": "skipped", "conclusion": "skipped", "outputs": {}}
                results.append((label, "skipped"))
                continue
            if "uses" in step:
                uses = render(step["uses"], ctx, failed)
                adir = self.w / uses[2:] if uses.startswith("./") else None
                if adir is not None and (adir / "action.yml").exists():
                    outcome, outputs, inner_results = self.composite(step, adir, ctx, failed, verbose)
                    results.append((label, outcome))
                    results.extend((f"{label} / {n}", o) for n, o in inner_results)
                    if sid:
                        ctx["steps"][sid] = {"outcome": outcome, "conclusion": outcome, "outputs": outputs}
                    if outcome == "failure" and not step.get("continue-on-error"):
                        failed = True
                    continue
                if uses.startswith("actions/checkout") and step.get("with", {}).get("path") == "recover":
                    shutil.copytree(self.w, self.w / "recover", symlinks=True, dirs_exist_ok=True, ignore=shutil.ignore_patterns("recover"))
                if sid:
                    ctx["steps"][sid] = {"outcome": "success", "conclusion": "success", "outputs": {}}
                results.append((label, "uses"))
                continue
            out_file = self.ws / f"out_{self.counter}"
            out_file.write_text("")
            env = self.cur_env(ctx, extra_env)
            env["GITHUB_OUTPUT"] = str(out_file)
            for k, v in step.get("env", {}).items():
                env[k] = render(v, ctx, failed)
            script = render(step["run"], ctx, failed)
            wd = pathlib.Path(render(step.get("working-directory", "."), ctx, failed))
            wd = wd if wd.is_absolute() else self.w / wd
            p = subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=wd, env=env, capture_output=True, text=True)
            if verbose:
                print(f"--- {label} rc={p.returncode}\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
            outputs = _parse_kv(out_file)
            outcome = "success" if p.returncode == 0 else "failure"
            if sid:
                ctx["steps"][sid] = {"outcome": outcome, "conclusion": outcome, "outputs": outputs}
            ctx["env"].update(_parse_kv(self.jobenv_file))
            results.append((label, outcome))
            if outcome == "failure" and not step.get("continue-on-error"):
                failed = True
        return failed, results

    def composite(self, step, adir, ctx, failed_outer, verbose):
        action = yaml.safe_load((adir / "action.yml").read_text())
        inputs = {k: v.get("default", "") for k, v in action.get("inputs", {}).items()}
        for k, v in step.get("with", {}).items():
            inputs[k] = render(v, ctx, failed_outer)
        sub = Ctx({**ctx, "inputs": inputs, "steps": {}}, ctx.secrets)
        sub.secret_reads = ctx.secret_reads
        failed, results = self.steps(action["runs"]["steps"], sub, {"GITHUB_ACTION_PATH": str(adir)}, verbose)
        outputs = {k: render(v["value"], sub, failed) for k, v in action.get("outputs", {}).items()}
        return ("failure" if failed else "success"), outputs, results


def run_job(doc, name, ctx, ws, stub_env=None, verbose=False):
    """Returns dict(result, outputs, steps, log, summary)."""
    job = doc["jobs"][name]
    w = ws / "w"
    log = ws / "stub.log"
    log.write_text("")
    event_file = ws / "event.json"
    event_file.write_text(json.dumps(ctx["github"].get("event", {})))
    base = dict(os.environ)
    base.update({
        "PATH": f"{ws / 'bin'}:{os.environ['PATH']}", "STUB_LOG": str(log), "STUB_DIR": str(ws / "state"),
        "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "1", "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_WORKSPACE": str(w), "RUNNER_TEMP": str(ws / "tmp"), "GITHUB_STEP_SUMMARY": str(ws / "summary.md"),
        "GITHUB_SHA": str(ctx["github"].get("sha", "")), "GITHUB_REF": ctx["github"]["ref"], "GITHUB_EVENT_PATH": str(event_file),
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
    for k, v in doc.get("env", {}).items():
        ctx["env"][k] = render(v, ctx)
    for k, v in job.get("env", {}).items():
        ctx["env"][k] = render(v, ctx)
    runner = _Run(ws, base, jobenv_file)
    failed, results = runner.steps(job["steps"], ctx, verbose=verbose)
    outs = {k: render(v, ctx, failed) for k, v in job.get("outputs", {}).items()}
    summary = (ws / "summary.md").read_text() if (ws / "summary.md").exists() else ""
    return {"result": "failure" if failed else "success", "outputs": outs, "steps": results, "log": log.read_text(), "summary": summary}
