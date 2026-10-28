"""Recipes: recipes/<content_type>/vN.toml pick the prompt versions and model settings for each node."""
import copy
import tomllib

from . import config


def load(content_type: str, version: str, overrides: list[str] | None = None) -> dict:
    p = config.RECIPES_DIR / content_type / f"{version}.toml"
    if not p.exists():
        raise SystemExit(f"recipe not found: {p}")
    recipe = tomllib.loads(p.read_text(encoding="utf-8"))
    recipe["id"] = f"{content_type}/{version}"
    for o in overrides or []:
        apply_override(recipe, o)
    if overrides:
        recipe["overrides"] = list(overrides)
    return recipe


def apply_override(recipe: dict, expr: str):
    """--set prompt.design=design/generic/v2  |  --set cover.resolution=2K  |  --set synopsis.max_chars=6000"""
    if "=" not in expr or "." not in expr.split("=", 1)[0]:
        raise SystemExit(f"bad --set value (want section.key=value): {expr}")
    path, raw = expr.split("=", 1)
    section, key = path.split(".", 1)
    recipe.setdefault(section, {})[key] = _coerce(raw)


def _coerce(raw: str):
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    for t in (int, float):
        try:
            return t(raw)
        except ValueError:
            pass
    return raw


def snapshot(recipe: dict) -> dict:
    return copy.deepcopy(recipe)
