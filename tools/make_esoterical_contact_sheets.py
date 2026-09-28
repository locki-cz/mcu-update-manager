from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import json
import math


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "esoterical-images-manifest.json").read_text(encoding="utf-8"))
    out_dir = root / "esoterical-contact-sheets"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows_per_sheet = 4
    cols = 2
    cell_w = 980
    cell_h = 390
    label_h = 58
    per_sheet = rows_per_sheet * cols

    for sheet_no in range(math.ceil(len(manifest) / per_sheet)):
        batch = manifest[sheet_no * per_sheet : (sheet_no + 1) * per_sheet]
        sheet = Image.new("RGB", (cols * cell_w, rows_per_sheet * (cell_h + label_h)), "white")
        draw = ImageDraw.Draw(sheet)
        for index, item in enumerate(batch):
            row = index // cols
            col = index % cols
            x = col * cell_w
            y = row * (cell_h + label_h)
            label = f"{sheet_no * per_sheet + index + 1:03d} | {item['title']} | {item['heading']}"
            draw.rectangle([x, y, x + cell_w - 1, y + label_h - 1], fill=(235, 235, 235))
            draw.text((x + 8, y + 8), label[:120], fill=(0, 0, 0))
            image = Image.open(root / item["path"]).convert("RGB")
            image.thumbnail((cell_w - 16, cell_h - 16), Image.Resampling.LANCZOS)
            sheet.paste(image, (x + 8, y + label_h + 8))
        sheet.save(out_dir / f"sheet-{sheet_no + 1:02d}.png")

    print(f"created {math.ceil(len(manifest) / per_sheet)} sheets")


if __name__ == "__main__":
    main()
