"""Regenerate assets/ocr-smoke.b64 from the Noto font listed in LICENSE-STATUS.md."""
import argparse
import base64
import hashlib
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


FONT_SHA256 = "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b"
FIXTURE = Path(__file__).resolve().parents[1] / "assets" / "ocr-smoke.b64"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("font", type=Path, help="NotoSansCJKsc-Regular.otf (Sans2.004)")
    args = parser.parse_args()
    if hashlib.sha256(args.font.read_bytes()).hexdigest() != FONT_SHA256:
        parser.error("Expected NotoSansCJKsc-Regular.otf from the pinned Sans2.004 release")

    font = ImageFont.truetype(str(args.font), size=56)
    image = Image.new("RGB", (960, 220), color="white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 20), "实习岗位测试", font=font, fill="black")
    draw.text((40, 112), "本地文字识别", font=font, fill="black")
    png = io.BytesIO()
    image.save(png, format="PNG")
    FIXTURE.write_bytes(base64.b64encode(png.getvalue()) + b"\n")


if __name__ == "__main__":
    main()
