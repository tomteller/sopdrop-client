"""
Sopdrop Share Tool

One-click sharing: export selected nodes, upload as a temporary share,
copy the paste command to clipboard. Colleague pastes it in Houdini.

Cloud shares expire after 24 hours.
Team shares are saved locally on shared storage with no expiry.
"""

import hou
import json

# Try PySide6 first (Houdini 20+), fall back to PySide2
try:
    from PySide6 import QtCore, QtWidgets
    PYSIDE_VERSION = 6
except ImportError:
    try:
        from PySide2 import QtCore, QtWidgets
        PYSIDE_VERSION = 2
    except ImportError:
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
    'accent': '#22c55e',
    'accent_hover': '#34d66d',
    'accent_pressed': '#16a34a',
    'cloud': '#4a9eff',
    'cloud_hover': '#5eadff',
    'cloud_pressed': '#3a8ae0',
    'team': '#f97316',
    'team_hover': '#fb923c',
    'team_pressed': '#ea580c',
}


def main():
    """Main entry point for the share tool."""
    # Check for sopdrop module
    try:
        import sopdrop
        from sopdrop.export import export_items
        from sopdrop.config import get_token, get_api_url, get_team_library_path
    except ImportError:
        hou.ui.displayMessage(
            "Sopdrop client not installed.\n\n"
            "Install with:\n"
            "pip install sopdrop\n\n"
            "Or add sopdrop-client to your PYTHONPATH.",
            title="Sopdrop - Not Installed",
            severity=hou.severityType.Error,
        )
        return

    # Get selected items
    pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    if not pane:
        hou.ui.displayMessage(
            "No network editor found.",
            title="Sopdrop",
            severity=hou.severityType.Error,
        )
        return

    parent = pane.pwd()
    items = list(parent.selectedItems())

    if not items:
        hou.ui.displayMessage(
            "No items selected.\n\n"
            "Select nodes to share.",
            title="Sopdrop - Nothing Selected",
            severity=hou.severityType.Warning,
        )
        return

    nodes = [i for i in items if isinstance(i, hou.Node)]
    if not nodes:
        hou.ui.displayMessage(
            "No nodes selected.\n\n"
            "Select at least one node to share.",
            title="Sopdrop - No Nodes",
            severity=hou.severityType.Warning,
        )
        return

    # Check if team library is configured
    team_path = get_team_library_path()
    has_team = team_path is not None and team_path.exists()

    if has_team and PYSIDE_VERSION > 0:
        # Show choice dialog: Cloud vs Team
        try:
            choice = _show_share_choice_dialog(len(nodes))
            if choice == "cloud":
                _do_cloud_share(items, nodes)
            elif choice == "team":
                _do_team_share(items, nodes, team_path)
            # else: cancelled
            return
        except Exception as e:
            print(f"Share choice dialog failed: {e}")
            # Fall through to fallback
            _do_share_choice_fallback(items, nodes, team_path)
            return

    if has_team and PYSIDE_VERSION == 0:
        # Fallback choice for no-Qt environments
        _do_share_choice_fallback(items, nodes, team_path)
        return

    # No team configured — go straight to cloud share
    _do_cloud_share(items, nodes)


# ============================================================
# Share Choice Dialog
# ============================================================

class ShareChoiceDialog(QtWidgets.QDialog):
    """Dialog to choose between Cloud and Team (local) sharing."""

    def __init__(self, node_count, parent=None):
        if parent is None:
            parent = hou.qt.mainWindow()
        super().__init__(parent)

        self.node_count = node_count
        self.choice = None  # "cloud", "team", or None (cancelled)

        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Sopdrop Share")
        self.setFixedWidth(380)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['bg_dark']};
                color: {COLORS['text']};
                font-family: "Segoe UI", "SF Pro Display", sans-serif;
            }}
        """)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        title = QtWidgets.QLabel(f"Share {self.node_count} node{'s' if self.node_count != 1 else ''}")
        title.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {COLORS['text_bright']};")
        layout.addWidget(title)

        subtitle = QtWidgets.QLabel("Choose where to share")
        subtitle.setStyleSheet(f"font-size: 12px; color: {COLORS['text_dim']};")
        layout.addWidget(subtitle)

        # Cloud option
        cloud_btn = self._make_option_button(
            "Cloud Share",
            "Anyone with the code can paste. Expires in 24 hours.",
            COLORS['cloud'],
            COLORS['cloud_hover'],
            COLORS['cloud_pressed'],
        )
        cloud_btn.clicked.connect(self._on_cloud)
        layout.addWidget(cloud_btn)

        # Team option
        team_btn = self._make_option_button(
            "Team Share",
            "Saved locally on shared storage. No expiry, stays off the cloud.",
            COLORS['team'],
            COLORS['team_hover'],
            COLORS['team_pressed'],
        )
        team_btn.clicked.connect(self._on_team)
        layout.addWidget(team_btn)

        layout.addStretch()

        # Cancel
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_light']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding: 8px 20px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_hover']};
            }}
        """)
        cancel_btn.clicked.connect(self.reject)
        cancel_layout = QtWidgets.QHBoxLayout()
        cancel_layout.addStretch()
        cancel_layout.addWidget(cancel_btn)
        layout.addLayout(cancel_layout)

    def _make_option_button(self, label, description, color, hover_color, pressed_color):
        """Create a large option button with label and description."""
        btn = QtWidgets.QPushButton()
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setFixedHeight(72)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_medium']};
                border: 1px solid {COLORS['border']};
                border-radius: 8px;
                text-align: left;
                padding: 16px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_hover']};
                border-color: {color};
            }}
            QPushButton:pressed {{
                background-color: {pressed_color}22;
                border-color: {pressed_color};
            }}
        """)

        # Use a layout inside the button via a container
        inner = QtWidgets.QWidget(btn)
        inner.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        inner_layout = QtWidgets.QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(4)

        label_widget = QtWidgets.QLabel(label)
        label_widget.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        label_widget.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {color}; background: transparent; border: none;")
        inner_layout.addWidget(label_widget)

        desc_widget = QtWidgets.QLabel(description)
        desc_widget.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        desc_widget.setStyleSheet(f"font-size: 11px; color: {COLORS['text_dim']}; background: transparent; border: none;")
        desc_widget.setWordWrap(True)
        inner_layout.addWidget(desc_widget)

        # Resize inner widget to fill the button
        inner.setGeometry(16, 8, 340, 56)

        return btn

    def _on_cloud(self):
        self.choice = "cloud"
        self.accept()

    def _on_team(self):
        self.choice = "team"
        self.accept()


def _show_share_choice_dialog(node_count):
    """Show the share choice dialog. Returns 'cloud', 'team', or None."""
    dialog = ShareChoiceDialog(node_count)
    dialog.exec_()
    return dialog.choice


def _do_share_choice_fallback(items, nodes, team_path):
    """Fallback share choice using basic Houdini dialog."""
    result = hou.ui.displayMessage(
        f"Share {len(nodes)} node{'s' if len(nodes) != 1 else ''}?\n\n"
        f"Cloud: anyone with code, 24hr expiry\n"
        f"Team: local shared storage, no expiry",
        buttons=("Cloud Share", "Team Share", "Cancel"),
        default_choice=0,
        close_choice=2,
        title="Sopdrop - Share",
    )

    if result == 0:
        _do_cloud_share(items, nodes)
    elif result == 1:
        _do_team_share(items, nodes, team_path)


# ============================================================
# Cloud Share (existing behavior)
# ============================================================

def _do_cloud_share(items, nodes):
    """Upload to Sopdrop cloud as a temporary share."""
    from sopdrop.export import export_items
    from sopdrop.config import get_token

    # Check if logged in
    if not get_token():
        result = hou.ui.displayMessage(
            "You need to log in to cloud share.\n\n"
            "Would you like to log in now?",
            buttons=("Login", "Cancel"),
            default_choice=0,
            close_choice=1,
            title="Sopdrop - Login Required",
        )
        if result == 0:
            _login()
            if not get_token():
                return
        else:
            return

    try:
        with hou.InterruptableOperation(
            "Exporting nodes...",
            open_interrupt_dialog=True,
        ):
            package = export_items(items)

        with hou.InterruptableOperation(
            "Sharing...",
            open_interrupt_dialog=True,
        ):
            result = _create_cloud_share(package)

        if result.get("error"):
            raise Exception(result["error"])

        share_code = result.get("shareCode", "")

        # Copy paste command to clipboard
        paste_cmd = f'sopdrop.paste("s/{share_code}")'
        _copy_to_clipboard(paste_cmd)

        # Show success
        node_count = len(nodes)
        msg = (
            f"Link copied to clipboard!\n\n"
            f"Code: {share_code}\n"
            f"Nodes: {node_count}\n\n"
            f"Your colleague can paste with:\n"
            f"  {paste_cmd}\n\n"
            f"Or click Paste with this in their clipboard.\n\n"
            f"Expires in 24 hours."
        )

        hou.ui.displayMessage(
            msg,
            title="Sopdrop - Shared (Cloud)",
        )

    except hou.OperationInterrupted:
        hou.ui.displayMessage("Share cancelled.", title="Sopdrop")
    except Exception as e:
        hou.ui.displayMessage(
            f"Cloud share failed:\n\n{e}",
            title="Sopdrop - Error",
            severity=hou.severityType.Error,
        )


# ============================================================
# Team Share (local)
# ============================================================

def _do_team_share(items, nodes, team_path):
    """Save to team shared storage as a local share."""
    from sopdrop.export import export_items

    try:
        with hou.InterruptableOperation(
            "Exporting nodes...",
            open_interrupt_dialog=True,
        ):
            package = export_items(items)

        share_code = _create_team_share(package, team_path)

        # Copy paste command to clipboard
        paste_cmd = f'sopdrop.paste("t/{share_code}")'
        _copy_to_clipboard(paste_cmd)

        # Show success
        node_count = len(nodes)
        msg = (
            f"Link copied to clipboard!\n\n"
            f"Code: {share_code}\n"
            f"Nodes: {node_count}\n\n"
            f"Your colleague can paste with:\n"
            f"  {paste_cmd}\n\n"
            f"Or click Paste with this in their clipboard.\n\n"
            f"Saved locally (no expiry)."
        )

        hou.ui.displayMessage(
            msg,
            title="Sopdrop - Shared (Team)",
        )

    except hou.OperationInterrupted:
        hou.ui.displayMessage("Share cancelled.", title="Sopdrop")
    except Exception as e:
        hou.ui.displayMessage(
            f"Team share failed:\n\n{e}",
            title="Sopdrop - Error",
            severity=hou.severityType.Error,
        )


def _create_team_share(package, team_path):
    """Write package to team shares directory and return share code."""
    import secrets
    from datetime import datetime, timezone
    from pathlib import Path

    shares_dir = team_path / "shares"
    shares_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique share code (same format as cloud: XX-XXXX)
    code = _generate_share_code(shares_dir)

    # Write the package file
    package_file = shares_dir / f"{code}.sopdrop"
    package_file.write_text(json.dumps(package, separators=(',', ':')))

    # Write manifest for quick metadata lookup
    metadata = package.get("metadata", {})
    manifest = {
        "shareCode": code,
        "name": None,
        "context": package.get("context", "unknown"),
        "nodeCount": metadata.get("node_count", 0),
        "nodeNames": metadata.get("node_names", []),
        "createdBy": _get_current_username(),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "local": True,
    }

    manifest_file = shares_dir / f"{code}.json"
    manifest_file.write_text(json.dumps(manifest, indent=2))

    return code


def _generate_share_code(shares_dir):
    """Generate a unique share code (XX-XXXX format)."""
    import secrets

    # Same character set as server (no I/O/0/1 to avoid confusion)
    letters = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
    alphanum = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

    for _ in range(10):  # retry on collision
        prefix = secrets.choice(letters) + secrets.choice(letters)
        suffix = ''.join(secrets.choice(alphanum) for _ in range(4))
        code = f"{prefix}-{suffix}"

        # Check for collision
        if not (shares_dir / f"{code}.sopdrop").exists():
            return code

    raise Exception("Could not generate unique share code after 10 attempts")


def _get_current_username():
    """Get the current user's display name for the manifest."""
    import getpass
    try:
        from sopdrop.api import SopdropClient
        from sopdrop.config import get_token
        if get_token():
            client = SopdropClient()
            user = client._get("auth/me")
            return user.get("username", user.get("email", getpass.getuser()))
    except Exception:
        pass
    return getpass.getuser()


# ============================================================
# Shared Utilities
# ============================================================

def _login():
    """Show login dialog."""
    import webbrowser
    from sopdrop.config import get_config, save_token, clear_token

    config = get_config()
    auth_url = f"{config['server_url']}/auth/cli"

    webbrowser.open(auth_url)

    result = hou.ui.readInput(
        "After logging in on the website, copy the token and paste it here:",
        buttons=("Save", "Cancel"),
        default_choice=0,
        close_choice=1,
        title="Sopdrop - Enter Token",
    )

    if result[0] == 0 and result[1].strip():
        token = result[1].strip()
        save_token(token)

        try:
            from sopdrop.api import SopdropClient
            client = SopdropClient()
            user = client._get("auth/me")
            hou.ui.displayMessage(
                f"Logged in as: {user.get('username', user.get('email'))}",
                title="Sopdrop - Login Success",
            )
        except Exception as e:
            clear_token()
            hou.ui.displayMessage(
                f"Login failed: {e}",
                title="Sopdrop - Login Failed",
                severity=hou.severityType.Error,
            )


def _create_cloud_share(package):
    """Upload package as a temporary share to the cloud."""
    from sopdrop.api import SopdropClient
    client = SopdropClient()
    return client.share(package)


def _copy_to_clipboard(text):
    """Copy text to system clipboard."""
    if QtWidgets:
        app = QtWidgets.QApplication.instance()
        if app:
            clipboard = app.clipboard()
            clipboard.setText(text)
            return

    # Fallback for non-Qt environments
    import subprocess
    import platform

    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
        elif system == "Linux":
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
        elif system == "Windows":
            subprocess.run(["clip"], input=text.encode(), check=True)
    except Exception:
        pass  # Clipboard copy is best-effort


if __name__ == "__main__":
    main()
