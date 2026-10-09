#!/bin/sh
set -eu

make
make test
scripts/check-binary-size.sh
