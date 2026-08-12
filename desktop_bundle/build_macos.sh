#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
python_bin="${PYTHON_BIN:-python3}"
version="${MS_EVENT_STUDIO_VERSION:-dev-candidate}"

if [[ ! "$version" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
  echo "Version may contain only letters, digits, period, underscore, and hyphen (maximum 64 characters)." >&2
  exit 1
fi

cd "$repo_root"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS packaging must run on macOS." >&2
  exit 1
fi
machine="$($python_bin -c 'import platform; print(platform.machine().lower())')"
if [[ "$machine" != "arm64" ]]; then
  echo "macOS ARM64 packaging requires an arm64 Python interpreter; found: $machine" >&2
  exit 1
fi

"$python_bin" -m pip install --upgrade pip wheel setuptools
"$python_bin" -m pip install -e '.[packaging]'
PYTHONPATH="src:tests:." "$python_bin" -m unittest discover -s tests -q
"$python_bin" -m desktop_bundle.build_desktop

app_path="$repo_root/release/macos/MS-Event-Studio.app"
executable="$app_path/Contents/MacOS/MS-Event-Studio"
if [[ ! -x "$executable" ]]; then
  echo "The packaged application executable is missing: $executable" >&2
  exit 1
fi

codesign --force --deep --sign - "$app_path"
codesign --verify --deep --strict "$app_path"
plutil -lint "$app_path/Contents/Info.plist"
file "$executable" | grep -q "arm64"

archive="$repo_root/release/MS-Event-Studio-${version}-macos-arm64.zip"
checksum="${archive}.sha256"
rm -f "$archive" "$checksum"
staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
bundle_root="$staging/MS-Event-Studio-${version}-macos-arm64"
mkdir -p "$bundle_root"
ditto "$app_path" "$bundle_root/MS-Event-Studio.app"
cp "$repo_root/release/macos/build_manifest.json" "$bundle_root/"
cp "$repo_root/release/macos/smoke_test.json" "$bundle_root/"
ditto -c -k --sequesterRsrc --keepParent "$bundle_root" "$archive"
archive_name="$(basename "$archive")"
archive_hash="$(shasum -a 256 "$archive" | awk '{print $1}')"
printf '%s  %s\n' "$archive_hash" "$archive_name" >"$checksum"

echo "Build complete: $archive"
