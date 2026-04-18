"""
Curves asset helpers for Sopdrop library.

Provides keyframe extraction and application so curve shapes are portable
across nodes and channels (channel-agnostic).
"""

from typing import Any, Dict, List


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


def _serialize_keyframe(kf) -> Dict[str, Any]:
    """Serialize a single hou.Keyframe to a dict."""
    return {
        "frame": kf.frame(),
        "value": _safe_get(kf.value, 0.0),
        "slope": _safe_get(kf.slope, 0.0),
        "accel": _safe_get(kf.accel, 0.0),
        "in_slope": _safe_get(kf.inSlope, 0.0),
        "in_accel": _safe_get(kf.inAccel, 0.0),
        "expression": _safe_get(kf.expression, ""),
        "in_expression": _safe_get(lambda: kf.expression(False), ""),
        "slope_auto": _safe_get(kf.isSlopeAuto, False),
        "in_slope_auto": _safe_get(kf.isInSlopeAuto, False),
        "slope_tied": _safe_get(kf.isSlopeTied, True),
        "accel_tied": _safe_get(kf.isAccelTied, True),
        "accel_as_ratio": _safe_get(kf.isAccelInterpretedAsRatio, True),
    }


def extract_curves(parms) -> List[Dict[str, Any]]:
    """Serialize keyframes from a list of hou.Parm objects.

    Args:
        parms: List of hou.Parm objects that have keyframes.

    Returns:
        List of dicts, each with ``name`` (parm name) and ``keyframes``
        (list of serialized keyframe dicts).
    """
    curves = []
    for parm in parms:
        kfs = parm.keyframes()
        if not kfs:
            continue
        curves.append({
            "name": parm.name(),
            "keyframes": [_serialize_keyframe(kf) for kf in kfs],
        })
    return curves


def get_curves_metadata_from_parms(parms) -> Dict[str, Any]:
    """Build curve metadata from a list of parms (instead of nodes)."""
    channel_names = []
    total_keyframes = 0
    all_frames = []  # type: List[float]
    has_expressions = False

    for parm in parms:
        kfs = parm.keyframes()
        if not kfs:
            continue
        channel_names.append(parm.name())
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


def apply_curves(curves_data, target_parms):
    """Apply saved curve data to target parms.

    Mapping rules:
    - 1 saved curve -> all target parms (same shape on each)
    - N saved curves -> N target parms (first to first, etc.)
    - Extras ignored on either side

    Args:
        curves_data: List of curve dicts (from ``extract_curves``).
        target_parms: List of hou.Parm objects to apply curves to.
    """
    import hou

    if not curves_data or not target_parms:
        return

    # Build mapping
    if len(curves_data) == 1:
        # 1-to-many: apply the single curve to every target parm
        pairs = [(curves_data[0], p) for p in target_parms]
    else:
        # N-to-N by order
        pairs = list(zip(curves_data, target_parms))

    with hou.undos.group("Sopdrop Paste Curves"):
        for curve, parm in pairs:
            parm.deleteAllKeyframes()
            for kf_data in curve["keyframes"]:
                kf = hou.Keyframe()
                kf.setFrame(kf_data["frame"])
                kf.setValue(kf_data["value"])
                kf.interpretAccelAsRatio(kf_data.get("accel_as_ratio", True))
                kf.setSlopeAuto(kf_data.get("slope_auto", False))
                kf.setInSlopeAuto(kf_data.get("in_slope_auto", False))
                kf.setSlope(kf_data["slope"])
                kf.setAccel(kf_data["accel"])
                if not kf_data.get("slope_tied", True):
                    kf.setInSlope(kf_data["in_slope"])
                if not kf_data.get("accel_tied", True):
                    kf.setInAccel(kf_data["in_accel"])
                expr = kf_data.get("expression", "")
                if expr:
                    kf.setExpression(expr)
                parm.setKeyframe(kf)
