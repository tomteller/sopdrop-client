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
    else:
        # No clipboard - show search/browse dialog
        if PYSIDE_VERSION > 0:
            try:
                _show_browse_dialog_modern(pane)
                return
            except Exception as e:
                print(f"Modern UI failed: {e}")
        _show_browse_dialog_fallback()


def _check_system_clipboard():
    """Check system clipboard for sopdrop.paste("slug") command."""
    import re
    try:
        if PYSIDE_VERSION == 0:
            return None

        clipboard = QtWidgets.QApplication.clipboard()
        text = clipboard.text().strip()

        if not text:
            return None

        # Look for sopdrop.paste("user/asset") pattern
        match = re.search(r'sopdrop\.paste\(["\']([^"\']+)["\']\)', text)
        if match:
            return match.group(1)

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
    """Get the best position to paste at (cursor or view center)."""
    position = None

    # Try cursor position first
    try:
        cursor_pos = pane.cursorPosition()
        bounds = pane.visibleBounds()
        if bounds.contains(cursor_pos):
            position = (cursor_pos[0], cursor_pos[1])
            print(f"[Sopdrop] Paste position: cursor ({position[0]:.1f}, {position[1]:.1f})")
            return position
    except Exception:
        pass

    # Fall back to view center
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

    def __init__(self, slug, pane, asset_info=None, parent=None):
        if parent is None:
            parent = hou.qt.mainWindow()
        super().__init__(parent)

        self.slug = slug
        self.pane = pane
        self.asset_info = asset_info or {}
        self.result_success = False

        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Paste from Sopdrop")
        self.setFixedWidth(380)
        self.setStyleSheet(self._get_stylesheet())
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header with icon
        header = QtWidgets.QHBoxLayout()

        icon_label = QtWidgets.QLabel("\u2193")  # Down arrow
        icon_label.setStyleSheet(
            f"font-size: 32px; color: {COLORS['accent']}; "
            f"background: {COLORS['accent']}22; border-radius: 8px; padding: 8px;"
        )
        icon_label.setFixedSize(56, 56)
        icon_label.setAlignment(QtCore.Qt.AlignCenter)
        header.addWidget(icon_label)

        title_layout = QtWidgets.QVBoxLayout()
        title_layout.setSpacing(2)

        title = QtWidgets.QLabel("Paste Asset")
        title.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {COLORS['text_bright']};")
        title_layout.addWidget(title)

        subtitle = QtWidgets.QLabel(self.slug)
        subtitle.setStyleSheet(f"font-size: 12px; color: {COLORS['text_dim']};")
        title_layout.addWidget(subtitle)

        header.addLayout(title_layout)
        header.addStretch()

        # Context badge
        context = self.asset_info.get("context", self.asset_info.get("houdini_context", "?"))
        context_color = COLORS.get(context.lower(), COLORS['text_dim'])
        context_badge = QtWidgets.QLabel(context.upper())
        context_badge.setStyleSheet(
            f"background: {context_color}; color: white; font-size: 10px; "
            f"font-weight: 600; padding: 4px 10px; border-radius: 4px;"
        )
        header.addWidget(context_badge)

        layout.addLayout(header)

        # Asset info card
        card = QtWidgets.QFrame()
        card.setStyleSheet(
            f"background: {COLORS['bg_medium']}; border: 1px solid {COLORS['border']}; border-radius: 8px;"
        )
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(8)

        # Asset details
        name = self.asset_info.get("name", self.slug.split("/")[-1])
        owner = self.asset_info.get("owner", {})
        owner_name = owner.get("username", self.slug.split("/")[0]) if isinstance(owner, dict) else str(owner)
        node_count = self.asset_info.get("nodeCount", self.asset_info.get("node_count", "?"))

        details = [
            ("Name", name),
            ("Author", owner_name),
            ("Nodes", str(node_count)),
        ]

        for label, value in details:
            row = QtWidgets.QHBoxLayout()
            label_widget = QtWidgets.QLabel(label)
            label_widget.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 12px;")
            label_widget.setFixedWidth(60)
            row.addWidget(label_widget)

            value_widget = QtWidgets.QLabel(value)
            value_widget.setStyleSheet(f"color: {COLORS['text_bright']}; font-size: 12px;")
            row.addWidget(value_widget)
            row.addStretch()

            card_layout.addLayout(row)

        layout.addWidget(card)

        # Context mismatch warning
        target_context = _get_context(self.pane.pwd())
        package_context = context.lower() if context != "?" else "unknown"

        if target_context != "unknown" and package_context != "unknown" and target_context != package_context:
            warning = QtWidgets.QFrame()
            warning.setStyleSheet(
                f"background: {COLORS['warning']}22; border: 1px solid {COLORS['warning']}44; border-radius: 6px;"
            )
            warning_layout = QtWidgets.QHBoxLayout(warning)
            warning_layout.setContentsMargins(12, 8, 12, 8)

            warning_icon = QtWidgets.QLabel("\u26A0")
            warning_icon.setStyleSheet(f"color: {COLORS['warning']}; font-size: 14px;")
            warning_layout.addWidget(warning_icon)

            warning_text = QtWidgets.QLabel(
                f"Context mismatch: Asset is {package_context.upper()}, current network is {target_context.upper()}"
            )
            warning_text.setStyleSheet(f"color: {COLORS['warning']}; font-size: 11px;")
            warning_text.setWordWrap(True)
            warning_layout.addWidget(warning_text)

            layout.addWidget(warning)

        layout.addStretch()

        # Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(12)

        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.setStyleSheet(self._button_style())
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        btn_layout.addStretch()

        paste_btn = QtWidgets.QPushButton("Paste")
        paste_btn.setStyleSheet(self._button_style(primary=True))
        paste_btn.setFixedWidth(100)
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
                    padding: 10px 20px;
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
        asset_info = sopdrop.info(slug)
    except Exception as e:
        print(f"Could not fetch asset info: {e}")

    dialog = PasteConfirmDialog(slug, pane, asset_info)
    result = dialog.exec_()

    if result == QtWidgets.QDialog.Accepted and dialog.result_success:
        _paste_by_slug(slug, pane)


def _show_browse_dialog_modern(pane):
    """Show modern browse dialog."""
    dialog = BrowseDialog(pane)
    result = dialog.exec_()

    if result == QtWidgets.QDialog.Accepted and dialog.slug_to_paste:
        _paste_by_slug(dialog.slug_to_paste, pane)


# ============================================================
# Fallback Dialogs (basic Houdini UI)
# ============================================================

def _confirm_and_paste_fallback(slug, pane):
    """Fallback confirmation using basic Houdini dialog."""
    import sopdrop

    try:
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

    # Do the paste
    try:
        items = import_items(package, target_node, position)

        meta = package.get("metadata", {})
        node_count = meta.get("node_count", len(items) if items else "?")
        print(f"Sopdrop: Pasted {node_count} nodes from {slug}")

    except ContextMismatchError as e:
        hou.ui.displayMessage(str(e), title="Sopdrop - Context Error", severity=hou.severityType.Error)
    except MissingDependencyError as e:
        hou.ui.displayMessage(str(e), title="Sopdrop - Missing HDAs", severity=hou.severityType.Error)
    except Exception as e:
        hou.ui.displayMessage(f"Paste failed: {e}", title="Sopdrop - Error", severity=hou.severityType.Error)


def _paste_by_slug(slug, pane=None):
    """Fetch and paste an asset by its slug."""
    import sopdrop
    from sopdrop.api import SopdropClient
    from sopdrop.importer import import_items

    if pane is None:
        pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)

    try:
        # Show progress
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

        # Get paste position
        position = _get_paste_position(pane) if pane else (0, 0)
        target_node = pane.pwd() if pane else None

        # Paste
        items = import_items(package, target_node, position)

        meta = package.get("metadata", {})
        node_count = meta.get("node_count", len(items) if items else "?")
        print(f"Sopdrop: Pasted {node_count} nodes from {slug}")

    except hou.OperationInterrupted:
        pass
    except Exception as e:
        hou.ui.displayMessage(f"Failed to paste: {e}", title="Sopdrop - Error", severity=hou.severityType.Error)


# Entry point
if __name__ == "__main__":
    main()
