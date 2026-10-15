use super::Request;
#[path = "config.rs"]
mod config;
use crate::common::{Error, MIN_WORKER_BUDGET};
use std::{
    ffi::{OsStr, OsString},
    io::Write,
    path::PathBuf,
};

const HELP: &str = "lcovmerge LCOV 2.6 merge profile\nUsage: lcovmerge --lcov-compat=2.6 -a PATTERN [-a PATTERN ...] [-o FILE]\n  -a, --add-tracefile PATTERN  add matching tracefiles (repeatable)\n  -o, --output-file FILE      write output (default stdout; last -o wins)\n      --include PATTERN       include matching source paths\n      --exclude PATTERN       exclude matching source paths\n      --(no-)function-coverage enable/disable function data\n      --(no-)branch-coverage   enable/disable branch data\n      --(no-)mcdc-coverage     enable/disable MC/DC data\n      --(no-)checksum          enable/disable checksums\n      --forget-test-names      merge testcase identities\n      --ignore-errors LIST     inconsistent, version, mismatch, format, empty, source, unused, negative\n      --keep-going             continue after recoverable errors\n      --tempdir DIR            temporary directory\n      --parallel[=N]           workers (default 1, 0 selects cores up to memory capacity)\n      --mem-limit SIZE         lcovmerge arena limit extension\n  -q, --quiet | -v, --verbose | -h, --help | --version\n";

fn err(msg: impl Into<String>) -> Error {
    Error::new(1, msg)
}

fn option_value(
    a: &OsStr,
    args: &[OsString],
    i: &mut usize,
    name: &str,
) -> Result<OsString, Error> {
    if let Some(value) = attached_value(a, name) {
        if value.is_empty() {
            return Err(err(format!("{name} requires a value")));
        }
        return Ok(value.to_os_string());
    }
    *i += 1;
    args.get(*i)
        .filter(|v| !v.is_empty())
        .cloned()
        .ok_or_else(|| err(format!("{name} requires a value")))
}

fn attached_value<'a>(arg: &'a OsStr, name: &str) -> Option<&'a OsStr> {
    os_suffix(arg, format!("{name}=").as_bytes())
}

fn os_suffix<'a>(arg: &'a OsStr, prefix: &[u8]) -> Option<&'a OsStr> {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        arg.as_bytes().strip_prefix(prefix).map(OsStr::from_bytes)
    }
    #[cfg(not(unix))]
    {
        let text = arg.to_str()?;
        let prefix = std::str::from_utf8(prefix).ok()?;
        text.strip_prefix(prefix).map(OsStr::new)
    }
}

fn short_attached<'a>(arg: &'a OsStr, short: &[u8]) -> Option<&'a OsStr> {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        let bytes = arg.as_bytes();
        (bytes.starts_with(short) && bytes.len() > short.len())
            .then(|| OsStr::from_bytes(&bytes[short.len()..]))
    }
    #[cfg(not(unix))]
    {
        let text = arg.to_str()?;
        let short = std::str::from_utf8(short).ok()?;
        text.strip_prefix(short)
            .filter(|suffix| !suffix.is_empty())
            .map(OsStr::new)
    }
}

fn has_long_value(arg: &OsStr, name: &str) -> bool {
    attached_value(arg, name).is_some()
}

fn split_setting(s: &str) -> Result<(&str, &str), Error> {
    s.split_once('=')
        .ok_or_else(|| err("--rc requires SETTING=VALUE"))
}

fn rc_bool(value: &str, key: &str) -> Result<bool, Error> {
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "yes" | "on" | "true" => Ok(true),
        "0" | "no" | "off" | "false" => Ok(false),
        _ => Err(err(format!(
            "invalid boolean value for --rc {key}: {value}"
        ))),
    }
}

fn apply_setting(req: &mut Request, key: &str, value: &str) -> Result<(), Error> {
    let o = &mut req.resources;
    match key {
        "function_coverage" => {
            o.function_data = rc_bool(value, key)?;
        }
        "branch_coverage" => o.branch_coverage = rc_bool(value, key)?,
        "mcdc_coverage" => req.mcdc_coverage = rc_bool(value, key)?,
        "checksum" => req.checksum = rc_bool(value, key)?,
        "check_data_consistency" => req.check_data_consistency = rc_bool(value, key)?,
        "derive_function_end_line" => req.derive_function_end_line = rc_bool(value, key)?,
        "forget_testcase_names" => req.forget_test_names = rc_bool(value, key)?,
        "include" => o.includes.push(value.to_owned()),
        "exclude" => o.excludes.push(value.to_owned()),
        "parallel" => {
            o.jobs = parallel(value)?;
            o.jobs_explicit = true;
        }
        "lcov_tmp_dir" => o.tmpdir = PathBuf::from(value),
        "ignore_errors" => add_ignore_errors(req, value)?,
        "memory" | "memory_percentage" => {
            return Err(err(format!(
                "unsupported LCOV resource setting: {key}; memory throttling is not implemented"
            )))
        }
        _ => {
            return Err(err(format!(
                "unsupported merge-relevant LCOV configuration setting: {key}"
            )))
        }
    }
    Ok(())
}

fn add_ignore_errors(req: &mut Request, list: &str) -> Result<(), Error> {
    const ALLOWED: &[&str] = &[
        "inconsistent",
        "version",
        "mismatch",
        "format",
        "empty",
        "source",
        "unused",
        "negative",
    ];
    for item in list.split([',', ':', ' ', '\t']).filter(|s| !s.is_empty()) {
        let item = item.to_ascii_lowercase();
        if !ALLOWED.contains(&item.as_str()) {
            return Err(err(format!("unsupported --ignore-errors category: {item}")));
        }
        req.ignore_errors.insert(item);
    }
    Ok(())
}

fn parallel(value: &str) -> Result<usize, Error> {
    let n = value
        .parse::<usize>()
        .map_err(|_| err("--parallel expects a non-negative worker count"))?;
    match n {
        0 => Ok(0),
        1..=32 => Ok(n),
        _ => Err(err("--parallel expects 0-32 (0 selects available cores)")),
    }
}

fn size(s: &str) -> Option<usize> {
    let (digits, power) = match s.as_bytes().last()? {
        b'k' | b'K' => (&s[..s.len() - 1], 10),
        b'm' | b'M' => (&s[..s.len() - 1], 20),
        b'g' | b'G' => (&s[..s.len() - 1], 30),
        _ => (s, 0),
    };
    let number = digits.parse::<usize>().ok()?;
    number.checked_mul(1usize.checked_shl(power)?)
}

/// Selector detection skips arguments consumed as values by LCOV options.
pub fn selected(args: &[OsString]) -> bool {
    let end = args.iter().position(|a| a == "--").unwrap_or(args.len());
    let mut i = 1;
    while i < end {
        let a = args[i].as_os_str();
        if a == "--lcov-compat=2.6"
            || a == "--lcov-compat"
            || a.to_str().is_some_and(|s| s.starts_with("--lcov-compat="))
        {
            return true;
        }
        if consumes_next(a) {
            i += 1;
        }
        i += 1;
    }
    false
}

fn consumes_next(a: &OsStr) -> bool {
    if matches!(a, x if x == "-a" || x == "-o" || x == "--add-tracefile" || x == "--output-file" || x == "--include" || x == "--exclude" || x == "--ignore-errors" || x == "--tempdir" || x == "--tmpdir" || x == "--mem-limit" || x == "--rc" || x == "--config-file")
    {
        return true;
    }
    false
}

pub fn parse(args: impl IntoIterator<Item = OsString>) -> Result<Option<Request>, Error> {
    let args: Vec<_> = args.into_iter().collect();
    let end = args.iter().position(|a| a == "--").unwrap_or(args.len());
    let mut selector = false;
    let mut configs = Vec::new();
    let mut rc = Vec::new();
    let mut argv = vec![args.first().cloned().unwrap_or_else(|| "lcovmerge".into())];
    let mut i = 1;
    while i < end {
        let a = args[i].as_os_str();
        if a == "--lcov-compat=2.6" {
            selector = true;
            i += 1;
            continue;
        }
        if a == "--lcov-compat" {
            return Err(err("--lcov-compat requires the supported version 2.6"));
        }
        if let Some(v) = a.to_str().and_then(|s| s.strip_prefix("--lcov-compat=")) {
            return Err(err(format!("unsupported LCOV compatibility version: {v}")));
        }
        if a == "--config-file" || a.to_str().is_some_and(|s| s.starts_with("--config-file=")) {
            configs.push(PathBuf::from(option_value(
                a,
                &args,
                &mut i,
                "--config-file",
            )?));
            i += 1;
            continue;
        }
        if a == "--rc" || a.to_str().is_some_and(|s| s.starts_with("--rc=")) {
            let setting = option_value(a, &args, &mut i, "--rc")?;
            rc.push(
                setting
                    .into_string()
                    .map_err(|_| err("--rc setting must be UTF-8"))?,
            );
            i += 1;
            continue;
        }
        argv.push(args[i].clone());
        if consumes_next(a) && a != "--parallel" {
            i += 1;
            if i < end {
                argv.push(args[i].clone());
            }
        }
        i += 1;
    }
    if !selector {
        return Err(err("missing --lcov-compat=2.6 selector"));
    }
    argv.extend(args.iter().skip(end).cloned());

    let loaded = config::Config::load(&configs)?;
    let mut req = Request::default();
    let o = &mut req.resources;
    o.branch_coverage = false;
    o.output = "-".into();
    o.function_data = true;
    // LCOV configuration files are applied first, then all --rc values, independent
    // of their position among command-line coverage switches.
    for (key, value) in loaded.settings {
        apply_setting(&mut req, &key, &value)?;
    }
    for setting in rc {
        let (key, value) = split_setting(&setting)?;
        let key = key.trim();
        if config::deprecated(key) {
            continue;
        }
        if !config::supported(key) && !matches!(key, "memory" | "memory_percentage") {
            return Err(err(format!(
                "unsupported merge-relevant --rc setting: {key}"
            )));
        }
        apply_setting(&mut req, key, value.trim())?;
    }

    let mut raw_inputs = Vec::new();
    let mut i = 1;
    let mut positional = false;
    let mut no_function = false;
    let mut no_branch = false;
    let mut no_checksum = false;
    let mut cli_include_seen = false;
    let mut cli_exclude_seen = false;
    let mut cli_ignore_seen = false;
    while i < argv.len() {
        let a_os = argv[i].as_os_str();
        let a = a_os.to_str().unwrap_or("");
        if !positional && a == "--" {
            positional = true;
            i += 1;
            continue;
        }
        if !positional && matches!(a, "-h" | "--help") {
            std::io::stdout()
                .write_all(HELP.as_bytes())
                .map_err(|e| err(format!("cannot write help: {e}")))?;
            return Ok(None);
        }
        if !positional && a == "--version" {
            writeln!(
                std::io::stdout(),
                "lcovmerge {} (LCOV compatibility profile 2.6)",
                env!("CARGO_PKG_VERSION")
            )
            .map_err(|e| err(format!("cannot write version: {e}")))?;
            return Ok(None);
        }
        if !positional && matches!(a, "-q" | "--quiet") {
            req.resources.quiet = true;
            i += 1;
            continue;
        }
        if !positional && matches!(a, "-v" | "--verbose") {
            req.resources.verbose = true;
            i += 1;
            continue;
        }
        if !positional
            && (a == "-a"
                || short_attached(a_os, b"-a").is_some()
                || a == "--add-tracefile"
                || has_long_value(a_os, "--add-tracefile"))
        {
            let v = if let Some(suffix) = short_attached(a_os, b"-a") {
                suffix.to_os_string()
            } else if a == "-a" {
                i += 1;
                argv.get(i)
                    .cloned()
                    .ok_or_else(|| err("-a requires a value"))?
            } else {
                option_value(a_os, &argv, &mut i, "--add-tracefile")?
            };
            raw_inputs.push(PathBuf::from(v));
            i += 1;
            continue;
        }
        if !positional
            && (a == "-o"
                || short_attached(a_os, b"-o").is_some()
                || a == "--output-file"
                || has_long_value(a_os, "--output-file"))
        {
            let v = if let Some(suffix) = short_attached(a_os, b"-o") {
                suffix.to_os_string()
            } else if a == "-o" {
                i += 1;
                argv.get(i)
                    .cloned()
                    .ok_or_else(|| err("-o requires a value"))?
            } else {
                option_value(a_os, &argv, &mut i, "--output-file")?
            };
            req.resources.output = PathBuf::from(v);
            i += 1;
            continue;
        }
        if !positional
            && (a == "--include"
                || has_long_value(a_os, "--include")
                || a == "--exclude"
                || has_long_value(a_os, "--exclude"))
        {
            let name = if a.starts_with("--include") {
                "--include"
            } else {
                "--exclude"
            };
            let v = option_value(a_os, &argv, &mut i, name)?
                .into_string()
                .map_err(|_| err("include/exclude patterns must be UTF-8"))?;
            if name == "--include" {
                if !cli_include_seen {
                    req.resources.includes.clear();
                    cli_include_seen = true;
                }
                req.resources.includes.push(v);
            } else {
                if !cli_exclude_seen {
                    req.resources.excludes.clear();
                    cli_exclude_seen = true;
                }
                req.resources.excludes.push(v);
            }
            i += 1;
            continue;
        }
        if !positional && a == "--forget-test-names" {
            req.forget_test_names = true;
            i += 1;
            continue;
        }
        if !positional && matches!(a, "--checksum" | "--no-checksum") {
            no_checksum |= a == "--no-checksum";
            req.checksum = !no_checksum;
            i += 1;
            continue;
        }
        if !positional && matches!(a, "--function-coverage" | "--no-function-coverage") {
            no_function |= a == "--no-function-coverage";
            req.resources.function_data = !no_function;
            i += 1;
            continue;
        }
        if !positional && matches!(a, "--branch-coverage" | "--no-branch-coverage") {
            no_branch |= a == "--no-branch-coverage";
            req.resources.branch_coverage = !no_branch;
            i += 1;
            continue;
        }
        if !positional && matches!(a, "--mcdc-coverage" | "--no-mcdc-coverage") {
            req.mcdc_coverage = a == "--mcdc-coverage";
            i += 1;
            continue;
        }
        if !positional && (a == "--ignore-errors" || has_long_value(a_os, "--ignore-errors")) {
            let list = option_value(a_os, &argv, &mut i, "--ignore-errors")?
                .into_string()
                .map_err(|_| err("--ignore-errors must be UTF-8"))?;
            if !cli_ignore_seen {
                req.ignore_errors.clear();
                cli_ignore_seen = true;
            }
            add_ignore_errors(&mut req, &list)?;
            i += 1;
            continue;
        }
        if !positional && a == "--keep-going" {
            req.keep_going = true;
            i += 1;
            continue;
        }
        if !positional
            && (a == "--tempdir"
                || a == "--tmpdir"
                || has_long_value(a_os, "--tempdir")
                || has_long_value(a_os, "--tmpdir"))
        {
            let name = if a == "--tempdir" || has_long_value(a_os, "--tempdir") {
                "--tempdir"
            } else {
                "--tmpdir"
            };
            req.resources.tmpdir = PathBuf::from(option_value(a_os, &argv, &mut i, name)?);
            i += 1;
            continue;
        }
        if !positional && (a == "--parallel" || a.starts_with("--parallel=")) {
            let n = if a == "--parallel" {
                if argv
                    .get(i + 1)
                    .and_then(|v| v.to_str())
                    .is_some_and(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
                {
                    i += 1;
                    argv[i].to_string_lossy().into_owned()
                } else {
                    "0".to_owned()
                }
            } else {
                a.strip_prefix("--parallel=").unwrap_or("").to_owned()
            };
            req.resources.jobs = parallel(&n)?;
            req.resources.jobs_explicit = true;
            i += 1;
            continue;
        }
        if !positional && (a == "--memory" || a.starts_with("--memory=")) {
            return Err(err("--memory is unsupported: LCOV memory throttling requires scheduler semantics not provided by this merge executor"));
        }
        if !positional && (a == "--mem-limit" || has_long_value(a_os, "--mem-limit")) {
            let v = option_value(a_os, &argv, &mut i, "--mem-limit")?
                .into_string()
                .map_err(|_| err("--mem-limit must be UTF-8"))?;
            req.resources.mem_limit = size(&v).ok_or_else(|| err("invalid --mem-limit size"))?;
            i += 1;
            continue;
        }
        if !positional && a.starts_with('-') && a != "-" {
            return Err(err(format!("unsupported LCOV merge option: {a}")));
        }
        raw_inputs.push(PathBuf::from(&argv[i]));
        i += 1;
    }
    if no_function {
        req.resources.function_data = false;
    }
    if raw_inputs.is_empty() {
        return Err(err(
            "merge requires at least one -a/--add-tracefile PATTERN",
        ));
    }
    for pattern in raw_inputs {
        expand_one(pattern.as_os_str(), &mut req.resources.inputs)?;
    }
    if req.resources.mem_limit < MIN_WORKER_BUDGET {
        return Err(err("--mem-limit must be at least 8M"));
    }
    if !req.resources.jobs_explicit {
        req.resources.jobs = 1;
    }
    let capacity = req.resources.mem_limit / MIN_WORKER_BUDGET;
    if req.resources.jobs == 0 {
        req.resources.jobs = std::thread::available_parallelism()
            .map_or(1, |n| n.get())
            .min(32)
            .min(capacity);
    } else if req.resources.jobs > capacity {
        return Err(err("worker count exceeds --mem-limit capacity"));
    }
    Ok(Some(req))
}

fn expand_one(pattern: &OsStr, out: &mut Vec<PathBuf>) -> Result<(), Error> {
    let Some(text) = pattern.to_str() else {
        // A byte path without glob metacharacters is an exact input path.
        #[cfg(unix)]
        {
            use std::os::unix::ffi::OsStrExt;
            if !pattern.as_bytes().iter().any(|b| b"*?[]\\".contains(b)) {
                out.push(PathBuf::from(pattern));
                return Ok(());
            }
        }
        return Err(err("non-UTF-8 LCOV glob patterns are unsupported; pass a literal byte path without glob syntax"));
    };
    if !text.bytes().any(|b| b"*?[]\\".contains(&b)) {
        out.push(PathBuf::from(pattern));
        return Ok(());
    }
    let paths =
        glob::glob(text).map_err(|e| err(format!("invalid tracefile pattern '{text}': {e}")))?;
    let mut found = Vec::new();
    for path in paths {
        found
            .push(path.map_err(|e| err(format!("cannot expand tracefile pattern '{text}': {e}")))?);
    }
    if found.is_empty() {
        return Err(err(format!("pattern matched no files: {text}")));
    }
    found.sort();
    out.extend(found);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn parse_s(s: &[&str]) -> Result<Option<Request>, Error> {
        parse(s.iter().map(OsString::from))
    }
    #[test]
    fn selector_position_values() {
        assert!(!selected(&[
            "x".into(),
            "-a".into(),
            "--lcov-compat=2.6".into()
        ]));
        assert!(parse_s(&["x", "-q", "--lcov-compat=2.6", "-a", "literal"])
            .unwrap()
            .is_some());
    }
    #[test]
    fn toggles_and_cli_rc_precedence() {
        let r = parse_s(&[
            "x",
            "--lcov-compat=2.6",
            "--no-function-coverage",
            "--rc=function_coverage=1",
            "-a",
            "a",
        ])
        .unwrap()
        .unwrap();
        assert!(!r.resources.function_data);
        let r = parse_s(&[
            "x",
            "--lcov-compat=2.6",
            "--rc=branch_coverage=on",
            "--no-branch-coverage",
            "-a",
            "a",
        ])
        .unwrap()
        .unwrap();
        assert!(!r.resources.branch_coverage);
    }
    #[test]
    fn options_with_equals_short_attached_and_parallel_zero() {
        let r = parse_s(&[
            "x",
            "--lcov-compat=2.6",
            "-afile",
            "-oresult",
            "-ofinal",
            "--tempdir=tmp",
            "--parallel=0",
            "--mem-limit=8M",
            "--ignore-errors=Inconsistent,format",
        ])
        .unwrap()
        .unwrap();
        assert_eq!(r.resources.inputs, vec![PathBuf::from("file")]);
        assert_eq!(r.resources.output, PathBuf::from("final"));
        assert_eq!(r.resources.jobs, 1);
        assert!(r.ignore_errors.contains("format"));
    }

    #[test]
    fn omitted_parallel_value_is_memory_capped_but_explicit_count_is_checked() {
        let auto = parse_s(&[
            "x",
            "--lcov-compat=2.6",
            "--mem-limit=8M",
            "--parallel",
            "-a",
            "a",
        ])
        .unwrap()
        .unwrap();
        assert_eq!(auto.resources.jobs, 1);
        assert!(parse_s(&[
            "x",
            "--lcov-compat=2.6",
            "--mem-limit=8M",
            "--parallel=2",
            "-a",
            "a",
        ])
        .is_err());
        let overridden = parse_s(&[
            "x",
            "--lcov-compat=2.6",
            "--parallel=2",
            "--parallel=0",
            "--mem-limit=8M",
            "-a",
            "a",
        ])
        .unwrap()
        .unwrap();
        assert_eq!(overridden.resources.jobs, 1);
        let from_rc = parse_s(&[
            "x",
            "--lcov-compat=2.6",
            "--rc=parallel=0",
            "--mem-limit=8M",
            "-a",
            "a",
        ])
        .unwrap()
        .unwrap();
        assert_eq!(from_rc.resources.jobs, 1);
    }

    #[test]
    fn explicit_dash_output_matches_lcov_stdout_destination() {
        let default = parse_s(&["x", "--lcov-compat=2.6", "-a", "a"])
            .unwrap()
            .unwrap();
        let explicit = parse_s(&["x", "--lcov-compat=2.6", "-o", "-", "-a", "a"])
            .unwrap()
            .unwrap();
        assert_eq!(default.resources.output, PathBuf::from("-"));
        assert_eq!(explicit.resources.output, default.resources.output);
    }

    #[test]
    fn deprecated_coverage_aliases_are_ignored_in_rc_and_config() {
        let dir = tempfile::tempdir().unwrap();
        let rc = dir.path().join("aliases.rc");
        std::fs::write(
            &rc,
            "lcov_branch_coverage = 1\nlcov_function_coverage = 0\nlcov_br_coverage = 1\nlcov_func_coverage = 0\ngenhtml_branch_coverage = 1\ngenhtml_function_coverage = 0\n",
        )
        .unwrap();
        let rc_arg = rc.to_string_lossy().into_owned();
        let r = parse_s(&[
            "x",
            "--lcov-compat=2.6",
            "--config-file",
            &rc_arg,
            "--rc=lcov_branch_coverage=1",
            "-a",
            "a",
        ])
        .unwrap()
        .unwrap();
        assert!(!r.resources.branch_coverage);
        assert!(r.resources.function_data);
    }
    #[test]
    fn rejects_memory_and_bad_categories() {
        assert!(parse_s(&["x", "--lcov-compat=2.6", "--memory=10", "-a", "a"]).is_err());
        assert!(parse_s(&[
            "x",
            "--lcov-compat=2.6",
            "--ignore-errors=callback",
            "-a",
            "a"
        ])
        .is_err());
    }

    #[test]
    fn explicit_config_inclusion_precedence_and_cycle_detection() {
        let dir = tempfile::tempdir().unwrap();
        let child = dir.path().join("child.rc");
        let parent = dir.path().join("parent.rc");
        std::fs::write(&child, "branch_coverage = 1\nfunction_coverage = 0\n").unwrap();
        std::fs::write(
            &parent,
            format!("config_file = {}\nbranch_coverage = 0\n", child.display()),
        )
        .unwrap();
        let parent_arg = parent.to_string_lossy().into_owned();
        let r = parse_s(&[
            "x",
            "--lcov-compat=2.6",
            "--config-file",
            &parent_arg,
            "--function-coverage",
            "--rc=branch_coverage=1",
            "--no-branch-coverage",
            "-a",
            "a",
        ])
        .unwrap()
        .unwrap();
        assert!(!r.resources.branch_coverage);
        assert!(r.resources.function_data);

        std::fs::write(&child, format!("config_file = {}\n", parent.display())).unwrap();
        assert!(parse_s(&[
            "x",
            "--lcov-compat=2.6",
            "--config-file",
            &parent_arg,
            "-a",
            "a"
        ])
        .is_err());
    }
    #[test]
    fn glob_supports_brackets_escape_determinism_duplicates() {
        let dir = tempfile::tempdir().unwrap();
        for n in ["a1.info", "a2.info", "a[3].info"] {
            std::fs::write(dir.path().join(n), "").unwrap();
        }
        let bracket = dir.path().join("a[12].info");
        let escaped = PathBuf::from(glob::Pattern::escape(
            dir.path().join("a[3].info").to_str().unwrap(),
        ));
        let r = parse([
            "x".into(),
            "--lcov-compat=2.6".into(),
            "-a".into(),
            bracket.into_os_string(),
            "-a".into(),
            escaped.into_os_string(),
        ])
        .unwrap()
        .unwrap();
        assert_eq!(
            r.resources.inputs,
            ["a1.info", "a2.info", "a[3].info"].map(|name| dir.path().join(name))
        );
    }
    #[cfg(unix)]
    #[test]
    fn byte_paths_for_inputs_output_and_tempdir() {
        use std::os::unix::ffi::OsStringExt;
        let bad = OsString::from_vec(vec![b'i', b'n', b'\xff']);
        let input = PathBuf::from(&bad);
        let output = PathBuf::from(OsString::from_vec(vec![b'o', b'\xfe']));
        let tmp = PathBuf::from(OsString::from_vec(vec![b't', b'\xfd']));
        let r = parse(vec![
            "x".into(),
            "--lcov-compat=2.6".into(),
            "-a".into(),
            input.clone().into_os_string(),
            "-o".into(),
            output.clone().into_os_string(),
            "--tempdir".into(),
            tmp.clone().into_os_string(),
        ])
        .unwrap()
        .unwrap();
        assert_eq!(r.resources.inputs[0], input);
        assert_eq!(r.resources.output, output);
        assert_eq!(r.resources.tmpdir, tmp);
    }

    #[cfg(unix)]
    #[test]
    fn attached_byte_path_options_preserve_raw_bytes() {
        use std::os::unix::ffi::OsStringExt;
        let input = OsString::from_vec(vec![b'-', b'a', b'i', b'\xff']);
        let output = OsString::from_vec(b"--output-file=out\xfe".to_vec());
        let tempdir = OsString::from_vec(b"--tempdir=tmp\xfd".to_vec());
        let r = parse(vec![
            "x".into(),
            "--lcov-compat=2.6".into(),
            input,
            output.clone(),
            tempdir.clone(),
        ])
        .unwrap()
        .unwrap();
        assert_eq!(
            r.resources.inputs,
            vec![PathBuf::from(OsString::from_vec(vec![b'i', b'\xff']))]
        );
        assert_eq!(
            r.resources.output,
            PathBuf::from(OsString::from_vec(b"out\xfe".to_vec()))
        );
        assert_eq!(
            r.resources.tmpdir,
            PathBuf::from(OsString::from_vec(b"tmp\xfd".to_vec()))
        );
    }
    #[test]
    fn size_detects_overflow() {
        assert_eq!(size("18446744073709551615G"), None);
    }
}
