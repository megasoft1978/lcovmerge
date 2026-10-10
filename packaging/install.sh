#!/bin/sh
set -eu

PROGRAM=lcovmerge
VERSION=${LCOVMERGE_VERSION:-1.0.2}
VERSION=${VERSION#v}
case "$VERSION" in
  *[!A-Za-z0-9.+-]*|'')
    printf 'Invalid LCOVMERGE_VERSION: %s\n' "$VERSION" >&2
    exit 1
    ;;
esac
BASE_URL=${LCOVMERGE_BASE_URL:-"https://github.com/megasoft1978/lcovmerge/releases/download/v$VERSION"}
BASE_URL=${BASE_URL%/}

os_name=$(uname -s)
machine=$(uname -m)

case "$os_name" in
  Linux) target_os=linux ;;
  Darwin) target_os=macos ;;
  *)
    printf 'Unsupported operating system: %s\n' "$os_name" >&2
    exit 1
    ;;
esac

case "$machine" in
  x86_64|amd64) target_arch=x86_64 ;;
  aarch64|arm64)
    if [ "$target_os" = macos ]; then
      target_arch=arm64
    else
      target_arch=aarch64
    fi
    ;;
  *)
    printf 'Unsupported architecture: %s\n' "$machine" >&2
    exit 1
    ;;
esac

asset="$PROGRAM-$VERSION-$target_os-$target_arch.tar.gz"
work_dir=$(mktemp -d "${TMPDIR:-/tmp}/lcovmerge-install.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT HUP INT TERM

download() {
  url=$1
  destination=$2
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --silent --show-error --retry 3 "$url" --output "$destination"
  elif command -v wget >/dev/null 2>&1; then
    wget -q "$url" -O "$destination"
  else
    printf 'curl or wget is required to download lcovmerge.\n' >&2
    exit 1
  fi
}

download "$BASE_URL/$asset" "$work_dir/$asset"
download "$BASE_URL/SHA256SUMS" "$work_dir/SHA256SUMS"

expected=$(awk -v file="$asset" '$2 == file || $2 == "*" file { print $1; exit }' "$work_dir/SHA256SUMS")
case "$expected" in
  *[!0123456789abcdefABCDEF]*|'')
    printf 'No valid SHA-256 entry for %s in SHA256SUMS.\n' "$asset" >&2
    exit 1
    ;;
esac
if [ "${#expected}" -ne 64 ]; then
  printf 'Invalid SHA-256 length for %s in SHA256SUMS.\n' "$asset" >&2
  exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$work_dir/$asset" | awk '{ print $1 }')
elif command -v shasum >/dev/null 2>&1; then
  actual=$(shasum -a 256 "$work_dir/$asset" | awk '{ print $1 }')
else
  printf 'sha256sum or shasum is required to verify the release.\n' >&2
  exit 1
fi
if [ "$(printf '%s' "$actual" | tr 'A-F' 'a-f')" != "$(printf '%s' "$expected" | tr 'A-F' 'a-f')" ]; then
  printf 'SHA-256 verification failed for %s.\n' "$asset" >&2
  exit 1
fi

tar -xzf "$work_dir/$asset" -C "$work_dir" "$PROGRAM"
if [ -n "${PREFIX:-}" ]; then
  install_dir=$PREFIX/bin
else
  install_dir=${HOME:?HOME must be set when PREFIX is unset}/.local/bin
fi
mkdir -p "$install_dir"
if command -v install >/dev/null 2>&1; then
  install -m 755 "$work_dir/$PROGRAM" "$install_dir/$PROGRAM"
else
  cp "$work_dir/$PROGRAM" "$install_dir/$PROGRAM"
  chmod 755 "$install_dir/$PROGRAM"
fi
printf 'Installed %s %s to %s\n' "$PROGRAM" "$VERSION" "$install_dir/$PROGRAM"
