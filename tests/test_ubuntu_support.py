"""Portable Ubuntu setup, GUI configuration and cached-model contract."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import easy_calibration as easy
import install_ubuntu_shortcut as shortcut
import prepare_models


class FakeVariable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeWidget:
    def __init__(self, *args, **kwargs):
        self.variable = kwargs.get("textvariable")

    def grid(self, *args, **kwargs):
        return None

    def grid_remove(self):
        return None

    def columnconfigure(self, *args, **kwargs):
        return None

    def focus_set(self):
        return None


class FakeRoot(FakeWidget):
    def __init__(self, urls=None, cancel=False):
        super().__init__()
        self.entries = []
        self.save = None
        self.urls = urls
        self.cancel = cancel
        self.closed = False

    def title(self, *args): pass
    def resizable(self, *args): pass
    def minsize(self, *args): pass

    def destroy(self):
        self.closed = True

    def mainloop(self):
        if self.cancel:
            self.destroy()
            return
        if self.urls:
            self.entries[0].variable.set(self.urls[0])
            self.entries[1].variable.set(self.urls[1])
        self.save()


def fake_tk(root):
    ttk = types.ModuleType("tkinter.ttk")
    ttk.Frame = ttk.Label = ttk.Checkbutton = FakeWidget
    def entry(*args, **kwargs):
        widget = FakeWidget(*args, **kwargs)
        root.entries.append(widget)
        return widget
    def button(*args, **kwargs):
        if kwargs.get("text") == "Lưu và tiếp tục":
            root.save = kwargs["command"]
        return FakeWidget(*args, **kwargs)
    ttk.Entry, ttk.Button = entry, button
    box = types.ModuleType("tkinter.messagebox")
    box.showerror = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected setup error"))
    tk = types.ModuleType("tkinter")
    tk.Tk, tk.StringVar, tk.BooleanVar = lambda: root, FakeVariable, FakeVariable
    tk.ttk, tk.messagebox = ttk, box
    return {"tkinter": tk, "tkinter.ttk": ttk, "tkinter.messagebox": box}


class UbuntuConfigurationTests(unittest.TestCase):
    def test_first_run_config_is_saved_from_one_window(self):
        urls = ["rtsp://root:widepass@192.0.2.3/wide", "rtsp://admin:p%40ss@192.0.2.8/ptz"]
        root = FakeRoot(urls=urls)
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, fake_tk(root)), patch("builtins.input", side_effect=AssertionError("terminal input was used")):
            path = Path(folder) / ".env.calibration.json"
            settings = easy.configure_gui(path)
            self.assertTrue(root.closed)
            self.assertEqual(settings["PTZ_PASSWORD"], "p@ss")
            self.assertEqual(settings["PTZ_HOST"], "192.0.2.8")
            self.assertEqual(settings["PTZ_PORT"], 80)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), settings)

    def test_existing_custom_onvif_connection_is_preserved(self):
        settings = {
            "WIDE_RTSP_URL": "rtsp://wide/stream", "PTZ_RTSP_URL": "rtsp://admin:stream-pass@ptz/stream",
            "PTZ_HOST": "ptz-onvif", "PTZ_PORT": 8080, "PTZ_USER": "onvif-user", "PTZ_PASSWORD": "different-pass", "DEVICE": "cpu",
        }
        root = FakeRoot()
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, fake_tk(root)):
            path = Path(folder) / ".env.calibration.json"
            easy.write_json(path, settings)
            self.assertEqual(easy.configure_gui(path), settings)

    def test_cancel_setup_does_not_create_credentials_file(self):
        root = FakeRoot(cancel=True)
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, fake_tk(root)):
            path = Path(folder) / ".env.calibration.json"
            with self.assertRaises(KeyboardInterrupt):
                easy.configure_gui(path)
            self.assertFalse(path.exists())


class PortableCacheTests(unittest.TestCase):
    def test_pinned_weights_are_checked_and_windows_paths_are_portable(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            directory = cache / "hub" / "checkpoints"
            directory.mkdir(parents=True)
            specs = []
            entries = []
            for name, content in (("aliked-n16.pth", b"first"), ("aliked_lightglue_v0-1_arxiv-pth", b"second")):
                path = directory / name
                path.write_bytes(content)
                digest = hashlib.sha256(content).hexdigest()
                specs.append({"filename": name, "sha256": digest})
                entries.append({"path": "hub\\checkpoints\\" + name, "sha256": digest})
            versions = {"torch": "test", "kornia": "test", "kornia_rs": "test"}
            document = {"versions": versions, "files": entries}
            self.assertTrue(prepare_models.cache_valid(document, cache, versions, specs))
            (directory / specs[1]["filename"]).write_bytes(b"corrupt")
            self.assertFalse(prepare_models.cache_valid(document, cache, versions, specs))

    def test_ubuntu_menu_entry_handles_a_clone_path_with_spaces(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "Camera project $one%two"
            project.mkdir()
            output = shortcut.install(project, Path(folder) / "desktop-data")
            text = output.read_text(encoding="utf-8")
            self.assertIn("Type=Application", text)
            self.assertIn("start-ubuntu.sh", text)
            self.assertIn("Camera project \\$one", text)
            self.assertIn("%%two", text)
            self.assertIn("Terminal=true", text)

    def test_shell_launcher_has_valid_syntax_and_help_without_setup(self):
        bash = os.environ.get("LUT_TEST_BASH") or shutil.which("bash")
        if not bash:
            self.skipTest("no bash path supplied")
        script = Path(__file__).resolve().parents[1] / "start-ubuntu.sh"
        for args in (("-n", str(script)), (str(script), "--help")):
            result = subprocess.run([bash, *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_first_run_prepares_runtime_once_for_both_ubuntu_architectures(self):
        bash = os.environ.get("LUT_TEST_BASH") or shutil.which("bash")
        if not bash:
            self.skipTest("no bash available")
        source = Path(__file__).resolve().parents[1] / "start-ubuntu.sh"
        for arch in ("x86_64", "aarch64"):
            with self.subTest(arch=arch), tempfile.TemporaryDirectory(prefix="lut ubuntu ") as folder:
                project = Path(folder) / "project with spaces"
                fake_bin = Path(folder) / "fake-bin"
                project.mkdir()
                fake_bin.mkdir()
                shutil.copyfile(source, project / "start-ubuntu.sh")
                (project / "requirements-portable.lock").write_text("test==1.0\n", encoding="utf-8")

                def shell_file(path, text):
                    with path.open("w", encoding="utf-8", newline="\n") as handle:
                        handle.write("#!/bin/sh\n" + text + "\n")
                    path.chmod(0o755)

                shell_file(fake_bin / "uname", 'if [ "$1" = "-s" ]; then echo Linux; else echo "$LUT_FAKE_ARCH"; fi')
                shell_file(fake_bin / "curl", 'while [ "$#" -gt 0 ]; do if [ "$1" = "--output" ]; then dest="$2"; shift 2; else shift; fi; done\necho archive > "$dest"\necho curl >> "$PWD/bootstrap-calls"')
                shell_file(fake_bin / "sha256sum", 'if [ "$1" = "-c" ]; then cat >/dev/null; exit 0; fi\nprintf "%064d  %s\\n" 0 "$1"')
                shell_file(fake_bin / "tar", 'while [ "$#" -gt 0 ]; do if [ "$1" = "-C" ]; then dest="$2"; shift 2; else shift; fi; done\nmkdir -p "$dest/python/bin"\ncp "$PWD/fake-python" "$dest/python/bin/python3"\nchmod +x "$dest/python/bin/python3"\necho tar >> "$PWD/bootstrap-calls"')
                shell_file(project / "fake-python", 'echo "python $*" >> "$PWD/bootstrap-calls"\nexit 0')
                environment = {**os.environ, "LUT_FAKE_BIN": str(fake_bin), "LUT_TEST_SCRIPT": str(project / "start-ubuntu.sh"), "LUT_FAKE_ARCH": arch}
                if os.name == "nt":
                    invocation = 'PATH="$(cygpath -u "$LUT_FAKE_BIN"):$PATH"; export PATH; exec bash "$(cygpath -u "$LUT_TEST_SCRIPT")" --prepare-only'
                else:
                    invocation = 'PATH="$LUT_FAKE_BIN:$PATH"; export PATH; exec bash "$LUT_TEST_SCRIPT" --prepare-only'
                for _ in range(2):
                    result = subprocess.run([bash, "-c", invocation], cwd=project, env=environment, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                calls = (project / "bootstrap-calls").read_text(encoding="utf-8")
                self.assertEqual(calls.count("curl\n"), 1)
                self.assertEqual(calls.count("tar\n"), 1)
                self.assertEqual(calls.count("python -m pip install"), 2)
                self.assertEqual(calls.count("prepare_models.py"), 2)


if __name__ == "__main__":
    unittest.main()
