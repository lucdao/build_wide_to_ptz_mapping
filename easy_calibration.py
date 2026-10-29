#!/usr/bin/env python3
"""Remember camera settings and run the complete calibration from one launcher."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from export_runtime_lut import validate_runtime_model


PROJECT = Path(__file__).resolve().parent
CAMERA_FIELDS = ("WIDE_RTSP_URL", "PTZ_RTSP_URL", "PTZ_HOST", "PTZ_PORT", "PTZ_USER", "PTZ_PASSWORD")


def validate_settings(settings: dict) -> None:
    if not isinstance(settings, dict):
        raise ValueError("Cau hinh camera phai la JSON object.")
    for key in ("WIDE_RTSP_URL", "PTZ_RTSP_URL"):
        value = settings.get(key)
        try:
            parts = urlsplit(value) if isinstance(value, str) else None
            valid = parts and parts.scheme in ("rtsp", "rtsps") and parts.hostname
            if parts:
                parts.port  # Validate the port without including credentials in an error.
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(f"{key} chua co dia chi RTSP hop le.")
    for key in ("PTZ_HOST", "PTZ_USER", "PTZ_PASSWORD"):
        if not isinstance(settings.get(key), str) or not settings[key]:
            raise ValueError(f"Thieu {key} trong cau hinh camera.")
    port = settings.get("PTZ_PORT", 80)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("PTZ_PORT phai la cong ONVIF tu 1 den 65535.")
    if settings.get("DEVICE", "cpu") not in ("cpu", "cuda"):
        raise ValueError("DEVICE phai la cpu hoac cuda.")


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def configure(path: Path) -> dict:
    try:
        previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, ValueError):
        previous = {}
    print("CAU HINH MOT LAN. Nhap RTSP gom user/password; Enter giu gia tri da luu.")
    wide = input("RTSP camera Wide: ").strip() or previous.get("WIDE_RTSP_URL") or os.getenv("WIDE_RTSP_URL", "")
    ptz = input("RTSP camera PTZ:  ").strip() or previous.get("PTZ_RTSP_URL") or os.getenv("PTZ_RTSP_URL", "")
    try:
        parts = urlsplit(ptz)
        host = parts.hostname or ""
        user = unquote(parts.username or previous.get("PTZ_USER", "admin"))
        password = unquote(parts.password) if parts.password is not None else previous.get("PTZ_PASSWORD", "")
    except ValueError:
        raise ValueError("RTSP PTZ khong hop le.") from None
    if not password:
        password = getpass.getpass("Mat khau ONVIF PTZ: ")
    settings = {
        "WIDE_RTSP_URL": wide, "PTZ_RTSP_URL": ptz, "PTZ_HOST": host,
        "PTZ_PORT": previous.get("PTZ_PORT", 80), "PTZ_USER": user, "PTZ_PASSWORD": password,
        "DEVICE": previous.get("DEVICE", "cpu"),
    }
    validate_settings(settings)
    write_json(path, settings)
    print(f"Da luu cau hinh cuc bo: {path.name}")
    return settings


def configure_gui(path: Path) -> dict:
    """Ask for the camera connection once, without using the terminal."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    try:
        previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, ValueError):
        previous = {}
    root = tk.Tk()
    root.title("Thiết lập camera Wide - PTZ")
    root.resizable(True, False)
    root.minsize(720, 250)
    panel = ttk.Frame(root, padding=22)
    panel.grid(sticky="nsew")
    root.columnconfigure(0, weight=1)
    panel.columnconfigure(1, weight=1)
    ttk.Label(panel, text="Kết nối camera (chỉ cần nhập một lần)", font=("TkDefaultFont", 14, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
    values = {key: tk.StringVar(value=str(previous.get(key, ""))) for key in CAMERA_FIELDS}
    values["PTZ_PORT"].set(str(previous.get("PTZ_PORT", 80)))
    values["PTZ_USER"].set(str(previous.get("PTZ_USER", "admin")))
    ttk.Label(panel, text="RTSP Wide").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=5)
    wide_entry = ttk.Entry(panel, textvariable=values["WIDE_RTSP_URL"], width=70)
    wide_entry.grid(row=1, column=1, sticky="ew", pady=5)
    ttk.Label(panel, text="RTSP PTZ").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=5)
    ttk.Entry(panel, textvariable=values["PTZ_RTSP_URL"], width=70).grid(row=2, column=1, sticky="ew", pady=5)
    advanced_initial = False
    if previous:
        try:
            old_ptz = urlsplit(str(previous.get("PTZ_RTSP_URL", "")))
            advanced_initial = (
                previous.get("PTZ_HOST") != (old_ptz.hostname or "")
                or previous.get("PTZ_PORT", 80) != 80
                or previous.get("PTZ_USER") != unquote(old_ptz.username or "admin")
                or previous.get("PTZ_PASSWORD") != unquote(old_ptz.password or "")
            )
        except ValueError:
            advanced_initial = True
    advanced = tk.BooleanVar(value=advanced_initial)
    details = ttk.Frame(panel)
    details.columnconfigure(1, weight=1)
    for row, (name, label) in enumerate((("PTZ_HOST", "IP ONVIF"), ("PTZ_PORT", "Cổng ONVIF"), ("PTZ_USER", "Tài khoản ONVIF"), ("PTZ_PASSWORD", "Mật khẩu ONVIF"))):
        ttk.Label(details, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
        entry = ttk.Entry(details, textvariable=values[name], show="*" if name == "PTZ_PASSWORD" else "")
        entry.grid(row=row, column=1, sticky="ew", pady=3)
    def show_details():
        if advanced.get():
            details.grid(row=4, column=0, columnspan=2, sticky="ew", pady=6)
        else:
            details.grid_remove()
    ttk.Checkbutton(panel, text="ONVIF dùng thông tin khác RTSP PTZ", variable=advanced, command=show_details).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 2))
    show_details()
    ttk.Label(panel, text="Mặc định ONVIF dùng tài khoản từ RTSP PTZ và cổng 80.").grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 12))
    buttons = ttk.Frame(panel)
    buttons.grid(row=6, column=0, columnspan=2, sticky="e")
    result = []

    def save():
        try:
            wide = values["WIDE_RTSP_URL"].get().strip()
            ptz = values["PTZ_RTSP_URL"].get().strip()
            parts = urlsplit(ptz)
            if advanced.get():
                host = values["PTZ_HOST"].get().strip() or parts.hostname or ""
                user = values["PTZ_USER"].get().strip()
                password = values["PTZ_PASSWORD"].get()
                port = int(values["PTZ_PORT"].get())
            else:
                host = parts.hostname or ""
                user = unquote(parts.username or "admin")
                password = unquote(parts.password or "")
                port = 80
            settings = {
                "WIDE_RTSP_URL": wide, "PTZ_RTSP_URL": ptz, "PTZ_HOST": host,
                "PTZ_PORT": port, "PTZ_USER": user, "PTZ_PASSWORD": password, "DEVICE": "cpu",
            }
            validate_settings(settings)
            write_json(path, settings)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Chưa lưu được cấu hình", str(exc), parent=root)
            return
        result.append(settings)
        root.destroy()

    ttk.Button(buttons, text="Hủy", command=root.destroy).grid(row=0, column=0, padx=5)
    ttk.Button(buttons, text="Lưu và tiếp tục", command=save).grid(row=0, column=1)
    wide_entry.focus_set()
    root.mainloop()
    if not result:
        raise KeyboardInterrupt
    return result[0]


def show_gui_error(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Hiệu chuẩn chưa hoàn tất", message, parent=root)
        root.destroy()
    except Exception:
        # A lost display must not hide the error already printed in the terminal.
        pass


def run_calibration(settings: dict, project: Path, *, reuse_corners: bool = False, open_output: bool = True) -> int:
    validate_settings(settings)
    run = project / "runs" / ("calibration_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    output = project / "output"
    device = "cpu" if os.environ.get("LUT_PORTABLE_CPU") == "1" else settings.get("DEVICE", "cpu")
    command = [sys.executable, str(project / "collect_full_dynamic_pipeline.py"), "--fit", "--device", device, "--run-root", str(run)]
    if reuse_corners:
        saved = output / "latest_corners.json"
        if not saved.is_file():
            raise ValueError("Chua co 4 goc da luu. Hay chay BAT_DAU_HIEU_CHUAN.cmd truoc.")
        command += ["--corners-file", str(saved)]
    else:
        command += ["--record-corners", "--preview"]
    environment = {**os.environ, **{key: str(settings.get(key, 80)) for key in CAMERA_FIELDS}, "PYTHONIOENCODING": "utf-8"}
    print("Cua so Wide/PTZ se mo. Can 4 goc va bam LUU; phan con lai tu dong.", flush=True)
    result = subprocess.run(command, cwd=project, env=environment)
    if result.returncode != 0:
        print(f"Chua hoan tat. Xem loi tai: {run / 'run_metadata.json'}", flush=True)
        return result.returncode
    manifest = json.loads((run / "run_metadata.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise ValueError("Lan hieu chuan chua complete; giu nguyen LUT da co.")
    mapping = json.loads((run / "03_polynomial_wide_to_grid" / "polynomial_wide_pixel_to_ptz_mapping.json").read_text(encoding="utf-8"))
    validate_runtime_model(mapping.get("global_mapping"))
    corners = json.loads((run / "00_corner_anchors" / "wide_corners.json").read_text(encoding="utf-8"))
    if corners.get("status") != "complete":
        raise ValueError("Bo 4 goc chua complete; khong cap nhat ket qua cuoi.")
    write_json(output / "latest_lut.json", mapping)
    write_json(output / "latest_corners.json", corners)
    write_json(output / "latest_result.json", {"status": "complete", "source_run": str(run), "lut": "latest_lut.json", "corners": "latest_corners.json"})
    print(f"DA XONG. LUT: {output / 'latest_lut.json'}", flush=True)
    if open_output and hasattr(os, "startfile"):
        os.startfile(str(output))
    elif open_output and shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", str(output)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT / ".env.calibration.json")
    parser.add_argument("--configure", action="store_true", help="Change saved settings and exit")
    parser.add_argument("--reuse-last-corners", action="store_true", help="Acquire a new run using the last completed corner file")
    parser.add_argument("--no-open", action="store_true", help="Do not open the output folder")
    parser.add_argument("--gui-config", action="store_true", help="Show the first-run camera setup window")
    args = parser.parse_args()
    try:
        if args.configure or not args.config.is_file():
            settings = configure_gui(args.config) if args.gui_config else configure(args.config)
            if args.configure:
                return 0
        else:
            try:
                settings = json.loads(args.config.read_text(encoding="utf-8"))
                validate_settings(settings)
            except (OSError, ValueError):
                settings = configure_gui(args.config) if args.gui_config else configure(args.config)
        result = run_calibration(settings, PROJECT, reuse_corners=args.reuse_last_corners, open_output=not args.no_open)
        if result not in (0, 130) and args.gui_config:
            show_gui_error("Kiểm tra kết nối camera và xem run_metadata.json trong thư mục runs của dự án.")
        return result
    except KeyboardInterrupt:
        print("Da dung.")
        return 130
    except (OSError, ValueError) as exc:
        print(f"Khong the bat dau/hoan tat: {exc}", file=sys.stderr)
        if args.gui_config:
            show_gui_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
