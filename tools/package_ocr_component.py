"""Package a frozen ZemuOCR directory and pin its exact bytes for the lite build."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def package(worker: Path, licenses: Path, output: Path, manifest: Path):
    root = Path(__file__).resolve().parents[1]
    assert (worker / 'ZemuOCR.exe').is_file()
    models = list(worker.rglob('*.onnx'))
    records = (root / 'notices/rapidocr/licenses/MODEL_LICENSES.md').read_text('utf-8').lower()
    assert len(models) == 3 and all(hashlib.sha256(p.read_bytes()).hexdigest() in records for p in models)
    required = ['CPython-LICENSE.txt', 'OpenSSL-LICENSE.txt', 'OpenSSL-AUTHORS.md',
                'ONNXRuntime-LICENSE.txt', 'ONNXRuntime-ThirdPartyNotices.txt', 'ONNXRuntime-Privacy.md',
                'geos-3.14.1.tar.bz2', 'shapely-2.2.0.tar.gz']
    with ZipFile(output, 'w', compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(worker.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(worker))
        archive.write(root / 'LICENSE', 'LICENSE.txt')
        for name in required:
            archive.write(licenses / name, 'Licenses/' + name)
        for path in sorted((root / 'notices').rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(root))
    with output.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({'sha256': digest, 'bytes': output.stat().st_size}, indent=2) + '\n', 'utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('worker', 'licenses', 'output', 'manifest'):
        parser.add_argument(arg, type=Path)
    package(**vars(parser.parse_args()))
