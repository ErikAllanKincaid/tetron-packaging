#!/usr/bin/env python3
# reconcile.py -- run from the tetron-packaging repo root.
# Checks the automatable constraints (CON-P01..CON-P06) from spec/constraints.py.
import ast
import glob
import json
import re
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def tracked() -> list[str]:
    return [f for f in run(["git", "ls-files"]).stdout.splitlines() if Path(f).is_file()]


def check_tests() -> dict:
    r = run([sys.executable, "-m", "unittest", "discover", "tests"])
    return {"passed": r.returncode == 0, "tail": r.stderr[-400:] if r.returncode else ""}


def check_stdlib() -> dict:
    tree = ast.parse(Path("mkdeb.py").read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods.add(n.module.split(".")[0])
    bad = sorted(m for m in mods if m not in sys.stdlib_module_names)
    return {"violations": len(bad), "modules": bad}


def check_syntax() -> dict:
    bad = []
    for f in glob.glob("scripts/*.sh"):
        if run(["sh", "-n", f]).returncode:
            bad.append(f)
    if run([sys.executable, "-m", "py_compile", "mkdeb.py"]).returncode:
        bad.append("mkdeb.py")
    return {"failures": len(bad), "files": bad}


def check_secrets() -> dict:
    pat = re.compile(r"BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")
    hits = [f for f in tracked() if pat.search(Path(f).read_text(errors="ignore"))]
    return {"count": len(hits), "files": hits}


def check_action() -> dict:
    p = Path("action.yml")
    if not p.exists():
        return {"missing": 1, "files": ["action.yml"]}
    refs = set(re.findall(r'action_path \}\}/([A-Za-z0-9_./-]+)', p.read_text()))
    missing = sorted(r for r in refs if not Path(r).exists())
    return {"missing": len(missing), "files": missing, "refs": sorted(refs)}


def check_local_docs() -> dict:
    ignored = run(["git", "check-ignore", "-q", "DO-NOT-COMMIT/x"]).returncode == 0
    n = len([f for f in run(["git", "ls-files", "DO-NOT-COMMIT"]).stdout.splitlines() if f])
    return {"ignored": ignored, "tracked": n}


if __name__ == "__main__":
    ctx = {"tests": check_tests(), "stdlib": check_stdlib(), "syntax": check_syntax(),
           "secrets": check_secrets(), "action": check_action(), "local_docs": check_local_docs()}
    print(json.dumps(ctx, indent=2))
    ok = (ctx["tests"]["passed"] and ctx["stdlib"]["violations"] == 0 and ctx["syntax"]["failures"] == 0
          and ctx["secrets"]["count"] == 0 and ctx["action"]["missing"] == 0
          and ctx["local_docs"]["ignored"] and ctx["local_docs"]["tracked"] == 0)
    sys.exit(0 if ok else 1)
