#!/usr/bin/env python3
"""Install a per-user application-menu shortcut for this particular clone."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


PROJECT = Path(__file__).resolve().parent


def desktop_quote(value: str) -> str:
    # Desktop Entry Exec uses double quotes and escapes these four characters.
    value = value.replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        value = value.replace(char, "\\" + char)
    return '"' + value + '"'


def install(project: Path, data_home: Path) -> Path:
    project = project.resolve()
    identity = hashlib.sha256(os.fsencode(project)).hexdigest()[:12]
    applications = data_home / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    path = applications / f"wide-ptz-calibration-{identity}.desktop"
    text = (
        "[Desktop Entry]\nType=Application\nVersion=1.0\n"
        "Name=Hieu chuan Wide PTZ\nComment=Can bon goc va tao LUT\n"
        f"Exec=/bin/bash {desktop_quote(str(project / 'start-ubuntu.sh'))}\n"
        "Icon=camera-video\nTerminal=true\nCategories=Utility;AudioVideo;\n"
    )
    temporary = path.with_suffix(".desktop.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(0o755)
    os.replace(temporary, path)
    return path


if __name__ == "__main__":
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    print(f"Ubuntu application shortcut: {install(PROJECT, data_home)}")
