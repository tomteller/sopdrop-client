#!/usr/bin/env python3
"""
Migrate a NAS-hosted Sopdrop team library to an on-prem sopdrop-server.

Reads the SQLite library at <NAS>/library/library.db and uploads each active
asset to the target server via /api/v1/assets/upload. Idempotent: skips
assets the target user already has by name.

Usage:
    export SOPDROP_TOKEN=sdrop_xxxxxxxxxxxx...
    python3 scripts/migrate-nas-to-server.py \\
        --nas /Volumes/team/library \\
        --server http://sopdrop.lan:4800 \\
        --visibility unlisted

Run with --dry-run first to see what would be uploaded.

Use --preserve-authorship to carry over the original `created_by`
(Windows OS username) and `created_at` from the NAS DB. The token user
must have admin or owner role on the target server (the override is
guarded server-side). Missing user accounts are auto-created with the
same shape as trust-LAN auto-create (`<name>@lan.local`, no password).
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

# Phrases the server uses for "this user already has an asset by this
# slug". We match prefix-only because the server formats it as
# "<You|<username>> already has an asset with this name".
ALREADY_EXISTS_FRAGMENT = "already has an asset with this name"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--nas", required=True, type=Path,
                   help="Path to the NAS team library root (the dir that contains library/library.db)")
    p.add_argument("--server", required=True,
                   help="On-prem server URL, e.g. http://sopdrop.lan:4800")
    p.add_argument("--token", default=os.environ.get("SOPDROP_TOKEN"),
                   help="API token (or set SOPDROP_TOKEN env var). With --preserve-authorship the token must belong to an admin or owner.")
    p.add_argument("--visibility", default="unlisted", choices=VALID_VISIBILITIES,
                   help="Visibility for migrated assets (default: unlisted)")
    p.add_argument("--dry-run", action="store_true",
                   help="List what would be migrated, upload nothing")
    p.add_argument("--limit", type=int, default=0,
                   help="Stop after N successful uploads (0 = no limit)")
    p.add_argument("--include-deleted", action="store_true",
                   help="Also migrate soft-deleted (trashed) assets")
    p.add_argument("--preserve-authorship", action="store_true",
                   help="Carry created_by (NAS OS username) and created_at from the source DB. "
                        "Missing user accounts are auto-created on the server. Requires an "
                        "admin/owner token on the target server.")
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


def fetch_existing_asset_slugs(server: str, token: str, username: str) -> set[str]:
    """Return the set of asset slugs already owned by `username` on the server.

    Used for idempotency: skip uploads we already have. Public-only —
    private/unlisted assets aren't returned, so a re-run after a partial
    private upload may attempt the upload again; the server will reject
    the duplicate with the standard "already has an asset" error and
    we'll mark it skipped.
    """
    existing: set[str] = set()
    page = 1
    while True:
        try:
            r = requests.get(f"{server}/api/v1/users/{username}/assets",
                             params={"page": page, "limit": 100},
                             headers={"Authorization": f"Bearer {token}"}, timeout=30)
        except requests.RequestException:
            return existing
        if r.status_code == 404:
            # User has no public profile yet (auto-created on first
            # upload). Treat as empty.
            return existing
        if r.status_code >= 400:
            return existing
        body = r.json()
        items = body.get("assets") or body.get("items") or (body if isinstance(body, list) else [])
        if not items:
            break
        for a in items:
            slug = a.get("slug") or a.get("name")
            if slug:
                # Slug from /users/:username/assets is just the asset
                # part (no owner prefix).
                existing.add(slug.lower())
        if len(items) < 100:
            break
        page += 1
    return existing


def fetch_token_user(server: str, token: str) -> dict:
    """Return the token's user dict (raises on failure)."""
    me = requests.get(f"{server}/api/v1/auth/me",
                      headers={"Authorization": f"Bearer {token}"}, timeout=15)
    me.raise_for_status()
    body = me.json()
    user = body.get("user") if isinstance(body, dict) and "user" in body else body
    if not user or not user.get("username"):
        sys.exit(f"could not determine current user from /auth/me: {me.text}")
    return user


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


def sanitize_username(raw: str | None) -> str | None:
    """Match server-side sanitizeLanUsername: lowercase, [a-z0-9._-], 2-32 chars."""
    if not raw:
        return None
    out = []
    for ch in raw.lower().strip():
        if ch.isalnum() or ch in "._-":
            out.append(ch)
    cleaned = "".join(out)[:32]
    return cleaned if len(cleaned) >= 2 else None


def load_assets(db_path: Path, include_deleted: bool) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    rows = conn.execute(f"""
        SELECT id, name, description, context, asset_type, file_path, thumbnail_path,
               tags, houdini_version, hda_type_name, metadata,
               created_by, created_at
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
               thumbs_dir: Path, visibility: str,
               as_user: str | None, created_at: str | None) -> tuple[bool, str, bool]:
    """Upload one asset.

    Returns (ok, info, already_exists). already_exists=True means the
    server rejected it as a duplicate of the same owner+slug — count as
    skipped, not failed.
    """
    file_name = asset.get("file_path")
    if not file_name:
        return False, "no file_path in DB row", False
    file_path = assets_dir / file_name
    if not file_path.is_file():
        return False, f"file missing on NAS: {file_path}", False

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
    if as_user:
        fields["asUser"] = as_user
    if created_at:
        fields["createdAt"] = created_at

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
        text = r.text[:300]
        if r.status_code == 400 and ALREADY_EXISTS_FRAGMENT in text:
            return False, "duplicate (server reports already exists)", True
        return False, f"HTTP {r.status_code}: {text}", False
    body = r.json()
    return True, body.get("asset", {}).get("slug") or body.get("slug") or "ok", False


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
    if args.preserve_authorship:
        print(f"Authorship: PRESERVE (created_by + created_at from source DB)")
    print()

    rows = load_assets(db_path, args.include_deleted)
    print(f"Found {len(rows)} active assets on NAS")

    token_user = None
    token_username = None
    if not args.dry_run:
        token_user = fetch_token_user(server, args.token)
        token_username = token_user["username"]
        print(f"  authenticated as '{token_username}' (role: {token_user.get('role', 'user')})")
        if args.preserve_authorship:
            role = token_user.get("role")
            is_admin = token_user.get("is_admin") or token_user.get("isAdmin")
            if not is_admin and role not in ("admin", "owner"):
                sys.exit(
                    "error: --preserve-authorship requires an admin or owner token. "
                    f"Token user '{token_username}' has role '{role}'. Promote with:\n"
                    f"  UPDATE users SET role='admin', is_admin=true WHERE username='{token_username}';"
                )

    # Idempotency: per-owner cache of existing slugs. Keyed by username
    # so re-runs don't double-upload the same (owner, slug). Lazily
    # populated as we encounter each owner.
    existing_per_user: dict[str, set[str]] = {}

    def existing_for(username: str) -> set[str]:
        if username not in existing_per_user:
            if args.dry_run:
                existing_per_user[username] = set()
            else:
                existing_per_user[username] = fetch_existing_asset_slugs(
                    server, args.token, username
                )
        return existing_per_user[username]

    uploaded = skipped = failed = 0
    failures: list[tuple[str, str]] = []

    for i, asset in enumerate(rows, 1):
        name = asset["name"]
        slug = slugify(name)
        prefix = f"[{i}/{len(rows)}] {name}"

        # Resolve effective owner and timestamp for this asset.
        as_user = None
        created_at = None
        owner_for_check = token_username or "self"
        if args.preserve_authorship:
            cleaned = sanitize_username(asset.get("created_by"))
            if cleaned:
                as_user = cleaned
                owner_for_check = cleaned
            else:
                # Fall back to token user when source row has no
                # usable created_by — better than dropping the asset.
                print(f"{prefix} — created_by '{asset.get('created_by')}' "
                      f"unusable, falling back to token user")
            ca = asset.get("created_at")
            if ca:
                created_at = ca

        if owner_for_check and slug.lower() in existing_for(owner_for_check):
            print(f"{prefix} — already on server for {owner_for_check}, skip")
            skipped += 1
            continue

        if args.dry_run:
            file_name = asset.get("file_path") or "?"
            file_path = assets_dir / file_name
            present = "ok" if file_path.is_file() else "MISSING"
            extra = ""
            if as_user or created_at:
                extra = f" [as={as_user or token_username}, at={created_at or 'now'}]"
            print(f"{prefix} — would upload ({file_name}, file: {present}){extra}")
            uploaded += 1
            continue

        try:
            ok, info, already = upload_one(
                server, args.token, asset, assets_dir, thumbs_dir,
                args.visibility, as_user, created_at,
            )
        except requests.RequestException as e:
            ok, info, already = False, f"network: {e}", False

        if ok:
            uploaded += 1
            existing_for(owner_for_check).add(slug.lower())
            attribution = f" as {as_user}" if as_user else ""
            print(f"{prefix} — uploaded ({info}){attribution}")
            if args.limit and uploaded >= args.limit:
                print(f"\n--limit {args.limit} reached, stopping")
                break
            time.sleep(0.1)  # gentle pacing
        elif already:
            skipped += 1
            existing_for(owner_for_check).add(slug.lower())
            print(f"{prefix} — already exists for {owner_for_check}, skip")
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
