"""Versioned prompt files: prompts/<kind>/<name>/vN.txt, referenced as "<kind>/<name>/vN".

Placeholders are written {{name}}. Prompt files are never edited once used; add a new version instead.
"""
import hashlib
import re

from . import config

_PH = re.compile(r"\{\{(\w+)\}\}")


def load(ref: str) -> str:
    p = config.PROMPTS_DIR / f"{ref}.txt"
    if not p.exists():
        raise SystemExit(f"prompt not found: {ref} ({p})")
    return p.read_text(encoding="utf-8").rstrip("\n")


def render(template: str, **values) -> str:
    missing = set(_PH.findall(template)) - values.keys()
    if missing:
        raise ValueError(f"missing values for placeholders: {sorted(missing)}")
    return _PH.sub(lambda m: str(values[m.group(1)]), template)   # single pass: inserted text is never re-processed


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
