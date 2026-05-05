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
        --team frame48 \\
        --preserve-authorship

Run with --dry-run first to see what would be uploaded.

What it migrates:
  - Active library_assets rows (file + thumbnail + tags + metadata)
  - Collections → team user_folders (when --team is set; parents first,
    idempotent; uses POST /teams/:slug/library/collections)
  - Asset → folder membership (picks the primary/first collection; the
    server stores one folder_id per asset, so multi-collection membership
    is lossy by design)
  - README and license (if present in source metadata JSON)

What it does NOT migrate:
  - Asset version history (only the latest file is uploaded as the
    initial version on the server)
  - Per-user state: is_favorite, use_count, last_used_at, sync_status

Use --preserve-authorship to carry over the original `created_by`
(Windows OS username) and `created_at` from the NAS DB. The token user
must have admin or owner role on the target server (the override is
guarded server-side). Missing user accounts are auto-created with the
same shape as trust-LAN auto-create (`<name>@lan.local`, no password).

Use --team <slug> to associate uploaded assets with a team library so
they show up in the panel's team view. Without it, uploads land in the
authoring user's personal library and folders are not migrated.

Use --no-collections to skip folder migration (default is on when
--team is set).

Use --repair-thumbnails to re-upload thumbnails for assets that already
exist on the server. Useful for fixing migrations that ran before the
thumbnail-MIME validation fix landed and left version.thumbnail_url
NULL on the server.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import quote

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
    p.add_argument("--visibility", default=None, choices=VALID_VISIBILITIES,
                   help="Visibility for migrated assets. Default: 'public' when --team is set "
                        "(trust-LAN team libraries have no meaningful 'unlisted' semantics — "
                        "the LAN itself is the access boundary), 'unlisted' otherwise.")
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
    p.add_argument("--team", default=None,
                   help="Team slug to associate uploaded assets with. Required to make assets "
                        "show up in a team library — without it, uploads land in the user's "
                        "personal library and won't appear in the panel's team view.")
    p.add_argument("--repair-thumbnails", action="store_true",
                   help="When an asset already exists on the server (idempotent re-run), still "
                        "try to upload its thumbnail via the /thumbnail endpoint. Useful for "
                        "migrations that ran before the thumbnail-MIME fix landed.")
    p.add_argument("--repair-icons", action="store_true",
                   help="When an asset already exists on the server (idempotent re-run), PATCH "
                        "its Houdini icon (e.g. 'SOP_scatter') via PUT /assets/:slug. Useful for "
                        "migrations that ran before the server gained an icon column.")
    p.add_argument("--no-collections", action="store_true",
                   help="Skip migrating collections → team user_folders. By default, when --team "
                        "is set, the script copies each NAS collection (and its parent chain) into "
                        "the team's folder list and assigns each uploaded asset to its primary "
                        "collection's folder.")
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
               icon, tags, houdini_version, hda_type_name, hda_type_label, hda_version,
               hda_category, metadata, created_by, created_at
        FROM library_assets
        {where}
        ORDER BY created_at
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_collections(db_path: Path) -> list[dict]:
    """Return all collection rows from the NAS DB."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT id, name, description, color, icon, parent_id, sort_order
            FROM collections
            ORDER BY sort_order, name
        """).fetchall()
    except sqlite3.OperationalError:
        # Older NAS libraries may not have a collections table.
        rows = []
    conn.close()
    return [dict(r) for r in rows]


def load_collection_memberships(db_path: Path) -> dict[str, list[tuple[str, int]]]:
    """Return asset_id → ordered list of (collection_id, sort_order)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute("""
            SELECT asset_id, collection_id, sort_order
            FROM collection_assets
            ORDER BY sort_order, added_at
        """).fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    out: dict[str, list[tuple[str, int]]] = {}
    for asset_id, coll_id, sort_order in rows:
        out.setdefault(asset_id, []).append((coll_id, sort_order or 0))
    return out


def topo_sort_collections(collections: list[dict]) -> list[dict]:
    """Return collections ordered so that every parent comes before its children."""
    by_id = {c["id"]: c for c in collections}
    visited: set[str] = set()
    out: list[dict] = []

    def visit(cid: str, stack: set[str]):
        if cid in visited or cid not in by_id:
            return
        if cid in stack:
            # Cycle — skip to avoid infinite recursion. NAS DB shouldn't
            # have these but better safe than sorry.
            return
        stack.add(cid)
        parent = by_id[cid].get("parent_id")
        if parent:
            visit(parent, stack)
        stack.discard(cid)
        visited.add(cid)
        out.append(by_id[cid])

    for c in collections:
        visit(c["id"], set())
    return out


def fetch_team_folders(server: str, token: str, team_slug: str) -> list[dict]:
    """List existing team folders. Returns empty list on any error so
    the caller can fall through to create-on-demand."""
    try:
        r = requests.get(
            f"{server}/api/v1/teams/{quote(team_slug, safe='')}/library/collections",
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
    except requests.RequestException:
        return []
    if r.status_code >= 400:
        return []
    body = r.json()
    return body.get("collections") or []


def update_team_folder_parent(server: str, token: str, team_slug: str,
                              folder_id: str, parent_slug: str | None) -> bool:
    """PATCH a team folder's parent. parent_slug=None clears it (root).
    Returns True on success."""
    payload = {"parentSlug": parent_slug}
    try:
        r = requests.put(
            f"{server}/api/v1/teams/{quote(team_slug, safe='')}/library/collections/"
            f"{quote(folder_id, safe='')}",
            headers={"Authorization": f"Bearer {token}"},
            json=payload, timeout=30,
        )
    except requests.RequestException as e:
        print(f"  folder parent fix failed (network): {e}", file=sys.stderr)
        return False
    if r.status_code >= 400:
        print(f"  folder parent fix failed: HTTP {r.status_code}: {r.text[:200]}",
              file=sys.stderr)
        return False
    return True


def create_team_folder(server: str, token: str, team_slug: str,
                       name: str, description: str | None,
                       color: str | None, icon: str | None,
                       parent_slug: str | None) -> dict | None:
    """POST a new team folder. Returns the folder dict, or None on failure
    (already-exists is treated as a soft success — caller should re-fetch)."""
    payload = {"name": name}
    if description:
        payload["description"] = description
    if color:
        payload["color"] = color
    if icon:
        payload["icon"] = icon
    if parent_slug:
        payload["parentSlug"] = parent_slug
    try:
        r = requests.post(
            f"{server}/api/v1/teams/{quote(team_slug, safe='')}/library/collections",
            headers={"Authorization": f"Bearer {token}"},
            json=payload, timeout=30,
        )
    except requests.RequestException as e:
        print(f"  folder '{name}' create failed: {e}", file=sys.stderr)
        return None
    if r.status_code == 409 or (r.status_code == 400 and "already exists" in r.text):
        return None  # caller handles via re-fetch
    if r.status_code >= 400:
        print(f"  folder '{name}' create failed: HTTP {r.status_code}: {r.text[:200]}",
              file=sys.stderr)
        return None
    return r.json()


def migrate_collections_to_team_folders(
    server: str, token: str, team_slug: str,
    nas_collections: list[dict], dry_run: bool,
) -> tuple[dict[str, str], int, int, int]:
    """Create team folders for each NAS collection (parents first).

    Returns (mapping, created, reused, repaired). `mapping` is
    nas_collection_id → server folder slug. Existing folders matching by
    slugified name are reused; if their parent_id doesn't match what the
    NAS hierarchy says it should be (e.g. a prior migration created them
    flat) we PATCH the parent via the team-folder PUT endpoint.
    """
    if not nas_collections:
        return {}, 0, 0, 0

    # Index existing server folders by slug. Slug uses the same
    # normalization as the server-side slugify.
    existing = {} if dry_run else {f["slug"]: f for f in fetch_team_folders(server, token, team_slug)}

    nas_to_slug: dict[str, str] = {}
    nas_to_uuid: dict[str, str] = {}  # NAS coll id → server folder UUID (for parent compare)
    created = 0
    reused = 0
    repaired = 0
    ordered = topo_sort_collections(nas_collections)

    for coll in ordered:
        cid = coll["id"]
        name = (coll.get("name") or "").strip()
        if not name:
            continue
        target_slug = slugify(name)

        # Resolve parent slug + UUID if any. The server returns parentId
        # as the parent's UUID (`id` field), so we compare in UUID space.
        parent_slug = None
        expected_parent_uuid = None
        parent_id = coll.get("parent_id")
        if parent_id and parent_id in nas_to_slug:
            parent_slug = nas_to_slug[parent_id]
            expected_parent_uuid = nas_to_uuid.get(parent_id)

        if target_slug in existing:
            nas_to_slug[cid] = target_slug
            existing_folder = existing[target_slug]
            folder_uuid = existing_folder.get("id")
            if folder_uuid:
                nas_to_uuid[cid] = folder_uuid
            reused += 1
            # Hierarchy repair: prior run may have created this folder
            # as a root (or under the wrong parent). PATCH if mismatched.
            current_parent = existing_folder.get("parentId")  # UUID or None
            if not dry_run and current_parent != expected_parent_uuid:
                if folder_uuid and update_team_folder_parent(
                    server, token, team_slug, folder_uuid, parent_slug,
                ):
                    existing_folder["parentId"] = expected_parent_uuid
                    repaired += 1
                    where = f"under '{parent_slug}'" if parent_slug else "as a root"
                    print(f"  fixed parent of existing folder '{target_slug}' → {where}")
            continue

        if dry_run:
            nas_to_slug[cid] = target_slug
            created += 1
            continue

        new_folder = create_team_folder(
            server, token, team_slug,
            name=name,
            description=coll.get("description"),
            color=coll.get("color"),
            icon=coll.get("icon"),
            parent_slug=parent_slug,
        )
        if new_folder:
            nas_to_slug[cid] = new_folder.get("slug") or target_slug
            if new_folder.get("id"):
                nas_to_uuid[cid] = new_folder["id"]
            existing[nas_to_slug[cid]] = new_folder
            created += 1
        else:
            # Server may have raced us or rejected — re-fetch and try
            # to find by slug.
            for f in fetch_team_folders(server, token, team_slug):
                if f["slug"] == target_slug:
                    nas_to_slug[cid] = target_slug
                    if f.get("id"):
                        nas_to_uuid[cid] = f["id"]
                    existing[target_slug] = f
                    reused += 1
                    break

    return nas_to_slug, created, reused, repaired


def primary_folder_slug_for_asset(
    asset_id: str,
    memberships: dict[str, list[tuple[str, int]]],
    coll_to_folder: dict[str, str],
) -> str | None:
    """Pick the folder for an asset that lives in (potentially) multiple
    NAS collections. Server allows one folder_id per asset, so we have
    to choose: lowest sort_order, then first by membership order."""
    entries = memberships.get(asset_id) or []
    for coll_id, _sort in sorted(entries, key=lambda e: e[1]):
        slug = coll_to_folder.get(coll_id)
        if slug:
            return slug
    return None


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


def _extract_readme_and_license(metadata_raw: str | None) -> tuple[str | None, str | None, str | None]:
    """Parse the source `library_assets.metadata` JSON for fields we want
    to preserve on the server. Returns (readme, license, license_url).
    Returns Nones for any field that's absent or unparseable."""
    if not metadata_raw:
        return None, None, None
    try:
        meta = json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
    except (json.JSONDecodeError, TypeError):
        return None, None, None
    if not isinstance(meta, dict):
        return None, None, None
    readme = meta.get("readme") or meta.get("README") or None
    lic = meta.get("license") or None
    lic_url = meta.get("license_url") or meta.get("licenseUrl") or None
    if readme and not isinstance(readme, str):
        readme = None
    if lic and not isinstance(lic, str):
        lic = None
    if lic_url and not isinstance(lic_url, str):
        lic_url = None
    return readme, lic, lic_url


def upload_one(server: str, token: str, asset: dict, assets_dir: Path,
               thumbs_dir: Path, visibility: str,
               as_user: str | None, created_at: str | None,
               team_slug: str | None,
               folder_slug: str | None) -> tuple[bool, str, bool, bool]:
    """Upload one asset.

    Returns (ok, info, already_exists, thumbnail_uploaded).
    already_exists=True means the server rejected it as a duplicate of
    the same owner+slug — count as skipped, not failed.
    thumbnail_uploaded=False means either no thumb on NAS or the file
    didn't sniff as an image (the asset still uploads).
    """
    file_name = asset.get("file_path")
    if not file_name:
        return False, "no file_path in DB row", False, False
    file_path = assets_dir / file_name
    if not file_path.is_file():
        return False, f"file missing on NAS: {file_path}", False, False

    readme, lic, lic_url = _extract_readme_and_license(asset.get("metadata"))

    fields = {
        "name": asset["name"],
        "description": asset.get("description") or "",
        "license": lic or "MIT",
        "houdiniContext": (asset.get("context") or "sop").lower(),
        "tags": json.dumps(parse_tags(asset.get("tags"))),
        "visibility": visibility,
    }
    if readme:
        fields["readme"] = readme
    if lic_url:
        fields["licenseUrl"] = lic_url
    if asset.get("houdini_version"):
        fields["minHoudiniVersion"] = asset["houdini_version"]
    if as_user:
        fields["asUser"] = as_user
    if created_at:
        fields["createdAt"] = created_at
    if team_slug:
        # Server resolves teamSlug → team_id. Without it, the asset
        # lands in the user's personal library and the team view in
        # the panel won't see it (Bug 13).
        fields["teamSlug"] = team_slug
    if folder_slug:
        # Server resolves folderSlug to a user_folder.id, scoped to
        # team_id when teamSlug is also set. Without this, the asset
        # is unfiled in the team library.
        fields["folderSlug"] = folder_slug
    icon = asset.get("icon")
    if icon:
        # NAS stores Houdini icon names (e.g. 'SOP_scatter'). Server
        # column is VARCHAR(64); send as-is, server truncates if needed.
        fields["icon"] = str(icon)[:64]

    files = {"file": (file_name, open(file_path, "rb"))}
    thumb_name = asset.get("thumbnail_path")
    thumb_handle = None
    thumb_full_path = None
    thumb_uploaded = False
    if thumb_name:
        thumb_full_path = thumbs_dir / thumb_name
        if thumb_full_path.is_file():
            # Server requires Content-Type to start with "image/". Sniff
            # the file's magic bytes first; fall back to extension; last
            # resort, send as image/jpeg if the extension hints at an
            # image. If we still can't classify, drop the thumbnail
            # rather than fail the whole asset.
            thumb_mime = _detect_image_mime(thumb_full_path)
            if thumb_mime:
                thumb_handle = open(thumb_full_path, "rb")
                files["thumbnail"] = (thumb_name, thumb_handle, thumb_mime)
                thumb_uploaded = True
            else:
                print(f"  thumbnail '{thumb_name}' doesn't look like an image, skipping",
                      file=sys.stderr)
                thumb_full_path = None  # don't try to send on retry either

    # Per-route limiters (uploadLimiter etc.) are bypassed for admin
    # tokens server-side, but the global IP-based generalLimiter runs
    # pre-auth and still applies. Honor 429 with exponential backoff
    # rather than failing the asset.
    try:
        r = _post_with_429_backoff(
            f"{server}/api/v1/assets/upload",
            headers={"Authorization": f"Bearer {token}"},
            data=fields, files=files, file_path=file_path, file_name=file_name,
            thumb_path=thumb_full_path,
        )
    finally:
        files["file"][1].close()
        if thumb_handle:
            thumb_handle.close()

    if r.status_code >= 400:
        text = r.text[:300]
        if r.status_code == 400 and ALREADY_EXISTS_FRAGMENT in text:
            return False, "duplicate (server reports already exists)", True, False
        return False, f"HTTP {r.status_code}: {text}", False, False
    body = r.json()
    slug_out = body.get("asset", {}).get("slug") or body.get("slug") or "ok"
    return True, slug_out, False, thumb_uploaded


def _detect_image_mime(path: Path) -> str | None:
    """Return an image/* MIME type for `path`, or None if it doesn't
    look like an image at all.

    Sniffs the file's magic bytes first (the only fully reliable
    signal — NAS thumbnails often have no extension or a misleading
    one). Falls back to the standard mimetypes module on the
    filename if the bytes don't match a known image header.
    """
    import mimetypes
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"BM"):
        return "image/bmp"
    if head.startswith(b"<?xml") or head.lstrip().startswith(b"<svg"):
        return "image/svg+xml"
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed and guessed.startswith("image/"):
        return guessed
    # Last-resort: trust the filename extension for common image types
    # even when the file's first 16 bytes don't match a known signature
    # (some legacy NAS thumbnails have leading metadata or are just
    # truncated). The server validates Content-Type only, so a best-
    # effort label is better than dropping the thumbnail.
    ext = path.suffix.lower()
    ext_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
        ".svg": "image/svg+xml",
    }
    if ext in ext_map:
        return ext_map[ext]
    return None


def _post_with_429_backoff(url, *, headers, data, files, file_path, file_name,
                           thumb_path, max_retries: int = 5):
    """POST that retries on 429 with exponential backoff.

    requests' file handles are consumed by the first send, so on retry
    we have to re-open them. Caller still owns the `files` dict for
    cleanup of the initial handles.
    """
    delay = 2.0
    for attempt in range(max_retries + 1):
        if attempt > 0:
            # Re-open handles for this retry.
            files = {"file": (file_name, open(file_path, "rb"))}
            if thumb_path and thumb_path.is_file():
                thumb_mime = _detect_image_mime(thumb_path) or "image/png"
                files["thumbnail"] = (thumb_path.name, open(thumb_path, "rb"), thumb_mime)
        r = requests.post(url, headers=headers, data=data, files=files, timeout=120)
        if r.status_code != 429:
            return r
        if attempt >= max_retries:
            return r
        # Honor Retry-After if the server set one, else exponential backoff.
        retry_after = r.headers.get("Retry-After")
        try:
            wait = float(retry_after) if retry_after else delay
        except (TypeError, ValueError):
            wait = delay
        print(f"  rate limited (429), waiting {wait:.1f}s and retrying ({attempt + 1}/{max_retries})",
              file=sys.stderr)
        time.sleep(wait)
        delay = min(delay * 2, 30.0)
    return r


def patch_asset_icon(server: str, token: str, owner_username: str,
                     asset_slug: str, icon: str) -> tuple[bool, str]:
    """PUT /assets/:owner/:slug to set the Houdini icon on an existing
    asset. Used by --repair-icons after the server gained an icon column."""
    url = f"{server}/api/v1/assets/{quote(owner_username, safe='')}/{quote(asset_slug, safe='')}"
    try:
        r = requests.put(url, headers={"Authorization": f"Bearer {token}"},
                         json={"icon": icon}, timeout=30)
    except requests.RequestException as e:
        return False, f"network: {e}"
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    return True, "ok"


def patch_thumbnail(server: str, token: str, owner_username: str,
                    asset_slug: str, thumb_path: Path) -> tuple[bool, str]:
    """Re-upload a thumbnail for an existing asset via /thumbnail.

    Returns (ok, info). Used during idempotent re-runs (--repair-thumbnails)
    to fix migrations that ran before the thumbnail-MIME fix landed and
    left version.thumbnail_url NULL.
    """
    if not thumb_path.is_file():
        return False, f"thumb file missing: {thumb_path}"
    thumb_mime = _detect_image_mime(thumb_path)
    if not thumb_mime:
        return False, "not an image"
    url = f"{server}/api/v1/assets/{quote(owner_username, safe='')}/{quote(asset_slug, safe='')}/thumbnail"
    files = {"thumbnail": (thumb_path.name, open(thumb_path, "rb"), thumb_mime)}
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                          files=files, timeout=60)
    finally:
        files["thumbnail"][1].close()
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    return True, "ok"


def main() -> int:
    args = parse_args()
    if not args.dry_run and not args.token:
        sys.exit("--token or SOPDROP_TOKEN env var required (unless --dry-run)")

    server = args.server.rstrip("/")
    db_path = resolve_db_path(args.nas)
    assets_dir = resolve_assets_dir(db_path)
    thumbs_dir = resolve_thumbnails_dir(db_path)

    # Visibility default: 'public' when migrating into a team (trust-LAN
    # deployments treat the LAN as the access boundary, and unlisted/
    # private assets are unreadable through the trust-LAN download path),
    # 'unlisted' otherwise (cloud/personal-library migrations).
    if args.visibility is None:
        args.visibility = "public" if args.team else "unlisted"

    print(f"NAS:    {db_path}")
    print(f"Server: {server}")
    print(f"Mode:   {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"Visibility: {args.visibility}")
    if args.preserve_authorship:
        print(f"Authorship: PRESERVE (created_by + created_at from source DB)")
    if args.team:
        print(f"Team:   {args.team} (assets will be associated with this team)")
    else:
        print(f"Team:   (none — assets land in personal libraries; pass --team <slug> for team library)")
    if args.repair_thumbnails:
        print(f"Repair: thumbnails will be re-uploaded for already-existing assets")
    if args.repair_icons:
        print(f"Repair: Houdini icons will be backfilled for already-existing assets")
    print()

    rows = load_assets(db_path, args.include_deleted)
    print(f"Found {len(rows)} active assets on NAS")

    nas_collections = load_collections(db_path)
    memberships = load_collection_memberships(db_path)
    print(f"Found {len(nas_collections)} collections "
          f"({sum(len(v) for v in memberships.values())} memberships)")

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
                    "error: --preserve-authorship requires an admin or owner token.\n"
                    f"Token user '{token_username}' has role '{role}'.\n\n"
                    "On a fresh server the first user to log in is auto-promoted to owner.\n"
                    "If that's not you, ask the first user to mint an API token and run\n"
                    "the migration as them, OR promote yourself manually:\n"
                    f"  docker compose exec postgres psql -U sopdrop -d sopdrop -c \\\n"
                    f"    \"UPDATE users SET role='admin', is_admin=true WHERE username='{token_username}';\""
                )

    # Migrate collections → team folders (parents first, idempotent).
    # Only meaningful when --team is set; without a team there's no
    # shared folder space. The server-side personal user_folders are
    # per-token-user and would dilute when --preserve-authorship maps
    # uploads to many different owners.
    coll_to_folder: dict[str, str] = {}
    if args.team and not args.no_collections and nas_collections:
        print(f"\nMigrating {len(nas_collections)} collection(s) → team folders...")
        coll_to_folder, created, reused, repaired = migrate_collections_to_team_folders(
            server, args.token, args.team, nas_collections, args.dry_run,
        )
        if args.dry_run:
            print(f"  would create {created} folder(s), reuse {reused}")
        else:
            tail = f", repaired {repaired} parent link(s)" if repaired else ""
            print(f"  created {created} folder(s), reused {reused}{tail}")
    elif nas_collections and (args.no_collections or not args.team):
        reason = "--no-collections" if args.no_collections else "no --team set"
        print(f"  skipping collection migration ({reason})")

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

    uploaded = skipped = failed = thumb_repaired = icon_repaired = 0
    thumb_uploaded_count = thumb_missing_count = thumb_unreadable_count = 0
    failures: list[tuple[str, str]] = []
    missing_thumbs: list[str] = []

    def maybe_repair_icon(asset: dict, owner: str, slug: str, log_prefix: str):
        """If --repair-icons and the asset already exists, PUT its
        Houdini icon name. For libraries migrated before the server
        gained an icon column."""
        nonlocal icon_repaired
        if not args.repair_icons:
            return
        icon = asset.get("icon")
        if not icon:
            return
        try:
            ok, info = patch_asset_icon(server, args.token, owner, slug, str(icon))
        except requests.RequestException as e:
            ok, info = False, f"network: {e}"
        if ok:
            icon_repaired += 1
            print(f"  icon backfilled for {owner}/{slug}: {icon}")
        else:
            print(f"  icon backfill failed for {owner}/{slug}: {info}", file=sys.stderr)

    def maybe_repair_thumb(asset: dict, owner: str, slug: str, log_prefix: str):
        """If --repair-thumbnails and the asset already exists, PATCH
        its thumbnail via the dedicated endpoint. Lets a re-run after
        the MIME-validation fix (Bug 11) backfill thumbnails for assets
        uploaded by an older script that left thumbnail_url NULL."""
        nonlocal thumb_repaired
        if not args.repair_thumbnails:
            return
        thumb_name = asset.get("thumbnail_path")
        if not thumb_name:
            return
        thumb_full = thumbs_dir / thumb_name
        if not thumb_full.is_file():
            return
        try:
            ok, info = patch_thumbnail(server, args.token, owner, slug, thumb_full)
        except requests.RequestException as e:
            ok, info = False, f"network: {e}"
        if ok:
            thumb_repaired += 1
            print(f"  thumbnail repaired for {owner}/{slug}")
        else:
            print(f"  thumbnail repair failed for {owner}/{slug}: {info}", file=sys.stderr)

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
            maybe_repair_thumb(asset, owner_for_check, slug, prefix)
            maybe_repair_icon(asset, owner_for_check, slug, prefix)
            continue

        # Resolve the folder slug for this asset (server stores one
        # folder_id per asset; pick the primary collection if multi).
        folder_slug = primary_folder_slug_for_asset(
            asset["id"], memberships, coll_to_folder
        ) if coll_to_folder else None

        if args.dry_run:
            file_name = asset.get("file_path") or "?"
            file_path = assets_dir / file_name
            present = "ok" if file_path.is_file() else "MISSING"
            extra = ""
            if as_user or created_at:
                extra = f" [as={as_user or token_username}, at={created_at or 'now'}]"
            if args.team:
                extra += f" [team={args.team}]"
            if folder_slug:
                extra += f" [folder={folder_slug}]"
            print(f"{prefix} — would upload ({file_name}, file: {present}){extra}")
            uploaded += 1
            continue

        try:
            ok, info, already, thumb_ok = upload_one(
                server, args.token, asset, assets_dir, thumbs_dir,
                args.visibility, as_user, created_at, args.team,
                folder_slug,
            )
        except requests.RequestException as e:
            ok, info, already, thumb_ok = False, f"network: {e}", False, False

        if ok:
            uploaded += 1
            existing_for(owner_for_check).add(slug.lower())
            attribution = f" as {as_user}" if as_user else ""
            folder_note = f" → {folder_slug}" if folder_slug else ""
            thumb_note = "" if thumb_ok else (
                " [no-thumb-on-NAS]" if not asset.get("thumbnail_path")
                else " [thumb-unreadable]"
            )
            print(f"{prefix} — uploaded ({info}){folder_note}{attribution}{thumb_note}")
            if thumb_ok:
                thumb_uploaded_count += 1
            elif not asset.get("thumbnail_path"):
                thumb_missing_count += 1
            else:
                thumb_unreadable_count += 1
                missing_thumbs.append(name)
            if args.limit and uploaded >= args.limit:
                print(f"\n--limit {args.limit} reached, stopping")
                break
            time.sleep(0.1)  # gentle pacing
        elif already:
            skipped += 1
            existing_for(owner_for_check).add(slug.lower())
            print(f"{prefix} — already exists for {owner_for_check}, skip")
            maybe_repair_thumb(asset, owner_for_check, slug, prefix)
            maybe_repair_icon(asset, owner_for_check, slug, prefix)
        else:
            failed += 1
            failures.append((name, info))
            print(f"{prefix} — FAILED: {info}", file=sys.stderr)

    print()
    summary = f"Summary: {uploaded} uploaded, {skipped} skipped, {failed} failed"
    if args.repair_thumbnails:
        summary += f", {thumb_repaired} thumbnails repaired"
    if args.repair_icons:
        summary += f", {icon_repaired} icons repaired"
    print(summary)
    if not args.dry_run and uploaded:
        print(f"Thumbnails: {thumb_uploaded_count} uploaded, "
              f"{thumb_missing_count} not on NAS, "
              f"{thumb_unreadable_count} unreadable")
        if missing_thumbs:
            print("  unreadable thumbnails (asset still uploaded; rerun with "
                  "--repair-thumbnails after fixing the source files):")
            for n in missing_thumbs[:20]:
                print(f"    - {n}")
            if len(missing_thumbs) > 20:
                print(f"    … and {len(missing_thumbs) - 20} more")
    if failures:
        print("\nFailures:")
        for n, why in failures:
            print(f"  - {n}: {why}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
