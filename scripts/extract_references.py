#!/usr/bin/env python3
"""Crop the 20 reference covers from the spec's contact sheet (page 28) into references/cutpaper_v1/."""
import json
import sys
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "references" / "cutpaper_v1"
GRID = [  # contact sheet order, row by row
    "Atomic Habits", "The Power of Habit", "Deep Work", "Digital Minimalism", "The Psychology of Money",
    "Rich Dad Poor Dad", "The Power of Now", "Bhagavad Gita", "Ikigai", "The Alchemist",
    "Siddhartha", "Sapiens", "Man's Search for Meaning", "Think Like a Monk", "Mindset",
    "The Courage to Be Disliked", "How to Win Friends", "The 5 Love Languages", "Why We Sleep", "The Heartfulness Way"]
# App Prod summaries.source_id for the reference books that exist in the catalogue
APP_PROD = {"Atomic Habits": "c989f8e07b9d7b23daccb35c", "The Power of Habit": "a37b11281d13eefddd1af3f3",
            "Deep Work": "8aa4b1c5bb684a6269116f6b", "Digital Minimalism": "SUM-178",
            "The Power of Now": "f03dc8f0c11ca5ca62b528af", "The Alchemist": "f861e3a265180ead7098c9f8",
            "Sapiens": "c326a5c6a29f060d4cbcf86b", "Man's Search for Meaning": "8128911960a712536999f96a",
            "Mindset": "02fd353c15e2090a8d892d94", "How to Win Friends": "8e0e78a1a569d44d964f3052",
            "Why We Sleep": "a5868c55e607ed00ff508d98", "The Heartfulness Way": "7128ce12-f52f-4b44-90aa-9b6243cefd25"}
# card boxes as fractions of the page (measured on the page-28 render)
X = [(0.0873, 0.2370), (0.2562, 0.4060), (0.4251, 0.5749), (0.5940, 0.7438), (0.7630, 0.9127)]
Y = [(0.1534, 0.3123), (0.3239, 0.4820), (0.4936, 0.6531), (0.6640, 0.8228)]


def slug(t):
    return "".join(c if c.isalnum() else "-" for c in t.lower()).strip("-").replace("--", "-")


def main(pdf="Kitab_AI_Thumbnail_Spec_1.pdf"):
    page = fitz.open(ROOT / pdf)[27]
    w, h = page.rect.width, page.rect.height
    OUT.mkdir(parents=True, exist_ok=True)
    books = []
    for i, title in enumerate(GRID):
        (x0, x1), (y0, y1) = X[i % 5], Y[i // 5]
        clip = fitz.Rect(x0 * w, y0 * h, x1 * w, y1 * h)
        pix = page.get_pixmap(clip=clip, dpi=300)
        f = OUT / f"{i + 1:02d}_{slug(title)}.png"
        pix.save(f)
        books.append({"n": i + 1, "title": title, "file": f.name, "source_id": APP_PROD.get(title)})
    (OUT / "books.json").write_text(json.dumps(books, indent=2), encoding="utf-8")
    print(f"saved {len(books)} reference covers to {OUT}")


if __name__ == "__main__":
    main(*sys.argv[1:])
