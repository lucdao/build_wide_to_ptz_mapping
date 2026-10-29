#!/usr/bin/env python3
"""Cache and exercise ALIKED/LightGlue before the operator starts teaching."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import tempfile
from pathlib import Path
from urllib.request import urlopen


PROJECT = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_valid(document: dict, cache: Path, versions: dict, models: list[dict]) -> bool:
    if not isinstance(document, dict) or document.get("versions") != versions or not document.get("files"):
        return False
    expected = {model["filename"]: model["sha256"] for model in models}
    if {Path(entry.get("path", "").replace("\\", "/")).name for entry in document["files"] if isinstance(entry, dict)} != set(expected):
        return False
    for entry in document["files"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            return False
        path = (cache / entry["path"].replace("\\", "/")).resolve()
        if not path.is_relative_to(cache.resolve()) or not path.is_file() or sha256(path) != expected.get(path.name):
            return False
    return True


def ensure_weights(cache: Path, models: list[dict]) -> list[Path]:
    """Verify pinned official weights before they are deserialized by PyTorch."""
    checkpoints = cache / "hub" / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    result = []
    for model in models:
        filename = model["filename"]
        if Path(filename).name != filename or not model["url"].startswith("https://"):
            raise ValueError("Invalid model manifest")
        path = checkpoints / filename
        if not path.is_file() or sha256(path) != model["sha256"]:
            print(f"Dang tai model {filename}...", flush=True)
            temporary = path.with_name(path.name + ".download")
            with urlopen(model["url"], timeout=90) as response, temporary.open("wb") as target:
                shutil.copyfileobj(response, target)
            if sha256(temporary) != model["sha256"]:
                raise RuntimeError(f"Model checksum mismatch: {filename}")
            os.replace(temporary, path)
        result.append(path)
    return result


def exercise_features(extractor, matcher) -> dict:
    """Use the application's actual feature/matching APIs on one synthetic pair."""
    import cv2
    import numpy as np
    import torch
    from build_wide_ptz_mapping import build_parser, extract_features, homography_candidate

    with tempfile.TemporaryDirectory(prefix="lut-feature-check-") as folder:
        root = Path(folder)
        image = np.random.default_rng(41).integers(0, 256, size=(240, 320, 3), dtype=np.uint8)
        cv2.putText(image, "WIDE PTZ", (25, 125), cv2.FONT_HERSHEY_SIMPLEX, 1, (240, 240, 240), 3)
        image_path = root / "synthetic.jpg"
        if not cv2.imwrite(str(image_path), image):
            raise RuntimeError("Cannot write the feature self-check image")
        device = torch.device("cpu")
        wide = extract_features("wide", image_path, extractor, 320, device, root)
        ptz = extract_features("ptz", image_path, extractor, 320, device, root)
        args = build_parser().parse_args([])
        result, *_ = homography_candidate(
            wide=wide, ptz=ptz, capture={"index": 0}, matcher=matcher, device=device,
            args=args, match_dir=root, run_root=root,
        )
        if not result["plausible"]:
            raise RuntimeError("ALIKED/LightGlue synthetic image-pair self-check failed")
        return {"matches": result["matches"], "inliers": result["inliers"], "plausible": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="Verify existing files without downloading or running inference")
    args = parser.parse_args()
    cache = PROJECT / ".runtime" / "model-cache" / "torch"
    stamp = cache.parent / "models.json"
    os.environ["TORCH_HOME"] = str(cache)
    versions = {name: importlib.metadata.version(name) for name in ("torch", "kornia", "kornia_rs")}
    models = json.loads((PROJECT / "model-weights.json").read_text(encoding="utf-8"))["models"]
    try:
        previous = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    if cache_valid(previous, cache, versions, models):
        print("ALIKED/LightGlue cache OK.", flush=True)
        return 0
    if args.check_only:
        print("Model cache is not ready.", flush=True)
        return 1

    print("Dang chuan bi va kiem tra ALIKED/LightGlue (chi lan dau)...", flush=True)
    files = ensure_weights(cache, models)
    import torch
    import kornia.feature as KF

    torch.set_num_threads(min(os.cpu_count() or 1, 4))
    extractor = KF.ALIKED.from_pretrained("aliked-n16", max_num_keypoints=256, device=torch.device("cpu")).eval()
    matcher = KF.LightGlue("aliked").eval()
    check = exercise_features(extractor, matcher)
    document = {"versions": versions, "files": [{"path": path.relative_to(cache).as_posix(), "sha256": sha256(path)} for path in files], "feature_self_check": check}
    stamp.parent.mkdir(parents=True, exist_ok=True)
    temporary = stamp.with_name(stamp.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2), encoding="utf-8")
    os.replace(temporary, stamp)
    print(f"ALIKED/LightGlue OK: {check['inliers']} inliers on the synthetic self-check.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
