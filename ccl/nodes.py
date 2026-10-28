"""Pipeline nodes. Each node reads the recipe section of the same name, writes its files through the
Generation object and stores its result in ctx for the next node.

  content -> facts -> synopsis -> prompt -> cover -> split -> export
"""
import io
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from . import models, prompts

STAGES = ["content", "facts", "synopsis", "prompt", "cover", "split", "export"]

SPLIT_CELLS = [  # cell name, area description, file index
    ("TOP-LEFT", "0–50% of the image width and 0–50% of the image height", 1),
    ("TOP-RIGHT", "50–100% of the image width and 0–50% of the image height", 2),
    ("BOTTOM-LEFT", "0–50% of the image width and 50–100% of the image height", 3),
    ("BOTTOM-RIGHT", "50–100% of the image width and 50–100% of the image height", 4),
]


def node_content(gen, item, recipe, ctx):
    data = item.to_dict() | {"body_sha": prompts.digest(item.body), "body_chars": len(item.body)}
    gen.record("content", [gen.write_json("content", data)], body_chars=len(item.body))


def node_facts(gen, item, recipe, ctx):
    cfg = recipe["facts"]
    if cfg.get("mode", "db") == "generate":
        out = models.gemini(prompts.render(prompts.load(cfg["prompt"]), **item.fields),
                            cfg.get("model", "gemini-3.5-flash"), cfg.get("thinking", "minimal"))
        facts = out["text"]
    else:
        facts = prompts.render(prompts.load(cfg["block"]), **item.fields)
    ctx["facts"] = facts
    used = cfg.get("block") or cfg.get("prompt")
    gen.record("facts", [gen.write_text("facts", facts)], mode=cfg.get("mode", "db"),
               block=cfg.get("block"), prompt=cfg.get("prompt"), template_sha=prompts.digest(prompts.load(used)))


def node_synopsis(gen, item, recipe, ctx):
    cfg = recipe["synopsis"]
    mode, body = cfg.get("mode", "auto"), item.body
    if mode == "passthrough" or (mode == "auto" and len(body) <= cfg["max_chars"]):
        ctx["synopsis"] = body
        gen.record("synopsis", [gen.write_text("synopsis", body)], mode="passthrough", chars=len(body))
        return
    lo, hi = cfg["min_chars"], cfg["max_chars"]
    ask = prompts.render(prompts.load(cfg["prompt"]), min_chars=lo, max_chars=hi, facts=ctx["facts"], source=body)
    retry_tpl = prompts.load(cfg["retry_prompt"]) if cfg.get("retry_prompt") else ""
    attempts, text, out = 0, "", {}
    for attempts in range(1, cfg.get("max_retries", 2) + 2):
        out = models.gemini(ask, cfg["model"], cfg.get("thinking"))
        text = out["text"]
        if lo <= len(text) <= hi:
            break
        print(f"  {gen.stem}: synopsis attempt {attempts} is {len(text)} chars (target {lo}-{hi})", file=sys.stderr)
        if retry_tpl:
            ask += prompts.render(retry_tpl, last_chars=len(text), min_chars=lo, max_chars=hi)
    trimmed = False
    if len(text) > hi:                      # last resort: cut at a sentence boundary
        text = text[:hi].rsplit(". ", 1)[0] + "."
        trimmed = True
    ctx["synopsis"] = text
    gen.record("synopsis", [gen.write_text("synopsis", text)], mode="generated", prompt=cfg["prompt"],
               model=cfg["model"], thinking=cfg.get("thinking"), attempts=attempts, chars=len(text),
               source_chars=len(body), trimmed=trimmed, finish=out.get("finish"), usage=out.get("usage"))


def node_prompt(gen, item, recipe, ctx):
    cfg = recipe["prompt"]
    design = prompts.load(cfg["design"])
    gemini_input = prompts.render(prompts.load(cfg["assembly"]), design=design,
                                  synopsis=ctx["synopsis"], facts=ctx["facts"])
    files = [gen.write_text("gemini-input", gemini_input)]
    if len(gemini_input) >= cfg.get("max_input_chars", 10_000):
        raise RuntimeError(f"Gemini input is {len(gemini_input)} chars (limit {cfg.get('max_input_chars')})")
    pillar = item.fields.get("pillar", "")
    best, best_rank, last, attempts = None, None, {}, 0
    for attempts in range(1, cfg.get("max_retries", 2) + 2):
        last = models.gemini(gemini_input, cfg["model"], cfg.get("thinking"))
        text = last["text"]
        if cfg.get("normalize_footer") and pillar:
            text = normalize_footer(text, pillar)
        if "<svg" in text:
            print(f"  {gen.stem}: prompt attempt {attempts} returned SVG code", file=sys.stderr)
            continue
        ok = not cfg.get("footer_check") or footer_score(text, pillar) >= 4
        rank = (ok, -len(text))
        if best is None or rank > best_rank:
            best, best_rank = text, rank
        if ok:
            break
        print(f"  {gen.stem}: prompt attempt {attempts} failed the footer check", file=sys.stderr)
    if best is None:
        raise RuntimeError("Gemini kept returning SVG code")
    ctx["gemini_input"], ctx["image_prompt"] = gemini_input, best
    files.append(gen.write_text("prompt", best))
    gen.record("prompt", files, design=cfg["design"], assembly=cfg["assembly"],
               design_sha=prompts.digest(design), assembly_sha=prompts.digest(prompts.load(cfg["assembly"])),
               model=cfg["model"],
               thinking=cfg.get("thinking"), attempts=attempts, input_chars=len(gemini_input),
               chars=len(best), footer_ok=bool(best_rank and best_rank[0]), finish=last.get("finish"),
               usage=last.get("usage"))


def node_cover(gen, item, recipe, ctx):
    cfg = recipe["cover"]
    out = models.image(ctx["image_prompt"], cfg["model"], cfg["aspect_ratio"], cfg["resolution"], cfg["quality"])
    ctx["grid"] = out["bytes"]
    gen.record("cover", [gen.write_bytes("grid", out["bytes"], _ext(out["mime"]))], model=cfg["model"],
               aspect_ratio=cfg["aspect_ratio"], resolution=cfg["resolution"], quality=cfg["quality"],
               elevenlabs_generation_id=out["generation_id"], seconds=out["seconds"])


def node_split(gen, item, recipe, ctx):
    cfg = recipe["split"]
    if cfg.get("mode", "model") == "crop":
        cells = crop_grid(ctx["grid"])
        ctx["covers"] = cells
        files = [gen.write_bytes(f"cover-{n}", data, "png") for n, data in enumerate(cells, 1)]
        from PIL import Image
        sizes = [Image.open(io.BytesIO(c)).size for c in cells]
        gen.record("split", files, mode="crop", cell_sizes=sizes)
        return
    tpl = prompts.load(cfg["prompt"])

    def one(cell):
        name, span, n = cell
        out = models.image(prompts.render(tpl, cell=name, span=span), cfg["model"], cfg["aspect_ratio"],
                           cfg["resolution"], cfg["quality"], references=[ctx["grid"]])
        return n, out

    with ThreadPoolExecutor(4) as ex:
        results = sorted(ex.map(one, SPLIT_CELLS), key=lambda r: r[0])
    ctx["covers"] = [out["bytes"] for _, out in results]
    files = [gen.write_bytes(f"cover-{n}", out["bytes"], _ext(out["mime"])) for n, out in results]
    gen.record("split", files, prompt=cfg["prompt"], model=cfg["model"], aspect_ratio=cfg["aspect_ratio"],
               resolution=cfg["resolution"], quality=cfg["quality"],
               elevenlabs_generation_ids=[out["generation_id"] for _, out in results])


def node_export(gen, item, recipe, ctx):
    from PIL import Image
    cfg = recipe["export"]
    files = []
    for n, data in enumerate(ctx["covers"], 1):
        im = to_ratio(Image.open(io.BytesIO(data)).convert("RGB"), 2, 3)
        variants = [("", im)] + [(f"-{w}x{h}", im.resize((w, h), Image.LANCZOS)) for w, h in cfg.get("sizes", [])]
        for suffix, v in variants:
            buf = io.BytesIO()
            v.save(buf, cfg["format"].upper(), quality=cfg["quality"], method=6)
            files.append(gen.write_bytes(f"cover-{n}{suffix}", buf.getvalue(), cfg["format"]))
    gen.record("export", files, format=cfg["format"], quality=cfg["quality"], sizes=cfg.get("sizes", []))


def to_ratio(im, rw, rh):
    """Centre-crop to an exact aspect ratio (e.g. 2:3)."""
    w, h = im.size
    if w * rh > h * rw:
        nw = h * rw // rh
        return im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    nh = w * rh // rw
    return im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))


def crop_grid(grid_bytes: bytes) -> list[bytes]:
    """Cut a 2x2 grid into its 4 cells locally (no model call) and trim the gutter on the inner edges only."""
    from PIL import Image
    im = Image.open(io.BytesIO(grid_bytes)).convert("RGB")
    w, h = im.size
    out = []
    for row, y in enumerate((0, h // 2)):
        for col, x in enumerate((0, w // 2)):
            inner = {"right": col == 0, "left": col == 1, "bottom": row == 0, "top": row == 1}
            cell = _trim_gutters(im.crop((x, y, x + w // 2, y + h // 2)), inner)
            buf = io.BytesIO()
            cell.save(buf, "PNG")
            out.append(buf.getvalue())
    return out


def _trim_gutters(cell, inner: dict, max_trim_frac=0.02):
    """Drop near-uniform light lines on the sides facing the grid centre (the gutter). Outer edges are kept,
    so light backgrounds and the top margin above the title are never cut."""
    import numpy as np
    a = np.asarray(cell).astype(int)
    h, w, _ = a.shape
    lim_y, lim_x = max(1, int(h * max_trim_frac)), max(1, int(w * max_trim_frac))

    def is_gutter(line):
        return line.mean() >= 225 and line.std() < 12

    def run(lines, lim):
        return next((i for i in range(lim) if not is_gutter(lines(i))), lim)

    top = run(lambda i: a[i], lim_y) if inner["top"] else 0
    bottom = run(lambda i: a[h - 1 - i], lim_y) if inner["bottom"] else 0
    left = run(lambda i: a[:, i], lim_x) if inner["left"] else 0
    right = run(lambda i: a[:, w - 1 - i], lim_x) if inner["right"] else 0
    return cell.crop((left, top, w - right, h - bottom))


NODES = {"content": node_content, "facts": node_facts, "synopsis": node_synopsis, "prompt": node_prompt,
         "cover": node_cover, "split": node_split, "export": node_export}


def load_ctx(gen, stage: str, ctx: dict):
    """Fill ctx from a stage's files (used when the stage was reused from a parent generation)."""
    info = gen.meta["stages"][stage]
    files = {f.split("__")[-1]: gen.dir / f for f in info["files"]}
    if stage == "facts":
        ctx["facts"] = files["facts.txt"].read_text(encoding="utf-8")
    elif stage == "synopsis":
        ctx["synopsis"] = files["synopsis.txt"].read_text(encoding="utf-8")
    elif stage == "prompt":
        ctx["gemini_input"] = files["gemini-input.txt"].read_text(encoding="utf-8")
        ctx["image_prompt"] = files["prompt.txt"].read_text(encoding="utf-8")
    elif stage == "cover":
        ctx["grid"] = next(p for k, p in files.items() if k.startswith("grid.")).read_bytes()
    elif stage == "split":
        ctx["covers"] = [files[k].read_bytes() for k in sorted(files) if k.startswith("cover-")]


# ---------------------------------------------------------------- footer helpers (Gita)

def normalize_footer(text: str, pillar: str) -> str:
    text = re.sub(r"(?i)practical gita", "Practical Gita", text)
    return re.sub(r"(?i)" + re.escape(pillar), pillar, text)


def footer_score(text: str, pillar: str) -> int:
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    if min(prose.count("Practical Gita"), prose.count(pillar)) >= 1:
        return 4
    return min(text.count("Practical Gita"), text.count(pillar))


def _ext(mime: str) -> str:
    return {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
