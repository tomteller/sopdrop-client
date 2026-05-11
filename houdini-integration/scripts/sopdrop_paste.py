"""
Sopdrop Paste Tool

Quick paste from Sopdrop clipboard - works like native Ctrl+V.
Pastes at cursor position with minimal interruption.
"""

import hou
import json

# Try PySide6 first (Houdini 20+), fall back to PySide2
try:
    from PySide6 import QtCore, QtGui, QtWidgets
    PYSIDE_VERSION = 6
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
        PYSIDE_VERSION = 2
    except ImportError:
        QtCore = None
        QtGui = None
        QtWidgets = None
        PYSIDE_VERSION = 0


# Houdini-matching dark theme colors
COLORS = {
    'bg_dark': '#1a1a1a',
    'bg_medium': '#252525',
    'bg_light': '#2d2d2d',
    'bg_hover': '#363636',
    'border': '#3d3d3d',
    'border_light': '#4a4a4a',
    'text': '#e0e0e0',
    'text_dim': '#808080',
    'text_bright': '#ffffff',
    'accent': '#22c55e',  # Green for paste
    'accent_hover': '#34d66d',
    'accent_pressed': '#16a34a',
    'warning': '#fbbf24',
    'error': '#f87171',
    'sop': '#4a9eff',
    'lop': '#f97316',
    'obj': '#fbbf24',
    'vop': '#a855f7',
    'dop': '#ef4444',
    'cop': '#06b6d4',
    'top': '#22c55e',
    'chop': '#ec4899',
    'rop': '#6366f1',
}


def main():
    """Main entry point for the paste tool."""
    # Check for sopdrop module
    try:
        import sopdrop
        from sopdrop.config import get_config_dir
    except ImportError:
        hou.ui.displayMessage(
            "Sopdrop client not installed.\n\n"
            "Install with: pip install sopdrop\n"
            "Or add sopdrop-client to your PYTHONPATH.",
            title="Sopdrop - Not Installed",
            severity=hou.severityType.Error,
        )
        return

    # Get current network editor
    pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    if not pane:
        hou.ui.displayMessage(
            "No network editor found.",
            title="Sopdrop",
            severity=hou.severityType.Error,
        )
        return

    # First, check system clipboard for sopdrop.paste("slug") command
    system_slug = _check_system_clipboard()
    if system_slug:
        # Found a paste command in system clipboard - show confirm dialog
        if PYSIDE_VERSION > 0:
            try:
                _show_paste_dialog(system_slug, pane)
                return
            except Exception as e:
                print(f"Modern UI failed: {e}")
        # Fallback
        _confirm_and_paste_fallback(system_slug, pane)
        return

    # Check Sopdrop local clipboard for cached package
    clipboard = _get_clipboard()

    if clipboard:
        # Quick paste from local clipboard (already fetched)
        _quick_paste(clipboard, pane)
        return

    # Cross-workstation Quick Copy: in HTTP team mode the server tracks
    # team-scoped temporary shares. Walking to another workstation and
    # hitting Paste should find the most recent team share so the user
    # doesn't have to copy the share code across machines (system
    # clipboards don't sync over LAN by default). Falls through to the
    # browse dialog when no active share or not configured.
    team_share = _try_fetch_latest_team_share()
    if team_share:
        slug = f"s/{team_share}"
        if PYSIDE_VERSION > 0:
            try:
                _show_paste_dialog(slug, pane)
                return
            except Exception as e:
                print(f"Modern UI failed: {e}")
        _confirm_and_paste_fallback(slug, pane)
        return

    # No clipboard - show search/browse dialog
    if PYSIDE_VERSION > 0:
        try:
            _show_browse_dialog_modern(pane)
            return
        except Exception as e:
            print(f"Modern UI failed: {e}")
    _show_browse_dialog_fallback()


def _try_fetch_latest_team_share():
    """Return the share_code of the latest non-expired team share if
    HTTP team mode is configured, otherwise None. Best-effort — any
    failure (offline, no team configured, no active share) returns
    None so the paste flow falls through cleanly."""
    try:
        from sopdrop.config import (
            get_team_library_mode, get_team_slug, get_active_library,
        )
        # Only try this when team mode is the current focus AND HTTP-
        # backed; otherwise the user's NAS team or personal library
        # paths handle their own clipboard story.
        if get_team_library_mode() != 'http':
            return None
        team_slug = get_team_slug()
        if not team_slug:
            return None
        from sopdrop.api import SopdropClient
        return SopdropClient().fetch_latest_team_share(team_slug)
    except Exception as e:
        print(f"[Sopdrop] latest team share lookup failed: {e}")
        return None


def _check_system_clipboard():
    """Check system clipboard for sopdrop paste commands or share URLs."""
    import re
    try:
        if PYSIDE_VERSION == 0:
            return None

        clipboard = QtWidgets.QApplication.clipboard()
        text = clipboard.text().strip()

        if not text:
            return None

        # Look for sopdrop.paste("user/asset"), sopdrop.paste("s/CODE"), or sopdrop.paste("t/CODE") pattern
        match = re.search(r'sopdrop\.paste\(["\']([^"\']+)["\']\)', text)
        if match:
            return match.group(1)

        # Look for share URLs: sopdrop.com/s/TC-XXXX or */s/TC-XXXX
        match = re.search(r'(?:sopdrop\.com|localhost:\d+)/s/([A-Z]{2}-[A-Z0-9]{4})\b', text)
        if match:
            return f"s/{match.group(1)}"

        # Look for team share code pattern: t/XX-XXXX (bare, not in a paste command)
        match = re.search(r'\bt/([A-Z]{2}-[A-Z0-9]{4})\b', text)
        if match:
            return f"t/{match.group(1)}"

        # Look for library link pattern: lib/slug-name (bare, not in a paste command)
        match = re.search(r'\blib/([a-z0-9][a-z0-9-]*[a-z0-9])\b', text)
        if match:
            return f"lib/{match.group(1)}"

    except Exception as e:
        print(f"Could not check system clipboard: {e}")

    return None


def _get_clipboard():
    """Get the current Sopdrop local clipboard content (cached package)."""
    try:
        from sopdrop.config import get_clipboard
        return get_clipboard()
    except Exception as e:
        print(f"Could not read clipboard: {e}")
    return None


def _save_to_clipboard(slug, package):
    """Save to local clipboard for quick re-paste."""
    try:
        from sopdrop.config import set_clipboard
        set_clipboard(slug, package)
    except Exception:
        pass


def _get_context(node):
    """Get the Houdini context from a node."""
    try:
        category = node.childTypeCategory().name().lower()
        context_map = {
            'sop': 'sop', 'object': 'obj', 'vop': 'vop',
            'dop': 'dop', 'cop2': 'cop', 'top': 'top',
            'lop': 'lop', 'chop': 'chop', 'shop': 'shop',
            'rop': 'out', 'driver': 'out',
        }
        return context_map.get(category, category)
    except Exception:
        return 'unknown'


def _get_paste_position(pane):
    """Get the best position to paste at (center of visible network view)."""
    try:
        bounds = pane.visibleBounds()
        center = bounds.center()
        position = (center[0], center[1])
        print(f"[Sopdrop] Paste position: view center ({position[0]:.1f}, {position[1]:.1f})")
        return position
    except Exception:
        pass

    return (0, 0)


# ============================================================
# Modern PySide Dialogs
# ============================================================

class PasteConfirmDialog(QtWidgets.QDialog):
    """Modern paste confirmation dialog."""

    def __init__(self, slug, pane, asset_info=None, local_asset=None, parent=None):
        if parent is None:
            parent = hou.qt.mainWindow()
        super().__init__(parent)

        self.slug = slug
        self.pane = pane
        self.asset_info = asset_info or {}
        self.local_asset = local_asset
        self.result_success = False
        self.result_view_library = False
        self.result_save_library = False

        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Sopdrop")
        self.setFixedWidth(360)
        self.setStyleSheet(self._get_stylesheet())
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 20)
        layout.setSpacing(14)

        # Top accent bar
        accent_bar = QtWidgets.QFrame()
        accent_bar.setFixedHeight(3)
        accent_bar.setStyleSheet(f"background: {COLORS['accent']}; border: none; border-radius: 1px; margin: 0px;")
        layout.addWidget(accent_bar)

        # Header: [S mark] [Asset name + slug] [context pill]
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)

        mark = QtWidgets.QLabel("S")
        mark.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {COLORS['bg_dark']}; "
            f"background: {COLORS['accent']}; border-radius: 5px;"
        )
        mark.setFixedSize(28, 28)
        mark.setAlignment(QtCore.Qt.AlignCenter)
        header.addWidget(mark)

        title_col = QtWidgets.QVBoxLayout()
        title_col.setSpacing(1)

        name = self.asset_info.get("name", self.slug.split("/")[-1])
        title = QtWidgets.QLabel(name)
        title.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {COLORS['text_bright']};")
        title_col.addWidget(title)

        slug_label = QtWidgets.QLabel(self.slug)
        slug_label.setStyleSheet(f"font-size: 11px; color: {COLORS['text_dim']};")
        title_col.addWidget(slug_label)

        header.addLayout(title_col)
        header.addStretch()

        context = self.asset_info.get("context", self.asset_info.get("houdini_context", "?"))
        context_color = COLORS.get(context.lower(), COLORS['text_dim'])
        context_badge = QtWidgets.QLabel(context.upper())
        context_badge.setStyleSheet(
            f"background: {context_color}22; color: {context_color}; font-size: 10px; "
            f"font-weight: 600; padding: 3px 8px; border-radius: 4px; "
            f"border: 1px solid {context_color}44;"
        )
        header.addWidget(context_badge)

        layout.addLayout(header)

        # Separator
        sep = QtWidgets.QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {COLORS['border']}; border: none;")
        layout.addWidget(sep)

        # Info card
        card = QtWidgets.QFrame()
        card.setStyleSheet(
            f"background: {COLORS['bg_medium']}; border: 1px solid {COLORS['border']}; border-radius: 6px;"
        )
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(14, 10, 14, 10)
        card_layout.setSpacing(6)

        owner = self.asset_info.get("owner", {})
        owner_name = owner.get("username", self.slug.split("/")[0]) if isinstance(owner, dict) else str(owner)
        node_count = self.asset_info.get("nodeCount", self.asset_info.get("node_count", "?"))

        for label, value in [("Author", owner_name), ("Nodes", str(node_count))]:
            row = QtWidgets.QHBoxLayout()
            label_w = QtWidgets.QLabel(label)
            label_w.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
            label_w.setFixedWidth(50)
            row.addWidget(label_w)

            value_w = QtWidgets.QLabel(value)
            value_w.setStyleSheet(f"color: {COLORS['text']}; font-size: 11px;")
            row.addWidget(value_w)
            row.addStretch()

            card_layout.addLayout(row)

        layout.addWidget(card)

        # Context mismatch warning
        target_context = _get_context(self.pane.pwd())
        package_context = context.lower() if context != "?" else "unknown"

        if target_context != "unknown" and package_context != "unknown" and target_context != package_context:
            warning = QtWidgets.QFrame()
            warning.setStyleSheet(
                f"background: {COLORS['warning']}15; border: 1px solid {COLORS['warning']}33; border-radius: 6px;"
            )
            warning_layout = QtWidgets.QHBoxLayout(warning)
            warning_layout.setContentsMargins(10, 6, 10, 6)

            warning_text = QtWidgets.QLabel(
                f"Context mismatch: asset is {package_context.upper()}, "
                f"current network is {target_context.upper()}"
            )
            warning_text.setStyleSheet(f"color: {COLORS['warning']}; font-size: 11px;")
            warning_text.setWordWrap(True)
            warning_layout.addWidget(warning_text)

            layout.addWidget(warning)

        layout.addStretch()

        # Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(8)

        # Library button — show for lib/ and remote slugs, not team shares
        if not self.slug.startswith("t/"):
            if self.local_asset:
                lib_btn = QtWidgets.QPushButton("View in Library")
                lib_btn.setStyleSheet(self._button_style())
                lib_btn.clicked.connect(self._on_view_library)
                btn_layout.addWidget(lib_btn)
            elif not self.slug.startswith("lib/"):
                # Save to Library only for cloud/remote assets
                lib_btn = QtWidgets.QPushButton("Save to Library")
                lib_btn.setStyleSheet(self._button_style())
                lib_btn.clicked.connect(self._on_save_library)
                btn_layout.addWidget(lib_btn)

        btn_layout.addStretch()

        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.setStyleSheet(self._button_style())
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        paste_btn = QtWidgets.QPushButton("Paste")
        paste_btn.setStyleSheet(self._button_style(primary=True))
        paste_btn.setFixedWidth(90)
        paste_btn.clicked.connect(self._on_paste)
        paste_btn.setDefault(True)
        btn_layout.addWidget(paste_btn)

        layout.addLayout(btn_layout)

    def _get_stylesheet(self):
        return f"""
            QDialog {{
                background-color: {COLORS['bg_dark']};
                color: {COLORS['text']};
                font-family: "Segoe UI", "SF Pro Display", sans-serif;
            }}
        """

    def _button_style(self, primary=False):
        if primary:
            return f"""
                QPushButton {{
                    background-color: {COLORS['accent']};
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-size: 13px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {COLORS['accent_hover']};
                }}
                QPushButton:pressed {{
                    background-color: {COLORS['accent_pressed']};
                }}
            """
        return f"""
            QPushButton {{
                background-color: transparent;
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_hover']};
                border-color: {COLORS['border_light']};
            }}
        """

    def _on_view_library(self):
        self.result_view_library = True
        self.accept()

    def _on_save_library(self):
        self.result_save_library = True
        self.accept()

    def _on_paste(self):
        self.result_success = True
        self.accept()


class BrowseDialog(QtWidgets.QDialog):
    """Modern browse/search dialog when clipboard is empty."""

    def __init__(self, pane, parent=None):
        if parent is None:
            parent = hou.qt.mainWindow()
        super().__init__(parent)

        self.pane = pane
        self.slug_to_paste = None

        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Paste from Sopdrop")
        self.setFixedWidth(400)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['bg_dark']};
                color: {COLORS['text']};
                font-family: "Segoe UI", "SF Pro Display", sans-serif;
            }}
            QLineEdit {{
                background-color: {COLORS['bg_medium']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding: 10px 14px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border-color: {COLORS['accent']};
            }}
        """)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        title = QtWidgets.QLabel("Paste from Sopdrop")
        title.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {COLORS['text_bright']};")
        layout.addWidget(title)

        subtitle = QtWidgets.QLabel("Enter an asset slug or browse the registry")
        subtitle.setStyleSheet(f"font-size: 12px; color: {COLORS['text_dim']};")
        layout.addWidget(subtitle)

        # Input
        self.slug_input = QtWidgets.QLineEdit()
        self.slug_input.setPlaceholderText("username/asset-name")
        self.slug_input.returnPressed.connect(self._on_paste)
        layout.addWidget(self.slug_input)

        # Example
        example = QtWidgets.QLabel("Example: sidefx/scatter-points")
        example.setStyleSheet(f"font-size: 11px; color: {COLORS['text_dim']};")
        layout.addWidget(example)

        layout.addStretch()

        # Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(12)

        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.setStyleSheet(self._button_style())
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        browse_btn = QtWidgets.QPushButton("Browse Web")
        browse_btn.setStyleSheet(self._button_style())
        browse_btn.clicked.connect(self._on_browse)
        btn_layout.addWidget(browse_btn)

        btn_layout.addStretch()

        paste_btn = QtWidgets.QPushButton("Paste")
        paste_btn.setStyleSheet(self._button_style(primary=True))
        paste_btn.clicked.connect(self._on_paste)
        paste_btn.setDefault(True)
        btn_layout.addWidget(paste_btn)

        layout.addLayout(btn_layout)

    def _button_style(self, primary=False):
        if primary:
            return f"""
                QPushButton {{
                    background-color: {COLORS['accent']};
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 10px 20px;
                    font-size: 13px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {COLORS['accent_hover']};
                }}
            """
        return f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_hover']};
            }}
        """

    def _on_browse(self):
        import webbrowser
        from sopdrop.config import get_config
        config = get_config()
        webbrowser.open(f"{config['server_url']}/browse")
        self.reject()

    def _on_paste(self):
        slug = self.slug_input.text().strip()
        if slug:
            self.slug_to_paste = slug
            self.accept()


# ============================================================
# Dialog Launchers
# ============================================================

def _show_paste_dialog(slug, pane):
    """Show modern paste confirmation dialog."""
    import sopdrop

    # Try to get asset info
    asset_info = {}
    try:
        if slug.startswith("lib/"):
            # Library asset — look up by slug
            lib_info = _get_library_asset_info(slug[4:])
            if lib_info:
                asset_info = {
                    "name": lib_info.get("name") or slug[4:],
                    "context": lib_info.get("context", "?"),
                    "nodeCount": lib_info.get("node_count", "?"),
                    "owner": {"username": "library"},
                }
        elif slug.startswith("t/"):
            # Team (local) share — read manifest from team shares dir
            share_info = _get_team_share_info(slug[2:])
            if share_info:
                asset_info = {
                    "name": share_info.get("name") or f"Team Share {slug[2:]}",
                    "context": share_info.get("context", "?"),
                    "nodeCount": share_info.get("nodeCount", "?"),
                    "owner": {"username": share_info.get("createdBy", "someone")},
                }
        elif slug.startswith("s/"):
            # Cloud share — get info from share endpoint
            from sopdrop.api import SopdropClient
            client = SopdropClient()
            share_info = client.share_info(slug[2:])
            asset_info = {
                "name": share_info.get("name") or f"Share {slug[2:]}",
                "context": share_info.get("context", "?"),
                "nodeCount": share_info.get("nodeCount", "?"),
                "owner": {"username": share_info.get("createdBy", "someone")},
            }
        else:
            asset_info = sopdrop.info(slug)
    except Exception as e:
        print(f"Could not fetch asset info: {e}")

    # Check if this asset exists in the local library
    local_asset = None
    try:
        if slug.startswith("lib/"):
            from sopdrop.library import get_asset_by_slug
            local_asset = get_asset_by_slug(slug[4:])
        elif not slug.startswith("t/"):
            from sopdrop.library import get_asset_by_remote_slug
            check_slug = slug[2:] if slug.startswith("s/") else slug
            local_asset = get_asset_by_remote_slug(check_slug)
    except Exception:
        pass

    # Skip the confirmation dialog for local-library pastes when the target
    # context matches the package — the dialog is just friction at that point.
    # We still show it for web/share sources (safety check) and for context
    # mismatches (warning).
    if local_asset:
        try:
            target_context = _get_context(pane.pwd())
            pkg_context = str(asset_info.get("context", "?")).lower()
            context_ok = (
                target_context == "unknown"
                or pkg_context in ("?", "", "unknown")
                or target_context == pkg_context
            )
        except Exception:
            context_ok = False
        if context_ok:
            _paste_by_slug(slug, pane)
            return

    dialog = PasteConfirmDialog(slug, pane, asset_info, local_asset=local_asset)
    result = dialog.exec_()

    if result != QtWidgets.QDialog.Accepted:
        return

    if dialog.result_view_library and local_asset:
        try:
            from sopdrop_library_panel import reveal_asset_in_panels
            reveal_asset_in_panels(local_asset['id'])
        except Exception as e:
            print(f"Sopdrop: Could not reveal in library: {e}")
    elif dialog.result_save_library:
        _save_share_to_library(slug, asset_info)
    elif dialog.result_success:
        _paste_by_slug(slug, pane)


def _show_browse_dialog_modern(pane):
    """Show modern browse dialog."""
    dialog = BrowseDialog(pane)
    result = dialog.exec_()

    if result == QtWidgets.QDialog.Accepted and dialog.slug_to_paste:
        _paste_by_slug(dialog.slug_to_paste, pane)


def _save_share_to_library(slug, asset_info):
    """Download a cloud share/asset and save it to the local library."""
    try:
        if slug.startswith("s/"):
            from sopdrop.api import SopdropClient
            client = SopdropClient()
            package = client.fetch_share(slug[2:])
        else:
            import sopdrop
            package = sopdrop.fetch(slug)

        if not package:
            print("Sopdrop: No package data to save.")
            return

        from sopdrop import library
        name = asset_info.get("name", slug.split("/")[-1])
        context = package.get("context") or asset_info.get("context", "sop")
        saved = library.save_asset(
            name=name,
            context=context,
            package_data=package,
        )
        if saved:
            # Mark with remote_slug so we can find it next time
            remote_slug = slug[2:] if slug.startswith("s/") else slug
            library.mark_asset_synced(saved['id'], remote_slug, "1.0.0")
            try:
                from sopdrop_library_panel import reveal_asset_in_panels
                reveal_asset_in_panels(saved['id'])
            except Exception:
                pass
            print(f"Sopdrop: Saved '{name}' to library.")
    except Exception as e:
        print(f"Sopdrop: Could not save to library: {e}")


# ============================================================
# Fallback Dialogs (basic Houdini UI)
# ============================================================

def _confirm_and_paste_fallback(slug, pane):
    """Fallback confirmation using basic Houdini dialog."""
    import sopdrop

    try:
        if slug.startswith("lib/"):
            # Library asset
            info = _get_library_asset_info(slug[4:])
            if info:
                name = info.get("name") or slug[4:]
                context = info.get("context", "?").upper()
                node_count = info.get("node_count", "?")
                owner_name = "library"
            else:
                name = slug[4:]
                context = "?"
                node_count = "?"
                owner_name = "library"
        elif slug.startswith("t/"):
            # Team (local) share
            info = _get_team_share_info(slug[2:])
            if info:
                name = info.get("name") or f"Team Share {slug[2:]}"
                context = info.get("context", "?").upper()
                node_count = info.get("nodeCount", "?")
                owner_name = info.get("createdBy", "someone")
            else:
                name = f"Team Share {slug[2:]}"
                context = "?"
                node_count = "?"
                owner_name = "unknown"
        elif slug.startswith("s/"):
            # Cloud share
            from sopdrop.api import SopdropClient
            client = SopdropClient()
            info = client.share_info(slug[2:])
            name = info.get("name") or f"Share {slug[2:]}"
            context = info.get("context", "?").upper()
            node_count = info.get("nodeCount", "?")
            owner_name = info.get("createdBy", "someone")
        else:
            info = sopdrop.info(slug)
            name = info.get("name", slug)
            context = info.get("context", "?").upper()
            node_count = info.get("nodeCount", "?")
            owner = info.get("owner", {})
            owner_name = owner.get("username", "unknown") if isinstance(owner, dict) else str(owner)

        result = hou.ui.displayMessage(
            f"Paste from Sopdrop?\n\n"
            f"Asset: {name}\n"
            f"By: {owner_name}\n"
            f"Context: {context}\n"
            f"Nodes: {node_count}",
            buttons=("Paste", "Cancel"),
            default_choice=0,
            close_choice=1,
            title="Sopdrop - Paste",
        )

        if result != 0:
            return

    except Exception:
        result = hou.ui.displayMessage(
            f"Paste '{slug}' from Sopdrop?",
            buttons=("Paste", "Cancel"),
            default_choice=0,
            close_choice=1,
            title="Sopdrop - Paste",
        )

        if result != 0:
            return

    _paste_by_slug(slug, pane)


def _show_browse_dialog_fallback():
    """Fallback browse dialog using basic Houdini UI."""
    result = hou.ui.readInput(
        "No Sopdrop clipboard.\n\n"
        "Enter asset slug to paste (e.g., username/asset-name):",
        buttons=("Paste", "Browse Web", "Cancel"),
        default_choice=0,
        close_choice=2,
        title="Sopdrop - Paste Asset",
    )

    if result[0] == 2:  # Cancel
        return

    if result[0] == 1:  # Browse Web
        import webbrowser
        from sopdrop.config import get_config
        config = get_config()
        webbrowser.open(f"{config['server_url']}/browse")
        return

    slug = result[1].strip()
    if slug:
        pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        _paste_by_slug(slug, pane)


# ============================================================
# Core Paste Functions
# ============================================================

def _offer_placeholders_and_paste(package, target_node, position):
    """Try to paste; on missing HDA deps, offer placeholder mode for V1 packages.

    Returns True on success, False if cancelled/error.
    """
    from sopdrop.importer import import_items, MissingDependencyError, _check_missing_hdas

    try:
        import_items(package, target_node, position)
        return True
    except MissingDependencyError:
        pass

    # Missing deps — check if V1 and offer placeholders
    fmt = package.get("format", "")
    is_v1 = fmt in ("sopdrop-v1", "chopsop-v1")

    deps = package.get("dependencies", [])
    missing = _check_missing_hdas(deps) if deps else []
    lines = []
    if missing:
        lines.append(f"Missing {len(missing)} HDA dependency(ies):\n")
        for dep in missing:
            label = dep.get("label") or dep.get("name", "unknown")
            cat = dep.get("category", "")
            lines.append(f"  - {label} ({cat})")
    else:
        lines.append("Missing HDA dependencies (types not installed).")

    if is_v1:
        lines.append("\nPaste with red placeholder subnets for the missing nodes?")
        reply = hou.ui.displayMessage(
            "\n".join(lines),
            buttons=("Paste with Placeholders", "Cancel"),
            title="Sopdrop - Missing Dependencies",
            severity=hou.severityType.Warning,
            default_choice=1,
        )
        if reply != 0:
            return False
        import_items(package, target_node, position, allow_placeholders=True)
        return True
    else:
        lines.append("\nInstall the missing HDAs and try again.")
        lines.append("(Placeholder mode is only available for code-based packages.)")
        hou.ui.displayMessage(
            "\n".join(lines),
            title="Sopdrop - Missing Dependencies",
            severity=hou.severityType.Warning,
        )
        return False


def _quick_paste(clipboard, pane):
    """Quickly paste from local clipboard at cursor/view position."""
    from sopdrop.importer import import_items, ContextMismatchError, MissingDependencyError

    package = clipboard.get("package")
    slug = clipboard.get("slug", "clipboard")

    if not package:
        hou.ui.displayMessage(
            "Clipboard is empty or invalid.",
            title="Sopdrop",
            severity=hou.severityType.Warning,
        )
        return

    # Check context
    target_node = pane.pwd()
    target_context = _get_context(target_node)
    package_context = package.get("context", "unknown")

    if target_context != "unknown" and package_context != "unknown":
        if target_context != package_context:
            result = hou.ui.displayMessage(
                f"Context mismatch!\n\n"
                f"Package: {package_context.upper()}\n"
                f"Current: {target_context.upper()}\n\n"
                f"Paste anyway?",
                buttons=("Paste", "Cancel"),
                default_choice=1,
                close_choice=1,
                title="Sopdrop - Context Mismatch",
                severity=hou.severityType.Warning,
            )
            if result != 0:
                return

    # Get paste position
    position = _get_paste_position(pane)

    # Do the paste (with placeholder offer on missing deps)
    try:
        if _offer_placeholders_and_paste(package, target_node, position):
            meta = package.get("metadata", {})
            node_count = meta.get("node_count", "?")
            print(f"Sopdrop: Pasted {node_count} nodes from {slug}")

    except ContextMismatchError as e:
        hou.ui.displayMessage(str(e), title="Sopdrop - Context Error", severity=hou.severityType.Error)
    except Exception as e:
        hou.ui.displayMessage(f"Paste failed: {e}", title="Sopdrop - Error", severity=hou.severityType.Error)


def _paste_by_slug(slug, pane=None):
    """Fetch and paste an asset by its slug (or share code like 's/TC-XXXX')."""
    import sopdrop
    from sopdrop.api import SopdropClient

    if pane is None:
        pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)

    try:
        if slug.startswith("lib/"):
            # Library asset — load from local library by slug
            lib_slug = slug[4:]
            package = _fetch_library_asset(lib_slug)
            if package is None:
                hou.ui.displayMessage(
                    f"Library asset not found: {lib_slug}\n\n"
                    "The asset may have been deleted or renamed.",
                    title="Sopdrop - Not Found",
                    severity=hou.severityType.Error,
                )
                return

            position = _get_paste_position(pane) if pane else (0, 0)
            target_node = pane.pwd() if pane else None

            if _offer_placeholders_and_paste(package, target_node, position):
                meta = package.get("metadata", {})
                node_count = meta.get("node_count", "?")
                print(f"Sopdrop: Pasted {node_count} nodes from library/{lib_slug}")

                # Record usage
                try:
                    from sopdrop.library import get_asset_by_slug, record_asset_use
                    asset = get_asset_by_slug(lib_slug)
                    if asset:
                        record_asset_use(asset['id'])
                except Exception:
                    pass
            return

        if slug.startswith("t/"):
            # Team (local) share — read from team shares directory
            share_code = slug[2:]
            package = _fetch_team_share(share_code)
            if package is None:
                hou.ui.displayMessage(
                    f"Team share not found: {share_code}\n\n"
                    "The share file may have been deleted, or your\n"
                    "team library path may not be configured.",
                    title="Sopdrop - Not Found",
                    severity=hou.severityType.Error,
                )
                return

            position = _get_paste_position(pane) if pane else (0, 0)
            target_node = pane.pwd() if pane else None

            if _offer_placeholders_and_paste(package, target_node, position):
                meta = package.get("metadata", {})
                node_count = meta.get("node_count", "?")
                print(f"Sopdrop: Pasted {node_count} nodes from team share {share_code}")
            return

        if slug.startswith("s/"):
            # Cloud share — fetch from share endpoint
            share_code = slug[2:]
            with hou.InterruptableOperation(f"Fetching share {share_code}...", open_interrupt_dialog=True):
                client = SopdropClient()
                package = client.fetch_share(share_code)

            position = _get_paste_position(pane) if pane else (0, 0)
            target_node = pane.pwd() if pane else None

            if _offer_placeholders_and_paste(package, target_node, position):
                meta = package.get("metadata", {})
                node_count = meta.get("node_count", "?")
                print(f"Sopdrop: Pasted {node_count} nodes from share {share_code}")
            return

        # Regular asset slug
        with hou.InterruptableOperation(f"Fetching {slug}...", open_interrupt_dialog=True):
            client = SopdropClient()
            result = client.install(slug)

        if result["type"] == "hda":
            hou.hda.installFile(str(result["path"]))
            hou.ui.displayMessage(f"Installed HDA: {slug}", title="Sopdrop")
            return

        package = result["package"]

        # Save to clipboard for quick re-paste
        _save_to_clipboard(slug, package)

        position = _get_paste_position(pane) if pane else (0, 0)
        target_node = pane.pwd() if pane else None

        if _offer_placeholders_and_paste(package, target_node, position):
            meta = package.get("metadata", {})
            node_count = meta.get("node_count", "?")
            print(f"Sopdrop: Pasted {node_count} nodes from {slug}")

    except hou.OperationInterrupted:
        pass
    except Exception as e:
        hou.ui.displayMessage(f"Failed to paste: {e}", title="Sopdrop - Error", severity=hou.severityType.Error)


# ============================================================
# Team Share Helpers
# ============================================================

def _get_team_share_info(code):
    """Read team share manifest from local shares directory. Returns dict or None."""
    try:
        from sopdrop.config import get_team_library_path

        team_path = get_team_library_path()
        if not team_path:
            return None

        manifest_file = team_path / "shares" / f"{code}.json"
        if manifest_file.exists():
            return json.loads(manifest_file.read_text())
    except Exception as e:
        print(f"Could not read team share manifest: {e}")
    return None


def _fetch_team_share(code):
    """Read team share package from local shares directory. Returns package dict or None."""
    try:
        from sopdrop.config import get_team_library_path

        team_path = get_team_library_path()
        if not team_path:
            return None

        package_file = team_path / "shares" / f"{code}.sopdrop"
        if package_file.exists():
            return json.loads(package_file.read_text())
    except Exception as e:
        print(f"Could not read team share package: {e}")
    return None


# ============================================================
# Library Asset Helpers
# ============================================================

def _get_library_asset_info(slug):
    """Look up library asset info by slug. Returns dict or None."""
    try:
        from sopdrop.library import get_asset_by_slug
        asset = get_asset_by_slug(slug)
        if asset:
            return {
                "name": asset.get("name"),
                "context": asset.get("context", "?"),
                "node_count": asset.get("node_count", "?"),
                "asset_type": asset.get("asset_type", "node"),
            }
    except Exception as e:
        print(f"Could not look up library asset: {e}")
    return None


def _fetch_library_asset(slug):
    """Load library asset package by slug. Returns package dict or None."""
    try:
        from sopdrop.library import get_asset_by_slug, load_asset_package
        from sopdrop.config import get_active_library
        print(f"[Sopdrop] Looking up library asset: {slug} (library={get_active_library()})")
        asset = get_asset_by_slug(slug)
        if asset:
            return load_asset_package(asset['id'])
        print(f"[Sopdrop] Asset not found by slug: {slug}")

        # Try the other library if not found
        from sopdrop.library import close_db
        from sopdrop.config import set_active_library, get_team_library_path
        current = get_active_library()
        other = 'team' if current == 'personal' else 'personal'
        if other == 'team' and not get_team_library_path():
            return None
        try:
            print(f"[Sopdrop] Trying {other} library...")
            close_db()
            set_active_library(other)
            asset = get_asset_by_slug(slug)
            if asset:
                pkg = load_asset_package(asset['id'])
                if pkg:
                    print(f"[Sopdrop] Found in {other} library")
                    return pkg
        except Exception:
            pass
        finally:
            close_db()
            set_active_library(current)
    except Exception as e:
        print(f"[Sopdrop] Could not load library asset '{slug}': {e}")
        import traceback
        traceback.print_exc()
    return None


# Entry point
if __name__ == "__main__":
    main()
