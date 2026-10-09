#!/bin/sh
set -eu

command -v lcov >/dev/null 2>&1 || {
  printf 'lcov is required for the differential check.\n' >&2
  exit 1
}
make
binary=$(scripts/find-lcovmerge.sh)
python3 scripts/differential-lcov.py "$binary"
