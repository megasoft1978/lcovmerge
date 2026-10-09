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
else
PLATFORM_SRC = src/platform_posix.c
ENTRY_SRC = src/platform_entry.c
LDFLAGS += -pthread
endif

SOURCES = src/lcovmerge.c $(PLATFORM_SRC) $(ENTRY_SRC)
HEADERS = include/platform.h include/version.h

.PHONY: all test test-docker asan fuzz bench dist clean install uninstall check-format

all: bin/lcovmerge

bin/lcovmerge: $(SOURCES) $(HEADERS) | bin
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SOURCES) $(LDFLAGS) -o $@

bin:
	mkdir -p $@

test: bin/lcovmerge
	$(PYTHON) tests/run_tests.py --binary bin/lcovmerge

test-docker:
	sh scripts/test-docker.sh

asan: | bin
	$(CC) $(CPPFLAGS) -O1 -g -std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Wstrict-prototypes -Werror \
		-fsanitize=address,undefined -fno-omit-frame-pointer $(SOURCES) $(LDFLAGS) -o bin/lcovmerge-asan
	ASAN_OPTIONS=halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
		$(PYTHON) tests/run_tests.py --binary bin/lcovmerge-asan --no-lcov

fuzz: | bin
	clang $(CPPFLAGS) -O1 -g -std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Wstrict-prototypes -Werror \
		-fsanitize=address,undefined -fno-omit-frame-pointer \
		src/lcovmerge.c src/platform_posix.c tests/fuzz/fuzz.c tests/fuzz/mutate.c -pthread -o bin/lcovmerge-fuzz
	ASAN_OPTIONS=halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
		bin/lcovmerge-fuzz tests/fuzz-corpus $${FUZZ_RUNS:-2000000}

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
	@if command -v clang-format >/dev/null 2>&1; then clang-format --dry-run --Werror $(SOURCES) $(HEADERS); \
	else echo 'clang-format unavailable; checked whitespace with git diff --check'; fi

clean:
	rm -rf bin dist
