"""Generate a PTZ acquisition grid from four manually observed Wide corners."""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Any, Callable


CORNER_ORDER = ("top_left", "top_right", "bottom_right", "bottom_left")
CORNER_LABELS = {
    "top_left": "TREN TRAI",
    "top_right": "TREN PHAI",
    "bottom_right": "DUOI PHAI",
    "bottom_left": "DUOI TRAI",
}


def corner_pixels(width: int, height: int) -> dict[str, list[int]]:
    if isinstance(width, bool) or isinstance(height, bool) or width < 2 or height < 2:
        raise ValueError("Wide image dimensions must be at least 2x2")
    return dict(zip(CORNER_ORDER, ([0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]), strict=True))


def validate_pose(pose: Any, ranges: dict[str, Any]) -> dict[str, float]:
    if not isinstance(pose, dict):
        raise ValueError("ONVIF pose must contain pan, tilt and zoom")
    result = {}
    for axis in ("pan", "tilt", "zoom"):
        value = pose.get(axis)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"ONVIF {axis} is missing or not finite")
        limits = ranges.get(axis)
        if limits is not None and not float(limits["min"]) - 1e-7 <= value <= float(limits["max"]) + 1e-7:
            raise ValueError(f"ONVIF {axis}={value} is outside the camera position range")
        result[axis] = float(value)
    return result


def read_stable_pose(
    read_pose: Callable[[], dict[str, float]], ranges: dict[str, Any],
    *, samples: int = 3, tolerance: float = 0.005, timeout: float = 10.0,
) -> dict[str, float]:
    """Read a stable, real status; never substitute a requested target."""
    if samples < 2 or not math.isfinite(tolerance) or tolerance <= 0 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("corner samples >= 2, positive finite tolerance and timeout are required")
    window: deque[dict[str, float]] = deque(maxlen=samples)
    deadline = time.monotonic() + timeout
    last_error = "position was not stable"
    while time.monotonic() < deadline:
        try:
            window.append(validate_pose(read_pose(), ranges))
            if len(window) == samples and all(
                max(pose[axis] for pose in window) - min(pose[axis] for pose in window) <= tolerance
                for axis in ("pan", "tilt", "zoom")
            ):
                return dict(window[-1])
        except Exception as exc:
            window.clear()
            last_error = str(exc)
        time.sleep(0.15)
    raise RuntimeError(f"Cannot record a stable ONVIF corner pose: {last_error}")


def validate_corners(
    document: dict[str, Any], width: int, height: int,
    ranges: dict[str, Any], *, zoom_tolerance: float = 0.005,
) -> dict[str, dict[str, float]]:
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Unsupported corner file; schema_version must be 1")
    if document.get("status") != "complete":
        raise ValueError("Corner file is incomplete; record all four corners first")
    reference = document.get("wide_reference", {})
    if not isinstance(reference, dict):
        raise ValueError("wide_reference must be an object")
    if reference.get("width") != width or reference.get("height") != height:
        raise ValueError("Corner reference dimensions do not match the current Wide stream")
    corners = document.get("corners", {})
    if not isinstance(corners, dict):
        raise ValueError("corners must contain all four named corner records")
    pixels = corner_pixels(width, height)
    poses = {}
    for name in CORNER_ORDER:
        item = corners.get(name, {})
        if not isinstance(item, dict):
            raise ValueError(f"{name} must contain a Wide pixel and measured pose")
        if item.get("wide_pixel") != pixels[name]:
            raise ValueError(f"{name} must refer to Wide pixel {pixels[name]}")
        poses[name] = validate_pose(item.get("pose"), ranges)
    zoom = poses["top_left"]["zoom"]
    if any(abs(pose["zoom"] - zoom) > zoom_tolerance for pose in poses.values()):
        raise ValueError("Keep the same zoom while recording all four corners")

    points = [(poses[name]["pan"], poses[name]["tilt"]) for name in CORNER_ORDER]
    pan_limits = ranges.get("pan")
    if pan_limits is not None:
        pan_span = float(pan_limits["max"]) - float(pan_limits["min"])
        if pan_span <= 0:
            raise ValueError("Camera pan range has no usable span")
        if max(p[0] for p in points) - min(p[0] for p in points) > pan_span * 0.5:
            raise ValueError("Corner pan span exceeds half the camera range; pan wrap/large spans are unsupported")
    # A consistently oriented convex quadrilateral keeps the bilinear grid
    # from folding. Axis directions may be reversed; both signs are accepted.
    crosses = []
    for index in range(4):
        a, b, c = points[index], points[(index + 1) % 4], points[(index + 2) % 4]
        crosses.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
    scale = max(max(p[0] for p in points) - min(p[0] for p in points), 1e-8) * max(
        max(p[1] for p in points) - min(p[1] for p in points), 1e-8
    )
    if not (all(cross > scale * 1e-7 for cross in crosses) or all(cross < -scale * 1e-7 for cross in crosses)):
        raise ValueError("Corner poses are repeated, degenerate or crossed; check the four corner labels")
    return poses


def build_corner_plan(
    document: dict[str, Any], width: int, height: int, ranges: dict[str, Any],
) -> list[dict[str, Any]]:
    """Bilinear pose interpolation; internal Wide pixels remain unmeasured."""
    poses = validate_corners(document, width, height, ranges)
    tl, tr, br, bl = [poses[name] for name in CORNER_ORDER]
    plan = []
    for row in range(6):
        t = row / 5.0
        for column in (range(5) if row % 2 == 0 else range(4, -1, -1)):
            s = column / 4.0
            weights = ((1 - s) * (1 - t), s * (1 - t), s * t, (1 - s) * t)
            pose = {
                axis: sum(weight * anchor[axis] for weight, anchor in zip(weights, (tl, tr, br, bl), strict=True))
                for axis in ("pan", "tilt")
            }
            pose["zoom"] = tl["zoom"]
            validate_pose(pose, ranges)
            sequence = len(plan)
            plan.append({
                "sequence": sequence, "index": sequence, "row": row, "column": column,
                "requested": pose,
                "wide_anchor_policy": "four_corner_pose_grid; actual Wide centres recovered by feature matching",
                "wide_anchor_fraction_nominal": [s, t],
                "wide_pixel_nominal": [s * (width - 1), t * (height - 1)],
            })
    return plan
