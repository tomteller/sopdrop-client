"""
Sopdrop TAB Menu Generator

Creates a Houdini shelf file with tools that appear in the TAB menu,
organized as:

  Sopdrop/Personal/[Collection]/[Asset Name]
  Sopdrop/Team/[Collection]/[Asset Name]

Assets appear in the appropriate network context (SOP assets in SOP networks, etc.)

Usage:
    from sopdrop.menu import regenerate_menu
    regenerate_menu()  # Creates/updates the shelf file
"""

import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
from xml.sax.saxutils import escape


# ==============================================================================
# Configuration
# ==============================================================================

# Map our context names to Houdini network type names
CONTEXT_TO_NETTYPE = {
    'sop': 'SOP',
    'vop': 'VOP',
    'dop': 'DOP',
    'cop': 'COP2',
    'cop2': 'COP2',
    'top': 'TOP',
    'lop': 'LOP',
    'chop': 'CHOP',
    'obj': 'OBJ',
    'rop': 'ROP',
    'out': 'ROP',
}

SHELF_FILE_NAME = "sopdrop_library.shelf"


# ==============================================================================
# Path Helpers
# ==============================================================================

def get_shelf_dir() -> Path:
    """Get the directory for the shelf file."""
    # Use the sopdrop-houdini toolbar directory (in HOUDINI_TOOLBAR_PATH)
    sopdrop_path = os.environ.get('SOPDROP_HOUDINI_PATH', '')
    if sopdrop_path:
        shelf_dir = Path(sopdrop_path) / "toolbar"
        if shelf_dir.exists():
            return shelf_dir

    # Fallback to user prefs
    try:
        import hou
        shelf_dir = Path(hou.homeHoudiniDirectory()) / "toolbar"
    except ImportError:
        shelf_dir = Path.home() / "Library" / "Preferences" / "houdini" / "20.5" / "toolbar"

    shelf_dir.mkdir(parents=True, exist_ok=True)
    return shelf_dir


def get_shelf_file() -> Path:
    """Get the path to the Sopdrop library shelf file."""
    return get_shelf_dir() / SHELF_FILE_NAME


# ==============================================================================
# XML Generation
# ==============================================================================

def generate_tool_xml(asset: Dict[str, Any], library_type: str = 'personal') -> str:
    """Generate XML for a single tool."""
    asset_id = asset.get('id', '')
    name = asset.get('name', 'Untitled')
    context = asset.get('context', 'sop').lower()
    asset_type = asset.get('asset_type', 'node')
    description = asset.get('description', '')

    # Skip VEX snippets - they don't belong in the TAB menu
    if asset_type == 'vex' or context == 'vex':
        return ''

    # Get Houdini network type
    net_type = CONTEXT_TO_NETTYPE.get(context, 'SOP')

    # Tool name (must be unique, valid identifier)
    safe_id = asset_id.replace('-', '_')
    tool_name = f"sopdrop_lib_{safe_id}"

    # Label without node count
    label = name

    # Submenu path: Sopdrop/[Personal|Team]/[Collection]
    collections = asset.get('collections', [])
    if collections:
        coll_name = collections[0]['name'] if isinstance(collections[0], dict) else 'Library'
    else:
        coll_name = 'Library'

    lib_prefix = 'Personal' if library_type == 'personal' else 'Team'
    submenu = f"Sopdrop/{lib_prefix}/{coll_name}"

    # Keywords for search
    keywords = f"{name.lower()},sopdrop,{context}"
    tags = asset.get('tags', [])
    if tags:
        keywords += "," + ",".join(t.lower() for t in tags[:3])

    # Script
    script = f'''import sopdrop.menu
sopdrop.menu.paste_asset("{asset_id}")'''

    # Help text
    help_text = description if description else f"Paste {name} from Sopdrop library"

    return f'''
  <tool name="{tool_name}" label="{escape(label)}" icon="$SOPDROP_HOUDINI_PATH/toolbar/icons/sopdrop_ramen.svg">
    <helpText><![CDATA[{escape(help_text)}]]></helpText>
    <toolSubmenu>{escape(submenu)}</toolSubmenu>
    <toolMenuContext name="network">
      <contextNetType>{net_type}</contextNetType>
    </toolMenuContext>
    <script scriptType="python"><![CDATA[
{script}
]]></script>
    <keywordList>
      <keyword>{escape(keywords)}</keyword>
    </keywordList>
  </tool>'''


def generate_browse_tool_xml(context: str) -> str:
    """Generate XML for a 'Browse Library...' tool."""
    net_type = CONTEXT_TO_NETTYPE.get(context, 'SOP')
    tool_name = f"sopdrop_browse_{context}"

    return f'''
  <tool name="{tool_name}" label="Browse Library..." icon="$SOPDROP_HOUDINI_PATH/toolbar/icons/sopdrop_ramen.svg">
    <helpText><![CDATA[Open the Sopdrop Library panel to browse all assets.]]></helpText>
    <toolSubmenu>Sopdrop</toolSubmenu>
    <toolMenuContext name="network">
      <contextNetType>{net_type}</contextNetType>
    </toolMenuContext>
    <script scriptType="python"><![CDATA[
import sopdrop.menu
sopdrop.menu.open_library_panel()
]]></script>
    <keywordList>
      <keyword>sopdrop,library,browse,assets</keyword>
    </keywordList>
  </tool>'''


def generate_shelf_xml(personal_assets: List[Dict[str, Any]], team_assets: List[Dict[str, Any]] = None) -> str:
    """Generate the complete shelf XML document."""
    # Generate tool XML for each asset
    tool_xmls = []
    for asset in personal_assets:
        xml = generate_tool_xml(asset, 'personal')
        if xml:
            tool_xmls.append(xml)

    if team_assets:
        for asset in team_assets:
            xml = generate_tool_xml(asset, 'team')
            if xml:
                tool_xmls.append(xml)

    # Add browse tools for each context
    for context in CONTEXT_TO_NETTYPE.keys():
        tool_xmls.append(generate_browse_tool_xml(context))

    tools_xml = '\n'.join(tool_xmls)

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!--
  Sopdrop Library TAB Menu Tools
  Auto-generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

  This file is regenerated when your library changes.
  Do not edit manually.
-->
<shelfDocument>
{tools_xml}
</shelfDocument>
'''


# ==============================================================================
# Main Functions
# ==============================================================================

def regenerate_menu(quiet: bool = False) -> bool:
    """
    Regenerate the TAB menu shelf file from the library.

    This creates a shelf file with tools for each library asset.
    The tools appear in the TAB menu under Sopdrop/[Personal|Team]/[Collection]/[Name].

    Args:
        quiet: If True, suppress print output

    Returns:
        True if successful
    """
    try:
        from .library import search_assets, get_asset_collections
        from .config import get_team_library_path

        # Get personal library assets with collection info
        personal_assets = search_assets(limit=500)
        _enrich_with_collections(personal_assets)

        # Get team library assets if available
        team_assets = []
        team_path = get_team_library_path()
        if team_path:
            try:
                from .config import set_active_library, get_active_library
                prev = get_active_library()
                set_active_library('team')
                team_assets = search_assets(limit=500)
                _enrich_with_collections(team_assets)
                set_active_library(prev)
            except Exception:
                pass

        total = len(personal_assets) + len(team_assets)
        if not quiet:
            print(f"[Sopdrop] Regenerating TAB menu with {total} assets...")

        # Generate XML
        xml_content = generate_shelf_xml(personal_assets, team_assets if team_assets else None)

        # Write shelf file
        shelf_file = get_shelf_file()
        shelf_file.write_text(xml_content)

        if not quiet:
            print(f"[Sopdrop] Created {total} tools in TAB menu")
            print(f"[Sopdrop] Shelf file: {shelf_file}")
            print("[Sopdrop] Press TAB and type 'sopdrop' to find your assets")

        # Try to reload shelves in Houdini
        try:
            import hou
            hou.shelves.loadFile(str(shelf_file))
            if not quiet:
                print("[Sopdrop] Shelf reloaded - tools should appear in TAB menu")
        except ImportError:
            pass
        except Exception as e:
            if not quiet:
                print(f"[Sopdrop] Note: Restart Houdini to see changes ({e})")

        return True

    except Exception as e:
        if not quiet:
            print(f"[Sopdrop] Failed to regenerate menu: {e}")
        import traceback
        traceback.print_exc()
        return False


def _enrich_with_collections(assets):
    """Add collection info to assets for TAB menu categorization."""
    try:
        from .library import get_asset_collections
        for asset in assets:
            if 'collections' not in asset or not asset['collections']:
                colls = get_asset_collections(asset.get('id', ''))
                asset['collections'] = colls
    except Exception:
        pass


def remove_menu() -> bool:
    """Remove the Sopdrop TAB menu shelf file."""
    try:
        shelf_file = get_shelf_file()
        if shelf_file.exists():
            shelf_file.unlink()
            print(f"[Sopdrop] Removed shelf file: {shelf_file}")
        return True
    except Exception as e:
        print(f"[Sopdrop] Failed to remove menu: {e}")
        return False


def cleanup_menu() -> bool:
    """Remove stale tools from TAB menu and regenerate from current library."""
    try:
        # 1. Delete the shelf file
        remove_menu()

        # 2. In Houdini, unload stale shelf entries
        try:
            import hou
            for tool_name in list(hou.shelves.tools().keys()):
                if tool_name.startswith('sopdrop_lib_') or tool_name.startswith('sopdrop_browse_'):
                    try:
                        hou.shelves.tools()[tool_name].destroy()
                    except Exception:
                        pass
        except ImportError:
            pass
        except Exception:
            pass

        # 3. Regenerate from current library
        return regenerate_menu()

    except Exception as e:
        print(f"[Sopdrop] Failed to cleanup menu: {e}")
        return False


# ==============================================================================
# Menu Actions (called from tool scripts)
# ==============================================================================

def paste_asset(asset_id: str):
    """
    Paste an asset from the library into the current network.
    Called from TAB menu tools.
    """
    try:
        import hou
        from .library import load_asset_package, record_asset_use, get_asset
        from .importer import import_items

        # Get current network editor
        pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if not pane:
            hou.ui.displayMessage("No network editor found")
            return

        target = pane.pwd()

        # Load the asset info
        asset = get_asset(asset_id)

        # Handle HDA assets differently to avoid UTF-8 issues
        if asset and asset.get('asset_type') == 'hda':
            _paste_hda(asset, target, pane)
            record_asset_use(asset_id)
            return

        # Load the package for node assets
        package = load_asset_package(asset_id)
        if not package:
            hou.ui.displayMessage("Failed to load asset from library")
            return

        # Check context compatibility (case-insensitive)
        target_ctx = target.childTypeCategory().name().upper()
        pkg_ctx = package.get('context', '').lower()
        expected_ctx = CONTEXT_TO_NETTYPE.get(pkg_ctx, '').upper()

        if expected_ctx and target_ctx != expected_ctx:
            name = asset.get('name', 'Asset') if asset else 'Asset'
            result = hou.ui.displayMessage(
                f"'{name}' is a {pkg_ctx.upper()} asset.\n"
                f"You're in a {target_ctx} network.\n\n"
                "Paste anyway?",
                buttons=("Paste", "Cancel"),
                default_choice=1
            )
            if result == 1:
                return

        # Get cursor position for placement
        cursor_pos = pane.cursorPosition()

        # Import the nodes
        import_items(package, target, position=cursor_pos)

        # Record usage
        record_asset_use(asset_id)

    except Exception as e:
        import hou
        hou.ui.displayMessage(f"Failed to paste asset: {e}")
        import traceback
        traceback.print_exc()


def _paste_hda(asset, target, pane):
    """Paste an HDA asset, handling binary file correctly to avoid UTF-8 errors."""
    import hou

    file_path = asset.get('file_path', '')
    if not file_path or not os.path.exists(file_path):
        hou.ui.displayMessage("HDA file not found")
        return

    try:
        # Install the HDA definition
        hou.hda.installFile(file_path)

        # Get the type name from asset metadata
        hda_type_name = asset.get('hda_type_name', '')
        hda_category = asset.get('hda_category', asset.get('context', 'Sop'))

        if hda_type_name:
            # Map category to Houdini type category
            category_map = {
                'sop': hou.sopNodeTypeCategory,
                'obj': hou.objNodeTypeCategory,
                'object': hou.objNodeTypeCategory,
                'dop': hou.dopNodeTypeCategory,
                'cop': hou.cop2NodeTypeCategory,
                'cop2': hou.cop2NodeTypeCategory,
                'vop': hou.vopNodeTypeCategory,
                'top': hou.topNodeTypeCategory,
                'lop': hou.lopNodeTypeCategory,
                'chop': hou.chopNodeTypeCategory,
                'rop': hou.ropNodeTypeCategory,
                'out': hou.ropNodeTypeCategory,
            }
            cat_func = category_map.get(hda_category.lower(), hou.sopNodeTypeCategory)

            # Try to create a node of this type
            try:
                # Extract base type name (strip version namespace)
                base_type = hda_type_name.split('::')[0] if '::' in hda_type_name else hda_type_name
                cursor_pos = pane.cursorPosition()
                node = target.createNode(hda_type_name)
                if node:
                    node.setPosition(cursor_pos)
                    node.setSelected(True, clear_all_selected=True)
            except Exception:
                # Try with full type name
                try:
                    node = target.createNode(base_type)
                    if node:
                        node.setPosition(pane.cursorPosition())
                except Exception as e2:
                    hou.ui.displayMessage(
                        f"HDA installed but could not create node.\n"
                        f"Type: {hda_type_name}\n"
                        f"Look for it in the TAB menu.\n\nError: {e2}"
                    )
        else:
            hou.ui.displayMessage(
                f"HDA installed: {os.path.basename(file_path)}\n"
                "Look for the new node type in the TAB menu."
            )
    except Exception as e:
        hou.ui.displayMessage(f"Failed to install HDA: {e}")


def open_library_panel():
    """Open the Sopdrop Library panel."""
    try:
        import hou

        # Try to find existing panel
        for pane_tab in hou.ui.paneTabs():
            if pane_tab.type() == hou.paneTabType.PythonPanel:
                try:
                    if 'sopdrop' in pane_tab.name().lower() or 'library' in pane_tab.name().lower():
                        pane_tab.setIsCurrentTab()
                        return
                except:
                    pass

        # Create floating panel
        try:
            desktop = hou.ui.curDesktop()
            panel = desktop.createFloatingPaneTab(hou.paneTabType.PythonPanel)
            if panel:
                try:
                    interface = hou.pypanel.interfaceByName('sopdrop_library')
                    if interface:
                        panel.setActiveInterface(interface)
                except:
                    pass
                return
        except:
            pass

        print("[Sopdrop] Open: Windows > Python Panel > Sopdrop Library")

    except Exception as e:
        print(f"[Sopdrop] Failed to open library panel: {e}")


# ==============================================================================
# Auto-regeneration
# ==============================================================================

_auto_regenerate = True


def set_auto_regenerate(enabled: bool):
    """Enable or disable automatic menu regeneration."""
    global _auto_regenerate
    _auto_regenerate = enabled


def should_regenerate() -> bool:
    """Check if menu should be regenerated."""
    return _auto_regenerate


def trigger_regenerate():
    """Trigger menu regeneration if enabled."""
    if should_regenerate():
        try:
            regenerate_menu(quiet=True)
        except:
            pass
