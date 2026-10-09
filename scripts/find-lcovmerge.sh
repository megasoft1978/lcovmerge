#!/bin/sh
set -eu

if [ "$#" -gt 1 ]; then
  printf 'Usage: %s [binary-path]\n' "$0" >&2
  exit 1
fi

if [ "$#" -eq 1 ]; then
  binary=$1
  if [ ! -f "$binary" ]; then
    printf 'Binary not found: %s\n' "$binary" >&2
    exit 1
  fi
  printf '%s\n' "$binary"
  exit 0
fi

if [ -n "${LCOVMERGE_BINARY:-}" ]; then
  if [ ! -f "$LCOVMERGE_BINARY" ]; then
    printf 'LCOVMERGE_BINARY not found: %s\n' "$LCOVMERGE_BINARY" >&2
    exit 1
  fi
  printf '%s\n' "$LCOVMERGE_BINARY"
  exit 0
fi

matches=$(find ./bin -maxdepth 1 -type f \( -name lcovmerge -o -name lcovmerge.exe \) -print 2>/dev/null | LC_ALL=C sort)
count=$(printf '%s\n' "$matches" | awk 'NF { count++ } END { print count + 0 }')
if [ "$count" -ne 1 ]; then
  printf 'Expected one built lcovmerge binary, found %s. Set LCOVMERGE_BINARY to select one.\n' "$count" >&2
  if [ -n "$matches" ]; then
    printf '%s\n' "$matches" >&2
  fi
  exit 1
fi
printf '%s\n' "$matches"
