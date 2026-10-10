#!/usr/bin/env python3
"""Collect repeatable Windows benchmark measurements for two lcovmerge builds."""

from __future__ import annotations

import argparse
import ctypes
import datetime
import json
import os
import pathlib
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / ".luna-tmp" / "windows-benchmark-artifact"
WORKLOADS = {
    "S": (8, 56, 23000, 5),
    "M": (32, 220, 16000, 3),
    "L": (64, 480, 16000, 2),
}
TOOLS = (
    ("lcovmerge", "Zig -O2"),
    ("lcovmerge_ucrt64_gcc", "MSYS2 UCRT64 GCC -O2"),
)


def command_output(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, check=False)
    except OSError:
        return "unavailable"
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "unavailable"


def first_line(value: str) -> str:
    return value.splitlines()[0] if value and value != "unavailable" else value


def powershell_value(script: str) -> str:
    executable = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not executable:
        return "unavailable"
    value = command_output([executable, "-NoLogo", "-NoProfile", "-NonInteractive",
                            "-Command", script])
    return first_line(value)


def memory_bytes() -> int | None:
    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = kernel32.GlobalMemoryStatusEx
        function.argtypes = [ctypes.POINTER(MemoryStatusEx)]
        function.restype = wintypes.BOOL
        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        return int(status.ullTotalPhys) if function(ctypes.byref(status)) else None
    except (AttributeError, OSError):
        return None


class PeakWorkingSet:
    """Read the Windows process lifetime peak working set without third-party tools."""

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    def __init__(self, process_id: int) -> None:
        self.handle: Any = None
        self.kernel32: Any = None
        self.psapi: Any = None
        self.available = False
        try:
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self.psapi = ctypes.WinDLL("psapi", use_last_error=True)
            self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel32.OpenProcess.restype = wintypes.HANDLE
            self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel32.CloseHandle.restype = wintypes.BOOL
            self.psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(self.Counters), wintypes.DWORD,
            ]
            self.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            self.handle = self.kernel32.OpenProcess(
                self.PROCESS_QUERY_INFORMATION | self.PROCESS_VM_READ, False, process_id)
            self.available = bool(self.handle)
        except (AttributeError, OSError):
            self.available = False

    def read(self) -> int | None:
        if not self.available or not self.handle or not self.psapi:
            return None
        counters = self.Counters()
        counters.cb = ctypes.sizeof(counters)
        if not self.psapi.GetProcessMemoryInfo(
                self.handle, ctypes.byref(counters), ctypes.sizeof(counters)):
            return None
        return int(counters.PeakWorkingSetSize)

    def close(self) -> None:
        if self.handle and self.kernel32:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None


def host_metadata(zig_compiler: str, gcc_compiler: str) -> dict[str, object]:
    zig_version = command_output([zig_compiler, "version"])
    gcc_version = command_output([gcc_compiler, "--version"])
    cpu = powershell_value(
        "(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)")
    os_build = powershell_value(
        "(Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty BuildNumber)")
    return {
        "runner_label": os.environ.get("BENCH_RUNNER_LABEL", "windows-latest"),
        "os": platform.platform(),
        "kernel": platform.release(),
        "os_build": os_build if os_build != "unavailable" else platform.version(),
        "architecture": platform.machine(),
        "cpu_model": cpu,
        "logical_cores": os.cpu_count(),
        "memory_bytes": memory_bytes(),
        "compiler": f"Zig {zig_version}; {first_line(gcc_version)}",
        "zig": zig_version,
        "gcc": first_line(gcc_version),
        "lcov": "not measured (this run compares lcovmerge builds only)",
        "rss_tool": "Windows GetProcessMemoryInfo PeakWorkingSetSize",
    }


def source_metadata() -> dict[str, object]:
    revision = command_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    return {
        "repository": os.environ.get("GITHUB_REPOSITORY", "local"),
        "revision": revision,
        "workflow": os.environ.get("GITHUB_WORKFLOW", "manual local run"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    }


def generate_dataset(name: str, directory: pathlib.Path) -> list[pathlib.Path]:
    shards, files, lines, _ = WORKLOADS[name]
    directory.mkdir(parents=True)
    command = [
        sys.executable, "-I", str(ROOT / "tools" / "gen-lcov.py"),
        "--out", str(directory),
        "--shards", str(shards),
        "--files", str(files),
        "--lines", str(lines),
        "--seed", "4242",
        "--checksums",
        "--benchmark-compatible",
    ]
    subprocess.run(command, cwd=ROOT, env=os.environ.copy(), check=True)
    paths = sorted(directory.glob("*.info"))
    if len(paths) != shards:
        raise RuntimeError(f"{name}: expected {shards} generated shards, found {len(paths)}")
    return paths


def input_size(paths: list[pathlib.Path]) -> int:
    return sum(path.stat().st_size for path in paths)


def timed_run(command: list[str], cwd: pathlib.Path, env: dict[str, str],
              log_path: pathlib.Path, timeout: int) -> dict[str, object]:
    start = time.perf_counter()
    peak_values: list[int] = []
    timed_out = False
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, cwd=cwd, env=env,
                                   stdout=log, stderr=subprocess.STDOUT)
        sampler = PeakWorkingSet(process.pid)
        try:
            while True:
                peak = sampler.read()
                if peak is not None:
                    peak_values.append(peak)
                try:
                    exit_code = process.wait(timeout=0.025)
                    break
                except subprocess.TimeoutExpired:
                    if time.perf_counter() - start >= timeout:
                        timed_out = True
                        process.kill()
                        exit_code = process.wait()
                        break
            peak = sampler.read()
            if peak is not None:
                peak_values.append(peak)
        finally:
            sampler.close()
    elapsed = time.perf_counter() - start
    return {
        "time_s": round(elapsed, 6),
        "exit_code": 124 if timed_out else exit_code,
        "rss_bytes": max(peak_values) if peak_values else None,
        "rss_available": bool(peak_values),
        "log": log_path,
    }


def result_status(exit_codes: list[int]) -> tuple[str, int]:
    for code in exit_codes:
        if code != 0:
            return ("TIMEOUT" if code == 124 else f"ERROR_{code}"), code
    return "OK", 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--zig-binary", type=pathlib.Path,
                        default=ROOT / "dist" / "lcovmerge-1.0.1-windows-x86_64.exe")
    parser.add_argument("--gcc-binary", type=pathlib.Path,
                        default=ROOT / "bin" / "lcovmerge-ucrt64-gcc.exe")
    parser.add_argument("--zig-compiler", default="zig.exe",
                        help="Zig executable used to build the release binary")
    parser.add_argument("--gcc-compiler", default="gcc.exe",
                        help="UCRT64 GCC executable used to build the comparison binary")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="timeout in seconds for each individual invocation")
    args = parser.parse_args()
    if platform.system() != "Windows":
        parser.error("Windows CI measurements require a Windows host")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    zig_binary = args.zig_binary.resolve()
    gcc_binary = args.gcc_binary.resolve()
    for binary in (zig_binary, gcc_binary):
        if not binary.is_file():
            parser.error(f"benchmark binary not found: {binary}")

    scratch = ROOT / ".luna-tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(scratch)
    os.environ["TMP"] = str(scratch)
    os.environ["TEMP"] = str(scratch)
    os.environ.setdefault("LC_ALL", "C")
    env = os.environ.copy()

    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        parser.error(f"refusing to overwrite non-empty artifact directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    host = host_metadata(args.zig_compiler, args.gcc_compiler)
    required_host = ("cpu_model", "logical_cores", "memory_bytes", "os_build", "zig", "gcc")
    if any(host.get(key) in (None, "", "unavailable") for key in required_host):
        parser.error("Windows host metadata is incomplete: " + ", ".join(
            key for key in required_host if host.get(key) in (None, "", "unavailable")))

    datasets: list[dict[str, object]] = []
    report = [
        "Windows benchmark artifact",
        f"measurement_date={datetime.datetime.now(datetime.timezone.utc).date().isoformat()}",
        f"host={json.dumps(host, sort_keys=True)}",
        f"source={json.dumps(source_metadata(), sort_keys=True)}",
        "generator=tools/gen-lcov.py --benchmark-compatible --checksums (native Windows Python)",
        "method=one warmup per binary; measured order alternates; median and minimum wall time of listed repeats",
        "rss=Windows GetProcessMemoryInfo PeakWorkingSetSize; max across measured runs; unknown unless every measured run is sampled",
        "temporary_data=generated inputs, outputs, sort runs, and logs are under .luna-tmp and removed after each dataset",
        "cache_state=no explicit cache flush; both binaries receive a warmup; runner load is uncontrolled",
        "",
    ]
    status = 0
    with tempfile.TemporaryDirectory(prefix="windows-benchmark-", dir=scratch) as temp_name:
        work = pathlib.Path(temp_name)
        for workload, (_, _, _, repeat_count) in WORKLOADS.items():
            generated = work / "generated" / workload
            paths = generate_dataset(workload, generated)
            (work / "logs").mkdir(parents=True, exist_ok=True)
            total_bytes = input_size(paths)
            dataset: dict[str, object] = {
                "name": workload,
                "input_bytes": total_bytes,
                "shards": len(paths),
                "description": "Generated sharded LCOV input.",
                "results": {},
            }
            result_map = dataset["results"]
            rows_by_tool: dict[str, list[str]] = {}

            # Warm both binaries before recording samples. The measured order then
            # alternates so neither build always runs first on a warm input cache.
            warmups: dict[str, dict[str, object]] = {}
            for key, _ in TOOLS:
                output = work / "output" / f"{workload}-{key}.info"
                tmpdir = work / "sort" / f"{workload}-{key}"
                tmpdir.mkdir(parents=True, exist_ok=True)
                output.parent.mkdir(parents=True, exist_ok=True)
                command = [str(zig_binary if key == "lcovmerge" else gcc_binary),
                           "--tmpdir", str(tmpdir), "-o", str(output), *map(str, paths)]
                warmups[key] = timed_run(command, ROOT, env,
                                         work / "logs" / f"{workload}-{key}-warmup.txt", args.timeout)
                try:
                    output.unlink()
                except FileNotFoundError:
                    pass

            samples: dict[str, list[dict[str, object]]] = {key: [] for key, _ in TOOLS}
            for sample_index in range(repeat_count):
                iteration = TOOLS if sample_index % 2 == 0 else tuple(reversed(TOOLS))
                for key, label in iteration:
                    binary = zig_binary if key == "lcovmerge" else gcc_binary
                    output = work / "output" / f"{workload}-{key}.info"
                    tmpdir = work / "sort" / f"{workload}-{key}"
                    command = [str(binary), "--tmpdir", str(tmpdir), "-o", str(output),
                               *map(str, paths)]
                    sample = timed_run(command, ROOT, env,
                                       work / "logs" / f"{workload}-{key}-run-{sample_index + 1}.txt",
                                       args.timeout)
                    samples[key].append(sample)
                    try:
                        output.unlink()
                    except FileNotFoundError:
                        pass
                    rows_by_tool.setdefault(key, []).append(
                        f"{label} run {sample_index + 1}: wall_seconds={sample['time_s']:.6f} "
                        f"exit_code={sample['exit_code']} "
                        f"peak_rss_bytes={sample['rss_bytes'] if sample['rss_bytes'] is not None else 'unknown'}")
                    if sample["exit_code"] != 0:
                        log_text = sample["log"].read_text(encoding="utf-8", errors="replace").strip()
                        rows_by_tool[key].append(f"{label} run {sample_index + 1} output: {log_text}")

            for key, label in TOOLS:
                measured_samples = samples[key]
                times = [float(sample["time_s"]) for sample in measured_samples]
                rss_values = [int(sample["rss_bytes"]) for sample in measured_samples
                              if sample["rss_bytes"] is not None]
                rss_bytes = max(rss_values) if len(rss_values) == repeat_count else None
                codes = [int(warmups[key]["exit_code"]),
                         *(int(sample["exit_code"]) for sample in measured_samples)]
                result_status_value, exit_code = result_status(codes)
                median_seconds = statistics.median(times)
                minimum_seconds = min(times)
                result_map[key] = {
                    "time_s": round(median_seconds, 6),
                    "median_time_s": round(median_seconds, 6),
                    "min_time_s": round(minimum_seconds, 6),
                    "rss_bytes": rss_bytes,
                    "rss_mib": round(rss_bytes / (1024 * 1024), 6) if rss_bytes is not None else None,
                    "rss_display": None if rss_bytes is not None else "unknown",
                    "rss_sample_count": len(rss_values),
                    "throughput_mb_s": (round(total_bytes / median_seconds / 1_000_000, 1)
                                         if result_status_value == "OK" and median_seconds > 0 else None),
                    "status": result_status_value,
                    "exit_code": exit_code,
                    "run_count": repeat_count,
                    "warmup_count": 1,
                }
                report.append(f"## {workload} · {label}")
                report.append(f"input_bytes={total_bytes} shards={len(paths)}")
                report.append(f"warmup_wall_seconds={warmups[key]['time_s']:.6f} "
                              f"exit_code={warmups[key]['exit_code']}")
                report.extend(rows_by_tool.get(key, []))
                report.append(
                    f"summary=median_seconds={median_seconds:.6f} min_seconds={minimum_seconds:.6f} "
                    f"peak_rss_bytes={rss_bytes if rss_bytes is not None else 'unknown'} "
                    f"status={result_status_value}")
                report.append("")
                if result_status_value != "OK":
                    status = 1
            datasets.append(dataset)
            shutil.rmtree(generated)
            report.append("")

    artifact = {
        "schema_version": 1,
        "platform": "windows",
        "measurement_date": datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
        "host": host,
        "source": source_metadata(),
        "method": {
            "runner": "bench/run-ci-windows.py",
            "repeat_counts": {name: spec[3] for name, spec in WORKLOADS.items()},
            "warmup_count": 1,
            "wall_time_summary": "median and minimum of measured runs per binary",
            "rss_summary": "maximum process lifetime PeakWorkingSetSize across measured runs; unknown unless all runs are sampled",
            "rss_tool": "Windows GetProcessMemoryInfo PeakWorkingSetSize",
            "timeout_seconds_per_run": args.timeout,
            "cache_state": "no explicit cache flush; one warmup per binary; measured order alternates; runner load uncontrolled",
            "generator": "tools/gen-lcov.py --benchmark-compatible --checksums using native Windows Python",
            "temporary_data": "under .luna-tmp; generated dataset is removed after measurement",
        },
        "tool_labels": {
            "lcovmerge": "lcovmerge 1.0.1 Zig 0.17.0 -O2 Windows x86_64",
            "lcovmerge_ucrt64_gcc": "lcovmerge 1.0.1 MSYS2 UCRT64 GCC -O2",
        },
        "datasets": datasets,
        "text_report": "benchmark.txt",
    }
    (output_dir / "benchmark.json").write_text(json.dumps(artifact, indent=2) + "\n",
                                                encoding="utf-8")
    (output_dir / "benchmark.txt").write_text("\n".join(report).rstrip() + "\n",
                                               encoding="utf-8")
    print(f"Wrote {output_dir / 'benchmark.json'} and {output_dir / 'benchmark.txt'}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
