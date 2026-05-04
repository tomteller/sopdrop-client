"""
Import module for Sopdrop.

Handles importing .sopdrop packages into Houdini.
Uses Houdini's native loadItemsFromFile() for reliable deserialization.
"""

import base64
import hashlib
import tempfile
import os
from typing import Dict, Any, List, Optional, Tuple


class ImportError(Exception):
    """Error during import."""
    pass


class ChecksumError(ImportError):
    """Checksum verification failed - data may be corrupted."""
    pass


class ContextMismatchError(ImportError):
    """Target context doesn't match package context."""
    pass


class MissingDependencyError(ImportError):
    """Missing required HDA dependency."""
    pass


class _NullParm:
    """Silently absorbs parameter operations for placeholder nodes."""

    def set(self, *args, **kwargs):
        pass

    def setExpression(self, *args, **kwargs):
        pass

    def setKeyframe(self, *args, **kwargs):
        pass

    def deleteAllKeyframes(self, *args, **kwargs):
        pass

    def revertToDefaults(self, *args, **kwargs):
        pass

    def lock(self, *args, **kwargs):
        pass

    def setAutoscope(self, *args, **kwargs):
        pass

    def setScope(self, *args, **kwargs):
        pass

    def setPending(self, *args, **kwargs):
        pass

    def pressButton(self, *args, **kwargs):
        pass

    def eval(self):
        return 0

    def evalAsString(self):
        return ""

    def unexpandedString(self):
        return ""

    def rawValue(self):
        return 0

    def __bool__(self):
        return True


class _NullParmTuple:
    """Silently absorbs parm tuple operations for placeholder nodes."""

    def set(self, *args, **kwargs):
        pass

    def setExpression(self, *args, **kwargs):
        pass

    def setKeyframe(self, *args, **kwargs):
        pass

    def deleteAllKeyframes(self, *args, **kwargs):
        pass

    def revertToDefaults(self, *args, **kwargs):
        pass

    def lock(self, *args, **kwargs):
        pass

    def setAutoscope(self, *args, **kwargs):
        pass

    def setScope(self, *args, **kwargs):
        pass

    def eval(self):
        return (0,)

    def __getitem__(self, index):
        return _NullParm()

    def __len__(self):
        return 1

    def __bool__(self):
        return True


class _PlaceholderNode:
    """Wraps a subnet standing in for a missing HDA.

    Intercepts parm()/parmTuple() to return _NullParm when the parameter
    doesn't exist on the subnet. Blocks setColor() to preserve the red
    warning color. Delegates everything else to the real subnet.
    """

    def __init__(self, subnet, type_name):
        object.__setattr__(self, '_subnet', subnet)
        object.__setattr__(self, '_type_name', type_name)

    def parm(self, name):
        real = self._subnet.parm(name)
        if real is not None:
            return real
        return _NullParm()

    def parmTuple(self, name):
        real = self._subnet.parmTuple(name)
        if real is not None:
            return real
        return _NullParmTuple()

    def setColor(self, *args, **kwargs):
        # Block — keep the red warning color
        pass

    def setUserData(self, key, value):
        self._subnet.setUserData(key, value)

    def __getattr__(self, name):
        return getattr(self._subnet, name)

    def __repr__(self):
        return f"<PlaceholderNode for '{self._type_name}': {self._subnet.path()}>"


class _PlaceholderParent:
    """Wraps the target node, intercepting createNode() for missing HDA types.

    When a missing type is encountered, creates a red subnet as a placeholder
    and returns a _PlaceholderNode wrapper. All other methods delegate to the
    real parent node.
    """

    def __init__(self, real_parent, missing_types):
        import hou as _hou
        object.__setattr__(self, '_real_parent', real_parent)
        object.__setattr__(self, '_missing_types', set(missing_types))
        object.__setattr__(self, '_hou', _hou)
        object.__setattr__(self, '_placeholders', [])

    def createNode(self, type_name, node_name=None, *args, **kwargs):
        hou = self._hou

        # Check if this type is one of the missing ones
        if type_name in self._missing_types:
            # Create a subnet as placeholder
            try:
                if node_name:
                    subnet = self._real_parent.createNode('subnet', node_name, *args, **kwargs)
                else:
                    subnet = self._real_parent.createNode('subnet', *args, **kwargs)
            except Exception:
                # If node_name conflicts, let Houdini pick a name
                subnet = self._real_parent.createNode('subnet', *args, **kwargs)

            # Mark it as a placeholder
            subnet.setColor(hou.Color(0.9, 0.15, 0.15))
            subnet.setComment(f"Missing HDA: {type_name}")
            subnet.setGenericFlag(hou.nodeFlag.DisplayComment, True)

            wrapper = _PlaceholderNode(subnet, type_name)
            self._placeholders.append(wrapper)
            return wrapper

        # Not a missing type — create normally
        if node_name:
            return self._real_parent.createNode(type_name, node_name, *args, **kwargs)
        return self._real_parent.createNode(type_name, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real_parent, name)


class _PlaceholderNamespace(dict):
    """Custom exec namespace that keeps hou_parent wrapped through asCode navigation.

    Old-format asCode output (flat, no function wrapping) navigates into nodes
    by reassigning hou_parent:

        hou_node = hou_parent.createNode(...)
        hou_parent = hou_node          # navigate in
        hou_parent = hou_node.parent() # navigate back out

    These reassignments lose the _PlaceholderParent wrapper, so subsequent
    createNode calls for missing types hit the real Houdini API and fail.

    This namespace intercepts every assignment to 'hou_parent' and re-wraps
    the value with _PlaceholderParent so placeholder interception stays active
    at every nesting level.
    """

    def __init__(self, initial, missing_types):
        super().__init__(initial)
        self._missing_types = set(missing_types)

    def __setitem__(self, key, value):
        if key == 'hou_parent' and value is not None:
            if isinstance(value, _PlaceholderParent):
                pass  # Already wrapped
            elif isinstance(value, _PlaceholderNode):
                # Unwrap PlaceholderNode, wrap the underlying subnet
                value = _PlaceholderParent(value._subnet, self._missing_types)
            else:
                # Wrap regular Houdini node
                value = _PlaceholderParent(value, self._missing_types)
        super().__setitem__(key, value)


def import_items(
    package: Dict[str, Any],
    target_node=None,
    position: Optional[Tuple[float, float]] = None,
    allow_placeholders: bool = False,
) -> List:
    """
    Import a .sopdrop package into Houdini.

    Uses Houdini's native loadItemsFromFile() for reliable deserialization.

    Args:
        package: The .sopdrop package dictionary
        target_node: Target parent node (default: current network)
        position: Position to place nodes at (default: cursor or center)
        allow_placeholders: If True, missing HDA deps are tolerated instead
            of raising MissingDependencyError. V1: creates red placeholder
            subnets. V2: lets loadItemsFromFile() proceed (Houdini creates
            native "unknown operator" nodes for missing types).

    Returns:
        List of created items
    """
    import hou

    # Validate package format
    fmt = package.get("format", "")
    with hou.undos.group("Sopdrop Paste"):
        if fmt == "sopdrop-v1" or fmt == "chopsop-v1":
            # Legacy format - use old code-based import
            return _import_v1(package, target_node, position, allow_placeholders)
        elif fmt.startswith("sopdrop-v") or fmt.startswith("chopsop-v"):
            # v2+ uses binary format (support old "chopsop" name for backwards compat)
            return _import_v2(package, target_node, position, allow_placeholders)
        else:
            raise ImportError(f"Unknown package format: {fmt}")


def _import_v2(
    package: Dict[str, Any],
    target_node=None,
    position: Optional[Tuple[float, float]] = None,
    allow_placeholders: bool = False,
) -> List:
    """Import v2 format (binary/cpio based)."""
    import hou

    # Get target node
    if target_node is None:
        pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if not pane:
            raise ImportError("No network editor found")
        target_node = pane.pwd()

    # Validate context
    target_context = _get_context(target_node)
    package_context = package.get("context", "unknown")

    if target_context != "unknown" and package_context != "unknown":
        if target_context != package_context:
            raise ContextMismatchError(
                f"Package is for {package_context.upper()} context, "
                f"but target is {target_context.upper()}. "
                f"Navigate to a {package_context.upper()} network and try again."
            )

    # Check dependencies
    dependencies = package.get("dependencies", [])
    if dependencies:
        missing = _check_missing_hdas(dependencies)
        if missing:
            if allow_placeholders:
                # Let loadItemsFromFile() proceed — Houdini creates native
                # "unknown operator" nodes for missing types (red error nodes).
                names = [d.get('name', '?') for d in missing]
                print(f"[Sopdrop] Proceeding with {len(missing)} missing HDA(s): {', '.join(names)}")
                print("[Sopdrop] Missing types will appear as error nodes in the network")
            else:
                raise MissingDependencyError(_format_missing_deps_error(missing, v2=True))

    # Get the binary data
    encoded_data = package.get("data")
    if not encoded_data:
        raise ImportError("Package contains no data")

    # Guard against excessively large packages that could OOM Houdini.
    # 500 MB decoded (667 MB base64-encoded) is a generous upper bound.
    MAX_ENCODED_SIZE = 667 * 1024 * 1024
    if len(encoded_data) > MAX_ENCODED_SIZE:
        raise ImportError(
            f"Package is too large ({len(encoded_data) // (1024*1024)} MB encoded). "
            f"Maximum supported size is ~500 MB."
        )

    # Decode from base64
    try:
        binary_data = base64.b64decode(encoded_data)
    except Exception as e:
        raise ImportError(f"Failed to decode package data: {e}")

    # Verify checksum
    expected_checksum = package.get("checksum")
    if expected_checksum:
        actual_checksum = hashlib.sha256(binary_data).hexdigest()
        if actual_checksum != expected_checksum:
            raise ChecksumError(
                "Package checksum verification failed. "
                "The data may be corrupted or tampered with."
            )

    # Check Houdini version (warn only)
    package_version = package.get("houdini_version", "unknown")
    current_version = hou.applicationVersionString()
    if package_version != "unknown" and package_version != current_version:
        print(f"Note: Package was created in Houdini {package_version}, "
              f"you are using {current_version}.")

    # Write binary data to temp file
    # Note: We must close the file before Houdini can read it
    fd, temp_path = tempfile.mkstemp(suffix='.cpio')
    try:
        os.write(fd, binary_data)
        os.fsync(fd)
    finally:
        os.close(fd)

    try:
        # Debug: print file size
        file_size = os.path.getsize(temp_path)
        print(f"[Sopdrop] Loading {file_size} bytes from temp file...")

        # Check if this package came from a container HDA (e.g. SOP Create).
        # If so, create the container first and load children into it.
        container_hda = (package.get("metadata") or {}).get("container_hda")
        load_target = target_node
        container_node = None

        if container_hda:
            type_name = container_hda.get("type_name")
            if type_name:
                try:
                    container_node = target_node.createNode(type_name)
                    is_hda = (container_node is not None
                              and container_node.type().definition() is not None)
                    if container_node and container_node.isSubNetwork() and not is_hda:
                        # Plain subnet container — its default children
                        # (subinput/suboutput) aren't locked, so we wipe
                        # them and load the saved items in their place.
                        for child in list(container_node.children()):
                            child.destroy()
                        load_target = container_node
                        print(f"[Sopdrop] Created container '{type_name}', loading children into it")
                    else:
                        # Custom HDA (SOP Create LOP, custom subnet HDAs, etc.)
                        # OR a non-subnet — we can't safely insert children
                        # into a locked HDA without either unlocking it
                        # (mutates the user's scene asset) or knowing the
                        # HDA's editable interior path (HDA-specific). Drop
                        # the wrapper and load the saved items flat into
                        # the parent network. Lossy but non-destructive.
                        if container_node:
                            container_node.destroy()
                            container_node = None
                        msg = ("HDA wrapper" if is_hda else
                               f"container '{type_name}' is not a subnet")
                        print(f"[Sopdrop] {msg} — loading children flat into parent. "
                              f"To preserve the wrapper, distribute the HDA itself.")
                except hou.OperationFailed:
                    container_node = None
                    print(f"[Sopdrop] Could not create container '{type_name}' (type not available), loading flat")

        # Track items before import to detect new ones
        items_before = set(target_node.allItems())

        # Load items using Houdini's native method
        result = load_target.loadItemsFromFile(temp_path)

        # Debug: see what we got back
        print(f"[Sopdrop] loadItemsFromFile returned: {type(result)}")

        # Get items from return value
        items_from_return = []
        if result is not None:
            if isinstance(result, (list, tuple)):
                items_from_return = [i for i in result if i is not None and hasattr(i, 'position')]
            elif hasattr(result, 'position'):
                items_from_return = [result]

        # Also detect new items by comparing before/after (fallback for older Houdini)
        items_after = set(target_node.allItems())
        new_items = list(items_after - items_before)

        # If we loaded into a container, the container is the only top-level item.
        # Just position it, select it, and lay out its children.
        if container_node is not None:
            if position:
                container_node.setPosition(hou.Vector2(position[0], position[1]))
            container_node.layoutChildren()
            target_node.setSelected(False, clear_all_selected=True)
            container_node.setSelected(True, clear_all_selected=False)
            print(f"[Sopdrop] Loaded into container '{container_node.type().name()}' with {len(container_node.children())} children")
            return [container_node]

        # Collect items to move and items to select
        # Strategy:
        # - Move network boxes (which moves their contents too)
        # - Move nodes NOT in network boxes
        # - Move sticky notes NOT in network boxes

        # Use networkBoxes() and stickyNotes() directly from target_node
        # to get the definitive list (avoids duplicates from set difference)
        all_netboxes_after = set(target_node.networkBoxes())
        all_stickies_after = set(target_node.stickyNotes())

        # Find which ones are new (weren't there before)
        new_netboxes = [nb for nb in all_netboxes_after if nb not in items_before]
        new_stickies = [s for s in all_stickies_after if s not in items_before]

        # For nodes, filter from new_items
        all_nodes = []
        for item in new_items:
            if isinstance(item, hou.Node):
                try:
                    if item.parent() == target_node:
                        all_nodes.append(item)
                except Exception:
                    pass

        # Network dots (connector waypoints) also need repositioning — they
        # aren't captured by networkBoxes()/stickyNotes() so pull from new_items.
        new_dots = [item for item in new_items if isinstance(item, hou.NetworkDot)]

        sticky_notes = new_stickies

        # Filter to only top-level network boxes for repositioning.
        # Nested boxes (box inside another box) move automatically when their
        # parent box moves — moving them independently double-moves them.
        new_netbox_set = set(new_netboxes)
        top_level_netboxes = []
        nested_netboxes = []
        for netbox in new_netboxes:
            try:
                parent_box = netbox.parentNetworkBox()
                if parent_box is not None and parent_box in new_netbox_set:
                    nested_netboxes.append(netbox)
                else:
                    top_level_netboxes.append(netbox)
            except Exception:
                top_level_netboxes.append(netbox)

        if nested_netboxes:
            print(f"[Sopdrop] {len(nested_netboxes)} nested netbox(es) will move with parent — skipping independent move")

        network_boxes = top_level_netboxes
        # all_netboxes used for selection includes everything
        all_netboxes = new_netboxes

        # Build set of nodes inside network boxes (we won't move these - the box moves them)
        nodes_in_boxes = set()
        for netbox in all_netboxes:
            try:
                for node in netbox.nodes():
                    nodes_in_boxes.add(node.path())
            except Exception as e:
                print(f"[Sopdrop] Error getting netbox nodes: {e}")

        # Filter to nodes NOT in network boxes
        nodes_to_move = [n for n in all_nodes if n.path() not in nodes_in_boxes]

        # For sticky notes, check if they have a parent network box
        # We need to do this BEFORE capturing positions, so we only capture loose stickies
        stickies_to_move = []
        stickies_in_boxes = []
        for sticky in sticky_notes:
            try:
                parent_box = sticky.parentNetworkBox()
                if parent_box is None:
                    # Not inside any network box - we need to move it
                    stickies_to_move.append(sticky)
                else:
                    stickies_in_boxes.append(sticky)
                    print(f"[Sopdrop] Sticky '{(sticky.text() or '')[:20]}...' is inside netbox, will move with box")
            except Exception as e:
                # If we can't check, assume it needs moving
                stickies_to_move.append(sticky)
                print(f"[Sopdrop] Could not check sticky parent box: {e}")

        # Capture network box sizes and positions BEFORE any operations
        netbox_data = {}
        for netbox in network_boxes:
            try:
                size = netbox.size()
                pos = netbox.position()
                comment = netbox.comment() or "unnamed"
                netbox_data[id(netbox)] = {
                    'size': (size[0], size[1]),
                    'pos': (pos[0], pos[1]),
                    'comment': comment
                }
                print(f"[Sopdrop] Captured netbox '{comment}': pos=({pos[0]:.2f}, {pos[1]:.2f}), size=({size[0]:.2f}, {size[1]:.2f})")
            except Exception as e:
                print(f"[Sopdrop] Error capturing netbox data: {e}")

        # Capture sticky note sizes and positions ONLY for stickies NOT in boxes
        # Stickies inside boxes will be moved by the box automatically
        sticky_data = {}
        for sticky in stickies_to_move:
            try:
                size = sticky.size()
                pos = sticky.position()
                text_preview = (sticky.text() or "")[:20]
                sticky_data[id(sticky)] = {
                    'size': (size[0], size[1]),
                    'pos': (pos[0], pos[1]),
                    'text': text_preview
                }
                print(f"[Sopdrop] Captured loose sticky '{text_preview}...': pos=({pos[0]:.2f}, {pos[1]:.2f}), size=({size[0]:.2f}, {size[1]:.2f})")
            except Exception as e:
                print(f"[Sopdrop] Error capturing sticky data: {e}")

        # For selection, we want all items (including nested boxes)
        all_top_level = all_nodes + all_netboxes + sticky_notes + new_dots

        print(f"[Sopdrop] Found {len(all_nodes)} nodes ({len(nodes_to_move)} outside boxes), {len(all_netboxes)} netboxes ({len(network_boxes)} top-level), {len(sticky_notes)} sticky notes ({len(stickies_to_move)} outside boxes), {len(new_dots)} dots")

        # If no items found, return empty
        if not all_top_level:
            print("[Sopdrop] Warning: No items detected after paste")
            return []

        # Reposition items to target location
        # - Nodes/stickies/dots outside boxes: move individually
        # - Network boxes: move the box (contents move with it)
        items_to_move = nodes_to_move + stickies_to_move + new_dots
        if position and (items_to_move or network_boxes):
            print(f"[Sopdrop] Repositioning {len(items_to_move)} loose items + {len(network_boxes)} netboxes to ({position[0]:.1f}, {position[1]:.1f})")
            _reposition_items(items_to_move, position, network_boxes, netbox_data, sticky_data)
        else:
            print("[Sopdrop] No position specified, items at original location")

        # Clear existing selection, then select all new items
        target_node.setSelected(False, clear_all_selected=True)
        for item in all_top_level:
            try:
                if hasattr(item, 'setSelected'):
                    item.setSelected(True, clear_all_selected=False)
            except Exception:
                pass

        return all_top_level

    except hou.OperationFailed as e:
        raise ImportError(f"Houdini failed to load items: {e}")
    finally:
        # Clean up temp file
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _patch_old_format_code(code):
    """Patch old-format v1 code to fix variable references after asCode navigation.

    Old exports concatenated asCode blocks with renamed variables (hou_node__N).
    After asCode's navigate-in/restore pattern, those variables end up pointing
    to the node's PARENT instead of the node itself, breaking subsequent
    connection and network-box code.

    Fix: inject re-establishment lines that look up each top-level node by name
    right before the manual wiring/netbox sections.

    New-format code (function-wrapped with _sdrop_create_) is returned as-is.
    """
    import re

    # New format uses function wrapping — no patching needed
    if '_sdrop_create_' in code:
        return code

    # Find all hou_node__N = hou_parent.createNode('type', 'name', ...) patterns.
    # Also handle bare hou_node (single-node exports without variable renaming).
    # Only take the FIRST createNode per variable — that's the top-level node.
    # Subsequent createNode calls on the same variable are children (recurse=True).
    pattern = (
        r"(hou_node(?:__\d+)?)\s*=\s*hou_parent\.createNode\("
        r"\s*['\"][^'\"]+['\"]\s*,\s*['\"]([^'\"]+)['\"]"
    )

    seen_vars = set()
    top_level = []
    for match in re.finditer(pattern, code):
        var_name = match.group(1)
        node_name = match.group(2)
        if var_name not in seen_vars:
            seen_vars.add(var_name)
            top_level.append((var_name, node_name))

    if not top_level:
        return code

    # Build re-establishment block: look up each top-level node by name
    lines = ["\n# Re-establish node references after asCode navigation"]
    for var_name, node_name in top_level:
        escaped = node_name.replace("'", "\\'")
        lines.append(f"{var_name} = hou_parent.node('{escaped}')")
    reestablish_block = "\n".join(lines) + "\n"

    # Find the earliest manual section marker (comments from export code)
    markers = [
        "\n# Wire connections",
        "\n# Rewire through dots",
        "\n# Network box:",
        "\n# Add items to network boxes",
        "\n# Sticky note ",
    ]

    insert_pos = len(code)
    for marker in markers:
        pos = code.find(marker)
        if pos != -1 and pos < insert_pos:
            insert_pos = pos

    # Fallback: look for manual setInput/addItem calls on hou_node__ variables
    if insert_pos == len(code):
        m = re.search(r'\nhou_node__\d+\.setInput\(', code)
        if m:
            insert_pos = m.start()

    if insert_pos == len(code):
        m = re.search(r'\nhou_netbox_\d+\s*=', code)
        if m:
            insert_pos = m.start()

    return code[:insert_pos] + reestablish_block + code[insert_pos:]


def _make_connections_resilient(code):
    """Wrap .setInput() and .addItem() calls in try/except to prevent cascade failures.

    asCode generates setInput calls that can fail if node types are missing
    or if variable references are stale. By wrapping these in try/except,
    node creation can complete even if some wiring fails.

    Uses multi-line try/except blocks (not inline) so compound statements
    like ``if cond: node.setInput(...)`` are handled correctly.
    """
    lines = code.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip lines already inside try blocks, comments, or except/finally
        if stripped.startswith(('try:', '#', 'except', 'finally')):
            result.append(line)
            i += 1
            continue

        # Wrap setInput / addItem calls in proper multi-line try/except
        if '.setInput(' in stripped or '.addItem(' in stripped:
            indent = line[:len(line) - len(line.lstrip())]
            result.append(f'{indent}try:')
            result.append(f'{indent}    {stripped}')
            result.append(f'{indent}except Exception:')
            result.append(f'{indent}    pass')
            i += 1
            continue

        result.append(line)
        i += 1

    return '\n'.join(result)


def _import_v1(
    package: Dict[str, Any],
    target_node=None,
    position: Optional[Tuple[float, float]] = None,
    allow_placeholders: bool = False,
) -> List:
    """Import v1 format (code-based, license-neutral).

    Used for packages exported from non-commercial Houdini (asCode output)
    and legacy packages. The code is plain Python with no license flags.

    Args:
        allow_placeholders: If True, missing HDA types become red placeholder
            subnets instead of raising MissingDependencyError.
    """
    import hou

    # Get target node
    if target_node is None:
        pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if not pane:
            raise ImportError("No network editor found")
        target_node = pane.pwd()

    # Validate context
    target_context = _get_context(target_node)
    package_context = package.get("context", "sop")

    if target_context != package_context:
        raise ContextMismatchError(
            f"Package is for {package_context} context, but target is {target_context}."
        )

    # Check dependencies
    use_placeholders = False
    missing_type_names = []
    dependencies = package.get("dependencies", [])
    if dependencies:
        missing = _check_missing_hdas(dependencies)
        if missing:
            if allow_placeholders:
                use_placeholders = True
                missing_type_names = [dep.get("name") for dep in missing if dep.get("name")]
                print(f"[Sopdrop] Using placeholders for {len(missing_type_names)} missing HDA(s): {', '.join(missing_type_names)}")
            else:
                raise MissingDependencyError(_format_missing_deps_error(missing))

    # Get the code
    code = package.get("code", "")
    if not code:
        raise ImportError("Package contains no code")

    # Verify checksum if present
    expected_checksum = package.get("checksum")
    if expected_checksum:
        actual_checksum = hashlib.sha256(code.encode('utf-8')).hexdigest()
        if actual_checksum != expected_checksum:
            raise ChecksumError(
                "Package checksum verification failed. "
                "The data may be corrupted or tampered with."
            )

    # Patch old-format packages to fix variable references.
    code = _patch_old_format_code(code)

    # Check if this package came from a container HDA (e.g. SOP Create).
    # If so, create the container first and load children into it.
    container_hda = (package.get("metadata") or {}).get("container_hda")
    container_node = None
    original_target = target_node

    if container_hda:
        type_name = container_hda.get("type_name")
        if type_name:
            try:
                container_node = target_node.createNode(type_name)
                is_hda = (container_node is not None
                          and container_node.type().definition() is not None)
                if container_node and container_node.isSubNetwork() and not is_hda:
                    # Plain subnet — safe to clear defaults and load.
                    for child in list(container_node.children()):
                        child.destroy()
                    target_node = container_node
                    print(f"[Sopdrop] Created container '{type_name}', loading children into it")
                else:
                    # Custom HDA or non-subnet — never unlock; fall back
                    # to loading flat in the parent. See _import_v2 for
                    # the canonical version of this rationale.
                    if container_node:
                        container_node.destroy()
                        container_node = None
                    msg = ("HDA wrapper" if is_hda else
                           f"container '{type_name}' is not a subnet")
                    print(f"[Sopdrop] {msg} — loading children flat into parent. "
                          f"To preserve the wrapper, distribute the HDA itself.")
            except hou.OperationFailed:
                container_node = None
                print(f"[Sopdrop] Could not create container '{type_name}' (type not available), loading flat")

    # Track items before import
    items_before = set(target_node.allItems())

    # Build the exec namespace — use proxy parent if placeholders are needed
    if use_placeholders:
        exec_parent = _PlaceholderParent(target_node, missing_type_names)
    else:
        exec_parent = target_node

    # Note: import_items() already wraps in hou.undos.group("Sopdrop Paste"),
    # so we do NOT create a second nested undo group here.
    package_meta = package.get("metadata", {})

    # If loading into a container, don't reposition children — they keep
    # their original layout inside the container. We position the container itself.
    inner_position = None if container_node else position
    result = _import_v1_inner(
        code, target_node, exec_parent, use_placeholders,
        missing_type_names, items_before, inner_position, package_meta,
    )

    # If we loaded into a container, return the container as the top-level item
    if container_node is not None:
        container_node.layoutChildren()
        if position:
            container_node.setPosition(hou.Vector2(position[0], position[1]))
        original_target.setSelected(False, clear_all_selected=True)
        container_node.setSelected(True, clear_all_selected=False)
        print(f"[Sopdrop] Loaded into container '{container_node.type().name()}' with {len(container_node.children())} children")
        return [container_node]

    return result


def _import_v1_inner(
    code, target_node, exec_parent, use_placeholders,
    missing_type_names, items_before, position, package_meta=None,
):
    """Inner import logic, runs inside an undo group."""
    import hou

    # Execute the code.
    # Strategy: try the full code first. If it fails, make connection/addItem
    # calls resilient (wrap in try/except) and retry so nodes are still created
    # even if some wiring fails.
    #
    # For placeholder mode, use _PlaceholderNamespace so that asCode's
    # hou_parent navigation (hou_parent = hou_node / hou_parent = hou_node.parent())
    # doesn't lose the _PlaceholderParent wrapper.
    if use_placeholders:
        namespace = _PlaceholderNamespace(
            {"hou": hou, "hou_parent": exec_parent},
            missing_type_names,
        )
    else:
        namespace = {"hou": hou, "hou_parent": exec_parent}
    exec_error = None
    try:
        exec(code, namespace)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        exc_type = type(e).__name__
        exec_error = f"{exc_type}: {e}"
        print(f"[Sopdrop] Code execution failed ({exc_type}): {e}")
        print(f"[Sopdrop] Full traceback:\n{tb}")

        # Show the failing line for debugging
        try:
            import re as _re
            line_match = _re.search(r'File "<string>", line (\d+)', tb) or _re.search(r'line (\d+)', tb)
            if line_match:
                line_num = int(line_match.group(1))
                code_lines = code.split('\n')
                start = max(0, line_num - 3)
                end = min(len(code_lines), line_num + 3)
                print("[Sopdrop] Code around error:")
                for j in range(start, end):
                    marker = ">>>" if j + 1 == line_num else "   "
                    print(f"  {marker} {j+1}: {code_lines[j]}")
        except Exception:
            pass

        # Retry with resilient connections — wrap setInput/addItem
        # calls in try/except so node creation can complete even if
        # some wiring fails.
        items_after_fail = set(target_node.allItems())
        partial_count = len(items_after_fail - items_before)

        # Decide whether to retry: if nothing was created, or if very
        # few items were created relative to expected (e.g., 3 out of 1600).
        expected = package_meta.get("node_count", 0) if package_meta else 0
        should_retry = (partial_count == 0
                        or (expected > 10 and partial_count < expected * 0.1))

        if should_retry:
            # Delete any partial items before retrying to avoid duplicates.
            # Only destroy top-level nodes (direct children of target_node) —
            # their children are destroyed automatically. Destroying in
            # arbitrary order (e.g., child after parent) can segfault.
            if partial_count > 0:
                print(f"[Sopdrop] Only {partial_count}/{expected} items created. Cleaning up partial result...")
                new_partial = items_after_fail - items_before
                top_level_to_destroy = []
                for item in new_partial:
                    try:
                        if isinstance(item, hou.Node) and item.parent() == target_node:
                            top_level_to_destroy.append(item)
                        elif isinstance(item, (hou.NetworkBox, hou.StickyNote, hou.NetworkDot)):
                            top_level_to_destroy.append(item)
                    except Exception:
                        pass
                for item in top_level_to_destroy:
                    try:
                        item.destroy()
                    except Exception:
                        pass

            print("[Sopdrop] Retrying with resilient connections...")
            resilient = _make_connections_resilient(code)
            if use_placeholders:
                retry_ns = _PlaceholderNamespace(
                    {"hou": hou, "hou_parent": exec_parent},
                    missing_type_names,
                )
            else:
                retry_ns = {"hou": hou, "hou_parent": exec_parent}
            try:
                exec(resilient, retry_ns)
                exec_error = None  # Retry succeeded
                print("[Sopdrop] Resilient retry succeeded")
            except Exception as e2:
                exc_type2 = type(e2).__name__
                print(f"[Sopdrop] Resilient retry also failed ({exc_type2}): {e2}")
                raise ImportError(
                    f"Failed to execute package code: {exc_type}: {e}"
                )
        else:
            # Enough items were created — continue with partial result
            print(f"[Sopdrop] Partial execution: {partial_count} items created before error.")
            print(f"[Sopdrop] Continuing with partial result. Some connections may be missing.")

    # Find newly created items
    items_after = set(target_node.allItems())
    new_items = list(items_after - items_before)

    # Separate items by type (same approach as v2 import)
    new_nodes = []
    new_netboxes = []
    new_stickies = []
    new_dots = []

    for item in new_items:
        if isinstance(item, hou.NetworkBox):
            new_netboxes.append(item)
        elif isinstance(item, hou.StickyNote):
            new_stickies.append(item)
        elif isinstance(item, hou.NetworkDot):
            new_dots.append(item)
        elif isinstance(item, hou.Node):
            if item.parent() == target_node:
                new_nodes.append(item)

    # Filter to only top-level network boxes for repositioning.
    # Nested boxes move automatically when their parent box moves.
    new_netbox_set = set(new_netboxes)
    top_level_netboxes = []
    for netbox in new_netboxes:
        try:
            parent_box = netbox.parentNetworkBox()
            if parent_box is not None and parent_box in new_netbox_set:
                continue
            top_level_netboxes.append(netbox)
        except Exception:
            top_level_netboxes.append(netbox)

    # Find nodes inside network boxes to avoid double-moving
    nodes_in_boxes = set()
    for netbox in new_netboxes:
        try:
            for n in netbox.nodes():
                nodes_in_boxes.add(n.path())
        except Exception:
            pass

    nodes_to_move = [n for n in new_nodes if n.path() not in nodes_in_boxes]

    # Capture netbox data for proper repositioning (top-level only)
    netbox_data = {}
    for netbox in top_level_netboxes:
        try:
            pos = netbox.position()
            size = netbox.size()
            netbox_data[id(netbox)] = {
                'pos': (pos[0], pos[1]),
                'size': (size[0], size[1]),
                'comment': netbox.comment() or 'unnamed',
            }
        except Exception:
            pass

    # Capture sticky data and separate loose stickies from box-contained ones
    sticky_data = {}
    stickies_to_move = []
    for sticky in new_stickies:
        try:
            parent_box = sticky.parentNetworkBox()
            if parent_box is None:
                stickies_to_move.append(sticky)
                pos = sticky.position()
                size = sticky.size()
                sticky_data[id(sticky)] = {
                    'pos': (pos[0], pos[1]),
                    'size': (size[0], size[1]),
                    'text': (sticky.text() or '')[:20],
                }
        except Exception:
            stickies_to_move.append(sticky)

    # Reposition: move loose nodes + loose stickies + dots individually,
    # move top-level network boxes separately (which moves their contents too)
    items_to_move = nodes_to_move + stickies_to_move + new_dots
    if position and (items_to_move or top_level_netboxes):
        _reposition_items(items_to_move, position, top_level_netboxes, netbox_data, sticky_data)

    # All top-level items for selection
    all_top_level = new_nodes + new_netboxes + new_stickies + new_dots

    # Clear existing selection, then select the new items
    target_node.setSelected(False, clear_all_selected=True)
    for item in all_top_level:
        try:
            if hasattr(item, 'setSelected'):
                item.setSelected(True, clear_all_selected=False)
        except Exception:
            pass

    return all_top_level


def import_at_cursor(package: Dict[str, Any], allow_placeholders: bool = False) -> List:
    """
    Import a package at the current cursor/view position.

    The nodes will be centered on either:
    1. The cursor position (if hovering over network view)
    2. The center of the visible network area (fallback)

    Relative positions between nodes are preserved.

    Args:
        package: The .sopdrop package dictionary
        allow_placeholders: If True, missing HDA deps are tolerated instead
            of raising MissingDependencyError.

    Returns:
        List of created items
    """
    import hou

    pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    if not pane:
        raise ImportError("No network editor found")

    target_node = pane.pwd()

    # Try to get the best paste position
    position = None

    # Method 1: Try cursor position (where mouse is hovering)
    try:
        cursor_pos = pane.cursorPosition()
        # Check if cursor is within visible bounds (i.e., mouse is over the pane)
        bounds = pane.visibleBounds()
        if bounds.contains(cursor_pos):
            position = (cursor_pos[0], cursor_pos[1])
            print(f"Pasting at cursor: ({position[0]:.1f}, {position[1]:.1f})")
    except Exception:
        pass

    # Method 2: Fall back to center of visible area
    if position is None:
        try:
            bounds = pane.visibleBounds()
            center = bounds.center()
            position = (center[0], center[1])
            print(f"Pasting at view center: ({position[0]:.1f}, {position[1]:.1f})")
        except Exception:
            # Last resort - use origin offset
            position = (0, 0)
            print("Pasting at origin")

    return import_items(package, target_node, position, allow_placeholders=allow_placeholders)


def show_package_info(package: Dict[str, Any]) -> None:
    """Display information about a .sopdrop package."""
    print("\n=== Package Info ===")
    print(f"Format: {package.get('format', 'unknown')}")
    print(f"Context: {package.get('context', 'unknown')}")
    print(f"Houdini Version: {package.get('houdini_version', 'unknown')}")

    meta = package.get("metadata", {})
    print(f"\nNodes: {meta.get('node_count', 0)}")

    node_names = meta.get('node_names', [])
    for name in node_names[:10]:
        print(f"  - {name}")
    if len(node_names) > 10:
        print(f"  ... and {len(node_names) - 10} more")

    node_types = meta.get('node_types', [])
    print(f"\nNode Types: {', '.join(node_types)}")

    if meta.get('network_boxes'):
        print(f"Network Boxes: {meta['network_boxes']}")

    if meta.get('sticky_notes'):
        print(f"Sticky Notes: {meta['sticky_notes']}")

    print(f"\nHas HDA Dependencies: {meta.get('has_hda_dependencies', False)}")

    deps = package.get("dependencies", [])
    if deps:
        print(f"\nDependencies:")
        for dep in deps:
            print(f"  - {dep['name']}")

    # Show checksum for v2
    if package.get("checksum"):
        print(f"\nChecksum: {package['checksum'][:16]}...")

    print("=" * 25)


def _get_context(node) -> str:
    """Get the Houdini context from a node."""
    try:
        category = node.childTypeCategory().name().lower()
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


def _check_missing_hdas(dependencies: List[Dict]) -> List[Dict]:
    """Check which HDA dependencies are missing.

    Returns list of dicts for each missing dependency, preserving all
    original fields (name, category, label, operator_type, sopdrop_slug, etc.).
    """
    import hou

    missing = []

    for dep in dependencies:
        name = dep.get("name")
        category_name = dep.get("category", "Sop")
        if not name:
            continue

        found = False
        cat = None
        all_types = None
        try:
            categories = hou.nodeTypeCategories()
            cat = categories.get(category_name)
            if cat:
                # Look through all registered types in this category.
                # hou.nodeType() can miss namespaced or versioned HDAs,
                # so we check the full type map directly.
                all_types = cat.nodeTypes()
                if name in all_types:
                    found = True
                else:
                    # Handle version mismatches: an HDA registered as
                    # "ns::Foo::2.0" should match dep name "ns::Foo",
                    # and vice versa.
                    for registered_name in all_types:
                        base = registered_name.rsplit("::", 1)[0] if "::" in registered_name else registered_name
                        dep_base = name.rsplit("::", 1)[0] if "::" in name else name
                        if registered_name == name or base == name or registered_name == dep_base or base == dep_base:
                            found = True
                            break
        except Exception:
            pass

        if not found and cat and all_types is not None:
            # Debug: log close matches to help diagnose lookup failures
            name_lower = name.lower()
            close = [r for r in all_types if name_lower in r.lower()]
            if close:
                # Case or namespace variant exists — treat as found
                found = True
                print(f"[Sopdrop] HDA '{name}' matched via case-insensitive: {close[:3]}")
            else:
                print(f"[Sopdrop] HDA '{name}' (category '{category_name}') not found in {len(all_types)} registered types")

        if not found:
            missing.append(dep)

    return missing


def _format_missing_deps_error(missing: List[Dict], **kwargs) -> str:
    """Format a human-readable error message for missing HDA dependencies."""
    lines = [f"Missing {len(missing)} HDA dependenc{'y' if len(missing) == 1 else 'ies'}:"]
    for dep in missing:
        label = dep.get("label") or dep.get("name", "unknown")
        category = dep.get("category", "")
        slug = dep.get("sopdrop_slug")
        if slug:
            lines.append(f"  - {label} ({category}) -> sopdrop.install(\"{slug}\")")
        else:
            lines.append(f"  - {label} ({category})")
    lines.append("")
    lines.append("Install the missing HDAs and try again,")
    lines.append("or paste with allow_placeholders=True to load with error nodes.")
    return "\n".join(lines)


def _reposition_items(items, target_position: Tuple[float, float], network_boxes=None, netbox_data=None, sticky_data=None) -> None:
    """Reposition items so their bounding box CENTER is at target_position.

    Strategy:
    - Calculate offset from current center to target
    - Move all loose nodes/stickies by that offset
    - Move network boxes by that offset and restore their original size
    - Restore sticky note sizes after moving
    """
    import hou

    if not items and not network_boxes:
        print("[Sopdrop] _reposition_items: No items to reposition")
        return

    if network_boxes is None:
        network_boxes = []
    if netbox_data is None:
        netbox_data = {}
    if sticky_data is None:
        sticky_data = {}

    # Calculate current bounding box including network boxes
    # Use SAVED positions/sizes where available for accuracy
    min_x = float('inf')
    min_y = float('inf')
    max_x = float('-inf')
    max_y = float('-inf')

    # Include nodes and sticky notes in bounds
    for item in items:
        try:
            # For sticky notes, use saved data if available
            if isinstance(item, hou.StickyNote):
                data = sticky_data.get(id(item), {})
                saved_pos = data.get('pos')
                saved_size = data.get('size')

                if saved_pos and saved_size:
                    x, y = saved_pos
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x + saved_size[0])
                    max_y = max(max_y, y + saved_size[1])
                else:
                    # Fallback to current values
                    pos = item.position()
                    x, y = pos[0], pos[1]
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    try:
                        size = item.size()
                        max_x = max(max_x, x + size[0])
                        max_y = max(max_y, y + size[1])
                    except:
                        max_x = max(max_x, x + 3)
                        max_y = max(max_y, y + 1)
            elif isinstance(item, hou.NetworkDot):
                # Dots are point-sized connector waypoints
                pos = item.position()
                x, y = pos[0], pos[1]
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
            else:
                # Regular nodes
                pos = item.position()
                x, y = pos[0], pos[1]
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                # Nodes are roughly 2x1 units
                max_x = max(max_x, x + 2)
                max_y = max(max_y, y + 1)

        except Exception as e:
            print(f"[Sopdrop] Error getting position for {item}: {e}")

    # Include network boxes in bounds (use saved data for accurate sizes)
    for netbox in network_boxes:
        try:
            data = netbox_data.get(id(netbox), {})
            saved_pos = data.get('pos')
            saved_size = data.get('size')

            if saved_pos and saved_size:
                min_x = min(min_x, saved_pos[0])
                min_y = min(min_y, saved_pos[1])
                max_x = max(max_x, saved_pos[0] + saved_size[0])
                max_y = max(max_y, saved_pos[1] + saved_size[1])
            else:
                # Fallback to current values
                pos = netbox.position()
                size = netbox.size()
                min_x = min(min_x, pos[0])
                min_y = min(min_y, pos[1])
                max_x = max(max_x, pos[0] + size[0])
                max_y = max(max_y, pos[1] + size[1])
        except Exception as e:
            print(f"[Sopdrop] Error getting netbox bounds: {e}")

    # Check if we found valid positions
    if min_x == float('inf') or min_y == float('inf'):
        print("[Sopdrop] _reposition_items: Could not calculate bounding box")
        return

    # Calculate center of bounding box
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2

    # Calculate offset to move center to target
    offset_x = target_position[0] - center_x
    offset_y = target_position[1] - center_y

    print(f"[Sopdrop] Bounding box: ({min_x:.1f}, {min_y:.1f}) to ({max_x:.1f}, {max_y:.1f})")
    print(f"[Sopdrop] Center: ({center_x:.1f}, {center_y:.1f}) -> Target: ({target_position[0]:.1f}, {target_position[1]:.1f})")
    print(f"[Sopdrop] Offset: ({offset_x:.1f}, {offset_y:.1f})")

    offset_vec = hou.Vector2(offset_x, offset_y)

    # Move nodes and sticky notes
    moved_count = 0
    for item in items:
        try:
            if isinstance(item, hou.StickyNote):
                # For sticky notes, calculate new position from saved data
                data = sticky_data.get(id(item), {})
                saved_pos = data.get('pos')
                saved_size = data.get('size')

                if saved_pos:
                    # Set position directly using saved position + offset
                    new_x = saved_pos[0] + offset_x
                    new_y = saved_pos[1] + offset_y
                    item.setPosition(hou.Vector2(new_x, new_y))
                    # Restore size
                    if saved_size:
                        item.setSize(hou.Vector2(saved_size[0], saved_size[1]))
                    text_preview = data.get('text', '')
                    print(f"[Sopdrop] Moved sticky '{text_preview}...' to ({new_x:.1f}, {new_y:.1f}), size: {saved_size}")
                else:
                    # Fallback - just use move
                    item.move(offset_vec)
            else:
                # Regular nodes - just move
                item.move(offset_vec)
            moved_count += 1
        except Exception as e:
            print(f"[Sopdrop] Error moving {item}: {e}")

    # Move network boxes - use saved data from BEFORE any operations
    for netbox in network_boxes:
        try:
            # Get the data we saved at the very beginning
            data = netbox_data.get(id(netbox), {})
            saved_size = data.get('size')
            comment = data.get('comment', 'unnamed')

            old_pos = netbox.position()

            # Use move() to move both box and contents
            netbox.move(offset_vec)

            # Restore original size from our saved copy
            if saved_size:
                netbox.setSize(hou.Vector2(saved_size[0], saved_size[1]))

            new_pos = netbox.position()
            print(f"[Sopdrop] Moved netbox '{comment}' from ({old_pos[0]:.1f}, {old_pos[1]:.1f}) to ({new_pos[0]:.1f}, {new_pos[1]:.1f}), restored size: {saved_size}")
            moved_count += 1
        except Exception as e:
            print(f"[Sopdrop] Error moving netbox: {e}")

    print(f"[Sopdrop] Moved {moved_count} items total")


# Legacy function for backwards compatibility
def import_network(package, target_node=None, position=None, force=False, allow_placeholders=False):
    """Legacy wrapper - use import_items() instead."""
    return import_items(package, target_node, position, allow_placeholders=allow_placeholders)
