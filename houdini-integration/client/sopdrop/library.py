"""
Sopdrop Local Library Manager.

Manages a local SQLite database of saved node setups, HDAs, and collections.
Works offline - syncs manually with cloud when desired.

Directory structure:
~/.sopdrop/
├── library/
│   ├── library.db          # SQLite database
│   ├── assets/             # .sopdrop and .hda files
│   │   └── {uuid}.sopdrop
│   └── thumbnails/         # Preview images
│       └── {uuid}.png
"""

import os
import json
import uuid
import shutil
import sqlite3
import warnings
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from .config import get_config_dir, get_library_path, get_active_library


# ==============================================================================
# Directory Management
# ==============================================================================

def get_library_dir():
    """Get the library root directory (respects active library setting)."""
    return get_library_path()


def get_library_db_path():
    """Get the SQLite database path."""
    return get_library_dir() / "library.db"


def get_library_assets_dir():
    """Get the directory for stored asset files."""
    return get_library_dir() / "assets"


def get_library_thumbnails_dir():
    """Get the directory for thumbnail images."""
    return get_library_dir() / "thumbnails"


def ensure_library_dirs():
    """Ensure all library directories exist."""
    dirs = [
        get_library_dir(),
        get_library_assets_dir(),
        get_library_thumbnails_dir(),
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def get_current_library_info():
    """Get information about the currently active library."""
    active = get_active_library()
    lib_dir = get_library_dir()
    return {
        "type": active,
        "name": "Team Library" if active == "team" else "Personal Library",
        "path": str(lib_dir),
        "exists": lib_dir.exists(),
    }


# ==============================================================================
# License Detection
# ==============================================================================

def detect_houdini_license():
    """Detect the current Houdini license type.

    Returns one of: 'commercial', 'indie', 'education', 'apprentice', or None.
    """
    try:
        import hou
        category = hou.licenseCategory()
        license_map = {
            hou.licenseCategoryType.Commercial: 'commercial',
            hou.licenseCategoryType.Indie: 'indie',
            hou.licenseCategoryType.Education: 'education',
            hou.licenseCategoryType.Apprentice: 'apprentice',
        }
        # ApprenticeHD may exist on some versions
        if hasattr(hou.licenseCategoryType, 'ApprenticeHD'):
            license_map[hou.licenseCategoryType.ApprenticeHD] = 'apprentice'
        return license_map.get(category, 'commercial')
    except (ImportError, AttributeError):
        return None


# ==============================================================================
# Database Schema
# ==============================================================================

SCHEMA = """
-- Collections (folders/categories for organization)
CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    color TEXT DEFAULT '#666666',
    icon TEXT DEFAULT 'folder',
    parent_id TEXT REFERENCES collections(id) ON DELETE SET NULL,
    sort_order INTEGER DEFAULT 0,
    source TEXT DEFAULT 'local',  -- 'local' or 'cloud'
    remote_id TEXT,               -- Cloud collection ID if synced
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Local library assets
CREATE TABLE IF NOT EXISTS library_assets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    context TEXT NOT NULL,
    asset_type TEXT DEFAULT 'node',  -- 'node' or 'hda'
    file_path TEXT NOT NULL,
    file_hash TEXT,
    file_size INTEGER,
    thumbnail_path TEXT,
    icon TEXT,                -- Houdini icon name (e.g., 'SOP_scatter')

    -- Metadata
    node_count INTEGER DEFAULT 0,
    node_types TEXT,          -- JSON array
    node_names TEXT,          -- JSON array
    tags TEXT,                -- JSON array
    houdini_version TEXT,
    has_hda_dependencies INTEGER DEFAULT 0,
    dependencies TEXT,        -- JSON array
    metadata TEXT,            -- Full package metadata as JSON

    -- HDA-specific metadata
    hda_type_name TEXT,       -- e.g., 'myasset::1.0'
    hda_type_label TEXT,      -- Human-readable label
    hda_version TEXT,         -- HDA version string
    hda_category TEXT,        -- e.g., 'Sop', 'Object'

    -- Timestamps and usage
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT,
    use_count INTEGER DEFAULT 0,

    -- Sync tracking (optional cloud connection)
    remote_slug TEXT,         -- user/asset-name if published
    remote_version TEXT,      -- Synced version number
    sync_status TEXT DEFAULT 'local_only',  -- local_only, synced, modified, conflict
    synced_at TEXT
);

-- Collection membership (many-to-many)
CREATE TABLE IF NOT EXISTS collection_assets (
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES library_assets(id) ON DELETE CASCADE,
    sort_order INTEGER DEFAULT 0,
    added_at TEXT NOT NULL,
    PRIMARY KEY (collection_id, asset_id)
);

-- Asset tags index for fast filtering
CREATE TABLE IF NOT EXISTS asset_tags (
    asset_id TEXT NOT NULL REFERENCES library_assets(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (asset_id, tag)
);

-- Smart filter presets (saved searches)
CREATE TABLE IF NOT EXISTS filter_presets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    filters TEXT NOT NULL,    -- JSON object with filter criteria
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Asset version history
CREATE TABLE IF NOT EXISTS asset_versions (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES library_assets(id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    file_path TEXT,
    file_hash TEXT,
    file_size INTEGER,
    node_count INTEGER,
    changelog TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(asset_id, version)
);

CREATE INDEX IF NOT EXISTS idx_asset_versions_asset ON asset_versions(asset_id);

-- User preferences and state
CREATE TABLE IF NOT EXISTS user_prefs (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Library identity metadata (team name/slug stored in the DB itself)
CREATE TABLE IF NOT EXISTS library_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_assets_context ON library_assets(context);
CREATE INDEX IF NOT EXISTS idx_assets_name ON library_assets(name);
CREATE INDEX IF NOT EXISTS idx_assets_created ON library_assets(created_at);
CREATE INDEX IF NOT EXISTS idx_assets_last_used ON library_assets(last_used_at);
CREATE INDEX IF NOT EXISTS idx_assets_use_count ON library_assets(use_count);
CREATE INDEX IF NOT EXISTS idx_assets_remote_slug ON library_assets(remote_slug);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON asset_tags(tag);
CREATE INDEX IF NOT EXISTS idx_collections_parent ON collections(parent_id);
"""

# FTS5 schema separated so a failure doesn't block core functionality.
# FTS5 uses memory-mapped I/O which can crash on network/shared drives.
FTS_SCHEMA = """
-- Full-text search (SQLite FTS5)
CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(
    name,
    description,
    tags,
    node_types,
    content=library_assets,
    content_rowid=rowid
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS assets_fts_insert AFTER INSERT ON library_assets BEGIN
    INSERT INTO assets_fts(rowid, name, description, tags, node_types)
    VALUES (NEW.rowid, NEW.name, NEW.description, NEW.tags, NEW.node_types);
END;

CREATE TRIGGER IF NOT EXISTS assets_fts_delete AFTER DELETE ON library_assets BEGIN
    INSERT INTO assets_fts(assets_fts, rowid, name, description, tags, node_types)
    VALUES ('delete', OLD.rowid, OLD.name, OLD.description, OLD.tags, OLD.node_types);
END;

CREATE TRIGGER IF NOT EXISTS assets_fts_update AFTER UPDATE ON library_assets BEGIN
    INSERT INTO assets_fts(assets_fts, rowid, name, description, tags, node_types)
    VALUES ('delete', OLD.rowid, OLD.name, OLD.description, OLD.tags, OLD.node_types);
    INSERT INTO assets_fts(rowid, name, description, tags, node_types)
    VALUES (NEW.rowid, NEW.name, NEW.description, NEW.tags, NEW.node_types);
END;
"""


# ==============================================================================
# Database Connection
# ==============================================================================

# Track connection per library path to support switching
_connections = {}
_current_db_path = None


def get_db():
    """Get or create database connection for the active library."""
    global _connections, _current_db_path

    ensure_library_dirs()
    db_path = str(get_library_db_path())

    # If we switched libraries, we need a new connection
    if db_path != _current_db_path:
        _current_db_path = db_path

    # Get or create connection for this path
    if db_path not in _connections:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # Disable memory-mapped I/O — prevents segfaults on network/shared
        # drives (team library folders) where mmap behaves unpredictably.
        conn.execute("PRAGMA mmap_size = 0")
        # Wait up to 5 s on a locked DB instead of failing immediately
        # (team libraries may have concurrent access from multiple users).
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.executescript(SCHEMA)

        # FTS5 uses mmap internally and can crash on network filesystems.
        # Create it separately so a failure doesn't block core functionality.
        try:
            conn.executescript(FTS_SCHEMA)
        except Exception as e:
            print(f"[Sopdrop] FTS5 unavailable for {db_path}: {e}")

        # Run migrations for existing databases
        _run_migrations(conn)

        conn.commit()
        _connections[db_path] = conn

    return _connections[db_path]


def _run_migrations(conn):
    """Run database migrations for schema updates."""
    # Add source and remote_id columns to collections table if they don't exist
    try:
        cursor = conn.execute("PRAGMA table_info(collections)")
        columns = {row[1] for row in cursor.fetchall()}

        if 'source' not in columns:
            conn.execute("ALTER TABLE collections ADD COLUMN source TEXT DEFAULT 'local'")

        if 'remote_id' not in columns:
            conn.execute("ALTER TABLE collections ADD COLUMN remote_id TEXT")

    except Exception as e:
        print(f"[Sopdrop] Migration warning (collections): {e}")

    # Add HDA columns to library_assets table if they don't exist
    try:
        cursor = conn.execute("PRAGMA table_info(library_assets)")
        columns = {row[1] for row in cursor.fetchall()}

        if 'asset_type' not in columns:
            conn.execute("ALTER TABLE library_assets ADD COLUMN asset_type TEXT DEFAULT 'node'")

        if 'hda_type_name' not in columns:
            conn.execute("ALTER TABLE library_assets ADD COLUMN hda_type_name TEXT")

        if 'hda_type_label' not in columns:
            conn.execute("ALTER TABLE library_assets ADD COLUMN hda_type_label TEXT")

        if 'hda_version' not in columns:
            conn.execute("ALTER TABLE library_assets ADD COLUMN hda_version TEXT")

        if 'hda_category' not in columns:
            conn.execute("ALTER TABLE library_assets ADD COLUMN hda_category TEXT")

        if 'icon' not in columns:
            conn.execute("ALTER TABLE library_assets ADD COLUMN icon TEXT")

        if 'license_type' not in columns:
            conn.execute("ALTER TABLE library_assets ADD COLUMN license_type TEXT")

    except Exception as e:
        print(f"[Sopdrop] Migration warning (library_assets): {e}")

    # Add library_meta table if it doesn't exist (for existing DBs)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS library_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
    except Exception as e:
        print(f"[Sopdrop] Migration warning (library_meta): {e}")


def close_db():
    """Close all database connections."""
    global _connections, _current_db_path
    for conn in _connections.values():
        try:
            conn.close()
        except Exception:
            pass
    _connections = {}
    _current_db_path = None


def get_library_meta(key: str, db=None) -> Optional[str]:
    """Get a metadata value from the library_meta table."""
    conn = db or get_db()
    row = conn.execute("SELECT value FROM library_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_library_meta(key: str, value: str, db=None):
    """Set a metadata value in the library_meta table."""
    conn = db or get_db()
    conn.execute(
        "INSERT OR REPLACE INTO library_meta (key, value) VALUES (?, ?)",
        (key, value)
    )
    conn.commit()


def detect_team_from_library(path: str) -> Optional[Dict[str, str]]:
    """
    Detect team identity from an existing library database.

    Opens the DB at the given path (expects a 'library/' subdirectory with library.db),
    reads team_name and team_slug from library_meta.

    Returns dict with 'team_name' and 'team_slug', or None if not found.
    """
    from pathlib import Path

    db_path = Path(path) / "library" / "library.db"
    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Check if library_meta table exists
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='library_meta'"
        ).fetchone()
        if not tables:
            conn.close()
            return None

        team_name = None
        team_slug = None

        row = conn.execute("SELECT value FROM library_meta WHERE key = 'team_name'").fetchone()
        if row:
            team_name = row[0]

        row = conn.execute("SELECT value FROM library_meta WHERE key = 'team_slug'").fetchone()
        if row:
            team_slug = row[0]

        conn.close()

        if team_name or team_slug:
            return {
                'team_name': team_name,
                'team_slug': team_slug,
            }
    except Exception as e:
        print(f"[Sopdrop] Could not read team metadata from {db_path}: {e}")

    return None


def switch_library(library_type):
    """
    Switch to a different library (personal or team).

    This closes the current connection and updates the active library setting.
    """
    from .config import set_active_library, get_team_library_path

    if library_type == "team":
        team_path = get_team_library_path()
        if not team_path:
            raise ValueError("Team library path not configured. Set it in Settings first.")

    set_active_library(library_type)
    # Trigger menu regeneration for new library
    _trigger_menu_regenerate()

    return get_current_library_info()


def dict_from_row(row):
    """Convert sqlite3.Row to dict."""
    if row is None:
        return None
    return dict(zip(row.keys(), row))


# ==============================================================================
# Collection Operations
# ==============================================================================

def create_collection(
    name: str,
    description: str = "",
    color: str = "#666666",
    icon: str = "folder",
    parent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new collection."""
    db = get_db()
    collection_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # Get next sort order
    cursor = db.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM collections WHERE parent_id IS ?"
        if parent_id else
        "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM collections WHERE parent_id IS NULL",
        (parent_id,) if parent_id else ()
    )
    sort_order = cursor.fetchone()[0]

    db.execute("""
        INSERT INTO collections (id, name, description, color, icon, parent_id, sort_order, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (collection_id, name, description, color, icon, parent_id, sort_order, now, now))
    db.commit()

    return get_collection(collection_id)


def get_collection(collection_id: str) -> Optional[Dict[str, Any]]:
    """Get a collection by ID."""
    db = get_db()
    row = db.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
    return dict_from_row(row)


def list_collections(parent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List collections, optionally filtered by parent."""
    db = get_db()
    if parent_id is None:
        rows = db.execute(
            "SELECT * FROM collections WHERE parent_id IS NULL ORDER BY sort_order, name"
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM collections WHERE parent_id = ? ORDER BY sort_order, name",
            (parent_id,)
        ).fetchall()
    return [dict_from_row(r) for r in rows]


def get_collection_tree() -> List[Dict[str, Any]]:
    """Get full collection hierarchy as nested structure."""
    all_collections = [dict_from_row(r) for r in get_db().execute(
        "SELECT * FROM collections ORDER BY sort_order, name"
    ).fetchall()]

    # Build tree
    by_id = {c['id']: {**c, 'children': []} for c in all_collections}
    root = []

    for c in all_collections:
        node = by_id[c['id']]
        if c['parent_id'] and c['parent_id'] in by_id:
            by_id[c['parent_id']]['children'].append(node)
        else:
            root.append(node)

    return root


def update_collection(collection_id: str, **kwargs) -> Optional[Dict[str, Any]]:
    """Update a collection's properties."""
    db = get_db()
    allowed = {'name', 'description', 'color', 'icon', 'parent_id', 'sort_order'}
    updates = {k: v for k, v in kwargs.items() if k in allowed}

    if not updates:
        return get_collection(collection_id)

    updates['updated_at'] = datetime.utcnow().isoformat()

    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [collection_id]

    db.execute(f"UPDATE collections SET {set_clause} WHERE id = ?", values)
    db.commit()

    return get_collection(collection_id)


def delete_collection(collection_id: str, recursive: bool = False):
    """Delete a collection. If recursive, delete children too."""
    db = get_db()

    if recursive:
        # Get all descendant IDs
        def get_descendants(cid):
            children = db.execute(
                "SELECT id FROM collections WHERE parent_id = ?", (cid,)
            ).fetchall()
            ids = [c[0] for c in children]
            for child_id in list(ids):
                ids.extend(get_descendants(child_id))
            return ids

        all_ids = [collection_id] + get_descendants(collection_id)
        placeholders = ",".join("?" * len(all_ids))
        db.execute(f"DELETE FROM collections WHERE id IN ({placeholders})", all_ids)
    else:
        # Move children to parent's parent
        parent = db.execute(
            "SELECT parent_id FROM collections WHERE id = ?", (collection_id,)
        ).fetchone()
        new_parent = parent[0] if parent else None

        db.execute(
            "UPDATE collections SET parent_id = ? WHERE parent_id = ?",
            (new_parent, collection_id)
        )
        db.execute("DELETE FROM collections WHERE id = ?", (collection_id,))

    db.commit()


# ==============================================================================
# Asset Operations
# ==============================================================================

def save_asset(
    name: str,
    context: str,
    package_data: Dict[str, Any],
    description: str = "",
    tags: List[str] = None,
    thumbnail_data: bytes = None,
    collection_ids: List[str] = None,
    icon: str = None,
) -> Dict[str, Any]:
    """
    Save an asset to the local library.

    Args:
        name: Display name for the asset
        context: Houdini context (sop, lop, etc.)
        package_data: Full package dict from export
        description: Optional description
        tags: List of tags for organization
        thumbnail_data: PNG image bytes for thumbnail
        collection_ids: Collections to add this asset to
        icon: Houdini icon name (e.g., 'SOP_scatter')

    Returns:
        The saved asset record
    """
    ensure_library_dirs()
    db = get_db()
    asset_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # Save the package file
    file_name = f"{asset_id}.sopdrop"
    file_path = get_library_assets_dir() / file_name
    package_json = json.dumps(package_data, separators=(',', ':'))
    file_path.write_text(package_json)

    # Calculate hash and size
    import hashlib
    file_hash = hashlib.sha256(package_json.encode()).hexdigest()
    file_size = len(package_json)

    # Save thumbnail if provided
    thumbnail_path = None
    if thumbnail_data:
        thumb_name = f"{asset_id}.png"
        thumb_file = get_library_thumbnails_dir() / thumb_name
        thumb_file.write_bytes(thumbnail_data)
        thumbnail_path = thumb_name

    # Extract metadata
    metadata = package_data.get('metadata', {})
    tags = tags or []

    # Insert asset record
    db.execute("""
        INSERT INTO library_assets (
            id, name, description, context, file_path, file_hash, file_size,
            thumbnail_path, icon, node_count, node_types, node_names, tags,
            houdini_version, has_hda_dependencies, dependencies, metadata,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        asset_id, name, description, context, file_name, file_hash, file_size,
        thumbnail_path, icon,
        metadata.get('node_count', 0),
        json.dumps(metadata.get('node_types', [])),
        json.dumps(metadata.get('node_names', [])),
        json.dumps(tags),
        package_data.get('houdini_version', ''),
        1 if metadata.get('has_hda_dependencies') else 0,
        json.dumps(package_data.get('dependencies', [])),
        json.dumps(metadata),
        now, now
    ))

    # Insert tags for indexing
    for tag in tags:
        db.execute(
            "INSERT OR IGNORE INTO asset_tags (asset_id, tag) VALUES (?, ?)",
            (asset_id, tag.lower())
        )

    # Add to collections
    if collection_ids:
        for coll_id in collection_ids:
            add_asset_to_collection(asset_id, coll_id)

    db.commit()

    # Trigger menu regeneration
    _trigger_menu_regenerate()

    return get_asset(asset_id)


def save_hda(
    name: str,
    hda_info: Dict[str, Any],
    description: str = "",
    tags: List[str] = None,
    thumbnail_data: bytes = None,
    collection_ids: List[str] = None,
    icon: str = None,
) -> Dict[str, Any]:
    """
    Save an HDA to the local library.

    Args:
        name: Display name for the HDA
        hda_info: Dict from export.detect_publishable_hda()
        description: Optional description
        tags: List of tags for organization
        thumbnail_data: PNG image bytes for thumbnail
        collection_ids: Collections to add this asset to
        icon: Houdini icon name (e.g., 'SOP_scatter')

    Returns:
        The saved asset record
    """
    import shutil

    ensure_library_dirs()
    db = get_db()
    asset_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # Copy the HDA file to library
    source_path = hda_info['library_path']
    file_name = f"{asset_id}.hda"
    dest_path = get_library_assets_dir() / file_name

    shutil.copy2(source_path, dest_path)

    # Calculate hash and size
    import hashlib
    with open(dest_path, 'rb') as f:
        file_data = f.read()
    file_hash = hashlib.sha256(file_data).hexdigest()
    file_size = len(file_data)

    # Save thumbnail if provided
    thumbnail_path = None
    if thumbnail_data:
        thumb_name = f"{asset_id}.png"
        thumb_file = get_library_thumbnails_dir() / thumb_name
        thumb_file.write_bytes(thumbnail_data)
        thumbnail_path = thumb_name

    # Get context from category
    category = hda_info.get('category', 'Sop').lower()
    context_map = {
        'sop': 'sop', 'object': 'obj', 'vop': 'vop', 'dop': 'dop',
        'cop2': 'cop', 'top': 'top', 'lop': 'lop', 'chop': 'chop',
        'shop': 'shop', 'rop': 'out', 'driver': 'out',
    }
    context = context_map.get(category, category)

    tags = tags or []

    # Detect license type for HDA compatibility tracking
    license_type = detect_houdini_license()

    # Insert asset record with HDA-specific fields
    db.execute("""
        INSERT INTO library_assets (
            id, name, description, context, asset_type, file_path, file_hash, file_size,
            thumbnail_path, icon, node_count, node_types, node_names, tags,
            houdini_version, has_hda_dependencies, dependencies, metadata,
            hda_type_name, hda_type_label, hda_version, hda_category,
            license_type, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        asset_id, name, description, context, 'hda', file_name, file_hash, file_size,
        thumbnail_path, icon,
        1,  # node_count = 1 for HDA
        json.dumps([hda_info.get('type_name', '')]),
        json.dumps([name]),
        json.dumps(tags),
        hda_info.get('houdini_version', ''),
        0,  # HDAs don't have HDA dependencies in the same way
        json.dumps([]),
        json.dumps({
            'type_name': hda_info.get('type_name'),
            'type_label': hda_info.get('type_label'),
            'category': hda_info.get('category'),
            'icon': hda_info.get('icon'),
        }),
        hda_info.get('type_name'),
        hda_info.get('type_label'),
        hda_info.get('version'),
        hda_info.get('category'),
        license_type, now, now
    ))

    # Insert tags for indexing
    for tag in tags:
        db.execute(
            "INSERT OR IGNORE INTO asset_tags (asset_id, tag) VALUES (?, ?)",
            (asset_id, tag.lower())
        )

    # Add to collections
    if collection_ids:
        for coll_id in collection_ids:
            add_asset_to_collection(asset_id, coll_id)

    db.commit()

    # Trigger menu regeneration
    _trigger_menu_regenerate()

    return get_asset(asset_id)


def install_hda(asset_id: str) -> bool:
    """
    Install an HDA from the library into Houdini.

    Args:
        asset_id: The library asset ID

    Returns:
        True if installation succeeded
    """
    import hou

    asset = get_asset(asset_id)
    if not asset:
        raise ValueError(f"Asset not found: {asset_id}")

    if asset.get('asset_type') != 'hda':
        raise ValueError(f"Asset is not an HDA: {asset_id}")

    file_path = get_library_assets_dir() / asset['file_path']
    if not file_path.exists():
        raise ValueError(f"HDA file not found: {file_path}")

    # Install the HDA
    hou.hda.installFile(str(file_path))

    # Record usage
    record_asset_use(asset_id)

    return True


def get_asset(asset_id: str) -> Optional[Dict[str, Any]]:
    """Get an asset by ID."""
    db = get_db()
    row = db.execute("SELECT * FROM library_assets WHERE id = ?", (asset_id,)).fetchone()
    if row is None:
        return None

    asset = dict_from_row(row)

    # Parse JSON fields
    for field in ('node_types', 'node_names', 'tags', 'dependencies', 'metadata'):
        if asset.get(field):
            try:
                asset[field] = json.loads(asset[field])
            except json.JSONDecodeError:
                asset[field] = []

    # Get collections this asset belongs to
    colls = db.execute("""
        SELECT c.* FROM collections c
        JOIN collection_assets ca ON c.id = ca.collection_id
        WHERE ca.asset_id = ?
        ORDER BY c.name
    """, (asset_id,)).fetchall()
    asset['collections'] = [dict_from_row(c) for c in colls]

    return asset


def load_asset_package(asset_id: str) -> Optional[Dict[str, Any]]:
    """Load the full package data for an asset."""
    asset = get_asset(asset_id)
    if not asset:
        return None

    file_path = get_library_assets_dir() / asset['file_path']
    if not file_path.exists():
        return None

    return json.loads(file_path.read_text())


def update_asset_package(asset_id: str, package_data: Dict[str, Any]) -> bool:
    """Update the package data (JSON file) for an asset."""
    asset = get_asset(asset_id)
    if not asset:
        return False

    file_path = get_library_assets_dir() / asset['file_path']
    if not file_path.exists():
        return False

    file_path.write_text(json.dumps(package_data, indent=2))

    # Update file hash and size
    import hashlib
    file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
    file_size = file_path.stat().st_size

    db = get_db()
    db.execute(
        "UPDATE library_assets SET file_hash = ?, file_size = ?, updated_at = ? WHERE id = ?",
        (file_hash, file_size, datetime.utcnow().isoformat(), asset_id)
    )
    db.commit()
    return True


def update_asset(asset_id: str, **kwargs) -> Optional[Dict[str, Any]]:
    """Update asset metadata."""
    db = get_db()

    allowed = {'name', 'description', 'tags', 'thumbnail_path'}
    updates = {k: v for k, v in kwargs.items() if k in allowed}

    if not updates:
        return get_asset(asset_id)

    # Handle tags specially
    if 'tags' in updates:
        tags = updates['tags']
        updates['tags'] = json.dumps(tags)

        # Update tags index
        db.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset_id,))
        for tag in tags:
            db.execute(
                "INSERT OR IGNORE INTO asset_tags (asset_id, tag) VALUES (?, ?)",
                (asset_id, tag.lower())
            )

    updates['updated_at'] = datetime.utcnow().isoformat()

    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [asset_id]

    db.execute(f"UPDATE library_assets SET {set_clause} WHERE id = ?", values)
    db.commit()

    # Trigger menu regeneration if tags changed (affects categorization)
    if 'tags' in kwargs:
        _trigger_menu_regenerate()

    return get_asset(asset_id)


def update_asset_thumbnail(asset_id: str, thumbnail_data: bytes) -> bool:
    """Update an asset's thumbnail image."""
    asset = get_asset(asset_id)
    if not asset:
        return False

    thumb_name = f"{asset_id}.png"
    thumb_file = get_library_thumbnails_dir() / thumb_name
    thumb_file.write_bytes(thumbnail_data)

    return update_asset(asset_id, thumbnail_path=thumb_name) is not None


def save_asset_version(
    asset_id: str,
    package_data: Dict[str, Any],
    description: str = None,
    tags: List[str] = None,
    thumbnail_data: bytes = None,
) -> Optional[Dict[str, Any]]:
    """
    Save a new version of an existing asset (updates the package data).

    Args:
        asset_id: ID of the asset to update
        package_data: New package data
        description: Optional new description (keeps existing if None)
        tags: Optional new tags (keeps existing if None)
        thumbnail_data: Optional new thumbnail (keeps existing if None)

    Returns:
        The updated asset record
    """
    import hashlib

    asset = get_asset(asset_id)
    if not asset:
        return None

    db = get_db()
    now = datetime.utcnow().isoformat()

    # Snapshot the current package file before overwriting
    file_path = get_library_assets_dir() / asset['file_path']
    current_hash = asset.get('file_hash', '')

    # Determine the current version label for the snapshot
    latest_row = db.execute(
        "SELECT version FROM asset_versions WHERE asset_id = ? ORDER BY created_at DESC LIMIT 1",
        (asset_id,)
    ).fetchone()
    snapshot_version = "1.0.0"
    if latest_row:
        snapshot_version = latest_row[0] if isinstance(latest_row, (tuple, list)) else latest_row['version']
    elif file_path.exists():
        # First version up — snapshot current as 1.0.0
        snapshot_name = f"{asset_id}_v1.0.0.sopdrop"
        snapshot_path = get_library_assets_dir() / snapshot_name
        if not snapshot_path.exists():
            import shutil
            shutil.copy2(str(file_path), str(snapshot_path))
            # Create initial version record for the original
            init_version_id = str(uuid.uuid4())
            db.execute("""
                INSERT OR IGNORE INTO asset_versions (id, asset_id, version, file_path, file_hash, file_size, node_count, changelog, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                init_version_id, asset_id, "1.0.0",
                snapshot_name, current_hash, asset.get('file_size', 0),
                asset.get('node_count', 0), "Initial version", asset.get('created_at', now),
            ))
    else:
        # Edge case — check if there's a snapshot already
        snapshot_version = "1.0.0"

    # Write new package data
    package_json = json.dumps(package_data, separators=(',', ':'))
    file_path.write_text(package_json)

    # Calculate new hash and size
    file_hash = hashlib.sha256(package_json.encode()).hexdigest()
    file_size = len(package_json)

    # Update thumbnail if provided
    thumbnail_path = asset.get('thumbnail_path')
    if thumbnail_data:
        thumb_name = f"{asset_id}.png"
        thumb_file = get_library_thumbnails_dir() / thumb_name
        thumb_file.write_bytes(thumbnail_data)
        thumbnail_path = thumb_name

    # Extract metadata from new package
    metadata = package_data.get('metadata', {})

    # Build updates
    updates = {
        'file_hash': file_hash,
        'file_size': file_size,
        'node_count': metadata.get('node_count', 0),
        'node_types': json.dumps(metadata.get('node_types', [])),
        'node_names': json.dumps(metadata.get('node_names', [])),
        'houdini_version': package_data.get('houdini_version', ''),
        'has_hda_dependencies': 1 if metadata.get('has_hda_dependencies') else 0,
        'dependencies': json.dumps(package_data.get('dependencies', [])),
        'metadata': json.dumps(metadata),
        'updated_at': now,
    }

    if thumbnail_path:
        updates['thumbnail_path'] = thumbnail_path

    if description is not None:
        updates['description'] = description

    if tags is not None:
        updates['tags'] = json.dumps(tags)
        # Update tags index
        db.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset_id,))
        for tag in tags:
            db.execute(
                "INSERT OR IGNORE INTO asset_tags (asset_id, tag) VALUES (?, ?)",
                (asset_id, tag.lower())
            )

    # Execute update
    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [asset_id]
    db.execute(f"UPDATE library_assets SET {set_clause} WHERE id = ?", values)

    # Create a version record with its own snapshot file
    try:
        # Re-check latest version (may have been created by snapshot above)
        latest_row2 = db.execute(
            "SELECT version FROM asset_versions WHERE asset_id = ? ORDER BY created_at DESC LIMIT 1",
            (asset_id,)
        ).fetchone()

        if latest_row2:
            latest_ver = latest_row2[0] if isinstance(latest_row2, (tuple, list)) else latest_row2['version']
            next_version = _increment_version(latest_ver)
        else:
            next_version = "1.1.0"

        # Save a snapshot file for this version
        snapshot_name = f"{asset_id}_v{next_version}.sopdrop"
        snapshot_path = get_library_assets_dir() / snapshot_name
        snapshot_path.write_text(package_json)

        version_id = str(uuid.uuid4())
        db.execute("""
            INSERT OR IGNORE INTO asset_versions (id, asset_id, version, file_path, file_hash, file_size, node_count, changelog, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            version_id, asset_id, next_version,
            snapshot_name, file_hash, file_size,
            metadata.get('node_count', 0), None, now,
        ))
    except Exception:
        pass  # Version tracking is non-critical

    db.commit()

    # Mark as modified if this asset is cloud-synced
    asset_after = get_asset(asset_id)
    if asset_after and asset_after.get('sync_status') in ('synced', 'modified') and asset_after.get('remote_slug'):
        mark_asset_modified(asset_id)

    # Trigger menu regeneration
    _trigger_menu_regenerate()

    return get_asset(asset_id)


def _increment_version(version_str: str) -> str:
    """Increment a semver minor version: 1.0.0 -> 1.1.0"""
    try:
        parts = version_str.split('.')
        if len(parts) >= 3:
            major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
            return f"{major}.{minor + 1}.0"
    except (ValueError, IndexError):
        pass
    return "1.1.0"


def get_asset_versions(asset_id: str) -> List[Dict[str, Any]]:
    """Get version history for an asset."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM asset_versions WHERE asset_id = ? ORDER BY created_at DESC",
        (asset_id,)
    ).fetchall()
    return [dict_from_row(r) for r in rows]


def load_version_package(version_id: str) -> Optional[Dict[str, Any]]:
    """Load the package data for a specific version."""
    db = get_db()
    row = db.execute("SELECT * FROM asset_versions WHERE id = ?", (version_id,)).fetchone()
    if not row:
        return None
    version = dict_from_row(row)
    file_path = get_library_assets_dir() / version['file_path']
    if not file_path.exists():
        # Fallback: try loading the current asset package
        return load_asset_package(version['asset_id'])
    return json.loads(file_path.read_text())


def revert_to_version(asset_id: str, version_id: str) -> Optional[Dict[str, Any]]:
    """Revert an asset to a previous version.

    Copies the version's snapshot file back as the current package and
    creates a new version record marking the revert.
    """
    import hashlib
    import shutil

    db = get_db()
    asset = get_asset(asset_id)
    if not asset:
        return None

    row = db.execute("SELECT * FROM asset_versions WHERE id = ?", (version_id,)).fetchone()
    if not row:
        return None
    version = dict_from_row(row)

    version_file = get_library_assets_dir() / version['file_path']
    if not version_file.exists():
        return None

    # Snapshot current state before reverting
    current_file = get_library_assets_dir() / asset['file_path']
    now = datetime.utcnow().isoformat()

    # Determine current latest version
    latest_row = db.execute(
        "SELECT version FROM asset_versions WHERE asset_id = ? ORDER BY created_at DESC LIMIT 1",
        (asset_id,)
    ).fetchone()
    if latest_row:
        cur_ver = latest_row[0] if isinstance(latest_row, (tuple, list)) else latest_row['version']
    else:
        cur_ver = "1.0.0"

    # Copy the version snapshot to the current file
    shutil.copy2(str(version_file), str(current_file))

    # Read the restored package for metadata
    package_data = json.loads(current_file.read_text())
    metadata = package_data.get('metadata', {})
    file_hash = hashlib.sha256(current_file.read_bytes()).hexdigest()
    file_size = current_file.stat().st_size

    # Update the asset record
    updates = {
        'file_hash': file_hash,
        'file_size': file_size,
        'node_count': metadata.get('node_count', 0),
        'node_types': json.dumps(metadata.get('node_types', [])),
        'node_names': json.dumps(metadata.get('node_names', [])),
        'houdini_version': package_data.get('houdini_version', ''),
        'metadata': json.dumps(metadata),
        'updated_at': now,
    }
    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [asset_id]
    db.execute(f"UPDATE library_assets SET {set_clause} WHERE id = ?", values)

    # Create a revert version record
    try:
        next_version = _increment_version(cur_ver)
        snapshot_name = f"{asset_id}_v{next_version}.sopdrop"
        snapshot_path = get_library_assets_dir() / snapshot_name
        shutil.copy2(str(current_file), str(snapshot_path))

        revert_id = str(uuid.uuid4())
        db.execute("""
            INSERT OR IGNORE INTO asset_versions (id, asset_id, version, file_path, file_hash, file_size, node_count, changelog, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            revert_id, asset_id, next_version,
            snapshot_name, file_hash, file_size,
            metadata.get('node_count', 0),
            f"Reverted to v{version.get('version', '?')}",
            now,
        ))
    except Exception:
        pass

    db.commit()
    _trigger_menu_regenerate()
    return get_asset(asset_id)


def save_vex_snippet(
    name: str,
    code: str,
    description: str = "",
    tags: List[str] = None,
    collection_id: str = None,
    snippet_type: str = "wrangle",
) -> Dict[str, Any]:
    """
    Save a VEX snippet to the library.

    Args:
        name: Display name for the snippet
        code: VEX code string
        description: Optional description
        tags: List of tags for organization
        collection_id: Optional collection to add to
        snippet_type: Type of snippet (wrangle, expression, etc.)

    Returns:
        The saved asset record
    """
    # Build a sopdrop-vex-v1 package
    package_data = {
        "format": "sopdrop-vex-v1",
        "context": "vex",
        "metadata": {
            "snippet_type": snippet_type,
            "language": "vex",
            "line_count": len(code.strip().split('\n')),
            "has_includes": '#include' in code,
        },
        "code": code,
    }

    collection_ids = [collection_id] if collection_id else None

    return save_asset(
        name=name,
        context="vex",
        package_data=package_data,
        description=description,
        tags=tags,
        collection_ids=collection_ids,
    )


def record_asset_use(asset_id: str):
    """Record that an asset was used (pasted)."""
    db = get_db()
    now = datetime.utcnow().isoformat()
    db.execute("""
        UPDATE library_assets
        SET last_used_at = ?, use_count = use_count + 1
        WHERE id = ?
    """, (now, asset_id))
    db.commit()


def delete_asset(asset_id: str):
    """Delete an asset from the library."""
    asset = get_asset(asset_id)
    if not asset:
        return

    db = get_db()

    # Delete files
    file_path = get_library_assets_dir() / asset['file_path']
    if file_path.exists():
        file_path.unlink()

    if asset.get('thumbnail_path'):
        thumb_path = get_library_thumbnails_dir() / asset['thumbnail_path']
        if thumb_path.exists():
            thumb_path.unlink()

    # Delete from database (cascades to collection_assets and asset_tags)
    db.execute("DELETE FROM library_assets WHERE id = ?", (asset_id,))
    db.commit()

    # Trigger menu regeneration
    _trigger_menu_regenerate()


# ==============================================================================
# Collection-Asset Relationships
# ==============================================================================

def add_asset_to_collection(asset_id: str, collection_id: str):
    """Add an asset to a collection."""
    db = get_db()
    now = datetime.utcnow().isoformat()

    # Get next sort order
    cursor = db.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM collection_assets WHERE collection_id = ?",
        (collection_id,)
    )
    sort_order = cursor.fetchone()[0]

    db.execute("""
        INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, sort_order, added_at)
        VALUES (?, ?, ?, ?)
    """, (collection_id, asset_id, sort_order, now))
    db.commit()


def remove_asset_from_collection(asset_id: str, collection_id: str):
    """Remove an asset from a collection."""
    db = get_db()
    db.execute(
        "DELETE FROM collection_assets WHERE collection_id = ? AND asset_id = ?",
        (collection_id, asset_id)
    )
    db.commit()


def get_collection_assets(collection_id: str) -> List[Dict[str, Any]]:
    """Get all assets in a collection."""
    db = get_db()
    rows = db.execute("""
        SELECT a.* FROM library_assets a
        JOIN collection_assets ca ON a.id = ca.asset_id
        WHERE ca.collection_id = ?
        ORDER BY ca.sort_order, a.name
    """, (collection_id,)).fetchall()

    assets = []
    for row in rows:
        asset = dict_from_row(row)
        for field in ('node_types', 'node_names', 'tags', 'dependencies', 'metadata'):
            if asset.get(field):
                try:
                    asset[field] = json.loads(asset[field])
                except json.JSONDecodeError:
                    asset[field] = []

        # Get collections this asset belongs to
        colls = db.execute("""
            SELECT c.* FROM collections c
            JOIN collection_assets ca2 ON c.id = ca2.collection_id
            WHERE ca2.asset_id = ?
            ORDER BY c.name
        """, (asset['id'],)).fetchall()
        asset['collections'] = [dict_from_row(c) for c in colls]

        assets.append(asset)

    return assets


def get_asset_collections(asset_id: str) -> List[Dict[str, Any]]:
    """Get all collections an asset belongs to."""
    db = get_db()
    rows = db.execute("""
        SELECT c.* FROM collections c
        JOIN collection_assets ca ON c.id = ca.collection_id
        WHERE ca.asset_id = ?
        ORDER BY c.name
    """, (asset_id,)).fetchall()
    return [dict_from_row(r) for r in rows]


# ==============================================================================
# Search and Query
# ==============================================================================

def search_assets(
    query: str = "",
    context: str = None,
    tags: List[str] = None,
    collection_id: str = None,
    sort_by: str = "updated_at",
    sort_order: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Search assets with filtering.

    Args:
        query: Text search (name, description, tags)
        context: Filter by context (sop, lop, etc.)
        tags: Filter by tags (all must match)
        collection_id: Filter to assets in this collection
        sort_by: Sort field (updated_at, created_at, name, use_count, last_used_at)
        sort_order: asc or desc
        limit: Max results
        offset: Pagination offset

    Returns:
        List of matching assets
    """
    db = get_db()

    # Build query
    conditions = []
    params = []
    joins = []

    # Text search - use simple LIKE for reliability
    if query:
        search_pattern = f"%{query}%"
        conditions.append("(library_assets.name LIKE ? OR library_assets.description LIKE ? OR library_assets.tags LIKE ?)")
        params.extend([search_pattern, search_pattern, search_pattern])

    # Context filter
    if context:
        conditions.append("library_assets.context = ?")
        params.append(context.lower())

    # Tags filter (all must match)
    if tags:
        for i, tag in enumerate(tags):
            alias = f"t{i}"
            joins.append(f"JOIN asset_tags {alias} ON library_assets.id = {alias}.asset_id AND {alias}.tag = ?")
            params.insert(len(params), tag.lower())

    # Collection filter
    if collection_id:
        joins.append("JOIN collection_assets ca ON library_assets.id = ca.asset_id AND ca.collection_id = ?")
        params.append(collection_id)

    # Build SQL
    where = " AND ".join(conditions) if conditions else "1=1"
    join_sql = " ".join(joins)

    # Validate sort
    valid_sorts = {'updated_at', 'created_at', 'name', 'use_count', 'last_used_at', 'node_count'}
    if sort_by not in valid_sorts:
        sort_by = 'updated_at'
    sort_order = 'DESC' if sort_order.lower() == 'desc' else 'ASC'

    sql = f"""
        SELECT DISTINCT library_assets.* FROM library_assets
        {join_sql}
        WHERE {where}
        ORDER BY library_assets.{sort_by} {sort_order}
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    rows = db.execute(sql, params).fetchall()

    assets = []
    for row in rows:
        asset = dict_from_row(row)
        for field in ('node_types', 'node_names', 'tags', 'dependencies', 'metadata'):
            if asset.get(field):
                try:
                    asset[field] = json.loads(asset[field])
                except json.JSONDecodeError:
                    asset[field] = []

        # Get collections this asset belongs to
        colls = db.execute("""
            SELECT c.* FROM collections c
            JOIN collection_assets ca ON c.id = ca.collection_id
            WHERE ca.asset_id = ?
            ORDER BY c.name
        """, (asset['id'],)).fetchall()
        asset['collections'] = [dict_from_row(c) for c in colls]

        assets.append(asset)

    return assets


def get_all_tags() -> List[Dict[str, Any]]:
    """Get all unique tags with usage counts."""
    db = get_db()
    rows = db.execute("""
        SELECT tag, COUNT(*) as count
        FROM asset_tags
        GROUP BY tag
        ORDER BY count DESC, tag ASC
    """).fetchall()
    return [{'tag': r[0], 'count': r[1]} for r in rows]


def get_recent_assets(limit: int = 10) -> List[Dict[str, Any]]:
    """Get recently used assets."""
    return search_assets(sort_by='last_used_at', sort_order='desc', limit=limit)


def get_frequent_assets(limit: int = 10) -> List[Dict[str, Any]]:
    """Get most frequently used assets."""
    return search_assets(sort_by='use_count', sort_order='desc', limit=limit)


# ==============================================================================
# Filter Presets (Saved Searches)
# ==============================================================================

def save_filter_preset(
    name: str,
    filters: Dict[str, Any],
    description: str = "",
) -> Dict[str, Any]:
    """Save a filter preset for quick access."""
    db = get_db()
    preset_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # Get next sort order
    cursor = db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM filter_presets")
    sort_order = cursor.fetchone()[0]

    db.execute("""
        INSERT INTO filter_presets (id, name, description, filters, sort_order, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (preset_id, name, description, json.dumps(filters), sort_order, now))
    db.commit()

    return get_filter_preset(preset_id)


def get_filter_preset(preset_id: str) -> Optional[Dict[str, Any]]:
    """Get a filter preset by ID."""
    db = get_db()
    row = db.execute("SELECT * FROM filter_presets WHERE id = ?", (preset_id,)).fetchone()
    if row is None:
        return None

    preset = dict_from_row(row)
    preset['filters'] = json.loads(preset['filters'])
    return preset


def list_filter_presets() -> List[Dict[str, Any]]:
    """List all filter presets."""
    db = get_db()
    rows = db.execute("SELECT * FROM filter_presets ORDER BY sort_order, name").fetchall()
    presets = []
    for row in rows:
        preset = dict_from_row(row)
        preset['filters'] = json.loads(preset['filters'])
        presets.append(preset)
    return presets


def delete_filter_preset(preset_id: str):
    """Delete a filter preset."""
    db = get_db()
    db.execute("DELETE FROM filter_presets WHERE id = ?", (preset_id,))
    db.commit()


# ==============================================================================
# User Preferences
# ==============================================================================

def get_pref(key: str, default: Any = None) -> Any:
    """Get a user preference."""
    db = get_db()
    row = db.execute("SELECT value FROM user_prefs WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return row[0]


def set_pref(key: str, value: Any):
    """Set a user preference."""
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO user_prefs (key, value) VALUES (?, ?)",
        (key, json.dumps(value))
    )
    db.commit()


# ==============================================================================
# Library Statistics
# ==============================================================================

# ==============================================================================
# Menu Regeneration Hook
# ==============================================================================

def _trigger_menu_regenerate():
    """Trigger TAB menu regeneration (lazy import to avoid circular deps)."""
    try:
        from . import menu
        menu.trigger_regenerate()
    except Exception as e:
        # Silently fail - menu regeneration is not critical
        pass


def get_library_stats() -> Dict[str, Any]:
    """Get library statistics."""
    db = get_db()

    asset_count = db.execute("SELECT COUNT(*) FROM library_assets").fetchone()[0]
    collection_count = db.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
    tag_count = db.execute("SELECT COUNT(DISTINCT tag) FROM asset_tags").fetchone()[0]

    # Size calculation
    assets_dir = get_library_assets_dir()
    thumbs_dir = get_library_thumbnails_dir()

    total_size = 0
    for f in assets_dir.iterdir():
        if f.is_file():
            total_size += f.stat().st_size
    for f in thumbs_dir.iterdir():
        if f.is_file():
            total_size += f.stat().st_size

    # Context breakdown
    context_counts = db.execute("""
        SELECT context, COUNT(*) as count
        FROM library_assets
        GROUP BY context
        ORDER BY count DESC
    """).fetchall()

    return {
        'asset_count': asset_count,
        'collection_count': collection_count,
        'tag_count': tag_count,
        'total_size_bytes': total_size,
        'total_size_mb': round(total_size / (1024 * 1024), 2),
        'contexts': {r[0]: r[1] for r in context_counts},
    }


# ==============================================================================
# Sync Status Tracking
# ==============================================================================

def mark_asset_synced(asset_id: str, remote_slug: str, remote_version: str):
    """Mark an asset as synced with the cloud."""
    db = get_db()
    now = datetime.utcnow().isoformat()
    db.execute("""
        UPDATE library_assets
        SET remote_slug = ?, remote_version = ?, sync_status = 'synced', synced_at = ?
        WHERE id = ?
    """, (remote_slug, remote_version, now, asset_id))
    db.commit()


def mark_asset_modified(asset_id: str):
    """Mark a synced asset as locally modified."""
    db = get_db()
    db.execute("""
        UPDATE library_assets
        SET sync_status = 'modified', updated_at = ?
        WHERE id = ? AND sync_status = 'synced'
    """, (datetime.utcnow().isoformat(), asset_id))
    db.commit()


def mark_asset_syncing(asset_id: str, draft_id: str):
    """Mark an asset as being synced (draft created, awaiting completion)."""
    db = get_db()
    db.execute("""
        UPDATE library_assets
        SET sync_status = 'syncing', metadata = json_set(COALESCE(metadata, '{}'), '$.draft_id', ?)
        WHERE id = ?
    """, (draft_id, asset_id))
    db.commit()


def get_sync_status() -> Dict[str, List[Dict[str, Any]]]:
    """Get assets grouped by sync status."""
    db = get_db()

    result = {
        'local_only': [],
        'synced': [],
        'modified': [],
    }

    for status in result.keys():
        rows = db.execute(
            "SELECT id, name, context, remote_slug, remote_version, synced_at FROM library_assets WHERE sync_status = ?",
            (status,)
        ).fetchall()
        result[status] = [dict_from_row(r) for r in rows]

    return result


def reset_syncing_status(asset_id: str):
    """Reset a 'syncing' asset back to 'local_only' (e.g., after publish failure)."""
    db = get_db()
    db.execute("""
        UPDATE library_assets
        SET sync_status = 'local_only',
            metadata = json_remove(COALESCE(metadata, '{}'), '$.draft_id')
        WHERE id = ? AND sync_status = 'syncing'
    """, (asset_id,))
    db.commit()


def verify_cloud_status(asset_id: str) -> str:
    """
    Verify an asset's cloud status by checking the server.

    Returns the verified status: 'synced', 'local_only', or 'error'.
    Also updates the local database to match reality.
    """
    db = get_db()
    row = db.execute(
        "SELECT remote_slug, sync_status FROM library_assets WHERE id = ?",
        (asset_id,)
    ).fetchone()

    if not row:
        return 'error'

    asset = dict_from_row(row)
    remote_slug = asset.get('remote_slug')
    current_status = asset.get('sync_status', 'local_only')

    # If local_only and no remote slug, nothing to verify
    if current_status == 'local_only' and not remote_slug:
        return 'local_only'

    # If 'syncing' with no remote_slug, it was a failed publish attempt
    if current_status == 'syncing' and not remote_slug:
        reset_syncing_status(asset_id)
        return 'local_only'

    # Check server for the asset
    if remote_slug:
        try:
            from .api import SopdropClient, NotFoundError
            client = SopdropClient()
            client._get(f"assets/{remote_slug}", auth=False)
            # Asset exists on server — mark as synced
            if current_status != 'synced':
                db.execute(
                    "UPDATE library_assets SET sync_status = 'synced' WHERE id = ?",
                    (asset_id,)
                )
                db.commit()
            return 'synced'
        except NotFoundError:
            # Asset no longer exists on server
            db.execute("""
                UPDATE library_assets
                SET sync_status = 'local_only', remote_slug = NULL,
                    remote_version = NULL, synced_at = NULL
                WHERE id = ?
            """, (asset_id,))
            db.commit()
            return 'local_only'
        except Exception:
            # Network error — can't verify, keep current status
            return current_status

    return current_status


def cleanup_stale_syncing():
    """Reset any assets stuck in 'syncing' status (drafts expire after 24h)."""
    db = get_db()
    db.execute("""
        UPDATE library_assets
        SET sync_status = 'local_only',
            metadata = json_remove(COALESCE(metadata, '{}'), '$.draft_id')
        WHERE sync_status = 'syncing'
          AND updated_at < datetime('now', '-24 hours')
    """)
    db.commit()


# ==============================================================================
# Cloud Sync Operations
# ==============================================================================

def pull_from_cloud(slug: str, version: str = None, collection_id: str = None,
                    thumbnail_url: str = None, cloud_asset_info: dict = None) -> Dict[str, Any]:
    """
    Pull an asset from the cloud and save to local library.

    Args:
        slug: The asset slug (user/name)
        version: Specific version to pull (default: latest)
        collection_id: Optional collection to add it to
        thumbnail_url: URL to download thumbnail from
        cloud_asset_info: Pre-fetched asset info (to avoid extra API call)

    Returns:
        The created local asset record
    """
    from .api import SopdropClient
    from urllib.request import urlopen, Request
    from urllib.error import URLError

    client = SopdropClient()

    # Get asset info if not provided
    asset_info = cloud_asset_info
    if not asset_info:
        asset_info = client.get_asset(slug)
        if not asset_info:
            raise ValueError(f"Asset not found: {slug}")

    # Install (downloads to cache and returns package)
    result = client.install(f"{slug}@{version}" if version else slug)

    if result['type'] == 'node':
        package = result['package']
    else:
        # For HDAs, we need different handling
        raise NotImplementedError("HDA pull not yet implemented")

    # Download thumbnail if available
    thumbnail_data = None
    # Try multiple possible thumbnail URL fields
    thumb_url = thumbnail_url or asset_info.get('thumbnailUrl') or asset_info.get('thumbnail_url') or asset_info.get('thumbnail')
    print(f"[Sopdrop] Thumbnail URL from cloud: {thumb_url}")

    if thumb_url:
        try:
            import ssl
            from .config import get_config

            # Handle relative URLs
            if thumb_url.startswith('/'):
                config = get_config()
                base = config.get('server_url', 'https://sopdrop.com').rstrip('/')
                thumb_url = base + thumb_url
                print(f"[Sopdrop] Full thumbnail URL: {thumb_url}")

            # Create request with headers
            headers = {
                "User-Agent": "sopdrop-client/0.1.2",
                "Accept": "image/*",
            }
            req = Request(thumb_url, headers=headers)

            # Handle SSL (Houdini's Python often has cert issues)
            if thumb_url.startswith("https://"):
                ctx = ssl.create_default_context()
                try:
                    import certifi
                    ctx = ssl.create_default_context(cafile=certifi.where())
                except ImportError:
                    pass
                try:
                    response = urlopen(req, timeout=15, context=ctx)
                except (ssl.SSLCertVerificationError, URLError):
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    response = urlopen(req, timeout=15, context=ctx)
            else:
                response = urlopen(req, timeout=15)

            thumbnail_data = response.read()
            content_type = response.headers.get('Content-Type', '')
            print(f"[Sopdrop] Downloaded thumbnail: {len(thumbnail_data)} bytes, type: {content_type}")

            # Validate it's actually an image
            if thumbnail_data and len(thumbnail_data) < 100:
                print(f"[Sopdrop] Warning: Thumbnail data too small, might be an error response")
                thumbnail_data = None

        except Exception as e:
            print(f"[Sopdrop] Failed to download thumbnail from {thumb_url}: {e}")
            import traceback
            traceback.print_exc()

    # Save to library
    asset = save_asset(
        name=asset_info.get('name', slug.split('/')[-1]),
        context=package.get('context', 'unknown'),
        package_data=package,
        description=asset_info.get('description', ''),
        tags=asset_info.get('tags', []),
        thumbnail_data=thumbnail_data,
        collection_ids=[collection_id] if collection_id else None,
    )

    # Mark as synced
    actual_version = version or asset_info.get('latestVersion') or asset_info.get('latest_version', '1.0.0')
    mark_asset_synced(asset['id'], slug, actual_version)

    return asset


def push_to_cloud(asset_id: str) -> Dict[str, Any]:
    """
    Push a local asset to the cloud (creates a draft for completion in browser).

    Args:
        asset_id: The local asset ID

    Returns:
        Dict with 'draft_id' and 'complete_url' for browser completion
    """
    import json
    import ssl
    import webbrowser
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    from .config import get_api_url, get_token

    asset = get_asset(asset_id)
    if not asset:
        raise ValueError(f"Asset not found: {asset_id}")

    package = load_asset_package(asset_id)
    if not package:
        raise ValueError("Failed to load asset package")

    token = get_token()
    if not token:
        raise ValueError("Not logged in. Please log in first.")

    # Upload as draft
    url = f"{get_api_url()}/drafts"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "sopdrop-library/0.1.0",
    }

    body = json.dumps({
        "package": package,
        "suggested_name": asset.get('name'),
        "suggested_description": asset.get('description'),
        "suggested_tags": asset.get('tags', []),
    }).encode('utf-8')

    req = Request(url, data=body, headers=headers, method="POST")

    # Handle SSL with fallback for Houdini's Python
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass

    try:
        response = urlopen(req, timeout=120, context=ctx)
    except (ssl.SSLCertVerificationError, URLError) as e:
        is_ssl = isinstance(e, ssl.SSLCertVerificationError) or (
            isinstance(e, URLError) and 'CERTIFICATE_VERIFY_FAILED' in str(e.reason))
        if not is_ssl:
            raise
        warnings.warn(
            "SSL verification failed for publish. Install certifi to fix.",
            stacklevel=2,
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        response = urlopen(req, timeout=120, context=ctx)

    try:
        result = json.loads(response.read().decode('utf-8'))
    except HTTPError as e:
        error_body = e.read().decode('utf-8')
        raise ValueError(f"Upload failed: {error_body}")

    # Open browser to complete
    complete_url = result.get('completeUrl')
    if complete_url:
        webbrowser.open(complete_url)

    return {
        'draft_id': result.get('draftId'),
        'complete_url': complete_url,
        'message': 'Draft created. Complete the listing in your browser.',
    }


def push_version_to_cloud(asset_id: str) -> Dict[str, Any]:
    """
    Push a new version of a cloud-synced asset.

    Uses the /drafts/version endpoint to create a version draft,
    then opens the browser for the user to set the version number and confirm.

    Args:
        asset_id: The local asset ID (must have a remote_slug)

    Returns:
        Dict with 'draft_id' and 'complete_url'
    """
    import json
    import ssl
    import webbrowser
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    from .config import get_api_url, get_token

    asset = get_asset(asset_id)
    if not asset:
        raise ValueError(f"Asset not found: {asset_id}")

    remote_slug = asset.get('remote_slug')
    if not remote_slug:
        raise ValueError("Asset is not synced to cloud. Use Publish instead.")

    package = load_asset_package(asset_id)
    if not package:
        raise ValueError("Failed to load asset package")

    token = get_token()
    if not token:
        raise ValueError("Not logged in. Please log in first.")

    # We need the server-side asset_id (UUID), not the local ID.
    # Fetch from the server using the slug.
    from .api import SopdropClient
    client = SopdropClient()
    try:
        server_asset = client._get(f"assets/{remote_slug}", auth=False)
        server_asset_id = server_asset.get('assetId') or server_asset.get('asset_id')
        if not server_asset_id:
            raise ValueError("Could not get server asset ID")
    except Exception as e:
        raise ValueError(f"Failed to look up cloud asset: {e}")

    # Upload as version draft
    url = f"{get_api_url()}/drafts/version"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "sopdrop-library/0.1.0",
    }

    body = json.dumps({
        "package": package,
        "assetId": server_asset_id,
    }).encode('utf-8')

    req = Request(url, data=body, headers=headers, method="POST")

    # Handle SSL with fallback for Houdini's Python
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass

    try:
        response = urlopen(req, timeout=120, context=ctx)
    except (ssl.SSLCertVerificationError, URLError) as e:
        is_ssl = isinstance(e, ssl.SSLCertVerificationError) or (
            isinstance(e, URLError) and 'CERTIFICATE_VERIFY_FAILED' in str(e.reason))
        if not is_ssl:
            raise
        warnings.warn(
            "SSL verification failed for version upload. Install certifi to fix.",
            stacklevel=2,
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        response = urlopen(req, timeout=120, context=ctx)

    try:
        result = json.loads(response.read().decode('utf-8'))
    except HTTPError as e:
        error_body = e.read().decode('utf-8')
        reset_syncing_status(asset_id)
        raise ValueError(f"Upload failed: {error_body}")

    # Open browser to set version number and confirm
    complete_url = result.get('completeUrl')
    if complete_url:
        webbrowser.open(complete_url)

    # Mark as syncing until browser confirmation completes
    draft_id = result.get('draftId', '')
    mark_asset_syncing(asset_id, draft_id)

    return {
        'draft_id': draft_id,
        'complete_url': complete_url,
        'message': 'Version draft created. Set the version number in your browser.',
    }


def import_from_cache(slug: str, version: str = None, collection_id: str = None) -> Optional[Dict[str, Any]]:
    """
    Import an already-cached cloud asset into the local library.

    Args:
        slug: The asset slug
        version: The cached version
        collection_id: Optional collection to add to

    Returns:
        The created library asset, or None if not cached
    """
    from .config import get_cache_dir

    cache_dir = get_cache_dir()
    slug_safe = slug.replace('/', '_')

    # Find cached file
    if version:
        cache_file = cache_dir / f"{slug_safe}@{version}.sopdrop"
    else:
        # Find any cached version
        matches = list(cache_dir.glob(f"{slug_safe}@*.sopdrop"))
        if not matches:
            return None
        # Use most recent
        cache_file = max(matches, key=lambda p: p.stat().st_mtime)
        version = cache_file.stem.split('@')[1]

    if not cache_file.exists():
        return None

    # Load package
    package = json.loads(cache_file.read_text())

    # Save to library
    asset = save_asset(
        name=slug.split('/')[-1].replace('-', ' ').title(),
        context=package.get('context', 'unknown'),
        package_data=package,
        collection_ids=[collection_id] if collection_id else None,
    )

    mark_asset_synced(asset['id'], slug, version)
    return asset


def get_cloud_saved_assets() -> List[Dict[str, Any]]:
    """
    Fetch user's saved and published assets from the cloud.

    Combines the saved/bookmarked list with the user's own published assets
    to ensure all cloud assets are available for pull.
    """
    from .api import SopdropClient
    from .config import get_token

    if not get_token():
        return []

    client = SopdropClient()
    seen_slugs = set()
    all_assets = []

    # Fetch saved/bookmarked assets
    try:
        result = client._get("saved?limit=100")
        for asset in result.get('assets', []):
            slug = asset.get('slug', '')
            if slug and slug not in seen_slugs:
                seen_slugs.add(slug)
                all_assets.append(asset)
    except Exception as e:
        print(f"[Sopdrop] Failed to fetch saved assets: {e}")

    # Also fetch user's own published assets
    try:
        result = client._get("users/me/assets")
        for asset in result.get('assets', []):
            slug = asset.get('slug', '')
            if slug and slug not in seen_slugs:
                seen_slugs.add(slug)
                # Normalize field names to match saved format
                all_assets.append({
                    'name': asset.get('name', ''),
                    'slug': slug,
                    'description': asset.get('description', ''),
                    'type': asset.get('assetType', 'node'),
                    'context': asset.get('houdiniContext', 'sop'),
                    'tags': asset.get('tags', []),
                    'latestVersion': asset.get('latestVersion', '1.0.0'),
                    'thumbnailUrl': asset.get('thumbnailUrl', ''),
                    'source': 'published',
                })
    except Exception as e:
        print(f"[Sopdrop] Failed to fetch published assets: {e}")

    return all_assets


def sync_saved_assets(collection_name: str = "Cloud Library") -> Dict[str, Any]:
    """
    Sync all saved assets from cloud to local library.

    Args:
        collection_name: Name of collection to add synced assets to

    Returns:
        Summary of sync operation
    """
    from .api import SopdropClient
    from .config import get_token

    if not get_token():
        return {'error': 'Not logged in', 'synced': 0, 'skipped': 0}

    # Get or create the cloud library collection
    collections = list_collections()
    cloud_coll = None
    for c in collections:
        if c['name'] == collection_name:
            cloud_coll = c
            break

    if not cloud_coll:
        cloud_coll = create_collection(collection_name, color='#6366f1')

    # Get cloud saved assets
    cloud_assets = get_cloud_saved_assets()
    if not cloud_assets:
        return {'synced': 0, 'skipped': 0, 'total': 0}

    # Get local assets by remote_slug
    db = get_db()
    local_slugs = set()
    rows = db.execute("SELECT remote_slug FROM library_assets WHERE remote_slug IS NOT NULL").fetchall()
    for row in rows:
        local_slugs.add(row[0])

    synced = 0
    skipped = 0
    errors = []

    for cloud_asset in cloud_assets:
        slug = cloud_asset.get('slug')
        if not slug:
            continue

        if slug in local_slugs:
            skipped += 1
            continue

        try:
            # Get thumbnail URL - try multiple possible field names
            thumb_url = (
                cloud_asset.get('thumbnailUrl') or
                cloud_asset.get('thumbnail_url') or
                cloud_asset.get('thumbnail') or
                cloud_asset.get('previewUrl') or
                cloud_asset.get('preview_url')
            )

            # Debug: show what we got from cloud
            print(f"[Sopdrop] Cloud asset data for {slug}:")
            print(f"  - thumbnailUrl: {cloud_asset.get('thumbnailUrl')}")
            print(f"  - thumbnail_url: {cloud_asset.get('thumbnail_url')}")
            print(f"  - Using: {thumb_url}")

            # Build asset info from cloud response
            asset_info = {
                'name': cloud_asset.get('name', slug.split('/')[-1]),
                'description': cloud_asset.get('description', ''),
                'tags': cloud_asset.get('tags', []),
                'latestVersion': cloud_asset.get('latestVersion') or cloud_asset.get('latest_version'),
                'thumbnailUrl': thumb_url,
            }

            # Pull the asset
            asset = pull_from_cloud(
                slug=slug,
                version=cloud_asset.get('savedVersion') or cloud_asset.get('latestVersion') or cloud_asset.get('latest_version'),
                collection_id=cloud_coll['id'],
                thumbnail_url=thumb_url,
                cloud_asset_info=asset_info,
            )
            if asset:
                synced += 1
                print(f"[Sopdrop] Synced: {slug} (thumbnail: {asset.get('thumbnail_path')})")
        except Exception as e:
            errors.append(f"{slug}: {e}")
            print(f"[Sopdrop] Failed to sync {slug}: {e}")
            import traceback
            traceback.print_exc()

    # Trigger menu regeneration if we synced any assets
    if synced > 0:
        _trigger_menu_regenerate()

    return {
        'synced': synced,
        'skipped': skipped,
        'total': len(cloud_assets),
        'errors': errors if errors else None,
    }


def sync_saved_assets_with_folders() -> Dict[str, Any]:
    """
    Sync all saved assets from cloud to local library, organizing by folder.

    Each cloud folder becomes a local collection with source='cloud'.
    Assets without a folder go into a "Cloud Library" collection.

    Returns:
        Summary of sync operation
    """
    from .api import SopdropClient
    from .config import get_token

    if not get_token():
        return {'error': 'Not logged in', 'synced': 0, 'skipped': 0}

    # Get cloud saved assets (includes folder field)
    cloud_assets = get_cloud_saved_assets()
    if not cloud_assets:
        return {'synced': 0, 'skipped': 0, 'total': 0}

    db = get_db()

    # Get local assets by remote_slug to check what's already synced
    local_slugs = set()
    rows = db.execute("SELECT remote_slug FROM library_assets WHERE remote_slug IS NOT NULL").fetchall()
    for row in rows:
        local_slugs.add(row[0])

    # Group cloud assets by folder
    assets_by_folder = {}
    for asset in cloud_assets:
        folder = asset.get('folder') or '__default__'
        if folder not in assets_by_folder:
            assets_by_folder[folder] = []
        assets_by_folder[folder].append(asset)

    # Create/get collections for each folder
    folder_to_collection = {}
    existing_collections = {c['name']: c for c in list_collections()}

    for folder_name in assets_by_folder.keys():
        if folder_name == '__default__':
            coll_name = "Cloud Library"
        else:
            coll_name = folder_name

        if coll_name in existing_collections:
            coll = existing_collections[coll_name]
            # Update to mark as cloud source if not already
            if coll.get('source') != 'cloud':
                db.execute(
                    "UPDATE collections SET source = 'cloud' WHERE id = ?",
                    (coll['id'],)
                )
                db.commit()
            folder_to_collection[folder_name] = coll['id']
        else:
            # Create new cloud collection
            new_coll = create_collection(coll_name, color='#6366f1')
            db.execute(
                "UPDATE collections SET source = 'cloud' WHERE id = ?",
                (new_coll['id'],)
            )
            db.commit()
            folder_to_collection[folder_name] = new_coll['id']

    synced = 0
    skipped = 0
    errors = []

    for folder_name, folder_assets in assets_by_folder.items():
        collection_id = folder_to_collection.get(folder_name)

        for cloud_asset in folder_assets:
            slug = cloud_asset.get('slug')
            if not slug:
                continue

            if slug in local_slugs:
                # Already have it - just ensure it's in the right collection
                asset_row = db.execute(
                    "SELECT id FROM library_assets WHERE remote_slug = ?",
                    (slug,)
                ).fetchone()
                if asset_row and collection_id:
                    now = datetime.now().isoformat()
                    db.execute("""
                        INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, added_at)
                        VALUES (?, ?, ?)
                    """, (collection_id, asset_row[0], now))
                    db.commit()
                skipped += 1
                continue

            try:
                # Get thumbnail URL
                thumb_url = (
                    cloud_asset.get('thumbnailUrl') or
                    cloud_asset.get('thumbnail_url') or
                    cloud_asset.get('thumbnail')
                )

                # Build asset info
                asset_info = {
                    'name': cloud_asset.get('name', slug.split('/')[-1]),
                    'description': cloud_asset.get('description', ''),
                    'tags': cloud_asset.get('tags', []),
                    'latestVersion': cloud_asset.get('latestVersion') or cloud_asset.get('latest_version'),
                    'thumbnailUrl': thumb_url,
                }

                # Pull the asset into the correct collection
                asset = pull_from_cloud(
                    slug=slug,
                    version=cloud_asset.get('savedVersion') or cloud_asset.get('latestVersion'),
                    collection_id=collection_id,
                    thumbnail_url=thumb_url,
                    cloud_asset_info=asset_info,
                )
                if asset:
                    synced += 1
                    print(f"[Sopdrop] Synced: {slug} -> {folder_name}")
            except Exception as e:
                errors.append(f"{slug}: {e}")
                print(f"[Sopdrop] Failed to sync {slug}: {e}")

    # Trigger menu regeneration if we synced any assets
    if synced > 0:
        _trigger_menu_regenerate()

    return {
        'synced': synced,
        'skipped': skipped,
        'total': len(cloud_assets),
        'errors': errors if errors else None,
    }


def get_cloud_folders() -> List[Dict[str, Any]]:
    """
    Fetch user's folders from the cloud.

    Returns:
        List of folder objects with id, name, slug, color, etc.
    """
    from .api import SopdropClient
    from .config import get_token

    if not get_token():
        return []

    try:
        client = SopdropClient()
        result = client._get("folders?flat=true")
        return result.get('folders', [])
    except Exception as e:
        print(f"[Sopdrop] Failed to fetch cloud folders: {e}")
        return []


def sync_cloud_folders() -> Dict[str, Any]:
    """
    Sync folders from cloud to local library as cloud collections.

    Cloud folders become read-only collections locally with source='cloud'.
    Assets in those folders are synced down.

    Returns:
        Summary of sync operation.
    """
    from .api import SopdropClient
    from .config import get_token

    if not get_token():
        return {'error': 'Not logged in', 'synced': 0, 'created': 0}

    # Get cloud folders
    cloud_folders = get_cloud_folders()
    if not cloud_folders:
        return {'synced': 0, 'created': 0, 'total': 0}

    db = get_db()
    created = 0
    updated = 0

    # Get existing cloud collections
    existing = {}
    rows = db.execute(
        "SELECT id, remote_id FROM collections WHERE source = 'cloud'"
    ).fetchall()
    for row in rows:
        existing[row[1]] = row[0]

    for folder in cloud_folders:
        folder_id = folder.get('id')
        if not folder_id:
            continue

        now = datetime.now().isoformat()

        if folder_id in existing:
            # Update existing
            db.execute("""
                UPDATE collections
                SET name = ?, description = ?, color = ?, icon = ?, updated_at = ?
                WHERE id = ?
            """, (
                folder.get('name'),
                folder.get('description'),
                folder.get('color', '#6366f1'),
                folder.get('icon', 'cloud'),
                now,
                existing[folder_id],
            ))
            updated += 1
        else:
            # Create new cloud collection
            coll_id = str(uuid.uuid4())
            db.execute("""
                INSERT INTO collections (id, name, description, color, icon, source, remote_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'cloud', ?, ?, ?)
            """, (
                coll_id,
                folder.get('name'),
                folder.get('description'),
                folder.get('color', '#6366f1'),
                folder.get('icon', 'cloud'),
                folder_id,
                now,
                now,
            ))
            created += 1

    db.commit()

    # Remove cloud collections that no longer exist remotely
    cloud_ids = {f.get('id') for f in cloud_folders if f.get('id')}
    for remote_id, local_id in existing.items():
        if remote_id not in cloud_ids:
            db.execute("DELETE FROM collections WHERE id = ?", (local_id,))
            db.commit()

    return {
        'created': created,
        'updated': updated,
        'total': len(cloud_folders),
    }


def sync_folder_assets(folder_slug: str) -> Dict[str, Any]:
    """
    Sync assets from a specific cloud folder to local library.

    Args:
        folder_slug: The cloud folder slug to sync

    Returns:
        Summary of sync operation.
    """
    from .api import SopdropClient
    from .config import get_token

    if not get_token():
        return {'error': 'Not logged in', 'synced': 0}

    try:
        client = SopdropClient()
        result = client._get(f"folders/{folder_slug}?limit=100")
    except Exception as e:
        return {'error': str(e), 'synced': 0}

    folder = result.get('folder', {})
    assets = result.get('assets', [])

    if not assets:
        return {'synced': 0, 'total': 0}

    # Find or create local collection for this folder
    db = get_db()
    row = db.execute(
        "SELECT id FROM collections WHERE source = 'cloud' AND remote_id = ?",
        (folder.get('id'),)
    ).fetchone()

    if not row:
        # Need to sync folders first
        sync_cloud_folders()
        row = db.execute(
            "SELECT id FROM collections WHERE source = 'cloud' AND remote_id = ?",
            (folder.get('id'),)
        ).fetchone()

    if not row:
        return {'error': 'Could not find/create collection', 'synced': 0}

    collection_id = row[0]

    # Get local assets by remote_slug
    local_slugs = set()
    rows = db.execute("SELECT remote_slug FROM library_assets WHERE remote_slug IS NOT NULL").fetchall()
    for r in rows:
        local_slugs.add(r[0])

    synced = 0
    skipped = 0

    for asset in assets:
        slug = asset.get('slug')
        if not slug:
            continue

        if slug in local_slugs:
            # Already have it - just ensure it's in this collection
            asset_row = db.execute(
                "SELECT id FROM library_assets WHERE remote_slug = ?",
                (slug,)
            ).fetchone()
            if asset_row:
                now = datetime.now().isoformat()
                db.execute("""
                    INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, added_at)
                    VALUES (?, ?, ?)
                """, (collection_id, asset_row[0], now))
            skipped += 1
            continue

        try:
            # Pull asset from cloud
            pulled = pull_from_cloud(
                slug=slug,
                version=asset.get('version'),
                collection_id=collection_id,
                thumbnail_url=asset.get('thumbnailUrl'),
            )
            if pulled:
                synced += 1
        except Exception as e:
            print(f"[Sopdrop] Failed to sync {slug}: {e}")

    db.commit()

    return {
        'synced': synced,
        'skipped': skipped,
        'total': len(assets),
        'folder': folder.get('name'),
    }


def get_team_saved_assets(team_slug: str) -> List[Dict[str, Any]]:
    """
    Fetch saved assets for a specific team from the cloud.

    Args:
        team_slug: The team's slug (e.g., "my-studio")

    Returns:
        List of team saved assets from the cloud.
    """
    from .api import SopdropClient
    from .config import get_token

    if not get_token():
        return []

    try:
        client = SopdropClient()
        result = client._get(f"teams/{team_slug}/saved?limit=200")
        return result.get('assets', [])
    except Exception as e:
        print(f"[Sopdrop] Failed to fetch team saved assets: {e}")
        return []


def sync_team_library(team_slug: str = None, collection_name: str = None) -> Dict[str, Any]:
    """
    Sync assets from a team's saved library on the website to the local team library.

    This syncs from the team's cloud saved assets to the configured team library
    location on disk. Multiple users can share this library via a network drive.

    Args:
        team_slug: The team's slug. If None, uses configured team_slug.
        collection_name: Name of collection to put synced assets in.
                        Defaults to "Team: {team_slug}"

    Returns:
        Summary of sync operation with synced/skipped/error counts.
    """
    from .api import SopdropClient
    from .config import get_token, get_team_slug, get_active_library

    if not get_token():
        return {'error': 'Not logged in', 'synced': 0, 'skipped': 0}

    # Get team slug from argument or config
    team_slug = team_slug or get_team_slug()
    if not team_slug:
        return {'error': 'No team configured. Set team slug in settings.', 'synced': 0, 'skipped': 0}

    # Must be in team library mode
    if get_active_library() != 'team':
        return {'error': 'Switch to team library first', 'synced': 0, 'skipped': 0}

    # Set collection name
    if not collection_name:
        collection_name = f"Team: {team_slug}"

    # Get or create the team collection
    collections = list_collections()
    team_coll = None
    for c in collections:
        if c['name'] == collection_name:
            team_coll = c
            break

    if not team_coll:
        team_coll = create_collection(collection_name, color='#10b981')  # Green for team

    # Get team saved assets from cloud
    print(f"[Sopdrop] Fetching team saved assets for: {team_slug}")
    team_assets = get_team_saved_assets(team_slug)

    if not team_assets:
        return {'synced': 0, 'skipped': 0, 'total': 0, 'message': 'No assets in team library'}

    print(f"[Sopdrop] Found {len(team_assets)} assets in team library")

    # Get local assets by remote_slug
    db = get_db()
    local_slugs = set()
    rows = db.execute("SELECT remote_slug FROM library_assets WHERE remote_slug IS NOT NULL").fetchall()
    for row in rows:
        local_slugs.add(row[0])

    synced = 0
    skipped = 0
    errors = []

    for team_asset in team_assets:
        slug = team_asset.get('slug')
        if not slug:
            continue

        if slug in local_slugs:
            skipped += 1
            continue

        try:
            # Get thumbnail URL
            thumb_url = (
                team_asset.get('thumbnailUrl') or
                team_asset.get('thumbnail_url') or
                team_asset.get('thumbnail') or
                team_asset.get('previewUrl') or
                team_asset.get('preview_url')
            )

            print(f"[Sopdrop] Syncing team asset: {slug}")

            # Build asset info
            asset_info = {
                'name': team_asset.get('name', slug.split('/')[-1]),
                'description': team_asset.get('description', ''),
                'tags': team_asset.get('tags', []),
                'latestVersion': team_asset.get('latestVersion') or team_asset.get('latest_version'),
                'thumbnailUrl': thumb_url,
            }

            # Pull the asset
            asset = pull_from_cloud(
                slug=slug,
                version=team_asset.get('savedVersion') or team_asset.get('latestVersion') or team_asset.get('latest_version'),
                collection_id=team_coll['id'],
                thumbnail_url=thumb_url,
                cloud_asset_info=asset_info,
            )
            if asset:
                synced += 1
                print(f"[Sopdrop] Synced: {slug}")

        except Exception as e:
            errors.append(f"{slug}: {e}")
            print(f"[Sopdrop] Failed to sync {slug}: {e}")
            import traceback
            traceback.print_exc()

    # Store team identity in the library database itself
    try:
        from .config import get_team_name as _get_team_name
        set_library_meta('team_slug', team_slug)
        team_name = _get_team_name() or team_slug
        set_library_meta('team_name', team_name)
    except Exception as e:
        print(f"[Sopdrop] Warning: could not write team metadata to library: {e}")

    # Trigger menu regeneration if we synced any assets
    if synced > 0:
        _trigger_menu_regenerate()

    return {
        'synced': synced,
        'skipped': skipped,
        'total': len(team_assets),
        'team': team_slug,
        'errors': errors if errors else None,
    }


def get_user_teams() -> List[Dict[str, Any]]:
    """
    Fetch list of teams the user belongs to.

    Returns:
        List of team objects with id, slug, name, role, etc.
    """
    from .api import SopdropClient
    from .config import get_token

    if not get_token():
        return []

    try:
        client = SopdropClient()
        result = client._get("teams")
        return result.get('teams', [])
    except Exception as e:
        print(f"[Sopdrop] Failed to fetch user teams: {e}")
        return []


# ==============================================================================
# Cross-Library Operations
# ==============================================================================

def copy_asset_to_library(asset_id: str, target_library: str) -> Optional[Dict[str, Any]]:
    """
    Copy an asset from current library to another library (personal or team).

    Args:
        asset_id: The asset ID to copy
        target_library: 'personal' or 'team'

    Returns:
        The newly created asset in the target library, or None on failure.
    """
    import shutil
    from .config import get_active_library, get_team_library_path, set_active_library

    current_library = get_active_library()

    if current_library == target_library:
        print(f"[Sopdrop] Asset is already in {target_library} library")
        return None

    # Validate target
    if target_library == "team":
        team_path = get_team_library_path()
        if not team_path:
            raise ValueError("Team library path not configured")

    # Load asset from current library
    asset = get_asset(asset_id)
    if not asset:
        raise ValueError(f"Asset not found: {asset_id}")

    asset_type = asset.get('asset_type', 'node')

    # Load thumbnail if exists
    thumbnail_data = None
    if asset.get('thumbnail_path'):
        thumb_path = get_library_thumbnails_dir() / asset['thumbnail_path']
        if thumb_path.exists():
            thumbnail_data = thumb_path.read_bytes()

    # For HDAs, copy the binary file directly instead of json-loading it
    if asset_type == 'hda':
        source_file = get_library_assets_dir() / asset['file_path']
        if not source_file.exists():
            raise ValueError(f"HDA file not found: {source_file}")

        # Switch to target library (close DB so it reconnects to the new path)
        close_db()
        set_active_library(target_library)
        try:
            # Build hda_info from existing asset metadata
            hda_info = {
                'library_path': str(source_file),
                'type_name': asset.get('hda_type_name', ''),
                'type_label': asset.get('hda_type_label', ''),
                'version': asset.get('hda_version', ''),
                'category': asset.get('hda_category', asset.get('context', 'Sop')),
            }
            new_asset = save_hda(
                name=asset['name'],
                hda_info=hda_info,
                description=asset.get('description', ''),
                tags=asset.get('tags', []),
                thumbnail_data=thumbnail_data,
                icon=asset.get('icon'),
            )

            if asset.get('remote_slug'):
                mark_asset_synced(
                    new_asset['id'],
                    asset['remote_slug'],
                    asset.get('remote_version', '1.0.0')
                )

            print(f"[Sopdrop] Copied HDA '{asset['name']}' to {target_library} library")
            return new_asset
        finally:
            close_db()
            set_active_library(current_library)

    # For node/vex assets, load the JSON package
    package = load_asset_package(asset_id)
    if not package:
        raise ValueError("Failed to load asset package")

    # Switch to target library (close DB so it reconnects to the new path)
    close_db()
    set_active_library(target_library)

    try:
        # Save asset in target library
        new_asset = save_asset(
            name=asset['name'],
            context=asset['context'],
            package_data=package,
            description=asset.get('description', ''),
            tags=asset.get('tags', []),
            thumbnail_data=thumbnail_data,
            icon=asset.get('icon'),
        )

        # Copy sync status if it was synced
        if asset.get('remote_slug'):
            mark_asset_synced(
                new_asset['id'],
                asset['remote_slug'],
                asset.get('remote_version', '1.0.0')
            )

        print(f"[Sopdrop] Copied '{asset['name']}' to {target_library} library")
        return new_asset

    finally:
        # Switch back to original library
        close_db()
        set_active_library(current_library)


def move_asset_to_library(asset_id: str, target_library: str) -> Optional[Dict[str, Any]]:
    """
    Move an asset from current library to another library (copy + delete).

    Args:
        asset_id: The asset ID to move
        target_library: 'personal' or 'team'

    Returns:
        The newly created asset in the target library, or None on failure.
    """
    # Copy first
    new_asset = copy_asset_to_library(asset_id, target_library)

    if new_asset:
        # Delete from source library
        delete_asset(asset_id)
        print(f"[Sopdrop] Moved asset to {target_library} library")

    return new_asset


def get_other_library_type() -> Optional[str]:
    """
    Get the other library type (for UI - "Copy to X Library").

    Returns:
        'personal' if currently in team, 'team' if currently in personal,
        or None if team library is not configured.
    """
    from .config import get_active_library, get_team_library_path

    current = get_active_library()

    if current == "team":
        return "personal"
    else:
        # Check if team is configured
        if get_team_library_path():
            return "team"
        return None
