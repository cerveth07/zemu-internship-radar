"""Build the multi-size Windows icon from the sidebar emblem."""
from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parents[1]
out = root / 'assets' / 'radar.ico'
out.parent.mkdir(parents=True, exist_ok=True)
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
with Image.open(root / 'assets' / 'brand-mark.png') as emblem:
    emblem.convert('RGBA').resize((256, 256), Image.Resampling.LANCZOS).save(out, sizes=SIZES)
print(out)
