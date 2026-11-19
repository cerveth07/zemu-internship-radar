"""Collect installed wheel license files for the pinned Windows build."""
from importlib.metadata import distribution
from pathlib import Path
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
target = ROOT / 'notices'
target.mkdir(exist_ok=True)
inventory = []
for requirement in (ROOT / 'constraints-windows.txt').read_text('utf-8').splitlines():
    name, version = requirement.split('==')
    dist = distribution(name)
    if dist.version != version:
        raise SystemExit(f'{name}: expected {version}, installed {dist.version}')
    written = []
    for file in dist.files or []:
        if not any(part.endswith('.dist-info') for part in file.parts):
            continue
        if not file.parts[0].endswith('.dist-info'):
            continue
        if not any(mark in file.name.upper() for mark in ('LICENSE', 'COPYING', 'NOTICE', 'COPYRIGHT')):
            continue
        dest = target / name / Path(*file.parts[1:])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dist.locate_file(file), dest)
        written.append(dest.relative_to(target).as_posix())
    inventory.append({'distribution': name, 'version': version, 'wheel_notices': written})
stdlib = Path(sys.base_prefix) / 'LICENSE.txt'
shutil.copyfile(stdlib, target / 'CPython-LICENSE.txt')
(target / 'distributions.json').write_text(json.dumps(inventory, indent=2)+'\n', 'utf-8')
