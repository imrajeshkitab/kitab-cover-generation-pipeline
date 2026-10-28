#!/usr/bin/env python3
"""Render one generation as a self-contained HTML page: the flow, the prompt used at each node, each
output, and where every file is saved.

  python scripts/report.py outputs/summary/<source_id>__<slug>/en/<gen_id>
  -> writes <gen folder>/<stem>__report.html
"""
import base64
import html
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ccl import config, prompts  # noqa: E402
from ccl.nodes import SPLIT_CELLS  # noqa: E402

FLOW = [("content", "App Prod", "snapshot of the content"), ("facts", "Template", "facts block"),
        ("synopsis", "Gemini", "condensed synopsis"), ("prompt", "Gemini", "4-cover image prompt"),
        ("cover", "ElevenLabs", "2x2 grid"), ("split", "ElevenLabs", "4 single covers"),
        ("export", "Local", "WebP files")]


def esc(s):
    return html.escape(str(s))


def pre(text, cls=""):
    return f'<pre class="{cls}">{esc(text)}</pre>'


def details(summary, body, open_=False):
    return f'<details{" open" if open_ else ""}><summary>{esc(summary)}</summary>{body}</details>'


def img(path: Path, width=260):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((width * 2, width * 3))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return (f'<figure><img src="data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}" '
            f'style="width:{width}px"><figcaption>{esc(path.name.split("__")[-1])}</figcaption></figure>')


def kv(d, keys):
    rows = "".join(f"<tr><th>{esc(k)}</th><td>{esc(d.get(k))}</td></tr>" for k in keys if d.get(k) is not None)
    return f'<table class="kv">{rows}</table>'


def main(folder):
    folder = Path(folder).resolve()
    meta = json.loads(next(folder.glob("*__meta.json")).read_text(encoding="utf-8"))
    st, recipe = meta["stages"], meta["recipe"]
    f = {p.name.split("__")[-1]: p for p in folder.iterdir() if p.is_file()}
    text = lambda name: f[name].read_text(encoding="utf-8") if name in f else ""
    content = json.loads(text("content.json"))
    stem = next(folder.glob("*__meta.json")).name.rsplit("__", 1)[0]

    cards = []

    # content
    cards.append(("content", "Source: App Prod (read-only)",
                  kv(content["db_ref"] | {"source_id": content["source_id"], "lang": content["lang"],
                                          "type": content["type"]}, ["type", "source_id", "lang", "table", "id"]),
                  details(f"Body fetched ({content['body_chars']:,} chars)", pre(content["body"]))
                  + details("Fields", pre(json.dumps(content["fields"], indent=2, ensure_ascii=False)), True)))

    # facts
    fc = recipe["facts"]
    tpl = prompts.load(fc["block"]) if fc.get("mode", "db") == "db" else prompts.load(fc["prompt"])
    cards.append(("facts", f"Template {fc.get('block') or fc.get('prompt')}",
                  details("Template", pre(tpl), True), pre(text("facts.txt"), "out")))

    # synopsis
    sc, si = recipe["synopsis"], st.get("synopsis", {})
    if si.get("mode") == "generated":
        prompt_side = (kv(si, ["prompt", "model", "thinking", "attempts", "source_chars", "chars", "trimmed", "finish"])
                       + details(f"Prompt template {sc['prompt']}", pre(prompts.load(sc["prompt"])), True)
                       + (details(f"Retry suffix {sc['retry_prompt']}", pre(prompts.load(sc["retry_prompt"])))
                          if sc.get("retry_prompt") else ""))
    else:
        prompt_side = "<p>Passthrough: the body is used as the synopsis unchanged.</p>"
    cards.append(("synopsis", f"mode {si.get('mode')}", prompt_side,
                  details(f"Synopsis ({si.get('chars', len(text('synopsis.txt'))):,} chars)", pre(text("synopsis.txt"), "out"), True)))

    # prompt
    pc, pi = recipe["prompt"], st.get("prompt", {})
    cards.append(("prompt", f"{pc['design']} + {pc['assembly']}",
                  kv(pi, ["model", "thinking", "attempts", "input_chars", "chars", "finish"])
                  + details(f"Design prompt {pc['design']}", pre(prompts.load(pc["design"])))
                  + details(f"Assembly {pc['assembly']}", pre(prompts.load(pc["assembly"])))
                  + details(f"Full Gemini input sent ({pi.get('input_chars', 0):,} chars)", pre(text("gemini-input.txt"))),
                  details(f"Image prompt ({pi.get('chars', 0):,} chars)", pre(text("prompt.txt"), "out"), True)))

    # cover
    ci = st.get("cover", {})
    grid = next((p for k, p in f.items() if k.startswith("grid.")), None)
    cards.append(("cover", f"{ci.get('model')} {ci.get('aspect_ratio')} {ci.get('resolution')} {ci.get('quality')}",
                  kv(ci, ["model", "aspect_ratio", "resolution", "quality", "seconds", "elevenlabs_generation_id"])
                  + "<p>Input: the image prompt above.</p>",
                  img(grid, 300) if grid else "<p>not run</p>"))

    # split
    xi, xc = st.get("split", {}), recipe["split"]
    covers = sorted((p for k, p in f.items() if k.startswith("cover-") and k.endswith(".png")), key=lambda p: p.name)
    split_prompts = "".join(pre(prompts.render(prompts.load(xc["prompt"]), cell=c, span=s)) for c, s, _ in SPLIT_CELLS)
    cards.append(("split", f"{xi.get('model', xc['model'])} {xc['aspect_ratio']} {xc['resolution']} {xc['quality']}",
                  kv(xi, ["prompt", "model", "resolution", "quality"]) + "<p>Reference image: the grid. One call per cell:</p>"
                  + details(f"Prompt template {xc['prompt']} (rendered x4)", split_prompts, True),
                  '<div class="row">' + "".join(img(p, 150) for p in covers) + "</div>" if covers else "<p>not run</p>"))

    # export
    ei = st.get("export", {})
    webps = sorted((p for k, p in f.items() if k.endswith(".webp")), key=lambda p: p.name)
    cards.append(("export", f"{ei.get('format', recipe['export']['format'])} q{ei.get('quality', recipe['export']['quality'])}",
                  "<p>Converts the 4 single covers.</p>",
                  "<ul>" + "".join(f"<li>{esc(p.name.split('__')[-1])} &middot; {p.stat().st_size // 1024} KB</li>" for p in webps) + "</ul>"
                  if webps else "<p>not run</p>"))

    flow = '<div class="flow">' + '<div class="arrow">→</div>'.join(
        f'<a class="step {"done" if s in st else "todo"}" href="#{s}"><b>{s}</b><span>{esc(w)}</span><i>{esc(o)}</i></a>'
        for s, w, o in FLOW) + "</div>"

    body_cards = "".join(
        f'<section id="{s}"><h2>{i + 1}. {s} <small>{esc(sub)}</small></h2><div class="grid2">'
        f'<div><h3>Prompt / settings used</h3>{left}</div><div><h3>Output</h3>{right}</div></div>'
        f'<p class="files">Files: {", ".join(esc(n) for n in st.get(s, {}).get("files", [])) or "—"}</p></section>'
        for i, (s, sub, left, right) in enumerate(cards))

    rel = folder.relative_to(config.OUTPUTS_DIR) if folder.is_relative_to(config.OUTPUTS_DIR) else folder
    tree = "\n".join([f"outputs/", f"├── index.sqlite          one row per generation (python -m ccl status)",
                      f"├── batches.jsonl         one line per run: ids, recipe, results, ElevenLabs credits",
                      f"└── {rel}/"]
                     + [f"      ├── {p.name}" for p in sorted(folder.iterdir()) if p.is_file()])
    naming = ("&lt;type&gt;__&lt;slug&gt;__&lt;source_id&gt;__&lt;lang&gt;__&lt;gen_id&gt;__&lt;stage&gt;.&lt;ext&gt;")

    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cover generation report</title><style>
:root{{--bg:#fff;--fg:#1d1d1f;--muted:#6b6b70;--card:#f6f6f8;--line:#e2e2e6;--accent:#b06d00;--ok:#1f7a3a}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151517;--fg:#ececf0;--muted:#a0a0a8;--card:#1f1f23;--line:#33333a;--accent:#f0b24a;--ok:#5fcf86}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,system-ui,sans-serif;margin:0;padding:24px 16px;max-width:1200px;margin:auto}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:18px;margin:0 0 12px}} h2 small{{color:var(--muted);font-weight:400;font-size:13px}}
h3{{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 0 8px}}
.meta{{color:var(--muted);margin-bottom:20px}} code{{font-size:12px}}
.flow{{display:flex;flex-wrap:wrap;align-items:stretch;gap:6px;margin:16px 0 28px}}
.step{{flex:1 1 120px;border:1px solid var(--line);border-radius:10px;padding:10px;text-decoration:none;color:var(--fg);background:var(--card);display:flex;flex-direction:column;gap:2px}}
.step.done{{border-color:var(--ok)}} .step span{{color:var(--accent);font-size:12px}} .step i{{color:var(--muted);font-size:12px;font-style:normal}}
.arrow{{align-self:center;color:var(--muted)}}
section{{border:1px solid var(--line);border-radius:12px;padding:16px;margin:0 0 16px;background:var(--card)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} @media (max-width:800px){{.grid2{{grid-template-columns:1fr}}}}
pre{{white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;max-height:420px;overflow:auto;font-size:12px;margin:6px 0}}
pre.out{{border-color:var(--accent)}} details{{margin:6px 0}} summary{{cursor:pointer;font-weight:600}}
table.kv{{border-collapse:collapse;font-size:12px;margin-bottom:8px}} table.kv th{{text-align:left;color:var(--muted);padding:2px 10px 2px 0;font-weight:500}}
figure{{margin:0 8px 8px 0;display:inline-block}} figure img{{border-radius:6px;border:1px solid var(--line);display:block}} figcaption{{font-size:11px;color:var(--muted)}}
.row{{display:flex;flex-wrap:wrap}} .files{{font-size:11px;color:var(--muted);margin:10px 0 0;word-break:break-all}}
</style></head><body>
<h1>{esc(meta['title'])}</h1>
<div class="meta">{esc(meta['type'])} · source_id <code>{esc(meta['source_id'])}</code> · {esc(meta['lang'])} · gen_id <code>{esc(meta['gen_id'])}</code>
 · recipe <code>{esc(recipe['id'])}</code> · status {esc(meta['status'])}{(' · parent ' + esc(meta['parent_gen_id'])) if meta.get('parent_gen_id') else ''}</div>
{flow}{body_cards}
<section><h2>Where everything is saved</h2><p>Every file name: <code>{naming}</code></p>{pre(tree)}</section>
</body></html>"""
    out = folder / f"{stem}__report.html"
    out.write_text(page, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main(sys.argv[1])
