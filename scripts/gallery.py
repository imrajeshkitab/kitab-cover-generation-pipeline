#!/usr/bin/env python3
"""Gallery for a batch: a sheet of the 2x2 grids, a sheet of every single cover, and an HTML page.

  python scripts/gallery.py --recipe summary/v7 --ids-file outputs/batch_20_ids.txt [--name batch-20]
  -> outputs/compare/<utc>__<name>__grids.jpg, __covers.jpg, __gallery.html
Uses each item's latest finished generation of the recipe.
"""
import argparse
import base64
import html
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ccl import config, store  # noqa: E402
from compare import font  # noqa: E402


def load(recipe, ids):
    books = []
    for sid in ids:
        gens = [g for g in store.index_query(recipe.split("/")[0], sid, limit=200)
                if g["recipe"] == recipe and g["status"].startswith("done")]
        if not gens:
            print(f"skip {sid}: no finished {recipe} generation", file=sys.stderr)
            continue
        g = gens[0]
        folder = config.OUTPUTS_DIR / g["folder"]
        content = json.loads(next(folder.glob("*__content.json")).read_text(encoding="utf-8"))
        books.append({"gen": g, "folder": folder, "fields": content["fields"],
                      "grid": next(folder.glob("*__grid.*")),
                      "covers": sorted(folder.glob("*__cover-[1-4].webp"))})
    return books


def label(d, x, y, b, w):
    f = b["fields"]
    d.text((x, y), (f.get("short_title") or f["title"])[:38], fill="#222", font=font(15))
    d.text((x, y + 19), f"{f.get('author', '')[:30]} · {f.get('genre', '')[:18]}", fill="#777", font=font(11))


def grids_sheet(books, out, cols=5, W=300, H=450, P=14, L=40):
    rows = (len(books) + cols - 1) // cols
    s = Image.new("RGB", (P + cols * (W + P), P + rows * (H + L + P)), "#f7f4ee")
    d = ImageDraw.Draw(s)
    for i, b in enumerate(books):
        x, y = P + (i % cols) * (W + P), P + (i // cols) * (H + L + P)
        label(d, x, y, b, W)
        s.paste(Image.open(b["grid"]).convert("RGB").resize((W, H)), (x, y + L))
    s.save(out, quality=88)


def covers_sheet(books, out, per_row=2, W=180, H=270, P=10, G=26, L=40):
    rows = (len(books) + per_row - 1) // per_row
    bw = 4 * (W + P)
    s = Image.new("RGB", (P + per_row * (bw + G), P + rows * (H + L + P)), "#f7f4ee")
    d = ImageDraw.Draw(s)
    for i, b in enumerate(books):
        x0, y = P + (i % per_row) * (bw + G), P + (i // per_row) * (H + L + P)
        label(d, x0, y, b, bw)
        for j, c in enumerate(b["covers"]):
            s.paste(Image.open(c).convert("RGB").resize((W, H)), (x0 + j * (W + P), y + L))
    s.save(out, quality=88)


def b64(path, w):
    im = Image.open(path).convert("RGB")
    im.thumbnail((w, w * 2))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def gallery_html(books, recipe, out):
    e = html.escape
    cards = []
    for i, b in enumerate(books, 1):
        f, g = b["fields"], b["gen"]
        covers = "".join(f'<figure><img src="{b64(c, 320)}" alt="option {j}"><figcaption>option {j}</figcaption></figure>'
                         for j, c in enumerate(b["covers"], 1))
        cards.append(f"""<section><header><span class="n">{i:02d}</span><div><h2>{e(f.get('short_title') or f['title'])}</h2>
<p>{e(f.get('author', ''))} · {e(f.get('genre', ''))}</p></div></header>
<div class="row"><figure class="grid"><img src="{b64(b['grid'], 360)}" alt="grid"><figcaption>2x2 grid</figcaption></figure>{covers}</div>
<p class="meta">source_id <code>{e(g['source_id'])}</code> · gen <code>{e(g['gen_id'])}</code> · <code>{e(str(b['folder'].relative_to(config.ROOT)))}</code></p></section>""")
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cover batch gallery</title><style>
:root{{--bg:#f7f4ee;--fg:#24211d;--muted:#7a7268;--card:#fffdf9;--line:#e6dfd3}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1916;--fg:#eee8de;--muted:#a59d90;--card:#24211d;--line:#3a352e}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,system-ui,sans-serif;margin:0 auto;padding:24px 16px;max-width:1400px}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:var(--muted);margin:0 0 20px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:0 0 14px}}
header{{display:flex;gap:12px;align-items:baseline}} .n{{color:var(--muted);font-variant-numeric:tabular-nums}}
h2{{font-size:17px;margin:0}} header p{{margin:2px 0 0;color:var(--muted)}}
.row{{display:flex;flex-wrap:wrap;gap:10px;margin-top:10px}} figure{{margin:0}} img{{width:160px;border-radius:6px;display:block;border:1px solid var(--line)}}
figure.grid img{{width:200px}} figcaption{{font-size:11px;color:var(--muted);margin-top:3px}}
.meta{{font-size:11px;color:var(--muted);margin:8px 0 0;word-break:break-all}} code{{font-size:11px}}
@media (max-width:600px){{img{{width:44vw}} figure.grid img{{width:90vw}}}}
</style></head><body><h1>Cover batch · {len(books)} books</h1>
<p class="sub">recipe <code>{e(recipe)}</code> · 4 options per book (2x2 grid cropped locally) · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>
{''.join(cards)}</body></html>"""
    out.write_text(page, encoding="utf-8")


def artifact_html(books, recipe, out, credits=None):
    """Shareable page (claude.ai artifact contract: no doctype/html/body, theme tokens, phone width)."""
    e = html.escape
    cards = []
    for i, b in enumerate(books, 1):
        f = b["fields"]
        covers = "".join(f'<figure><img src="{b64(c, 300)}" alt="{e(f.get("short_title") or f["title"])}, option {j}" '
                         f'loading="lazy"><figcaption>Option {j}</figcaption></figure>' for j, c in enumerate(b["covers"], 1))
        cards.append(f"""<article class="book" id="b{i:02d}">
  <div class="book-head"><h2>{e(f.get('short_title') or f['title'])}</h2>
  <p class="by">{e(f.get('author', ''))} <span class="genre">{e(f.get('genre', ''))}</span></p></div>
  <div class="options">{covers}</div>
</article>""")
    note = f" · about {credits:,} image credits" if credits else ""
    page = f"""<title>Kitab Cover Drafts</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500&family=Work+Sans:wght@400;500;600&display=swap">
<style>
/* Layout: a quiet review shelf; one row per book, its four cover options side by side, wrapping to two columns on phones. */
:root {{
  --paper: #f6f2ea; --card: #fffcf6; --ink: #2a2620; --muted: #776d60; --line: #e4dccf; --accent: #9a6b2f;
  --display: "Newsreader", Georgia, "Times New Roman", serif;
  --body: "Work Sans", -apple-system, "Segoe UI", sans-serif;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --paper: #1c1a17; --card: #25221e; --ink: #efe8dc; --muted: #a99f90; --line: #3a352e; --accent: #d9a35a; color-scheme: dark }} }}
:root[data-theme="dark"] {{ --paper: #1c1a17; --card: #25221e; --ink: #efe8dc; --muted: #a99f90; --line: #3a352e; --accent: #d9a35a; color-scheme: dark }}
body {{ background: var(--paper); color: var(--ink); font: 15px/1.55 var(--body);
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }}
.wrap {{ max-width: 1180px; margin: 0 auto; padding-block: 32px 56px; display: grid; gap: 18px; }}
header {{ display: grid; gap: 8px; padding-bottom: 8px; }}
.eyebrow {{ font-size: 12px; letter-spacing: .12em; text-transform: uppercase; color: var(--accent); font-weight: 600; margin: 0; }}
h1 {{ font: 500 clamp(30px, 5vw, 44px)/1.1 var(--display); margin: 0; text-wrap: balance; }}
.lede {{ margin: 0; max-width: 68ch; color: var(--muted); }}
.facts {{ display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 4px 0 0; padding: 0; list-style: none; font-size: 13px; color: var(--muted); }}
.facts b {{ color: var(--ink); font-weight: 500; }}
.book {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; display: grid; gap: 12px; min-width: 0; }}
.book-head {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 14px; }}
h2 {{ font: 500 22px/1.2 var(--display); margin: 0; }}
.by {{ margin: 0; color: var(--muted); font-size: 14px; }}
.genre {{ margin-left: 8px; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }}
.options {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
figure {{ margin: 0; display: grid; gap: 5px; }}
figure img {{ width: 100%; max-width: 100%; aspect-ratio: 2 / 3; object-fit: cover; border-radius: 6px; display: block;
  box-shadow: 0 1px 2px rgba(40, 28, 16, .12), 0 6px 18px rgba(40, 28, 16, .08); background: var(--line); }}
figcaption {{ font-size: 12px; color: var(--muted); }}
footer {{ font-size: 12px; color: var(--muted); padding-top: 8px; }}
@media (max-width: 640px) {{ .options {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
</style>
<div class="wrap">
<header>
  <p class="eyebrow">Draft · Book summary covers</p>
  <h1>Kitab Cover Drafts</h1>
  <p class="lede">Four cover options for each of {len(books)} book summaries, in the symbolic cut-paper style from the Kitab thumbnail spec.
  Each cover tries to show how a reader wants to feel after the book, rather than the title or the book's method.
  These are drafts for review; nothing here is live in the app.</p>
  <ul class="facts"><li><b>{len(books)}</b> books</li><li><b>{4 * len(books)}</b> options</li><li>Prompt written by Gemini, art by GPT Image 2.5 (Sunburst) at 2K{note}</li>
  <li>Prompt recipe <b>{e(recipe)}</b></li><li>{datetime.now(timezone.utc).strftime('%d %b %Y')}</li></ul>
</header>
{''.join(cards)}
<footer>Generated with the Kitab content-cover pipeline. Titles come from the summaries catalogue; the art leaves room for the title at the top, as the spec asks.</footer>
</div>"""
    out.write_text(page, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipe", required=True)
    ap.add_argument("--ids-file", required=True)
    ap.add_argument("--name", default="batch")
    ap.add_argument("--credits", type=int, help="image credits used, shown on the shareable page")
    a = ap.parse_args()
    ids = [i.strip() for i in Path(a.ids_file).read_text().replace("\n", ",").split(",") if i.strip()]
    books = load(a.recipe, ids)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = config.OUTPUTS_DIR / "compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{stamp}__{a.name}"
    grids_sheet(books, Path(f"{base}__grids.jpg"))
    covers_sheet(books, Path(f"{base}__covers.jpg"))
    gallery_html(books, a.recipe, Path(f"{base}__gallery.html"))
    artifact_html(books, a.recipe, Path(f"{base}__share.html"), a.credits)
    print(f"{len(books)} books\n{base}__grids.jpg\n{base}__covers.jpg\n{base}__gallery.html\n{base}__share.html")


if __name__ == "__main__":
    main()
