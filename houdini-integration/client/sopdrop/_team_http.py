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

from . import _team_mirror
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
    """Drop the in-process ETag cache AND the persistent disk mirror.
    Called after writes (asset save/delete/move/folder change) so the
    next read reflects the change rather than serving a stale 304."""
    with _etag_cache_lock:
        _etag_cache.clear()
    team = get_team_slug()
    if team:
        try:
            _team_mirror.clear(team)
        except Exception as e:
            print(f"[Sopdrop] team mirror clear failed: {e}")


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

    Three-tier cache hierarchy:
      1. In-process ETag cache (RAM, 5-min TTL). Free repeat-opens
         within a single Houdini session.
      2. Persistent SQLite mirror at
         ~/.sopdrop/cache/team-libraries/<team>.db. Survives Houdini
         restarts; primes the in-process cache on cold open and lets
         the cold-open conditional GET return 304 → instant render.
      3. Server. Hit only when neither cache has a usable ETag, or the
         conditional GET comes back 200 (real change).
    """
    team = get_team_slug() or ""
    cache_key = _cache_key("library", "all")
    etag, cached = _cache_get(cache_key)

    # Tier 2: warm the in-process cache from the disk mirror on cold
    # open. Subsequent calls in this session use the RAM path directly.
    if cached is None and team:
        m_assets, m_coll_map, m_etag, _last_synced = _team_mirror.read_snapshot(team)
        if m_assets and m_etag:
            cached = (m_assets, m_coll_map)
            etag = m_etag
            _cache_put(cache_key, m_etag, cached)

    # If we have any ETag (RAM or disk-warmed), try a conditional GET.
    # On 304 we reuse the cached body and skip pagination entirely —
    # this is the common case for repeat opens.
    if etag:
        try:
            result = _client().list_assets(limit=100, offset=0, if_none_match=etag)
            if result.not_modified:
                return cached  # (assets, coll_map)
        except (OfflineError, SopdropError):
            # Network's down or server's unreachable — return what we
            # have rather than failing the panel render. The user still
            # sees the library; refresh-on-reconnect catches the rest.
            if cached is not None:
                return cached
            raise

    body = _client().list_all_assets()
    assets = [_asset_from_http(a) for a in body.get("assets", [])]

    raw_map = body.get("collectionMap") or {}
    coll_map: dict[str, set] = {}
    # Server returns collectionMap as folder_uuid → list[asset_dbId].
    # Translate to folder_uuid → set[asset_uuid] (the shape the panel
    # expects, matching the SQLite path).
    db_to_asset_id: dict[int, str] = {}
    for a in body.get("assets", []):
        db_to_asset_id[a.get("dbId")] = a.get("id")
    for coll_uuid, db_ids in raw_map.items():
        ids = {db_to_asset_id.get(d) for d in db_ids if db_to_asset_id.get(d)}
        if ids:
            coll_map[coll_uuid] = ids

    # Populate per-asset 'collections' list (panel reads asset['collections']).
    # Use the unfiltered cached body so nested folders also resolve.
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

    # Stash result + ETag in both tiers. The first-page ETag is captured
    # by list_all_assets and forwarded under _firstPageEtag.
    first_etag = body.get("_firstPageEtag")
    if first_etag:
        _cache_put(cache_key, first_etag, (assets, coll_map))
        if team:
            try:
                _team_mirror.write_snapshot(
                    team, assets=assets, coll_map=coll_map, etag=first_etag,
                )
                _team_mirror.write_collections(team, coll_body.get("collections", []))
            except Exception as e:
                # Mirror is best-effort — failures here shouldn't break
                # the panel render. Log so a misconfigured cache dir
                # doesn't silently rot.
                print(f"[Sopdrop] team mirror write failed: {e}")

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
    list_collections and get_collection_tree to share a single GET.

    Two-tier cache: RAM ETag cache + disk mirror. Cold opens warm the
    RAM cache from disk so the conditional GET can return 304.
    """
    cache_key = _cache_key("library", "collections")
    etag, cached = _cache_get(cache_key)

    # Disk warm-up — the snapshot stores the asset list's etag, which
    # isn't the same as the collections etag, so we don't have one.
    # But we can still warm `cached` so an offline open renders folders.
    team = get_team_slug() or ""
    if cached is None and team:
        raw = _team_mirror.read_collections(team)
        if raw is not None:
            cached = {"collections": raw}

    try:
        if etag:
            result = _client().list_collections(if_none_match=etag)
            if result.not_modified:
                return cached
        result = _client().list_collections()
    except (OfflineError, SopdropError):
        # Offline / server unreachable — render from mirror if we have
        # one, otherwise propagate so callers can display an error.
        if cached is not None:
            return cached
        raise
    body = result.body or {}
    if result.etag:
        _cache_put(cache_key, result.etag, body)
    if team and body.get("collections") is not None:
        try:
            _team_mirror.write_collections(team, body["collections"])
        except Exception as e:
            print(f"[Sopdrop] team mirror collections write failed: {e}")
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


def _bump_patch_semver(current: str | None) -> str:
    """Return the next patch version after `current`. '1.2.3' → '1.2.4'.
    Falls back to '1.0.0' when current is missing or unparseable."""
    if not current:
        return "1.0.0"
    # Strip pre-release / build metadata before bumping; we re-emit a
    # clean release version (1.2.4-beta → 1.2.4 happens here, which is
    # technically a downgrade — but pre-release version-up is rare and
    # the user can always type a custom version in a future dialog).
    core = str(current).split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    nums = []
    for p in parts:
        try:
            nums.append(int(p))
        except ValueError:
            return "1.0.0"
    while len(nums) < 3:
        nums.append(0)
    nums[2] += 1
    return f"{nums[0]}.{nums[1]}.{nums[2]}"


def save_asset_version(asset_id, package_data, *,
                       description=None, tags=None,
                       thumbnail_data=None) -> dict | None:
    """Publish a new version of an existing team asset.

    The panel calls this after "Update from selection" — re-export the
    user's currently-selected nodes and POST as the next patch version
    on the existing slug. Returns the server response (with the new
    version dict) or None on failure.

    description/tags/thumbnail_data are accepted for signature parity
    with the SQLite path; the server's /versions endpoint stores the
    new file + changelog and leaves asset-level metadata to PUT
    /assets/:slug. We forward `description` to PUT after the version
    publishes, so panel-side metadata edits during version-up land too.
    """
    asset = get_asset(asset_id)
    if not asset:
        print(f"[Sopdrop] save_asset_version: asset {asset_id} not found")
        return None
    remote_slug = asset.get("_remote_slug") or asset.get("remote_slug")
    if not remote_slug:
        print(f"[Sopdrop] save_asset_version: asset {asset_id} has no remote slug")
        return None

    next_version = _bump_patch_semver(asset.get("remote_version"))
    package_json = json.dumps(package_data, indent=2).encode("utf-8")
    file_name = f"{asset.get('name') or 'asset'}.sopdrop"

    try:
        body = _http.publish_version(
            remote_slug,
            version=next_version,
            file_bytes=package_json,
            file_name=file_name,
            changelog=f"Updated to {next_version}",
        )
    except SopdropError as e:
        # If we collide on version (e.g. two artists race a version-up),
        # bump again and retry once. Beyond that, surface the error.
        if "Version conflict" in str(e) or "already exists" in str(e):
            try:
                fresh = get_asset(asset_id)
                next_version = _bump_patch_semver(
                    (fresh or asset).get("remote_version") or next_version
                )
                body = _http.publish_version(
                    remote_slug,
                    version=next_version,
                    file_bytes=package_json,
                    file_name=file_name,
                    changelog=f"Updated to {next_version}",
                )
            except SopdropError as e2:
                print(f"[Sopdrop] save_asset_version retry failed: {e2}")
                return None
        else:
            print(f"[Sopdrop] save_asset_version failed: {e}")
            return None

    # Forward editable metadata from the dialog so the user's edits to
    # description / tags during version-up actually persist. Done after
    # the version publishes so a partial failure leaves us with the new
    # binary but old metadata, which is recoverable.
    meta_fields: dict = {}
    if description is not None:
        meta_fields["description"] = description
    if tags is not None:
        meta_fields["tags"] = list(tags) if not isinstance(tags, list) else tags
    if meta_fields:
        try:
            _http.update_asset_meta(remote_slug, fields=meta_fields)
        except SopdropError as e:
            print(f"[Sopdrop] version published but metadata update failed: {e}")

    if thumbnail_data:
        try:
            update_asset_thumbnail(asset_id, thumbnail_data)
        except Exception as e:
            print(f"[Sopdrop] version published but thumbnail update failed: {e}")

    invalidate_cache()
    return body or {"version": next_version}


def revert_to_version(*args, **kwargs):
    print("[Sopdrop] Reverting to a previous version isn't supported on "
          "the team server yet — version history is server-tracked but "
          "the client can't re-download an arbitrary version into a slug.")
    return None


def update_asset_package(asset_id: str, package_data) -> bool:
    """Backed by save_asset_version — same shape, no separate endpoint."""
    result = save_asset_version(asset_id, package_data)
    return result is not None


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
