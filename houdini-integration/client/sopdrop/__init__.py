"""
Sopdrop - Houdini Asset Registry Client

Usage in Houdini Python shell:
    import sopdrop

    # Auth (one-time setup)
    sopdrop.login()

    # Browse & Search
    sopdrop.search("scatter")
    sopdrop.info("username/scatter-points")

    # Copy to clipboard (for quick paste later)
    sopdrop.copy("username/scatter-points")

    # Paste - direct or from clipboard
    sopdrop.paste("username/scatter-points")  # Direct paste
    sopdrop.paste()  # Paste from clipboard (if copied)

    # Install HDAs
    sopdrop.install("username/my-hda")
    sopdrop.install("username/my-hda@1.2.0")

    # Review before pasting
    sopdrop.show_info("username/scatter-points")

    # Publish from Houdini
    # Select nodes, then use the Publish shelf tool
    # Or: sopdrop.preview_export() to see what would be exported
"""

__version__ = "0.1.2"

from .api import SopdropClient, SopdropError, AuthError, NotFoundError
from .config import get_config, set_server_url, get_clipboard, set_clipboard, clear_clipboard

# Global client instance
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = SopdropClient()
    return _client


# Public API
def login():
    """Authenticate with Sopdrop server. Opens browser for token."""
    return _get_client().login()


def logout():
    """Clear stored credentials."""
    return _get_client().logout()


def search(query, context=None, tags=None):
    """Search for assets in the registry."""
    return _get_client().search(query, context=context, tags=tags)


def info(asset_slug):
    """Get details about an asset. Format: 'username/asset-name'"""
    return _get_client().info(asset_slug)


def install(asset_ref, force=False):
    """
    Install an asset locally.

    Args:
        asset_ref: Asset reference, e.g., 'user/asset' or 'user/asset@1.0.0'
        force: Force reinstall if already cached

    Returns:
        Dict with 'type' ('node' or 'hda'), 'package' (for nodes), and 'path'
    """
    return _get_client().install(asset_ref, force=force)


def paste(asset_ref=None, force=False, trust=False):
    """
    Paste an asset into current Houdini network.

    Args:
        asset_ref: Asset to paste (e.g., 'user/scatter-points@1.0.0')
        force: Skip context mismatch check
        trust: Skip security warning for untrusted assets

    If asset_ref is None, pastes from Houdini clipboard.
    """
    return _get_client().paste(asset_ref, force=force, trust=trust)


def copy(asset_ref):
    """
    Copy an asset to local clipboard for quick paste.

    This fetches the package and stores it locally so the Paste
    shelf tool can paste it instantly without network delay.

    Args:
        asset_ref: Asset slug like "username/asset-name" or "username/asset@1.0.0"

    Example:
        sopdrop.copy("sidefx/scatter-points")  # Fetch and cache
        # Then in Houdini, click Paste shelf tool - instant paste!
    """
    client = _get_client()
    result = client.install(asset_ref)

    if result["type"] == "hda":
        raise SopdropError("HDAs don't need copying - just install them with sopdrop.install()")

    package = result["package"]
    set_clipboard(asset_ref, package)

    meta = package.get("metadata", {})
    node_count = meta.get("node_count", "?")
    context = package.get("context", "?")

    print(f"Copied to Sopdrop clipboard: {asset_ref}")
    print(f"  Context: {context.upper()}, Nodes: {node_count}")
    print(f"  Use Paste shelf tool or sopdrop.paste() to paste")

    return {"slug": asset_ref, "context": context, "node_count": node_count}


def paste_from_clipboard(trust=False):
    """
    Paste asset from system clipboard.

    Looks for sopdrop.paste("user/asset") in clipboard and pastes it.
    Copy an asset from sopdrop.com, then use this or the Paste shelf tool.

    This is called by the Sopdrop shelf tool.
    """
    import re

    # Get clipboard content
    try:
        import hou
        # Use Qt clipboard (available in Houdini)
        from PySide2.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        text = clipboard.text().strip()
    except ImportError:
        raise SopdropError("This function requires Houdini")

    if not text:
        raise SopdropError("Clipboard is empty. Copy an asset from sopdrop.com first.")

    # Look for sopdrop.paste("user/asset") pattern
    match = re.search(r'sopdrop\.paste\(["\']([^"\']+)["\']\)', text)
    if match:
        slug = match.group(1)
        print(f"Pasting: {slug}")
        return paste(slug, trust=trust)

    # Not a sopdrop command
    raise SopdropError(
        "No Sopdrop asset in clipboard.\n\n"
        "Copy an asset from sopdrop.com, then click Paste."
    )


def publish(nodes=None, name=None, description=None, license="mit", tags=None):
    """
    Publish selected nodes to Sopdrop.

    Args:
        nodes: Houdini items to publish (default: selected items)
        name: Asset name (prompted if not provided)
        description: Asset description
        license: License type (default: 'mit')
        tags: List of tags
    """
    return _get_client().publish(nodes, name=name, description=description, license=license, tags=tags)


def publish_hda(hda_info, name=None, description=None, license="mit", tags=None, is_public=True):
    """
    Publish an HDA to Sopdrop.

    Args:
        hda_info: Dict from export.detect_publishable_hda()
        name: Asset name (defaults to HDA type label)
        description: Asset description
        license: License type (default: 'mit')
        tags: List of tags
        is_public: Whether the asset is publicly visible

    Example:
        from sopdrop.export import detect_publishable_hda
        hda_info = detect_publishable_hda(hou.selectedNodes())
        if hda_info:
            sopdrop.publish_hda(hda_info, name="My Tool")
    """
    return _get_client().publish_hda(
        hda_info,
        name=name,
        description=description,
        license=license,
        tags=tags,
        is_public=is_public,
    )


def versions(asset_slug):
    """List all versions of an asset."""
    return _get_client().versions(asset_slug)


def cache_status():
    """Show local cache status."""
    return _get_client().cache_status()


def cache_clear():
    """Clear local cache."""
    return _get_client().cache_clear()


def show_code(asset_ref):
    """
    Show the Python code in an asset.

    Useful for reviewing assets before pasting.
    """
    return _get_client().show_code(asset_ref)


def show_info(asset_ref):
    """
    Show detailed information about an asset.
    """
    return _get_client().show_info(asset_ref)


def preview(asset_ref):
    """
    Preview an asset without executing it.

    Shows what nodes would be created, any risks, and metadata.
    Use this to inspect assets before pasting.

    Args:
        asset_ref: Asset reference like "user/asset" or "user/asset@1.0.0"

    Example:
        sopdrop.preview("artist/scatter-tool")  # See what's in it
        sopdrop.paste("artist/scatter-tool")    # Then paste if it looks safe
    """
    return _get_client().preview(asset_ref)


def preview_export(items=None):
    """
    Preview what would be exported without actually publishing.

    Args:
        items: Items to preview (default: selected items)
    """
    from .export import preview_export as _preview
    return _preview(items)


# ==============================================================================
# TAB Menu Functions
# ==============================================================================

def regenerate_menu(quiet=False):
    """
    Regenerate the Houdini TAB menu from your library.

    This creates an OPmenu XML file that adds your saved assets to
    Houdini's TAB menu, organized by context and category (first tag).

    The menu is automatically regenerated when you:
    - Save a new asset
    - Delete an asset
    - Sync from cloud
    - Change asset tags

    You can also call this manually after making changes.

    Args:
        quiet: If True, suppress output messages
    """
    from .menu import regenerate_menu as _regen
    return _regen(quiet=quiet)


def remove_menu():
    """
    Remove the Sopdrop TAB menu.

    This removes the OPmenu XML file, removing Sopdrop entries from
    the TAB menu. Useful if you want a clean TAB menu.
    """
    from .menu import remove_menu as _remove
    return _remove()


def enable_tab_menu(enabled=True):
    """
    Enable or disable automatic TAB menu updates.

    When enabled (default), the TAB menu is automatically regenerated
    when you save, delete, or sync assets.

    Args:
        enabled: True to enable, False to disable
    """
    from .menu import set_auto_regenerate
    set_auto_regenerate(enabled)
    if enabled:
        regenerate_menu(quiet=True)
