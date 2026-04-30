# Library System

Personal library is always local SQLite. The team library has **two modes**:

- **`nas`** (default): legacy shared SQLite file on a NAS share. Hits unrecoverable lock contention with more than 2–3 concurrent writers; fine for tiny teams, breaks under real production load.
- **`http`**: talks to a self-hosted `sopdrop-server` over the LAN (see [`deploy/onprem`](../deploy/onprem)). Postgres replaces SQLite; reads/writes return the same dict shapes via `_team_http.py`. This is the recommended mode for any team of more than 2–3 artists.

For the full on-prem deployment story (architecture, auth modes, schema, recovery), see **[on-prem.md](on-prem.md)**. This doc focuses on the library data model and the dispatch contract between SQLite and HTTP modes.

## Switching modes

The easiest path is the **Settings dialog** in the Houdini panel — it has a mode toggle in the TEAM LIBRARY section, with conditional fields for each mode. See [houdini-panel.md](houdini-panel.md#settings-dialog) for the UI.

To set the mode programmatically or by editing `~/.sopdrop/config.json` directly:

```json
{
  "active_library": "team",
  "team_library_mode": "http",
  "team_slug": "your-team",
  "server_url": "http://sopdrop.lan:4800",
  "local_only": true
}
```

```python
import sopdrop, sopdrop.config as cfg
sopdrop.set_server_url("http://sopdrop.lan:4800")
cfg.set_team_library_mode("http")
cfg.set_team_slug("your-team")
cfg.set_active_library("team")
# `local_only: true` activates trust-LAN auth on the client (no token,
# identity from workstation OS username via X-Sopdrop-User header).
# Requires TRUST_LAN_AUTH=true on the server. See on-prem.md.
```

## Architecture

```
~/.sopdrop/library/           (personal library, always SQLite)
├── library.db                SQLite database (schema below)
├── assets/                   All .sopdrop/.hda files
│   ├── {uuid}.sopdrop        Current package (always latest)
│   ├── {uuid}_v1.0.0.sopdrop Version snapshot (immutable)
│   ├── {uuid}.hda            Digital asset binary
│   └── ...
├── thumbnails/
│   └── {uuid}.png
└── trash/                    Soft-deleted files (auto-purge after 30 days)

/shared/team/library/         (team library, NAS mode — legacy)
└── (same structure)

~/.sopdrop/cache/thumbnails/  Disk LRU for HTTP-fetched thumbnails (500 MB cap)
                              Used when team_library_mode = http
```

## HTTP team library data flow

When `active_library = "team"` and `team_library_mode = "http"`, every panel read/write routes through `_team_http.py` instead of touching SQLite:

- **Reads** (`get_all_assets_cached`, `search_assets`, `list_collections`, `get_all_tags`, `get_library_stats`, `get_recent_assets`, `get_frequent_assets`, `list_trashed_assets`): `GET /api/v1/teams/<slug>/library` and friends, paginated, ETag-cached in-process. A panel reopen with no library changes does one round-trip for revalidation and reuses the cached body — no JSON parse, no SQL on the server.
- **Writes** (`save_asset`, `save_hda`, `update_asset`, `delete_asset`, `update_asset_thumbnail`, `record_asset_use`, `create/update/delete_collection`, `restore_asset`, `purge_asset`): existing `/api/v1/assets/*` and `/teams/<slug>/library/*` routes. The server's `canWriteAsset()` allows any team member to mutate team-owned assets. Purge is owner/admin-only.
- **Paste** (`load_asset_package`): `GET /api/v1/assets/<owner/name>/download/<version>` returns the .sopdrop JSON; the panel proceeds normally.
- **Thumbnails**: server returns `thumbnailUrl` in the asset row. The panel fetches via a 4-thread `QThreadPool` (`_HttpThumbnailDispatcher` in `sopdrop_library_panel.py`) with disk LRU at `~/.sopdrop/cache/thumbnails/`. The server sets `Cache-Control: public, max-age=31536000, immutable` on `/library/*` so warm clients make zero network calls.
- **Auth**: every request carries either a Bearer token (`Authorization: Bearer sdrop_…`) or, in trust-LAN mode (`local_only=true` + `team_library_mode=http`), the workstation username (`X-Sopdrop-User: alice`). Server picks one based on its `TRUST_LAN_AUTH` env var. Both flow through the same `_auth_headers()` helper in `http_library.py` — call sites don't care which is in use.

The shape contract: every dict returned from `_team_http.py` matches the SQLite-row shape exactly, plus two HTTP-only keys: `_thumbnail_url` (absolute URL) and `_download_url` (absolute URL). The panel widget tree was not modified for the swap; only `AssetCardWidget._load_thumbnail` was extended to dispatch to the HTTP loader when `_thumbnail_url` is present.

## Functions panel-callable in HTTP mode

Every panel-callable function in `library.py` either has an HTTP shim in `_team_http.py` or logs a clean "not yet supported in HTTP mode" message and no-ops. None will crash with `NoneType` from `get_db()` returning None.

Shimmed (full HTTP support):
- `get_all_assets_cached`, `search_assets`, `get_asset`, `load_asset_package`
- `list_collections`, `get_collection_tree`, `get_collection`
- `get_all_tags`, `get_all_artists`, `get_library_stats`
- `get_recent_assets`, `get_frequent_assets`, `get_asset_collections`
- `record_asset_use`, `create_collection`, `update_collection`, `delete_collection`
- `update_asset`, `delete_asset`, `update_asset_thumbnail`
- `save_asset`, `save_hda`
- `add_asset_to_collection`, `remove_asset_from_collection`, `get_collection_assets`
- `toggle_favorite`
- `list_trashed_assets`, `restore_asset`, `purge_asset`, `empty_trash`

Not yet supported (logs a warning, returns None / False):
- `save_asset_version`, `revert_to_version` — versioning beyond v1.0.0 is the agreed deferred feature
- `update_asset_package` — re-uploading a package in place; use save-as-new instead

## Database Schema

### `library_assets` — Core asset table

```sql
id TEXT PRIMARY KEY,               -- UUID
name TEXT NOT NULL,
description TEXT,
context TEXT NOT NULL,             -- sop, lop, vop, dop, cop, top, chop, obj, out, vex, path, curves
asset_type TEXT DEFAULT 'node',    -- node, hda, vex, path
file_path TEXT NOT NULL,           -- Relative path: assets/{uuid}.sopdrop
file_hash TEXT,                    -- SHA256
file_size INTEGER,
thumbnail_path TEXT,               -- Relative: {uuid}.png
icon TEXT,                         -- Houdini icon name (SOP_scatter)
slug TEXT,                         -- Shareable identifier (my-scatter-tool)

-- Node metadata
node_count INTEGER DEFAULT 0,
node_types TEXT,                   -- JSON array
node_names TEXT,                   -- JSON array
tags TEXT,                         -- JSON array
houdini_version TEXT,
has_hda_dependencies INTEGER DEFAULT 0,
dependencies TEXT,                 -- JSON array
metadata TEXT,                     -- Full package metadata as JSON

-- HDA-specific
hda_type_name TEXT,
hda_type_label TEXT,
hda_version TEXT,
hda_category TEXT,

-- Usage tracking
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL,
last_used_at TEXT,
use_count INTEGER DEFAULT 0,
is_favorite INTEGER DEFAULT 0,
created_by TEXT,                   -- OS username

-- Cloud sync
remote_slug TEXT,                  -- user/asset-name if published
remote_version TEXT,
sync_status TEXT DEFAULT 'local_only',  -- local_only | synced | modified | syncing
synced_at TEXT,

-- Soft delete
deleted_at TEXT                    -- ISO timestamp when trashed, NULL = active
```

### `collections` — Folders for organization

```sql
id TEXT PRIMARY KEY,
name TEXT NOT NULL,
description TEXT,
color TEXT DEFAULT '#666666',
icon TEXT DEFAULT 'folder',
parent_id TEXT REFERENCES collections(id) ON DELETE SET NULL,
sort_order INTEGER DEFAULT 0,
source TEXT DEFAULT 'local',       -- 'local' or 'cloud'
remote_id TEXT,                    -- Cloud folder ID if synced
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL
```

### `collection_assets` — Many-to-many

```sql
collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
asset_id TEXT NOT NULL REFERENCES library_assets(id) ON DELETE CASCADE,
sort_order INTEGER DEFAULT 0,
added_at TEXT NOT NULL,
PRIMARY KEY (collection_id, asset_id)
```

### `asset_tags` — Tag index

```sql
asset_id TEXT NOT NULL REFERENCES library_assets(id) ON DELETE CASCADE,
tag TEXT NOT NULL,
PRIMARY KEY (asset_id, tag)
```

### `asset_versions` — Version history

```sql
id TEXT PRIMARY KEY,
asset_id TEXT NOT NULL REFERENCES library_assets(id) ON DELETE CASCADE,
version TEXT NOT NULL,             -- Semver (1.0.0, 1.1.0)
file_path TEXT,                    -- Snapshot: {asset_id}_v{version}.sopdrop
file_hash TEXT,
file_size INTEGER,
node_count INTEGER,
changelog TEXT,
created_at TEXT DEFAULT (datetime('now')),
UNIQUE(asset_id, version)
```

### `filter_presets`, `user_prefs`, `library_meta` — Support tables

```sql
-- Saved search filters
filter_presets (id, name, description, filters JSON, sort_order, created_at)

-- User preferences (key-value)
user_prefs (key TEXT PRIMARY KEY, value TEXT)

-- Library identity (team_name, team_slug)
library_meta (key TEXT PRIMARY KEY, value TEXT)
```

### Full-Text Search (FTS5)

```sql
CREATE VIRTUAL TABLE assets_fts USING fts5(
    name, description, tags, node_types,
    content=library_assets, content_rowid=rowid
);
```

Kept in sync via INSERT/UPDATE/DELETE triggers on `library_assets`.

## Asset Types & Contexts

| Context | Color | Description |
|---------|-------|-------------|
| `sop` | Blue | SOP network nodes |
| `lop` | Orange | LOP/USD network nodes |
| `obj` | Gold | OBJ-level nodes |
| `vop` | Purple | VOP shader nodes |
| `dop` | Red | DOP simulation nodes |
| `cop` | Cyan | COP2 compositing nodes |
| `top` | Green | TOP/PDG nodes |
| `chop` | Pink | CHOP channel nodes |
| `out` | Indigo | ROP render output nodes |
| `vex` | Gold | VEX code snippets (no Houdini serialization) |
| `path` | Teal | File path references (HDRI, texture, geo) |
| `curves` | Amber | Animation keyframe curves |

### Special Asset Types

- **VEX snippets** (`context='vex'`): Raw VEX code saved as `sopdrop-vex-v1` package. Pasted to clipboard, not into network. Skipped in TAB menu.
- **Path references** (`context='path'`): File path saved as `sopdrop-path-v1` package. Stores the path string, copies to clipboard on paste.
- **Curves** (`context='curves'`): Animation keyframes. Uses same V2 cpio export/import as regular nodes. Extra metadata: channel names, keyframe counts, frame range.
- **HDAs** (`asset_type='hda'`): Binary `.hda` file stored directly. Installed via `hou.hda.installFile()` on paste.

## Key Operations

### Save Asset

```
save_asset(name, context, package_data, ...)
  1. ensure_library_dirs()
  2. Generate UUID, write package JSON atomically
  3. SHA256 checksum the JSON
  4. Save thumbnail if provided
  5. Generate unique slug (my-tool, my-tool-2, my-tool-3...)
  6. INSERT into library_assets
  7. Index tags in asset_tags
  8. Add to collections
  9. Commit + trigger TAB menu regeneration
```

### Version Up

```
save_asset_version(asset_id, package_data, ...)
  1. If first version-up: snapshot current file as v1.0.0
  2. Write new package JSON to main file
  3. Increment version: 1.0.0 → 1.1.0 → 1.2.0
  4. Write version snapshot: {uuid}_v{version}.sopdrop
  5. INSERT into asset_versions
  6. If cloud-synced: mark as modified
```

### Soft Delete / Trash

```
delete_asset(asset_id)
  1. Move .sopdrop + .png to trash/ directory
  2. Set deleted_at = now
  3. Remove from all collections
  4. Trigger menu regeneration

restore_asset(asset_id)
  1. Move files back from trash/
  2. Clear deleted_at
  3. Regenerate slug (avoid collisions)

Auto-purge: get_db() calls _auto_purge_trash() — deletes trash > 30 days old
```

### Search

```
search_assets(query, context, tags, collection_id, sort_by, ...)
  - Text: LIKE on name, description, tags
  - Tags: JOIN on asset_tags (all must match)
  - Collection: JOIN on collection_assets
  - Always filters: deleted_at IS NULL
  - Sort: updated_at, created_at, name, use_count, last_used_at, node_count
```

### Usage Tracking

`record_asset_use(asset_id)` increments `use_count` and sets `last_used_at` on every paste. Writes to local mirror only (per-user tracking, not shared state). Wrapped in try/except — never fails the parent operation.

## Library Types

### Personal Library

- Path: `~/.sopdrop/library/` (from `get_library_path()`)
- SQLite with WAL journal mode for performance
- Single-user, no concurrency concerns
- `PRAGMA busy_timeout = 5000`

### Team Library

- Path: configurable via `team_library_path` in `~/.sopdrop/config.json`
- Typically a shared network drive (NAS)
- NAS DB uses NO WAL mode (network drives can't handle mmap)
- `PRAGMA mmap_size = 0` — prevents segfaults on network filesystems
- `PRAGMA busy_timeout = 15000` — 15-second wait for concurrent access
- Identity stored in `library_meta` table (`team_name`, `team_slug`)

### Team Mirror (Local Cache)

Every collection click on a NAS-hosted team library triggers SQLite queries over the network (~2s latency each). The local mirror eliminates this:

```
NAS (source of truth)                Local mirror (fast reads)
/shared/team/library/                ~/.sopdrop/team_mirror/{hash}/
├── library.db  ──backup()──────►    ├── library.db  (WAL-enabled!)
├── assets/     (NOT mirrored)       └── thumbnails/ (lazy-cached)
├── thumbnails/ ──lazy copy──────►
└── trash/      (NOT mirrored)
```

**How it works:**

- **Reads** (browse, search, list collections) hit the local mirror — instant, WAL-enabled
- **Writes** (save, delete, update) go to the NAS DB via `@_writes_to_nas` decorator, then the mirror auto-refreshes
- **Asset files** (`.sopdrop`, `.hda`) stay on NAS — only read on paste/install, not during browsing
- **Thumbnails** are lazy-cached: copied from NAS to mirror on first view
- **Mirror path**: `~/.sopdrop/team_mirror/{sha256(nas_path)[:12]}/` — supports multiple teams

**Mirror refresh triggers:**

1. First `get_db()` call for team library (bootstrap)
2. Ctrl+R / manual refresh (via `_LibraryWorker` background thread)
3. After every write (`_nas_write_session` auto-refreshes on exit)
4. Library switch to team (`_refresh_assets` worker does it)

**Change detection:** `os.stat(nas_db).st_mtime` compared to stored `_nas_db_mtime`. Unchanged = skip copy.

**Stale mirror fallback:** If NAS becomes unavailable after initial mirror, reads continue from the (potentially stale) local copy. The UI shows a warning toast: "Team drive unavailable — showing cached data".

**Background loading:** Mirror refresh + asset queries run on a `_LibraryWorker` QThread for team libraries so the panel stays responsive during the 2-30s NAS backup. See [houdini-panel.md](houdini-panel.md) for details.

### NAS Contention Mitigation

Multiple workstations accessing the NAS `library.db` simultaneously can cause `database is locked` errors. Mitigations:

- **Conditional schema**: `_get_nas_db()` and `get_db()` check if `library_assets` table exists before running `executescript(SCHEMA)` — avoids acquiring an exclusive write lock on every connection
- **High busy_timeout**: NAS connections use 15000ms; `detect_team_from_library()` uses 10000ms; `refresh_team_mirror()` source uses 15000ms
- **Local usage tracking**: `record_asset_use()` writes to local mirror only, not NAS
- **Diagnostic logging**: NAS connect time, mirror refresh time, and contention errors are printed to the Houdini console with `[Sopdrop]` prefix

### Library Switching

```python
switch_library("team")      # Closes current connection, opens team DB
switch_library("personal")  # Switches back
get_current_library_info()  # Returns {type, name, path, exists}
```

### Team Detection

`detect_team_from_library(path)` reads team identity from an existing library DB. Uses `try/finally` to always close the SQLite connection. Has `busy_timeout = 10000` to handle NAS contention.

## Cloud Sync

### Sync Status Flow

```
local_only → syncing → synced → modified → syncing → synced
                ↓
          (failure) → local_only  (via reset_syncing_status)
```

### Pull from Cloud

```
pull_from_cloud(slug, version, ...)
  1. client.install(slug@version) downloads package
  2. save_asset() stores locally
  3. Download thumbnail from cloud URL
  4. mark_asset_synced(asset_id, slug, version)
```

### Push to Cloud

```
push_to_cloud(asset_id)
  1. Load package from local file
  2. POST to /api/v1/drafts (creates draft)
  3. mark_asset_syncing(asset_id, draft_id)
  4. User completes publish in browser
  5. On completion: mark_asset_synced()
```

## File Writing Safety

All file writes use atomic operations:

```python
_atomic_write_text(path, content)   # Write to temp, os.replace()
_atomic_write_bytes(path, data)     # Same for binary
_atomic_copy(src, dst)              # Copy via temp file
```

If interrupted mid-write, the original file remains intact.

## JSON Field Handling

Columns storing JSON arrays (`node_types`, `node_names`, `tags`, `dependencies`, `metadata`) are stored as text in SQLite. On read, they're parsed back:

```python
for field in ('node_types', 'node_names', 'tags', 'dependencies', 'metadata'):
    if asset.get(field):
        asset[field] = json.loads(asset[field])
```

## Diagnostic Logging

Key library operations print to the Houdini console with `[Sopdrop]` prefix for troubleshooting:

| Operation | Log message |
|-----------|-------------|
| NAS DB connect | `Connecting to NAS DB: {path}` + elapsed time |
| Mirror refresh | `Refreshing team mirror from NAS...` + elapsed time |
| Mirror bootstrap | `Team mirror not found — bootstrapping from NAS...` |
| Team detection failure | `Could not read team metadata from {path} ({time}s): {error}` |
| Mirror refresh failure | `Mirror refresh failed ({time}s): {error}` |
