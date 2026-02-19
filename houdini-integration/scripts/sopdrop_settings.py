"""
Sopdrop Settings Tool

Launches the unified SettingsDialog from the library panel.
Falls back to basic Houdini dialogs if PySide is not available.
"""

import hou
import webbrowser


def main():
    """Main entry point for the settings tool."""
    # Check for sopdrop module
    try:
        import sopdrop
        from sopdrop.config import get_token, get_config, get_cache_dir
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

    # Try to use the unified SettingsDialog from library panel
    try:
        import importlib
        import sopdrop_library_panel
        importlib.reload(sopdrop_library_panel)
        dialog = sopdrop_library_panel.SettingsDialog(hou.qt.mainWindow())
        dialog.exec_()
        return
    except Exception as e:
        print(f"[Sopdrop] Library panel settings unavailable, using fallback: {e}")

    # Fallback to basic dialog
    fallback_settings()


def fallback_settings():
    """Fallback settings using basic Houdini dialogs."""
    from sopdrop.config import get_token, get_config

    config = get_config()
    token = get_token()

    user_info = "Not logged in"
    if token:
        try:
            from sopdrop.api import SopdropClient
            client = SopdropClient()
            user = client._get("auth/me")
            username = user.get("username", user.get("email", "Unknown"))
            user_info = f"Logged in as: {username}"
        except Exception:
            user_info = "Logged in (session may be expired)"

    choices = [
        f"--- Account ---",
        user_info,
        "Login" if not token else "Logout",
        "",
        f"--- Settings ---",
        f"Server: {config.get('server_url', 'Not set')}",
        "Change Server URL",
        "",
        f"--- Cache ---",
        "Clear Cache",
        "",
        f"--- Help ---",
        "Open Documentation",
    ]

    result = hou.ui.selectFromList(
        choices,
        exclusive=True,
        title="Sopdrop Settings",
        message="Manage your Sopdrop configuration:",
        width=400,
        height=350,
    )

    if not result:
        return

    selected = choices[result[0]]

    if selected == "Login":
        _fallback_login()
    elif selected == "Logout":
        _fallback_logout()
    elif selected == "Change Server URL":
        _fallback_change_server()
    elif selected == "Clear Cache":
        _fallback_clear_cache()
    elif selected == "Open Documentation":
        from sopdrop.config import get_config
        config = get_config()
        webbrowser.open(f"{config['server_url']}/docs")


def _fallback_login():
    """Fallback login using basic dialogs."""
    from sopdrop.config import get_config, save_token, clear_token
    from sopdrop.api import SopdropClient

    config = get_config()
    auth_url = f"{config['server_url']}/auth/cli"

    webbrowser.open(auth_url)

    result = hou.ui.readInput(
        "Paste your API token here:",
        buttons=("Save", "Cancel"),
        title="Sopdrop - Enter Token",
    )

    if result[0] != 0 or not result[1].strip():
        return

    token = result[1].strip()
    save_token(token)

    try:
        client = SopdropClient()
        user = client._get("auth/me")
        username = user.get("username", user.get("email"))
        hou.ui.displayMessage(f"Welcome, {username}!", title="Sopdrop - Login Success")
    except Exception as e:
        clear_token()
        hou.ui.displayMessage(f"Login failed: {e}", title="Sopdrop", severity=hou.severityType.Error)


def _fallback_logout():
    """Fallback logout."""
    from sopdrop.config import clear_token

    result = hou.ui.displayMessage(
        "Are you sure you want to log out?",
        buttons=("Logout", "Cancel"),
        title="Sopdrop - Confirm Logout",
    )

    if result == 0:
        clear_token()
        hou.ui.displayMessage("Logged out.", title="Sopdrop")


def _fallback_change_server():
    """Fallback server URL change."""
    from sopdrop.config import get_config, save_config

    config = get_config()

    result = hou.ui.readInput(
        "Enter the Sopdrop server URL:",
        buttons=("Save", "Cancel"),
        initial_contents=config.get("server_url", "https://sopdrop.com"),
        title="Sopdrop - Server URL",
    )

    if result[0] != 0:
        return

    url = result[1].strip().rstrip("/")
    if url:
        if not url.startswith("http"):
            url = f"https://{url}"
        config["server_url"] = url
        save_config(config)
        hou.ui.displayMessage(f"Server URL updated to:\n{url}", title="Sopdrop")


def _fallback_clear_cache():
    """Fallback cache clear."""
    from sopdrop.config import get_cache_dir

    cache_dir = get_cache_dir()
    files = list(cache_dir.glob("*.sopdrop")) + list(cache_dir.glob("*.hda")) if cache_dir.exists() else []

    if not files:
        hou.ui.displayMessage("Cache is empty.", title="Sopdrop")
        return

    result = hou.ui.displayMessage(
        f"Clear {len(files)} cached files?",
        buttons=("Clear", "Cancel"),
        title="Sopdrop - Clear Cache",
    )

    if result == 0:
        for f in files:
            try:
                f.unlink()
            except Exception:
                pass
        hou.ui.displayMessage(f"Cleared {len(files)} files.", title="Sopdrop")


# Entry point
if __name__ == "__main__":
    main()
