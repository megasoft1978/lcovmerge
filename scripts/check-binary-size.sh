#!/bin/sh
set -eu

limit_bytes=160000

if [ "$#" -gt 0 ]; then
  binaries=$*
else
  binaries=$(find . -type f \( -name lcovmerge -o -name lcovmerge.exe \) -not -path './.git/*' -print | LC_ALL=C sort)
fi

if [ -z "$binaries" ]; then
  printf 'No lcovmerge binaries found for the size-budget check.\n' >&2
  exit 1
fi

status=0
for binary in $binaries; do
  if [ ! -f "$binary" ]; then
    printf 'Binary not found: %s\n' "$binary" >&2
    status=1
    continue
  fi
  size=$(wc -c < "$binary" | tr -d '[:space:]')
  printf '%s: %s bytes (limit %s)\n' "$binary" "$size" "$limit_bytes"
  if [ "$size" -gt "$limit_bytes" ]; then
    printf 'Size budget exceeded: %s is %s bytes; limit is %s bytes.\n' \
      "$binary" "$size" "$limit_bytes" >&2
    status=1
  fi
done
exit "$status"
