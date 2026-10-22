#!/usr/bin/env python3
"""shellcheck the `run:` scripts of the composite actions (actionlint only lints workflows).
`${{ }}` expressions are replaced by a plain placeholder first, exactly like actionlint does."""
import re
import subprocess
import sys
import tempfile

import yaml

rc = 0
for name in ("cf-deploy", "cf-ci-check"):
    steps = yaml.safe_load(open(f"actions/{name}/action.yml"))["runs"]["steps"]
    for s in steps:
        if "run" not in s:
            continue
        body = re.sub(r"\$\{\{.*?\}\}", "EXPR", s["run"])
        with tempfile.NamedTemporaryFile("w", suffix=".sh") as f:
            f.write("#!/usr/bin/env bash\n" + body + "\n")
            f.flush()
            p = subprocess.run(["shellcheck", "-s", "bash", "-S", "warning", f.name], capture_output=True, text=True)
        if p.returncode:
            rc = 1
            print(f"{name}: {s.get('name', '?')}\n{p.stdout}")
print("composite action scripts: shellcheck " + ("FAILED" if rc else "clean"))
sys.exit(rc)
