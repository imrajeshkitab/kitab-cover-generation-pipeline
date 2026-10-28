"""Command line:

  python -m ccl items summary --search chatter          # browse content in App Prod
  python -m ccl run summary --ids 48b88d565dd40b745de573db,SUM-440 --until prompt
  python -m ccl run summary --ids SUM-440 --start-at cover --from latest
  python -m ccl run summary --ids SUM-440 --set prompt.design=design/generic/v2 --until prompt
  python -m ccl run gita_insight --ids K1,K2
  python -m ccl status --type summary
"""
import argparse
import json

from . import config, nodes, recipes, runner, sources, store


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ccl", description="Content cover pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("items", help="list content available in App Prod")
    a.add_argument("type", choices=list(sources.LIST))
    a.add_argument("--search")
    a.add_argument("--lang", default="en")
    a.add_argument("--limit", type=int, default=50)

    r = sub.add_parser("run", help="run the pipeline for some items")
    r.add_argument("type", choices=list(sources.FETCH))
    r.add_argument("--ids", required=True, help="comma-separated source ids (gita_insight also takes K1/G12/F5 or 'all')")
    r.add_argument("--recipe", default="v1", help="recipe version in recipes/<type>/ (default v1)")
    r.add_argument("--lang", default="en")
    r.add_argument("--start-at", default="content", choices=nodes.STAGES)
    r.add_argument("--until", default="export", choices=nodes.STAGES)
    r.add_argument("--from", dest="from_ref", help="reuse earlier stages from this gen_id, or 'latest'")
    r.add_argument("--set", dest="sets", action="append", default=[], help="override a recipe value: section.key=value")
    r.add_argument("--workers", type=int, default=4)

    s = sub.add_parser("status", help="list generations from the index")
    s.add_argument("--type")
    s.add_argument("--id", dest="source_id")
    s.add_argument("--lang")
    s.add_argument("--limit", type=int, default=30)

    t = sub.add_parser("trace", help="from any output file (or gen_id): book, version, prompts and settings used")
    t.add_argument("target")

    args = ap.parse_args(argv)
    config.load_env()

    if args.cmd == "items":
        for row in sources.LIST[args.type](lang=args.lang, search=args.search, limit=args.limit):
            print(" | ".join(str(v) for v in row.values()))
    elif args.cmd == "run":
        recipe = recipes.load(args.type, args.recipe, args.sets)
        ids = [i.strip() for i in args.ids.split(",") if i.strip()]
        results = runner.run(args.type, ids, recipe, args.lang, args.start_at, args.until, args.from_ref, args.workers)
        print(json.dumps(results, indent=2, ensure_ascii=False))
    elif args.cmd == "trace":
        trace(args.target)
    elif args.cmd == "status":
        for g in store.index_query(args.type, args.source_id, args.lang, args.limit):
            print(f"{g['gen_id']}  {g['type']}  {g['alias'] or g['source_id']}  {g['lang']}  {g['recipe']}  "
                  f"{g['status']}  [{g['stages']}]  {g['title'][:50]}")


def trace(target: str):
    import re
    from pathlib import Path
    m = re.search(r"\d{8}T\d{6}Z-[0-9a-f]{4}", Path(target).name)
    if not m:
        raise SystemExit("no gen_id found in that name")
    gen_id = m.group(0)
    hit = [g for g in store.index_query(limit=100000) if g["gen_id"] == gen_id]
    if not hit:
        raise SystemExit(f"gen_id {gen_id} not in the index")
    folder = config.OUTPUTS_DIR / hit[0]["folder"]
    meta = json.loads(next(folder.glob("*__meta.json")).read_text(encoding="utf-8"))
    variant = re.search(r"__cover-(\d)", Path(target).name)
    st = meta["stages"]
    show = lambda d, keys: ", ".join(f"{k}={d[k]}" for k in keys if d.get(k) is not None)
    print(f"book       {meta['title']}  ({meta['type']}, source_id {meta['source_id']}, {meta['lang']})")
    print(f"version    gen_id {gen_id}  created {meta['created_at']}  status {meta['status']}"
          + (f"  parent {meta['parent_gen_id']}" if meta.get("parent_gen_id") else ""))
    if variant:
        cell = ["top-left", "top-right", "bottom-left", "bottom-right"][int(variant.group(1)) - 1]
        print(f"variant    {variant.group(1)} of 4 ({cell} cell of the grid)")
    print(f"recipe     {meta['recipe']['id']}" + (f"  overrides {meta['recipe']['overrides']}" if meta['recipe'].get('overrides') else ""))
    for name in ("facts", "synopsis", "prompt", "cover", "split"):
        if name in st:
            keys = {"facts": ["block", "prompt", "template_sha"], "synopsis": ["mode", "prompt", "model", "chars"],
                    "prompt": ["design", "design_sha", "assembly", "assembly_sha", "model", "thinking", "attempts"],
                    "cover": ["model", "aspect_ratio", "resolution", "quality", "elevenlabs_generation_id"],
                    "split": ["mode", "model", "prompt"]}[name]
            reused = f"  (reused from {st[name]['reused_from']})" if st[name].get("reused_from") else ""
            print(f"{name:<10} {show(st[name], keys)}{reused}")
    files = {f.split("__")[-1]: folder / f for s_ in st.values() for f in s_.get("files", [])}
    print("files")
    for label, key in (("exact Gemini input (fixed prompts + book text)", "gemini-input.txt"),
                       ("text-to-image prompt (Gemini output)", "prompt.txt"), ("2x2 grid", "grid.png")):
        if key in files:
            print(f"  {label:<48} {files[key]}")
    print(f"  {'all metadata':<48} {next(folder.glob('*__meta.json'))}")


if __name__ == "__main__":
    main()
