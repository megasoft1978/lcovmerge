#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
binary=${1:-"$script_dir/../bin/lcovmerge"}
exec python3 "$script_dir/run_tests.py" --binary "$binary"
