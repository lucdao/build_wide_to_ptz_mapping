"""A single OpenCV window for viewing both cameras and teaching PTZ corners."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import cv2
import numpy as np

from capture_onvif import RTSPFrameReader, set_absolute_position
from collect_ptz_pan_tilt_grid import close_rtsp_reader, raw_position, wait_for_command_pose
from corner_grid import CORNER_LABELS, CORNER_ORDER, validate_pose


WINDOW = "HIEU CHUAN WIDE - PTZ"
STEPS = (0.00025, 0.0025, 0.015)
BUTTONS = {
    "fine": (20, 455, 130, 505), "normal": (140, 455, 260, 505), "fast": (270, 455, 380, 505),
    "zoom_out": (405, 455, 525, 505), "zoom_in": (535, 455, 655, 505),
    "left": (720, 470, 780, 520), "up": (790, 435, 850, 480),
    "down": (790, 490, 850, 535), "right": (860, 470, 920, 520),
    "save": (960, 455, 1255, 525), "cancel": (1130, 10, 1255, 45),
}
RETRY_BUTTONS = {name: (15 + index * 315, 460, 315 + index * 315, 525) for index, name in enumerate(CORNER_ORDER)}


def nudge_pose(pose: dict, direction: str, ranges: dict, step: float, pan_sign: int = 1, tilt_sign: int = 1) -> dict:
    target = dict(pose)
    axis, sign = {"left": ("pan", -pan_sign), "right": ("pan", pan_sign), "up": ("tilt", tilt_sign), "down": ("tilt", -tilt_sign), "zoom_out": ("zoom", -1), "zoom_in": ("zoom", 1)}[direction]
    limits = ranges[axis]
    target[axis] = max(limits["min"], min(limits["max"], target[axis] + sign * step * (limits["max"] - limits["min"])))
    return validate_pose(target, ranges)


def draw_preview(wide: np.ndarray | None, ptz: np.ndarray | None, corner: str, message: str = "", speed: int = 1, zoom_enabled: bool = True) -> np.ndarray:
    canvas = np.full((590, 1280, 3), (28, 25, 22), dtype=np.uint8)
    for image, origin, title in ((wide, 0, "WIDE - GOC CAN CAN"), (ptz, 640, "PTZ - DUA DIEM DO VAO TAM")):
        cv2.putText(canvas, title, (origin + 15, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (230, 230, 230), 1, cv2.LINE_AA)
        if image is None:
            cv2.putText(canvas, "Dang cho hinh camera...", (origin + 140, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (150, 160, 240), 1)
            continue
        height, width = image.shape[:2]
        scale = min(640 / width, 360 / height)
        w, h = max(1, round(width * scale)), max(1, round(height * scale))
        x, y = origin + (640 - w) // 2, 60 + (360 - h) // 2
        canvas[y:y+h, x:x+w] = cv2.resize(image, (w, h), interpolation=cv2.INTER_AREA)
        if origin == 0:
            marker = (x + (w - 1 if "right" in corner else 0), y + (h - 1 if "bottom" in corner else 0))
            cv2.circle(canvas, marker, 16, (0, 220, 255), 4, cv2.LINE_AA)
        else:
            cv2.drawMarker(canvas, (x + w // 2, y + h // 2), (0, 0, 255), cv2.MARKER_CROSS, 16, 1)
    labels = {"fine": "1 TINH", "normal": "2 VUA", "fast": "3 NHANH", "zoom_out": "ZOOM -", "zoom_in": "ZOOM +", "left": "A <", "up": "W ^", "down": "S v", "right": "D >", "save": f"ENTER: LUU GOC {CORNER_ORDER.index(corner) + 1}/4", "cancel": "Q DUNG"}
    for name, (x1, y1, x2, y2) in BUTTONS.items():
        color = (70, 125, 55) if name == "save" else (75, 65, 55)
        if name in ("zoom_out", "zoom_in") and not zoom_enabled:
            color = (45, 45, 45)
        if name in ("fine", "normal", "fast") and ("fine", "normal", "fast").index(name) == speed:
            color = (145, 100, 30)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, -1)
        cv2.putText(canvas, labels[name], (x1 + 8, (y1 + y2) // 2 + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (250, 250, 250), 1, cv2.LINE_AA)
    zoom_label = "Chinh zoom truoc goc 1" if zoom_enabled else "Zoom da co dinh"
    cv2.putText(canvas, f"{CORNER_LABELS[corner]} | {zoom_label} | X/Y: dao chieu pan/tilt", (15, 555), cv2.FONT_HERSHEY_SIMPLEX, 0.54, (190, 220, 240), 1, cv2.LINE_AA)
    cv2.putText(canvas, message[:145], (15, 580), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (160, 210, 170), 1, cv2.LINE_AA)
    return canvas


class CornerPreview:
    def __init__(self, args: Any, ptz: Any, token: str, ranges: dict, ptz_url: str):
        self.args, self.ptz, self.token, self.ranges, self.ptz_url = args, ptz, token, ranges, ptz_url
        self.wide_reader = self.ptz_reader = None
        self.executor = None
        self.future = None
        self.purpose = ""
        self.message = "WASD / mui ten hoac giu nut chuot de di chuyen; Enter de luu."
        self.speed, self.pan_sign, self.tilt_sign = 1, 1, 1
        self.active_direction = None
        self.save_requested = self.cancelled = self.ready = False
        self.retry_mode, self.retry_choice = False, None
        self.zoom_locked = False
        self.last_move = 0.0

    def __enter__(self):
        try:
            self.pose = validate_pose(raw_position(self.ptz, self.token, require_zoom=self.ranges.get("zoom") is not None), self.ranges)
            self.wide_reader = RTSPFrameReader(self.args.wide_url, self.args.rtsp_timeout)
            self.ptz_reader = RTSPFrameReader(self.ptz_url, self.args.rtsp_timeout)
            self.executor = ThreadPoolExecutor(max_workers=1)
            cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
            cv2.setMouseCallback(WINDOW, self._mouse)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self.executor:
            self.executor.shutdown(wait=True)
        # Release manual control before the automatic acquisition loop starts.
        try:
            self.ptz.Stop({"ProfileToken": self.token, "PanTilt": True, "Zoom": self.ranges.get("zoom") is not None})
        except Exception:
            pass
        close_rtsp_reader(self.wide_reader)
        close_rtsp_reader(self.ptz_reader)
        try:
            cv2.destroyWindow(WINDOW)
        except cv2.error:
            pass

    def set_message(self, message: str):
        self.message = message

    def on_corner_saved(self, name: str, pose: dict):
        self.pose = dict(pose)
        self.zoom_locked = True
        self.message = f"Da luu {CORNER_LABELS[name]}. Chuyen den goc tiep theo."

    def _mouse(self, event, x, y, flags, userdata):
        if event == cv2.EVENT_LBUTTONUP:
            self.active_direction = None
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        if self.retry_mode:
            for name, (x1, y1, x2, y2) in RETRY_BUTTONS.items():
                if x1 <= x <= x2 and y1 <= y <= y2:
                    self.retry_choice = name
                    return
            if 1130 <= x <= 1255 and 10 <= y <= 45:
                self.cancelled = True
            return
        for action, (x1, y1, x2, y2) in BUTTONS.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                if action in ("left", "right", "up", "down", "zoom_out", "zoom_in"):
                    if not self.save_requested:
                        self.active_direction = action
                elif action == "save":
                    self.save_requested, self.active_direction = True, None
                elif action == "cancel":
                    self.cancelled = True
                else:
                    self.speed = ("fine", "normal", "fast").index(action)
                break

    def _move(self, target):
        self.ptz.AbsoluteMove(set_absolute_position(self.ptz, self.token, target["pan"], target["tilt"], target["zoom"]))
        return target

    def _settle(self):
        state = wait_for_command_pose(
            self.ptz, self.token, self.pose, self.ranges, self.args.settle,
            self.args.move_timeout, self.args.tolerance, self.args.stable_samples,
        )
        if state["wait_fallback"] or state["normalization"]["mode"] != "reported_onvif":
            raise RuntimeError("Camera did not confirm the manual pose")

    def _poll(self):
        if self.future is not None and self.future.done():
            try:
                result = self.future.result()
                if self.purpose == "move":
                    self.pose = result
                else:
                    self.ready = True
            except Exception:
                self.message = "Khong xac nhan duoc pose. Kiem tra camera, can lai roi Enter."
                self.save_requested = False
                self.active_direction = None
            self.future = None

    @staticmethod
    def _frame(reader):
        with reader.lock:
            if reader.latest_frame is None or time.monotonic() - reader.latest_read_at > 3.0:
                return None
            return reader.latest_frame.copy()

    @staticmethod
    def _visible():
        try:
            return cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) >= 1
        except cv2.error:
            return False

    def confirm_corner(self, name: str) -> str:
        self.save_requested = self.ready = False
        self.active_direction = None
        directions = {
            ord("a"): "left", ord("d"): "right", ord("w"): "up", ord("s"): "down",
            ord("-"): "zoom_out", ord("+"): "zoom_in", ord("="): "zoom_in",
            2424832: "left", 2555904: "right", 2490368: "up", 2621440: "down",
            65361: "left", 65363: "right", 65362: "up", 65364: "down",
            0x01000012: "left", 0x01000014: "right", 0x01000013: "up", 0x01000015: "down",
        }
        while True:
            self._poll()
            if self.cancelled:
                return "q"
            if self.ready:
                return ""
            wide, ptz = self._frame(self.wide_reader), self._frame(self.ptz_reader)
            message = "Dang cho camera on dinh de luu..." if self.save_requested else self.message
            cv2.imshow(WINDOW, draw_preview(wide, ptz, name, message, self.speed, not self.zoom_locked and self.ranges.get("zoom") is not None))
            key = cv2.waitKeyEx(20)
            if 65 <= key <= 90:
                key += 32
            if key in (ord("q"), 27) or not self._visible():
                return "q"
            if key in (10, 13):
                self.save_requested, self.active_direction = True, None
            elif key in (ord("1"), ord("2"), ord("3")):
                self.speed = key - ord("1")
            elif key == ord("x"):
                self.pan_sign *= -1
                self.message = "Da dao chieu pan."
            elif key == ord("y"):
                self.tilt_sign *= -1
                self.message = "Da dao chieu tilt."
            direction = self.active_direction or directions.get(key)
            if direction in ("zoom_out", "zoom_in") and (self.zoom_locked or self.ranges.get("zoom") is None):
                self.active_direction = direction = None
                self.message = "Zoom duoc giu co dinh sau khi luu goc dau tien."
            if wide is None or ptz is None:
                self.save_requested = False
                self.active_direction = direction = None
                self.message = "Can hinh moi tu ca hai camera truoc khi luu."
            if self.future is None:
                if self.save_requested:
                    self.purpose = "settle"
                    self.future = self.executor.submit(self._settle)
                elif direction and time.monotonic() - self.last_move > 0.12:
                    target = nudge_pose(self.pose, direction, self.ranges, STEPS[self.speed], self.pan_sign, self.tilt_sign)
                    self.purpose = "move"
                    self.future = self.executor.submit(self._move, target)
                    self.last_move = time.monotonic()

    def choose_retry_corner(self, message: str) -> str:
        self.retry_mode, self.retry_choice = True, None
        self.active_direction = None
        self.set_message("Bon goc chua hop le. Bam nut goc can luu lai hoac phim 1-4; Q:dung.")
        try:
            while True:
                image = draw_preview(self._frame(self.wide_reader), self._frame(self.ptz_reader), "top_left", self.message, self.speed)
                cv2.rectangle(image, (0, 425), (1280, 540), (28, 25, 22), -1)
                for index, (name, (x1, y1, x2, y2)) in enumerate(RETRY_BUTTONS.items(), start=1):
                    cv2.rectangle(image, (x1, y1), (x2, y2), (75, 65, 55), -1)
                    cv2.putText(image, f"{index}. {CORNER_LABELS[name]}", (x1 + 12, y1 + 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (250, 250, 250), 1)
                cv2.imshow(WINDOW, image)
                key = cv2.waitKeyEx(30)
                if self.cancelled or key in (ord("q"), 27) or not self._visible():
                    return "q"
                if self.retry_choice:
                    return self.retry_choice
                if ord("1") <= key <= ord("4"):
                    return CORNER_ORDER[key - ord("1")]
        finally:
            self.retry_mode = False
