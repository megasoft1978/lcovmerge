PREFIX ?= /usr/local
DESTDIR ?=
CC ?= cc
AR ?= ar
PYTHON ?= python3
GIT_COMMIT ?= $(shell git rev-parse --short=12 HEAD 2>/dev/null || printf unknown)
CFLAGS ?= -O2
CFLAGS += -std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Wstrict-prototypes -Werror
CFLAGS += -fstack-protector-strong -D_FORTIFY_SOURCE=2
CPPFLAGS += -Iinclude -DLCOVMERGE_GIT_COMMIT=\"$(GIT_COMMIT)\"
LDFLAGS ?=

ifeq ($(OS),Windows_NT)
PLATFORM_SRC = src/platform_win32.c
ENTRY_SRC =
LDFLAGS += -municode
BIN ?= bin/lcovmerge.exe
UNIT_BIN ?= bin/lcovmerge-unit.exe
PTEST_BIN ?= bin/lcovmerge-ptest.exe
UNIT_LDFLAGS =
else
PLATFORM_SRC = src/platform_posix.c
ENTRY_SRC = src/platform_entry.c
LDFLAGS += -pthread
BIN ?= bin/lcovmerge
UNIT_BIN ?= bin/lcovmerge-unit
PTEST_BIN ?= bin/lcovmerge-ptest
UNIT_LDFLAGS = -pthread
endif

CORE_SRC = src/lcovmerge.c \
	src/lm_util.c \
	src/lm_io.c \
	src/lm_record.c \
	src/lm_path.c \
	src/lm_chunk.c \
	src/lm_emit.c \
	src/lm_parse.c \
	src/lm_direct.c \
	src/lm_partition.c \
	src/lm_direct_par.c \
	src/lm_merge.c
SOURCES = $(CORE_SRC) $(PLATFORM_SRC) $(ENTRY_SRC)
HEADERS = include/platform.h include/version.h src/lm_internal.h
UNIT_SOURCE = tests/unit/helpers.c
UNIT_CPPFLAGS = -Dlcovmerge_main=lcovmerge_unit_unused_main -Dwmain=lcovmerge_unit_unused_wmain
PTEST_CPPFLAGS = -DLM_TEST_PARTITION
TSAN_CFLAGS = -O1 -g -std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Wstrict-prototypes -Werror \
	-fsanitize=thread -fno-omit-frame-pointer
ASAN_CFLAGS = -O1 -g -std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Wstrict-prototypes -Werror \
	-fsanitize=address,undefined -fno-omit-frame-pointer
.PHONY: all test unit ptest tsan test-docker asan fuzz fuzz-guided bench dist clean install uninstall check-format

all: $(BIN)

$(BIN): $(SOURCES) $(HEADERS) | bin
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SOURCES) $(LDFLAGS) -o $@

bin:
	mkdir -p $@

# Test-only build: LCOVMERGE_TEST_PARTITION=P forces P partitions of the threaded direct path on tiny
# inputs. The hook is compiled in only here; the shipped binary has no such switch.
$(PTEST_BIN): $(SOURCES) $(HEADERS) | bin
	$(CC) $(CPPFLAGS) $(PTEST_CPPFLAGS) $(CFLAGS) $(SOURCES) $(LDFLAGS) -o $@

ptest: $(PTEST_BIN)

test: $(BIN) $(UNIT_BIN) $(PTEST_BIN)
	./$(UNIT_BIN)
	$(PYTHON) tests/run_tests.py --binary $(BIN) --partition-binary $(PTEST_BIN)
	sh tests/doc-consistency.sh $(BIN)

unit: $(UNIT_BIN)
	./$(UNIT_BIN)

$(UNIT_BIN): $(UNIT_SOURCE) $(CORE_SRC) $(PLATFORM_SRC) $(HEADERS) | bin
	$(CC) $(CPPFLAGS) $(CFLAGS) $(UNIT_CPPFLAGS) $(UNIT_SOURCE) $(CORE_SRC) $(PLATFORM_SRC) $(UNIT_LDFLAGS) -o $@

test-docker: dist
	sh scripts/test-docker.sh

asan: | bin
	$(CC) $(CPPFLAGS) $(ASAN_CFLAGS) $(SOURCES) $(LDFLAGS) -o bin/lcovmerge-asan
	$(CC) $(CPPFLAGS) $(PTEST_CPPFLAGS) $(ASAN_CFLAGS) $(SOURCES) $(LDFLAGS) -o bin/lcovmerge-ptest-asan
	$(CC) $(CPPFLAGS) $(ASAN_CFLAGS) $(UNIT_CPPFLAGS) $(UNIT_SOURCE) $(CORE_SRC) $(PLATFORM_SRC) $(UNIT_LDFLAGS) -o bin/lcovmerge-unit-asan
	ASAN_OPTIONS=halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
		bin/lcovmerge-unit-asan
	ASAN_OPTIONS=halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
		$(PYTHON) tests/run_tests.py --binary bin/lcovmerge-asan --no-lcov --partition-binary bin/lcovmerge-ptest-asan

# ThreadSanitizer: unit tests plus the core suite (forced partitions on the test build, -j explicit).
tsan: | bin
	$(CC) $(CPPFLAGS) $(PTEST_CPPFLAGS) $(TSAN_CFLAGS) $(SOURCES) $(LDFLAGS) -o bin/lcovmerge-ptest-tsan
	TSAN_OPTIONS=halt_on_error=1 $(PYTHON) tests/run_tests.py --binary bin/lcovmerge-ptest-tsan --no-lcov \
		--partition-binary bin/lcovmerge-ptest-tsan

fuzz: | bin
	clang $(CPPFLAGS) $(PTEST_CPPFLAGS) -O1 -g -std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Wstrict-prototypes -Werror \
		-fsanitize=address,undefined -fno-omit-frame-pointer \
		$(CORE_SRC) src/platform_posix.c tests/fuzz/fuzz.c tests/fuzz/mutate.c -pthread -o bin/lcovmerge-fuzz
	ASAN_OPTIONS=halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
		bin/lcovmerge-fuzz tests/fuzz-corpus $${FUZZ_RUNS:-2000000}

FUZZ_TIME ?= 10
fuzz-guided: | bin
	mkdir -p .cache/fuzz-artifacts
	clang $(CPPFLAGS) -DLCOVMERGE_LIBFUZZER -O1 -g -std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Wstrict-prototypes -Werror \
		-fsanitize=fuzzer,address,undefined -fno-omit-frame-pointer \
		$(CORE_SRC) src/platform_posix.c tests/fuzz/fuzz.c -pthread -o bin/lcovmerge-libfuzz
	ASAN_OPTIONS=halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
		bin/lcovmerge-libfuzz tests/fuzz-corpus -max_len=1048576 -max_total_time=$(FUZZ_TIME) \
		-artifact_prefix=.cache/fuzz-artifacts/

bench: bin/lcovmerge
	sh bench/run.sh

dist:
	sh scripts/build-all.sh

install: bin/lcovmerge
	install -d $(DESTDIR)$(PREFIX)/bin $(DESTDIR)$(PREFIX)/share/man/man1
	install -m 0755 bin/lcovmerge $(DESTDIR)$(PREFIX)/bin/lcovmerge
	install -m 0644 man/lcovmerge.1 $(DESTDIR)$(PREFIX)/share/man/man1/lcovmerge.1

uninstall:
	rm -f $(DESTDIR)$(PREFIX)/bin/lcovmerge $(DESTDIR)$(PREFIX)/share/man/man1/lcovmerge.1

check-format:
	git diff --check
	@if command -v clang-format >/dev/null 2>&1; then clang-format --dry-run --Werror $(SOURCES) $(HEADERS) $(UNIT_SOURCE); \
	else echo 'clang-format unavailable; checked whitespace with git diff --check'; fi

clean:
	rm -rf bin dist
