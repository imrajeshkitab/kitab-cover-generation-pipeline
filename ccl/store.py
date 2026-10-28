"""Output layout, file naming and the generation index.

Every file is named so it identifies itself:

  <type>__<slug>__<source_id>__<lang>__<gen_id>__<stage>.<ext>
  summary__chatter-the-voice-in-our-head__48b88d565dd40b745de573db__en__20261028T121530Z-7f3a__prompt.txt

and lives in   outputs/<type>/<source_id>__<slug>/<lang>/<gen_id>/

gen_id = UTC timestamp + 4 random hex chars (unique even for parallel runs). One gen_id covers one pass of
the pipeline for one item; re-running from a later stage creates a new gen_id whose meta points to its parent.
"""
import json
import re
import secrets
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config

_lock = threading.Lock()


def new_gen_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)


def slugify(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n].rstrip("-") or "untitled"


def safe_id(source_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", source_id)


class Generation:
    def __init__(self, item, recipe: dict, parent_gen_id: str | None = None, gen_id: str | None = None):
        self.item = item
        self.gen_id = gen_id or new_gen_id()
        self.slug = slugify(item.title)
        sid = safe_id(item.source_id)
        self.stem = f"{item.type}__{self.slug}__{sid}__{item.lang}__{self.gen_id}"
        self.dir = config.OUTPUTS_DIR / item.type / f"{sid}__{self.slug}" / item.lang / self.gen_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta = {
            "gen_id": self.gen_id, "type": item.type, "source_id": item.source_id, "lang": item.lang,
            "alias": item.alias, "title": item.title, "recipe": recipe, "parent_gen_id": parent_gen_id,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "running", "stages": {}, "error": None,
        }
        self.save()

    # files
    def path(self, stage: str, ext: str) -> Path:
        return self.dir / f"{self.stem}__{stage}.{ext}"

    def write_text(self, stage: str, text: str, ext: str = "txt") -> Path:
        p = self.path(stage, ext)
        p.write_text(text, encoding="utf-8")
        return p

    def write_json(self, stage: str, data) -> Path:
        return self.write_text(stage, json.dumps(data, indent=2, ensure_ascii=False), "json")

    def write_bytes(self, stage: str, data: bytes, ext: str) -> Path:
        p = self.path(stage, ext)
        p.write_bytes(data)
        return p

    def record(self, stage: str, files: list[Path], **info):
        self.meta["stages"][stage] = {"files": [f.name for f in files], **info}
        self.save()

    def adopt(self, parent_dir: Path, parent_gen_id: str, stage: str, info: dict):
        """Copy a stage's files from a parent generation, renamed to this gen_id."""
        new_files = []
        for name in info.get("files", []):
            src = parent_dir / name
            dst = self.dir / name.replace(parent_gen_id, self.gen_id)
            shutil.copy2(src, dst)
            new_files.append(dst)
        self.meta["stages"][stage] = {**info, "files": [f.name for f in new_files], "reused_from": parent_gen_id}
        self.save()

    def finish(self, status: str, error: str | None = None):
        self.meta["status"] = status
        self.meta["error"] = error
        self.save()

    def save(self):
        (self.dir / f"{self.stem}__meta.json").write_text(
            json.dumps(self.meta, indent=2, ensure_ascii=False), encoding="utf-8")
        index_upsert(self)


# ---------------------------------------------------------------- index

_SCHEMA = """create table if not exists generations (
  gen_id text primary key, type text, source_id text, lang text, alias text, title text,
  recipe text, recipe_overrides text, parent_gen_id text, created_at text, status text,
  stages text, folder text, error text)"""


def _conn():
    config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.INDEX_DB, timeout=30)
    c.execute(_SCHEMA)
    return c


def index_upsert(gen: Generation):
    m = gen.meta
    row = (m["gen_id"], m["type"], m["source_id"], m["lang"], m["alias"], m["title"], m["recipe"]["id"],
           json.dumps(m["recipe"].get("overrides", [])), m["parent_gen_id"], m["created_at"], m["status"],
           ",".join(m["stages"]), str(gen.dir.relative_to(config.OUTPUTS_DIR)), m["error"])
    with _lock, _conn() as c:
        c.execute("insert or replace into generations values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)


def index_query(type_=None, source_id=None, lang=None, limit=50) -> list[dict]:
    sql, args = "select * from generations where 1=1", []
    for col, val in (("type", type_), ("source_id", source_id), ("lang", lang)):
        if val:
            sql += f" and {col} = ?"
            args.append(val)
    sql += " order by created_at desc, gen_id desc limit ?"
    args.append(limit)
    with _conn() as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(sql, args)]


def find_parent(type_: str, source_id: str, lang: str, gen_ref: str, needed_stage: str) -> tuple[Path, dict]:
    """Resolve --from (a gen_id or 'latest') to (folder, meta) of a generation that has needed_stage."""
    rows = index_query(type_, source_id, lang, limit=500)
    if gen_ref != "latest":
        rows = [r for r in rows if r["gen_id"] == gen_ref]
    for r in rows:
        folder = config.OUTPUTS_DIR / r["folder"]
        meta_file = next(folder.glob("*__meta.json"), None)
        if not meta_file:
            continue
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if needed_stage in meta["stages"]:
            return folder, meta
    raise SystemExit(f"no earlier generation of {type_} {source_id} ({lang}) with stage '{needed_stage}' "
                     f"(--from {gen_ref})")
