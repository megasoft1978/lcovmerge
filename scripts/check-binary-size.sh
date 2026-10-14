#!/bin/sh
set -eu

# Rust standard-library builds are larger than the former C binaries; retain a
# 1 MiB ceiling to catch accidental debug or unstripped release artifacts.
limit_bytes=1048576

if [ "$#" -eq 0 ]; then
  # Inspect installed release outputs, never Cargo's debug/test executables.
  for binary in bin/lcovmerge bin/lcovmerge.exe; do
    if [ -f "$binary" ]; then set -- "$@" "$binary"; fi
  done
fi

if [ "$#" -eq 0 ]; then
  printf 'No lcovmerge binaries found for the size-budget check.\n' >&2
  exit 1
fi

status=0
for binary do
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
