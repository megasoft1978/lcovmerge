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
python3 "$script_dir/update-packaging.py" \
  "$sums_file" "$version" \
  "$repo_root/packaging/homebrew/lcovmerge.rb" \
  "$repo_root/packaging/scoop/lcovmerge.json"
