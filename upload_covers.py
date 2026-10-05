#!/usr/bin/env python3
"""Upload generated cover grids to the App dev Supabase project.

  books/practical-gita/K3-01/covers.png  ->  RMS-content/gita/draft-covers/K1.png
  books/practical-gita/G3-12/covers.png  ->  RMS-content/gita/draft-covers/G12.png

Needs APPDEV_SUPABASE_URL and APPDEV_SUPABASE_SERVICE_ROLE_KEY in .env (project kijxqpprmvywetklzhbg).

Usage:
  python upload_covers.py                # every folder that has a covers.png
  python upload_covers.py --overwrite    # replace files that already exist
"""
import argparse
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).parent
EXPECTED_REF = "kijxqpprmvywetklzhbg"
BUCKET = "RMS-content"
PREFIX = "gita/draft-covers"
LETTER = {"K3": "K", "G3": "G", "F3": "F"}


def load_env():
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--prefix", default=PREFIX, help="folder inside the bucket")
    args = ap.parse_args()
    load_env()
    url = os.environ.get("APPDEV_SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("APPDEV_SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        sys.exit("APPDEV_SUPABASE_URL / APPDEV_SUPABASE_SERVICE_ROLE_KEY missing in .env")
    if EXPECTED_REF not in url:
        sys.exit(f"APPDEV_SUPABASE_URL does not point at project {EXPECTED_REF}")
    headers = {"Authorization": f"Bearer {key}", "apikey": key, "Content-Type": "image/png",
               "x-upsert": "true" if args.overwrite else "false"}

    done = skipped = 0
    for folder in sorted((ROOT / "books" / "practical-gita").glob("[KGF]3-[0-9][0-9]")):
        img = folder / "covers.png"
        if not img.exists():
            continue
        sheet, num = folder.name.split("-")
        name = f"{LETTER[sheet]}{int(num)}.png"
        r = requests.post(f"{url}/storage/v1/object/{BUCKET}/{args.prefix}/{name}",
                          headers=headers, data=img.read_bytes(), timeout=120)
        if r.ok:
            done += 1
            print(f"uploaded {folder.name}/covers.png -> {BUCKET}/{args.prefix}/{name}")
        elif r.status_code in (400, 409) and "exists" in r.text.lower():
            skipped += 1
            print(f"exists, skipped: {name} (use --overwrite to replace)")
        else:
            sys.exit(f"{name}: {r.status_code} {r.text[:300]}")
    print(f"{done} uploaded, {skipped} skipped")
    print(f"public URL pattern: {url}/storage/v1/object/public/{BUCKET}/{args.prefix}/K1.png")


if __name__ == "__main__":
    main()
