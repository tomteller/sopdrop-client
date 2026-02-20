"""
Export module for Sopdrop.

Handles exporting Houdini nodes to the .sopdrop package format.
Uses Houdini's native saveItemsToFile() for reliable serialization.
"""

import base64
import hashlib
import json
import tempfile
import os
from typing import List, Dict, Any, Optional

# Version of the package format
CHOP_FORMAT_VERSION = "sopdrop-v2"


def _is_non_commercial() -> bool:
    """Check if running a non-commercial Houdini license."""
    try:
        import hou
        # hou.isApprentice() returns True for Apprentice (free non-commercial)
        if hasattr(hou, 'isApprentice') and hou.isApprentice():
            return True
        # Also check license category for NC (Non-Commercial) licenses
        if hasattr(hou, 'licenseCategory'):
            cat = hou.licenseCategory()
            if cat in (hou.licenseCategoryType.Apprentice,):
                return True
        return False
    except Exception:
        return False


def export_items(items) -> Dict[str, Any]:
    """
    Export selected Houdini items as a .sopdrop package.

    Uses saveItemsToFile() (v2/cpio) for commercial licenses, and
    asCode() (v1/python) for non-commercial licenses to avoid
    embedding the NC license flag in the binary data.

    Args:
        items: List of hou.NetworkMovableItem (nodes, network boxes, sticky notes)

    Returns:
        A .sopdrop package dictionary ready for JSON serialization
    """
    import hou

    if not items:
        raise ValueError("No items to export")

    # Separate items by type for metadata
    nodes = []
    network_boxes = []
    sticky_notes = []

    for item in items:
        if isinstance(item, hou.Node):
            nodes.append(item)
        elif isinstance(item, hou.NetworkBox):
            network_boxes.append(item)
        elif isinstance(item, hou.StickyNote):
            sticky_notes.append(item)

    if not nodes:
        raise ValueError("No nodes to export. Select at least one node.")

    # All items must share the same parent
    parent = nodes[0].parent()
    for node in nodes[1:]:
        if node.parent() != parent:
            raise ValueError("All selected nodes must be in the same network")

    # Get context
    context = _get_context(parent)

    # Collect metadata before serialization
    node_types = []
    node_names = []
    all_node_count = 0

    for node in nodes:
        node_types.append(node.type().name())
        node_names.append(node.name())
        all_node_count += 1
        # Count children recursively
        all_node_count += len(node.allSubChildren())

    # Detect HDA dependencies
    dependencies = _detect_hda_dependencies(nodes)

    # Capture node graph data (positions, connections, types)
    node_graph = _capture_node_graph(nodes)

    # Non-commercial Houdini: use asCode() (v1 format) to avoid
    # embedding the NC license flag in .cpio binary data
    if _is_non_commercial():
        return _export_as_code(
            items, nodes, network_boxes, sticky_notes,
            parent, context, node_types, node_names,
            all_node_count, dependencies, node_graph
        )

    # Commercial: use saveItemsToFile (v2/cpio) for full fidelity
    with tempfile.NamedTemporaryFile(suffix='.cpio', delete=False) as f:
        temp_path = f.name

    try:
        # saveItemsToFile handles nodes, network boxes, sticky notes, connections
        parent.saveItemsToFile(items, temp_path)

        # Read the binary data
        with open(temp_path, 'rb') as f:
            binary_data = f.read()

        # Encode as base64 for JSON transport
        encoded_data = base64.b64encode(binary_data).decode('ascii')

        # Generate checksum for integrity verification
        checksum = hashlib.sha256(binary_data).hexdigest()

    finally:
        # Clean up temp file
        if os.path.exists(temp_path):
            os.unlink(temp_path)

    # Build the package
    package = {
        "format": CHOP_FORMAT_VERSION,
        "context": context,
        "houdini_version": hou.applicationVersionString(),
        "metadata": {
            "node_count": all_node_count,
            "top_level_count": len(nodes),
            "node_types": list(set(node_types)),
            "node_names": node_names,
            "network_boxes": len(network_boxes),
            "sticky_notes": len(sticky_notes),
            "has_hda_dependencies": len(dependencies) > 0,
            "node_graph": node_graph,
        },
        "dependencies": dependencies,
        "data": encoded_data,
        "checksum": checksum,
    }

    return package


def _export_as_code(items, nodes, network_boxes, sticky_notes,
                    parent, context, node_types, node_names,
                    all_node_count, dependencies, node_graph) -> Dict[str, Any]:
    """
    Export using asCode() — produces license-neutral Python code.

    Used for non-commercial Houdini to avoid the NC flag in .cpio data.
    The v1 format stores executable Python that recreates the node network.

    Sticky notes and network boxes are serialized manually (not via asCode)
    to ensure correct relative positioning when imported.
    """
    import hou
    import re

    parent_path = parent.path()
    # Escape parent path for regex (handles special chars in node names)
    parent_path_escaped = re.escape(parent_path)

    code_parts = []

    # Export each node via asCode, with unique variable prefix per node
    # to avoid variable name collisions between multiple asCode outputs
    for idx, node in enumerate(nodes):
        code = node.asCode(
            brief=True,
            save_box_membership=False,
            save_outgoing_wires=True,
        )
        # Prefix variable names to avoid collisions between nodes.
        # asCode uses: hou_node, hou_parm, hou_parm_template, hou_parm_template_group
        # Use regex word boundaries (\b) to match exact names only,
        # so 'hou_parm' won't accidentally match inside 'hou_parm_template'.
        for var in ['hou_parm_template_group', 'hou_parm_template', 'hou_parm', 'hou_node']:
            code = re.sub(r'\b' + var + r'\b', f'{var}_{idx}', code)
        code_parts.append(code)

    # Export network boxes with explicit creation code
    for i, netbox in enumerate(network_boxes):
        pos = netbox.position()
        size = netbox.size()
        color = netbox.color()
        comment = netbox.comment()
        name = netbox.name()
        code_parts.append(f"""
# Network box: {name}
hou_netbox_{i} = hou_parent.createNetworkBox("{name}")
hou_netbox_{i}.setPosition(hou.Vector2({pos[0]}, {pos[1]}))
hou_netbox_{i}.setSize(hou.Vector2({size[0]}, {size[1]}))
hou_netbox_{i}.setColor(hou.Color(({color.rgb()[0]}, {color.rgb()[1]}, {color.rgb()[2]})))
hou_netbox_{i}.setComment({repr(comment)})""")
        # Add contained items by name
        for item in netbox.items():
            item_name = item.name()
            code_parts.append(
                f'try:\n'
                f'    _item = hou_parent.item("{item_name}")\n'
                f'    if _item: hou_netbox_{i}.addItem(_item)\n'
                f'except: pass'
            )

    # Export sticky notes with explicit creation code to preserve positions
    for i, sticky in enumerate(sticky_notes):
        pos = sticky.position()
        size = sticky.size()
        text = sticky.text()
        text_size = sticky.textSize()
        draw_bg = sticky.drawBackground()
        try:
            text_color = sticky.textColor()
            tc_rgb = text_color.rgb()
        except Exception:
            tc_rgb = (1.0, 1.0, 1.0)
        try:
            bg_color = sticky.color()
            bg_rgb = bg_color.rgb()
        except Exception:
            bg_rgb = (0.3, 0.3, 0.3)

        code_parts.append(f"""
# Sticky note {i}
hou_sticky_{i} = hou_parent.createStickyNote()
hou_sticky_{i}.setPosition(hou.Vector2({pos[0]}, {pos[1]}))
hou_sticky_{i}.setSize(hou.Vector2({size[0]}, {size[1]}))
hou_sticky_{i}.setText({repr(text)})
hou_sticky_{i}.setTextSize({text_size})
hou_sticky_{i}.setDrawBackground({draw_bg})
hou_sticky_{i}.setTextColor(hou.Color(({tc_rgb[0]}, {tc_rgb[1]}, {tc_rgb[2]})))
hou_sticky_{i}.setColor(hou.Color(({bg_rgb[0]}, {bg_rgb[1]}, {bg_rgb[2]})))""")

    # Join all code
    raw_code = "\n".join(code_parts)

    # Normalize paths:
    # 1. hou.node('/parent/path/child') → hou_parent.node('child')
    # 2. hou.node('/parent/path/child/grandchild') → hou_parent.node('child/grandchild')
    # 3. hou.node('/parent/path') → hou_parent
    # Must do child paths BEFORE parent path (longer match first)
    normalized = re.sub(
        r'''hou\.node\(['"]''' + parent_path_escaped + r'''/([^'"]+)['"]\)''',
        r"hou_parent.node('\1')",
        raw_code
    )
    # Then replace exact parent path references
    normalized = normalized.replace(
        f"hou.node('{parent_path}')",
        "hou_parent"
    )
    normalized = normalized.replace(
        f'hou.node("{parent_path}")',
        "hou_parent"
    )

    # Generate checksum from the code text
    code_bytes = normalized.encode('utf-8')
    checksum = hashlib.sha256(code_bytes).hexdigest()

    return {
        "format": "sopdrop-v1",
        "context": context,
        "houdini_version": hou.applicationVersionString(),
        "metadata": {
            "node_count": all_node_count,
            "top_level_count": len(nodes),
            "node_types": list(set(node_types)),
            "node_names": node_names,
            "network_boxes": len(network_boxes),
            "sticky_notes": len(sticky_notes),
            "has_hda_dependencies": len(dependencies) > 0,
            "node_graph": node_graph,
        },
        "dependencies": dependencies,
        "code": normalized,
        "checksum": checksum,
    }


def _get_context(parent) -> str:
    """Get the Houdini context from a parent node."""
    try:
        category = parent.childTypeCategory().name().lower()
        # Normalize context names
        context_map = {
            'sop': 'sop',
            'object': 'obj',
            'vop': 'vop',
            'dop': 'dop',
            'cop2': 'cop',
            'top': 'top',
            'lop': 'lop',
            'chop': 'chop',
            'shop': 'shop',
            'rop': 'out',
            'driver': 'out',
        }
        return context_map.get(category, category)
    except Exception:
        return 'unknown'


def _detect_hda_dependencies(nodes) -> List[Dict[str, str]]:
    """
    Detect custom HDA dependencies in the selected nodes.

    Returns list of HDAs that are not built-in Houdini assets.
    """
    import hou

    dependencies = []
    seen = set()

    def check_node(node):
        try:
            definition = node.type().definition()
            if definition:
                lib_path = definition.libraryFilePath()
                type_name = node.type().name()

                if type_name not in seen and not _is_builtin_hda(lib_path):
                    seen.add(type_name)
                    dependencies.append({
                        "name": type_name,
                        "library": lib_path,
                        "category": node.type().category().name(),
                    })
        except Exception:
            pass

        # Check children recursively
        for child in node.allSubChildren():
            check_node(child)

    for node in nodes:
        check_node(node)

    return dependencies


def _capture_node_graph(nodes, recursive=True) -> Dict[str, Any]:
    """
    Capture node graph data including positions, connections, shapes, and icons.

    This data enables web-based visualization of the node network
    that closely matches how it appears in Houdini.

    Args:
        nodes: List of nodes to capture
        recursive: If True, also capture children of container nodes (subnets, etc.)

    Returns:
        Dictionary mapping node names to their graph data:
        {
            "nodeName": {
                "type": "attribwrangle",
                "x": 3.5,
                "y": -2.0,
                "inputs": ["input1", "input2"],
                "outputs": ["output1"],
                "color": [1.0, 0.5, 0.0],  # Optional node color
                "flags": {"display": true, "render": false},
                "shape": "rect",  # Houdini node shape
                "icon": "SOP_attribwrangle",  # Icon name for lookup
                "children": {...}  # Nested node_graph for container nodes
            }
        }
    """
    import hou

    node_graph = {}
    node_set = set(nodes)  # For quick lookup of selected nodes

    for node in nodes:
        try:
            pos = node.position()

            # Get input connections (only from other selected nodes)
            inputs = []
            for i, input_node in enumerate(node.inputs()):
                if input_node and input_node in node_set:
                    inputs.append(input_node.name())

            # Get output connections (only to other selected nodes)
            outputs = []
            for output_conn in node.outputConnections():
                output_node = output_conn.outputNode()
                if output_node and output_node in node_set:
                    if output_node.name() not in outputs:
                        outputs.append(output_node.name())

            # Get node color if set
            color = None
            try:
                node_color = node.color()
                # Only include if not default gray
                if node_color != hou.Color((0.8, 0.8, 0.8)):
                    color = [node_color.rgb()[0], node_color.rgb()[1], node_color.rgb()[2]]
            except Exception:
                pass

            # Get node flags
            flags = {}
            try:
                if hasattr(node, 'isDisplayFlagSet'):
                    flags['display'] = node.isDisplayFlagSet()
                if hasattr(node, 'isRenderFlagSet'):
                    flags['render'] = node.isRenderFlagSet()
                if hasattr(node, 'isBypassed'):
                    flags['bypass'] = node.isBypassed()
            except Exception:
                pass

            # Get node shape
            shape = None
            try:
                # First check for user-defined shape override
                user_shape = node.userData("nodeshape")
                if user_shape:
                    shape = user_shape
                else:
                    # Fall back to the default shape for this node type
                    shape = node.type().defaultShape()
            except Exception:
                pass

            # Get icon name (format: CONTEXT_nodetype, e.g., SOP_attribwrangle)
            icon = None
            try:
                icon = node.type().icon()
            except Exception:
                pass

            node_graph[node.name()] = {
                "type": node.type().name(),
                "x": pos.x(),
                "y": pos.y(),
                "inputs": inputs,
                "outputs": outputs,
            }

            # Only include optional fields if they have values
            if color:
                node_graph[node.name()]["color"] = color
            if flags:
                node_graph[node.name()]["flags"] = flags
            if shape:
                node_graph[node.name()]["shape"] = shape
            if icon:
                node_graph[node.name()]["icon"] = icon

            # Recursively capture children for container nodes (subnets, geo, etc.)
            if recursive:
                try:
                    children = list(node.children())
                    if children:
                        # Get the context of the children
                        child_context = None
                        try:
                            child_context = node.childTypeCategory().name().lower()
                        except Exception:
                            pass

                        # Recursively capture children
                        children_graph = _capture_node_graph(children, recursive=True)
                        if children_graph:
                            node_graph[node.name()]["children"] = children_graph
                            node_graph[node.name()]["childContext"] = child_context
                except Exception:
                    # Node doesn't support children, skip
                    pass

        except Exception as e:
            # Skip nodes that fail, but continue with others
            print(f"Warning: Could not capture graph data for {node.name()}: {e}")
            continue

    return node_graph


def _is_builtin_hda(lib_path: str) -> bool:
    """Check if an HDA library path is a built-in Houdini asset."""
    import hou

    if not lib_path:
        return True

    # Get Houdini installation path
    hfs = hou.getenv("HFS", "")

    # Built-in HDAs are in $HFS or standard Houdini paths
    builtin_indicators = [
        hfs,
        "/opt/hfs",
        "/Applications/Houdini",
        "C:/Program Files/Side Effects",
        "SideFX",
    ]

    lib_lower = lib_path.lower()
    for indicator in builtin_indicators:
        if indicator and indicator.lower() in lib_lower:
            return True

    return False


# Public alias for HDA detection
def detect_hda_dependencies(nodes) -> List[Dict[str, str]]:
    """Public wrapper for HDA dependency detection."""
    return _detect_hda_dependencies(nodes)


def detect_publishable_hda(nodes) -> Optional[Dict[str, Any]]:
    """
    Check if the selected nodes represent a publishable HDA.

    Returns HDA info if:
    - Single node is selected
    - That node's type is defined in a custom (non-builtin) HDA

    Returns:
        Dict with HDA info, or None if not a publishable HDA
    """
    import hou

    if len(nodes) != 1:
        return None

    node = nodes[0]

    try:
        definition = node.type().definition()
        if not definition:
            return None

        lib_path = definition.libraryFilePath()
        if not lib_path or _is_builtin_hda(lib_path):
            return None

        # It's a custom HDA - gather info
        node_type = node.type()

        # Safely get the type label (might have non-UTF-8 chars)
        try:
            type_label = node_type.description()
            # Ensure it's valid UTF-8
            if isinstance(type_label, bytes):
                type_label = type_label.decode('utf-8', errors='replace')
            else:
                # Re-encode to clean up any bad characters
                type_label = type_label.encode('utf-8', errors='replace').decode('utf-8')
        except Exception:
            type_label = node_type.name()

        return {
            'node': node,
            'type_name': node_type.name(),
            'type_label': type_label,
            'library_path': lib_path,
            'definition': definition,
            'category': node_type.category().name(),
            'version': definition.version() if hasattr(definition, 'version') else None,
            'icon': node_type.icon(),
        }
    except Exception as e:
        print(f"[Sopdrop] HDA detection error: {e}")
        return None


def export_hda(hda_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Export an HDA for publishing.

    Reads the .hda file and packages it for upload.

    Args:
        hda_info: Dict from detect_publishable_hda()

    Returns:
        Package dict ready for upload
    """
    import hou
    import os

    lib_path = hda_info['library_path']
    definition = hda_info['definition']

    if not os.path.exists(lib_path):
        raise ValueError(f"HDA file not found: {lib_path}")

    # Read the HDA file
    with open(lib_path, 'rb') as f:
        binary_data = f.read()

    # Encode as base64 for transport
    encoded_data = base64.b64encode(binary_data).decode('ascii')

    # Generate checksum
    checksum = hashlib.sha256(binary_data).hexdigest()

    # Get context from the HDA's category
    category = hda_info['category'].lower()
    context_map = {
        'sop': 'sop',
        'object': 'obj',
        'vop': 'vop',
        'dop': 'dop',
        'cop2': 'cop',
        'top': 'top',
        'lop': 'lop',
        'chop': 'chop',
        'shop': 'shop',
        'rop': 'out',
        'driver': 'out',
    }
    context = context_map.get(category, category)

    # Get additional info from the definition
    help_text = None
    try:
        help_text = definition.embeddedHelp()
    except:
        pass

    # Check for embedded icon
    icon_data = None
    try:
        icon_name = definition.icon()
        # If it's an embedded icon, it would be stored in the HDA
        # For now we just record the icon name
    except:
        pass

    # Count sections/contents
    sections = []
    try:
        for section_name in definition.sections().keys():
            sections.append(section_name)
    except:
        pass

    package = {
        "format": "sopdrop-hda-v1",
        "asset_type": "hda",
        "context": context,
        "houdini_version": hou.applicationVersionString(),
        "metadata": {
            "type_name": hda_info['type_name'],
            "type_label": hda_info['type_label'],
            "hda_version": hda_info.get('version'),
            "icon": hda_info.get('icon'),
            "has_help": bool(help_text),
            "sections": sections,
            "file_name": os.path.basename(lib_path),
            "file_size": len(binary_data),
        },
        "data": encoded_data,
        "checksum": checksum,
    }

    return package


def preview_export(items=None) -> None:
    """
    Preview what would be exported without actually publishing.

    Args:
        items: Items to preview (default: selected items)
    """
    import hou

    if items is None:
        pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if pane:
            items = list(pane.pwd().selectedItems())

    if not items:
        print("No items to preview")
        return

    # Separate by type
    nodes = [i for i in items if isinstance(i, hou.Node)]
    netboxes = [i for i in items if isinstance(i, hou.NetworkBox)]
    stickies = [i for i in items if isinstance(i, hou.StickyNote)]

    print("\n=== Export Preview ===")
    print(f"Nodes: {len(nodes)}")
    for node in nodes:
        print(f"  - {node.name()} ({node.type().name()})")
        # Count children
        children = node.allSubChildren()
        if children:
            print(f"    + {len(children)} children")

    if netboxes:
        print(f"\nNetwork Boxes: {len(netboxes)}")
        for nb in netboxes:
            print(f"  - {nb.name()}")

    if stickies:
        print(f"\nSticky Notes: {len(stickies)}")

    # Check for HDA dependencies
    deps = _detect_hda_dependencies(nodes)
    if deps:
        print(f"\nHDA Dependencies: {len(deps)}")
        for dep in deps:
            print(f"  - {dep['name']} ({dep['category']})")

    # Get context
    if nodes:
        context = _get_context(nodes[0].parent())
        print(f"\nContext: {context}")

    print("=" * 25)


# Legacy export function for backwards compatibility
def export_network(items, **kwargs) -> Dict[str, Any]:
    """Legacy wrapper - use export_items() instead."""
    return export_items(items)
