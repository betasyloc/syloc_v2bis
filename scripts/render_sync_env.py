#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Push selected env keys from local .env to Render.

By default, only non-sensitive routing keys are synced.
Use --include-stripe to also sync Stripe keys.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.render.com/v1"

# Keys merged from .env by default (never DATABASE_URL / Render-generated secrets here).
SYNC_KEYS = (
    "RENDER_EXTERNAL_URL",
    "CSRF_TRUSTED_ORIGINS",
)

SYNC_KEYS_STRIPE = (
    "STRIPE_SECRET_KEY",
    "STRIPE_PUBLISHABLE_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "STRIPE_PRICE_ID_BASE",
    "STRIPE_PRICE_ID_BASE_ANNUAL",
    "STRIPE_PRICE_ID_PREMIUM",
    "STRIPE_PRICE_ID_PREMIUM_ANNUAL",
)

SYNC_KEYS_EMAIL = (
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_USE_SSL",
    "EMAIL_USE_TLS",
    "EMAIL_HOST_USER",
    "EMAIL_HOST_PASSWORD",
    "DEFAULT_FROM_EMAIL",
)


def load_dotenv(path: Path) -> dict[str, str]:
    """Minimal KEY=VAL per line (no multiline values)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1].replace("\\n", "\n")
        elif val.startswith("'") and val.endswith("'"):
            val = val[1:-1]
        out[key] = val
    return out


def api_request(method: str, url: str, token: str, data: object | None = None) -> object:
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if body else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code} {e.reason}\n{err}") from e


def fetch_all_env_vars(service_id: str, token: str) -> dict[str, str]:
    merged: dict[str, str] = {}
    cursor: str | None = None
    while True:
        q = f"{API}/services/{service_id}/env-vars?limit=100"
        if cursor:
            q += f"&cursor={urllib.parse.quote(cursor, safe='')}"
        rows = api_request("GET", q, token)
        if not isinstance(rows, list):
            raise SystemExit(f"Unexpected API response (expected list): {rows!r}")
        for row in rows:
            ev = row.get("envVar") or {}
            k = (ev.get("key") or "").strip()
            v = ev.get("value")
            if k:
                merged[k] = v if v is not None else ""
        if not rows:
            break
        next_cursor = rows[-1].get("cursor")
        if not next_cursor:
            break
        cursor = next_cursor
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge selected keys from local .env into Render service env (Stripe, URLs).",
    )
    parser.add_argument(
        "--service-id",
        required=True,
        help="Render service ID (e.g. srv_...), from Dashboard - Settings.",
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=Path(__file__).resolve().parent.parent / ".env",
        help="Path to .env file (default: project root).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned changes only; no API PUT.",
    )
    parser.add_argument(
        "--include-stripe",
        action="store_true",
        help="Also sync Stripe keys from .env (disabled by default).",
    )
    parser.add_argument(
        "--include-email",
        action="store_true",
        help="Also sync SMTP email keys from .env (disabled by default).",
    )
    args = parser.parse_args()

    token = (os.environ.get("RENDER_API_KEY") or "").strip()
    if not token:
        sys.exit("Set RENDER_API_KEY (Render API key, rnd_...).")

    local = load_dotenv(args.dotenv)
    remote = fetch_all_env_vars(args.service_id, token)

    updates: dict[str, str] = dict(remote)
    changed: list[tuple[str, str, str]] = []
    keys_to_sync = (
        SYNC_KEYS
        + (SYNC_KEYS_STRIPE if args.include_stripe else ())
        + (SYNC_KEYS_EMAIL if args.include_email else ())
    )
    for key in keys_to_sync:
        if key not in local:
            continue
        new_val = (local[key] or "").strip()
        if not new_val:
            continue
        old_val = remote.get(key, "")
        if new_val != old_val:
            changed.append((key, old_val, new_val))
            updates[key] = new_val

    payload = [{"key": k, "value": v} for k, v in sorted(updates.items())]

    if not changed:
        print("No updates: selected keys missing or same values in .env.")
        return

    print("Planned changes:")
    for key, old, new in changed:
        old_show = "(empty)" if not old else (f"{old[:6]}..." if len(old) > 24 else old)
        new_show = f"{new[:6]}..." if len(new) > 24 else new
        print(f"  {key}: {old_show} -> {new_show}")

    if args.dry_run:
        print("\n--dry-run: no request sent to Render.")
        return

    api_request("PUT", f"{API}/services/{args.service_id}/env-vars", token, payload)
    print(f"\nOK: {len(payload)} env var(s) saved on Render. A deploy may start automatically.")


if __name__ == "__main__":
    main()
