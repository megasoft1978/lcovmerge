#!/bin/sh
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  printf 'Usage: %s SHA256SUMS VERSION\n' "$0" >&2
  exit 2
fi

sums_file=$1
version=${2:-${LCOVMERGE_VERSION:-}}
if [ -z "$version" ]; then
  printf 'A release version is required as the second argument or LCOVMERGE_VERSION.\n' >&2
  exit 2
fi
version=${version#v}
case "$version" in
  *[!A-Za-z0-9.+-]*|'')
    printf 'Invalid release version: %s\n' "$version" >&2
    exit 2
    ;;
esac
if [ ! -f "$sums_file" ]; then
  printf 'SHA256SUMS file not found: %s\n' "$sums_file" >&2
  exit 1
fi

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH='' cd -- "$script_dir/.." && pwd)

hash_for() {
  asset=$1
  value=$(awk -v file="$asset" '$2 == file || $2 == "*" file { print $1; exit }' "$sums_file")
  case "$value" in
    *[!0123456789abcdefABCDEF]*|'')
      printf 'Missing or invalid SHA-256 entry for %s.\n' "$asset" >&2
      return 1
      ;;
  esac
  if [ "${#value}" -ne 64 ]; then
    printf 'Invalid SHA-256 length for %s.\n' "$asset" >&2
    return 1
  fi
  printf '%s\n' "$value"
}

mac_arm=$(hash_for "lcovmerge-$version-macos-arm64.tar.gz")
mac_intel=$(hash_for "lcovmerge-$version-macos-x86_64.tar.gz")
win_x64=$(hash_for "lcovmerge-$version-windows-x86_64.zip")

formula="$repo_root/packaging/homebrew/lcovmerge.rb"
scoop="$repo_root/packaging/scoop/lcovmerge.json"
formula_tmp=$(mktemp "$repo_root/packaging/homebrew/lcovmerge.rb.XXXXXX")
scoop_tmp=$(mktemp "$repo_root/packaging/scoop/lcovmerge.json.XXXXXX")
trap 'rm -f "$formula_tmp" "$scoop_tmp"' EXIT HUP INT TERM

awk \
  -v version="$version" \
  -v mac_arm="$mac_arm" \
  -v mac_intel="$mac_intel" \
  '{
    gsub(/@VERSION@/, version)
    gsub(/@SHA256_MACOS_ARM64@/, mac_arm)
    gsub(/@SHA256_MACOS_X86_64@/, mac_intel)
    print
  }' "$formula" > "$formula_tmp"

awk \
  -v version="$version" \
  -v win_x64="$win_x64" \
  '{
    gsub(/@VERSION@/, version)
    gsub(/@SHA256_WINDOWS_X86_64@/, win_x64)
    print
  }' "$scoop" > "$scoop_tmp"

if grep -q '@' "$formula_tmp" || grep -q '@' "$scoop_tmp"; then
  printf 'Unresolved packaging placeholder remains after update.\n' >&2
  exit 1
fi
mv "$formula_tmp" "$formula"
mv "$scoop_tmp" "$scoop"
printf 'Updated Homebrew and Scoop manifests for version %s.\n' "$version"
