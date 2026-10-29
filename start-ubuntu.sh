#!/usr/bin/env bash
# One-command Ubuntu launcher. Runtime, dependencies and model weights stay in
# this clone; a first run needs Internet, subsequent runs use the local cache.
set -Eeuo pipefail

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$PROJECT_DIR"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  printf '%s\n' 'Su dung: bash start-ubuntu.sh [--configure | --reuse-last-corners | --prepare-only]'
  exit 0
fi
case "$(uname -s):$(uname -m)" in
  Linux:x86_64)
    ARCH=x86_64
    PYTHON_URL='https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz'
    PYTHON_SHA='ce4f6d0a6cfdb3bed6e44c2f9affd85943c76a42e25ad3a259002f31d6fb8a20'
    ;;
  Linux:aarch64|Linux:arm64)
    ARCH=aarch64
    PYTHON_URL='https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz'
    PYTHON_SHA='bb4b6392670c5d3f93876996fcaa19858a4a413a0c7720144c18b0d6f1f0db2d'
    ;;
  *) printf '%s\n' 'Can Ubuntu Linux x86_64 hoac aarch64.' >&2; exit 1 ;;
esac

PREPARE_ONLY=0
if [[ "${1:-}" == "--prepare-only" ]]; then PREPARE_ONLY=1; shift; fi
if [[ "$PREPARE_ONLY" == 0 && -z "${DISPLAY:-}" ]]; then
  printf '%s\n' 'Can Ubuntu Desktop co man hinh X11/XWayland (DISPLAY) de xem hai camera.' >&2
  exit 1
fi

RUNTIME_ROOT="$PROJECT_DIR/.runtime"
RUNTIME_DIR="$RUNTIME_ROOT/ubuntu-$ARCH-3.12.16"
CACHE_DIR="$RUNTIME_ROOT/cache"
PYTHON="$RUNTIME_DIR/python/bin/python3"
ARCHIVE="$CACHE_DIR/python-3.12.16-$ARCH.tar.gz"
mkdir -p "$RUNTIME_DIR" "$CACHE_DIR"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$RUNTIME_ROOT/calibration.lock"
  flock -n 9 || { printf '%s\n' 'Chuong trinh da dang chay.' >&2; exit 1; }
fi

download() {
  local url="$1" destination="$2"
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --retry 3 --connect-timeout 20 --output "$destination" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget --https-only --tries=3 --timeout=30 --output-document="$destination" "$url"
  elif command -v python3 >/dev/null 2>&1; then
    python3 - "$url" "$destination" <<'PY'
import shutil, sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=90) as source, open(sys.argv[2], "wb") as target:
    shutil.copyfileobj(source, target)
PY
  else
    printf '%s\n' 'Can curl, wget hoac python3 de tai moi truong lan dau.' >&2
    return 1
  fi
}

if [[ ! -x "$PYTHON" || ! -f "$RUNTIME_DIR/.python.sha256" || "$(cat "$RUNTIME_DIR/.python.sha256")" != "$PYTHON_SHA" ]]; then
  printf '%s\n' 'Dang chuan bi Python rieng trong thu muc du an...'
  if [[ ! -f "$ARCHIVE" ]] || ! echo "$PYTHON_SHA  $ARCHIVE" | sha256sum -c --status; then
    download "$PYTHON_URL" "$ARCHIVE.download"
    echo "$PYTHON_SHA  $ARCHIVE.download" | sha256sum -c --status || { printf '%s\n' 'Tep Python tai ve sai SHA256.' >&2; exit 1; }
    mv -f -- "$ARCHIVE.download" "$ARCHIVE"
  fi
  tar -xzf "$ARCHIVE" -C "$RUNTIME_DIR"
  [[ -x "$PYTHON" ]] || { printf '%s\n' 'Khong tim thay Python sau khi giai nen.' >&2; exit 1; }
  printf '%s\n' "$PYTHON_SHA" > "$RUNTIME_DIR/.python.sha256"
fi

export PYTHONNOUSERSITE=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8 LUT_PORTABLE_CPU=1
export PIP_CACHE_DIR="$CACHE_DIR/pip" TORCH_HOME="$RUNTIME_ROOT/model-cache/torch"
if [[ "${XDG_SESSION_TYPE:-}" == "wayland" && -n "${DISPLAY:-}" && -z "${QT_QPA_PLATFORM:-}" ]]; then
  export QT_QPA_PLATFORM=xcb
fi
REQUIREMENTS="$PROJECT_DIR/requirements-portable.lock"
WANTED_HASH="$(sha256sum "$REQUIREMENTS" | cut -d ' ' -f 1)"
SAVED_HASH="$(cat "$RUNTIME_DIR/.requirements.sha256" 2>/dev/null || true)"
if [[ "$WANTED_HASH" != "$SAVED_HASH" ]] || ! "$PYTHON" -c 'import cv2, numpy, scipy, torch, kornia, onvif, zeep, requests, tkinter' >/dev/null 2>&1; then
  printf '%s\n' 'Dang tai thu vien CPU. Lan dau can Internet va co the mat nhieu phut...'
  "$PYTHON" -m pip install --disable-pip-version-check --no-warn-script-location setuptools==84.2.1 wheel==0.48.0
  "$PYTHON" -m pip install --disable-pip-version-check --no-warn-script-location --no-build-isolation -r "$REQUIREMENTS"
  "$PYTHON" -c 'import cv2, numpy, scipy, torch, kornia, onvif, zeep, requests, tkinter'
  printf '%s\n' "$WANTED_HASH" > "$RUNTIME_DIR/.requirements.sha256"
fi

"$PYTHON" "$PROJECT_DIR/prepare_models.py"
if [[ "$PREPARE_ONLY" == 1 ]]; then
  printf '%s\n' 'MOI TRUONG VA MODEL DA SAN SANG.'
  exit 0
fi
"$PYTHON" "$PROJECT_DIR/install_ubuntu_shortcut.py" || printf '%s\n' 'Khong tao duoc shortcut; van co the chay bang bash start-ubuntu.sh.' >&2
"$PYTHON" "$PROJECT_DIR/easy_calibration.py" --gui-config "$@"
