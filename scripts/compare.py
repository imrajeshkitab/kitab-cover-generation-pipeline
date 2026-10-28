#!/usr/bin/env python3
"""Comparison sheet: per book, the spec's reference cover (if any) next to our 4 cover options.

  python scripts/compare.py --recipe summary/v2 --ids c989f8e07b9d7b23daccb35c,8aa4b1c5bb684a6269116f6b
  -> outputs/compare/<utc time>__summary-v2.jpg   (uses each item's latest finished generation of that recipe)
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ccl import config, store  # noqa: E402

REFS = config.ROOT / "references" / "cutpaper_v1"
W, H, PAD, LABEL = 240, 360, 14, 46


def font(size):
    for f in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def quarters(grid_path):
    im = Image.open(grid_path).convert("RGB")
    w, h = im.size
    return [im.crop((x, y, x + w // 2, y + h // 2)) for y in (0, h // 2) for x in (0, w // 2)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipe", required=True, help="e.g. summary/v2")
    ap.add_argument("--ids", required=True)
    args = ap.parse_args()
    type_ = args.recipe.split("/")[0]
    refs = {b["source_id"]: REFS / b["file"] for b in json.loads((REFS / "books.json").read_text()) if b["source_id"]}

    rows = []
    for sid in [i.strip() for i in args.ids.split(",") if i.strip()]:
        gens = [g for g in store.index_query(type_, sid, limit=200) if g["recipe"] == args.recipe and g["status"].startswith("done")]
        if not gens:
            print(f"no finished {args.recipe} generation for {sid}", file=sys.stderr)
            continue
        g = gens[0]
        folder = config.OUTPUTS_DIR / g["folder"]
        covers = sorted(folder.glob("*__cover-[1-4].webp")) or sorted(folder.glob("*__cover-[1-4].png"))
        if not covers:                                   # grid-only run: show the 4 cells cropped from the grid
            grid = next(folder.glob("*__grid.*"), None)
            covers = quarters(grid) if grid else []
        rows.append((g, refs.get(sid), covers))

    cols = 5
    sheet = Image.new("RGB", (PAD + cols * (W + PAD), PAD + len(rows) * (H + LABEL + PAD)), "white")
    d = ImageDraw.Draw(sheet)
    f_title, f_small = font(16), font(12)
    for r, (g, ref, covers) in enumerate(rows):
        y = PAD + r * (H + LABEL + PAD)
        d.text((PAD, y), g["title"][:70], fill="#222", font=f_title)
        d.text((PAD, y + 22), f"{args.recipe} · gen {g['gen_id']}", fill="#777", font=f_small)
        y += LABEL
        cells = [("reference (spec)", ref)] + [(f"option {i + 1}", c) for i, c in enumerate(covers)]
        for c, (label, path) in enumerate(cells):
            x = PAD + c * (W + PAD)
            if isinstance(path, Image.Image):
                sheet.paste(path.resize((W, H)), (x, y))
            elif path and Path(path).exists():
                sheet.paste(Image.open(path).convert("RGB").resize((W, H)), (x, y))
            else:
                d.rectangle([x, y, x + W, y + H], outline="#ccc")
                d.text((x + 10, y + H // 2), "no reference", fill="#999", font=f_small)
    out_dir = config.OUTPUTS_DIR / "compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}__{args.recipe.replace('/', '-')}.jpg"
    sheet.save(out, quality=88)
    print(out)


if __name__ == "__main__":
    main()
