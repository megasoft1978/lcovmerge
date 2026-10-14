#!/bin/sh
set -eu

make fuzz FUZZ_SECONDS="${FUZZ_SECONDS:-300}"
