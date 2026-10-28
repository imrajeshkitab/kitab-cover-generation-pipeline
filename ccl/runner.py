"""Runs the node chain for a batch of items, in parallel, and logs the batch."""
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import config, models, sources, store
from .nodes import NODES, STAGES, load_ctx
from .sources import Item


def run(content_type: str, ids: list[str], recipe: dict, lang="en", start_at="content", until="export",
        from_ref: str | None = None, workers: int = 4) -> list[dict]:
    if STAGES.index(start_at) > 0 and not from_ref:
        raise SystemExit(f"--start-at {start_at} needs --from (a gen_id or 'latest')")
    if STAGES.index(until) < STAGES.index(start_at):
        raise SystemExit("--until is before --start-at")
    items = sources.FETCH[content_type](ids, lang)

    uses_images = STAGES.index(until) >= STAGES.index("cover")
    credits_before = models.elevenlabs_credits_used() if uses_images else None
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def one(item: Item) -> dict:
        parent = None
        if from_ref:
            needed = STAGES[STAGES.index(start_at) - 1]
            folder, meta = store.find_parent(item.type, item.source_id, item.lang, from_ref, needed)
            parent = (folder, meta)
            snap = json.loads(next(folder.glob("*__content.json")).read_text(encoding="utf-8"))
            item = Item.from_dict({k: snap[k] for k in Item.__dataclass_fields__})   # exact text of the parent
        gen = store.Generation(item, recipe, parent_gen_id=parent[1]["gen_id"] if parent else None)
        ctx = {}
        label = item.alias or item.source_id
        try:
            for stage in STAGES[:STAGES.index(until) + 1]:
                if STAGES.index(stage) < STAGES.index(start_at):
                    info = parent[1]["stages"].get(stage)
                    if info:
                        gen.adopt(parent[0], parent[1]["gen_id"], stage, info)
                        load_ctx(gen, stage, ctx)
                    continue
                NODES[stage](gen, item, recipe, ctx)
                print(f"{label} [{gen.gen_id}] {stage} ok", flush=True)
            gen.finish("done" if until == "export" else f"done:{until}")
        except Exception as e:
            gen.finish("failed", f"{type(e).__name__}: {e}")
            print(f"{label} [{gen.gen_id}] FAILED: {e}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
        return {"source_id": item.source_id, "alias": item.alias, "title": item.title, "gen_id": gen.gen_id,
                "status": gen.meta["status"], "folder": str(gen.dir)}

    with ThreadPoolExecutor(max(1, workers)) as ex:
        results = list(ex.map(one, items))

    credits_after = models.elevenlabs_credits_used() if uses_images else None
    batch = {"started": started, "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "type": content_type, "lang": lang, "recipe": recipe["id"], "overrides": recipe.get("overrides", []),
             "start_at": start_at, "until": until, "from": from_ref, "results": results,
             "elevenlabs_credits": (credits_after - credits_before)
             if credits_before is not None and credits_after is not None else None}
    config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    with (config.OUTPUTS_DIR / "batches.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(batch, ensure_ascii=False) + "\n")
    return results
