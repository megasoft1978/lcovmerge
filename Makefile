PREFIX ?= /usr/local
DESTDIR ?=
CARGO ?= cargo
RUSTUP ?= rustup
PYTHON ?= python3
GIT_COMMIT ?= $(shell git rev-parse --short=12 HEAD 2>/dev/null || printf unknown)
export LCOVMERGE_GIT_COMMIT := $(GIT_COMMIT)
BIN ?= $(if $(filter Windows_NT,$(OS)),bin/lcovmerge.exe,bin/lcovmerge)
PTEST_BIN ?= $(if $(filter Windows_NT,$(OS)),bin/lcovmerge-ptest.exe,bin/lcovmerge-ptest)

.PHONY: all build test unit ptest asan tsan fuzz bench dist clean install uninstall check-format test-docker force

all: $(BIN)

build: $(BIN)

$(BIN): Cargo.toml Cargo.lock build.rs $(wildcard src/*.rs) force
	$(PYTHON) scripts/build-optimized.py --cargo "$(CARGO)" --output "$(BIN)"

$(PTEST_BIN): Cargo.toml Cargo.lock build.rs $(wildcard src/*.rs) force
	$(CARGO) build --locked --release --features test-partition --target-dir target/partition
	mkdir -p bin
	@binary_stage="$(PTEST_BIN).$$$$"; trap 'rm -f "$$binary_stage"' EXIT HUP INT TERM; \
		cp target/partition/release/lcovmerge$(if $(filter Windows_NT,$(OS)),.exe,) "$$binary_stage" && mv "$$binary_stage" "$(PTEST_BIN)"

ptest: $(PTEST_BIN)

test: $(BIN) $(PTEST_BIN)
	$(CARGO) test --locked
	$(PYTHON) tests/run_tests.py --binary $(BIN) --partition-binary $(PTEST_BIN)
	$(PYTHON) -m unittest discover -s tests/compat -p 'test_*.py'
	$(PYTHON) tests/test_compare_builds.py
	$(PYTHON) tests/test_profile_comparison.py
	$(PYTHON) tests/test_remote_native.py
	$(PYTHON) tests/test_native_confirmation.py
	$(PYTHON) tests/test_native_promotion.py
	$(PYTHON) tests/test_published_release.py
	$(PYTHON) tests/test_profile_fixtures.py
	$(PYTHON) tests/compat_operations.py --binary $(BIN)
	sh tests/doc-consistency.sh $(BIN)

unit:
	$(CARGO) test --locked

asan:
	RUSTFLAGS="-C opt-level=1 -C debuginfo=1 -C force-frame-pointers=yes -Z sanitizer=address" RUSTC_BOOTSTRAP=1 \
		$(CARGO) test --locked --target $$(rustc -vV | sed -n 's/^host: //p')
	RUSTFLAGS="-C opt-level=1 -C debuginfo=1 -C force-frame-pointers=yes -Z sanitizer=address" RUSTC_BOOTSTRAP=1 \
		$(CARGO) build --locked --features test-partition --target $$(rustc -vV | sed -n 's/^host: //p')
	ASAN_OPTIONS=halt_on_error=1 $(PYTHON) tests/run_tests.py \
		--binary target/$$(rustc -vV | sed -n 's/^host: //p')/debug/lcovmerge --no-lcov \
		--partition-binary target/$$(rustc -vV | sed -n 's/^host: //p')/debug/lcovmerge
	ASAN_OPTIONS=halt_on_error=1 $(PYTHON) tests/compat_operations.py \
		--binary target/$$(rustc -vV | sed -n 's/^host: //p')/debug/lcovmerge

tsan:
	$(RUSTUP) component add rust-src
	RUSTFLAGS="-C opt-level=1 -C debuginfo=1 -Z sanitizer=thread" RUSTDOCFLAGS="-Z sanitizer=thread" RUSTC_BOOTSTRAP=1 \
		$(CARGO) test --locked -Z build-std --target-dir target/tsan \
		--target $$(rustc -vV | sed -n 's/^host: //p') --features test-partition
	RUSTFLAGS="-C opt-level=1 -C debuginfo=1 -Z sanitizer=thread" RUSTC_BOOTSTRAP=1 \
		$(CARGO) build --locked -Z build-std --target-dir target/tsan \
		--target $$(rustc -vV | sed -n 's/^host: //p') --features test-partition
	TSAN_OPTIONS=halt_on_error=1 $(PYTHON) tests/run_tests.py --no-lcov \
		--binary target/tsan/$$(rustc -vV | sed -n 's/^host: //p')/debug/lcovmerge \
		--partition-binary target/tsan/$$(rustc -vV | sed -n 's/^host: //p')/debug/lcovmerge
	TSAN_OPTIONS=halt_on_error=1 $(PYTHON) tests/compat_operations.py \
		--binary target/tsan/$$(rustc -vV | sed -n 's/^host: //p')/debug/lcovmerge

FUZZ_SECONDS ?= 300
FUZZ_RUNS ?= 250000
fuzz: $(BIN)
	FUZZ_RUNS=$(FUZZ_RUNS) $(CARGO) test --locked --release --test rust_properties fuzz_corpus_mutations -- --ignored --nocapture
	$(PYTHON) scripts/fuzz-smoke.py $(BIN) --seconds $(shell expr $(FUZZ_SECONDS) / 2) --profile native
	$(PYTHON) scripts/fuzz-smoke.py $(BIN) --seconds $(shell expr $(FUZZ_SECONDS) - $(shell expr $(FUZZ_SECONDS) / 2)) --profile compat

bench: $(BIN)
	sh bench/run.sh

dist:
	sh scripts/build-all.sh

test-docker: dist
	sh scripts/test-docker.sh

install: $(BIN)
	install -d $(DESTDIR)$(PREFIX)/bin $(DESTDIR)$(PREFIX)/share/man/man1
	install -m 0755 $(BIN) $(DESTDIR)$(PREFIX)/bin/lcovmerge
	install -m 0644 man/lcovmerge.1 $(DESTDIR)$(PREFIX)/share/man/man1/lcovmerge.1

uninstall:
	rm -f $(DESTDIR)$(PREFIX)/bin/lcovmerge $(DESTDIR)$(PREFIX)/share/man/man1/lcovmerge.1

check-format:
	git diff --check
	$(CARGO) fmt --all -- --check
	$(CARGO) clippy --locked --all-targets --all-features -- -D warnings

clean:
	rm -rf bin dist target

force:
