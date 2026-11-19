"""Prepare an NSIS build directory containing appdist/Zemu from desktop.spec --lite."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def prepare(stage: Path, licenses: Path):
    root = Path(__file__).resolve().parents[1]
    app = stage / 'appdist/Zemu'
    runtime = app / '_internal'
    spec = json.loads((root / 'packaging/ocr-component.json').read_bytes())
    assert json.loads((runtime / 'ocr-component.json').read_bytes()) == spec
    assert not any((runtime / name).exists() for name in ('rapidocr', 'cv2', 'onnxruntime', 'numpy'))
    assert not list(app.rglob('*.onnx'))
    for name in ('boss_runner.mjs', 'shixiseng_runner.mjs', 'boss_favorites_runner.mjs',
                 'shixiseng-node/node.exe', 'shixiseng-node/node_modules/playwright-core/package.json'):
        original, included = (root / 'tools' / name), (runtime / 'tools' / name)
        with original.open('rb') as first, included.open('rb') as second:
            assert hashlib.file_digest(first, 'sha256').digest() == hashlib.file_digest(second, 'sha256').digest()
    shutil.copyfile(root / 'LICENSE', app / 'LICENSE.txt')
    bundled = app / 'Licenses'
    bundled.mkdir(exist_ok=True)
    for name in ('CPython-LICENSE.txt', 'OpenSSL-LICENSE.txt', 'OpenSSL-AUTHORS.md', 'NSIS-copyright.txt'):
        shutil.copyfile(licenses / name, bundled / name)
    shutil.copyfile(root / 'assets/radar.ico', stage / 'radar.ico')
    shutil.copyfile(root / 'packaging/lite.nsi', stage / 'setup.nsi')
    files = sorted(p.relative_to(app) for p in app.rglob('*') if p.is_file())
    dirs = sorted((p.relative_to(app) for p in app.rglob('*') if p.is_dir()),
                  key=lambda p: (-len(p.parts), str(p)))
    commands = []
    for action, paths in [('Delete', files), ('RMDir', dirs)]:
        for path in paths:
            relative = str(path).replace('/', chr(92))
            if any(char in relative for char in ('"', '$', '\n', '\r')):
                raise ValueError(f'Unexpected character in {relative}')
            commands.append(f'{action} "$INSTDIR{chr(92)}{relative}"')
    (stage / 'remove-installed-files.nsh').write_text('\n'.join(commands) + '\n', encoding='utf-8-sig')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', type=Path)
    parser.add_argument('licenses', type=Path, help='corresponding Windows v1.0 distribution licenses')
    prepare(**vars(parser.parse_args()))
