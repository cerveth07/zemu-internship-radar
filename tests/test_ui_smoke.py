"""Optional browser smoke test for the daily flow.

Runs tools/check_daily_flow.cjs against a throwaway uvicorn instance. Skips unless node, a
Playwright build and a Chromium binary are all available; set RADAR_PLAYWRIGHT_MODULE /
RADAR_CHROME to point at them (see tools/README notes in docs/desktop-ui.md).
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "check_daily_flow.cjs"


def unavailable(message):
    if os.environ.get('RADAR_REQUIRE_BROWSER') == '1':
        pytest.fail(message)
    pytest.skip(message)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def playwright_module():
    module = os.environ.get("RADAR_PLAYWRIGHT_MODULE")
    if module and Path(module).exists():
        return module
    try:
        done = subprocess.run(["node", "-e", "require.resolve('playwright-core')"], cwd=ROOT,
                              capture_output=True, check=False, timeout=20)
    except OSError:
        return None
    return "playwright-core" if done.returncode == 0 else None


@pytest.fixture()
def server(tmp_path):
    port = free_port()
    env = dict(os.environ, RADAR_PRIVATE_RC_DB_PATH=str(tmp_path / "smoke.sqlite"))
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                "--port", str(port)], cwd=ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        import urllib.request
        for _ in range(80):
            try:
                with urllib.request.urlopen(base + "/api/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.15)
        else:
            unavailable("本地服务未能启动，无法完成浏览器冒烟")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def test_daily_flow_in_a_real_browser(server):
    if shutil.which("node") is None:
        unavailable("Node.js not installed")
    module = playwright_module()
    if module is None:
        unavailable("未安装 Playwright，无法完成浏览器冒烟（设置 RADAR_PLAYWRIGHT_MODULE）")
    env = dict(os.environ, RADAR_PRIVATE_RC_URL=server, RADAR_PLAYWRIGHT_MODULE=module)
    done = subprocess.run(["node", str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, timeout=180, check=False)
    output = (done.stdout or b"").decode("utf-8", "replace") + (done.stderr or b"").decode("utf-8", "replace")
    assert done.returncode == 0, output


def test_career_evidence_task_material_flow_in_a_real_browser(server):
    if shutil.which("node") is None:
        unavailable("Node.js not installed")
    module = playwright_module()
    if module is None:
        unavailable("Playwright is required for the career workflow")
    env = dict(os.environ, RADAR_PRIVATE_RC_URL=server, RADAR_PLAYWRIGHT_MODULE=module)
    done = subprocess.run(["node", str(ROOT / "tools/check_career_workspace_ui.cjs")], cwd=ROOT,
                          env=env, capture_output=True, timeout=150, check=False)
    output = (done.stdout or b"").decode("utf-8", "replace") + (done.stderr or b"").decode("utf-8", "replace")
    assert done.returncode == 0, output


@pytest.mark.parametrize('scenario', ['save-return', 'examples'])
def test_save_feedback_cancel_and_examples_in_a_real_browser(server, scenario):
    if shutil.which('node') is None:
        unavailable('Node.js not installed')
    module = playwright_module()
    if module is None:
        unavailable('Playwright is required for save/return regressions')
    env = dict(os.environ, RADAR_PRIVATE_RC_URL=server, RADAR_PLAYWRIGHT_MODULE=module, RADAR_SCENARIO=scenario)
    done = subprocess.run(['node', str(ROOT / 'tools/check_save_return_ui.cjs')], cwd=ROOT,
                          env=env, capture_output=True, timeout=180, check=False)
    output = (done.stdout or b'').decode('utf-8', 'replace') + (done.stderr or b'').decode('utf-8', 'replace')
    assert done.returncode == 0, output


def test_company_size_in_rendered_pages_and_capture_inbox(server):
    if shutil.which('node') is None:
        unavailable('Node.js not installed')
    module = playwright_module()
    if module is None:
        unavailable('Playwright is required for company size checks')
    env = dict(os.environ, RADAR_PRIVATE_RC_URL=server, RADAR_PLAYWRIGHT_MODULE=module)
    done = subprocess.run(['node', str(ROOT / 'tools/check_company_size_ui.cjs')], cwd=ROOT,
                          env=env, capture_output=True, timeout=120, check=False)
    output = (done.stdout or b'').decode('utf-8', 'replace') + (done.stderr or b'').decode('utf-8', 'replace')
    assert done.returncode == 0, output


def test_company_scale_range_filter_in_a_real_browser(server):
    module = playwright_module()
    if shutil.which('node') is None or module is None:
        unavailable('Playwright / Node.js is required for company scale checks')
    env = dict(os.environ, RADAR_PRIVATE_RC_URL=server, RADAR_PLAYWRIGHT_MODULE=module)
    done = subprocess.run(['node', str(ROOT / 'tools/check_company_scale_ui.cjs')], cwd=ROOT,
                          env=env, capture_output=True, timeout=120, check=False)
    output = (done.stdout or b'').decode('utf-8', 'replace') + (done.stderr or b'').decode('utf-8', 'replace')
    assert done.returncode == 0, output


@pytest.mark.parametrize('viewport', ['desktop', 'compact'])
def test_company_lists_import_match_and_filter_in_a_real_browser(server, viewport):
    module = playwright_module()
    if shutil.which('node') is None or module is None:
        unavailable('Playwright / Node.js is required for company list checks')
    env = dict(os.environ, RADAR_PRIVATE_RC_URL=server, RADAR_PLAYWRIGHT_MODULE=module, RADAR_LIST_VIEWPORT=viewport)
    done = subprocess.run(['node', str(ROOT / 'tools/check_company_lists_ui.cjs')], cwd=ROOT,
                          env=env, capture_output=True, timeout=180, check=False)
    output = (done.stdout or b'').decode('utf-8', 'replace') + (done.stderr or b'').decode('utf-8', 'replace')
    assert done.returncode == 0, output


def test_list_workspace_scope_drafts_and_continuous_editing(server):
    module = playwright_module()
    if shutil.which('node') is None or module is None:
        unavailable('Playwright / Node.js is required for list workspace checks')
    env = dict(os.environ, RADAR_PRIVATE_RC_URL=server, RADAR_PLAYWRIGHT_MODULE=module)
    done = subprocess.run(['node', str(ROOT / 'tools/check_list_workspace_ux.cjs')], cwd=ROOT,
                          env=env, capture_output=True, timeout=180, check=False)
    output = (done.stdout or b'').decode('utf-8', 'replace') + (done.stderr or b'').decode('utf-8', 'replace')
    assert done.returncode == 0, output
