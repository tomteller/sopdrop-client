"""
HTTP-backed team library client.

Talks to the on-prem sopdrop-server via the /api/v1/teams/:slug/library/*
endpoints. This module is the data-source for the Houdini panel when the
client is configured for an on-prem team library (Phase 1+).

Phase 0 ships read-only methods. Writes (publish/edit/delete) reuse the
existing /api/v1/assets/* routes via SopdropClient and land in Phase 1.

Design notes:
  - Uses urllib (matching api.py) so we don't add a `requests` dep.
  - Errors are typed so the panel can render distinct UI for each failure
    mode without string-matching exception messages.
  - ETag-aware GET: callers can pass `if_none_match=<etag>` and receive
    None when the server returns 304, allowing zero-cost revalidation.
  - No retries here. The panel runs us on a background QThread; the user
    will retry by opening the panel again. We don't want to hide latency
    in this layer.
"""

from __future__ import annotations

import json
import mimetypes
import os
import socket
import uuid as _uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request

from .api import AuthError, NotFoundError, SopdropError, _ssl_urlopen
from .config import get_api_url, get_token, get_workstation_user, use_lan_trust_auth


def _auth_headers() -> dict:
    """Headers carrying the caller's identity to the server.

    In trust-LAN mode (local-only + HTTP team mode), sends the
    workstation OS username via X-Sopdrop-User. Otherwise sends the
    standard Bearer token. Server prefers token if both are present.
    Caller decides whether missing identity is an error.
    """
    headers = {}
    token = get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if use_lan_trust_auth():
        ws_user = get_workstation_user()
        if ws_user:
            headers["X-Sopdrop-User"] = ws_user
    return headers


def _have_auth() -> bool:
    """True if either a token is set OR trust-LAN can supply identity."""
    if get_token():
        return True
    if use_lan_trust_auth() and get_workstation_user():
        return True
    return False

DEFAULT_TIMEOUT = 15  # seconds


# ─── Errors ─────────────────────────────────────────────────────────────


class OfflineError(SopdropError):
    """Server is unreachable (network error, DNS failure, connection refused, timeout).

    Distinct from SopdropError so the panel can render a "Can't reach
    server — Retry" banner instead of a generic error toast.
    """


class ServerError(SopdropError):
    """Server returned 5xx. Probably transient; user can retry."""


class ForbiddenError(SopdropError):
    """User is authenticated but not a member of this team."""


# ─── Result types ───────────────────────────────────────────────────────


class HttpResult:
    """Lightweight wrapper carrying body + ETag for cache-aware callers.

    Truthy iff body is not None. `etag` is the server's ETag (None if
    absent). Callers that don't care about caching can just use
    `result.body` directly.
    """

    __slots__ = ("body", "etag", "not_modified")

    def __init__(self, body: Any, etag: str | None, not_modified: bool = False):
        self.body = body
        self.etag = etag
        self.not_modified = not_modified

    def __bool__(self) -> bool:
        return self.body is not None

    def __repr__(self) -> str:
        return f"HttpResult(body={'<dict>' if self.body else None}, etag={self.etag!r}, not_modified={self.not_modified})"


# ─── Client ─────────────────────────────────────────────────────────────


class HttpLibraryClient:
    """Client for /api/v1/teams/:slug/library/* endpoints."""

    def __init__(self, team_slug: str, *, timeout: float = DEFAULT_TIMEOUT):
        if not team_slug:
            raise ValueError("team_slug is required")
        self.team_slug = team_slug
        self.timeout = timeout

    # ── Internal: HTTP plumbing ─────────────────────────────────────────

    def _url(self, path: str, params: dict | None = None) -> str:
        base = get_api_url().rstrip("/")
        slug = quote(self.team_slug, safe="")
        path = path.lstrip("/")
        url = f"{base}/teams/{slug}/library/{path}" if path else f"{base}/teams/{slug}/library"
        if params:
            cleaned = {k: v for k, v in params.items() if v is not None and v != ""}
            if cleaned:
                url = f"{url}?{urlencode(cleaned, doseq=True)}"
        return url

    def _get(self, path: str, params: dict | None = None,
             *, if_none_match: str | None = None) -> HttpResult:
        url = self._url(path, params)
        headers = {
            "Accept": "application/json",
            "User-Agent": "sopdrop-client/0.1.2",
        }
        if not _have_auth():
            # No token AND trust-LAN can't supply identity. Fail fast.
            raise AuthError(
                "No identity available. Either log in (sopdrop.login()) or "
                "enable local-only mode with HTTP team mode for trust-LAN auth."
            )
        headers.update(_auth_headers())
        if if_none_match:
            headers["If-None-Match"] = if_none_match

        req = Request(url, headers=headers, method="GET")
        try:
            response = _ssl_urlopen(req, timeout=self.timeout)
        except HTTPError as e:
            return self._raise_http_error(e)
        except (URLError, socket.timeout, ConnectionError, OSError) as e:
            raise OfflineError(f"Cannot reach server: {e}") from e

        etag = response.headers.get("ETag")
        # 304 Not Modified — body intentionally empty
        if response.status == 304:
            return HttpResult(body=None, etag=etag, not_modified=True)

        raw = response.read()
        if not raw:
            return HttpResult(body=None, etag=etag)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ServerError(f"Invalid JSON from server: {e}") from e
        return HttpResult(body=body, etag=etag)

    def _raise_http_error(self, e: HTTPError):
        # Read the body for context if available; some clients tools
        # echo this back to the user.
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = ""
        try:
            msg = json.loads(body).get("error", body)
        except json.JSONDecodeError:
            msg = body or str(e)

        if e.code == 304:
            # Some HTTP libraries surface 304 as an HTTPError; treat as success.
            etag = None
            try:
                etag = e.headers.get("ETag")
            except Exception:
                pass
            return HttpResult(body=None, etag=etag, not_modified=True)
        if e.code == 401:
            raise AuthError("Authentication required. Run sopdrop.login()")
        if e.code == 403:
            raise ForbiddenError(msg or "Forbidden")
        if e.code == 404:
            raise NotFoundError(msg or "Not found")
        if 500 <= e.code < 600:
            raise ServerError(f"Server error ({e.code}): {msg}")
        raise SopdropError(f"API error ({e.code}): {msg}")

    # ── Public read API ─────────────────────────────────────────────────

    def list_assets(
        self,
        *,
        q: str | None = None,
        context: str | None = None,
        type: str | None = None,
        tags: list[str] | str | None = None,
        sort: str = "updated",
        limit: int = 100,
        offset: int = 0,
        since: str | None = None,
        if_none_match: str | None = None,
    ) -> HttpResult:
        """Paginated browse. Body shape: {assets, collectionMap, total, limit, offset, lastUpdated}."""
        if isinstance(tags, list):
            tags = ",".join(tags)
        return self._get(
            "",
            {
                "q": q,
                "context": context,
                "type": type,
                "tags": tags,
                "sort": sort,
                "limit": limit,
                "offset": offset,
                "since": since,
            },
            if_none_match=if_none_match,
        )

    def list_all_assets(self, *, page_size: int = 100, **filters) -> dict:
        """Convenience: page through list_assets and return one combined dict.

        Returns the same shape as list_assets, but with `assets` containing
        every row matching the filters. Use this for the initial library
        load when total count is small. For large libraries the panel
        should paginate explicitly (Phase 2).
        """
        all_assets: list[dict] = []
        merged_map: dict[str, list[int]] = {}
        offset = 0
        total = 0
        last_updated = None
        while True:
            result = self.list_assets(limit=page_size, offset=offset, **filters)
            if not result.body:
                break
            body = result.body
            page = body.get("assets", [])
            all_assets.extend(page)
            for k, v in (body.get("collectionMap") or {}).items():
                merged_map.setdefault(k, []).extend(v)
            total = body.get("total", total)
            last_updated = body.get("lastUpdated", last_updated)
            offset += len(page)
            if len(page) < page_size or offset >= total:
                break
        return {
            "assets": all_assets,
            "collectionMap": merged_map,
            "total": total,
            "limit": page_size,
            "offset": 0,
            "lastUpdated": last_updated,
        }

    def list_collections(self, *, if_none_match: str | None = None) -> HttpResult:
        """Folder/collection sidebar. Body shape: {collections: [...], total}."""
        return self._get("collections", if_none_match=if_none_match)

    def list_tags(self, *, sort: str = "popular", limit: int = 200,
                  if_none_match: str | None = None) -> HttpResult:
        """Tag counts. Body shape: {tags: [{tag, count}, ...]}."""
        return self._get(
            "tags",
            {"sort": sort, "limit": limit},
            if_none_match=if_none_match,
        )

    def get_stats(self) -> HttpResult:
        """Footer stats. Body shape: {assetCount, collectionCount, totalSizeBytes, totalSizeMb, lastUpdated}."""
        return self._get("stats")

    def get_asset(self, asset_id: str) -> HttpResult:
        """Single team-asset by UUID."""
        return self._get(f"assets/{quote(asset_id, safe='')}")

    # ── Writes (mutating) ───────────────────────────────────────────────

    def _write(self, method: str, path: str, body: dict | None = None) -> dict:
        """Issue a JSON POST/PUT/DELETE to a /teams/:slug/library/* route.

        Returns the parsed body (dict) or raises a typed error.
        """
        url = self._url(path)
        headers = {
            "Accept": "application/json",
            "User-Agent": "sopdrop-client/0.1.2",
        }
        if not _have_auth():
            raise AuthError(
                "No identity available. Either log in (sopdrop.login()) or "
                "enable local-only mode with HTTP team mode for trust-LAN auth."
            )
        headers.update(_auth_headers())

        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")

        req = Request(url, data=data, headers=headers, method=method)
        try:
            response = _ssl_urlopen(req, timeout=self.timeout)
        except HTTPError as e:
            self._raise_http_error(e)
            raise  # _raise_http_error always raises, this is for type-checkers
        except (URLError, socket.timeout, ConnectionError, OSError) as e:
            raise OfflineError(f"Cannot reach server: {e}") from e

        raw = response.read()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def record_use(self, asset_id: str) -> dict:
        """Best-effort usage tracking. Caller swallows OfflineError if needed."""
        return self._write("POST", f"assets/{quote(asset_id, safe='')}/use")

    def create_collection(self, *, name: str, description: str | None = None,
                          color: str | None = None, icon: str | None = None,
                          parent_slug: str | None = None) -> dict:
        return self._write("POST", "collections", {
            "name": name,
            "description": description,
            "color": color,
            "icon": icon,
            "parentSlug": parent_slug,
        })

    def update_collection(self, folder_id: str, **fields) -> dict:
        return self._write("PUT", f"collections/{quote(folder_id, safe='')}", fields)

    def delete_collection(self, folder_id: str) -> dict:
        return self._write("DELETE", f"collections/{quote(folder_id, safe='')}")

    def list_trash(self) -> HttpResult:
        """Soft-deleted team assets. Body shape: {assets: [...], total}."""
        return self._get("trash")

    def restore_asset(self, asset_id: str) -> dict:
        """Undelete a team asset (clears is_deprecated)."""
        return self._write("POST", f"assets/{quote(asset_id, safe='')}/restore")

    def purge_asset(self, asset_id: str) -> dict:
        """Permanently delete (admin/owner only)."""
        return self._write("DELETE", f"assets/{quote(asset_id, safe='')}/purge")


# ─── Multipart upload for save_asset / save_hda ─────────────────────────
#
# These hit the existing POST /api/v1/assets/upload route (with teamSlug
# body param). Lives here so all team-library HTTP code is in one module.

def _mp_field(name: str, value: str) -> bytes:
    """A single text field. Includes the leading boundary marker."""
    return (
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
    ).encode("utf-8")


def _mp_file(name: str, filename: str, content_type: str, content: bytes) -> bytes:
    """A single file part. Includes headers + content + trailing CRLF."""
    head = (
        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        f'Content-Type: {content_type}\r\n\r\n'
    ).encode("utf-8")
    return head + content + b'\r\n'


def upload_asset(
    *,
    name: str,
    file_bytes: bytes,
    file_name: str,
    thumbnail_bytes: bytes | None = None,
    thumbnail_name: str | None = None,
    description: str = "",
    readme: str = "",
    license: str = "MIT",
    houdini_context: str = "sop",
    tags: list[str] | None = None,
    visibility: str = "private",
    folder_slug: str | None = None,
    team_slug: str | None = None,
    timeout: float = 60,
) -> dict:
    """Upload an asset (.sopdrop or .hda) via POST /api/v1/assets/upload.

    Returns the server's JSON response body. When team_slug is set the
    asset is created as team-owned; the user must be a member.
    """
    boundary = f"----sopdrop{_uuid.uuid4().hex}"
    sep = f"--{boundary}\r\n".encode("utf-8")
    parts: list[bytes] = []

    def add_field(k: str, v: str | None):
        if v is None or v == "":
            return
        parts.append(sep)
        parts.append(_mp_field(k, v))

    add_field("name", name)
    add_field("description", description)
    add_field("readme", readme)
    add_field("license", license)
    add_field("houdiniContext", houdini_context)
    add_field("visibility", visibility)
    if folder_slug:
        add_field("folderSlug", folder_slug)
    if team_slug:
        add_field("teamSlug", team_slug)
    if tags:
        add_field("tags", json.dumps(tags))

    # File
    file_ct = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    parts.append(sep)
    parts.append(_mp_file("file", file_name, file_ct, file_bytes))

    # Thumbnail (optional)
    if thumbnail_bytes:
        thumb_name = thumbnail_name or "thumbnail.png"
        thumb_ct = mimetypes.guess_type(thumb_name)[0] or "image/png"
        parts.append(sep)
        parts.append(_mp_file("thumbnail", thumb_name, thumb_ct, thumbnail_bytes))

    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    url = f"{get_api_url().rstrip('/')}/assets/upload"
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
        "User-Agent": "sopdrop-client/0.1.2",
        "Accept": "application/json",
    }
    if not _have_auth():
        raise AuthError("No identity available (no token and trust-LAN not configured).")
    headers.update(_auth_headers())

    req = Request(url, data=body, headers=headers, method="POST")
    try:
        response = _ssl_urlopen(req, timeout=timeout)
    except HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
            err = json.loads(err_body).get("error", err_body)
        except Exception:
            err = str(e)
        if e.code == 401:
            raise AuthError(err)
        if e.code == 403:
            raise ForbiddenError(err)
        if e.code == 404:
            raise NotFoundError(err)
        if 500 <= e.code < 600:
            raise ServerError(f"Server error ({e.code}): {err}")
        raise SopdropError(f"Upload failed ({e.code}): {err}")
    except (URLError, socket.timeout, ConnectionError, OSError) as e:
        raise OfflineError(f"Cannot reach server: {e}") from e

    raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def update_asset_meta(asset_slug: str, *, fields: dict, timeout: float = 30) -> dict:
    """PUT /api/v1/assets/:slug — update metadata (description, tags, etc).

    `asset_slug` is "owner/name". Server already handles team-aware auth.
    """
    url = f"{get_api_url().rstrip('/')}/assets/{asset_slug}"
    body = json.dumps(fields).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "sopdrop-client/0.1.2",
    }
    if not _have_auth():
        raise AuthError("No identity available (no token and trust-LAN not configured).")
    headers.update(_auth_headers())
    req = Request(url, data=body, headers=headers, method="PUT")
    try:
        response = _ssl_urlopen(req, timeout=timeout)
    except HTTPError as e:
        if e.code == 401: raise AuthError("Authentication required")
        if e.code == 403: raise ForbiddenError("Not allowed")
        if e.code == 404: raise NotFoundError("Asset not found")
        raise SopdropError(f"Update failed ({e.code})")
    except (URLError, socket.timeout, OSError) as e:
        raise OfflineError(f"Cannot reach server: {e}") from e
    raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def delete_asset_remote(asset_slug: str, *, timeout: float = 30) -> None:
    url = f"{get_api_url().rstrip('/')}/assets/{asset_slug}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "sopdrop-client/0.1.2",
    }
    if not _have_auth():
        raise AuthError("No identity available (no token and trust-LAN not configured).")
    headers.update(_auth_headers())
    req = Request(url, headers=headers, method="DELETE")
    try:
        _ssl_urlopen(req, timeout=timeout)
    except HTTPError as e:
        if e.code == 401: raise AuthError("Authentication required")
        if e.code == 403: raise ForbiddenError("Not allowed")
        if e.code == 404: raise NotFoundError("Asset not found")
        raise SopdropError(f"Delete failed ({e.code})")
    except (URLError, socket.timeout, OSError) as e:
        raise OfflineError(f"Cannot reach server: {e}") from e
