"""Exercise four-corner teaching and the 30-position collector without hardware."""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from corner_grid import CORNER_ORDER, build_corner_plan, corner_pixels, read_stable_pose, validate_corners


RANGES = {"pan": {"min": -1.0, "max": 1.0}, "tilt": {"min": -1.0, "max": 1.0}, "zoom": {"min": 0.0, "max": 1.0}}
WIDTH, HEIGHT = 2560, 1440


def anchors() -> dict:
    values = ((0.55, 0.35), (-0.15, 0.30), (-0.10, -0.45), (0.50, -0.40))
    pixels = corner_pixels(WIDTH, HEIGHT)
    return {
        "schema_version": 1, "status": "complete", "method": "four_corner_bilinear_ptz",
        "camera": {"onvif_host": "192.0.2.10", "onvif_port": 80, "profile_token": "profile-1"},
        "wide_reference": {"width": WIDTH, "height": HEIGHT, "stream_url": "rtsp://wide/stream"},
        "corners": {
            name: {"wide_pixel": pixels[name], "pose": {"pan": pan, "tilt": tilt, "zoom": 0.1}}
            for name, (pan, tilt) in zip(CORNER_ORDER, values, strict=True)
        },
    }


class CornerGeometryTests(unittest.TestCase):
    def test_trapezoid_generates_30_unique_cells_with_exact_endpoints(self):
        document = anchors()
        plan = build_corner_plan(document, WIDTH, HEIGHT, RANGES)
        by_cell = {(job["row"], job["column"]): job for job in plan}
        self.assertEqual(len(plan), 30)
        self.assertEqual(len(by_cell), 30)
        self.assertEqual([job["sequence"] for job in plan], list(range(30)))
        self.assertEqual([job["column"] for job in plan[5:10]], [4, 3, 2, 1, 0])
        for cell, name in (((0, 0), "top_left"), ((0, 4), "top_right"), ((5, 4), "bottom_right"), ((5, 0), "bottom_left")):
            self.assertEqual(by_cell[cell]["requested"], document["corners"][name]["pose"])
        for axis in ("pan", "tilt"):
            values = [document["corners"][name]["pose"][axis] for name in CORNER_ORDER]
            expected = 0.15 * values[0] + 0.45 * values[1] + 0.30 * values[2] + 0.10 * values[3]
            self.assertAlmostEqual(by_cell[(2, 3)]["requested"][axis], expected)
        self.assertTrue(all("wide_pixel" not in job for job in plan))

    def test_invalid_or_incompatible_corner_files_are_rejected(self):
        cases = []
        wrong_zoom = anchors()
        wrong_zoom["corners"]["top_right"]["pose"]["zoom"] = 0.3
        cases.append(wrong_zoom)
        crossed = anchors()
        crossed["corners"]["bottom_left"]["pose"], crossed["corners"]["bottom_right"]["pose"] = (
            crossed["corners"]["bottom_right"]["pose"], crossed["corners"]["bottom_left"]["pose"]
        )
        cases.append(crossed)
        repeated = anchors()
        repeated["corners"]["top_right"]["pose"] = repeated["corners"]["top_left"]["pose"].copy()
        cases.append(repeated)
        seam = anchors()
        seam["corners"]["top_left"]["pose"]["pan"] = 0.99
        seam["corners"]["top_right"]["pose"]["pan"] = -0.99
        cases.append(seam)
        for name, bad_value in (("pan", float("nan")), ("tilt", 2.8), ("zoom", True)):
            invalid = anchors()
            invalid["corners"]["top_left"]["pose"][name] = bad_value
            cases.append(invalid)
        partial = anchors()
        partial["status"] = "incomplete"
        cases.append(partial)
        resized = anchors()
        resized["wide_reference"]["width"] = 1920
        cases.append(resized)
        for index, document in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                validate_corners(document, WIDTH, HEIGHT, RANGES)

    def test_stability_uses_observed_status_and_discards_invalid_samples(self):
        pose = anchors()["corners"]["top_left"]["pose"]
        reader = MagicMock(side_effect=[{**pose, "pan": float("nan")}, {**pose, "pan": 0.2}, pose, pose, pose])
        with patch("corner_grid.time.sleep"):
            self.assertEqual(read_stable_pose(reader, RANGES), pose)
        self.assertEqual(reader.call_count, 5)
        with patch("corner_grid.time.monotonic", side_effect=[0.0, 11.0]):
            with self.assertRaisesRegex(RuntimeError, "stable ONVIF"):
                read_stable_pose(lambda: pose, RANGES, timeout=10.0)


class CollectorIntegrationTests(unittest.TestCase):
    def collector(self):
        import collect_full_dynamic_pipeline as collector
        return collector

    def options(self, *extra):
        return self.collector().build_parser().parse_args([
            "--wide-url", "rtsp://wide/stream", "--ptz-host", "192.0.2.10", "--ptz-password", "test-only",
            "--retries", "0", "--settle", "0", *map(str, extra),
        ])

    def test_missing_zoom_status_cannot_be_recorded_as_a_measured_zero(self):
        from collect_ptz_pan_tilt_grid import raw_position
        ptz = MagicMock()
        ptz.GetStatus.return_value = SimpleNamespace(Position=SimpleNamespace(PanTilt=SimpleNamespace(x=0.1, y=0.2)))
        with self.assertRaisesRegex(RuntimeError, "Position.Zoom"):
            raw_position(ptz, "profile-1", require_zoom=True)
        self.assertEqual(raw_position(ptz, "profile-1")["zoom"], 0.0)

    def prepare_boundary_mocks(self, stack: ExitStack, collector, ptz):
        import cv2
        import numpy as np

        profile = SimpleNamespace(Name="fake-camera", token="profile-1")
        stack.enter_context(patch.object(collector, "connect_ptz", return_value=(None, None, profile, "profile-1", None, ptz)))
        stack.enter_context(patch.object(collector, "discover_onvif_ranges", return_value=copy.deepcopy(RANGES)))
        stack.enter_context(patch.object(collector, "RTSPFrameReader", return_value=object()))
        stack.enter_context(patch.object(collector, "close_rtsp_reader"))
        stack.enter_context(patch.object(collector, "set_absolute_position", side_effect=lambda p, token, pan, tilt, zoom: {"pan": pan, "tilt": tilt, "zoom": zoom}))
        def state(*args, **kwargs):
            pose = ptz.AbsoluteMove.call_args.args[0]
            return {"normalized": pose.copy(), "raw": pose.copy(), "normalization": {"mode": "reported_onvif"}, "wait_fallback": False}
        before = stack.enter_context(patch.object(collector, "wait_for_command_pose", side_effect=state))
        after = stack.enter_context(patch.object(collector, "read_and_normalize_after_capture", side_effect=state))
        def capture(reader, path, timeout):
            self.assertTrue(cv2.imwrite(str(path), np.zeros((120, 160, 3), dtype=np.uint8)))
            return 160, 120
        frames = stack.enter_context(patch.object(collector, "capture_frame", side_effect=capture))
        return before, after, frames

    def test_four_enter_presses_then_30_real_collector_move_capture_cycles(self):
        collector = self.collector()
        document = anchors()
        ptz = MagicMock()
        poses = [document["corners"][name]["pose"] for name in CORNER_ORDER for _ in range(3)]
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack, redirect_stdout(io.StringIO()):
            root = Path(folder)
            _, _, frames = self.prepare_boundary_mocks(stack, collector, ptz)
            stack.enter_context(patch.object(collector, "raw_position", side_effect=poses))
            stack.enter_context(patch("corner_grid.time.sleep"))
            def enter(*args):
                self.assertEqual(ptz.AbsoluteMove.call_count, 0)
                return ""
            prompts = stack.enter_context(patch("builtins.input", side_effect=enter))
            result = collector.capture_dynamic_grid(self.options("--record-corners"), root, {"reference_width": WIDTH, "reference_height": HEIGHT})
            self.assertEqual(prompts.call_count, 4)
            self.assertEqual(ptz.AbsoluteMove.call_count, 30)
            self.assertEqual(frames.call_count, 30)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(len(result["captures"]), 30)
            saved = json.loads((root / "00_corner_anchors" / "wide_corners.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "complete")
            expected = build_corner_plan(saved, WIDTH, HEIGHT, RANGES)
            for call, job in zip(ptz.AbsoluteMove.call_args_list, expected, strict=True):
                self.assertEqual(call.args[0], job["requested"])
            self.assertTrue(all(item["actual_after_capture_normalization"]["mode"] == "reported_onvif" for item in result["captures"]))

    def test_reusing_anchors_checks_camera_identity_before_any_move(self):
        collector = self.collector()
        ptz = MagicMock()
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            root = Path(folder)
            source = root / "other_camera.json"
            document = anchors()
            document["camera"]["onvif_host"] = "192.0.2.99"
            source.write_text(json.dumps(document), encoding="utf-8")
            original = source.read_bytes()
            self.prepare_boundary_mocks(stack, collector, ptz)
            with self.assertRaisesRegex(ValueError, "different ONVIF"):
                collector.capture_dynamic_grid(self.options("--corners-file", source), root, {"reference_width": WIDTH, "reference_height": HEIGHT})
            ptz.AbsoluteMove.assert_not_called()
            self.assertEqual(source.read_bytes(), original)

    def test_cancel_preserves_recorded_corner_coordinates_without_moving_camera(self):
        collector = self.collector()
        document = anchors()
        poses = [document["corners"][name]["pose"] for name in CORNER_ORDER[:2] for _ in range(3)]
        ptz = MagicMock()
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack, redirect_stdout(io.StringIO()):
            root = Path(folder)
            stack.enter_context(patch("builtins.input", side_effect=["", "", "q"]))
            stack.enter_context(patch.object(collector, "raw_position", side_effect=poses))
            stack.enter_context(patch("corner_grid.time.sleep"))
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                collector.record_wide_corners(self.options(), root, {"reference_width": WIDTH, "reference_height": HEIGHT}, ptz, "profile-1", RANGES)
            saved = json.loads((root / "00_corner_anchors" / "wide_corners.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "incomplete")
            self.assertEqual(set(saved["corners"]), {"top_left", "top_right"})
            ptz.AbsoluteMove.assert_not_called()

    def test_target_fallback_is_rejected_before_and_after_capture(self):
        collector = self.collector()
        for bad_stage in ("before", "after"):
            with self.subTest(stage=bad_stage), tempfile.TemporaryDirectory() as folder, ExitStack() as stack, redirect_stdout(io.StringIO()):
                root = Path(folder)
                source = root / "anchors.json"
                source.write_text(json.dumps(anchors()), encoding="utf-8")
                ptz = MagicMock()
                before, after, frames = self.prepare_boundary_mocks(stack, collector, ptz)
                bad = before if bad_stage == "before" else after
                bad.side_effect = None
                bad.return_value = {"wait_fallback": True, "normalization": {"mode": "commanded_target_fallback"}}
                with self.assertRaises(RuntimeError):
                    collector.capture_dynamic_grid(self.options("--corners-file", source), root, {"reference_width": WIDTH, "reference_height": HEIGHT})
                self.assertEqual(frames.call_count, 0 if bad_stage == "before" else 1)
                metadata = json.loads((root / "01_ptz_overlap_grid" / "ptz_capture_metadata.json").read_text(encoding="utf-8"))
                self.assertEqual(metadata["captures"], [])
                self.assertEqual(metadata["status"], "incomplete")

    def test_fit_flag_delegates_only_after_complete_capture(self):
        collector = self.collector()
        for capture_status in ("complete", "incomplete"):
            with self.subTest(status=capture_status), tempfile.TemporaryDirectory() as folder, ExitStack() as stack, redirect_stdout(io.StringIO()):
                root = Path(folder) / "run"
                stack.enter_context(patch("sys.argv", ["collector", "--wide-url", "rtsp://wide/stream", "--ptz-host", "192.0.2.10", "--ptz-password", "test-only", "--run-root", str(root), "--fit"]))
                stack.enter_context(patch.object(collector, "capture_wide", return_value={}))
                stack.enter_context(patch.object(collector, "capture_dynamic_grid", return_value={"status": capture_status}))
                fit = stack.enter_context(patch.object(collector.subprocess, "run"))
                self.assertEqual(collector.main(), 0 if capture_status == "complete" else 2)
                self.assertEqual(fit.call_count, 1 if capture_status == "complete" else 0)
                metadata = json.loads((root / "run_metadata.json").read_text(encoding="utf-8"))
                self.assertEqual("runtime_lut" in metadata, capture_status == "complete")

    def test_cancel_is_reported_as_cancelled_without_fitting(self):
        collector = self.collector()
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack, redirect_stdout(io.StringIO()):
            root = Path(folder) / "run"
            stack.enter_context(patch("sys.argv", ["collector", "--wide-url", "rtsp://wide/stream", "--ptz-host", "192.0.2.10", "--ptz-password", "test-only", "--run-root", str(root), "--fit"]))
            stack.enter_context(patch.object(collector, "capture_wide", return_value={}))
            stack.enter_context(patch.object(collector, "capture_dynamic_grid", side_effect=collector.CalibrationCancelled("operator stopped")))
            fit = stack.enter_context(patch.object(collector.subprocess, "run"))
            self.assertEqual(collector.main(), 130)
            fit.assert_not_called()
            metadata = json.loads((root / "run_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
