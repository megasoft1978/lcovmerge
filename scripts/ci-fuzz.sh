#!/bin/sh
set -eu

make
binary=$(scripts/find-lcovmerge.sh)
python3 scripts/fuzz-smoke.py "$binary" --seconds 300
