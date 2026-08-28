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
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
from xml.sax.saxutils import escape

from ._log import debug


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

# Network type -> the hou attribute that proves the running Houdini has it.
# Houdini 20.5 introduced Copernicus (COP) alongside the legacy compositing
# network (COP2), and COP2 is being retired — so which one exists depends on
# the version we happen to be running in.
NETTYPE_PROBES = {
    'SOP': 'sopNodeTypeCategory',
    'VOP': 'vopNodeTypeCategory',
    'DOP': 'dopNodeTypeCategory',
    'COP2': 'cop2NodeTypeCategory',
    'COP': 'copNodeTypeCategory',
    'TOP': 'topNodeTypeCategory',
    'LOP': 'lopNodeTypeCategory',
    'CHOP': 'chopNodeTypeCategory',
    'OBJ': 'objNodeTypeCategory',
    'ROP': 'ropNodeTypeCategory',
}

SHELF_FILE_NAME = "sopdrop_library.shelf"


# ==============================================================================
# Houdini Version Compatibility
# ==============================================================================

def _supported_net_types():
    """The set of contextNetType values the running Houdini understands.

    Returns None when we're not inside Houdini and have nothing to check
    against.
    """
    try:
        import hou
    except ImportError:
        return None

    supported = {net_type for net_type, probe in NETTYPE_PROBES.items()
                 if hasattr(hou, probe)}
    return supported or None


def get_context_net_types():
    """Map our contexts to contextNetType values valid in this Houdini.

    Houdini's shelf loader rejects a tool whose network type it doesn't
    recognise, and a single bad tool can take the whole shelf file down with
    it — and with it every Sopdrop entry in the TAB menu.  So contexts this
    Houdini can't host are dropped rather than emitted, and COP assets fall
    back to Copernicus on versions where the legacy COP2 network is gone.
    """
    supported = _supported_net_types()
    if supported is None:
        return dict(CONTEXT_TO_NETTYPE)

    resolved = {}
    for context, net_type in CONTEXT_TO_NETTYPE.items():
        if net_type == 'COP2' and 'COP2' not in supported:
            net_type = 'COP'
        if net_type in supported:
            resolved[context] = net_type
    return resolved


def node_type_category(context: str):
    """Return the hou node type category for one of our contexts, or None.

    Probes are ordered most- to least-preferred so that 'cop' resolves to the
    legacy COP2 category where it still exists and to Copernicus where it
    doesn't.  Never raises — a context this Houdini doesn't have is None.
    """
    probes = {
        'sop': ('sopNodeTypeCategory',),
        'obj': ('objNodeTypeCategory',),
        'object': ('objNodeTypeCategory',),
        'vop': ('vopNodeTypeCategory',),
        'dop': ('dopNodeTypeCategory',),
        'cop': ('cop2NodeTypeCategory', 'copNodeTypeCategory'),
        'cop2': ('cop2NodeTypeCategory', 'copNodeTypeCategory'),
        'top': ('topNodeTypeCategory',),
        'lop': ('lopNodeTypeCategory',),
        'chop': ('chopNodeTypeCategory',),
        'rop': ('ropNodeTypeCategory',),
        'out': ('ropNodeTypeCategory',),
    }.get((context or '').lower(), ())

    try:
        import hou
    except ImportError:
        return None

    for probe in probes:
        getter = getattr(hou, probe, None)
        if getter is None:
            continue
        try:
            return getter()
        except Exception:
            continue
    return None


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
        # Outside Houdini: use the newest Houdini prefs directory we can find.
        from .houdini_paths import newest_prefs_dir
        prefs = newest_prefs_dir()
        if prefs is None:
            raise RuntimeError(
                "Could not find a Houdini preferences directory. "
                "Set SOPDROP_HOUDINI_PATH or run this from inside Houdini.")
        shelf_dir = prefs / "toolbar"

    shelf_dir.mkdir(parents=True, exist_ok=True)
    return shelf_dir


def get_shelf_file() -> Path:
    """Get the path to the Sopdrop library shelf file."""
    return get_shelf_dir() / SHELF_FILE_NAME


def _load_shelf_file(shelf_file: Path) -> None:
    """Load a shelf file into the running Houdini session.

    hou.shelves.loadFile is the targeted call, but it has historically been
    the first thing to break across Houdini versions, so fall back to a full
    reload of every shelf file before giving up.
    """
    import hou

    try:
        hou.shelves.loadFile(str(shelf_file))
    except Exception:
        hou.shelves.reloadFiles()


# ==============================================================================
# XML Generation
# ==============================================================================

def generate_tool_xml(asset: Dict[str, Any], library_type: str = 'personal',
                      net_types: Optional[Dict[str, str]] = None) -> str:
    """Generate XML for a single tool."""
    asset_id = asset.get('id', '')
    name = asset.get('name', 'Untitled')
    context = asset.get('context', 'sop').lower()
    asset_type = asset.get('asset_type', 'node')
    description = asset.get('description', '')

    # Skip VEX snippets and path assets - they don't belong in the TAB menu
    if asset_type == 'vex' or context == 'vex':
        return ''
    if context == 'path':
        return ''

    # Get Houdini network type. Contexts this Houdini can't host are skipped
    # rather than written out with a network type the shelf loader will reject.
    if net_types is None:
        net_types = get_context_net_types()
    net_type = net_types.get(context)
    if net_type is None:
        net_type = net_types.get('sop')
        if net_type is None:
            return ''

    # Tool name (must be unique, valid identifier)
    safe_id = asset_id.replace('-', '_')
    tool_name = f"sopdrop_lib_{safe_id}"

    # Prefix with (SD) so users know it's a Sopdrop snippet, not a native node
    label = f"(SD) {name}"

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

    # Script — ensure sopdrop paths are on sys.path (same setup as pypanel
    # and shelf tools) so this works even if pythonrc hasn't run yet or the
    # Library panel hasn't been opened.
    script = f'''import sys, os
_sp = os.environ.get("SOPDROP_HOUDINI_PATH", "")
if _sp:
    for _sd in ("scripts", "client"):
        _p = os.path.join(_sp, _sd)
        if _p not in sys.path:
            sys.path.insert(0, _p)
try:
    import sopdrop.menu
    # Pass Houdini's tool kwargs through so a dragged wire (TAB on an
    # extended connector) auto-connects to the pasted recipe's entry node.
    sopdrop.menu.paste_asset("{asset_id}", kwargs=globals().get("kwargs"))
except ImportError:
    import hou
    hou.ui.displayMessage(
        "Sopdrop is not installed.\\nRun: pip install sopdrop",
        title="Sopdrop", severity=hou.severityType.Warning)
except Exception as e:
    import hou
    hou.ui.displayMessage(str(e), title="Sopdrop Error")'''

    # Help text
    help_text = description if description else f"Paste {name} from Sopdrop library"

    # Use asset's Houdini icon if set, otherwise fall back to sopdrop icon
    asset_icon = asset.get('icon', '')
    if asset_icon:
        icon_attr = asset_icon  # Houdini icon name like "SOP_scatter"
    else:
        icon_attr = "$SOPDROP_HOUDINI_PATH/toolbar/icons/sopdrop_logo.svg"

    return f'''
  <tool name="{tool_name}" label="{escape(label)}" icon="{escape(icon_attr)}">
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


def generate_browse_tool_xml(context: str, net_type: Optional[str] = None) -> str:
    """Generate XML for a 'Browse Library...' tool."""
    if net_type is None:
        net_type = get_context_net_types().get(context)
        if net_type is None:
            return ''
    tool_name = f"sopdrop_browse_{context}"

    return f'''
  <tool name="{tool_name}" label="Browse Library..." icon="$SOPDROP_HOUDINI_PATH/toolbar/icons/sopdrop_logo.svg">
    <helpText><![CDATA[Open the Sopdrop Library panel to browse all assets.]]></helpText>
    <toolSubmenu>Sopdrop</toolSubmenu>
    <toolMenuContext name="network">
      <contextNetType>{net_type}</contextNetType>
    </toolMenuContext>
    <script scriptType="python"><![CDATA[
import sys, os
_sp = os.environ.get("SOPDROP_HOUDINI_PATH", "")
if _sp:
    for _sd in ("scripts", "client"):
        _p = os.path.join(_sp, _sd)
        if _p not in sys.path:
            sys.path.insert(0, _p)
try:
    import sopdrop.menu
    sopdrop.menu.open_library_panel()
except ImportError:
    import hou
    hou.ui.displayMessage(
        "Sopdrop is not installed.\\nRun: pip install sopdrop",
        title="Sopdrop", severity=hou.severityType.Warning)
except Exception as e:
    import hou
    hou.ui.displayMessage(str(e), title="Sopdrop Error")
]]></script>
    <keywordList>
      <keyword>sopdrop,library,browse,assets</keyword>
    </keywordList>
  </tool>'''


def build_shelf(personal_assets: List[Dict[str, Any]],
                team_assets: List[Dict[str, Any]] = None):
    """Build the shelf XML document and the set of tool names it defines.

    The names come back with the XML so callers never have to re-derive them
    and drift from what was actually written.
    """
    # Resolve network types once — they depend on the running Houdini version
    net_types = get_context_net_types()

    # Generate tool XML for each asset
    tool_xmls = []
    tool_names = set()

    for asset in personal_assets:
        xml = generate_tool_xml(asset, 'personal', net_types)
        if xml:
            tool_xmls.append(xml)
            tool_names.add(f"sopdrop_lib_{asset.get('id', '').replace('-', '_')}")

    if team_assets:
        for asset in team_assets:
            xml = generate_tool_xml(asset, 'team', net_types)
            if xml:
                tool_xmls.append(xml)
                tool_names.add(f"sopdrop_lib_{asset.get('id', '').replace('-', '_')}")

    # Add one browse tool per network type. Several of our contexts share a
    # network type (rop/out, and cop/cop2 once the legacy COP2 network is
    # gone), and duplicates would just clutter the TAB menu.
    seen_net_types = set()
    for context, net_type in net_types.items():
        if net_type in seen_net_types:
            continue
        xml = generate_browse_tool_xml(context, net_type)
        if xml:
            seen_net_types.add(net_type)
            tool_xmls.append(xml)
            tool_names.add(f"sopdrop_browse_{context}")

    tools_xml = '\n'.join(tool_xmls)

    xml_content = f'''<?xml version="1.0" encoding="UTF-8"?>
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
    return xml_content, tool_names


def generate_shelf_xml(personal_assets: List[Dict[str, Any]], team_assets: List[Dict[str, Any]] = None) -> str:
    """Generate the complete shelf XML document."""
    return build_shelf(personal_assets, team_assets)[0]


# ==============================================================================
# Main Functions
# ==============================================================================

def collect_menu_assets(skip_team: bool = False, quiet: bool = True):
    """Collect (personal_assets, team_assets) for menu display.

    Shared by the native TAB-menu shelf generation (regenerate_menu) and
    Sopdrop's own Shift+Tab menu (tabmenu.py).

    Handles active-library switching safely: waits for any in-flight
    Library-panel worker before close_db() (docs/crash-safety.md #13/#16)
    and always restores the original active library.

    Team assets:
      - HTTP mode: served by the mirror-backed get_all_assets_cached()
        (ETag cache + persistent disk mirror) — works offline and without
        the Library panel ever having been opened. Assets arrive with
        'collections' already populated.
      - NAS mode: search_assets() against the local mirror SQLite.
    """
    from .library import search_assets, close_db, get_all_assets_cached
    from .config import (
        get_active_library, set_active_library,
        get_team_library_mode, is_team_library_configured,
    )

    def _switch(lib):
        _wait_for_library_worker()
        close_db()
        set_active_library(lib)

    original_library = get_active_library()
    personal_assets, team_assets = [], []

    try:
        if original_library != 'personal':
            _switch('personal')

        personal_assets = search_assets(limit=500)
        _enrich_with_collections(personal_assets)

        if is_team_library_configured() and not skip_team:
            try:
                _switch('team')
                if get_team_library_mode() == 'http':
                    team_assets, _ = get_all_assets_cached()
                else:
                    team_assets = search_assets(limit=500)
                    _enrich_with_collections(team_assets)
            except Exception as e:
                if not quiet:
                    print(f"[Sopdrop] Could not load team assets for menu: {e}")
    finally:
        try:
            if get_active_library() != original_library:
                _switch(original_library)
        except Exception:
            pass

    return personal_assets, team_assets


def regenerate_menu(quiet: bool = False, skip_reload: bool = False, skip_team: bool = False) -> bool:
    """
    Regenerate the TAB menu shelf file from the library.

    This creates a shelf file with tools for each library asset.
    The tools appear in the TAB menu under Sopdrop/[Personal|Team]/[Collection]/[Name].

    When the native TAB menu is disabled (config `tab_menu_enabled`, the
    default — recipes live in the Shift+Tab menu instead), the shelf is
    written with only the "Browse Library..." tools and any stale per-asset
    tools are destroyed.

    Args:
        quiet: If True, suppress print output
        skip_team: If True, only include personal library assets (avoids NAS access).
                   Team assets will be added when the Library panel opens.

    Returns:
        True if successful
    """
    try:
        from .config import get_tab_menu_enabled

        tab_enabled = get_tab_menu_enabled()
        personal_assets, team_assets = [], []
        if tab_enabled:
            personal_assets, team_assets = collect_menu_assets(
                skip_team=skip_team, quiet=quiet)
        elif not quiet:
            print("[Sopdrop] Native TAB menu recipes disabled — use Shift+Tab "
                  "in the network editor (enable in Settings to restore)")

        total = len(personal_assets) + len(team_assets)
        if not quiet:
            print(f"[Sopdrop] Regenerating TAB menu with {total} assets...")

        # Generate XML
        xml_content, valid_tools = build_shelf(
            personal_assets, team_assets if team_assets else None)

        # Write shelf file
        shelf_file = get_shelf_file()
        shelf_file.write_text(xml_content)

        if not quiet:
            print(f"[Sopdrop] Created {total} tools in TAB menu")
            print(f"[Sopdrop] Shelf file: {shelf_file}")
            if tab_enabled:
                print("[Sopdrop] Press TAB and type 'sopdrop' to find your assets")
            else:
                print("[Sopdrop] Press Shift+Tab in a network editor to browse recipes")

        # Try to reload shelves in Houdini (skip during modal
        # dialog saves — hou.shelves.loadFile modifies UI state which
        # can crash on Windows when called inside an exec_() event loop).
        if not skip_reload:
            try:
                import hou

                # Destroy orphaned tools (e.g. deleted assets still in memory)
                for tool_name in list(hou.shelves.tools().keys()):
                    if tool_name.startswith('sopdrop_lib_') or tool_name.startswith('sopdrop_browse_'):
                        if tool_name not in valid_tools:
                            try:
                                hou.shelves.tools()[tool_name].destroy()
                            except Exception:
                                pass

                _load_shelf_file(shelf_file)
                debug("Shelf reloaded - tools should appear in TAB menu")
            except ImportError:
                pass
            except Exception as e:
                # A shelf that won't load means an empty TAB menu, which is
                # otherwise indistinguishable from an empty library — always
                # say so, even in quiet mode.
                print(f"[Sopdrop] Could not load the TAB menu shelf: {e}")
                print(f"[Sopdrop] Shelf file: {shelf_file}")
                print("[Sopdrop] Run sopdrop.menu.diagnose() for details.")

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


def diagnose() -> None:
    """Print why the Sopdrop TAB menu might not be showing up.

    Almost every way the TAB menu can fail is silent by design — a missing
    env var, a shelf file Houdini declined to parse, a library that came back
    empty.  This walks the same path and says what it finds.
    """
    print("=== Sopdrop TAB menu diagnostics ===")

    try:
        import hou
        print(f"Houdini:               {hou.applicationVersionString()}")
        print(f"Python:                {sys.version.split()[0]}")
    except ImportError:
        print("Houdini:               not running inside Houdini")
        hou = None

    sopdrop_path = os.environ.get('SOPDROP_HOUDINI_PATH', '')
    print(f"SOPDROP_HOUDINI_PATH:  {sopdrop_path or '(not set)'}")
    if not sopdrop_path:
        print("  -> Houdini can't find the Sopdrop package. Re-run install.py,")
        print("     or point a Houdini package file at the integration folder.")

    print(f"HOUDINI_TOOLBAR_PATH:  {os.environ.get('HOUDINI_TOOLBAR_PATH', '(default)')}")

    net_types = get_context_net_types()
    print(f"Network types in use:  {', '.join(sorted(set(net_types.values())))}")
    dropped = sorted(set(CONTEXT_TO_NETTYPE) - set(net_types))
    if dropped:
        print(f"Contexts unavailable:  {', '.join(dropped)} (not in this Houdini)")

    try:
        shelf_file = get_shelf_file()
    except Exception as e:
        print(f"Shelf file:            could not resolve ({e})")
        return

    print(f"Shelf file:            {shelf_file}")
    if not shelf_file.exists():
        print("  -> Not written yet. Run sopdrop.menu.regenerate_menu().")
        return

    try:
        import xml.etree.ElementTree as ET
        root = ET.parse(str(shelf_file)).getroot()
        tools = root.findall('tool')
        print(f"Tools in shelf file:   {len(tools)}")
    except Exception as e:
        print(f"  -> Shelf file is not valid XML: {e}")
        print("     Run sopdrop.menu.cleanup_menu() to rebuild it.")
        return

    if hou is None:
        return

    loaded = [name for name in hou.shelves.tools()
              if name.startswith('sopdrop_lib_') or name.startswith('sopdrop_browse_')]
    print(f"Tools loaded by Houdini: {len(loaded)}")
    if not loaded:
        print("  -> Houdini did not load the shelf file. Trying now...")
        try:
            _load_shelf_file(shelf_file)
            print("     Loaded. Press TAB and type 'sopdrop'.")
        except Exception as e:
            print(f"     Failed: {e}")


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
# Wire auto-connect (dragged-wire TAB menu paste)
# ==============================================================================

def _find_entry_node(nodes):
    """Pick the recipe's entry node — the one a dragged output wire should feed.

    The most-upstream node that accepts an input and has its first input free:
    prefer true sources (all inputs empty), then the topmost (highest Y), then
    the leftmost. Returns None if nothing in the recipe can take an input.
    """
    candidates = []
    for n in nodes:
        try:
            if n.type().maxNumInputs() <= 0:
                continue
            if n.input(0) is not None:
                continue
        except Exception:
            continue
        candidates.append(n)

    if not candidates:
        return None

    def sort_key(n):
        try:
            connected = sum(1 for i in n.inputs() if i is not None)
        except Exception:
            connected = 0
        try:
            pos = n.position()
            return (connected, -pos[1], pos[0])
        except Exception:
            return (connected, 0, 0)

    candidates.sort(key=sort_key)
    return candidates[0]


def _find_exit_node(nodes):
    """Pick the recipe's exit node — the one whose output a dragged input wire
    should connect to.

    A node with an output that isn't already feeding another node in the recipe
    (a sink), preferring the bottommost (lowest Y) then leftmost. Returns None
    if nothing qualifies.
    """
    node_set = set(nodes)
    candidates = []
    for n in nodes:
        try:
            if n.type().maxNumOutputs() <= 0:
                continue
        except Exception:
            pass
        try:
            downstream = [c.outputNode() for c in n.outputConnections()]
        except Exception:
            downstream = []
        feeds_recipe = any(d in node_set for d in downstream if d is not None)
        if not feeds_recipe:
            candidates.append(n)

    if not candidates:
        return None

    def sort_key(n):
        try:
            pos = n.position()
            return (pos[1], pos[0])
        except Exception:
            return (0, 0)

    candidates.sort(key=sort_key)
    return candidates[0]


def _autowire_from_kwargs(kwargs, created_items, target):
    """Connect a dragged network-editor wire to the just-pasted recipe.

    Houdini populates the TAB tool's kwargs with the pending connection:
      - dragging from an output: ``inputnodename`` + ``outputindex`` — wire that
        output into the recipe's entry node (forward, the common case).
      - dragging from an input: ``outputnodename`` + ``inputindex`` — wire the
        recipe's exit node into that input (reverse).
    Best-effort: any failure is logged and ignored so paste still succeeds.
    """
    if not kwargs:
        return
    try:
        import hou
    except ImportError:
        return

    try:
        nodes = [it for it in (created_items or [])
                 if isinstance(it, hou.Node) and it.parent() == target]
        if not nodes:
            return

        # Forward: dragged from a node's output (extending a wire down).
        in_name = kwargs.get('inputnodename')
        if in_name:
            src = target.node(in_name)
            if src is not None:
                entry = _find_entry_node(nodes)
                if entry is not None:
                    out_idx = int(kwargs.get('outputindex', 0) or 0)
                    entry.setInput(0, src, out_idx)
                    return

        # Reverse: dragged from a node's input (extending a wire up).
        out_name = kwargs.get('outputnodename')
        if out_name:
            dst = target.node(out_name)
            if dst is not None:
                exit_node = _find_exit_node(nodes)
                if exit_node is not None:
                    in_idx = int(kwargs.get('inputindex', 0) or 0)
                    dst.setInput(in_idx, exit_node, 0)
    except Exception as e:
        print(f"[Sopdrop] Auto-wire skipped: {e}")


# ==============================================================================
# Menu Actions (called from tool scripts)
# ==============================================================================

def _wait_for_library_worker(timeout_ms=2000):
    """Wait for any in-flight library panel background worker to finish.

    Must be called before close_db() to avoid closing the SQLite connection
    while a _LibraryWorker thread is mid-query (segfault).
    See docs/crash-safety.md Fix #13.
    """
    try:
        import sopdrop_library_panel as panel_mod
        panels = getattr(panel_mod, '_active_panels', [])
    except ImportError:
        return

    for ref in panels:
        panel = ref()
        if panel is not None:
            worker = getattr(panel, '_worker', None)
            if worker is not None and worker.isRunning():
                print("[Sopdrop] Waiting for library worker to finish before switching DB...")
                worker.wait(timeout_ms)


def paste_asset(asset_id: str, kwargs=None, pane=None, position=None):
    """
    Paste an asset from the library into the current network.
    Called from TAB menu tools and the Shift+Tab menu (tabmenu.py).

    Args:
        asset_id: Library asset UUID to paste.
        kwargs: Houdini tool kwargs from the network-editor TAB menu. When the
            tool is invoked by dragging a wire, this carries the source/target
            node + connector index so the dragged wire can auto-connect to the
            pasted recipe (mirroring native node creation). None when pasted
            without a dragged wire.
        pane: Target hou.NetworkEditor. Defaults to the first network editor
            found. The Shift+Tab menu passes the editor the key was pressed
            in so multi-editor layouts paste into the right network.
        position: (x, y) network coords to paste at. Defaults to the pane's
            current cursor position. The Shift+Tab menu passes the position
            captured at keypress (the mouse moves to use the popup).
    """
    try:
        import hou
    except ImportError:
        return

    try:
        from .library import load_asset_package, record_asset_use, get_asset, close_db
        from .config import (
            get_active_library, set_active_library, is_team_library_configured,
        )
        from .importer import import_items
    except ImportError as e:
        hou.ui.displayMessage(
            f"Sopdrop module import failed: {e}\n\nTry: pip install sopdrop",
            title="Sopdrop", severity=hou.severityType.Warning)
        return

    try:
        # Get current network editor
        if pane is None:
            pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if not pane:
            hou.ui.displayMessage("No network editor found")
            return

        target = pane.pwd()

        # Load the asset info — try current library first, then the other one.
        # This handles the case where a team asset is in the TAB menu from a
        # previous session but the current library is set to personal (or vice
        # versa).
        original_library = get_active_library()
        asset = get_asset(asset_id)
        switched = False
        if asset is None:
            other = 'team' if original_library == 'personal' else 'personal'
            # Only try team if a team library is configured (NAS path OR
            # HTTP mode + slug — gating on the path alone silently broke
            # team pastes for HTTP-mode teams until the panel was opened)
            if other == 'team' and not is_team_library_configured():
                pass
            else:
                try:
                    print(f"[Sopdrop] Asset {asset_id[:8]}... not in {original_library} library, trying {other}...")
                    _wait_for_library_worker()
                    close_db()
                    set_active_library(other)
                    asset = get_asset(asset_id)
                    if asset is not None:
                        switched = True
                        print(f"[Sopdrop] Found in {other} library")
                    else:
                        # Restore original
                        _wait_for_library_worker()
                        close_db()
                        set_active_library(original_library)
                except Exception as e:
                    print(f"[Sopdrop] Could not check {other} library: {e}")
                    try:
                        _wait_for_library_worker()
                        close_db()
                        set_active_library(original_library)
                    except Exception:
                        pass

        try:
            # Handle HDA assets differently to avoid UTF-8 issues
            if asset and asset.get('asset_type') == 'hda':
                created = paste_hda(asset, target, pane, position=position)
                _autowire_from_kwargs(kwargs, created, target)
                try:
                    record_asset_use(asset_id)
                except Exception:
                    pass  # Non-critical — paste already succeeded
                return

            # Load the package for node assets
            package = load_asset_package(asset_id)
            if not package:
                print(f"[Sopdrop] Asset not found: {asset_id}")
                hou.ui.displayMessage(
                    f"Asset not found in library (ID: {asset_id[:12]}...).\n\n"
                    "This can happen if:\n"
                    "  - The asset was deleted\n"
                    "  - The team library mirror hasn't synced yet\n"
                    "    (open the Library panel to trigger sync)\n"
                    "  - The library database needs rebuilding",
                    title="Sopdrop - Not Found")
                return

            # New-format curves: keyframe-only data, apply to scoped parms
            if "curves" in package:
                from .curves import apply_curves

                target_parms = []
                for node in hou.selectedNodes():
                    for p in node.parms():
                        if p.isScoped():
                            target_parms.append(p)

                if not target_parms:
                    hou.ui.displayMessage(
                        "No scoped channels found.\n\n"
                        "Select a node and scope channels in the Animation Editor,\n"
                        "then try pasting again.",
                        title="Sopdrop - Paste Curves",
                    )
                    return

                apply_curves(package["curves"], target_parms)
                try:
                    record_asset_use(asset_id)
                except Exception:
                    pass
                return

            # Old-format curves: use original network context for matching,
            # then patch the package so the importer doesn't reject it.
            pkg_ctx_raw = package.get('context', '').lower()
            if pkg_ctx_raw == 'curves':
                source_ctx = package.get('metadata', {}).get('source_context', '')
                if source_ctx:
                    package['context'] = source_ctx
                    pkg_ctx_raw = source_ctx

            # Check context compatibility (case-insensitive)
            target_ctx = target.childTypeCategory().name().upper()
            expected_ctx = get_context_net_types().get(pkg_ctx_raw, '').upper()

            if expected_ctx and target_ctx != expected_ctx:
                name = asset.get('name', 'Asset') if asset else 'Asset'
                result = hou.ui.displayMessage(
                    f"'{name}' is a {pkg_ctx_raw.upper()} asset.\n"
                    f"You're in a {target_ctx} network.\n\n"
                    "Paste anyway?",
                    buttons=("Paste", "Cancel"),
                    default_choice=1
                )
                if result == 1:
                    return

            # Get placement position (explicit position from the Shift+Tab
            # menu wins; otherwise the pane's live cursor position)
            cursor_pos = position if position is not None else pane.cursorPosition()

            # Import the nodes
            created = import_items(package, target, position=cursor_pos)

            # If the tool was invoked by dragging a wire in the network editor,
            # connect that wire to the pasted recipe — same QoL behavior as
            # creating a native node on an extended connector.
            _autowire_from_kwargs(kwargs, created, target)

            # Record usage (non-critical — paste already succeeded)
            try:
                record_asset_use(asset_id)
            except Exception:
                pass
        finally:
            # Restore original library if we switched
            if switched:
                try:
                    _wait_for_library_worker()
                    close_db()
                    set_active_library(original_library)
                except Exception:
                    pass

    except Exception as e:
        import hou
        hou.ui.displayMessage(f"Failed to paste asset: {e}")
        import traceback
        traceback.print_exc()


def paste_hda(asset, target, pane, position=None):
    """Paste an HDA asset, handling binary file correctly to avoid UTF-8 errors.

    Returns a list with the created node (for auto-wiring), or None.
    """
    import hou

    def _place_pos():
        if position is not None:
            return hou.Vector2(position[0], position[1])
        return pane.cursorPosition()

    file_path = asset.get('file_path', '')
    if not file_path or not os.path.exists(file_path):
        hou.ui.displayMessage("HDA file not found")
        return None

    node = None
    try:
        # Install the HDA definition. installFile() can raise
        # hou.LoadWarning for non-fatal issues even though the definition
        # was installed — log and continue instead of failing the paste.
        try:
            hou.hda.installFile(file_path)
        except hou.LoadWarning as e:
            print(f"[Sopdrop] HDA installed with warnings (ignored):\n{e}")

        # Get the type name from asset metadata
        hda_type_name = asset.get('hda_type_name', '')

        if hda_type_name:
            # Try to create a node of this type
            try:
                # Extract base type name (strip version namespace)
                base_type = hda_type_name.split('::')[0] if '::' in hda_type_name else hda_type_name
                cursor_pos = _place_pos()
                node = target.createNode(hda_type_name)
                if node:
                    node.setPosition(cursor_pos)
                    node.setSelected(True, clear_all_selected=True)
            except Exception:
                # Try with full type name
                try:
                    node = target.createNode(base_type)
                    if node:
                        node.setPosition(_place_pos())
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

    return [node] if node is not None else None


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


def trigger_regenerate(skip_reload: bool = True):
    """Trigger menu regeneration if enabled.

    Args:
        skip_reload: If True (default), skip hou.shelves.loadFile() — safe for
                     calls during modal dialogs (e.g., SaveToLibraryDialog).
                     If False, also reload the shelf file in Houdini so changes
                     appear immediately (use for deletes, renames, etc.).
    """
    if should_regenerate():
        try:
            regenerate_menu(quiet=True, skip_reload=skip_reload)
        except:
            pass
