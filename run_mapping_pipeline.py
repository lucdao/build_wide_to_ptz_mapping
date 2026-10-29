#!/usr/bin/env python3
"""Build local PTZ-to-Wide homographies, then fit the global Wide-to-PTZ poly."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent


def run_step(name: str, command: list[str]) -> None:
    print(f"[{name}] {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=PROJECT_DIR, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True, help="Complete output from collect_full_dynamic_pipeline.py.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Defaults to RUN_ROOT/03_polynomial_wide_to_grid.")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--max-keypoints", type=int, default=2048)
    parser.add_argument("--max-degree", type=int, choices=(1, 2), default=2, help="Compare degree 1 up to this maximum; default includes the quadratic model.")
    parser.add_argument("--ransac-threshold-grid", type=float, default=0.35)
    parser.add_argument("--ransac-iterations", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=20260914)
    parser.add_argument("--reuse-local-mapping", action="store_true", help="Reuse an existing 02.../wide_pixel_to_ptz_mapping.json.")
    args = parser.parse_args()

    run_root = args.run_root.resolve()
    if not run_root.is_dir():
        raise SystemExit(f"Không tìm thấy run-root: {run_root}")
    for relative_path in (
        Path("00_wide_current_and_new_set") / "wide_capture_metadata.json",
        Path("01_ptz_overlap_grid") / "ptz_capture_metadata.json",
    ):
        if not (run_root / relative_path).is_file():
            raise SystemExit(f"Run thiếu metadata: {run_root / relative_path}")

    local_mapping = run_root / "02_aliked_lightglue_wide_ptz_mapping" / "wide_pixel_to_ptz_mapping.json"
    if args.reuse_local_mapping:
        if not local_mapping.is_file():
            raise SystemExit(f"Không tìm thấy local mapping để dùng lại: {local_mapping}")
        print(f"[local homographies] using {local_mapping}", flush=True)
    else:
        if local_mapping.exists():
            raise SystemExit(
                f"Local mapping đã tồn tại: {local_mapping}. Dùng --reuse-local-mapping để fit lại tầng toàn cục."
            )
        run_step(
            "local homographies",
            [
                sys.executable,
                str(PROJECT_DIR / "build_wide_ptz_mapping.py"),
                "--run-root",
                str(run_root),
                "--device",
                args.device,
                "--max-keypoints",
                str(args.max_keypoints),
            ],
        )

    output_dir = (args.output_dir or run_root / "03_polynomial_wide_to_grid").resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SystemExit(f"Output đã có dữ liệu, không ghi đè: {output_dir}")

    fit_command = [
        sys.executable,
        str(PROJECT_DIR / "fit_polynomial_wide_to_grid.py"),
        "--run-root",
        str(run_root),
        "--output-dir",
        str(output_dir),
        "--max-degree",
        str(args.max_degree),
        "--ransac-threshold",
        str(args.ransac_threshold_grid),
        "--ransac-iterations",
        str(args.ransac_iterations),
        "--seed",
        str(args.seed),
    ]
    run_step("global polynomial", fit_command)
    print(f"[complete] LUT_camera_release input: {output_dir / 'polynomial_wide_pixel_to_ptz_mapping.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
