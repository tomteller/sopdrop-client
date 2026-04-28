"""
Disk-backed LRU cache for HTTP-fetched thumbnails.

Used by the Houdini panel (Phase 3+) to avoid re-fetching team-library
thumbnails over the network. Server sets `Cache-Control: immutable` on
hash-addressed thumbnail URLs, so once cached, a thumbnail never needs to
be re-validated.

Layout:
    ~/.sopdrop/cache/thumbnails/
      <sha256(url)>.bin
      <sha256(url)>.bin
      ...

The on-disk filename is the sha256 of the source URL, so:
  - We never depend on filesystem path-safety of the source URL
  - Identical URLs from different sessions hit the same cache entry
  - Easy to garbage-collect by mtime when over budget

The cache stores raw bytes — decoding to QPixmap is the panel's job and
must happen on a worker thread, not here. This module never imports Qt.
"""

from __future__ import annotations

import hashlib
import os
import socket
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .api import _ssl_urlopen
from .config import get_cache_dir

DEFAULT_MAX_BYTES = 500 * 1024 * 1024  # 500 MB
DEFAULT_TIMEOUT = 10  # seconds


def _key_for_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class ThumbnailCache:
    """Thread-safe disk LRU. Reads/writes are atomic via tempfile + replace.

    Concurrency model:
      - `get_bytes()` and `fetch()` may be called from worker threads.
      - The cache directory is shared across processes (multiple Houdini
        sessions on one machine) — writes use atomic rename so concurrent
        fetches of the same URL produce a valid file in the end.
      - Eviction is lock-protected to avoid two threads picking the same
        victim and racing on unlink.
    """

    def __init__(self, *, max_bytes: int = DEFAULT_MAX_BYTES,
                 cache_dir: Path | None = None,
                 timeout: float = DEFAULT_TIMEOUT):
        self.max_bytes = max(1024 * 1024, int(max_bytes))  # 1 MB floor sanity
        self.timeout = timeout
        self._dir = (cache_dir or (get_cache_dir() / "thumbnails")).resolve()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._evict_lock = threading.Lock()

    # ── Internal ────────────────────────────────────────────────────────

    def _path_for(self, url: str) -> Path:
        return self._dir / f"{_key_for_url(url)}.bin"

    def _touch(self, path: Path) -> None:
        """Bump mtime so the LRU sees this file as recently used."""
        try:
            now = time.time()
            os.utime(path, (now, now))
        except OSError:
            pass

    def _atomic_write(self, path: Path, data: bytes) -> None:
        tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}")
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    # ── Public API ──────────────────────────────────────────────────────

    def get_bytes(self, url: str) -> bytes | None:
        """Return cached bytes for `url`, or None if not cached."""
        path = self._path_for(url)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            return None
        except OSError:
            return None
        self._touch(path)
        return data

    def put_bytes(self, url: str, data: bytes) -> None:
        """Write bytes to the cache. Atomic; safe under concurrent writers."""
        if not data:
            return
        path = self._path_for(url)
        self._atomic_write(path, data)
        # Evict opportunistically — cheap when under budget, blocks new
        # writes only when over.
        self._maybe_evict()

    def fetch(self, url: str) -> bytes | None:
        """Cache hit → return bytes. Miss → HTTP GET, store, return bytes.

        Returns None on network/HTTP failure (caller falls back to a
        placeholder). Never raises — thumbnails are best-effort.
        """
        cached = self.get_bytes(url)
        if cached is not None:
            return cached
        data = self._http_fetch(url)
        if data is None:
            return None
        try:
            self.put_bytes(url, data)
        except OSError:
            # Disk full / permissions — still return the bytes so the
            # caller can render this session.
            pass
        return data

    def _http_fetch(self, url: str) -> bytes | None:
        try:
            req = Request(url, headers={"User-Agent": "sopdrop-client/0.1.2"}, method="GET")
            response = _ssl_urlopen(req, timeout=self.timeout)
            return response.read()
        except (HTTPError, URLError, socket.timeout, ConnectionError, OSError):
            return None

    # ── Eviction ────────────────────────────────────────────────────────

    def total_bytes(self) -> int:
        try:
            return sum(p.stat().st_size for p in self._dir.iterdir() if p.is_file())
        except FileNotFoundError:
            return 0

    def _maybe_evict(self) -> None:
        # Avoid scanning the directory on every put — only when we plausibly
        # exceeded budget. Cheap probe first; full scan under lock.
        try:
            total = self.total_bytes()
        except OSError:
            return
        if total <= self.max_bytes:
            return
        with self._evict_lock:
            self._evict_to_target(self.max_bytes // 10 * 9)  # drop to 90%

    def _evict_to_target(self, target_bytes: int) -> None:
        try:
            files = [p for p in self._dir.iterdir() if p.is_file()]
        except FileNotFoundError:
            return
        # Sort oldest-mtime first so we evict LRU
        try:
            files.sort(key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        running = sum(self._safe_size(p) for p in files)
        for p in files:
            if running <= target_bytes:
                return
            sz = self._safe_size(p)
            try:
                p.unlink()
                running -= sz
            except OSError:
                continue

    @staticmethod
    def _safe_size(p: Path) -> int:
        try:
            return p.stat().st_size
        except OSError:
            return 0

    def clear(self) -> None:
        """Wipe the entire cache. For settings UI / tests."""
        with self._evict_lock:
            try:
                for p in self._dir.iterdir():
                    if p.is_file():
                        try:
                            p.unlink()
                        except OSError:
                            pass
            except FileNotFoundError:
                pass


# ─── Module-level singleton ─────────────────────────────────────────────
#
# Most callers want a single shared cache for the process. Use this unless
# you need a custom cache_dir or size for tests.

_default_cache: ThumbnailCache | None = None
_default_cache_lock = threading.Lock()


def get_default_cache() -> ThumbnailCache:
    global _default_cache
    if _default_cache is None:
        with _default_cache_lock:
            if _default_cache is None:
                _default_cache = ThumbnailCache()
    return _default_cache
