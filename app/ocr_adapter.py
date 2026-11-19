"""Optional local Chinese OCR adapter. Original screenshots are never changed.

RapidOCR comes from RapidAI; recognition is a *suggestion*, not a validated JD.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import importlib.util
from numbers import Number
from . import ocr_component


def is_available() -> bool:
    if spec := ocr_component.manifest():
        return ocr_component.installed(spec)
    return importlib.util.find_spec('rapidocr') is not None and importlib.util.find_spec('onnxruntime') is not None


@lru_cache(maxsize=1)
def _engine():
    from rapidocr import RapidOCR
    return RapidOCR()


def recognize_file(path: Path) -> dict:
    if spec := ocr_component.manifest():
        return ocr_component.recognize(path, spec)
    result = _engine()(str(path))
    txts = getattr(result, 'txts', None)
    scores = getattr(result, 'scores', None)
    # No invented content on unrecognized images.
    if txts is None and isinstance(result, (tuple, list)):
        entries = result[0] if len(result) == 2 and isinstance(result[0], list) else result
        txts = [r[1] for r in entries if isinstance(r, (list, tuple)) and len(r) >= 3]
        scores = [r[2] for r in entries if isinstance(r, (list, tuple)) and len(r) >= 3]
    strings = [str(v).strip() for v in (txts or ())]
    strings = [v for v in strings if v]
    if not strings:
        return {'text': '', 'line_count': 0, 'confidence': None, 'provider': 'rapidocr'}
    content = '\n'.join(strings)
    if len(content) > 100_000:
        raise ValueError('识别结果超过 100000 字符，不能静默截断 JD')
    nums = [float(v) for v in (scores if scores is not None else []) if isinstance(v, Number)]
    return {'text': content, 'line_count': len(strings),
            'confidence': round(sum(nums)/len(nums), 3) if nums else None, 'provider': 'rapidocr'}


def smoke_check(base64_fixture: Path) -> dict:
    """Verify the installed OCR engine loads models and recognizes Chinese text.

    The fixture is encoded as text so it is straightforward to version, bundle,
    and audit without shipping any font. Recognition happens entirely locally.
    """
    import base64
    import tempfile

    with tempfile.TemporaryDirectory(prefix='radar-ocr-check-') as directory:
        screenshot = Path(directory) / 'sample.png'
        encoded = base64_fixture.read_text('ascii').strip()
        screenshot.write_bytes(base64.b64decode(encoded, validate=True))
        result = recognize_file(screenshot)
    if not any(word in result['text'] for word in ('实习', '产品经理', '岗位')):
        raise RuntimeError('中文 OCR 无法识别预置岗位截图；请检查中文模型及 ONNX Runtime')
    return result
