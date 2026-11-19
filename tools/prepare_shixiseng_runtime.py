"""Prepare the pinned Node sidecar for a Windows PyInstaller build."""
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
target = root / 'tools' / 'shixiseng-node'
if sys.platform != 'win32':
    raise SystemExit('Windows EXE builds must prepare their runtime on Windows')
node, npm = shutil.which('node.exe'), shutil.which('npm.cmd')
if not node or not npm:
    raise SystemExit('Install Windows Node.js 22+ to build the Shixiseng connector')
major = int(subprocess.check_output([node, '--version'], text=True).strip().lstrip('v').split('.')[0])
if major < 22:
    raise SystemExit('Node.js 22+ is required')
# npm.cmd is a Windows batch file; shell invocation is needed for its trusted path.
subprocess.run([npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund', '--prefix', str(target)], check=True, shell=True)
shutil.copy2(node, target / 'node.exe')
print('Pinned Playwright Core and Node executable prepared for packaging')
