"""Portable, project-root-contained manifest path resolution."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

from .errors import PathSecurityError


_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def resolve_project_path(project_root: str | Path, manifest_path: str) -> Path:
    root = Path(project_root).resolve()
    raw = str(manifest_path)
    if not raw or "\x00" in raw:
        raise PathSecurityError("manifest path is empty or contains NUL")

    windows = PureWindowsPath(raw)
    posix = PurePosixPath(raw)
    normalized_parts = PurePosixPath(raw.replace("\\", "/")).parts
    ordinary_parts = [part for part in normalized_parts if part not in ("/", "\\")]
    if (
        windows.is_absolute()
        or bool(windows.drive)
        or posix.is_absolute()
        or ":" in raw
        or any(part in ("..", "") for part in normalized_parts)
    ):
        raise PathSecurityError(f"unsafe project-relative path: {raw!r}")
    for part in ordinary_parts:
        if part.endswith((" ", ".")) or any(ord(character) < 32 for character in part):
            raise PathSecurityError(f"non-portable project path segment: {part!r}")
        device_stem = part.rstrip(" .").split(".", 1)[0].upper()
        if device_stem in _WINDOWS_RESERVED:
            raise PathSecurityError(f"Windows reserved device path segment: {part!r}")

    candidate = (root.joinpath(*normalized_parts)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PathSecurityError(f"path escapes project root: {raw!r}") from exc
    return candidate
