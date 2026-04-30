#!/usr/bin/env python3
"""
Migrate a NAS-hosted Sopdrop team library to an on-prem sopdrop-server.

Reads the SQLite library at <NAS>/library/library.db and uploads each active
asset to the target server via /api/v1/assets/upload. Idempotent: skips
assets the authenticated user already has by name.

Usage:
    export SOPDROP_TOKEN=sdrop_xxxxxxxxxxxx...
    python3 scripts/migrate-nas-to-server.py \\
        --nas /Volumes/team/library \\
        --server http://sopdrop.lan:4800 \\
        --visibility unlisted

Run with --dry-run first to see what would be uploaded.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("requests is required: pip install requests")


VALID_VISIBILITIES = ("public", "unlisted", "private", "draft")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--nas", required=True, type=Path,
                   help="Path to the NAS team library root (the dir that contains library/library.db)")
    p.add_argument("--server", required=True,
                   help="On-prem server URL, e.g. http://sopdrop.lan:4800")
    p.add_argument("--token", default=os.environ.get("SOPDROP_TOKEN"),
                   help="API token (or set SOPDROP_TOKEN env var)")
    p.add_argument("--visibility", default="unlisted", choices=VALID_VISIBILITIES,
                   help="Visibility for migrated assets (default: unlisted)")
    p.add_argument("--dry-run", action="store_true",
                   help="List what would be migrated, upload nothing")
    p.add_argument("--limit", type=int, default=0,
                   help="Stop after N successful uploads (0 = no limit)")
    p.add_argument("--include-deleted", action="store_true",
                   help="Also migrate soft-deleted (trashed) assets")
    return p.parse_args()


def resolve_db_path(nas_root: Path) -> Path:
    candidates = [nas_root / "library" / "library.db", nas_root / "library.db"]
    for c in candidates:
        if c.is_file():
            return c
    sys.exit(f"library.db not found under {nas_root} (looked for {', '.join(str(c) for c in candidates)})")


def resolve_assets_dir(db_path: Path) -> Path:
    d = db_path.parent / "assets"
    if not d.is_dir():
        sys.exit(f"assets dir not found at {d}")
    return d


def resolve_thumbnails_dir(db_path: Path) -> Path:
    return db_path.parent / "thumbnails"


def fetch_existing_asset_names(server: str, token: str) -> set[str]:
    """Return the set of asset slugs the token's user already owns."""
    me = requests.get(f"{server}/api/v1/auth/me",
                      headers={"Authorization": f"Bearer {token}"}, timeout=15)
    me.raise_for_status()
    username = me.json().get("user", {}).get("username") or me.json().get("username")
    if not username:
        sys.exit(f"could not determine current user from /auth/me: {me.text}")

    existing: set[str] = set()
    page = 1
    while True:
        r = requests.get(f"{server}/api/v1/users/{username}/assets",
                         params={"page": page, "limit": 100},
                         headers={"Authorization": f"Bearer {token}"}, timeout=30)
        r.raise_for_status()
        body = r.json()
        items = body.get("assets") or body.get("items") or body if isinstance(body, list) else []
        if not items:
            break
        for a in items:
            slug = a.get("slug") or a.get("name")
            if slug:
                existing.add(slug.lower())
        if len(items) < 100:
            break
        page += 1
    print(f"  current user '{username}' has {len(existing)} existing assets")
    return existing


def slugify(name: str) -> str:
    out = []
    prev_dash = False
    for ch in name.lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def load_assets(db_path: Path, include_deleted: bool) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    rows = conn.execute(f"""
        SELECT id, name, description, context, asset_type, file_path, thumbnail_path,
               tags, houdini_version, hda_type_name, metadata
        FROM library_assets
        {where}
        ORDER BY created_at
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        if isinstance(v, list):
            return [str(t) for t in v if t]
    except (json.JSONDecodeError, TypeError):
        pass
    return [t.strip() for t in str(raw).split(",") if t.strip()]


def upload_one(server: str, token: str, asset: dict, assets_dir: Path,
               thumbs_dir: Path, visibility: str) -> tuple[bool, str]:
    file_name = asset.get("file_path")
    if not file_name:
        return False, "no file_path in DB row"
    file_path = assets_dir / file_name
    if not file_path.is_file():
        return False, f"file missing on NAS: {file_path}"

    fields = {
        "name": asset["name"],
        "description": asset.get("description") or "",
        "license": "MIT",
        "houdiniContext": (asset.get("context") or "sop").lower(),
        "tags": json.dumps(parse_tags(asset.get("tags"))),
        "visibility": visibility,
    }
    if asset.get("houdini_version"):
        fields["minHoudiniVersion"] = asset["houdini_version"]

    files = {"file": (file_name, open(file_path, "rb"))}
    thumb_name = asset.get("thumbnail_path")
    thumb_handle = None
    if thumb_name:
        thumb_path = thumbs_dir / thumb_name
        if thumb_path.is_file():
            thumb_handle = open(thumb_path, "rb")
            files["thumbnail"] = (thumb_name, thumb_handle)

    try:
        r = requests.post(f"{server}/api/v1/assets/upload",
                          headers={"Authorization": f"Bearer {token}"},
                          data=fields, files=files, timeout=120)
    finally:
        files["file"][1].close()
        if thumb_handle:
            thumb_handle.close()

    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}: {r.text[:300]}"
    body = r.json()
    return True, body.get("asset", {}).get("slug") or body.get("slug") or "ok"


def main() -> int:
    args = parse_args()
    if not args.dry_run and not args.token:
        sys.exit("--token or SOPDROP_TOKEN env var required (unless --dry-run)")

    server = args.server.rstrip("/")
    db_path = resolve_db_path(args.nas)
    assets_dir = resolve_assets_dir(db_path)
    thumbs_dir = resolve_thumbnails_dir(db_path)

    print(f"NAS:    {db_path}")
    print(f"Server: {server}")
    print(f"Mode:   {'DRY RUN' if args.dry_run else 'LIVE'}")
    print()

    rows = load_assets(db_path, args.include_deleted)
    print(f"Found {len(rows)} active assets on NAS")

    existing: set[str] = set()
    if not args.dry_run:
        existing = fetch_existing_asset_names(server, args.token)

    uploaded = skipped = failed = 0
    failures: list[tuple[str, str]] = []

    for i, asset in enumerate(rows, 1):
        name = asset["name"]
        slug = slugify(name)
        prefix = f"[{i}/{len(rows)}] {name}"

        if slug.lower() in existing:
            print(f"{prefix} — already on server, skip")
            skipped += 1
            continue

        if args.dry_run:
            file_name = asset.get("file_path") or "?"
            file_path = assets_dir / file_name
            present = "ok" if file_path.is_file() else "MISSING"
            print(f"{prefix} — would upload ({file_name}, file: {present})")
            uploaded += 1
            continue

        try:
            ok, info = upload_one(server, args.token, asset, assets_dir,
                                  thumbs_dir, args.visibility)
        except requests.RequestException as e:
            ok, info = False, f"network: {e}"

        if ok:
            uploaded += 1
            existing.add(slug.lower())
            print(f"{prefix} — uploaded ({info})")
            if args.limit and uploaded >= args.limit:
                print(f"\n--limit {args.limit} reached, stopping")
                break
            time.sleep(0.1)  # gentle pacing
        else:
            failed += 1
            failures.append((name, info))
            print(f"{prefix} — FAILED: {info}", file=sys.stderr)

    print()
    print(f"Summary: {uploaded} uploaded, {skipped} skipped, {failed} failed")
    if failures:
        print("\nFailures:")
        for n, why in failures:
            print(f"  - {n}: {why}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
