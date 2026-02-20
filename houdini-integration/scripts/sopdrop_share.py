"""
Sopdrop Share Tool

One-click sharing: export selected nodes, upload as a temporary share,
copy the paste command to clipboard. Colleague pastes it in Houdini.

Shares expire after 24 hours.
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


def main():
    """Main entry point for the share tool."""
    # Check for sopdrop module
    try:
        import sopdrop
        from sopdrop.export import export_items
        from sopdrop.config import get_token, get_api_url
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

    # Check if logged in
    if not get_token():
        result = hou.ui.displayMessage(
            "You need to log in to share.\n\n"
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

    # Export and share
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
            result = _create_share(package)

        if result.get("error"):
            raise Exception(result["error"])

        share_code = result.get("shareCode", "")
        share_url = result.get("shareUrl", "")

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
            title="Sopdrop - Shared",
        )

    except hou.OperationInterrupted:
        hou.ui.displayMessage("Share cancelled.", title="Sopdrop")
    except Exception as e:
        hou.ui.displayMessage(
            f"Share failed:\n\n{e}",
            title="Sopdrop - Error",
            severity=hou.severityType.Error,
        )


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


def _create_share(package):
    """Upload package as a temporary share."""
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
