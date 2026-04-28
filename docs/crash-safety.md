# Crash Safety

All crash mitigations applied to the Sopdrop Houdini client. Follow these patterns for all future code.

## Applied Fixes (April 2026)

### 1. Drag Timer — Static Method

**File**: `sopdrop_library_panel.py`
**Problem**: `_poll_drag_position` was an instance method on `AssetCardWidget`. Timer connected to `self._poll_drag_position`. If the card widget is destroyed mid-drag, the timer fires into a dead object → segfault.
**Fix**: Made `_poll_drag_position` a `@staticmethod`. Timer connects to `AssetCardWidget._poll_drag_position` (class, not instance). All state accessed through class variables (`_custom_drag_active`, `_drag_timer`).

### 2. AssetPopover Signal Leak

**File**: `sopdrop_library_panel.py`
**Problem**: `AssetPopover.__init__` connects to `QApplication.applicationStateChanged` but never disconnects. When the popover is destroyed, the signal still references the dead object → segfault on any focus change.
**Fix**: Added `_disconnect_app_signal()` method. Called from both `closeEvent()` and `deleteLater()`. Tracks connection state with `_app_signal_connected` boolean.

### 3. `_active_instance` Stale Reference

**File**: `sopdrop_library_panel.py`
**Problem**: `CollectionListWidget._active_instance` (class variable) never cleared on widget destruction. Drag code accesses it → segfault if widget is dead.
**Fix**: Connected `destroyed` signal to `CollectionListWidget._on_instance_destroyed` (static method) which sets `_active_instance = None`. Usage sites also guard with `objectName()`.

### 4. `processEvents()` Removal

**File**: `sopdrop_library_panel.py`
**Problem**: Three calls to `processEvents()` pumped the event loop during operations. In Houdini's embedded Qt, this re-entrancy can destroy widgets mid-operation or trigger recursive event handling → segfault.
**Fix**: Removed all 3 calls. Operations now complete without pumping the event loop.

### 5. Nested Undo Group

**File**: `importer.py`
**Problem**: `import_items()` wraps in `hou.undos.group()`, then `_import_v1()` had a second nested group. If an exception propagates through nested undo groups, Houdini's undo stack gets corrupted → potential crash on undo.
**Fix**: Removed the inner `hou.undos.group()` from `_import_v1()`. Only the outer group in `import_items()` remains.

### 6. Dead Node Validation in Export

**File**: `export.py`
**Problem**: If a user deletes nodes between selection and export (or stale references are passed), accessing a dead `hou.Node` can segfault.
**Fix**: Added `_validate_items()` function that calls `item.name()` on each item before export. Dead items raise `hou.ObjectWasDeleted` (or segfault) — caught by try/except, item skipped.

### 7. Unsafe Destroy Order in Retry Cleanup

**File**: `importer.py`
**Problem**: On V1 import failure, cleanup iterated a `set` of items and destroyed them in arbitrary order. Destroying a child after its parent was already destroyed → segfault.
**Fix**: Filter to only top-level items (direct children of `target_node`, or non-node items like network boxes/stickies/dots). Children are auto-destroyed when their parent is destroyed.

### 8. `tempfile.mktemp()` → `tempfile.mkstemp()`

**Files**: `export.py`, `importer.py`
**Problem**: `tempfile.mktemp()` is deprecated — returns a filename without creating it (TOCTOU race condition). Another process could create the file between name generation and use.
**Fix**: Replaced with `tempfile.mkstemp()` which atomically creates the file and returns `(fd, path)`. The fd is properly closed after use.

### 9. Delete Timer Liveness Guards

**File**: `sopdrop_library_panel.py`
**Problem**: Delete timers (5-second undo window) use closures that capture `self`. If the panel is destroyed before the timer fires, accessing `self` → segfault.
**Fix**: Wrapped timer callbacks in `objectName()` liveness check:
```python
def _safe_finalize():
    try:
        self.objectName()
        self._finalize_delete(asset_id)
    except RuntimeError:
        pass
```
Applied to both single delete and bulk delete timers.

### 10. SQLite Connection Leak

**File**: `library.py`
**Problem**: `detect_team_from_library()` opened a SQLite connection but some error paths didn't close it. Repeated calls (e.g., setup wizard checking multiple paths) leak connections.
**Fix**: Wrapped in `try/finally` with `conn.close()`.

### 11. Package Size Guard

**File**: `importer.py`
**Problem**: V2 import base64-decodes `package["data"]` into memory. A 2 GB base64 string would decode to ~1.5 GB binary, potentially OOM-killing Houdini.
**Fix**: Added 667 MB max for base64-encoded data (~500 MB decoded). Rejects with clear error message before attempting decode.

### 12. Background Library Loading

**File**: `sopdrop_library_panel.py`, `library.py`
**Problem**: Team library mirror refresh (`sqlite3.backup()` over NAS) takes 2-30+ seconds. Ran synchronously on the main thread during `_refresh_assets()` and `_select_library()`, freezing the entire Houdini UI.
**Fix**: Added `_LibraryWorker(QThread)` that runs mirror refresh + asset query off the main thread. `_refresh_assets()` shows loading state, spawns worker, and populates grid when worker emits `finished` signal. Personal library remains synchronous (fast, local SQLite). Worker results guarded with `objectName()` liveness check and `sender()` identity check to ignore stale workers.

### 13. TAB Menu Regen Racing with Background Worker

**Files**: `sopdrop_library_panel.py`, `sopdrop/menu.py`
**Problem**: `_regenerate_tab_menu()` was called in `LibraryPanel.__init__()` immediately after `_refresh_assets()`. For team libraries, `_refresh_assets()` spawns a `_LibraryWorker` background thread that queries the database. `regenerate_menu()` calls `close_db()` to switch between personal and team library queries, closing the connection the background worker is actively using → segfault.
**Fix**:
- In `__init__`: skip `_regenerate_tab_menu()` if a worker was spawned (`self._worker is not None`)
- In `_on_worker_finished`: call `_regenerate_tab_menu()` after worker completes (safe timing)
- Same pattern applied to `_refresh_all()` (Ctrl+R)
- In `_select_library()`: cancel and `wait(500)` on in-flight worker **before** calling `close_db()`
- Wrap `finished.disconnect()` in try/except for `RuntimeError`/`TypeError` (signal may already be disconnected)

### 14. NameError in HDA Dependency Check

**File**: `importer.py`
**Problem**: `_check_missing_hdas()` defined `cat` and `all_types` inside a `try` block. If the block failed after `cat` was set but before `all_types`, the fallback debug logging referenced `all_types` → `NameError`.
**Fix**: Initialize `cat = None` and `all_types = None` before the try block. Guard the fallback with `if cat and all_types is not None`.

### 15. NAS Database Contention ("database is locked")

**Files**: `library.py`
**Problem**: Multiple workstations connecting to the shared NAS `library.db` simultaneously. `_get_nas_db()` ran `executescript(SCHEMA)` on every new connection, acquiring an exclusive write lock even when tables already existed. `detect_team_from_library()` and `refresh_team_mirror()` had no `busy_timeout`, so they failed instantly when another workstation held a lock. `record_asset_use()` was decorated with `@_writes_to_nas`, routing usage tracking writes to the NAS (unnecessary — usage is per-user/local).
**Fix**:
- `_get_nas_db()`: Check if `library_assets` table exists before running schema; increased `busy_timeout` to 15000ms
- `detect_team_from_library()`: Added `timeout=10` and `busy_timeout = 10000`
- `refresh_team_mirror()`: Added `timeout=15` and `busy_timeout = 15000` on source connection
- `get_db()` mirror + personal paths: Conditional schema (skip if tables exist)
- `record_asset_use()`: Removed `@_writes_to_nas` (usage is local), wrapped body in try/except (non-critical)
- `menu.py`: Wrapped `record_asset_use()` calls in try/except

### 16. TAB Menu Paste Racing with Background Worker

**Files**: `menu.py`, `importer.py`
**Problem**: `paste_asset()` (called from TAB menu tools) calls `close_db()` when switching between personal/team libraries to find an asset. If a `_LibraryWorker` is still running (e.g. user just opened the Library panel and the team mirror refresh is in progress), `close_db()` closes the SQLite connection the worker is actively querying on its background thread → segfault. Same class of bug as Fix #13 but in a different code path. Additionally, `_import_v2()` called `item.parent()` on newly-loaded nodes without try/except (every other similar call in the function was protected).
**Fix**:
- Added `_wait_for_library_worker()` in `menu.py` that checks `_active_panels` for any running `_LibraryWorker` and calls `worker.wait(2000)` before `close_db()`
- Called `_wait_for_library_worker()` before every `close_db()` in `paste_asset()`
- Wrapped `item.parent()` call in `_import_v2()` (line 444) with try/except to match surrounding code

## Rules for Future Code

### Timer Callbacks

- Use `@staticmethod` for any timer callback
- Access state through class variables, not `self`
- Always check liveness before accessing widget references
- Always stop timers in cleanup paths

### Signal Connections

- Track connection state with a boolean
- Disconnect in `closeEvent()` AND `deleteLater()`
- Wrap disconnect in try/except for `RuntimeError` and `TypeError`
- Never connect to application-level signals without a disconnect plan

### Widget References

- Never store widget references in class variables without a `destroyed` signal handler
- Always guard stale references with `objectName()` before use
- Use `deleteLater()` (never `delete()`) for widget cleanup
- Remove layout items with `takeAt()` before calling `deleteLater()`

### Houdini Node References

- Call `item.name()` to validate before accessing properties
- Wrap in try/except — dead nodes can segfault or raise `hou.ObjectWasDeleted`
- When destroying nodes, only destroy top-level items — children auto-destroy

### Event Loop

- Never call `processEvents()` in Houdini's embedded Qt
- Never call `QApplication.exec_()` or similar re-entrant methods
- Use single-shot timers for deferred work instead

### Undo Groups

- One `hou.undos.group()` per user-visible operation
- Never nest undo groups
- Keep the group in the outermost entry point (`import_items()`, not `_import_v1()`)

### File I/O

- Use `tempfile.mkstemp()` (never `mktemp()`) for temp files
- Always close file descriptors from `mkstemp()`
- Use atomic write helpers for library files (`_atomic_write_text`, `_atomic_write_bytes`)
- Guard against oversized inputs before allocating memory

### Background Threads

- Use `QThread` (not Python `threading.Thread`) — Qt signals cross threads safely
- Never access widgets from a worker thread — only return data via signals
- Guard signal handlers with `objectName()` liveness check (widget may be destroyed before the worker finishes)
- Check `self.sender() is self._worker` to ignore results from stale/superseded workers
- Cancel in-flight workers before starting a new one (set a `_cancelled` flag and disconnect `finished`)
- Personal library queries run synchronously (fast, local SQLite) — only team library uses workers

### SQLite

- Always close connections in `finally` blocks
- Use `PRAGMA mmap_size = 0` for team/network libraries (NAS DB only — mirror is local)
- Use `PRAGMA busy_timeout = 15000` for NAS connections, `5000` for local
- Skip WAL mode on network drives (but enable WAL on the local mirror — major perf win)
- Use `sqlite3.Connection.backup()` for mirror refresh — safe against concurrent NAS writers
- **Never run `executescript(SCHEMA)` unconditionally on NAS** — it acquires an exclusive write lock; check if `library_assets` table exists first
- **Never call `close_db()` while a `_LibraryWorker` is running** — it closes the connection mid-query → segfault
- When cancelling a worker before `close_db()`, call `worker.wait(500)` to let it finish its current operation
- Non-critical DB operations (usage tracking, etc.) must catch all exceptions — never fail the parent operation
