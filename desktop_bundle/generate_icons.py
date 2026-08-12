#!/usr/bin/env python3
"""Derive runtime, Windows, and macOS icons from the approved master PNG."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


RUNTIME_SIZES = (32, 64, 128, 256)
WINDOWS_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
MACOS_ICONSET = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _master(repository: Path) -> Image.Image:
    path = repository / "src/ms_event_studio/assets/app_icon_master.png"
    if not path.is_file():
        raise FileNotFoundError(f"application icon master is missing: {path}")
    image = Image.open(path).convert("RGBA")
    if image.width != image.height or image.width < 1024:
        raise ValueError("application icon master must be square and at least 1024 px")
    if image.getextrema()[3][0] != 0:
        raise ValueError("application icon master must retain transparent outer corners")
    return image


def generate_runtime_assets(repository: str | Path) -> list[Path]:
    root = Path(repository).resolve()
    image = _master(root)
    output = root / "src/ms_event_studio/assets"
    paths: list[Path] = []
    for size in RUNTIME_SIZES:
        path = output / f"app_icon_{size}.png"
        image.resize((size, size), Image.Resampling.LANCZOS).save(path, format="PNG")
        paths.append(path)
    return paths


def generate_packaging_icon(repository: str | Path, platform_name: str) -> Path:
    root = Path(repository).resolve()
    image = _master(root)
    output = root / "build/icons"
    output.mkdir(parents=True, exist_ok=True)
    if platform_name == "windows":
        path = output / "MS-Event-Studio.ico"
        image.save(path, format="ICO", sizes=[(size, size) for size in WINDOWS_SIZES])
        return path
    if platform_name != "macos":
        raise ValueError(f"unsupported icon platform: {platform_name}")
    iconset = output / "MS-Event-Studio.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for filename, size in MACOS_ICONSET.items():
        image.resize((size, size), Image.Resampling.LANCZOS).save(
            iconset / filename,
            format="PNG",
        )
    path = output / "MS-Event-Studio.icns"
    if sys.platform != "darwin":
        raise RuntimeError("macOS .icns generation must run natively with iconutil")
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(path)],
        check=True,
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--platform", choices=("windows", "macos"))
    parser.add_argument("--runtime-assets", action="store_true")
    args = parser.parse_args(argv)
    paths: list[Path] = []
    if args.runtime_assets or args.platform is None:
        paths.extend(generate_runtime_assets(args.repository))
    if args.platform is not None:
        paths.append(generate_packaging_icon(args.repository, args.platform))
    print(
        json.dumps(
            [
                {
                    "path": str(path.resolve()),
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
                for path in paths
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
