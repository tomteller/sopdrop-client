"""
Curves asset helpers for Sopdrop library.

Curves assets use the same V2 cpio export/import as regular node assets.
This module extracts curve-specific metadata (channel names, keyframe counts,
frame range) for display in the save dialog and asset cards.
"""

from typing import Any, Dict, List, Tuple


def _safe_get(func, default=0.0):
    """Call a hou.Keyframe accessor, returning *default* on KeyframeValueNotSet."""
    try:
        return func()
    except Exception:
        return default


def get_curves_metadata(nodes) -> Dict[str, Any]:
    """
    Extract curve-specific metadata from nodes that have keyframed parms.

    Args:
        nodes: List of hou.Node objects.

    Returns:
        Dict with channel_count, channel_names, keyframe_count, frame_range,
        has_expressions.
    """
    channel_names = []
    total_keyframes = 0
    all_frames = []
    has_expressions = False

    for node in nodes:
        for parm in node.parms():
            kfs = parm.keyframes()
            if not kfs:
                continue
            channel_names.append(f"{node.name()}/{parm.name()}")
            total_keyframes += len(kfs)
            for kf in kfs:
                all_frames.append(kf.frame())
                expr = _safe_get(kf.expression, "")
                if expr:
                    has_expressions = True

    frame_range = [min(all_frames), max(all_frames)] if all_frames else [0, 0]

    return {
        "channel_count": len(channel_names),
        "channel_names": channel_names,
        "keyframe_count": total_keyframes,
        "frame_range": frame_range,
        "has_expressions": has_expressions,
    }


def get_keyframed_nodes(items) -> List:
    """
    Filter items to only nodes that have at least one keyframed parm.

    Args:
        items: List of hou.NetworkMovableItem (nodes, boxes, etc.)

    Returns:
        List of hou.Node objects with keyframes.
    """
    import hou

    nodes = []
    for item in items:
        if not isinstance(item, hou.Node):
            continue
        for parm in item.parms():
            if parm.keyframes():
                nodes.append(item)
                break
    return nodes
