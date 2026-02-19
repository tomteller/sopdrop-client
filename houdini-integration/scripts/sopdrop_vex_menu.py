"""
Sopdrop VEX Snippet Menu

Provides VEX snippet integration for Houdini parameter context menus.
When right-clicking on VEX-capable parameters (e.g., wrangle nodes),
users can browse and paste VEX snippets from their Sopdrop library.

Usage:
    Called automatically by Houdini's parameter menu callback system.
    Can also be used directly:

        from sopdrop_vex_menu import paste_vex_to_parm, save_parm_as_snippet
        paste_vex_to_parm(parm, snippet_id)
        save_parm_as_snippet(parm)
"""

import hou


# VEX-capable parameter types (wrangle snippet fields, expression fields)
VEX_PARM_NAMES = {
    'snippet', 'code', 'vexcode', 'vex_code', 'script',
    'expr', 'expression', 'vexpression',
}

# Node types known to have VEX snippet parameters
VEX_NODE_TYPES = {
    'attribwrangle', 'pointwrangle', 'volumewrangle',
    'attribvop', 'volumevop',
    'snippet',
}


def is_vex_parameter(parm):
    """Check if a parameter is a VEX-capable text field."""
    if not parm:
        return False

    # Check by parameter name
    name = parm.name().lower()
    if name in VEX_PARM_NAMES:
        return True

    # Check by node type
    node = parm.node()
    if node:
        type_name = node.type().name().lower()
        if any(vt in type_name for vt in VEX_NODE_TYPES):
            # Only match string-type parms on wrangle nodes
            template = parm.parmTemplate()
            if template.type() == hou.parmTemplateType.String:
                return True

    return False


def get_vex_snippets():
    """Get all VEX snippets from the library, grouped by collection."""
    try:
        from sopdrop import library
        assets = library.search_assets(context='vex', limit=200)

        # Group by collection
        grouped = {}
        ungrouped = []
        for asset in assets:
            colls = asset.get('collections', [])
            if colls:
                coll_name = colls[0]['name'] if isinstance(colls[0], dict) else 'Library'
                grouped.setdefault(coll_name, []).append(asset)
            else:
                ungrouped.append(asset)

        return grouped, ungrouped
    except Exception as e:
        print(f"[Sopdrop] Failed to load VEX snippets: {e}")
        return {}, []


def paste_vex_to_parm(parm, asset_id):
    """Paste a VEX snippet's code into a Houdini parameter."""
    try:
        from sopdrop import library
        package = library.load_asset_package(asset_id)
        if package and 'code' in package:
            parm.set(package['code'])
            print(f"[Sopdrop] Pasted VEX snippet to {parm.path()}")
        else:
            hou.ui.displayMessage("Failed to load VEX snippet", severity=hou.severityType.Error)
    except Exception as e:
        hou.ui.displayMessage(f"Failed to paste snippet: {e}", severity=hou.severityType.Error)


def save_parm_as_snippet(parm, name=None):
    """Save the current parameter value as a VEX snippet in the library."""
    try:
        from sopdrop import library

        code = parm.eval()
        if not code or not code.strip():
            hou.ui.displayMessage("Parameter is empty", severity=hou.severityType.Warning)
            return

        if not name:
            result = hou.ui.readInput(
                "Enter a name for this VEX snippet:",
                buttons=("Save", "Cancel"),
                title="Save VEX Snippet",
                initial_contents=parm.node().name() if parm.node() else "",
            )
            if result[0] != 0 or not result[1].strip():
                return
            name = result[1].strip()

        # Optional tags
        tag_result = hou.ui.readInput(
            "Tags (comma-separated, optional):",
            buttons=("Save", "Skip"),
            title="Save VEX Snippet",
        )
        tags = []
        if tag_result[0] == 0 and tag_result[1].strip():
            tags = [t.strip() for t in tag_result[1].split(',') if t.strip()]

        library.save_vex_snippet(name=name, code=code, tags=tags)
        hou.ui.displayMessage(f"Saved VEX snippet: {name}", title="Sopdrop")

    except ImportError:
        hou.ui.displayMessage(
            "Sopdrop client not installed.\n\nInstall with: pip install sopdrop",
            title="Sopdrop",
            severity=hou.severityType.Error,
        )
    except Exception as e:
        hou.ui.displayMessage(f"Failed to save snippet: {e}", severity=hou.severityType.Error)


def build_vex_menu(parm):
    """
    Build a menu list for Houdini's parameter context menu.

    Returns a list of (token, label) tuples suitable for
    hou.ParmTemplate.setMenuItems().

    This is called by Houdini's PARMmenu.xml callback system.
    """
    items = []

    if not is_vex_parameter(parm):
        return items

    # "Save current code" option
    items.append(("sopdrop_vex_save", "Save to Sopdrop Library..."))
    items.append(("", ""))  # separator

    # VEX snippets from library
    grouped, ungrouped = get_vex_snippets()

    for coll_name, assets in sorted(grouped.items()):
        # Collection header (non-selectable)
        items.append(("", f"--- {coll_name} ---"))
        for asset in assets:
            token = f"sopdrop_vex_{asset['id']}"
            items.append((token, asset.get('name', 'Untitled')))

    if ungrouped:
        if grouped:
            items.append(("", "--- Library ---"))
        for asset in ungrouped:
            token = f"sopdrop_vex_{asset['id']}"
            items.append((token, asset.get('name', 'Untitled')))

    if not grouped and not ungrouped:
        items.append(("", "(No VEX snippets in library)"))

    return items


def handle_vex_menu_action(parm, token):
    """Handle a VEX menu selection."""
    if token == "sopdrop_vex_save":
        save_parm_as_snippet(parm)
    elif token.startswith("sopdrop_vex_"):
        asset_id = token.replace("sopdrop_vex_", "")
        paste_vex_to_parm(parm, asset_id)
