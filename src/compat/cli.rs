use super::Request;
use crate::common::{Error, MIN_WORKER_BUDGET};
use std::{
    ffi::{OsStr, OsString},
    path::{Path, PathBuf},
};

const HELP: &str = "lcovmerge LCOV 2.6 merge profile\nUsage: lcovmerge --lcov-compat=2.6 -a PATTERN [-a PATTERN ...] [-o FILE]\n  -a, --add-tracefile PATTERN  add matching tracefiles (repeatable)\n  -o, --output-file FILE      write output (default stdout)\n      --include PATTERN       include matching source paths\n      --exclude PATTERN       exclude matching source paths\n      --(no-)function-coverage enable/disable function data\n      --(no-)branch-coverage   enable/disable branch data\n      --(no-)mcdc-coverage     enable/disable MC/DC data\n      --(no-)checksum          enable/disable checksums\n      --forget-test-names      merge testcase identities\n      --ignore-errors LIST     supported: inconsistent\n      --keep-going             continue after recoverable errors\n      --tempdir DIR            temporary directory\n      --parallel[=N]           worker count (1-32)\n      --mem-limit SIZE         lcovmerge arena limit extension\n  -q, --quiet | -v, --verbose | -h, --help | --version\n";

fn err(msg: impl Into<String>) -> Error {
    Error::new(1, msg)
}
fn val(args: &[OsString], i: &mut usize, opt: &str) -> Result<String, Error> {
    *i += 1;
    args.get(*i)
        .and_then(|s| s.to_str())
        .map(str::to_owned)
        .ok_or_else(|| err(format!("{opt} requires a UTF-8 value")))
}
fn pattern_match(p: &[u8], s: &[u8]) -> bool {
    if p.is_empty() {
        return s.is_empty();
    }
    match p[0] {
        b'*' => (0..=s.len()).any(|n| pattern_match(&p[1..], &s[n..])),
        b'?' => !s.is_empty() && pattern_match(&p[1..], &s[1..]),
        c => s.first() == Some(&c) && pattern_match(&p[1..], &s[1..]),
    }
}
fn expand_one(pattern: &OsStr, out: &mut Vec<PathBuf>) -> Result<(), Error> {
    let text = pattern
        .to_str()
        .ok_or_else(|| err("glob patterns must be UTF-8"))?;
    if !text.bytes().any(|b| b == b'*' || b == b'?') {
        out.push(pattern.into());
        return Ok(());
    }
    let (dir, leaf) = match text.rsplit_once('/') {
        Some((d, l)) => (if d.is_empty() { "/" } else { d }, l),
        None => (".", text),
    };
    let entries = std::fs::read_dir(dir)
        .map_err(|e| err(format!("cannot read glob directory {dir}: {e}")))?;
    let mut matches = Vec::new();
    for entry in entries {
        let entry = entry.map_err(|e| err(format!("cannot read glob entry: {e}")))?;
        let name = entry.file_name();
        if pattern_match(leaf.as_bytes(), name.to_string_lossy().as_bytes()) {
            matches.push(Path::new(dir).join(name));
        }
    }
    matches.sort();
    if matches.is_empty() {
        return Err(err(format!("pattern matched no files: {text}")));
    }
    out.extend(matches);
    Ok(())
}
fn size(s: &str) -> Option<usize> {
    let (num, shift) = match s.as_bytes().last()? {
        b'k' | b'K' => (&s[..s.len() - 1], 10),
        b'm' | b'M' => (&s[..s.len() - 1], 20),
        b'g' | b'G' => (&s[..s.len() - 1], 30),
        _ => (s, 0),
    };
    num.parse::<usize>().ok()?.checked_shl(shift)
}
fn rc_bool(value: &str, key: &str) -> Result<bool, Error> {
    match value.to_ascii_lowercase().as_str() {
        "1" | "yes" | "on" | "true" => Ok(true),
        "0" | "no" | "off" | "false" => Ok(false),
        _ => Err(err(format!(
            "invalid boolean value for --rc {key}: {value}"
        ))),
    }
}

/// Parse only after the selector has been found before `--`; selection is positional-independent.
pub fn selected(args: &[OsString]) -> bool {
    args.iter()
        .skip(1)
        .take_while(|a| a.as_os_str() != OsStr::new("--"))
        .any(|a| {
            a == "--lcov-compat" || a.to_str().is_some_and(|s| s.starts_with("--lcov-compat="))
        })
}
pub fn parse(args: impl IntoIterator<Item = OsString>) -> Result<Option<Request>, Error> {
    let args: Vec<_> = args.into_iter().collect();
    let home_rc = std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".lcovrc"));
    let lcov_home_rc = std::env::var_os("LCOV_HOME").map(|h| PathBuf::from(h).join("etc/lcovrc"));
    if home_rc.as_ref().is_some_and(|p| p.is_file())
        || lcov_home_rc.as_ref().is_some_and(|p| p.is_file())
    {
        return Err(err(
            "LCOV configuration discovery is unsupported; remove the config or use native mode",
        ));
    }
    let end = args.iter().position(|a| a == "--").unwrap_or(args.len());
    let mut argv = vec![args.first().cloned().unwrap_or_else(|| "lcovmerge".into())];
    let mut selector_seen = false;
    for a in args.iter().skip(1).take(end.saturating_sub(1)) {
        if a == "--lcov-compat=2.6" {
            selector_seen = true;
            continue;
        }
        if a == "--lcov-compat" {
            return Err(err("--lcov-compat requires the supported version 2.6"));
        }
        if let Some(s) = a.to_str().and_then(|s| s.strip_prefix("--lcov-compat=")) {
            return Err(err(format!("unsupported LCOV compatibility version: {s}")));
        }
        argv.push(a.clone());
    }
    if !selector_seen {
        return Err(err("missing --lcov-compat=2.6 selector"));
    }
    argv.extend(args.iter().skip(end).cloned());
    let mut req = Request::default();
    let o = &mut req.resources;
    o.branch_coverage = false;
    o.output = "-".into();
    o.function_data = true;
    let mut raw_inputs = Vec::new();
    let mut i = 1;
    let mut positional = false;
    let mut function_disabled = false;
    while i < argv.len() {
        let a = argv[i]
            .to_str()
            .ok_or_else(|| err("arguments must be UTF-8 except file paths"))?;
        if !positional && a == "--" {
            positional = true;
            i += 1;
            continue;
        }
        if !positional && (a == "-h" || a == "--help") {
            print!("{HELP}");
            return Ok(None);
        }
        if !positional && a == "--version" {
            println!(
                "lcovmerge {} (LCOV compatibility profile 2.6)",
                env!("CARGO_PKG_VERSION")
            );
            return Ok(None);
        }
        if !positional && (a == "-q" || a == "--quiet") {
            o.quiet = true;
            i += 1;
            continue;
        }
        if !positional && (a == "-v" || a == "--verbose") {
            o.verbose = true;
            i += 1;
            continue;
        }
        if !positional && (a == "-a" || a == "--add-tracefile") {
            raw_inputs.push(PathBuf::from(val(&argv, &mut i, a)?));
            i += 1;
            continue;
        }
        if !positional && (a == "-o" || a == "--output-file") {
            o.output = val(&argv, &mut i, a)?.into();
            i += 1;
            continue;
        }
        if !positional && (a == "--include" || a == "--exclude") {
            let v = val(&argv, &mut i, a)?;
            if a == "--include" {
                o.includes.push(v)
            } else {
                o.excludes.push(v)
            }
            i += 1;
            continue;
        }
        if !positional && a == "--forget-test-names" {
            req.forget_test_names = true;
            i += 1;
            continue;
        }
        if !positional && (a == "--checksum" || a == "--no-checksum") {
            req.checksum = a == "--checksum";
            i += 1;
            continue;
        }
        if !positional && (a == "--function-coverage" || a == "--no-function-coverage") {
            if a == "--no-function-coverage" {
                function_disabled = true;
            }
            o.function_data = !function_disabled;
            i += 1;
            continue;
        }
        if !positional && (a == "--branch-coverage" || a == "--no-branch-coverage") {
            o.branch_coverage = a == "--branch-coverage";
            i += 1;
            continue;
        }
        if !positional && (a == "--mcdc-coverage" || a == "--no-mcdc-coverage") {
            req.mcdc_coverage = a == "--mcdc-coverage";
            i += 1;
            continue;
        }
        if !positional && a == "--ignore-errors" {
            for e in val(&argv, &mut i, a)?.split(',') {
                if e != "inconsistent" {
                    return Err(err(format!("unsupported --ignore-errors category: {e}")));
                }
                req.ignore_errors.insert(e.into());
            }
            i += 1;
            continue;
        }
        if !positional && a == "--keep-going" {
            req.keep_going = true;
            i += 1;
            continue;
        }
        if !positional && (a == "--tempdir" || a == "--tmpdir") {
            o.tmpdir = val(&argv, &mut i, a)?.into();
            i += 1;
            continue;
        }
        if !positional && (a == "--parallel" || a.starts_with("--parallel=")) {
            let n = if let Some(v) = a.strip_prefix("--parallel=") {
                v.to_owned()
            } else if argv.get(i + 1).is_some_and(|v| {
                v.to_str()
                    .is_some_and(|s| s.bytes().all(|b| b.is_ascii_digit()))
            }) {
                i += 1;
                argv[i].to_string_lossy().into_owned()
            } else {
                "0".into()
            };
            let n = if n == "0" {
                std::thread::available_parallelism()
                    .map_or(1, |n| n.get())
                    .min(32)
            } else {
                n.parse::<usize>()
                    .ok()
                    .filter(|n| (1..=32).contains(n))
                    .ok_or_else(|| err("--parallel expects 1-32"))?
            };
            o.jobs = n;
            o.jobs_explicit = true;
            i += 1;
            continue;
        }
        if !positional && a == "--mem-limit" {
            o.mem_limit =
                size(&val(&argv, &mut i, a)?).ok_or_else(|| err("invalid --mem-limit size"))?;
            i += 1;
            continue;
        }
        if !positional && a == "--rc" {
            let setting = val(&argv, &mut i, a)?;
            let (key, value) = setting
                .split_once('=')
                .ok_or_else(|| err("--rc requires SETTING=VALUE"))?;
            match key {
                "function_coverage" => {
                    if function_disabled && rc_bool(value, key)? {
                        // LCOV preserves a negative command-line function toggle.
                    } else {
                        o.function_data = rc_bool(value, key)?;
                        function_disabled = !o.function_data;
                    }
                }
                "branch_coverage" => o.branch_coverage = rc_bool(value, key)?,
                "mcdc_coverage" | "mcdc-coverage" => req.mcdc_coverage = rc_bool(value, key)?,
                "checksum" => req.checksum = rc_bool(value, key)?,
                _ => {
                    return Err(err(format!(
                        "unsupported merge-relevant --rc setting: {key}"
                    )))
                }
            }
            i += 1;
            continue;
        }
        if !positional && a == "--config-file" {
            return Err(err(
                "--config-file is unsupported: configuration discovery is not implemented",
            ));
        }
        if !positional && a.starts_with('-') && a != "-" {
            return Err(err(format!("unsupported LCOV merge option: {a}")));
        }
        raw_inputs.push(PathBuf::from(&argv[i]));
        i += 1;
    }
    if raw_inputs.is_empty() {
        return Err(err(
            "merge requires at least one -a/--add-tracefile PATTERN",
        ));
    }
    for p in raw_inputs {
        expand_one(p.as_os_str(), &mut o.inputs)?;
    }
    o.mem_limit = o.mem_limit.max(MIN_WORKER_BUDGET);
    if !o.jobs_explicit {
        o.jobs = std::thread::available_parallelism().map_or(1, |n| n.get().min(4));
    }
    if o.jobs > o.mem_limit / MIN_WORKER_BUDGET {
        return Err(err("worker count exceeds --mem-limit capacity"));
    }
    Ok(Some(req))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn parse_s(s: &[&str]) -> Result<Option<Request>, Error> {
        parse(s.iter().map(OsString::from))
    }
    #[test]
    fn selector_position_and_delimiter() {
        assert!(parse_s(&["x", "-q", "--lcov-compat=2.6", "-a", "a"])
            .unwrap()
            .is_some());
        assert!(parse_s(&["x", "--", "--lcov-compat=2.6", "a"]).is_err());
    }
    #[test]
    fn rejects_version_and_operation() {
        assert!(parse_s(&["x", "--lcov-compat=9", "-a", "a"]).is_err());
        assert!(parse_s(&["x", "--lcov-compat=2.6", "--capture"]).is_err());
    }
    #[test]
    fn defaults_and_controls() {
        let r = parse_s(&[
            "x",
            "--lcov-compat=2.6",
            "-a",
            "a",
            "--no-function-coverage",
            "--function-coverage",
            "--no-checksum",
            "--forget-test-names",
        ])
        .unwrap()
        .unwrap();
        assert!(!r.resources.branch_coverage);
        assert!(!r.resources.function_data);
        assert!(!r.checksum);
        assert!(r.forget_test_names);
    }

    #[test]
    fn rc_supported_values_and_unknown_rejection() {
        let r = parse_s(&[
            "x",
            "--lcov-compat=2.6",
            "--rc",
            "branch_coverage=on",
            "-a",
            "a",
        ])
        .unwrap()
        .unwrap();
        assert!(r.resources.branch_coverage);
        assert!(parse_s(&[
            "x",
            "--lcov-compat=2.6",
            "--rc",
            "genhtml_hi_limit=90",
            "-a",
            "a",
        ])
        .is_err());
    }

    #[test]
    fn quoted_glob_expands_sorted_and_keeps_duplicates() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("b.info"), "").unwrap();
        std::fs::write(dir.path().join("a.info"), "").unwrap();
        let pattern = dir.path().join("*.info").to_string_lossy().into_owned();
        let r = parse_s(&["x", "--lcov-compat=2.6", "-a", &pattern, "-a", &pattern])
            .unwrap()
            .unwrap();
        assert_eq!(r.resources.inputs.len(), 4);
        assert!(r.resources.inputs[0] < r.resources.inputs[1]);
        assert_eq!(r.resources.inputs[0], r.resources.inputs[2]);
    }
}
