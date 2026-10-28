"""Read-only access to App Prod through the Supabase Management API.

The database host is IPv6-only, so a direct Postgres connection often times out; the Management API
works over HTTPS. Auth uses SUPABASE_ACCESS_TOKEN, or the token saved by `supabase login`.
Every query is sent with read_only=true.
"""
import os
from pathlib import Path

import requests

from . import config

API = "https://api.supabase.com/v1/projects/{ref}/database/query"


def _token() -> str:
    t = os.environ.get("SUPABASE_ACCESS_TOKEN")
    if t:
        return t
    p = Path.home() / ".supabase" / "access-token"
    if p.exists():
        return p.read_text().strip()
    raise SystemExit("No Supabase access token: run `supabase login` or set SUPABASE_ACCESS_TOKEN")


def query(sql: str) -> list[dict]:
    r = requests.post(API.format(ref=config.APP_PROD_REF),
                      headers={"Authorization": f"Bearer {_token()}"},
                      json={"query": sql, "read_only": True}, timeout=120)
    if not r.ok:
        raise RuntimeError(f"App Prod query failed ({r.status_code}): {r.text[:300]}")
    return r.json()


def lit(v) -> str:
    """SQL string literal."""
    return "'" + str(v).replace("'", "''") + "'"


def lits(values) -> str:
    return ", ".join(lit(v) for v in values)
