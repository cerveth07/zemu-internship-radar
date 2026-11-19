"""Access to the frontend sources for tests.

The UI ships as several plain scripts rather than one bundle, so tests that need to check wiring
should look at the concatenated sources in load order instead of pinning a single file name.
For behaviour tests prefer tests/test_frontend_behaviour.py, which runs the real render code.
"""
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
HARNESS = ROOT / "tests" / "fe" / "harness.cjs"


def script_files():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return re.findall(r'<script src="/static/([^"]+\.js)"></script>', html)


def frontend_source():
    """All frontend scripts joined in the order index.html loads them."""
    return "\n".join((STATIC / name).read_text(encoding="utf-8") for name in script_files())


def run_node(script, timeout=40):
    assert shutil.which("node"), "Node.js not installed"
    done = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True,
                          timeout=timeout, check=False)
    stderr = (done.stderr or b"").decode("utf-8", "replace")
    stdout = (done.stdout or b"").decode("utf-8", "replace")
    assert done.returncode == 0, stderr + stdout
    return stdout


def harness_script(body):
    """Prefix a node script with the shared harness and a failing assert helper."""
    return ("const fe=require(" + repr(str(HARNESS)).replace("'", '"') + ");\n"
            "const assert=require('node:assert/strict');\n" + body)
