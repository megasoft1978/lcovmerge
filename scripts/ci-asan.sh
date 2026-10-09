#!/bin/sh
set -eu

sanitizer_cflags='-std=c11 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer'
sanitizer_ldflags='-fsanitize=address,undefined'

make CFLAGS="$sanitizer_cflags" LDFLAGS="$sanitizer_ldflags"
make test CFLAGS="$sanitizer_cflags" LDFLAGS="$sanitizer_ldflags"
