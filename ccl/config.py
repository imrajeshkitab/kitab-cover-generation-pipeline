"""Paths, environment and constants shared by the pipeline."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
RECIPES_DIR = ROOT / "recipes"
OVERRIDES_DIR = ROOT / "overrides"
OUTPUTS_DIR = Path(os.environ.get("CCL_OUTPUTS", ROOT / "outputs"))
INDEX_DB = OUTPUTS_DIR / "index.sqlite"

APP_PROD_REF = os.environ.get("APP_PROD_REF", "ziifsmeagbyyjsnmxwns")   # App Prod (read-only)
GITA_JOURNEY_ID = "defc4266-1eef-4756-b61c-01836d89b08b"                 # "Gita In A Day"


def load_env():
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            k = k.strip()
            if k.isidentifier():
                os.environ.setdefault(k, v.strip().strip("\"'"))


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"{name} is not set (add it to .env)")
    return v
