"""Regression checks for exported LUTs and the real sibling runtime consumer.

Run: python -m unittest discover -s tests -v
Optional: LUT_CPP_CHECK points to the compiled LUT_camera_release mapping test.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from export_runtime_lut import RUNTIME_TERMS, build_runtime_model, validate_runtime_model


PROJECT = Path(__file__).resolve().parents[1]
RELEASE = Path(os.environ.get("LUT_CAMERA_RELEASE", str(PROJECT.parent / "LUT_camera_release")))
VALIDATOR = RELEASE / "calibration" / "validate_lut_mapping.py"


def legacy_model(degree: int = 2) -> dict:
    return {
        "degree": degree,
        "terms_powers_xy": [list(pair) for pair in RUNTIME_TERMS[:3 if degree == 1 else 6]],
        "coefficients_uv": [[2.0, 2.5], [2.2, 0.0], [0.0, 2.7]] + (
            [[0.08, 0.03], [0.04, -0.06], [0.02, 0.07]] if degree == 2 else []
        ),
        "input_normalization": {"width": 2560, "height": 1440},
        "state_surface": "affine",
        "state_grid_actual_after_capture": [
            [[0.6 - 0.1 * column + 0.005 * row**2, -0.1 - 0.09 * row + 0.002 * column**2, 0.0]
             for column in range(5)] for row in range(6)
        ],
    }


def run_python(*args: object) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *map(str, args)], cwd=PROJECT, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


class ExportContractTests(unittest.TestCase):
    def test_linear_zero_padding_preserves_the_mapping(self):
        source = legacy_model(1)
        model = build_runtime_model(source)
        self.assertEqual(model["coefficients_uv"][3:], [[0.0, 0.0]] * 3)
        self.assertEqual(model["state_surface"], "pchip_2d_separable")
        for x, y in ((0, 0), (-1, 1), (0.37, -0.62)):
            feature = [1, x, y, x*x, x*y, y*y]
            for axis in (0, 1):
                original = sum(feature[i] * row[axis] for i, row in enumerate(source["coefficients_uv"]))
                exported = sum(feature[i] * row[axis] for i, row in enumerate(model["coefficients_uv"]))
                self.assertEqual(original, exported)
        self.assertEqual(source["degree"], 1)
        self.assertEqual(source["state_surface"], "affine")

    def test_coefficients_follow_their_terms_when_reordered(self):
        source = legacy_model()
        expected = build_runtime_model(source)
        source["terms_powers_xy"].reverse()
        source["coefficients_uv"].reverse()
        self.assertEqual(build_runtime_model(source), expected)

    def test_incompatible_inputs_fail_instead_of_being_truncated(self):
        modifications = [
            ("degree", 3), ("degree", True), ("input_normalization", None),
            ("coefficients_uv", [[0.0, 0.0]] * 5),
            ("coefficients_uv", [[float("nan"), 0.0]] * 6),
            ("terms_powers_xy", [[0, 0]] * 6),
            ("state_grid_actual_after_capture", [[[0, 0, 0]] * 5] * 5),
            ("input_normalization", {"width": False, "height": 1440}),
            ("input_normalization", {"width": 2560, "height": 0}),
        ]
        for key, value in modifications:
            with self.subTest(key=key, value=value):
                source = legacy_model()
                source[key] = value
                with self.assertRaises(ValueError):
                    build_runtime_model(source)
        with self.assertRaises(ValueError):
            build_runtime_model({"H_wide_pixel_to_ptz_lattice": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]})

    @unittest.skipUnless(VALIDATOR.is_file(), "sibling LUT_camera_release is unavailable")
    def test_reference_mapping_matches_the_export_contract(self):
        reference = json.loads((RELEASE / "mapping" / "wide_1.3_ptz_1.8.json").read_text(encoding="utf-8"))["global_mapping"]
        model = build_runtime_model(reference)
        validate_runtime_model(model)
        for key in ("coefficients_uv", "state_grid_actual_after_capture", "input_normalization"):
            self.assertEqual(model[key], reference[key])

    @unittest.skipUnless(VALIDATOR.is_file(), "sibling LUT_camera_release is unavailable")
    def test_legacy_conversion_passes_real_validator_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "legacy.json"
            output = Path(folder) / "runtime.json"
            source.write_text(json.dumps({"model": legacy_model(1)}), encoding="utf-8")
            original = source.read_bytes()
            result = run_python(PROJECT / "export_runtime_lut.py", source, "--output", output)
            self.assertEqual(result.returncode, 0, result.stderr)
            checked = run_python(VALIDATOR, output)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(json.loads(checked.stdout)["status"], "valid")
            result = run_python(PROJECT / "predict_wide_pixel_pose.py", "--mapping", output, "--x", 1280, "--y", 720)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["lattice_uv_raw"], [2.0, 2.5])
            saved = output.read_bytes()
            again = run_python(PROJECT / "export_runtime_lut.py", source, "--output", output)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(output.read_bytes(), saved)
            self.assertEqual(source.read_bytes(), original)


class SyntheticPipelineTests(unittest.TestCase):
    def make_run(self, run: Path, degree: int) -> None:
        import cv2
        import numpy as np
        from scipy.optimize import root

        model = legacy_model(degree)
        width, height = 2560, 1440
        run.mkdir()
        wide_dir = run / "00_wide_current_and_new_set"
        ptz_dir = run / "01_ptz_overlap_grid"
        local_dir = run / "02_aliked_lightglue_wide_ptz_mapping"
        for directory in (wide_dir, ptz_dir, local_dir):
            directory.mkdir()
        (wide_dir / "wide_capture_metadata.json").write_text("{}", encoding="utf-8")
        (ptz_dir / "ptz_capture_metadata.json").write_text("{}", encoding="utf-8")
        self.assertTrue(cv2.imwrite(str(wide_dir / "wide.jpg"), np.zeros((height, width, 3), dtype=np.uint8)))
        pairs = []
        for row in range(6):
            for column in range(5):
                def residual(point):
                    x, y = point
                    feature = np.array([1, x, y, x*x, x*y, y*y])[:len(model["coefficients_uv"])]
                    return feature @ np.array(model["coefficients_uv"]) - [column, row]
                solution = root(residual, [(column - 2) / 2.2, (row - 2.5) / 2.7])
                self.assertTrue(solution.success)
                x, y = solution.x
                pan, tilt, zoom = model["state_grid_actual_after_capture"][row][column]
                pairs.append({
                    "ptz_index": row * 5 + column, "row": row, "column": column,
                    "wide_image": "00_wide_current_and_new_set/wide.jpg",
                    "mapped_ptz_center_on_wide": [(x + 1) * width / 2, (y + 1) * height / 2],
                    "actual_after_capture": {"pan": pan, "tilt": tilt, "zoom": zoom},
                })
        model["H_wide_pixel_to_ptz_lattice"] = [[4.4 / width, 0, -0.2], [0, 5.4 / height, -0.2], [0, 0, 1]]
        uu, vv = np.meshgrid(np.arange(5), np.arange(6))
        design = np.column_stack([np.ones(30), uu.ravel(), vv.ravel()])
        affine = np.linalg.lstsq(design, np.array(model["state_grid_actual_after_capture"]).reshape(30, 3), rcond=None)[0]
        model["linearity_probe"] = {"affine_coefficients_intercept_u_v": affine.tolist()}
        source = {"model": model, "centre_pairs": pairs}
        (local_dir / "wide_pixel_to_ptz_mapping.json").write_text(json.dumps(source), encoding="utf-8")

    def compare_cpp_predictions(self, output: Path, model: dict) -> None:
        import numpy as np
        from fit_polynomial_wide_to_grid import PoseSurface

        binary = os.environ.get("LUT_CPP_CHECK")
        if not binary:
            return
        result = subprocess.run([binary, str(output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        surface = PoseSurface(np.array(model["state_grid_actual_after_capture"]), "pchip_2d_separable", np.zeros((3, 3)))
        count = 0
        for line in result.stdout.splitlines():
            pixel_text, raw_text, used_text, pose_text, _ = line.split()
            pixel = np.array(list(map(float, pixel_text.split(","))))
            x, y = pixel / np.array([1280, 720]) - 1
            uv = np.array([1, x, y, x*x, x*y, y*y]) @ np.array(model["coefficients_uv"])
            used = np.clip(uv, [0, 0], [4, 5])
            pose = surface.predict(*used)
            for actual, expected in (
                (raw_text.removeprefix("raw="), uv),
                (used_text.removeprefix("used="), used),
                (pose_text.removeprefix("pose="), pose),
            ):
                np.testing.assert_allclose(list(map(float, actual.split(","))), expected, atol=1e-8, rtol=0)
            count += 1
        self.assertEqual(count, 15)

    def test_linear_and_quadratic_runs_export_consumable_luts(self):
        with tempfile.TemporaryDirectory() as folder:
            for degree in (1, 2):
                with self.subTest(degree=degree):
                    run = Path(folder) / f"synthetic_degree_{degree}"
                    self.make_run(run, degree)
                    result = run_python(
                        PROJECT / "run_mapping_pipeline.py", "--run-root", run,
                        "--reuse-local-mapping", "--max-degree", degree, "--ransac-iterations", 100,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    output = run / "03_polynomial_wide_to_grid" / "polynomial_wide_pixel_to_ptz_mapping.json"
                    document = json.loads(output.read_text(encoding="utf-8"))
                    self.assertNotIn("model", document)
                    model = document["global_mapping"]
                    validate_runtime_model(model)
                    self.assertEqual(document["fit_quality"]["selected_degree"], degree)
                    self.assertLess(document["fit_quality"]["polynomial_grid_rmse_all"], 1e-8)
                    if VALIDATOR.is_file():
                        checked = run_python(VALIDATOR, output)
                        self.assertEqual(checked.returncode, 0, checked.stderr)
                    self.compare_cpp_predictions(output, model)


if __name__ == "__main__":
    unittest.main()
