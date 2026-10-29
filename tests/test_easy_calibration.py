"""Check the integrated operator window and the one-click calibration runner."""

from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

import calibration_preview as preview
import easy_calibration as easy
from export_runtime_lut import RUNTIME_TERMS


RANGES = {"pan": {"min": -1.0, "max": 1.0}, "tilt": {"min": -1.0, "max": 1.0}, "zoom": {"min": 0.0, "max": 1.0}}
POSE = {"pan": 0.4, "tilt": 0.3, "zoom": 0.1}


class ImmediateExecutor:
    def __init__(self, **kwargs):
        pass

    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, **kwargs):
        pass


class PreviewTests(unittest.TestCase):
    def test_manual_nudge_is_bounded_and_preserves_zoom(self):
        target = preview.nudge_pose({**POSE, "pan": 0.99}, "right", RANGES, 0.1)
        self.assertEqual(target["pan"], 1.0)
        self.assertEqual(target["zoom"], POSE["zoom"])
        reverse = preview.nudge_pose(POSE, "up", RANGES, 0.01, tilt_sign=-1)
        self.assertAlmostEqual(reverse["tilt"], 0.28)
        self.assertEqual(POSE["tilt"], 0.3)
        zoomed = preview.nudge_pose(POSE, "zoom_in", RANGES, 0.01)
        self.assertAlmostEqual(zoomed["zoom"], 0.11)
        self.assertEqual(zoomed["pan"], POSE["pan"])

    def test_zoom_can_be_set_before_first_corner_and_is_then_locked(self):
        with ExitStack() as stack:
            args, ptz, reader, settled, closed = self.boundary_mocks(stack)
            stack.enter_context(patch.object(preview.cv2, "waitKeyEx", side_effect=[ord("+"), 13, -1, ord("+"), 13, -1]))
            with preview.CornerPreview(args, ptz, "profile-1", RANGES, "rtsp://ptz/stream") as operator:
                self.assertEqual(operator.confirm_corner("top_left"), "")
                recorded = ptz.AbsoluteMove.call_args.args[0].copy()
                self.assertGreater(recorded["zoom"], POSE["zoom"])
                operator.on_corner_saved("top_left", recorded)
                self.assertEqual(operator.confirm_corner("top_right"), "")
            self.assertEqual(ptz.AbsoluteMove.call_count, 1)
            self.assertEqual(settled.call_count, 2)

    def test_render_keeps_both_camera_images_visible(self):
        wide = np.full((1440, 2560, 3), (50, 100, 150), dtype=np.uint8)
        ptz = np.full((480, 640, 3), (150, 100, 50), dtype=np.uint8)
        image = preview.draw_preview(wide, ptz, "top_left")
        self.assertEqual(image.shape, (590, 1280, 3))
        np.testing.assert_array_equal(image[200, 200], [50, 100, 150])
        np.testing.assert_array_equal(image[200, 800], [150, 100, 50])
        np.testing.assert_array_equal(image[240, 960], [0, 0, 255])
        np.testing.assert_array_equal(image[240, 970], [150, 100, 50])

    def boundary_mocks(self, stack):
        args = SimpleNamespace(wide_url="rtsp://wide/stream", rtsp_timeout=1, settle=0, move_timeout=1, tolerance=0.04, stable_samples=3)
        ptz = MagicMock()
        reader = SimpleNamespace(lock=threading.Lock(), latest_frame=np.zeros((180, 320, 3), dtype=np.uint8), latest_read_at=time.monotonic())
        stack.enter_context(patch.object(preview, "RTSPFrameReader", return_value=reader))
        closed = stack.enter_context(patch.object(preview, "close_rtsp_reader"))
        stack.enter_context(patch.object(preview, "raw_position", return_value=POSE.copy()))
        stack.enter_context(patch.object(preview, "ThreadPoolExecutor", ImmediateExecutor))
        stack.enter_context(patch.object(preview, "set_absolute_position", side_effect=lambda p, t, pan, tilt, zoom: {"pan": pan, "tilt": tilt, "zoom": zoom}))
        settled = stack.enter_context(patch.object(preview, "wait_for_command_pose", return_value={"wait_fallback": False, "normalization": {"mode": "reported_onvif"}}))
        for name in ("namedWindow", "setMouseCallback", "imshow", "destroyWindow"):
            stack.enter_context(patch.object(preview.cv2, name))
        stack.enter_context(patch.object(preview.cv2, "getWindowProperty", return_value=1))
        return args, ptz, reader, settled, closed

    def test_move_and_save_stays_in_one_window_and_waits_for_actual_pose(self):
        with ExitStack() as stack:
            args, ptz, reader, settled, closed = self.boundary_mocks(stack)
            stack.enter_context(patch.object(preview.cv2, "waitKeyEx", side_effect=[ord("d"), 13, -1]))
            with preview.CornerPreview(args, ptz, "profile-1", RANGES, "rtsp://ptz/stream") as operator:
                self.assertEqual(operator.confirm_corner("top_left"), "")
            self.assertEqual(ptz.AbsoluteMove.call_count, 1)
            self.assertGreater(ptz.AbsoluteMove.call_args.args[0]["pan"], POSE["pan"])
            self.assertEqual(ptz.AbsoluteMove.call_args.args[0]["zoom"], POSE["zoom"])
            settled.assert_called_once()
            ptz.Stop.assert_called_once()
            self.assertEqual(closed.call_count, 2)

    def test_ubuntu_arrow_keys_move_and_save(self):
        for key in (65363, 0x01000014):
            with self.subTest(key=key), ExitStack() as stack:
                args, ptz, reader, settled, closed = self.boundary_mocks(stack)
                stack.enter_context(patch.object(preview.cv2, "waitKeyEx", side_effect=[key, 13, -1]))
                with preview.CornerPreview(args, ptz, "profile-1", RANGES, "rtsp://ptz/stream") as operator:
                    self.assertEqual(operator.confirm_corner("top_left"), "")
                self.assertEqual(ptz.AbsoluteMove.call_count, 1)
                self.assertGreater(ptz.AbsoluteMove.call_args.args[0]["pan"], POSE["pan"])

    def test_stale_camera_frame_cannot_confirm_a_corner(self):
        with ExitStack() as stack:
            args, ptz, reader, settled, closed = self.boundary_mocks(stack)
            reader.latest_read_at = time.monotonic() - 20
            stack.enter_context(patch.object(preview.cv2, "waitKeyEx", side_effect=[13, ord("q")]))
            with preview.CornerPreview(args, ptz, "profile-1", RANGES, "rtsp://ptz/stream") as operator:
                self.assertEqual(operator.confirm_corner("top_left"), "q")
            settled.assert_not_called()
            ptz.AbsoluteMove.assert_not_called()

    def test_stale_frame_also_blocks_manual_motion(self):
        with ExitStack() as stack:
            args, ptz, reader, settled, closed = self.boundary_mocks(stack)
            reader.latest_read_at = time.monotonic() - 20
            stack.enter_context(patch.object(preview.cv2, "waitKeyEx", side_effect=[ord("d"), ord("q")]))
            with preview.CornerPreview(args, ptz, "profile-1", RANGES, "rtsp://ptz/stream") as operator:
                self.assertEqual(operator.confirm_corner("top_left"), "q")
            ptz.AbsoluteMove.assert_not_called()

    def test_retry_corner_is_selectable_with_a_click(self):
        with ExitStack() as stack:
            args, ptz, reader, settled, closed = self.boundary_mocks(stack)
            with preview.CornerPreview(args, ptz, "profile-1", RANGES, "rtsp://ptz/stream") as operator:
                def click(*args):
                    x1, y1, x2, y2 = preview.RETRY_BUTTONS["bottom_left"]
                    operator._mouse(preview.cv2.EVENT_LBUTTONDOWN, (x1+x2)//2, (y1+y2)//2, 0, None)
                    return -1
                stack.enter_context(patch.object(preview.cv2, "waitKeyEx", side_effect=click))
                self.assertEqual(operator.choose_retry_corner("invalid"), "bottom_left")
                self.assertFalse(operator.retry_mode)
            ptz.AbsoluteMove.assert_not_called()

    def test_measured_saved_pose_replaces_the_last_requested_pose(self):
        with ExitStack() as stack:
            args, ptz, reader, settled, closed = self.boundary_mocks(stack)
            with preview.CornerPreview(args, ptz, "profile-1", RANGES, "rtsp://ptz/stream") as operator:
                actual = {**POSE, "pan": 0.399}
                operator.on_corner_saved("top_left", actual)
                self.assertEqual(operator.pose, actual)

    def test_collector_preview_mode_records_four_corners_without_terminal_input(self):
        import collect_full_dynamic_pipeline as collector
        args = collector.build_parser().parse_args(["--preview", "--wide-url", "rtsp://wide/stream", "--ptz-host", "192.0.2.10", "--ptz-password", "test-only"])
        poses = [{"pan": pan, "tilt": tilt, "zoom": 0.1} for pan, tilt in ((0.4, 0.4), (0.0, 0.4), (0.0, -0.1), (0.4, -0.1)) for _ in range(3)]
        operator = MagicMock()
        operator.confirm_corner.return_value = ""
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack, redirect_stdout(io.StringIO()):
            viewer = stack.enter_context(patch.object(preview, "CornerPreview"))
            viewer.return_value.__enter__.return_value = operator
            terminal = stack.enter_context(patch("builtins.input", side_effect=AssertionError("terminal input is not expected")))
            stack.enter_context(patch.object(collector, "raw_position", side_effect=poses))
            stack.enter_context(patch("corner_grid.time.sleep"))
            result = collector.record_wide_corners(args, Path(folder), {"reference_width": 2560, "reference_height": 1440}, MagicMock(), "profile-1", RANGES)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(operator.confirm_corner.call_count, 4)
            terminal.assert_not_called()
            viewer.return_value.__exit__.assert_called_once()


class EasyRunnerTests(unittest.TestCase):
    def settings(self):
        return {"WIDE_RTSP_URL": "rtsp://root:wide-secret@192.0.2.3/a", "PTZ_RTSP_URL": "rtsp://admin:ptz-secret@192.0.2.8/b", "PTZ_HOST": "192.0.2.8", "PTZ_PORT": 80, "PTZ_USER": "admin", "PTZ_PASSWORD": "ptz-secret", "DEVICE": "cpu"}

    def completed_process(self, command, **kwargs):
        run = Path(command[command.index("--run-root") + 1])
        model = {
            "mapping_type": "polynomial_wide_pixel_to_ptz_lattice", "degree": 2,
            "terms_powers_xy": [list(pair) for pair in RUNTIME_TERMS], "coefficients_uv": [[0, 0]] * 6,
            "state_grid_actual_after_capture": [[[0, 0, 0]] * 5] * 6,
            "input_normalization": {"width": 2560, "height": 1440}, "state_surface": "pchip_2d_separable",
        }
        easy.write_json(run / "run_metadata.json", {"status": "complete"})
        easy.write_json(run / "03_polynomial_wide_to_grid" / "polynomial_wide_pixel_to_ptz_mapping.json", {"global_mapping": model})
        easy.write_json(run / "00_corner_anchors" / "wide_corners.json", {"status": "complete", "corners": {}})
        return SimpleNamespace(returncode=0)

    def test_setup_remembers_two_urls_and_derives_onvif_credentials(self):
        with tempfile.TemporaryDirectory() as folder, patch("builtins.input", side_effect=[self.settings()["WIDE_RTSP_URL"], "rtsp://admin:p%40ss@192.0.2.8:554/b"]), redirect_stdout(io.StringIO()) as output:
            path = Path(folder) / ".env.calibration.json"
            settings = easy.configure(path)
            self.assertEqual(settings["PTZ_PASSWORD"], "p@ss")
            self.assertEqual(settings["PTZ_HOST"], "192.0.2.8")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), settings)
            self.assertNotIn("p@ss", output.getvalue())

    def test_one_command_enables_preview_fit_and_publishes_validated_output(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(easy.subprocess, "run", side_effect=self.completed_process) as execute, redirect_stdout(io.StringIO()):
            project = Path(folder)
            self.assertEqual(easy.run_calibration(self.settings(), project, open_output=False), 0)
            command = execute.call_args.args[0]
            for option in ("--record-corners", "--preview", "--fit"):
                self.assertIn(option, command)
            self.assertNotIn("ptz-secret", " ".join(command))
            self.assertEqual(execute.call_args.kwargs["env"]["PTZ_PASSWORD"], "ptz-secret")
            self.assertTrue((project / "output" / "latest_lut.json").is_file())
            self.assertEqual(json.loads((project / "output" / "latest_result.json").read_text(encoding="utf-8"))["status"], "complete")

    def test_failed_capture_preserves_the_previous_lut(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(easy.subprocess, "run", return_value=SimpleNamespace(returncode=1)), redirect_stdout(io.StringIO()):
            project = Path(folder)
            output = project / "output" / "latest_lut.json"
            easy.write_json(output, {"previous": True})
            original = output.read_bytes()
            self.assertEqual(easy.run_calibration(self.settings(), project, open_output=False), 1)
            self.assertEqual(output.read_bytes(), original)

    def test_success_exit_with_invalid_lut_does_not_replace_previous_output(self):
        def invalid_process(command, **kwargs):
            result = self.completed_process(command, **kwargs)
            run = Path(command[command.index("--run-root") + 1])
            path = run / "03_polynomial_wide_to_grid" / "polynomial_wide_pixel_to_ptz_mapping.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["global_mapping"]["coefficients_uv"] = [[0, 0]] * 3
            easy.write_json(path, document)
            return result
        with tempfile.TemporaryDirectory() as folder, patch.object(easy.subprocess, "run", side_effect=invalid_process), redirect_stdout(io.StringIO()):
            project = Path(folder)
            output = project / "output" / "latest_lut.json"
            easy.write_json(output, {"previous": True})
            original = output.read_bytes()
            with self.assertRaises(ValueError):
                easy.run_calibration(self.settings(), project, open_output=False)
            self.assertEqual(output.read_bytes(), original)

    def test_reuse_uses_saved_corners_without_another_manual_preview(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(easy.subprocess, "run", side_effect=self.completed_process) as execute, redirect_stdout(io.StringIO()):
            project = Path(folder)
            with self.assertRaisesRegex(ValueError, "Chua co 4 goc"):
                easy.run_calibration(self.settings(), project, reuse_corners=True, open_output=False)
            easy.write_json(project / "output" / "latest_corners.json", {"status": "complete"})
            self.assertEqual(easy.run_calibration(self.settings(), project, reuse_corners=True, open_output=False), 0)
            command = execute.call_args.args[0]
            self.assertIn("--corners-file", command)
            self.assertNotIn("--preview", command)


if __name__ == "__main__":
    unittest.main()
