"""Run one local image through the Windows OCR component."""
import json
import sys
from pathlib import Path

from app.ocr_adapter import recognize_file


if __name__ == '__main__':
    source, output = map(Path, sys.argv[1:])
    result = recognize_file(source)
    output.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
