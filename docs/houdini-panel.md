# Houdini Library Panel

The Qt-based library browser (`sopdrop_library_panel.py`, ~10k lines). Runs inside Houdini's embedded Qt environment.

## Widget Hierarchy

```
LibraryPanel (QWidget) ─────────────── Main container
├── CollectionListWidget (QWidget) ─── Sidebar (folders/collections)
│   └── _DropAwareContainer (QWidget)  Drop target for drag-reorder
├── AssetGridWidget (QWidget) ──────── Grid view of asset cards
│   └── AssetCardWidget (QFrame) ───── Individual asset card
│       └── AssetPopover (QFrame) ──── Hover detail popup (singleton)
├── _LibraryWorker (QThread) ───────── Background mirror refresh + asset query
├── _HttpThumbnailDispatcher (QObject)─ Singleton: 4-thread QThreadPool for
│   └── _HttpThumbnailRunnable          HTTP thumbnail fetch in HTTP team mode
├── Dialogs:
│   ├── SaveToLibraryDialog ────────── Save nodes to library
│   ├── SaveVexDialog ──────────────── Save VEX snippet
│   ├── SavePathDialog ─────────────── Save file path reference
│   ├── SaveCurvesDialog ───────────── Save animation curves
│   ├── SettingsDialog ─────────────── Library settings (NAS/HTTP team mode toggle)
│   ├── AssetDetailDialog ──────────── Full asset detail view (read-only)
│   ├── EditAssetDialog ────────────── Edit asset metadata
│   └── HoudiniIconBrowser ─────────── Browse Houdini icons
└── Helpers:
    ├── TagPill, TagFlowWidget, TagInputWidget ── Tag UI
    ├── ToastWidget ────────────────── Notification toasts
    ├── _SyncIcon ──────────────────── Cloud sync status icon
    └── FlowLayout ─────────────────── CSS-like flow layout
```

## Settings Dialog

Most-relevant section for the on-prem flow. The TEAM LIBRARY group has a mode radio:

- **Shared folder (NAS / SMB)** — legacy NAS path. Shows `Team Library Path:` field + Browse button + Team combo (auto-detected from NAS `library_meta`).
- **On-prem Sopdrop server** — HTTP mode. Hides the path field, shows only the Team combo (populated by Fetch Teams from `GET /api/v1/teams`).

The "Team Slug" identifier is **never exposed in the UI** — slug is internal, users pick their team from the dropdown which displays team names.

Visibility rules computed by `_apply_settings_visibility()`:

| Section | local_only OFF, NAS | local_only OFF, HTTP | local_only ON, NAS | local_only ON, HTTP (trust-LAN) |
|---|---|---|---|---|
| SERVER URL | visible | visible | hidden | visible |
| ACCOUNT (Login button) | visible | visible | hidden | **hidden** |
| Fetch Teams | visible | visible | hidden | visible |

The trust-LAN scenario (Local-only ON + HTTP team mode) hides the Login button entirely — identity comes from the workstation OS username via the `X-Sopdrop-User` header. See [on-prem.md](on-prem.md) for the auth model.

Status line text differs by mode:
- NAS: counts assets via local SQLite
- HTTP, token mode: `Connected to '<slug>' on <url> — N asset(s).`
- HTTP, trust-LAN: `Connected as <ws-user> to '<slug>' on <url> — N asset(s).`

Status probe is best-effort with a 5s timeout. Error messages distinguish trust-LAN failures (server `TRUST_LAN_AUTH=false`) from token failures.

## Startup & TAB Menu

### TAB Menu System (`menu.py`)

Assets from the library appear in Houdini's TAB menu as tools under `Sopdrop/Personal/[Collection]/[Name]` and `Sopdrop/Team/[Collection]/[Name]`. Each tool calls `sopdrop.menu.paste_asset(asset_id)` to paste the asset into the current network.

The TAB menu is a shelf file (`sopdrop_library.shelf`) with one `<tool>` per asset:
- Tools are context-filtered (SOP assets only appear in SOP networks, etc.)
- Tool names prefixed with `(SD)` to distinguish from native nodes
- VEX snippets are excluded from the TAB menu (not pasteable into networks)
- Each tool has keywords for TAB search: asset name, tags, context

### Startup Flow (`pythonrc.py`)

```
Houdini launches
  └── hou.ui.addEventLoopCallback(_deferred_init)  [waits for UI]
        └── _init_sopdrop_menu()
              ├── Load existing shelf file if present (instant, no DB)
              └── regenerate_menu(skip_team=True)
                    └── Query personal library only (fast, local SQLite)
                    └── Write shelf XML + hou.shelves.loadFile()
```

**Key design**: Startup never touches the NAS. `skip_team=True` means only personal library assets are in the TAB menu initially. Team assets are added later when the Library panel opens and the background mirror refresh completes (`_on_worker_finished` → `_regenerate_tab_menu()`).

### TAB Menu Regeneration Triggers

| Trigger | Includes team? | Reload shelf? |
|---------|---------------|---------------|
| Houdini startup (`pythonrc.py`) | No (`skip_team`) | Yes |
| Library panel opens (`__init__`) | Yes (after worker) | Yes |
| Ctrl+R refresh | Yes (after worker) | Yes |
| Save/delete/rename asset | Yes | No (skip_reload during modal dialogs) |
| Library switch | Yes (after worker) | Yes |

### Cross-Library Asset Lookup

When pasting from the TAB menu, `paste_asset()` in `menu.py` tries the current library first, then falls back to the other:

```
paste_asset(asset_id)
  ├── get_asset(asset_id)  [current library]
  ├── if not found:
  │     ├── Switch to other library (personal ↔ team)
  │     ├── get_asset(asset_id)
  │     └── Restore original library (via finally)
  ├── load_asset_package(asset_id)
  └── import_items(package, target, position)
```

This handles the common case where team assets are in the TAB menu from a previous session's shelf file but the current library is set to personal (startup didn't include team).

## Asset Card Thumbnails

Cards display thumbnails with a priority system:

1. **Saved screenshot** — loaded from `thumbnails/{uuid}.png`. Source depends on team library mode:
   - `nas` mode: lazy-copied from NAS to local mirror on first view.
   - `http` mode: fetched via the disk LRU at `~/.sopdrop/cache/thumbnails/` (hash-keyed by URL). Network fetches run on a 4-thread `QThreadPool` (`_HttpThumbnailDispatcher` in `sopdrop_library_panel.py`) and post bytes back to the main thread via a Qt signal. Server sets `Cache-Control: immutable` on `/library/*`, so warm clients make zero network calls. PNG decode happens on the main thread inside the dispatcher's `_on_loaded` slot, and the result is stashed in `AssetCardWidget._thumb_cache` for re-use.
2. **Houdini icon** — if asset has an `icon` field (e.g., `SOP_scatter`), drawn centered at 50% opacity
3. **Sopdrop logo** — `sopdrop_logo.svg` at 15% opacity
4. **Context letter** — fallback glyph: `{ }` for VEX, `~` for curves, first letter of context for everything else

## Asset Detail Dialog

Full read-only view opened via right-click > "View Details" or the card context menu.

**Layout:**
- **Thumbnail area** (top, 200px): Shows screenshot, or Houdini icon fallback (80px, 50% opacity), or context text
- **Name row**: Houdini icon (24px) + asset name + context badge (SOP/LOP/HDA)
- **Artist**: "by {created_by}" — shows the OS username of who saved the asset
- **Description**: Full text
- **Tags**: Clickable tag pills (navigate to tag filter)
- **Metadata grid**: Type, node count, node types, Houdini version, file size, use count, created date, collections, HDA type, license
- **HDA dependencies**: Listed with category and Sopdrop slug if available
- **Version history**: Each version with paste/revert buttons
- **Buttons**: Edit Details, Close

## Asset Popover (Hover)

Floating popup shown on card hover. Shows: name, context badge, artist ("by {created_by}"), description, metadata (node count, version, usage, date), node types, Houdini version, file size, collections, tags.

## Crash-Safety Patterns

These patterns are critical. Houdini embeds Qt, and widget lifecycle bugs cause silent segfaults. See [crash-safety.md](crash-safety.md) for the full list.

### Pattern 1: Static Timer Callbacks

**Problem**: Timer bound to instance method → instance destroyed → timer fires → segfault.

**Solution**: Use `@staticmethod` for timer callbacks. Access state through class-level variables, not `self`.

```python
# AssetCardWidget drag polling
@staticmethod
def _poll_drag_position():
    if not AssetCardWidget._custom_drag_active:
        if AssetCardWidget._drag_timer:
            AssetCardWidget._drag_timer.stop()
        return
    coll_widget = CollectionListWidget._active_instance
    if coll_widget:
        try:
            coll_widget.objectName()  # Liveness check
        except RuntimeError:
            CollectionListWidget._active_instance = None
            return
        # ... safe to use coll_widget ...
```

### Pattern 2: Signal Disconnect on Cleanup

**Problem**: Widget connects to `QApplication.applicationStateChanged`. Widget destroyed → signal fires → segfault.

**Solution**: Disconnect in both `closeEvent()` and `deleteLater()`.

```python
# AssetPopover
def _disconnect_app_signal(self):
    if self._app_signal_connected:
        try:
            app = QtWidgets.QApplication.instance()
            if app:
                app.applicationStateChanged.disconnect(self._on_app_state_changed)
        except (RuntimeError, TypeError):
            pass
        self._app_signal_connected = False

def closeEvent(self, event):
    self._disconnect_app_signal()
    super().closeEvent(event)

def deleteLater(self):
    self._disconnect_app_signal()
    super().deleteLater()
```

### Pattern 3: Class-Level Instance + `destroyed` Signal

**Problem**: Class variable `_active_instance` holds widget reference. Widget destroyed → stale reference → segfault.

**Solution**: Connect `destroyed` signal to static cleanup.

```python
# CollectionListWidget
_active_instance = None

def __init__(self, parent=None):
    super().__init__(parent)
    CollectionListWidget._active_instance = self
    self.destroyed.connect(CollectionListWidget._on_instance_destroyed)

@staticmethod
def _on_instance_destroyed():
    CollectionListWidget._active_instance = None
```

### Pattern 4: `objectName()` Liveness Guard

**Problem**: Need to use a widget reference but it might be destroyed.

**Solution**: Call `objectName()` first — it raises `RuntimeError` if the C++ object is gone.

```python
try:
    widget.objectName()  # Throws if deleted
except RuntimeError:
    return  # Widget is dead, bail out
# ... safe to use widget ...
```

Used in: drag timer, delete timer callbacks, mouseReleaseEvent, worker finished handler.

### Pattern 5: Delete Timer with Undo

**Problem**: 5-second undo window uses timer callback. Panel might close before timer fires.

**Solution**: Capture values in closure, guard with `objectName()`.

```python
_aid = asset_id  # Capture for closure
def _safe_finalize():
    try:
        self.objectName()  # Guard: panel still alive?
        self._finalize_delete(_aid)
    except RuntimeError:
        pass  # Panel was destroyed, skip
timer.timeout.connect(_safe_finalize)
```

## SaveToLibraryDialog

### Container HDA Handling

When saving a single node that is a custom HDA and a subnet with children:

```python
if node.isSubNetwork() and node.children():
    # Export the children, not the container
    self.items = list(node.allItems())
    self.container_hda = self.hda_info  # Store for metadata
    self.hda_info = None                # Not an HDA binary save
    self.is_hda = False
```

The `container_hda` metadata is stored in the package so the importer can reconstruct the container on paste.

### UI Stats Line

Adapts display based on asset type:
- **HDA**: Shows HDA label
- **Container HDA**: Shows container label + child node count
- **Regular nodes**: Shows node count + total (including sub-children)

## Background Library Loading

Team library reads go through a local SQLite mirror to avoid NAS latency (see [library.md](library.md)). The mirror refresh can take 2-30+ seconds, so it runs off the main thread.

### Architecture

```
_refresh_assets() [main thread]
  ├── Capture UI state (search text, filters, collection, sort)
  ├── Build query_fn closure (captures state, runs DB queries)
  ├── Cancel any in-flight _LibraryWorker
  ├── Personal library? → run query_fn() synchronously → _apply_assets()
  └── Team library? → show loading → spawn _LibraryWorker(query_fn)
                                         │
                                    [worker thread]
                                    ├── refresh_team_mirror()
                                    ├── query_fn()
                                    └── emit finished(result)
                                         │
                                    [main thread, via signal]
                                    └── _on_worker_finished(result)
                                        ├── Liveness guard (objectName())
                                        ├── Stale worker check (sender())
                                        ├── _apply_assets(result['assets'])
                                        └── _regenerate_tab_menu()
```

### Key design decisions

- **Personal library stays synchronous** — local SQLite with WAL is <50ms, no worker needed
- **UI state captured before spawning** — combo boxes and search text read on main thread, not worker
- **Closure-based query** — the `query_fn` lambda captures filter state so the worker is stateless
- **Cancellation** — `_worker.cancel()` + disconnect before starting a new worker; worker checks `_cancelled` flag between operations
- **Stale worker guard** — `_on_worker_finished` checks `self.sender() is self._worker` to ignore results from superseded workers
- **Liveness guard** — `objectName()` check in case the panel is destroyed before the worker finishes (see [crash-safety.md](crash-safety.md) Pattern 4)
- **Deferred scroll** — `reveal_asset()` stashes `_pending_scroll_to`; `_apply_assets()` picks it up after the grid is populated
- **Deferred TAB menu regen** — `_regenerate_tab_menu()` is deferred to `_on_worker_finished` when a worker is active, to avoid `close_db()` racing with the worker (see [crash-safety.md](crash-safety.md) Fix 13)
- **Slow worker logging** — Workers that take >2s print elapsed time and asset count to the console

## Paste Flow (Double-Click / TAB Menu)

```
Double-click AssetCardWidget (or TAB menu paste_asset)
  ├── VEX?  → copy code to clipboard
  ├── Path? → copy path to clipboard (+ paste into file parm if focused)
  ├── Curves? → apply keyframes to selected channels in Animation Editor
  ├── HDA?  → hou.hda.installFile() + createNode()
  └── Node? → load_asset_package() → import_items(package, target, cursor_pos)
```

Context mismatch (e.g., SOP asset in LOP network) prompts the user before pasting.

## Things to Never Do

1. **Never call `processEvents()`** — Re-entrancy in Houdini's Qt can destroy widgets mid-operation
2. **Never nest `hou.undos.group()`** — Exception propagation through nested groups corrupts the undo stack
3. **Never destroy items in arbitrary order** — Always destroy top-level items only; children are auto-destroyed
4. **Never bind timer callbacks to instance methods** — Use `@staticmethod` with class-level state
5. **Never hold widget references without liveness checks** — Always guard with `objectName()` before use
6. **Never call `close_db()` while a `_LibraryWorker` is running** — Wait on the worker first
7. **Never access NAS on the main thread during startup** — Use `skip_team=True` for startup menu generation
