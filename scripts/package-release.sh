#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s VERSION\n' "$0" >&2
  exit 2
fi
version=${1#v}
case "$version" in
  *[!A-Za-z0-9.+-]*|'')
    printf 'Invalid release version: %s\n' "$version" >&2
    exit 2
    ;;
esac

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"
build_dir=${LCOVMERGE_BUILD_DIR:-dist}
release_dir=${LCOVMERGE_RELEASE_DIR:-dist/release}
case "$release_dir" in
  /*) ;;
  *) release_dir="$repo_root/$release_dir" ;;
esac
docker_context=${LCOVMERGE_DOCKER_CONTEXT:-packaging/docker}
case "$docker_context" in
  /*) ;;
  *) docker_context="$repo_root/$docker_context" ;;
esac
stage_dir=$(mktemp -d "${TMPDIR:-/tmp}/lcovmerge-package.XXXXXX")
trap 'rm -rf "$stage_dir"' EXIT HUP INT TERM

mkdir -p "$release_dir"
rm -f "$release_dir"/lcovmerge-*.tar.gz "$release_dir"/lcovmerge-*.zip "$release_dir"/SHA256SUMS "$release_dir"/sbom.cdx.json "$release_dir"/RELEASE_NOTES.md "$release_dir"/SHA256SUMS.sigstore.json

package_tarball() {
  target=$1
  asset_arch=$2
  binary_name=lcovmerge
  source="$build_dir/lcovmerge-$version-$asset_arch"
  if [ ! -f "$source" ]; then
    printf 'Expected release binary is missing: %s\n' "$source" >&2
    exit 1
  fi
  scripts/check-binary-size.sh "$source"
  stage="$stage_dir/$target"
  mkdir -p "$stage"
  cp "$source" "$stage/$binary_name"
  chmod 755 "$stage/$binary_name"
  archive="$release_dir/lcovmerge-$version-$asset_arch.tar.gz"
  python3 scripts/create-tarball.py "$stage/$binary_name" "$archive"
}

package_tarball linux-x86_64 linux-x86_64
package_tarball linux-aarch64 linux-aarch64
package_tarball macos-arm64 macos-arm64
package_tarball macos-x86_64 macos-x86_64

windows_binary="$build_dir/lcovmerge-$version-windows-x86_64.exe"
if [ ! -f "$windows_binary" ]; then
  printf 'Expected release binary is missing: %s\n' "$windows_binary" >&2
  exit 1
fi
scripts/check-binary-size.sh "$windows_binary"
windows_stage="$stage_dir/windows-x86_64"
mkdir -p "$windows_stage"
cp "$windows_binary" "$windows_stage/lcovmerge.exe"
python3 - "$windows_stage/lcovmerge.exe" "$release_dir/lcovmerge-$version-windows-x86_64.zip" <<'PY'
import sys
import zipfile
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
info = zipfile.ZipInfo("lcovmerge.exe", date_time=(1980, 1, 1, 0, 0, 0))
info.compress_type = zipfile.ZIP_DEFLATED
info.external_attr = 0o100755 << 16
with zipfile.ZipFile(destination, "w") as archive:
    archive.writestr(info, source.read_bytes())
PY

(
  cd "$release_dir"
  python3 - <<'PY'
import hashlib
from pathlib import Path

files = sorted([*Path('.').glob('lcovmerge-*.tar.gz'), *Path('.').glob('lcovmerge-*.zip')])
with Path('SHA256SUMS').open('w', encoding='ascii', newline='\n') as output:
    for path in files:
        output.write(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
PY
)
scripts/update-packaging.sh "$release_dir/SHA256SUMS" "$version"
python3 scripts/write-sbom.py "$version" "$build_dir" "$release_dir/sbom.cdx.json"
python3 scripts/extract-release-notes.py "$version" CHANGELOG.md "$release_dir/RELEASE_NOTES.md"

rm -rf "$docker_context/amd64" "$docker_context/arm64"
mkdir -p "$docker_context/amd64" "$docker_context/arm64"
cp "$build_dir/lcovmerge-$version-linux-x86_64" "$docker_context/amd64/lcovmerge"
cp "$build_dir/lcovmerge-$version-linux-aarch64" "$docker_context/arm64/lcovmerge"
chmod 555 "$docker_context/amd64/lcovmerge" "$docker_context/arm64/lcovmerge"

printf 'Prepared release files in %s.\n' "$release_dir"
