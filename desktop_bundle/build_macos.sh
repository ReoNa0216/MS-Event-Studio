#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
version="${MS_EVENT_STUDIO_VERSION:-0.4.1}"

if [[ ! "$version" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
  echo "Version may contain only letters, digits, period, underscore, and hyphen (maximum 64 characters)." >&2
  exit 1
fi

cd "$repo_root"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS packaging must run on macOS." >&2
  exit 1
fi
if [[ -n "${PYTHON_BIN:-}" ]]; then
  python_bin="$PYTHON_BIN"
else
  bootstrap_python="${PYTHON_BOOTSTRAP:-python3}"
  venv_root="$repo_root/build/venv/macos"
  if [[ ! -x "$venv_root/bin/python" ]]; then
    "$bootstrap_python" -m venv "$venv_root"
  fi
  python_bin="$venv_root/bin/python"
fi
machine="$($python_bin -c 'import platform; print(platform.machine().lower())')"
if [[ "$machine" != "arm64" ]]; then
  echo "macOS ARM64 packaging requires an arm64 Python interpreter; found: $machine" >&2
  exit 1
fi

"$python_bin" -m pip install --upgrade pip wheel setuptools
"$python_bin" -m pip install -e . -r packaging/macos/requirements-macos.txt
PYTHONPATH="src:tests:." "$python_bin" -m unittest discover -s tests -q
"$python_bin" scripts/capture_ui_matrix.py --validate-only
export MS_EVENT_STUDIO_VERSION="$version"
"$python_bin" -m desktop_bundle.build_desktop

dist_root="$repo_root/dist/macos"
app_path="$dist_root/MS-Event-Studio.app"
executable="$app_path/Contents/MacOS/MS-Event-Studio"
if [[ ! -x "$executable" ]]; then
  echo "The packaged application executable is missing: $executable" >&2
  exit 1
fi

codesign --force --deep --sign - "$app_path"
plutil -lint "$app_path/Contents/Info.plist"
file "$executable" | grep -q "arm64"
"$executable" --webview-smoke --smoke-report "$dist_root/smoke_test.json"
PYTHONPATH="src:." "$python_bin" - "$dist_root/smoke_test.json" <<'PY'
import json
from pathlib import Path
import sys

from desktop_bundle.webview_smoke import validate_webview_smoke_payload

validate_webview_smoke_payload(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
PY
# The executable smoke may create WebKit-owned files inside the bundle on some
# macOS versions. Seal and verify the exact tree that will be archived only
# after that probe, then hash the finalized bundle.
codesign --force --deep --sign - "$app_path"
codesign --verify --deep --strict "$app_path"
"$python_bin" -m desktop_bundle.build_desktop --refresh-manifest --dist-root "$dist_root"

release_root="$repo_root/release"
archive="$release_root/MS-Event-Studio-${version}-macos-arm64.zip"
checksum="${archive}.sha256"
mkdir -p "$repo_root/build/release-staging"
staging="$(mktemp -d "$repo_root/build/release-staging/macos.XXXXXX")"
trap 'rm -rf "$staging"' EXIT
bundle_root="$staging/MS-Event-Studio-${version}-macos-arm64"
mkdir -p "$bundle_root"
ditto "$app_path" "$bundle_root/MS-Event-Studio.app"
cp "$dist_root/build_manifest.json" "$bundle_root/"
cp "$dist_root/smoke_test.json" "$bundle_root/"
staged_archive="$staging/$(basename "$archive")"
staged_checksum="${staged_archive}.sha256"
ditto -c -k --sequesterRsrc --keepParent "$bundle_root" "$staged_archive"
unzip -tq "$staged_archive"
archive_name="$(basename "$archive")"
archive_hash="$(shasum -a 256 "$staged_archive" | awk '{print $1}')"
printf '%s  %s\n' "$archive_hash" "$archive_name" >"$staged_checksum"
mkdir -p "$release_root"
rm -f "$archive" "$checksum"
mv "$staged_archive" "$archive"
mv "$staged_checksum" "$checksum"

echo "Build complete: $archive"
