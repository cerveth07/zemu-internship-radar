import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from zipfile import ZipFile

import pytest

from app import ocr_component as component


class Reply(io.BytesIO):
    def geturl(self):
        return component.URL


@pytest.fixture
def archive(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with ZipFile(buf, 'w') as bundle:
        bundle.writestr('ZemuOCR.exe', b'synthetic worker fixture')
        bundle.writestr('_internal/fixture.txt', b'synthetic dependency')
    blob = buf.getvalue()
    spec = {'sha256': hashlib.sha256(blob).hexdigest(), 'bytes': len(blob)}
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    return blob, spec


@pytest.mark.parametrize('failure', ['offline', 'truncated', 'wrong_digest', 'oversize'])
def test_failed_download_is_retryable_and_keeps_cache_clean(archive, monkeypatch, failure):
    blob, spec = archive
    def broken(request, timeout):
        if failure == 'offline':
            raise OSError('offline')
        return Reply({'truncated': blob[:-10], 'wrong_digest': b'x' * len(blob), 'oversize': blob + b'x'}[failure])
    monkeypatch.setattr(component, 'urlopen', broken)
    assert not component.installed(spec)
    with pytest.raises(component.ComponentError):
        component.ensure_installed(spec)
    folder = component._destination(spec)
    assert not list(folder.parent.iterdir())
    monkeypatch.setattr(component, 'urlopen', lambda *a, **kw: Reply(blob))
    assert (component.ensure_installed(spec) / '_internal/fixture.txt').read_bytes() == b'synthetic dependency'
    monkeypatch.setattr(component, 'urlopen', lambda *a, **kw: pytest.fail('cached OCR requested the network'))
    assert component.installed(spec) and component.ensure_installed(spec) == folder


def test_parallel_recognitions_install_component_once(archive, monkeypatch):
    blob, spec = archive
    downloads = []
    def fetch(request, timeout):
        downloads.append(request.full_url)
        return Reply(blob)
    monkeypatch.setattr(component, 'urlopen', fetch)
    with ThreadPoolExecutor(max_workers=3) as pool:
        folders = list(pool.map(component.ensure_installed, [spec] * 3))
    assert len(set(folders)) == 1 and downloads == [component.URL]
