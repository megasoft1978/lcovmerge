use crate::common::{Error, Options, MAX_LINE, MIN_WORKER_BUDGET};
use std::ffi::{OsStr, OsString};
use std::fs::File;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;

pub const HELP: &str = "usage: lcovmerge [options] a.info b.info ... -o out.info
  -o, --output FILE           output file, or - for stdout
  --mem-limit SIZE            arena cap; bytes or binary K/M/G, case-insensitive
                              default 64M; minimum 8M per external-sort job
  --tmpdir DIR                temporary run directory
  -j, --jobs N                1-32 jobs (default min(4, cores), memory-limited); N>=2 given
                              explicitly also threads sorted-input merges of large inputs
  --prefix-strip PREFIX       strip an SF path prefix at a path boundary
  --rebase OLD=NEW            rewrite SF path prefixes at path boundaries
  --include GLOB              include matching SF paths (repeatable)
  --exclude GLOB              exclude matching SF paths; exclusions win
  --branch-coverage on|off    retain or drop BRDA rows (default on)
  --no-function-data          drop FN/FNDA/FNL/FNA data
  --strict-checksum           exit 2 on conflict; otherwise warn/use lexical min
  --warn-unknown              report preserved unknown records
  -q                          suppress progress
  -v                          show progress and merged statistics
  --stats                     print merged LF/LH/FNF/FNH/BRF/BRH to stderr
  --version                   print version and git commit
  -h, --help                  show this text
  inputs: shell expands globs; @file reads paths (8 levels), @@ escapes @
  filters: rebase, prefix-strip, includes, then exclusions
  sorted regular files stream on one job unless -j N (N>=2) is given; fallback uses --jobs
  exits: 0 success, 1 usage, 2 input/format, 3 I/O/allocation/interruption
";

fn decimal(value: &str) -> Option<u64> {
    if value.is_empty() {
        return None;
    }
    value.bytes().try_fold(0u64, |n, digit| {
        if digit.is_ascii_digit() {
            n.checked_mul(10)?.checked_add(u64::from(digit - b'0'))
        } else {
            None
        }
    })
}

fn size(value: &str) -> Option<usize> {
    let last = *value.as_bytes().last()?;
    let shift = match last {
        b'k' | b'K' => 10,
        b'm' | b'M' => 20,
        b'g' | b'G' => 30,
        _ => 0,
    };
    let number = if shift == 0 {
        value
    } else {
        &value[..value.len() - 1]
    };
    usize::try_from(decimal(number)?.checked_mul(1u64 << shift)?).ok()
}

fn usage(first: &str) -> Error {
    Error::new(
        1,
        format!("invalid or incomplete option near '{first}'\n{HELP}"),
    )
}

pub fn parse(args: impl IntoIterator<Item = OsString>) -> Result<Option<Options>, Error> {
    let args: Vec<OsString> = args.into_iter().collect();
    let first = args.get(1).and_then(|s| s.to_str()).unwrap_or("<path>");
    let mut options = Options::default();
    let mut end_options = false;
    let mut index = 1;
    while index < args.len() {
        let argument = &args[index];
        let arg = argument.to_str().unwrap_or("");
        if !end_options && arg.is_empty() && argument.as_encoded_bytes().starts_with(b"-") {
            return Err(usage(first));
        }
        if !end_options && arg == "--" {
            end_options = true;
            index += 1;
            continue;
        }
        if !end_options && matches!(arg, "--help" | "-h") {
            std::io::stdout()
                .write_all(HELP.as_bytes())
                .map_err(|error| Error::new(3, format!("cannot write help: {error}")))?;
            return Ok(None);
        }
        if !end_options && arg == "--version" {
            writeln!(
                std::io::stdout(),
                "lcovmerge {} (git {})",
                env!("CARGO_PKG_VERSION"),
                env!("LCOVMERGE_GIT_COMMIT")
            )
            .map_err(|error| Error::new(3, format!("cannot write version: {error}")))?;
            return Ok(None);
        }
        if !end_options {
            let needs_value = matches!(
                arg,
                "-o" | "--output"
                    | "--mem-limit"
                    | "--tmpdir"
                    | "-j"
                    | "--jobs"
                    | "--prefix-strip"
                    | "--rebase"
                    | "--include"
                    | "--exclude"
                    | "--branch-coverage"
            );
            let value = if needs_value {
                index += 1;
                args.get(index).ok_or_else(|| usage(first))?
            } else {
                OsStr::new("")
            };
            if matches!(arg, "-o" | "--output" | "--tmpdir") {
                if arg == "--tmpdir" {
                    options.tmpdir = value.into();
                } else {
                    options.output = value.into();
                }
                index += 1;
                continue;
            }
            let value = value.to_str().ok_or_else(|| usage(first))?;
            match arg {
                "-o" | "--output" => unreachable!("handled file option"),
                "--mem-limit" => options.mem_limit = size(value).ok_or_else(|| usage(first))?,
                "--tmpdir" => options.tmpdir = value.into(),
                "-j" | "--jobs" => {
                    let jobs = decimal(value)
                        .filter(|&n| (1..=32).contains(&n))
                        .ok_or_else(|| usage(first))?;
                    options.jobs = jobs as usize;
                    options.jobs_explicit = true;
                }
                "--prefix-strip" => options.prefix_strip = Some(value.to_owned()),
                "--rebase" => {
                    let (old, new) = value.split_once('=').ok_or_else(|| usage(first))?;
                    options.rebases.push((old.to_owned(), new.to_owned()));
                }
                "--include" => options.includes.push(value.to_owned()),
                "--exclude" => options.excludes.push(value.to_owned()),
                "--branch-coverage" => {
                    options.branch_coverage = match value {
                        "on" => true,
                        "off" => false,
                        _ => return Err(usage(first)),
                    }
                }
                "--no-function-data" => options.function_data = false,
                "--strict-checksum" => options.strict_checksum = true,
                "--warn-unknown" => options.warn_unknown = true,
                "-q" => options.quiet = true,
                "-v" => options.verbose = true,
                "--stats" => options.stats = true,
                _ if arg.starts_with('-') && arg != "-" => return Err(usage(first)),
                _ => options.inputs.push(argument.into()),
            }
        } else {
            options.inputs.push(argument.into());
        }
        index += 1;
    }
    if !options.jobs_explicit {
        options.jobs = options
            .jobs
            .min(options.mem_limit / MIN_WORKER_BUDGET)
            .max(1);
    }
    if options.output.as_os_str().is_empty()
        || options.inputs.is_empty()
        || options.mem_limit < MIN_WORKER_BUDGET
        || options.jobs > options.mem_limit / MIN_WORKER_BUDGET
    {
        return Err(usage(first));
    }
    let inputs = std::mem::take(&mut options.inputs);
    for input in inputs {
        expand(input.as_os_str(), 0, &mut options.inputs)?;
    }
    if options.inputs.is_empty() {
        return Err(Error::new(1, "no input files"));
    }
    Ok(Some(options))
}

fn strip_at(argument: &OsStr, count: usize) -> Option<&OsStr> {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        let bytes = argument.as_bytes();
        bytes
            .get(..count)
            .filter(|head| head.iter().all(|&b| b == b'@'))?;
        Some(OsStr::from_bytes(&bytes[count..]))
    }
    #[cfg(not(unix))]
    {
        let text = argument.to_str()?;
        let prefix = if count == 2 { "@@" } else { "@" };
        text.strip_prefix(prefix).map(OsStr::new)
    }
}

fn expand(argument: &OsStr, depth: usize, inputs: &mut Vec<PathBuf>) -> Result<(), Error> {
    if depth > 8 {
        return Err(Error::new(1, "@listfile nesting exceeds 8 levels"));
    }
    if strip_at(argument, 2).is_some() {
        inputs.push(strip_at(argument, 1).unwrap().into());
        return Ok(());
    }
    let Some(path) = strip_at(argument, 1) else {
        inputs.push(argument.into());
        return Ok(());
    };
    if !std::fs::metadata(path).is_ok_and(|m| m.is_file()) {
        return Err(Error::at(3, path, 0, "list file must be a regular file"));
    }
    let file = File::open(path).map_err(|_| Error::at(3, path, 0, "cannot open listfile"))?;
    let mut reader = BufReader::new(file);
    let mut line = Vec::new();
    let mut number = 0;
    loop {
        crate::common::check_cancelled()?;
        line.clear();
        let mut have_line = false;
        loop {
            let available = reader
                .fill_buf()
                .map_err(|_| Error::at(3, path, number + 1, "invalid listfile line"))?;
            if available.is_empty() {
                break;
            }
            have_line = true;
            let newline = memchr::memchr(b'\n', available);
            let count = newline.map_or(available.len(), |pos| pos + 1);
            let content_count = count - usize::from(newline.is_some());
            if line.len() + content_count > MAX_LINE {
                return Err(Error::at(2, path, number + 1, "invalid listfile line"));
            }
            line.extend_from_slice(&available[..content_count]);
            reader.consume(count);
            if newline.is_some() {
                break;
            }
        }
        if !have_line {
            break;
        }
        number += 1;
        if line.last() == Some(&b'\r') {
            line.pop();
        }
        let text = std::str::from_utf8(&line)
            .map_err(|_| Error::at(2, path, number, "invalid listfile line"))?;
        if text.contains('\0') {
            return Err(Error::at(2, path, number, "invalid listfile line"));
        }
        if !text.is_empty() && !text.starts_with('#') {
            expand(OsStr::new(text), depth + 1, inputs)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn size_boundaries() {
        assert_eq!(size("8m"), Some(8 << 20));
        assert_eq!(size("18446744073709551615G"), None);
        for invalid in ["", "-1", "+1", " 1", "1MiB", "1.5M"] {
            assert_eq!(size(invalid), None);
        }
    }
    #[test]
    fn explicit_jobs_require_memory() {
        let args = [
            "lcovmerge",
            "--mem-limit",
            "8M",
            "-j",
            "2",
            "a.info",
            "-o",
            "out",
        ]
        .map(OsString::from);
        assert_eq!(parse(args).unwrap_err().code, 1);
    }

    #[test]
    fn listfile_blank_rows_do_not_end_expansion() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("inputs.list");
        std::fs::write(&path, "\n# comment\n\na.info\n\nb.info\n").unwrap();
        let mut inputs = Vec::new();
        expand(OsStr::new(&format!("@{}", path.display())), 0, &mut inputs).unwrap();
        assert_eq!(inputs, [PathBuf::from("a.info"), PathBuf::from("b.info")]);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn native_unix_paths_survive_cli_and_merge() {
        use std::os::unix::ffi::{OsStrExt, OsStringExt};
        let dir = tempfile::tempdir().unwrap();
        let input = dir
            .path()
            .join(OsString::from_vec(b"input-\xff.info".to_vec()));
        let output = dir
            .path()
            .join(OsString::from_vec(b"output-\xfe.info".to_vec()));
        std::fs::write(&input, "SF:/raw.c\nDA:1,2\nend_of_record\n").unwrap();
        let options = parse([
            OsString::from("lcovmerge"),
            OsString::from("-q"),
            input.clone().into_os_string(),
            OsString::from("-o"),
            output.clone().into_os_string(),
        ])
        .unwrap()
        .unwrap();
        assert_eq!(options.inputs[0], input);
        crate::engine::run(options).unwrap();
        assert!(std::fs::read_to_string(output)
            .unwrap()
            .contains("DA:1,2\n"));

        let escaped = OsString::from_vec(b"@@\xff.info".to_vec());
        let options = parse([
            OsString::from("lcovmerge"),
            escaped,
            OsString::from("-o"),
            OsString::from("out"),
        ])
        .unwrap()
        .unwrap();
        assert_eq!(options.inputs[0].as_os_str().as_bytes(), b"@\xff.info");

        let normal_input = dir.path().join("normal.info");
        std::fs::write(&normal_input, "SF:/list.c\nDA:1,1\nend_of_record\n").unwrap();
        let list = dir.path().join(OsString::from_vec(b"list-\xfd".to_vec()));
        std::fs::write(&list, format!("{}\n", normal_input.display())).unwrap();
        let mut list_argument = OsString::from("@");
        list_argument.push(list.as_os_str());
        let options = parse([
            OsString::from("lcovmerge"),
            list_argument,
            OsString::from("-o"),
            OsString::from("out"),
        ])
        .unwrap()
        .unwrap();
        assert_eq!(options.inputs, [normal_input]);
    }

    #[cfg(unix)]
    #[test]
    fn native_path_arguments_preserve_their_bytes() {
        use std::os::unix::ffi::{OsStrExt, OsStringExt};
        let input = OsString::from_vec(b"input-\xff.info".to_vec());
        let output = OsString::from_vec(b"output-\xfe.info".to_vec());
        let options = parse([
            OsString::from("lcovmerge"),
            input.clone(),
            OsString::from("-o"),
            output.clone(),
        ])
        .unwrap()
        .unwrap();
        assert_eq!(options.inputs[0].as_os_str(), input.as_os_str());
        assert_eq!(options.output.as_os_str(), output.as_os_str());
        let escaped = OsString::from_vec(b"@@\xff.info".to_vec());
        let options = parse([
            OsString::from("lcovmerge"),
            escaped,
            OsString::from("-o"),
            output,
        ])
        .unwrap()
        .unwrap();
        assert_eq!(options.inputs[0].as_os_str().as_bytes(), b"@\xff.info");
    }
}
