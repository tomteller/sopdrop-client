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


def import_items(
    package: Dict[str, Any],
    target_node=None,
    position: Optional[Tuple[float, float]] = None,
) -> List:
    """
    Import a .sopdrop package into Houdini.

    Uses Houdini's native loadItemsFromFile() for reliable deserialization.

    Args:
        package: The .sopdrop package dictionary
        target_node: Target parent node (default: current network)
        position: Position to place nodes at (default: cursor or center)

    Returns:
        List of created items
    """
    import hou

    # Validate package format
    fmt = package.get("format", "")
    if fmt == "sopdrop-v1" or fmt == "chopsop-v1":
        # Legacy format - use old code-based import
        return _import_v1(package, target_node, position)
    elif fmt.startswith("sopdrop-v") or fmt.startswith("chopsop-v"):
        # v2+ uses binary format (support old "chopsop" name for backwards compat)
        return _import_v2(package, target_node, position)
    else:
        raise ImportError(f"Unknown package format: {fmt}")


def _import_v2(
    package: Dict[str, Any],
    target_node=None,
    position: Optional[Tuple[float, float]] = None,
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
            raise MissingDependencyError(
                f"Missing HDA dependencies: {', '.join(missing)}. "
                f"Install these HDAs first."
            )

    # Get the binary data
    encoded_data = package.get("data")
    if not encoded_data:
        raise ImportError("Package contains no data")

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
    temp_path = tempfile.mktemp(suffix='.cpio')
    with open(temp_path, 'wb') as f:
        f.write(binary_data)
        f.flush()
        os.fsync(f.fileno())  # Ensure data is written to disk

    try:
        # Debug: print file size
        file_size = os.path.getsize(temp_path)
        print(f"[Sopdrop] Loading {file_size} bytes from temp file...")

        # Track items before import to detect new ones
        items_before = set(target_node.allItems())

        # Load items using Houdini's native method
        result = target_node.loadItemsFromFile(temp_path)

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
                if item.parent() == target_node:
                    all_nodes.append(item)

        network_boxes = new_netboxes
        sticky_notes = new_stickies

        # Build set of nodes inside network boxes (we won't move these - the box moves them)
        nodes_in_boxes = set()
        for netbox in network_boxes:
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

        # For selection, we want all top-level items
        all_top_level = all_nodes + network_boxes + sticky_notes

        print(f"[Sopdrop] Found {len(all_nodes)} nodes ({len(nodes_to_move)} outside boxes), {len(network_boxes)} netboxes (unique), {len(sticky_notes)} sticky notes ({len(stickies_to_move)} outside boxes)")

        # If no items found, return empty
        if not all_top_level:
            print("[Sopdrop] Warning: No items detected after paste")
            return []

        # Reposition items to target location
        # - Nodes/stickies outside boxes: move individually
        # - Network boxes: move the box (contents move with it)
        items_to_move = nodes_to_move + stickies_to_move
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


def _import_v1(
    package: Dict[str, Any],
    target_node=None,
    position: Optional[Tuple[float, float]] = None,
) -> List:
    """Import v1 format (code-based, license-neutral).

    Used for packages exported from non-commercial Houdini (asCode output)
    and legacy packages. The code is plain Python with no license flags.
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

    # Track items before import
    items_before = set(target_node.allItems())

    # Execute the code
    try:
        exec(code, {
            "hou": hou,
            "hou_parent": target_node,
        })
    except Exception as e:
        raise ImportError(f"Failed to execute package code: {e}")

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

    # Find nodes inside network boxes to avoid double-moving
    nodes_in_boxes = set()
    for netbox in new_netboxes:
        try:
            for n in netbox.nodes():
                nodes_in_boxes.add(n.path())
        except Exception:
            pass

    nodes_to_move = [n for n in new_nodes if n.path() not in nodes_in_boxes]

    # Capture netbox data for proper repositioning
    netbox_data = {}
    for netbox in new_netboxes:
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
    # move network boxes separately (which moves their contents too)
    items_to_move = nodes_to_move + stickies_to_move + new_dots
    if position and (items_to_move or new_netboxes):
        _reposition_items(items_to_move, position, new_netboxes, netbox_data, sticky_data)

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


def import_at_cursor(package: Dict[str, Any]) -> List:
    """
    Import a package at the current cursor/view position.

    The nodes will be centered on either:
    1. The cursor position (if hovering over network view)
    2. The center of the visible network area (fallback)

    Relative positions between nodes are preserved.

    Args:
        package: The .sopdrop package dictionary

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

    return import_items(package, target_node, position)


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


def _check_missing_hdas(dependencies: List[Dict]) -> List[str]:
    """Check which HDA dependencies are missing."""
    import hou

    missing = []

    for dep in dependencies:
        name = dep.get("name")
        category_name = dep.get("category", "Sop")
        if not name:
            continue

        # Try to find the node type in the specified category
        try:
            categories = hou.nodeTypeCategories()
            if category_name in categories:
                node_type = hou.nodeType(categories[category_name], name)
                if node_type is None:
                    missing.append(name)
            else:
                # Category not found, assume missing
                missing.append(name)
        except Exception:
            missing.append(name)

    return missing


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
def import_network(package, target_node=None, position=None, force=False):
    """Legacy wrapper - use import_items() instead."""
    return import_items(package, target_node, position)
