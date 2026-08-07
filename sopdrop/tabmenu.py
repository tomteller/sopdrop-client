"""
Sopdrop Shift+Tab recipe menu.

Sopdrop's own TAB menu: a lightweight, searchable popup bound to
Shift+Tab in the network editor (see sopdrop-houdini's
scripts/python/nodegraphhooks.py). Shows every Sopdrop recipe —
personal + team — matching the current network context, without
injecting anything into Houdini's native TAB menu.

Interaction model (mirrors the native TAB menu):
  - Shift+Tab opens the popup at the mouse cursor, search field focused
  - Type to filter (name, tags, collection)
  - Up/Down to navigate, Enter (or click) to paste, Esc / click-outside
    to dismiss
  - The paste lands at the network position the mouse was at when
    Shift+Tab was pressed (captured before the mouse moves to the popup)

Crash-safety notes (docs/crash-safety.md patterns):
  - The widget is a Qt.Popup with WA_DeleteOnClose; a module-level strong
    ref keeps it alive while shown and is cleared from closeEvent().
  - The paste runs from a 0 ms single-shot timer so it executes AFTER the
    popup has fully closed — never re-entrant with popup event handling.
    The deferred closure captures only plain data (asset id, pane,
    position tuple), no widget references.
  - No processEvents(), no exec_(), no nested event loops.
"""

import traceback

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets


# Strong reference to the currently-open menu (a Qt.Popup with
# WA_DeleteOnClose would otherwise be garbage-collected mid-show).
_menu_instance = None


def _scaled(size):
    """DPI-scale a pixel size using Houdini's UI scale when available."""
    try:
        import hou
        return hou.ui.scaledSize(size)
    except Exception:
        return size


class _RecipeMenu(QtWidgets.QFrame):
    """Searchable popup listing pasteable Sopdrop recipes."""

    def __init__(self, entries, pane, position, empty_text, parent=None):
        super().__init__(parent)
        self._entries = entries          # list of dicts (plain data only)
        self._pane = pane                # hou.NetworkEditor to paste into
        self._position = position        # (x, y) network coords or None
        self._empty_text = empty_text

        self.setWindowFlags(
            QtCore.Qt.Popup
            | QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.NoDropShadowWindowHint
        )
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setFixedSize(_scaled(340), _scaled(400))
        self.setStyleSheet("""
            QFrame {
                background-color: #2b2b2b;
                border: 1px solid #555555;
            }
            QLineEdit {
                background-color: #1e1e1e;
                border: 1px solid #444444;
                border-radius: 2px;
                padding: 4px 6px;
                color: #e0e0e0;
                selection-background-color: #cc7a29;
            }
            QListWidget {
                background-color: #2b2b2b;
                border: none;
                color: #d0d0d0;
                outline: none;
            }
            QListWidget::item {
                padding: 3px 6px;
            }
            QListWidget::item:selected {
                background-color: #cc7a29;
                color: #ffffff;
            }
            QLabel {
                color: #888888;
                border: none;
            }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        m = _scaled(6)
        layout.setContentsMargins(m, m, m, m)
        layout.setSpacing(_scaled(4))

        header = QtWidgets.QLabel("Sopdrop Recipes")
        header.setStyleSheet("color: #cc7a29; font-weight: bold; border: none;")
        layout.addWidget(header)

        self.search = QtWidgets.QLineEdit(self)
        self.search.setPlaceholderText("Search recipes...")
        self.search.textChanged.connect(self._refilter)
        self.search.installEventFilter(self)
        layout.addWidget(self.search)

        self.list = QtWidgets.QListWidget(self)
        self.list.setUniformItemSizes(True)
        self.list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(lambda _item: self._trigger())
        layout.addWidget(self.list, 1)

        self._refilter("")

    # ── filtering / navigation ──────────────────────────────────────────

    def _refilter(self, text):
        text = (text or "").strip().lower()
        self.list.clear()

        for entry in self._entries:
            if text and text not in entry["haystack"]:
                continue
            item = QtWidgets.QListWidgetItem(entry["label"])
            item.setData(QtCore.Qt.UserRole, entry["id"])
            item.setToolTip(entry.get("tooltip", ""))
            self.list.addItem(item)

        if self.list.count() == 0:
            placeholder = QtWidgets.QListWidgetItem(
                self._empty_text if not text else "No matching recipes")
            placeholder.setFlags(QtCore.Qt.NoItemFlags)
            self.list.addItem(placeholder)
        else:
            self.list.setCurrentRow(0)

    def _move_selection(self, delta):
        count = self.list.count()
        if count == 0:
            return
        row = self.list.currentRow()
        row = max(0, min(count - 1, (0 if row < 0 else row) + delta))
        self.list.setCurrentRow(row)

    def eventFilter(self, obj, event):
        """Route navigation keys from the search field to the list."""
        if obj is self.search and event.type() == QtCore.QEvent.KeyPress:
            key = event.key()
            if key == QtCore.Qt.Key_Down:
                self._move_selection(1)
                return True
            if key == QtCore.Qt.Key_Up:
                self._move_selection(-1)
                return True
            if key == QtCore.Qt.Key_PageDown:
                self._move_selection(8)
                return True
            if key == QtCore.Qt.Key_PageUp:
                self._move_selection(-8)
                return True
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                self._trigger()
                return True
            if key == QtCore.Qt.Key_Escape:
                self.close()
                return True
        return super().eventFilter(obj, event)

    # ── paste ───────────────────────────────────────────────────────────

    def _trigger(self):
        item = self.list.currentItem()
        if item is None:
            return
        asset_id = item.data(QtCore.Qt.UserRole)
        if not asset_id:
            return

        # Capture plain locals BEFORE closing — after close() this widget
        # is scheduled for deletion and must not be referenced again.
        pane = self._pane
        position = self._position
        self.close()

        # Defer the paste one event-loop tick so it runs after the popup
        # is gone (never re-entrant with popup teardown).
        QtCore.QTimer.singleShot(0, lambda: _do_paste(asset_id, pane, position))

    def closeEvent(self, event):
        global _menu_instance
        if _menu_instance is self:
            _menu_instance = None
        super().closeEvent(event)


def _do_paste(asset_id, pane, position):
    """Paste an asset (runs from a single-shot timer on the main thread)."""
    try:
        from . import menu as _menu
        _menu.paste_asset(asset_id, pane=pane, position=position)
    except Exception as e:
        traceback.print_exc()
        try:
            import hou
            hou.ui.displayMessage(f"Sopdrop paste failed: {e}", title="Sopdrop")
        except Exception:
            pass


# ── entry point (called from nodegraphhooks.py) ─────────────────────────


def _context_of(node):
    """Sopdrop context name ('sop', 'lop', ...) for a network, or 'unknown'."""
    try:
        from .importer import _get_context
        return _get_context(node)
    except Exception:
        return "unknown"


def _build_entries(pane):
    """Collect + filter recipes for the pane's current network context."""
    from .menu import collect_menu_assets, CONTEXT_TO_NETTYPE

    editor_ctx = _context_of(pane.pwd())
    editor_net = CONTEXT_TO_NETTYPE.get(editor_ctx)

    personal, team = collect_menu_assets(quiet=True)

    entries = []
    for lib_name, assets in (("Personal", personal), ("Team", team)):
        for asset in assets:
            asset_type = asset.get("asset_type", "node")
            asset_ctx = (asset.get("context") or "sop").lower()

            # Same exclusions as the native TAB menu shelf
            if asset_type == "vex" or asset_ctx in ("vex", "path"):
                continue

            # Context filter (normalized through the net-type map so
            # cop/cop2 and out/rop aliases match). Unknown contexts on
            # either side fall through and are shown.
            asset_net = CONTEXT_TO_NETTYPE.get(asset_ctx)
            if editor_net and asset_net and asset_net != editor_net:
                continue

            name = asset.get("name", "Untitled")
            collections = asset.get("collections") or []
            if collections and isinstance(collections[0], dict):
                coll_name = collections[0].get("name") or ""
            else:
                coll_name = ""

            where = f"{lib_name}/{coll_name}" if coll_name else lib_name
            tags = [t.lower() for t in (asset.get("tags") or [])]

            entries.append({
                "id": asset.get("id", ""),
                "label": f"{name}    ({where})",
                "tooltip": asset.get("description") or "",
                "haystack": " ".join(
                    [name.lower(), lib_name.lower(), coll_name.lower()] + tags),
                "sort": (lib_name != "Personal", name.lower()),
            })

    entries.sort(key=lambda e: e["sort"])
    return entries, editor_ctx


def show_tab_menu(editor=None):
    """Open the Shift+Tab recipe menu at the mouse cursor.

    Args:
        editor: The hou.NetworkEditor the key was pressed in (from the
            nodegraph hook). Falls back to the first network editor.
    """
    import hou

    pane = editor
    if pane is None:
        pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    if pane is None:
        return

    # Capture the paste position NOW — the mouse is about to move away
    # from the network view to interact with the popup.
    position = None
    try:
        cursor = pane.cursorPosition()
        position = (cursor[0], cursor[1])
    except Exception:
        pass

    try:
        entries, editor_ctx = _build_entries(pane)
    except Exception as e:
        traceback.print_exc()
        print(f"[Sopdrop] Shift+Tab menu failed to load recipes: {e}")
        return

    ctx_label = editor_ctx.upper() if editor_ctx != "unknown" else "this"
    empty_text = f"No Sopdrop recipes for {ctx_label} networks"

    parent = None
    try:
        parent = hou.qt.mainWindow()
    except Exception:
        pass

    global _menu_instance
    # If a previous popup is somehow still open, close it first.
    if _menu_instance is not None:
        try:
            _menu_instance.close()
        except Exception:
            pass
        _menu_instance = None

    popup = _RecipeMenu(entries, pane, position, empty_text, parent=parent)
    _menu_instance = popup

    # Place at the mouse, clamped so the popup stays on-screen.
    pos = QtGui.QCursor.pos()
    try:
        screen = QtGui.QGuiApplication.screenAt(pos)
        if screen is not None:
            geo = screen.availableGeometry()
            x = min(max(pos.x(), geo.left()), geo.right() - popup.width())
            y = min(max(pos.y(), geo.top()), geo.bottom() - popup.height())
            pos = QtCore.QPoint(x, y)
    except Exception:
        pass
    popup.move(pos)
    popup.show()
    popup.search.setFocus()
