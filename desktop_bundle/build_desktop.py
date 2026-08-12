"""Build and smoke-test a native PyInstaller onedir desktop candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


APP_NAME = "MS-Event-Studio"


def host_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    raise ValueError("desktop candidates are currently defined only for Windows and macOS")


def _within_repository(repository: Path, path: Path) -> Path:
    resolved_repository = repository.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(resolved_repository)
    except ValueError as exc:
        raise ValueError(f"build path escapes repository: {resolved}") from exc
    return resolved


def build_arguments(repository: Path, *, platform_name: str) -> list[str]:
    repository = repository.resolve()
    if platform_name not in {"windows", "macos"}:
        raise ValueError("PyInstaller is not a cross-compiler; build on Windows or macOS")
    dist = _within_repository(repository, repository / "release" / platform_name)
    work = _within_repository(repository, repository / "build/pyinstaller" / platform_name)
    spec = _within_repository(repository, repository / "build/pyinstaller/spec")
    entry = _within_repository(repository, repository / "desktop_bundle/ms_event_studio_gui.py")
    source = _within_repository(repository, repository / "src")
    arguments = [
        "--name",
        APP_NAME,
        "--onedir",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--log-level",
        "WARN",
        "--paths",
        str(source),
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(spec),
        "--exclude-module",
        "matplotlib",
        "--exclude-module",
        "IPython",
        "--exclude-module",
        "notebook",
        "--exclude-module",
        "torch",
        "--exclude-module",
        "numba",
        "--exclude-module",
        "pyarrow.tests",
        "--exclude-module",
        "pandas.tests",
        "--exclude-module",
        "scipy.tests",
        "--exclude-module",
        "conda",
        str(entry),
    ]
    if platform_name == "windows":
        binary_root = Path(sys.prefix) / "Library/bin"
        # Conda's MKL runtime loads these by name at runtime; static import
        # inspection sees mkl_rt but not its thread/core/CPU dispatch modules.
        for name in (
            "mkl_intel_thread.2.dll",
            "mkl_core.2.dll",
            "mkl_avx2.2.dll",
            "mkl_def.2.dll",
            "libiomp5md.dll",
        ):
            dll = binary_root / name
            if dll.is_file():
                arguments[-1:-1] = ["--add-binary", f"{dll}{os.pathsep}."]
    return arguments


def locate_executable(dist_root: Path, platform_name: str) -> Path:
    if platform_name == "windows":
        return dist_root / APP_NAME / f"{APP_NAME}.exe"
    if platform_name == "macos":
        return dist_root / f"{APP_NAME}.app/Contents/MacOS/{APP_NAME}"
    raise ValueError("unsupported desktop platform")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_manifest(root: Path) -> tuple[list[dict[str, object]], str]:
    rows: list[dict[str, object]] = []
    aggregate = hashlib.sha256()
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda p: p.as_posix()):
        relative = path.relative_to(root).as_posix()
        digest = _sha256(path)
        row = {"path": relative, "size_bytes": path.stat().st_size, "sha256": digest}
        rows.append(row)
        aggregate.update(f"{relative}\0{row['size_bytes']}\0{digest}\n".encode("utf-8"))
    return rows, aggregate.hexdigest()


def build(repository: Path) -> dict[str, object]:
    try:
        import PyInstaller
        import PyInstaller.__main__
    except ImportError as exc:
        raise RuntimeError("install the packaging extra: pip install -e .[packaging]") from exc

    repository = repository.resolve()
    platform_name = host_platform()
    arguments = build_arguments(repository, platform_name=platform_name)
    PyInstaller.__main__.run(arguments)
    dist_root = repository / "release" / platform_name
    executable = locate_executable(dist_root, platform_name)
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller candidate executable is missing: {executable}")
    smoke_report = dist_root / "smoke_test.json"
    smoke_report.unlink(missing_ok=True)
    smoke = subprocess.run(
        [str(executable), "--smoke-test", "--smoke-report", str(smoke_report)],
        cwd=dist_root,
        timeout=60,
        check=False,
    )
    try:
        smoke_payload = json.loads(smoke_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"packaged desktop smoke test produced no valid report (exit {smoke.returncode})"
        ) from exc
    if (
        smoke.returncode != 0
        or smoke_payload.get("status") != "ok"
        or not isinstance(smoke_payload.get("application_version"), str)
    ):
        raise RuntimeError(
            f"packaged desktop smoke test failed with exit {smoke.returncode}: {smoke_payload}"
        )
    files, tree_sha = _tree_manifest(dist_root)
    report = {
        "schema": "ms-event-studio-desktop-build-v1",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "application_version": smoke_payload.get("application_version"),
        "platform": platform_name,
        "host_platform": platform.platform(),
        "python_version": platform.python_version(),
        "pyinstaller_version": PyInstaller.__version__,
        "bundle_mode": "onedir-windowed",
        "executable": executable.relative_to(dist_root).as_posix(),
        "executable_sha256": _sha256(executable),
        "file_count": len(files),
        "bundle_bytes": sum(int(row["size_bytes"]) for row in files),
        "tree_sha256": tree_sha,
        "smoke_test_exit_code": smoke.returncode,
        "smoke_test": smoke_payload,
        "files": files,
    }
    manifest_path = dist_root / "build_manifest.json"
    manifest_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    report = build(args.repository)
    summary_keys = (
        "platform",
        "application_version",
        "executable",
        "executable_sha256",
        "file_count",
        "bundle_bytes",
        "tree_sha256",
        "smoke_test_exit_code",
        "smoke_test",
    )
    print(
        json.dumps(
            {key: report[key] for key in summary_keys},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
