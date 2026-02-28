"""
Export module for Sopdrop.

Handles exporting Houdini nodes to the .sopdrop package format.
Uses Houdini's asCode() for inspectable, auditable, diffable serialization.
"""

import base64
import hashlib
import os
import re
from typing import List, Dict, Any, Optional


def export_items(items) -> Dict[str, Any]:
    """
    Export selected Houdini items as a .sopdrop package.

    Always uses asCode() to produce inspectable Python code.
    recurse=True ensures children inside subnets/containers are captured.

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
    network_dots = []

    for item in items:
        if isinstance(item, hou.Node):
            nodes.append(item)
        elif isinstance(item, hou.NetworkBox):
            network_boxes.append(item)
        elif isinstance(item, hou.StickyNote):
            sticky_notes.append(item)
        elif isinstance(item, hou.NetworkDot):
            network_dots.append(item)

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

    # Serialize via asCode
    parent_path = parent.path()
    parent_path_escaped = re.escape(parent_path)

    code_parts = []

    # Export each node via asCode, wrapped in a function to isolate scope.
    #
    # WHY FUNCTION WRAPPING IS NECESSARY:
    # asCode(recurse=True) generates code that mutates `hou_parent` and
    # `hou_node` as navigation variables — it does "hou_parent = hou_node"
    # to descend into children, then ".parent()" to restore. After the
    # restoration chain finishes, hou_node ends up pointing to the node's
    # PARENT (not the node itself), and hou_parent is back to original.
    #
    # When we concatenate multiple asCode blocks with shared variables,
    # this causes:
    # 1. hou_node__N points to WRONG node after asCode finishes
    # 2. Manual connection code uses wrong references → broken wires
    # 3. asCode's internal connection code can crash if name lookups fail
    #
    # By wrapping each node's asCode in a function:
    # - hou_parent modifications stay LOCAL (don't affect outer scope)
    # - No variable collisions between nodes (each has own scope)
    # - We capture the node reference right after createNode (before
    #   navigation mutates it) and return it
    # - asCode's internal connection code runs harmlessly in isolation

    for idx, node in enumerate(nodes):
        code = node.asCode(
            brief=True,
            recurse=True,
            save_box_membership=False,
            save_outgoing_wires=False,
        )

        # Strip asCode's inline connection block — we handle all wiring
        # explicitly in Pass 1 (direct) and Pass 2 (dot rewire).
        # The inline block starts with "# Code to establish connections for"
        # and sets redundant setInput calls that flood the undo history.
        conn_marker = '# Code to establish connections for '
        if conn_marker in code:
            code = code[:code.index(conn_marker)].rstrip() + '\n'

        # Inject a reference save right after the first createNode call.
        # This captures the top-level node before asCode's navigation
        # reassigns the variable to children/parent.
        create_marker = 'hou_node = hou_parent.createNode('
        if create_marker in code:
            pos = code.index(create_marker)
            newline_pos = code.index('\n', pos)
            code = (code[:newline_pos + 1]
                    + '_sdrop_result = hou_node\n'
                    + code[newline_pos + 1:])

        # Indent all lines for function body
        indented_lines = []
        for line in code.split('\n'):
            if line.strip():
                indented_lines.append('    ' + line)
            else:
                indented_lines.append('')
        indented = '\n'.join(indented_lines)

        func_name = f'_sdrop_create_{idx}'
        node_name_escaped = node.name().replace("'", "\\'")
        wrapped = (f'def {func_name}(hou_parent, hou):\n'
                   f'    _sdrop_result = None\n'
                   f'{indented}\n'
                   f'    return _sdrop_result\n')
        code_parts.append(wrapped)
        # Call the function; fall back to name lookup if _sdrop_result is None
        code_parts.append(f'_sdrop_node_{idx} = {func_name}(hou_parent, hou)')
        code_parts.append(
            f'if _sdrop_node_{idx} is None:\n'
            f'    _sdrop_node_{idx} = hou_parent.node(\'{node_name_escaped}\')'
        )

    # Build node name -> variable mapping for connection code.
    # Each function returns the created node, stored in _sdrop_node_N.
    node_name_to_var = {}
    for idx, node in enumerate(nodes):
        node_name_to_var[node.name()] = f'_sdrop_node_{idx}'

    # Build a lookup of which items belong to which network boxes.
    # We need this because network box item addition must happen AFTER
    # all items (nodes, stickies, dots) are created. Sticky notes and dots
    # get auto-generated names on creation, so we track them by variable
    # reference instead of by original name.
    sticky_var_by_obj = {}  # sticky note object -> variable name
    dot_var_by_obj = {}     # populated later during dot export

    # Export network boxes — creation only (item addition is deferred)
    netbox_items_deferred = []  # [(netbox_var, [(item_type, ref)])]
    for i, netbox in enumerate(network_boxes):
        pos = netbox.position()
        size = netbox.size()
        color = netbox.color()
        comment = netbox.comment()
        name = netbox.name()
        netbox_var = f'hou_netbox_{i}'
        code_parts.append(f"""
# Network box: {name}
{netbox_var} = hou_parent.createNetworkBox("{name}")
{netbox_var}.setPosition(hou.Vector2({pos[0]}, {pos[1]}))
{netbox_var}.setSize(hou.Vector2({size[0]}, {size[1]}))
{netbox_var}.setColor(hou.Color(({color.rgb()[0]}, {color.rgb()[1]}, {color.rgb()[2]})))
{netbox_var}.setComment({repr(comment)})""")

        # Record contained items for deferred addition
        contained = []
        for item in netbox.items():
            if isinstance(item, hou.Node):
                # Nodes keep their original names from asCode
                contained.append(('node', item.name(), item))
            elif isinstance(item, hou.StickyNote):
                contained.append(('sticky', None, item))  # ref filled in later
            elif isinstance(item, hou.NetworkDot):
                contained.append(('dot', None, item))      # ref filled in later
            elif isinstance(item, hou.NetworkBox):
                contained.append(('node', item.name(), item))  # nested boxes use name
        netbox_items_deferred.append((netbox_var, contained))

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

        var = f'hou_sticky_{i}'
        sticky_var_by_obj[sticky] = var

        code_parts.append(f"""
# Sticky note {i}
{var} = hou_parent.createStickyNote()
{var}.setPosition(hou.Vector2({pos[0]}, {pos[1]}))
{var}.setSize(hou.Vector2({size[0]}, {size[1]}))
{var}.setText({repr(text)})
{var}.setTextSize({text_size})
{var}.setDrawBackground({draw_bg})
{var}.setTextColor(hou.Color(({tc_rgb[0]}, {tc_rgb[1]}, {tc_rgb[2]})))
{var}.setColor(hou.Color(({bg_rgb[0]}, {bg_rgb[1]}, {bg_rgb[2]})))""")

    # Export network dots (wire reroute points)
    #
    # Dots are visual wire routing points. asCode() ignores them entirely,
    # creating direct node-to-node connections. We must:
    # 1. Create each dot and wire its input (handling dot chains correctly)
    # 2. Figure out which downstream node+input each dot feeds into
    # 3. Rewire those connections through the appropriate dot
    #
    # Challenges:
    # - Multiple dots can share the same upstream (one output splitting to two dots)
    # - Dots can chain through other dots (Dot1 → Dot2 → Node)
    # - The Houdini API doesn't expose what a dot outputs to (only inputItem())
    # - We use position-based heuristics to match dots to downstream connections

    selected_dot_set = set(network_dots)
    dot_vars = {}       # dot object -> Python variable name (for rewiring)
    dot_var_by_obj = {}  # also track for network box membership

    for i, dot in enumerate(network_dots):
        pos = dot.position()
        var = f'hou_dot_{i}'
        dot_vars[dot] = var
        dot_var_by_obj[dot] = var

        code_parts.append(f"""
# Network dot {i}
{var} = hou_parent.createNetworkDot()
{var}.setPosition(hou.Vector2({pos[0]}, {pos[1]}))""")

        # Wire the dot's input — handle chained dots vs node inputs
        try:
            input_item = dot.inputItem()
            if input_item:
                if input_item in selected_dot_set:
                    # Chained dot: reference the upstream dot's variable directly
                    upstream_var = dot_vars[input_item]
                    code_parts.append(f'{var}.setInput({upstream_var}, 0)')
                else:
                    # Input is a node: use its asCode variable reference
                    input_name = input_item.name()
                    input_out_idx = dot.inputItemOutputIndex()
                    node_var = node_name_to_var.get(input_name)
                    if node_var:
                        code_parts.append(f'{var}.setInput({node_var}, {input_out_idx})')
                    else:
                        code_parts.append(
                            f'try:\n'
                            f'    _up = hou_parent.item("{input_name}")\n'
                            f'    if _up: {var}.setInput(_up, {input_out_idx})\n'
                            f'except: pass'
                        )
        except Exception:
            pass

        try:
            if dot.isPinned():
                code_parts.append(f'{var}.setPinned(True)')
        except Exception:
            pass

    # Generate all connections between selected nodes.
    #
    # Strategy: two-pass approach.
    #   Pass 1: Wire ALL connections directly (node→node, using variable refs).
    #   Pass 2: For connections that should route through dots, overwrite with
    #           dot-routed connections. setInput() on the same input index just
    #           replaces the previous connection, so pass 2 cleanly overwrites pass 1.
    #
    # This avoids the previous bug where connections from a source+output that
    # had SOME dots would ALL get routed through dots, dropping direct connections
    # when there were more connections than dots.

    from collections import defaultdict
    selected_node_names = set(n.name() for n in nodes)

    # Collect ALL connections between selected nodes
    # NodeConnection API (from node.inputConnections()):
    #   conn.inputNode()  = upstream node (provides data into the wire)
    #   conn.inputIndex() = input connector index on the DOWNSTREAM node (this node)
    #   conn.outputNode() = downstream node (this node, receives data)
    #   conn.outputIndex()= output connector index on the UPSTREAM node
    all_connections = []  # (dn_name, dn_input, up_name, up_output, dn_x, dn_y)
    for node in nodes:
        try:
            node_pos = node.position()
            for conn in node.inputConnections():
                upstream = conn.inputNode()
                if upstream is None:
                    continue
                if upstream.name() not in selected_node_names:
                    continue
                downstream_input = conn.inputIndex()
                upstream_output = conn.outputIndex()
                all_connections.append((
                    node.name(), downstream_input,
                    upstream.name(), upstream_output,
                    node_pos[0], node_pos[1]
                ))
        except Exception:
            pass

    # Pass 1: Wire ALL connections directly
    code_parts.append("\n# Wire connections (direct)")
    for dn_name, dn_input, up_name, up_output, _, _ in all_connections:
        dn_var = node_name_to_var.get(dn_name)
        up_var = node_name_to_var.get(up_name)
        if dn_var and up_var:
            code_parts.append(
                f'try: {dn_var}.setInput({dn_input}, {up_var}, {up_output})\n'
                f'except Exception: pass'
            )

    # Pass 2: Overwrite dot-routed connections
    # Build dot routing info: which (source, output) pairs have terminal dots
    if network_dots:
        # Trace each dot back through any chain to find the source NODE
        dot_source = {}  # dot -> (source_node_name, source_output_idx)
        for dot in network_dots:
            try:
                out_idx = dot.inputItemOutputIndex()
                up = dot.inputItem()
                while up in selected_dot_set:
                    out_idx = up.inputItemOutputIndex()
                    up = up.inputItem()
                if up and isinstance(up, hou.Node):
                    dot_source[dot] = (up.name(), out_idx)
            except Exception:
                pass

        # Terminal dots: dots whose output goes to a node, not another dot
        has_dot_downstream = set()
        for dot in network_dots:
            try:
                up = dot.inputItem()
                if up in selected_dot_set:
                    has_dot_downstream.add(up)
            except Exception:
                pass
        terminal_dots = [d for d in network_dots if d not in has_dot_downstream]

        # Group terminal dots by their source
        rewire_candidates = defaultdict(list)  # (source_name, source_output) -> [(dot_var, x, y)]
        for dot in terminal_dots:
            source = dot_source.get(dot)
            if source:
                var = dot_vars[dot]
                pos = dot.position()
                rewire_candidates[source].append((var, pos[0], pos[1]))

        if rewire_candidates:
            code_parts.append("\n# Rewire through dots")

            # For each source+output, match dots to connections by position
            for source_key, dot_list in rewire_candidates.items():
                # Find connections from this source+output
                matching = [
                    (dn_name, dn_input, dn_x, dn_y)
                    for dn_name, dn_input, up_name, up_output, dn_x, dn_y
                    in all_connections
                    if (up_name, up_output) == source_key
                ]
                if not matching:
                    continue

                # Sort dots by X position, connections by downstream input index
                dots_sorted = sorted(dot_list, key=lambda c: c[1])
                conns_sorted = sorted(matching, key=lambda c: (c[0], c[1]))

                if len(conns_sorted) == 1 and len(dots_sorted) == 1:
                    # Simple 1:1 match
                    dn_name, dn_input, _, _ = conns_sorted[0]
                    dot_var = dots_sorted[0][0]
                    dn_var = node_name_to_var.get(dn_name)
                    if dn_var:
                        code_parts.append(
                            f'try: {dn_var}.setInput({dn_input}, {dot_var}, 0)\n'
                            f'except Exception: pass'
                        )

                elif len(conns_sorted) <= len(dots_sorted):
                    # Enough dots for all connections — match by position proximity
                    remaining_dots = list(dots_sorted)
                    for dn_name, dn_input, dn_x, dn_y in conns_sorted:
                        best_i = min(
                            range(len(remaining_dots)),
                            key=lambda j: (remaining_dots[j][1] - dn_x) ** 2
                                        + (remaining_dots[j][2] - dn_y) ** 2
                        )
                        dot_var = remaining_dots.pop(best_i)[0]
                        dn_var = node_name_to_var.get(dn_name)
                        if dn_var:
                            code_parts.append(
                                f'try: {dn_var}.setInput({dn_input}, {dot_var}, 0)\n'
                                f'except Exception: pass'
                            )

                else:
                    # More connections than dots — match dots to closest, rest stay direct
                    remaining_conns = list(conns_sorted)
                    for dot_var, dx, dy in dots_sorted:
                        if not remaining_conns:
                            break
                        best_i = min(
                            range(len(remaining_conns)),
                            key=lambda j: (remaining_conns[j][2] - dx) ** 2
                                        + (remaining_conns[j][3] - dy) ** 2
                        )
                        dn_name, dn_input, _, _ = remaining_conns.pop(best_i)
                        dn_var = node_name_to_var.get(dn_name)
                        if dn_var:
                            code_parts.append(
                                f'try: {dn_var}.setInput({dn_input}, {dot_var}, 0)\n'
                                f'except Exception: pass'
                            )
                    # Remaining connections keep their direct wiring from pass 1

    # Add items to network boxes (deferred until all items exist).
    # Use variable references for nodes (avoids name lookup failures if
    # asCode renamed a node to avoid conflicts). Sticky notes and dots
    # also use their variable references since they get auto-generated names.
    if netbox_items_deferred:
        code_parts.append("\n# Add items to network boxes")
        for netbox_var, contained in netbox_items_deferred:
            for item_type, ref, obj in contained:
                if item_type == 'node':
                    # Use asCode variable if available, fall back to name lookup
                    node_var = node_name_to_var.get(ref)
                    if node_var:
                        code_parts.append(f'{netbox_var}.addItem({node_var})')
                    else:
                        code_parts.append(
                            f'try:\n'
                            f'    _item = hou_parent.item("{ref}")\n'
                            f'    if _item: {netbox_var}.addItem(_item)\n'
                            f'except: pass'
                        )
                elif item_type == 'sticky':
                    var = sticky_var_by_obj.get(obj)
                    if var:
                        code_parts.append(
                            f'try:\n'
                            f'    {netbox_var}.addItem({var})\n'
                            f'except: pass'
                        )
                elif item_type == 'dot':
                    var = dot_var_by_obj.get(obj)
                    if var:
                        code_parts.append(
                            f'try:\n'
                            f'    {netbox_var}.addItem({var})\n'
                            f'except: pass'
                        )

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
            "network_dots": len(network_dots),
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
    Each entry includes: name, library, category, operator_type, version, label.
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
                    node_type = node.type()
                    category = node_type.category().name()

                    # Full namespaced type e.g. "Sop/com.artist::scatter::2.0"
                    operator_type = f"{category}/{type_name}"
                    try:
                        operator_type = node_type.nameWithCategory()
                    except Exception:
                        pass

                    # HDA version from definition
                    version = None
                    try:
                        v = definition.version()
                        if v:
                            version = v
                    except Exception:
                        pass

                    # Human-readable label
                    label = None
                    try:
                        desc = node_type.description()
                        if desc:
                            label = desc
                    except Exception:
                        pass

                    dependencies.append({
                        "name": type_name,
                        "operator_type": operator_type,
                        "label": label,
                        "category": category,
                        "library": lib_path,
                        "version": version,
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
    dots = [i for i in items if isinstance(i, hou.NetworkDot)]

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

    if dots:
        print(f"\nNetwork Dots: {len(dots)}")

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
