"""Content sources: fetch items from App Prod and normalise them into Item objects.

source_id is the language-independent id of the content:
  summary       summaries.source_id      (shared by the en/hi rows)
  bite          bytes.source_id          (shared by the en/hi rows)
  journey       journeys.id
  gita_insight  journey_steps.id         (steps exist only in English today)
"""
import csv
import re
from dataclasses import asdict, dataclass, field

from . import config, db


@dataclass
class Item:
    type: str
    source_id: str
    lang: str
    title: str                       # title used on the cover
    body: str                        # text the synopsis is made from
    fields: dict = field(default_factory=dict)    # values for the facts block
    db_ref: dict = field(default_factory=dict)    # where the content came from (table, row ids)
    alias: str = ""                  # short human label, e.g. K1

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


def short_title(title: str) -> str:
    """The title without its subtitle: "Chatter: The Voice in Our Head ..." -> "Chatter"."""
    return (title or "").split(":")[0].strip()


# ---------------------------------------------------------------- summaries

def _summaries(where: str, lang: str) -> list[dict]:
    return db.query(
        "select id, source_id, title, author, category, tagline, language, "
        "(select count(*) from summary_chapters c where c.summary_id = s.id) as chapters "
        f"from summaries s where s.language = {db.lit(lang)} and s.published and {where} order by s.title")


def list_summary(lang="en", search=None, limit=50):
    where = f"s.title ilike {db.lit('%' + search + '%')}" if search else "true"
    return [{"source_id": r["source_id"], "title": r["title"], "author": r["author"],
             "category": r["category"], "chapters": r["chapters"]} for r in _summaries(where, lang)[:limit]]


def fetch_summary(ids, lang="en") -> list[Item]:
    rows = {r["source_id"]: r for r in _summaries(f"s.source_id in ({db.lits(ids)})", lang)}
    _require(ids, rows, "summary", lang)
    chapters = db.query(
        "select summary_id, chapter, title, content from summary_chapters "
        f"where summary_id in ({db.lits(r['id'] for r in rows.values())}) "
        "order by summary_id, nullif(regexp_replace(chapter, '\\D', '', 'g'), '')::int nulls last, chapter")
    items = []
    for sid in ids:
        r = rows[sid]
        ch = [c for c in chapters if c["summary_id"] == r["id"]]
        body = "\n\n".join(f"{c['title']}\n{(c['content'] or '').strip()}" for c in ch)
        items.append(Item(
            type="summary", source_id=sid, lang=lang, title=r["title"], body=body,
            fields={"title": r["title"], "short_title": short_title(r["title"]), "author": r["author"] or "",
                    "tagline": r["tagline"] or "", "genre": r["category"] or ""},
            db_ref={"table": "summaries", "id": r["id"], "chapters": [c["chapter"] for c in ch]}))
    return items


# ---------------------------------------------------------------- bites

def list_bite(lang="en", search=None, limit=50):
    where = f"and title ilike {db.lit('%' + search + '%')}" if search else ""
    rows = db.query(f"select source_id, title, author, category from bytes where language = {db.lit(lang)} "
                    f"and published {where} order by title limit {int(limit)}")
    return rows


def fetch_bite(ids, lang="en") -> list[Item]:
    rows = {r["source_id"]: r for r in db.query(
        "select id, source_id, title, author, category, content from bytes "
        f"where language = {db.lit(lang)} and source_id in ({db.lits(ids)})")}
    _require(ids, rows, "bite", lang)
    return [Item(type="bite", source_id=s, lang=lang, title=rows[s]["title"], body=(rows[s]["content"] or "").strip(),
                 fields={"title": rows[s]["title"], "short_title": short_title(rows[s]["title"]),
                         "author": rows[s]["author"] or "", "genre": rows[s]["category"] or "", "tagline": ""},
                 db_ref={"table": "bytes", "id": rows[s]["id"]}) for s in ids]


# ---------------------------------------------------------------- journeys

def list_journey(lang="en", search=None, limit=50):
    where = f"and j.title ilike {db.lit('%' + search + '%')}" if search else ""
    return db.query("select j.id as source_id, t.title, j.category, t.steps from journeys j "
                    f"join journey_translations t on t.journey_id = j.id and t.language = {db.lit(lang)} "
                    f"where t.published {where} order by t.title limit {int(limit)}")


def fetch_journey(ids, lang="en") -> list[Item]:
    rows = {r["journey_id"]: r for r in db.query(
        "select t.id, t.journey_id, t.title, t.tagline, t.overview, j.category from journey_translations t "
        f"join journeys j on j.id = t.journey_id where t.language = {db.lit(lang)} and t.journey_id in ({db.lits(ids)})")}
    _require(ids, rows, "journey", lang)
    steps = db.query(f"select journey_id, section_number, section_title, step, title from journey_steps "
                     f"where language = {db.lit(lang)} and journey_id in ({db.lits(ids)}) "
                     "order by journey_id, section_number, step")
    items = []
    for jid in ids:
        r = rows[jid]
        outline, last = [], None
        for s in (s for s in steps if s["journey_id"] == jid):
            if s["section_title"] != last:
                outline.append(f"\n{s['section_title']}")
                last = s["section_title"]
            outline.append(f"- {s['title']}")
        body = ((r["overview"] or "").strip() + "\n" + "\n".join(outline)).strip()
        items.append(Item(type="journey", source_id=jid, lang=lang, title=r["title"], body=body,
                          fields={"title": r["title"], "tagline": r["tagline"] or "", "genre": r["category"] or "",
                                  "author": ""},
                          db_ref={"table": "journey_translations", "id": r["id"]}))
    return items


# ---------------------------------------------------------------- Gita insights

_PILLAR_LETTER = {"know": "K", "grow": "G", "free": "F"}


def _gita_steps(lang="en") -> list[dict]:
    rows = db.query("select id, section_title, step, title, content from journey_steps "
                    f"where journey_id = {db.lit(config.GITA_JOURNEY_ID)} and language = {db.lit(lang)} "
                    "and title ilike 'Key Insight%'")
    for r in rows:
        r["number"] = int(re.search(r"Key Insight (\d+)", r["title"]).group(1))
        pillar = r["section_title"].split(":")[0].strip()          # "Know Yourself"
        r["pillar"] = pillar[:1].upper() + pillar[1:].lower()      # "Know yourself"
        r["letter"] = _PILLAR_LETTER[pillar.split()[0].lower()]
        r["original_title"] = re.sub(r"^Key Insight \d+:\s*", "", r["title"]).strip()
    rows.sort(key=lambda r: r["number"])
    seen = {}
    for r in rows:                                                  # K1..K22, G1..G59, F1..F26
        seen[r["letter"]] = seen.get(r["letter"], 0) + 1
        r["alias"] = f"{r['letter']}{seen[r['letter']]}"
    return rows


def _gita_overrides() -> dict:
    p = config.OVERRIDES_DIR / "gita_insight.csv"
    if not p.exists():
        raise SystemExit(f"missing {p} (reworked titles, genre, audience); run scripts/migrate_gita_overrides.py")
    with p.open(encoding="utf-8") as f:
        return {r["source_id"]: r for r in csv.DictReader(f)}


def list_gita_insight(lang="en", search=None, limit=200):
    rows = _gita_steps(lang)
    if search:
        rows = [r for r in rows if search.lower() in r["title"].lower()]
    return [{"source_id": r["id"], "alias": r["alias"], "title": r["original_title"], "pillar": r["pillar"]}
            for r in rows[:limit]]


def fetch_gita_insight(ids, lang="en") -> list[Item]:
    rows = _gita_steps(lang)
    by_key = {r["id"]: r for r in rows} | {r["alias"].upper(): r for r in rows}
    wanted = [r["id"] for r in rows] if ids == ["all"] else ids
    ov = _gita_overrides()
    items = []
    for key in wanted:
        r = by_key.get(key) or by_key.get(key.upper())
        if not r:
            raise SystemExit(f"gita_insight not found: {key}")
        o = ov.get(r["id"])
        if not o:
            raise SystemExit(f"no overrides row for {r['alias']} ({r['id']}) in overrides/gita_insight.csv")
        title = o["reworked_title"].strip() or r["original_title"]
        items.append(Item(type="gita_insight", source_id=r["id"], lang=lang, title=title,
                          body=(r["content"] or "").strip(), alias=r["alias"],
                          fields={"title": title, "pillar": r["pillar"], "genre": o["genre"],
                                  "audience": o["target_audience"]},
                          db_ref={"table": "journey_steps", "id": r["id"], "insight": r["number"]}))
    return items


# ---------------------------------------------------------------- registry

FETCH = {"summary": fetch_summary, "bite": fetch_bite, "journey": fetch_journey, "gita_insight": fetch_gita_insight}
LIST = {"summary": list_summary, "bite": list_bite, "journey": list_journey, "gita_insight": list_gita_insight}


def _require(ids, rows, kind, lang):
    missing = [i for i in ids if i not in rows]
    if missing:
        raise SystemExit(f"{kind} not found in App Prod (lang={lang}): {', '.join(missing)}")
