#!/usr/bin/env python3
"""Export a final polynomial calibration as a LUT_camera_release input JSON."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any


RUNTIME_TERMS = ((0, 0), (1, 0), (0, 1), (2, 0), (1, 1), (0, 2))
RUNTIME_ROWS = 6
RUNTIME_COLUMNS = 5


def finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def check_array(value: Any, shape: tuple[int, ...], name: str) -> None:
    if not isinstance(value, list) or len(value) != shape[0]:
        raise ValueError(f"{name} must have shape {'x'.join(map(str, shape))}")
    for index, item in enumerate(value):
        if len(shape) == 1:
            finite_number(item, f"{name}[{index}]")
        else:
            check_array(item, shape[1:], f"{name}[{index}]")


def validate_runtime_model(model: dict[str, Any]) -> None:
    """Check shape AND conventions hard-coded in the current C++ consumer."""
    if not isinstance(model, dict):
        raise ValueError("global_mapping must be an object")
    if model.get("mapping_type") != "polynomial_wide_pixel_to_ptz_lattice":
        raise ValueError("global_mapping.mapping_type must be polynomial_wide_pixel_to_ptz_lattice")
    if model.get("degree") != 2 or model.get("terms_powers_xy") != [list(pair) for pair in RUNTIME_TERMS]:
        raise ValueError("runtime requires terms [1, x, y, x^2, xy, y^2] in that order")
    check_array(model.get("coefficients_uv"), (6, 2), "global_mapping.coefficients_uv")
    check_array(
        model.get("state_grid_actual_after_capture"),
        (RUNTIME_ROWS, RUNTIME_COLUMNS, 3),
        "global_mapping.state_grid_actual_after_capture",
    )
    normalization = model.get("input_normalization")
    if not isinstance(normalization, dict):
        raise ValueError("global_mapping.input_normalization must be an object")
    for key in ("width", "height"):
        if finite_number(normalization.get(key), f"input_normalization.{key}") <= 0:
            raise ValueError(f"input_normalization.{key} must be positive")
    if model.get("state_surface") != "pchip_2d_separable":
        raise ValueError("runtime uses pchip_2d_separable for the pose grid")


def build_runtime_model(source: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize a degree-1/2 model without truncating or refitting it.

    A linear winner gets zero quadratic coefficients, preserving its mapping.
    The consumer always uses PCHIP, even if the legacy baseline used affine.
    """
    if not isinstance(source, dict) or "coefficients_uv" not in source:
        raise ValueError("use the final polynomial JSON; the intermediate homography is not a runtime LUT")
    degree = source.get("degree")
    if isinstance(degree, bool) or degree not in (1, 2):
        raise ValueError("runtime export supports degree 1 or 2; degree 3 must be refitted, not truncated")
    expected_terms = RUNTIME_TERMS[:3] if degree == 1 else RUNTIME_TERMS
    terms = source.get("terms_powers_xy")
    if not isinstance(terms, list) or len(terms) != len(expected_terms) or any(
        not isinstance(pair, (list, tuple)) or len(pair) != 2
        or any(isinstance(power, bool) or not isinstance(power, int) for power in pair)
        for pair in terms
    ):
        raise ValueError("terms_powers_xy must describe every fitted polynomial term")
    powers = [tuple(pair) for pair in terms]
    if len(set(powers)) != len(powers) or set(powers) != set(expected_terms):
        raise ValueError("terms_powers_xy is inconsistent with the polynomial degree")
    coefficients = source["coefficients_uv"]
    check_array(coefficients, (len(powers), 2), "coefficients_uv")
    normalization = source.get("input_normalization")
    if not isinstance(normalization, dict):
        raise ValueError("input_normalization must be an object containing width and height")
    by_power = dict(zip(powers, coefficients, strict=True))
    model = {
        "mapping_type": "polynomial_wide_pixel_to_ptz_lattice",
        "degree": 2,
        "terms_powers_xy": [list(pair) for pair in RUNTIME_TERMS],
        "coefficients_uv": [list(by_power.get(pair, [0.0, 0.0])) for pair in RUNTIME_TERMS],
        "input_normalization": {
            key: normalization.get(key) for key in ("width", "height")
        },
        "state_surface": "pchip_2d_separable",
        "state_grid_actual_after_capture": copy.deepcopy(source.get("state_grid_actual_after_capture")),
    }
    # Retain optional calibration diagnostics present in the reference file.
    for key in ("linearity_probe", "pchip_trigger"):
        if key in source:
            model[key] = copy.deepcopy(source[key])
    validate_runtime_model(model)
    return model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mapping", type=Path, help="Final legacy model JSON or a global_mapping JSON")
    parser.add_argument("--output", type=Path, required=True, help="New output file; existing files are never overwritten")
    args = parser.parse_args()
    try:
        document = json.loads(args.mapping.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("mapping JSON must be an object")
        source = document.get("global_mapping", document.get("model", document))
        model = build_runtime_model(source)
        payload = {
            "status": "complete",
            "pipeline": "runtime LUT export: polynomial Wide->grid + PCHIP pose surface",
            "source_mapping": str(args.mapping.resolve()),
            "export_metadata": {
                "selected_degree": source["degree"],
                "source_state_surface": source.get("state_surface"),
                "runtime_state_surface": "pchip_2d_separable",
            },
            "global_mapping": model,
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": "exported", "mapping": str(args.output.resolve()), "pose_grid": "6x5x3"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
