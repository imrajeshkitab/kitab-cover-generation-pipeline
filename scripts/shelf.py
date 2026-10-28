#!/usr/bin/env python3
"""Shelf test: the spec's 20 reference covers on top, our options below, same card size.

  python scripts/shelf.py --recipe summary/v6 --ids a,b,c,d,e   -> outputs/compare/<utc>__shelf__summary-v6.jpg
Each column below is one book; rows are its options 1-4 (cells cropped from the grid if not split).
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ccl import config, store  # noqa: E402
from compare import font, quarters  # noqa: E402

W, H, P = 200, 300, 12


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipe", required=True)
    ap.add_argument("--ids", required=True)
    a = ap.parse_args()
    refs_dir = config.ROOT / "references" / "cutpaper_v1"
    refs = [refs_dir / b["file"] for b in json.loads((refs_dir / "books.json").read_text())]
    cols = []
    for sid in a.ids.split(","):
        g = next(g for g in store.index_query(a.recipe.split("/")[0], sid, limit=200)
                 if g["recipe"] == a.recipe and g["status"].startswith("done"))
        folder = config.OUTPUTS_DIR / g["folder"]
        cov = sorted(folder.glob("*__cover-[1-4].webp")) or quarters(next(folder.glob("*__grid.*")))
        cols.append(cov)
    ncol = max(5, len(cols))
    head = 40
    rows_ref, rows_new = 4, 4
    sheet = Image.new("RGB", (P + ncol * (W + P), 2 * head + (rows_ref + rows_new) * (H + P) + P), "#f7f4ee")
    d = ImageDraw.Draw(sheet)
    d.text((P, 12), "Spec reference shelf (20)", fill="#333", font=font(18))
    for i, r in enumerate(refs):
        sheet.paste(Image.open(r).convert("RGB").resize((W, H)), (P + (i % 5) * (W + P), head + (i // 5) * (H + P)))
    y0 = head + rows_ref * (H + P) + head
    d.text((P, y0 - 28), f"Our {a.recipe} options: one column per book, rows = options 1-4", fill="#333", font=font(18))
    for c, cov in enumerate(cols):
        for r, im in enumerate(cov):
            im = im if isinstance(im, Image.Image) else Image.open(im).convert("RGB")
            sheet.paste(im.resize((W, H)), (P + c * (W + P), y0 + r * (H + P)))
    out = config.OUTPUTS_DIR / "compare" / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}__shelf__{a.recipe.replace('/', '-')}.jpg"
    sheet.save(out, quality=88)
    print(out)


if __name__ == "__main__":
    main()
