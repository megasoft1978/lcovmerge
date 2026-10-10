#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -P "$(dirname "$0")" && pwd)
ROOT=$(CDPATH='' cd -P "$SCRIPT_DIR/.." && pwd)

usage() {
    echo "Usage: $0 [S|M] [OUTPUT_DIR]" >&2
    exit 2
}

[ "$#" -le 2 ] || usage
SIZE=${1:-S}
case "$SIZE" in
    S) SHARDS=8; FILES=56; LINES=23000 ;;
    M) SHARDS=32; FILES=220; LINES=16000 ;;
    *) usage ;;
esac

TMP_ROOT=${TMPDIR:-/tmp}
mkdir -p "$TMP_ROOT"
if [ "$#" -eq 2 ]; then
    OUT_DIR=$2
    mkdir -p "$OUT_DIR"
else
    OUT_DIR=$(mktemp -d "$TMP_ROOT/lcovmerge-reproduce.XXXXXX")
fi
OUT_DIR=$(CDPATH='' cd -P "$OUT_DIR" && pwd)
WORK_DIR=$(mktemp -d "$TMP_ROOT/lcovmerge-reproduce-work.XXXXXX")
REPORT=$OUT_DIR/report.txt
: > "$REPORT"
STATUS_WRAPPER=$WORK_DIR/run-command.sh
cat > "$STATUS_WRAPPER" <<'SH'
#!/bin/sh
"$@"
command_status=$?
if [ -n "${LCOVMERGE_MEASURE_STATUS:-}" ]; then
    printf '%s\n' "$command_status" > "$LCOVMERGE_MEASURE_STATUS"
fi
exit 0
SH
chmod +x "$STATUS_WRAPPER"

# shellcheck disable=SC2329
cleanup() {
    result=$?
    rm -rf "$WORK_DIR"
    exit "$result"
}
trap cleanup 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

OS_NAME=$(uname -s)
case "$OS_NAME" in
    Darwin) TIME_STYLE=darwin ;;
    Linux) TIME_STYLE=linux ;;
    *) echo "Unsupported host: $OS_NAME (expected macOS or Linux)" >&2; exit 2 ;;
esac
[ -x /usr/bin/time ] || { echo "/usr/bin/time is required" >&2; exit 2; }

quote_command() {
    python3 -c 'import shlex, sys; print(shlex.join(sys.argv[1:]))' "$@"
}

record_command() {
    command_label=$1
    shift
    command_text=$(quote_command "$@")
    printf 'command[%s]=%s\n' "$command_label" "$command_text" | tee -a "$REPORT"
}

record_version() {
    version_label=$1
    shift
    if version_output=$("$@" 2>&1); then
        version_status=0
    else
        version_status=$?
    fi
    version_output=$(printf '%s\n' "$version_output" | tr '\n' ' ' | sed 's/[[:space:]][[:space:]]*/ /g; s/^ //; s/ $//')
    [ -n "$version_output" ] || version_output="no version output (exit $version_status)"
    printf 'version[%s]=%s\n' "$version_label" "$version_output" | tee -a "$REPORT"
}

echo "lcovmerge reproducibility run" | tee -a "$REPORT"
printf 'date_utc=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" | tee -a "$REPORT"
printf 'host=%s\n' "$(uname -a)" | tee -a "$REPORT"
if [ "$OS_NAME" = Darwin ] && command -v sw_vers >/dev/null 2>&1; then
    printf 'host_version=%s\n' "$(sw_vers -productVersion)" | tee -a "$REPORT"
fi
printf 'architecture=%s\n' "$(uname -m)" | tee -a "$REPORT"
printf 'git_commit=%s\n' "$(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)" | tee -a "$REPORT"
printf 'workload=%s shards=%s files=%s lines=%s seed=4242 input_flags=--checksums --benchmark-compatible\n' \
    "$SIZE" "$SHARDS" "$FILES" "$LINES" | tee -a "$REPORT"
printf 'results_dir=%s\n' "$OUT_DIR" | tee -a "$REPORT"
printf 'timing_source=/usr/bin/time %s\n' "$([ "$TIME_STYLE" = darwin ] && echo '-l' || echo '-v')" | tee -a "$REPORT"
if [ "$TIME_STYLE" = darwin ]; then
    echo 'macOS timing wrapper records child status because denied kern.clockrate access can make /usr/bin/time return 1; RSS is n/a if time -l emits no peak value' | tee -a "$REPORT"
fi
printf '%s\n' 'cache_policy=no explicit cache flush; one run per available tool' | tee -a "$REPORT"

record_command build make -C "$ROOT" bin/lcovmerge
make -C "$ROOT" bin/lcovmerge

record_version lcovmerge "$ROOT/bin/lcovmerge" --version
record_version python3 python3 --version
record_version make make --version
if command -v cc >/dev/null 2>&1; then
    record_version compiler cc --version
else
    echo 'version[compiler]=unavailable' | tee -a "$REPORT"
fi
if command -v node >/dev/null 2>&1; then
    record_version node node --version
else
    echo 'version[node]=unavailable' | tee -a "$REPORT"
fi
if command -v npm >/dev/null 2>&1; then
    record_version npm npm --version
else
    echo 'version[npm]=unavailable' | tee -a "$REPORT"
fi
if command -v npx >/dev/null 2>&1; then
    record_version npx npx --version
else
    echo 'version[npx]=unavailable' | tee -a "$REPORT"
fi
if command -v lcov >/dev/null 2>&1; then
    LCOV_BIN=$(command -v lcov)
    record_version lcov "$LCOV_BIN" --version
else
    LCOV_BIN=
    echo 'version[lcov]=unavailable' | tee -a "$REPORT"
fi
if command -v lcov-result-merger >/dev/null 2>&1; then
    NODE_KIND=direct
    NODE_BIN=$(command -v lcov-result-merger)
    record_version lcov-result-merger "$NODE_BIN" --version
elif command -v npx >/dev/null 2>&1 && npx --no-install -- lcov-result-merger --version >/dev/null 2>&1; then
    NODE_KIND=npx
    NODE_BIN=npx
    record_version lcov-result-merger npx --no-install -- lcov-result-merger --version
else
    NODE_KIND=unavailable
    NODE_BIN=
    echo 'version[lcov-result-merger]=unavailable (neither executable nor locally available through npx)' | tee -a "$REPORT"
fi

GEN_DIR=$WORK_DIR/$SIZE
record_command fixture-generator python3 "$ROOT/tools/gen-lcov.py" --out "$GEN_DIR" \
    --shards "$SHARDS" --files "$FILES" --lines "$LINES" --seed 4242 --checksums --benchmark-compatible
python3 "$ROOT/tools/gen-lcov.py" --out "$GEN_DIR" --shards "$SHARDS" \
    --files "$FILES" --lines "$LINES" --seed 4242 --checksums --benchmark-compatible

INPUT_BYTES=0
INPUT_COUNT=0
for input_file in "$GEN_DIR"/*.info; do
    file_bytes=$(wc -c < "$input_file" | tr -d '[:space:]')
    INPUT_BYTES=$((INPUT_BYTES + file_bytes))
    INPUT_COUNT=$((INPUT_COUNT + 1))
done
printf 'generated_inputs=%s input_bytes=%s\n' "$INPUT_COUNT" "$INPUT_BYTES" | tee -a "$REPORT"

printf '\nMeasurements\n' | tee -a "$REPORT"
printf '%-20s %-9s %-14s %-12s %-14s %s\n' 'Tool' 'Size' 'Input bytes' 'Wall (s)' 'Peak RSS' 'Status' | tee -a "$REPORT"
printf '%-20s %-9s %-14s %-12s %-14s %s\n' '----' '----' '-----------' '--------' '--------' '------' | tee -a "$REPORT"

run_measurement() {
    measure_label=$1
    shift
    stdout_file=$OUT_DIR/$measure_label.stdout
    stderr_file=$OUT_DIR/$measure_label.stderr
    time_file=$OUT_DIR/$measure_label.time
    status_file=$WORK_DIR/$measure_label.status
    record_command "$measure_label" "$@"
    if [ "$TIME_STYLE" = darwin ]; then
        record_command "$measure_label-timed" /usr/bin/time -l "$STATUS_WRAPPER" "$@"
        rm -f "$status_file"
        if LCOVMERGE_MEASURE_STATUS="$status_file" /usr/bin/time -l "$STATUS_WRAPPER" "$@" > "$stdout_file" 2> "$time_file"; then
            time_status=0
        else
            time_status=$?
        fi
        if [ -f "$status_file" ]; then
            LAST_STATUS=$(cat "$status_file")
        else
            LAST_STATUS=$time_status
        fi
        cp "$time_file" "$stderr_file"
        LAST_WALL=$(awk '/real/ && $1 ~ /^[0-9]+([.][0-9]+)?$/ { print $1; exit }' "$time_file")
        LAST_RSS=$(awk '/maximum resident set size/ { print $1; exit }' "$time_file")
    else
        if /usr/bin/time -v -o "$time_file" "$@" > "$stdout_file" 2> "$stderr_file"; then
            LAST_STATUS=0
        else
            LAST_STATUS=$?
        fi
        LAST_WALL=$(awk -F': ' '/Elapsed \(wall clock\) time/ {
            value = $2; count = split(value, part, ":")
            if (count == 2) printf "%.3f", part[1] * 60 + part[2]
            else if (count == 3) printf "%.3f", part[1] * 3600 + part[2] * 60 + part[3]
            exit
        }' "$time_file")
        LAST_RSS=$(awk -F': *' '/Maximum resident set size \(kbytes\)/ { printf "%.0f", $2 * 1024; exit }' "$time_file")
    fi
    [ -n "$LAST_WALL" ] || LAST_WALL=n/a
    [ -n "$LAST_RSS" ] || LAST_RSS=n/a
    if [ "$LAST_RSS" = n/a ]; then
        rss_display=n/a
    else
        rss_display=$(awk -v bytes="$LAST_RSS" 'BEGIN { printf "%.2f MiB", bytes / 1048576 }')
    fi
    if [ "$LAST_STATUS" -eq 0 ]; then
        LAST_RESULT=OK
    else
        LAST_RESULT="ERROR_$LAST_STATUS"
        FAILED=1
    fi
    row=$(printf '%-20s %-9s %-14s %-12s %-14s %s' "$measure_label" "$SIZE" \
        "$INPUT_BYTES" "$LAST_WALL" "$rss_display" "$LAST_RESULT")
    printf '%s\n' "$row" | tee -a "$REPORT"
}

FAILED=0
set -- "$ROOT/bin/lcovmerge" -o "$OUT_DIR/lcovmerge-$SIZE.info"
for input_file in "$GEN_DIR"/*.info; do
    set -- "$@" "$input_file"
done
run_measurement lcovmerge "$@"
LCOVMERGE_STATUS=$LAST_STATUS

if [ -n "$LCOV_BIN" ]; then
    set -- "$LCOV_BIN" --branch-coverage --function-coverage
    for input_file in "$GEN_DIR"/*.info; do
        set -- "$@" -a "$input_file"
    done
    set -- "$@" -o "$OUT_DIR/lcov-$SIZE.info"
    run_measurement lcov "$@"
    LCOV_STATUS=$LAST_STATUS
    if [ "$LCOV_STATUS" -eq 0 ] && [ "$LCOVMERGE_STATUS" -eq 0 ]; then
        if python3 - "$OUT_DIR/lcovmerge-$SIZE.info" "$OUT_DIR/lcov-$SIZE.info" <<'PY'
from collections import defaultdict
from pathlib import Path
import posixpath
import sys


def normalize(path):
    return posixpath.normpath(path.replace("\\", "/"))


def parse(path):
    files = {}
    current = None
    for raw in Path(path).read_text(encoding="utf-8", errors="strict").splitlines():
        if raw.startswith("SF:"):
            current = files.setdefault(normalize(raw[3:]), {
                "DA": defaultdict(int), "FN": set(), "FNDA": defaultdict(int),
                "BRDA": defaultdict(lambda: [0, True]), "_FNL": {},
            })
        elif raw.startswith("DA:") and current is not None:
            fields = raw[3:].split(",", 2)
            current["DA"][int(fields[0])] += int(fields[1])
        elif raw.startswith("FN:") and current is not None:
            fields = raw[3:].split(",", 2)
            if len(fields) == 2:
                start, name = int(fields[0]), fields[1]
            else:
                start, name = int(fields[0]), fields[2]
            current["FN"].add((start, name))
        elif raw.startswith("FNDA:") and current is not None:
            count, name = raw[5:].split(",", 1)
            current["FNDA"][name] += int(count)
        elif raw.startswith("FNL:") and current is not None:
            fields = raw[4:].split(",")
            current["_FNL"][int(fields[0])] = (int(fields[1]), int(fields[2]) if len(fields) > 2 else 0)
        elif raw.startswith("FNA:") and current is not None:
            index, count, name = raw[4:].split(",", 2)
            start, _end = current["_FNL"][int(index)]
            current["FN"].add((start, name))
            current["FNDA"][name] += int(count)
        elif raw.startswith("BRDA:") and current is not None:
            fields, count_text = raw[5:].rsplit(",", 1)
            line, block, branch = fields.split(",", 2)
            key = (int(line), block, branch)
            value = current["BRDA"][key]
            if count_text != "-":
                value[0] += int(count_text)
                value[1] = False
    return {
        source: {kind: dict(values) if kind != "FN" else values
                 for kind, values in records.items() if not kind.startswith("_")}
        for source, records in files.items()
    }


left, right = map(parse, sys.argv[1:])
differences = []
for source in sorted(set(left) | set(right)):
    if source not in left or source not in right:
        differences.append((source, "SF", "present" if source in left else "missing",
                            "present" if source in right else "missing"))
        continue
    for kind in ("DA", "FN", "FNDA", "BRDA"):
        left_rows, right_rows = left[source][kind], right[source][kind]
        for key in sorted(set(left_rows) | set(right_rows), key=repr):
            if kind == "FN":
                left_value, right_value = key in left_rows, key in right_rows
            else:
                left_value, right_value = left_rows.get(key), right_rows.get(key)
            if left_value != right_value:
                differences.append((source, kind, repr(key), repr(left_value), repr(right_value)))
if differences:
    print(f"semantic_compare=FAIL differences={len(differences)}")
    for difference in differences[:20]:
        print("difference=" + " | ".join(map(str, difference)))
    raise SystemExit(1)
print(f"semantic_compare=PASS sf_paths={len(left)} keyed_records=DA,FN,FNDA,BRDA")
PY
        then
            echo "comparison=PASS normalized SF paths and keyed DA/FN/FNDA/BRDA rows and counts" | tee -a "$REPORT"
        else
            echo "comparison=FAIL normalized LCOV result differs; see command output above" | tee -a "$REPORT"
            FAILED=1
        fi
    else
        echo "comparison=NOT RUN because lcov or lcovmerge returned a non-zero status" | tee -a "$REPORT"
        FAILED=1
    fi
else
    echo 'comparison=NOT RUN (lcov unavailable)' | tee -a "$REPORT"
fi

if [ "$NODE_KIND" = direct ]; then
    set -- "$NODE_BIN" "$GEN_DIR"/'*.info' "$OUT_DIR/lcov-result-merger-$SIZE.info"
    run_measurement lcov-result-merger "$@"
elif [ "$NODE_KIND" = npx ]; then
    set -- npx --no-install -- lcov-result-merger "$GEN_DIR"/'*.info' "$OUT_DIR/lcov-result-merger-$SIZE.info"
    run_measurement lcov-result-merger "$@"
else
    printf '%-20s %-9s %-14s %-12s %-14s %s\n' 'lcov-result-merger' "$SIZE" \
        "$INPUT_BYTES" n/a n/a UNAVAILABLE | tee -a "$REPORT"
fi

printf '\n%s\n' "Detailed results, logs, and successful merged outputs are in $OUT_DIR." | tee -a "$REPORT"
printf '%s\n' 'Generated input shards were removed when this script exits.' | tee -a "$REPORT"
exit "$FAILED"
