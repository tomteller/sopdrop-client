"""
Persistent SQLite mirror of an HTTP team library.

Why this exists
---------------
The in-process ETag cache in `_team_http.py` makes repeat opens within a
Houdini session free, but every fresh process pays one full GET of the
team library before the panel can render. With a few hundred assets this
is hundreds of milliseconds on top of normal startup; the user sees the
panel load empty, hang on context-letter placeholders, then populate.

This module persists each team library to disk so the next cold open
renders instantly from local SQLite, then revalidates against the server
in the background. On 304 (the common case) we keep what we already had
and skip parsing entirely.

Storage layout
--------------
    ~/.sopdrop/cache/team-libraries/<team-slug>.db

Per-team isolation: each team gets its own DB so an artist on multiple
teams doesn't pay cross-team eviction churn, and the file is cheap to
nuke when something goes sideways.

Schema
------
mirror_assets       — one row per asset, payload is the JSON dict the
                      panel consumes (already shape-converted).
mirror_collections  — one row per folder; payload is the panel-shape
                      dict.
mirror_coll_assets  — folder_id (UUID) → asset_id (UUID) membership.
                      Driven from the server's collectionMap.
mirror_meta         — k/v: last-known ETag and ISO timestamp.

The payload columns are JSON blobs because the asset shape includes
arrays (tags, node_names, collections), nested dicts (metadata), and
HTTP-only fields (_thumbnail_url, _download_url) that don't need to be
queryable. Keeping it as JSON sidesteps schema migrations every time we
add a field server-side.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .config import get_cache_dir


_SCHEMA = """
CREATE TABLE IF NOT EXISTS mirror_assets (
    asset_id TEXT PRIMARY KEY,
    db_id    INTEGER,
    payload  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mirror_collections (
    coll_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mirror_coll_assets (
    coll_id  TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    PRIMARY KEY (coll_id, asset_id)
);

CREATE INDEX IF NOT EXISTS idx_mirror_coll_assets_asset
    ON mirror_coll_assets(asset_id);

CREATE TABLE IF NOT EXISTS mirror_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


_init_lock = threading.Lock()
_initialized: set[str] = set()


def _slugify(s: str) -> str:
    """File-system-safe team slug for the DB filename. Server-side slugs
    are already lowercase alnum-with-dashes, but we sanitize defensively
    so a hand-edited config can't escape the cache dir."""
    out = []
    for ch in (s or "default").lower():
        if ch.isalnum() or ch in "-_":
            out.append(ch)
    cleaned = "".join(out).strip("-_") or "default"
    return cleaned[:64]


def _db_path(team_slug: str) -> Path:
    base = get_cache_dir() / "team-libraries"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{_slugify(team_slug)}.db"


def _connect(team_slug: str) -> sqlite3.Connection:
    """Open a connection, creating the file + schema on first use.

    Each call returns a fresh connection (sqlite3 connections are not
    safe to share across threads). Caller closes when done. Schema setup
    is run once per process per file.
    """
    path = _db_path(team_slug)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL gives concurrent readers + a single writer without long locks,
    # which matters when the panel reads on the worker thread while a
    # background revalidation writes on another.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.DatabaseError:
        pass

    key = str(path)
    if key not in _initialized:
        with _init_lock:
            if key not in _initialized:
                conn.executescript(_SCHEMA)
                conn.commit()
                _initialized.add(key)
    return conn


# ─── Read API ───────────────────────────────────────────────────────────


def read_snapshot(team_slug: str) -> tuple[list[dict], dict[str, set[str]], str | None, float | None]:
    """Return (assets, coll_map, etag, last_synced_epoch).

    `assets` mirrors what _team_http.get_all_assets_cached returns —
    panel-shape dicts including the per-asset 'collections' list.
    `coll_map` is collection_uuid → set(asset_uuid).
    `etag` is what the server returned with the last successful GET; it
    drives the next conditional revalidation.
    `last_synced_epoch` lets the caller decide whether to trust the
    snapshot or force a full refresh.
    """
    try:
        conn = _connect(team_slug)
    except sqlite3.Error:
        return [], {}, None, None
    try:
        rows = conn.execute("SELECT payload FROM mirror_assets").fetchall()
        assets: list[dict] = []
        for row in rows:
            try:
                assets.append(json.loads(row["payload"]))
            except (json.JSONDecodeError, TypeError):
                continue

        coll_map: dict[str, set[str]] = {}
        for r in conn.execute("SELECT coll_id, asset_id FROM mirror_coll_assets"):
            coll_map.setdefault(r["coll_id"], set()).add(r["asset_id"])

        meta_rows = conn.execute("SELECT key, value FROM mirror_meta").fetchall()
        meta = {r["key"]: r["value"] for r in meta_rows}
    finally:
        conn.close()

    etag = meta.get("etag") or None
    last_synced = None
    try:
        last_synced = float(meta["last_synced_epoch"]) if meta.get("last_synced_epoch") else None
    except (ValueError, TypeError):
        last_synced = None
    return assets, coll_map, etag, last_synced


def read_collections(team_slug: str) -> list[dict] | None:
    """Return the cached raw collection list (server JSON shape), or None
    if we haven't mirrored collections yet."""
    try:
        conn = _connect(team_slug)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute("SELECT payload FROM mirror_collections").fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        try:
            out.append(json.loads(r["payload"]))
        except (json.JSONDecodeError, TypeError):
            continue
    return out or None


# ─── Write API ──────────────────────────────────────────────────────────


def write_snapshot(team_slug: str, *, assets: list[dict],
                   coll_map: dict[str, set[str]], etag: str | None) -> None:
    """Replace the snapshot with a fresh set of assets + collection
    membership. Atomic: a failure mid-write rolls back and leaves the
    previous snapshot in place."""
    try:
        conn = _connect(team_slug)
    except sqlite3.Error:
        return
    try:
        with conn:
            conn.execute("DELETE FROM mirror_assets")
            conn.execute("DELETE FROM mirror_coll_assets")
            for a in assets:
                aid = a.get("id")
                if not aid:
                    continue
                conn.execute(
                    "INSERT INTO mirror_assets(asset_id, db_id, payload) VALUES (?, ?, ?)",
                    (aid, _safe_int(a.get("dbId")), json.dumps(a)),
                )
            for coll_id, asset_ids in coll_map.items():
                for aid in asset_ids:
                    conn.execute(
                        "INSERT OR IGNORE INTO mirror_coll_assets(coll_id, asset_id) VALUES (?, ?)",
                        (coll_id, aid),
                    )
            _set_meta(conn, "etag", etag or "")
            _set_meta(conn, "last_synced_epoch", str(time.time()))
    finally:
        conn.close()


def write_collections(team_slug: str, raw_collections: list[dict]) -> None:
    """Replace the cached collection list (raw server shape — not panel shape)."""
    try:
        conn = _connect(team_slug)
    except sqlite3.Error:
        return
    try:
        with conn:
            conn.execute("DELETE FROM mirror_collections")
            for c in raw_collections or []:
                cid = c.get("id")
                if not cid:
                    continue
                conn.execute(
                    "INSERT INTO mirror_collections(coll_id, payload) VALUES (?, ?)",
                    (cid, json.dumps(c)),
                )
    finally:
        conn.close()


def clear(team_slug: str) -> None:
    """Drop the mirror — used by panel "Clear cache" or after a write
    that invalidates the cache (asset save/delete/move)."""
    try:
        conn = _connect(team_slug)
    except sqlite3.Error:
        return
    try:
        with conn:
            conn.execute("DELETE FROM mirror_assets")
            conn.execute("DELETE FROM mirror_collections")
            conn.execute("DELETE FROM mirror_coll_assets")
            conn.execute("DELETE FROM mirror_meta")
    finally:
        conn.close()


# ─── Helpers ────────────────────────────────────────────────────────────


def _safe_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO mirror_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
