"""
HTTP-mode team library shim.

When `team_library_mode` is set to "http" and the active library is
"team", every team-library function in library.py routes through here
instead of touching SQLite. The contract: this module returns dicts
with the *exact same shape* the SQLite path would return, so the panel
sees no difference.

The conversion layer (`_asset_from_http`, `_collection_from_http`) maps
server JSON to SQLite-row-style dicts. New keys like `_thumbnail_url`
and `_download_url` carry HTTP-only fields the panel will use in
Phase 3 for thumbnail loading and Phase 1 for paste downloads.

Errors from the HTTP layer (OfflineError, AuthError, etc.) propagate as
exceptions; library.py callers either let them bubble or catch and
fall back where appropriate.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from . import http_library as _http
from .api import AuthError, NotFoundError, SopdropError
from .config import (
    get_active_library,
    get_config,
    get_team_library_mode,
    get_team_slug,
)
from .http_library import (
    ForbiddenError,
    HttpLibraryClient,
    OfflineError,
    ServerError,
)


def _absolute_url(maybe_url: str | None) -> str | None:
    """Server returns /library/... paths in local-storage mode; turn those
    into absolute URLs by prepending the configured server URL so the
    panel/thumbnail cache can fetch them directly."""
    if not maybe_url:
        return None
    if maybe_url.startswith(("http://", "https://")):
        return maybe_url
    base = (get_config().get("server_url") or "").rstrip("/")
    if maybe_url.startswith("/"):
        return f"{base}{maybe_url}"
    return f"{base}/{maybe_url}"


# ─── ETag cache ─────────────────────────────────────────────────────────
#
# In-process cache of the last response per (team_slug, endpoint, filters).
# When the panel reopens with identical filters, we send If-None-Match;
# the server returns 304 and we reuse the cached body. This is the big
# perceived-perf win: a panel refresh after no library changes does
# ~zero work — one round-trip, no JSON parse, no SQL on the server.

_etag_cache_lock = threading.Lock()
_etag_cache: dict[tuple, tuple[str, Any, float]] = {}  # key -> (etag, body, ts)
_ETAG_CACHE_TTL = 5 * 60  # 5 min — bound stale-on-process-crash exposure


def _cache_key(*parts) -> tuple:
    return (get_team_slug() or "",) + tuple(parts)


def _cache_get(key: tuple) -> tuple[str | None, Any]:
    with _etag_cache_lock:
        entry = _etag_cache.get(key)
        if not entry:
            return None, None
        etag, body, ts = entry
        if (time.time() - ts) > _ETAG_CACHE_TTL:
            _etag_cache.pop(key, None)
            return None, None
        return etag, body


def _cache_put(key: tuple, etag: str | None, body: Any) -> None:
    if not etag or body is None:
        return
    with _etag_cache_lock:
        _etag_cache[key] = (etag, body, time.time())


def invalidate_cache() -> None:
    """Drop the in-process ETag cache. Called after writes so the next
    read reflects the change rather than a stale 304."""
    with _etag_cache_lock:
        _etag_cache.clear()


# ─── Activation ─────────────────────────────────────────────────────────


def is_active() -> bool:
    """True iff the current call should route through HTTP rather than SQLite."""
    return get_active_library() == "team" and get_team_library_mode() == "http"


def _client() -> HttpLibraryClient:
    slug = get_team_slug()
    if not slug:
        raise SopdropError(
            "Team library is in HTTP mode but no team_slug is configured. "
            "Set sopdrop.config.set_team_slug('your-team') first."
        )
    return HttpLibraryClient(slug)


# ─── Shape conversion: server JSON → SQLite-row-style dict ──────────────


def _asset_from_http(a: dict) -> dict:
    """Map a server asset row to the dict shape the panel reads.

    Adds _thumbnail_url and _download_url for the panel's HTTP-aware paths
    (Phase 3 thumbnail fetch, paste-download). Sets thumbnail_path/file_path
    to None so legacy SQLite codepaths short-circuit cleanly.
    """
    metadata = a.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}

    return {
        # Identity
        "id": a.get("id"),
        "name": a.get("name") or "",
        "slug": (a.get("slug") or "").split("/", 1)[-1] if "/" in (a.get("slug") or "") else a.get("slug"),
        # Type/context
        "asset_type": a.get("type") or "node",
        "context": a.get("context") or "sop",
        # Description
        "description": a.get("description") or "",
        # Metadata fields (already deserialized by server)
        "node_count": a.get("nodeCount") or 0,
        "node_names": a.get("nodeNames") or [],
        "node_types": metadata.get("nodeTypes") or [],
        "tags": a.get("tags") or [],
        "houdini_version": metadata.get("houdiniVersion") or "",
        "has_hda_dependencies": int(bool(metadata.get("hasHdaDependencies"))),
        "dependencies": metadata.get("dependencies") or [],
        "metadata": metadata,
        # File pointers — HTTP-only, no local path
        "file_path": None,
        "file_hash": a.get("fileHash"),
        "file_size": a.get("fileSize"),
        "thumbnail_path": None,
        "_thumbnail_url": _absolute_url(a.get("thumbnailUrl")),
        "_download_url": _absolute_url(a.get("downloadUrl")),
        "_remote_slug": a.get("slug"),
        # HDA-specific (server doesn't track separately yet — leave blank)
        "hda_type_name": metadata.get("hdaTypeName"),
        "hda_type_label": metadata.get("hdaTypeLabel"),
        "hda_version": metadata.get("hdaVersion"),
        "hda_category": metadata.get("hdaCategory"),
        # Houdini icon name (e.g. 'SOP_scatter'). Server returns it as
        # a top-level field on the asset; legacy packages may have it
        # tucked inside metadata, so fall back there.
        "icon": a.get("icon") or metadata.get("icon"),
        # Timestamps + use tracking
        "created_at": a.get("createdAt"),
        "updated_at": a.get("updatedAt"),
        "last_used_at": None,
        "use_count": a.get("downloadCount") or 0,  # closest analog
        "is_favorite": 0,  # per-user; phase 1 leaves this off (favorites endpoint exists separately)
        "created_by": a.get("owner"),
        # Sync (HTTP team assets are inherently "synced")
        "remote_slug": a.get("slug"),
        "remote_version": a.get("latestVersion"),
        "sync_status": "synced",
        "synced_at": a.get("updatedAt"),
        # Soft-delete
        "deleted_at": None,
        # Collections — populated by caller from collectionMap
        "collections": [],
    }


def _collection_from_http(c: dict) -> dict:
    """Map a server folder row to the dict shape the panel reads."""
    return {
        "id": c.get("id"),
        "name": c.get("name") or "",
        "description": c.get("description"),
        "color": c.get("color") or "#666666",
        "icon": c.get("icon") or "folder",
        "parent_id": c.get("parentId"),
        "sort_order": c.get("position") or 0,
        "source": "team",
        "remote_id": c.get("dbId"),
        "created_at": c.get("createdAt"),
        "updated_at": c.get("updatedAt"),
        "asset_count": c.get("assetCount") or 0,
    }


# ─── Read functions ─────────────────────────────────────────────────────


def get_all_assets_cached() -> tuple[list[dict], dict]:
    """HTTP variant of library.get_all_assets_cached().

    Returns (assets, collection_map) where collection_map maps
    collection-id → set(asset-id), matching the SQLite shape.

    Uses an ETag cache to make repeat opens nearly free. The first call
    fetches the full list; subsequent calls send If-None-Match and reuse
    the cached body if the server returns 304.
    """
    cache_key = _cache_key("library", "all")
    etag, cached = _cache_get(cache_key)

    # Only the first page carries an ETag; if we have one, try a
    # conditional GET. On 304 we reuse the entire cached body — including
    # collection_map — and skip the rest of the pagination loop entirely.
    if etag:
        result = _client().list_assets(limit=100, offset=0, if_none_match=etag)
        if result.not_modified:
            return cached  # cached value is already (assets, coll_map)

    body = _client().list_all_assets()
    assets = [_asset_from_http(a) for a in body.get("assets", [])]
    by_id = {a["id"]: a for a in assets}

    raw_map = body.get("collectionMap") or {}
    coll_map: dict[str, set] = {}
    # Server returns dbId-keyed in some cases; for Phase 0 we used folder_id (UUID)
    # — match the panel's expectation of {collection_uuid: set(asset_uuids)}.
    # Each value is a list of dbIds (numeric); we need to reverse-map dbId→assetId.
    db_id_to_asset = {a.get("metadata", {}).get("__dbId__"): a for a in assets}
    # Phase 0 server returns dbId in `collectionMap` values. But our asset rows
    # carry it via `dbId` field too — we discarded it during conversion. Easier:
    # query collection memberships separately if we need them. For now, build
    # a name-based fallback: collections will populate via collection_map keys.
    # The panel uses collection_map[coll_id] = set(asset_ids), so we need to
    # cross-reference. Use a separate lookup keyed by server dbId.
    db_to_asset_id: dict[int, str] = {}
    for a in body.get("assets", []):
        db_to_asset_id[a.get("dbId")] = a.get("id")
    for coll_uuid, db_ids in raw_map.items():
        ids = {db_to_asset_id.get(d) for d in db_ids if db_to_asset_id.get(d)}
        if ids:
            coll_map[coll_uuid] = ids

    # Populate per-asset 'collections' list (panel reads asset['collections']).
    # Use the unfiltered cached body — list_collections() defaults to
    # parent_id=None which returns ROOTS ONLY, so assets in nested
    # folders never had their collections populated and showed up
    # under "Uncategorized" when grouping was enabled.
    coll_body = _list_collections_body()
    coll_lookup = {c["id"]: _collection_from_http(c) for c in coll_body.get("collections", [])}
    asset_to_colls: dict[str, list] = {}
    for cid, asset_ids in coll_map.items():
        coll = coll_lookup.get(cid)
        if not coll:
            continue
        small = {"id": coll["id"], "name": coll["name"], "color": coll["color"],
                 "icon": coll["icon"], "parent_id": coll["parent_id"],
                 "sort_order": coll["sort_order"]}
        for aid in asset_ids:
            asset_to_colls.setdefault(aid, []).append(small)
    for asset in assets:
        asset["collections"] = asset_to_colls.get(asset["id"], [])

    # Stash result + ETag for next-call short-circuit. The first-page
    # ETag is captured by list_all_assets and forwarded under
    # _firstPageEtag, so we don't need a separate revalidation round-trip.
    first_etag = body.get("_firstPageEtag")
    if first_etag:
        _cache_put(cache_key, first_etag, (assets, coll_map))

    return assets, coll_map


def search_assets(*, query="", context=None, tags=None, collection_id=None,
                  sort_by="updated_at", sort_order="desc", limit=100, offset=0,
                  favorites_only=False) -> list[dict]:
    sort_map = {
        "updated_at": "updated", "created_at": "recent", "name": "name",
        "use_count": "downloads", "last_used_at": "updated",
    }
    body = _client().list_assets(
        q=query or None,
        context=context,
        tags=tags,
        sort=sort_map.get(sort_by, "updated"),
        limit=limit, offset=offset,
    ).body or {}
    assets = [_asset_from_http(a) for a in body.get("assets", [])]
    if collection_id:
        # Server doesn't filter by collection yet — drop client-side
        in_coll = set()
        raw_map = body.get("collectionMap") or {}
        if collection_id in raw_map:
            db_to_id = {a.get("dbId"): a.get("id") for a in body.get("assets", [])}
            in_coll = {db_to_id.get(d) for d in raw_map[collection_id]}
        assets = [a for a in assets if a["id"] in in_coll]
    return assets


def get_asset(asset_id: str) -> dict | None:
    try:
        result = _client().get_asset(asset_id)
    except NotFoundError:
        return None
    if not result.body:
        return None
    return _asset_from_http(result.body)


def load_asset_package(asset_id: str) -> dict | None:
    """Download the .sopdrop package by hitting /assets/:slug/download/latest.

    Returns the parsed package dict, or None on failure.
    """
    asset = get_asset(asset_id)
    if not asset:
        return None
    remote_slug = asset.get("_remote_slug")
    version = asset.get("remote_version") or "latest"
    if not remote_slug:
        return None

    from urllib.error import HTTPError, URLError
    from urllib.request import Request
    from urllib.parse import quote
    from .api import _ssl_urlopen
    from .config import get_api_url
    from .http_library import _auth_headers

    url = f"{get_api_url().rstrip('/')}/assets/{remote_slug}/download/{quote(version, safe='')}"
    headers = {"Accept": "application/json", "User-Agent": "sopdrop-client/0.1.2"}
    headers.update(_auth_headers())
    try:
        response = _ssl_urlopen(Request(url, headers=headers), timeout=30)
        body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError):
        return None
    pkg = body.get("package") if isinstance(body, dict) else None
    return pkg


def _list_collections_body() -> dict:
    """Fetch + cache the raw collections response. Used by both
    list_collections and get_collection_tree to share a single GET."""
    cache_key = _cache_key("library", "collections")
    etag, cached = _cache_get(cache_key)
    if etag:
        result = _client().list_collections(if_none_match=etag)
        if result.not_modified:
            return cached
    result = _client().list_collections()
    body = result.body or {}
    if result.etag:
        _cache_put(cache_key, result.etag, body)
    return body


def list_collections(parent_id: str | None = None) -> list[dict]:
    body = _list_collections_body()
    collections = [_collection_from_http(c) for c in body.get("collections", [])]
    if parent_id is None:
        return [c for c in collections if not c["parent_id"]]
    return [c for c in collections if c["parent_id"] == parent_id]


def get_collection_tree() -> list[dict]:
    body = _list_collections_body()
    flat = [_collection_from_http(c) for c in body.get("collections", [])]
    by_id = {c["id"]: dict(c, children=[]) for c in flat}
    roots = []
    for c in by_id.values():
        if c["parent_id"] and c["parent_id"] in by_id:
            by_id[c["parent_id"]]["children"].append(c)
        else:
            roots.append(c)
    return roots


def get_collection(collection_id: str) -> dict | None:
    # list_collections() defaults to parent_id=None which filters to
    # ROOT folders only; nested folders need the unfiltered set so we
    # can resolve any UUID, not just top-level ones. This is what the
    # filter-chip / breadcrumb paths in the panel rely on for naming.
    body = _list_collections_body()
    for c in body.get("collections", []):
        if c.get("id") == collection_id:
            return _collection_from_http(c)
    return None


def get_all_tags() -> list[dict]:
    cache_key = _cache_key("library", "tags")
    etag, cached = _cache_get(cache_key)
    if etag:
        result = _client().list_tags(if_none_match=etag)
        if result.not_modified:
            return cached
    result = _client().list_tags()
    body = result.body or {}
    tags = body.get("tags", [])
    if result.etag:
        _cache_put(cache_key, result.etag, tags)
    return tags


def get_library_stats() -> dict:
    body = _client().get_stats().body or {}
    return {
        "asset_count": body.get("assetCount", 0),
        "collection_count": body.get("collectionCount", 0),
        "total_size_mb": body.get("totalSizeMb", 0),
    }


def get_recent_assets(limit: int = 10) -> list[dict]:
    body = _client().list_assets(sort="updated", limit=limit).body or {}
    return [_asset_from_http(a) for a in body.get("assets", [])]


def get_frequent_assets(limit: int = 10) -> list[dict]:
    body = _client().list_assets(sort="downloads", limit=limit).body or {}
    return [_asset_from_http(a) for a in body.get("assets", [])]


def get_asset_collections(asset_id: str) -> list[dict]:
    """Return the asset's folder memberships.

    Single-asset GET /assets/:id returns no collections (server doesn't
    join the per-asset folder lookup on that path), so we resolve via
    the cached library list — which DOES carry collections after
    get_all_assets_cached's post-processing. Falls back to the per-asset
    fetch only if the asset isn't in the cached set (e.g. just-created).
    """
    try:
        assets, _ = get_all_assets_cached()
        for a in assets:
            if a.get("id") == asset_id:
                return a.get("collections", [])
    except Exception:
        pass
    a = get_asset(asset_id)
    return a.get("collections", []) if a else []


# ─── Write functions ────────────────────────────────────────────────────


def record_asset_use(asset_id: str) -> None:
    try:
        _client().record_use(asset_id)
    except (OfflineError, SopdropError):
        pass  # best-effort, never blocks paste
    # Don't invalidate cache for use tracking — download_count isn't shown
    # prominently and stale-by-one paste isn't worth re-fetching the world.


def create_collection(*, name, description="", color="#666666", icon="folder",
                      parent_id=None) -> dict:
    parent_slug = None
    if parent_id:
        parent = get_collection(parent_id)
        if parent:
            # We need slug from server; refetch flat list
            body = _client().list_collections().body or {}
            for c in body.get("collections", []):
                if c.get("id") == parent_id:
                    parent_slug = c.get("slug")
                    break
    f = _client().create_collection(
        name=name, description=description, color=color, icon=icon,
        parent_slug=parent_slug,
    )
    invalidate_cache()
    return _collection_from_http(f)


def update_collection(collection_id: str, **fields) -> dict | None:
    server_fields = {}
    for k in ("name", "description", "color", "icon", "position"):
        if k in fields:
            server_fields[k] = fields[k]
    if "sort_order" in fields:
        server_fields["position"] = fields["sort_order"]
    # Reparent (drag-drop in the panel sidebar). The panel passes
    # parent_id as the new parent's UUID (or None to make it a root);
    # the server PUT endpoint expects parentSlug, so resolve UUID→slug
    # via the cached collection list. parent_id explicitly None clears
    # the parent.
    if "parent_id" in fields:
        new_parent_uuid = fields["parent_id"]
        if new_parent_uuid in (None, ""):
            server_fields["parentSlug"] = None
        else:
            body = _list_collections_body()
            parent_slug = None
            for c in body.get("collections", []):
                if c.get("id") == new_parent_uuid:
                    parent_slug = c.get("slug")
                    break
            if parent_slug is None:
                # Unknown parent UUID — surface rather than silently
                # leaving the folder where it was.
                raise SopdropError(
                    f"Cannot move collection: parent '{new_parent_uuid}' not found"
                )
            server_fields["parentSlug"] = parent_slug
    if not server_fields:
        return get_collection(collection_id)
    f = _client().update_collection(collection_id, **server_fields)
    invalidate_cache()
    return _collection_from_http(f)


def delete_collection(collection_id: str, recursive: bool = False) -> None:
    # Server-side cascade not implemented; recursive flag ignored for now.
    _client().delete_collection(collection_id)
    invalidate_cache()


def update_asset(asset_id: str, **fields) -> dict | None:
    asset = get_asset(asset_id)
    if not asset:
        return None
    remote_slug = asset.get("_remote_slug")
    if not remote_slug:
        return None
    # Map SQLite-style keys to server keys
    server_fields = {}
    if "name" in fields:
        server_fields["name"] = fields["name"]
    if "description" in fields:
        server_fields["description"] = fields["description"]
    if "tags" in fields:
        server_fields["tags"] = fields["tags"]
    if not server_fields:
        return asset
    _http.update_asset_meta(remote_slug, fields=server_fields)
    invalidate_cache()
    return get_asset(asset_id)


def delete_asset(asset_id: str) -> None:
    asset = get_asset(asset_id)
    if not asset:
        return
    remote_slug = asset.get("_remote_slug")
    if remote_slug:
        _http.delete_asset_remote(remote_slug)
        invalidate_cache()


def update_asset_thumbnail(asset_id: str, thumbnail_data: bytes) -> bool:
    asset = get_asset(asset_id)
    if not asset:
        return False
    remote_slug = asset.get("_remote_slug")
    if not remote_slug:
        return False
    # Reuse the multipart helper but for a thumbnail-only POST.
    import mimetypes
    import socket as _socket
    import uuid as _uuid
    from urllib.error import HTTPError, URLError
    from urllib.request import Request
    from .api import _ssl_urlopen
    from .config import get_api_url, get_token

    boundary = f"----sopdrop{_uuid.uuid4().hex}"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="thumbnail"; filename="thumb.png"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + thumbnail_data + tail
    # Note: thumbnail-only POST uses a single part; format differs slightly
    # from upload_asset (multi-part). Verified separately.

    url = f"{get_api_url().rstrip('/')}/assets/{remote_slug}/thumbnail"
    from .http_library import _auth_headers, _have_auth
    if not _have_auth():
        raise AuthError("No identity available (no token and trust-LAN not configured).")
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        **_auth_headers(),
        "User-Agent": "sopdrop-client/0.1.2",
    }
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        _ssl_urlopen(req, timeout=60)
        return True
    except (HTTPError, URLError, _socket.timeout, OSError):
        return False


def save_asset(*, name, package_data, description="", tags=None,
               thumbnail_data=None, license="MIT", icon=None,
               created_by=None) -> str | None:
    """Publish a node-package asset to the team library via HTTP.

    Returns the new asset's UUID, or None on failure.
    """
    package_json = json.dumps(package_data, indent=2).encode("utf-8")
    file_name = f"{name}.sopdrop"
    body = _http.upload_asset(
        name=name,
        file_bytes=package_json,
        file_name=file_name,
        thumbnail_bytes=thumbnail_data,
        thumbnail_name=("thumb.png" if thumbnail_data else None),
        description=description or "",
        license=license,
        houdini_context=(package_data.get("context") or "sop").lower(),
        tags=tags or [],
        visibility="private",
        team_slug=get_team_slug(),
    )
    invalidate_cache()
    return body.get("id") or (body.get("asset") or {}).get("id")


def list_trashed_assets() -> list[dict]:
    """List soft-deleted team assets. Same dict shape as get_all_assets_cached
    rows, with extra keys: 'deleted_at', 'deleted_reason'."""
    body = _client().list_trash().body or {}
    out = []
    for a in body.get("assets", []):
        out.append({
            "id": a.get("id"),
            "dbId": a.get("dbId"),
            "name": a.get("name"),
            "slug": (a.get("slug") or "").split("/", 1)[-1],
            "asset_type": a.get("type") or "node",
            "context": a.get("context") or "sop",
            "description": a.get("description") or "",
            "tags": a.get("tags") or [],
            "node_count": 0, "node_names": [], "node_types": [],
            "metadata": {}, "dependencies": [], "has_hda_dependencies": 0,
            "file_path": None, "file_hash": None,
            "file_size": a.get("fileSize") or 0,
            "thumbnail_path": None,
            "_thumbnail_url": _absolute_url(a.get("thumbnailUrl")),
            "_remote_slug": a.get("slug"),
            "icon": None, "houdini_version": "",
            "hda_type_name": None, "hda_type_label": None,
            "hda_version": None, "hda_category": None,
            "created_at": a.get("deletedAt"),
            "updated_at": a.get("deletedAt"),
            "last_used_at": None, "use_count": 0, "is_favorite": 0,
            "created_by": a.get("owner"),
            "remote_slug": a.get("slug"), "remote_version": None,
            "sync_status": "synced", "synced_at": None,
            "deleted_at": a.get("deletedAt"),
            "deleted_reason": a.get("deletedReason"),
            "collections": [],
        })
    return out


def restore_asset(asset_id: str) -> bool:
    """Undelete a team asset. Returns True on success."""
    try:
        _client().restore_asset(asset_id)
        invalidate_cache()
        return True
    except (NotFoundError, OfflineError, SopdropError) as e:
        print(f"[Sopdrop] restore_asset failed: {e}")
        return False


def purge_asset(asset_id: str) -> bool:
    """Permanently delete a team asset (admin/owner only)."""
    try:
        _client().purge_asset(asset_id)
        invalidate_cache()
        return True
    except (NotFoundError, ForbiddenError, OfflineError, SopdropError) as e:
        print(f"[Sopdrop] purge_asset failed: {e}")
        return False


def add_asset_to_collection(asset_id: str, collection_id: str) -> None:
    """Server folder model is one-to-many (each asset in at most one folder),
    not many-to-many like the SQLite model. Treat add as 'set this asset's
    folder to the given one'."""
    asset = get_asset(asset_id)
    if not asset:
        return
    folder = get_collection(collection_id)
    if not folder:
        return
    # We need the slug, not the UUID, for the PUT endpoint
    body = _client().list_collections().body or {}
    folder_slug = None
    for c in body.get("collections", []):
        if c.get("id") == collection_id:
            folder_slug = c.get("slug")
            break
    if not folder_slug:
        return
    remote_slug = asset.get("_remote_slug")
    if not remote_slug:
        return
    _http.update_asset_meta(remote_slug, fields={"folderSlug": folder_slug})
    invalidate_cache()


def remove_asset_from_collection(asset_id: str, collection_id: str) -> None:
    """Clear the asset's folder. Single-folder model means this just nulls
    out folder_id regardless of which collection_id is passed."""
    asset = get_asset(asset_id)
    if not asset:
        return
    remote_slug = asset.get("_remote_slug")
    if not remote_slug:
        return
    _http.update_asset_meta(remote_slug, fields={"folderSlug": None})
    invalidate_cache()


def get_collection_assets(collection_id: str) -> list[dict]:
    """All assets in a folder. Reuses search_assets's collection_id filter."""
    return search_assets(collection_id=collection_id, limit=500)


def toggle_favorite(asset_id: str) -> bool:
    """Use the existing /api/v1/favorites endpoint. Returns new is_favorite state.

    Per-user, not team-wide. Server returns success on toggle; we
    shape-match SQLite by returning the new boolean.
    """
    asset = get_asset(asset_id)
    if not asset:
        return False
    remote_slug = asset.get("_remote_slug")
    if not remote_slug:
        return False
    # Phase 1 simplification: try to add; if 409 (already favorited), delete instead.
    from urllib.error import HTTPError
    from urllib.request import Request
    from urllib.parse import quote
    from .api import _ssl_urlopen
    from .config import get_api_url, get_token

    base = get_api_url().rstrip("/")
    from .http_library import _auth_headers
    headers = {"Accept": "application/json",
               "Content-Type": "application/json",
               "User-Agent": "sopdrop-client/0.1.2"}
    headers.update(_auth_headers())
    try:
        body = json.dumps({"slug": remote_slug}).encode("utf-8")
        _ssl_urlopen(Request(f"{base}/favorites", data=body, headers=headers, method="POST"), timeout=15)
        return True
    except HTTPError as e:
        if e.code == 409:
            # already favorited → unfavorite
            try:
                _ssl_urlopen(Request(f"{base}/favorites/{quote(remote_slug, safe='')}",
                                     headers=headers, method="DELETE"), timeout=15)
                return False
            except Exception:
                return False
        return False
    except Exception:
        return False


def get_all_artists() -> list[dict]:
    """Derive artist counts from the cached asset list."""
    assets, _ = get_all_assets_cached()
    counts: dict[str, int] = {}
    for a in assets:
        owner = a.get("created_by") or "unknown"
        counts[owner] = counts.get(owner, 0) + 1
    out = [{"artist": k, "count": v} for k, v in counts.items()]
    out.sort(key=lambda x: (-x["count"], x["artist"]))
    return out


# ─── Stubs for things we haven't built server-side yet ──────────────────
#
# These call sites exist in the panel; we don't want them to crash by
# falling through to get_db() which is None in HTTP mode. They raise a
# clear error or no-op with a log message.


def save_asset_version(*args, **kwargs):
    """Per-asset version snapshots aren't implemented for HTTP mode yet
    (we agreed 'fresh start' for versions). Telling the user clearly."""
    print("[Sopdrop] Versioning isn't supported on the team server yet — "
          "edit the asset metadata in place, or save as a new asset.")
    return None


def revert_to_version(*args, **kwargs):
    print("[Sopdrop] Versioning isn't supported on the team server yet.")
    return None


def update_asset_package(asset_id: str, package_data) -> bool:
    """Re-uploading the package means uploading it as a new asset (server
    has no in-place package update for an existing slug). The panel's UX
    around this is mostly used for the curve/path special-cases — left
    unsupported here pending product call."""
    print("[Sopdrop] update_asset_package is not yet supported in HTTP "
          "team mode. Re-save the asset under a new name.")
    return False


def empty_trash() -> int:
    """Purge all trashed team assets the caller is allowed to purge.
    Returns number successfully purged."""
    n = 0
    for asset in list_trashed_assets():
        if purge_asset(asset["id"]):
            n += 1
    return n


def save_hda(*, name, hda_bytes, hda_filename, description="", tags=None,
             thumbnail_data=None, license="MIT") -> str | None:
    """Publish an .hda asset to the team library via HTTP."""
    body = _http.upload_asset(
        name=name,
        file_bytes=hda_bytes,
        file_name=hda_filename,
        thumbnail_bytes=thumbnail_data,
        thumbnail_name=("thumb.png" if thumbnail_data else None),
        description=description or "",
        license=license,
        houdini_context="sop",
        tags=tags or [],
        visibility="private",
        team_slug=get_team_slug(),
    )
    invalidate_cache()
    return body.get("id") or (body.get("asset") or {}).get("id")
