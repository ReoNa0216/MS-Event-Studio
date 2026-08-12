"""Build and smoke-test a native PyInstaller onedir desktop candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

from desktop_bundle.webview_smoke import (
    run_packaged_webview_smoke,
    validate_webview_smoke_payload,
)

APP_NAME = "MS-Event-Studio"
WINDOWS_RUNTIME_CONFIG = f"{APP_NAME}.exe.config"
EXPECTED_PLATFORM_BACKENDS = {
    "windows": "edgechromium",
    "macos": "cocoa",
}


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


def build_arguments(
    repository: Path,
    *,
    platform_name: str,
    dist_root: Path | None = None,
) -> list[str]:
    repository = repository.resolve()
    if platform_name not in {"windows", "macos"}:
        raise ValueError("PyInstaller is not a cross-compiler; build on Windows or macOS")
    dist = _within_repository(
        repository,
        dist_root if dist_root is not None else repository / "dist" / platform_name,
    )
    work = _within_repository(repository, repository / "build/pyinstaller" / platform_name)
    spec = _within_repository(
        repository,
        repository / "packaging" / platform_name / "ms_event_studio.spec",
    )
    arguments = [
        "--noconfirm",
        "--clean",
        "--log-level",
        "WARN",
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        str(spec),
    ]
    return arguments


def locate_executable(dist_root: Path, platform_name: str) -> Path:
    if platform_name == "windows":
        return dist_root / APP_NAME / f"{APP_NAME}.exe"
    if platform_name == "macos":
        return dist_root / f"{APP_NAME}.app/Contents/MacOS/{APP_NAME}"
    raise ValueError("unsupported desktop platform")


def validate_windows_runtime_config(path: Path) -> None:
    """Validate the CLR policy required by pythonnet/WebView2 bundles."""

    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"invalid Windows runtime config: {path}") from exc
    if root.tag != "configuration":
        raise ValueError("Windows runtime config root must be <configuration>")
    nodes = root.findall("./runtime/loadFromRemoteSources")
    if len(nodes) != 1 or nodes[0].attrib.get("enabled", "").casefold() != "true":
        raise ValueError("Windows runtime config must enable loadFromRemoteSources")


def copy_windows_runtime_config(repository: Path, executable: Path) -> Path:
    source = repository / "packaging/windows" / WINDOWS_RUNTIME_CONFIG
    validate_windows_runtime_config(source)
    target = executable.with_name(WINDOWS_RUNTIME_CONFIG)
    shutil.copy2(source, target)
    validate_windows_runtime_config(target)
    return target


def project_application_version(repository: Path) -> str:
    """Return the single package version declared by ``pyproject.toml``."""

    try:
        with (repository / "pyproject.toml").open("rb") as handle:
            version = tomllib.load(handle)["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError("pyproject.toml does not declare a valid project version") from exc
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("pyproject.toml project version must be a non-empty string")
    return version


def validate_smoke_candidate_identity(
    repository: Path,
    payload: dict[str, object],
    *,
    platform_name: str,
) -> None:
    """Require the frozen smoke to identify this source and one native backend."""

    expected_version = project_application_version(repository)
    actual_version = payload.get("application_version")
    if actual_version != expected_version:
        raise RuntimeError(
            "packaged smoke application_version does not match pyproject.toml: "
            f"expected {expected_version!r}, got {actual_version!r}"
        )
    expected_backend = EXPECTED_PLATFORM_BACKENDS.get(platform_name)
    if expected_backend is None:
        raise ValueError(f"unsupported desktop platform: {platform_name}")
    runtime = payload.get("runtime")
    actual_backend = runtime.get("platform_backend") if isinstance(runtime, dict) else None
    if actual_backend != expected_backend:
        raise RuntimeError(
            "packaged smoke used the wrong native WebView backend: "
            f"expected {expected_backend!r}, got {actual_backend!r}"
        )


def validate_single_renderer_tree(
    files: list[dict[str, object]],
    *,
    platform_name: str,
) -> None:
    """Reject Tk/Tcl and alternate renderer artifacts from a finalized tree."""

    normalized = [str(row.get("path", "")).replace("\\", "/").casefold() for row in files]
    forbidden_parts = (
        "/_tkinter.",
        "/tkinter/",
        "/tkinter.",
        "/idlelib/",
        "/turtledemo/",
        "/turtle.",
        "/tcl/",
        "/tcl8",
        "/tk/",
        "/tk8",
        "/libtcl",
        "/libtk",
        "/tk86",
        "/cefpython",
        "/libcef",
        "/pywebview-android.jar",
        "/webbrowserinterop.",
        "/qt5",
        "/qt6",
        "/libqt",
        "/qtwebengine",
        "/pyqt5/",
        "/pyqt6/",
        "/pyside2/",
        "/pyside6/",
    )
    forbidden = sorted(
        path for path in normalized if any(marker in f"/{path}" for marker in forbidden_parts)
    )
    if forbidden:
        raise RuntimeError(
            "finalized candidate contains Tk/Tcl or a second renderer: "
            + ", ".join(forbidden[:5])
        )

    required_assets = (
        "ms_event_studio/web/index.html",
        "ms_event_studio/web/tokens.css",
        "ms_event_studio/web/app.css",
        "ms_event_studio/web/app.js",
    )
    missing_assets = [
        asset for asset in required_assets if not any(path.endswith(asset) for path in normalized)
    ]
    if missing_assets:
        raise RuntimeError(
            "finalized candidate is missing production Web assets: "
            + ", ".join(missing_assets)
        )

    if platform_name == "windows":
        required_runtime = (
            "webview/lib/microsoft.web.webview2.core.dll",
            "webview/lib/microsoft.web.webview2.winforms.dll",
            "webview/lib/runtimes/win-x64/native/webview2loader.dll",
        )
        missing_runtime = [
            item for item in required_runtime if not any(path.endswith(item) for path in normalized)
        ]
        if missing_runtime:
            raise RuntimeError(
                "Windows candidate is missing the Edge Chromium runtime: "
                + ", ".join(missing_runtime)
            )
    elif platform_name != "macos":
        raise ValueError(f"unsupported desktop platform: {platform_name}")


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


def write_build_manifest(
    repository: Path,
    dist_root: Path,
    *,
    platform_name: str,
    smoke_exit_code: int = 0,
) -> dict[str, object]:
    """Hash the finalized native tree and its already-written smoke report."""

    try:
        import PyInstaller
    except ImportError as exc:
        raise RuntimeError("PyInstaller is required to finalize a bundle manifest") from exc
    repository = repository.resolve()
    dist_root = _within_repository(repository, dist_root)
    executable = locate_executable(dist_root, platform_name)
    if not executable.is_file():
        raise RuntimeError(f"candidate executable is missing: {executable}")
    if platform_name == "windows":
        validate_windows_runtime_config(executable.with_name(WINDOWS_RUNTIME_CONFIG))
    smoke_report = dist_root / "smoke_test.json"
    try:
        smoke_payload = validate_webview_smoke_payload(
            json.loads(smoke_report.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("cannot finalize a bundle without a valid WebView smoke report") from exc
    validate_smoke_candidate_identity(
        repository,
        smoke_payload,
        platform_name=platform_name,
    )

    manifest_path = dist_root / "build_manifest.json"
    manifest_path.unlink(missing_ok=True)
    files, tree_sha = _tree_manifest(dist_root)
    validate_single_renderer_tree(files, platform_name=platform_name)
    report = {
        "schema": "ms-event-studio-desktop-build-v2",
        "built_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "application_version": smoke_payload.get("application_version"),
        "platform": platform_name,
        "host_platform": platform.platform(),
        "python_version": platform.python_version(),
        "pyinstaller_version": PyInstaller.__version__,
        "bundle_mode": "onedir-windowed",
        "renderer": "pywebview",
        "executable": executable.relative_to(dist_root).as_posix(),
        "executable_sha256": _sha256(executable),
        "file_count": len(files),
        "bundle_bytes": sum(int(row["size_bytes"]) for row in files),
        "tree_sha256": tree_sha,
        "smoke_test_exit_code": smoke_exit_code,
        "smoke_test": smoke_payload,
        "files": files,
    }
    manifest_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def build(repository: Path, *, dist_root: Path | None = None) -> dict[str, object]:
    from desktop_bundle.generate_icons import generate_packaging_icon

    try:
        import PyInstaller
        import PyInstaller.__main__
    except ImportError as exc:
        raise RuntimeError("install the packaging extra: pip install -e .[packaging]") from exc

    repository = repository.resolve()
    platform_name = host_platform()
    for required_asset in (
        repository / "src/ms_event_studio/web/index.html",
        repository / "src/ms_event_studio/web/app.css",
        repository / "src/ms_event_studio/web/app.js",
    ):
        if not required_asset.is_file():
            raise RuntimeError(f"Phase 2R Web asset is missing: {required_asset}")
    generate_packaging_icon(repository, platform_name)
    resolved_dist_root = _within_repository(
        repository,
        dist_root if dist_root is not None else repository / "dist" / platform_name,
    )
    resolved_dist_root.mkdir(parents=True, exist_ok=True)
    # A candidate manifest must describe only the bundle produced by this run;
    # never let a previous report become an input to its successor.
    (resolved_dist_root / "build_manifest.json").unlink(missing_ok=True)
    (resolved_dist_root / "smoke_test.json").unlink(missing_ok=True)
    arguments = build_arguments(
        repository,
        platform_name=platform_name,
        dist_root=resolved_dist_root,
    )
    try:
        PyInstaller.__main__.run(arguments)
    except PermissionError as exc:
        raise RuntimeError(
            "The previous desktop candidate is in use. Close every running "
            "MS Event Studio window or build to a separate --dist-root."
        ) from exc
    dist_root = resolved_dist_root
    executable = locate_executable(dist_root, platform_name)
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller candidate executable is missing: {executable}")
    if platform_name == "windows":
        copy_windows_runtime_config(repository, executable)
    smoke_report = dist_root / "smoke_test.json"
    smoke, smoke_payload = run_packaged_webview_smoke(
        executable,
        smoke_report,
        cwd=dist_root,
        timeout_seconds=90,
    )
    report = write_build_manifest(
        repository,
        dist_root,
        platform_name=platform_name,
        smoke_exit_code=smoke.returncode,
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--dist-root",
        type=Path,
        help="optional repository-contained dist root (useful while an older EXE is open)",
    )
    parser.add_argument(
        "--refresh-manifest",
        action="store_true",
        help="rehash a finalized native bundle after signing and its final smoke",
    )
    args = parser.parse_args(argv)
    if args.refresh_manifest:
        repository = args.repository.resolve()
        platform_name = host_platform()
        dist_root = _within_repository(
            repository,
            args.dist_root
            if args.dist_root is not None
            else repository / "dist" / platform_name,
        )
        report = write_build_manifest(
            repository,
            dist_root,
            platform_name=platform_name,
        )
    else:
        report = build(args.repository, dist_root=args.dist_root)
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
