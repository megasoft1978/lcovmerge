use self::merge::{merge_into, write_sections};
use self::record::Section;
use super::Request;
use crate::common::{cancelled, check_cancelled, stdin_reader, stdout_writer, Error, MAX_LINE};
use flate2::read::MultiGzDecoder;
use flate2::write::GzEncoder;
use flate2::Compression;
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader, BufWriter, Read, Write};
use std::path::Path;

#[allow(dead_code)] // Kept private until spill execution is introduced.
#[path = "codec.rs"]
mod codec;
#[path = "line_spill.rs"]
mod line_spill;
#[path = "merge.rs"]
mod merge;
#[path = "policy.rs"]
mod policy;
#[path = "record.rs"]
mod record;
#[path = "source.rs"]
mod source;
use policy::{
    can_recover, num, parse_branch, parse_fn, parse_mcdc, parse_named_count,
    validate_or_generate_checksum,
};

// read_until retries Interrupted indefinitely. Cancellation must escape that loop.
struct StopOnCancel<R>(R);
impl<R: Read> Read for StopOnCancel<R> {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        match self.0.read(buffer) {
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted && cancelled() => {
                Err(std::io::Error::other("interrupted"))
            }
            result => result,
        }
    }
}

pub fn run(request: Request) -> Result<(), Error> {
    let opt = &request.resources;
    if opt.inputs.is_empty() {
        return Err(Error::new(1, "LCOV profile requires at least one -a input"));
    }
    for category in &request.ignore_errors {
        if ![
            "annotate",
            "branch",
            "callback",
            "category",
            "child",
            "corrupt",
            "count",
            "deprecated",
            "empty",
            "excessive",
            "format",
            "fork",
            "gcov",
            "graph",
            "inconsistent",
            "internal",
            "mismatch",
            "missing",
            "negative",
            "package",
            "parallel",
            "parent",
            "path",
            "range",
            "source",
            "unmapped",
            "unreachable",
            "unsupported",
            "unused",
            "usage",
            "utility",
            "version",
        ]
        .contains(&category.to_ascii_lowercase().as_str())
        {
            return Err(Error::new(
                1,
                format!("unknown argument for --ignore-errors: '{category}'"),
            ));
        }
    }
    if opt.inputs.iter().filter(|p| p.as_os_str() == "-").count() > 1 {
        return Err(Error::new(1, "stdin may appear only once in LCOV inputs"));
    }

    if let Some(result) = try_line_spill(&request)? {
        return result;
    }

    let mut sections: Vec<Section> = Vec::new();
    let mut indexes: HashMap<(String, String), usize> = HashMap::new();
    let mut held = 0usize;
    for path in &opt.inputs {
        check_cancelled()?;
        let label = if path.as_os_str() == "-" {
            "-".to_owned()
        } else {
            path.display().to_string()
        };
        let reader: Box<dyn Read> = if path.as_os_str() == "-" {
            stdin_reader().map_err(|e| Error::new(3, format!("cannot read stdin: {e}")))?
        } else {
            let file =
                File::open(path).map_err(|e| Error::new(3, format!("cannot open {label}: {e}")))?;
            if is_gzip_path(path) {
                Box::new(MultiGzDecoder::new(file))
            } else {
                Box::new(file)
            }
        };
        if let Err(e) = parse_input(
            reader,
            &label,
            &request,
            &mut sections,
            &mut indexes,
            &mut held,
            opt.mem_limit,
        ) {
            if request.keep_going && e.code == 2 {
                continue;
            }
            return Err(e);
        }
    }
    for s in &mut sections {
        for blocks in s.branches.values_mut() {
            blocks.retain(|b| !b.is_empty());
        }
    }
    let mut source_checksums: HashMap<(String, u64), String> = HashMap::new();
    for s in &sections {
        for (&ln, (_, sum)) in &s.lines {
            if let Some(sum) = sum {
                let key = (s.source.clone(), ln);
                if source_checksums.get(&key).is_some_and(|old| old != sum) {
                    let e = Error::new(2, format!("checksum mismatch at {}:{ln}", s.source));
                    if !can_recover(&request, &e) {
                        return Err(e);
                    }
                    continue;
                }
                source_checksums.insert(key, sum.clone());
            }
        }
    }
    for s in &mut sections {
        for (&ln, value) in &mut s.lines {
            if value.1.is_none() {
                value.1 = source_checksums.get(&(s.source.clone(), ln)).cloned();
            }
        }
    }
    for i in 0..sections.len() {
        let source = sections[i].source.clone();
        let known = sections
            .iter()
            .filter(|x| x.source == source)
            .filter_map(|x| x.version.as_ref())
            .next()
            .cloned();
        if let Some(v) = known {
            if sections[i].version.as_ref().is_some_and(|x| x != &v) {
                let e = Error::new(2, format!("conflicting source versions for {source}"));
                if !can_recover(&request, &e) {
                    return Err(e);
                }
            }
            sections[i].version = Some(v);
        }
    }
    policy::derive_function_ends(&mut sections, &request)?;
    sections.sort_by(|a, b| {
        a.source
            .cmp(&b.source)
            .then_with(|| a.testcase.cmp(&b.testcase))
    });
    if opt.output.as_os_str() == "-" || opt.output.as_os_str().is_empty() {
        let stdout =
            stdout_writer().map_err(|e| Error::new(3, format!("cannot write stdout: {e}")))?;
        write_sections(
            &mut BufWriter::new(stdout),
            &sections,
            request.checksum || opt.strict_checksum,
            opt.function_data,
            opt.branch_coverage,
            request.mcdc_coverage,
        )
    } else {
        let mut staged = tempfile::NamedTempFile::new_in(
            opt.output
                .parent()
                .filter(|p| !p.as_os_str().is_empty())
                .unwrap_or(Path::new(".")),
        )
        .map_err(|e| {
            Error::new(
                3,
                format!("cannot stage output near {}: {e}", opt.output.display()),
            )
        })?;
        let result = if is_gzip_path(&opt.output) {
            let mut encoder = GzEncoder::new(&mut staged, Compression::default());
            let written = {
                let mut writer = BufWriter::new(&mut encoder);
                write_sections(
                    &mut writer,
                    &sections,
                    request.checksum || opt.strict_checksum,
                    opt.function_data,
                    opt.branch_coverage,
                    request.mcdc_coverage,
                )
            };
            written.and_then(|_| {
                encoder
                    .finish()
                    .map(|_| ())
                    .map_err(|e| Error::new(3, format!("gzip output failed: {e}")))
            })
        } else {
            write_sections(
                &mut BufWriter::new(&mut staged),
                &sections,
                request.checksum || opt.strict_checksum,
                opt.function_data,
                opt.branch_coverage,
                request.mcdc_coverage,
            )
        }
        .and_then(|_| {
            staged
                .as_file()
                .sync_all()
                .map_err(|e| Error::new(3, format!("cannot sync staged output: {e}")))
        });
        result?;
        check_cancelled()?;
        staged.persist(&opt.output).map_err(|e| {
            Error::new(
                3,
                format!("cannot replace {}: {}", opt.output.display(), e.error),
            )
        })?;
        Ok(())
    }
}

// This first executor increment deliberately handles only regular-file DA profiles. The
// preflight pass is non-consuming and sends every richer family to the reference path.
fn try_line_spill(req: &Request) -> Result<Option<Result<(), Error>>, Error> {
    use std::io::BufRead;
    let o = &req.resources;
    if req.checksum || o.strict_checksum || o.inputs.iter().any(|p| p.as_os_str() == "-") {
        return Ok(None);
    }
    // Keep at most one preflight reader open at a time; large input lists are not retained.
    for p in &o.inputs {
        if !p.metadata().is_ok_and(|m| m.is_file()) {
            return Ok(None);
        }
        let f = match File::open(p) {
            Ok(f) => f,
            Err(_) => return Ok(None),
        };
        let r: Box<dyn Read> = if is_gzip_path(p) {
            Box::new(MultiGzDecoder::new(f))
        } else {
            Box::new(f)
        };
        let label = p.display().to_string();
        // Eligibility scan checks only record families/section framing. Detailed DA and path
        // validation occurs in the streaming execution pass; malformed profiles fall back.
        let mut r = BufReader::with_capacity(crate::common::IO_BLOCK, StopOnCancel(r));
        let mut buf = Vec::with_capacity(4096);
        let mut line = 0u64;
        let mut open = false;
        loop {
            check_cancelled()?;
            let n = (&mut r)
                .take((MAX_LINE + 2) as u64)
                .read_until(b'\n', &mut buf)
                .map_err(|e| Error::at(3, &label, line + 1, format!("input read failed: {e}")))?;
            if n == 0 {
                break;
            }
            line += 1;
            if buf.last() == Some(&b'\n') {
                buf.pop();
            }
            if buf.last() == Some(&b'\r') {
                buf.pop();
            }
            let eligible = if buf.len() > MAX_LINE || buf.contains(&0) {
                false
            } else if let Ok(s) = std::str::from_utf8(&buf) {
                if s.is_empty() || s.starts_with('#') {
                    true
                } else if s.starts_with("TN:") {
                    !open
                } else if s.starts_with("SF:") {
                    open = true;
                    true
                } else if s == "end_of_record" {
                    if !open {
                        false
                    } else {
                        open = false;
                        true
                    }
                } else if s.starts_with("DA:")
                    || s.starts_with("VER:")
                    || [
                        "LF:", "LH:", "FNF:", "FNH:", "BRF:", "BRH:", "MD:", "MCF:", "MCH:",
                    ]
                    .iter()
                    .any(|x| s.starts_with(x))
                    || (!o.function_data
                        && ["FN:", "FNDA:", "FNL:", "FNA:"]
                            .iter()
                            .any(|x| s.starts_with(x)))
                    || (!o.branch_coverage && s.starts_with("BRDA:"))
                    || (!req.mcdc_coverage && s.starts_with("MCDC:"))
                {
                    open
                } else {
                    false
                }
            } else {
                false
            };
            buf.clear();
            if !eligible {
                return Ok(None);
            }
        }
        // Let legacy parser diagnose an unfinished or malformed section boundary.
        if open {
            return Ok(None);
        }
    }
    // Keep the current raw line plus retained SF and TN strings outside the arena budget.
    let reserve = 3 * MAX_LINE + 3 * crate::common::IO_BLOCK + 64 * 1024;
    let budget = o.mem_limit.saturating_sub(reserve).max(1);
    let memory_fanin = (o
        .mem_limit
        .saturating_sub(3 * crate::common::IO_BLOCK + 64 * 1024)
        / (3 * MAX_LINE + crate::common::IO_BLOCK + 4096))
        .clamp(2, 32);
    let fanin = descriptor_fanin(memory_fanin)?;
    let mut spill = line_spill::Spill::new(&o.tmpdir, budget, fanin)?;
    for (i, path) in o.inputs.iter().enumerate() {
        check_cancelled()?;
        let label = path.display().to_string();
        let f = File::open(path).map_err(|e| Error::new(3, format!("cannot open {label}: {e}")))?;
        let reader: Box<dyn Read> = if is_gzip_path(path) {
            Box::new(MultiGzDecoder::new(f))
        } else {
            Box::new(f)
        };
        let mut input = BufReader::with_capacity(crate::common::IO_BLOCK, StopOnCancel(reader));
        let mut buf = Vec::with_capacity(4096);
        let mut tc = String::new();
        let mut section_tc = String::new();
        let mut source: Option<String> = None;
        let mut selected = false;
        let mut section = 0u64;
        let mut ordinal = 0u64;
        let mut line_no = 0u64;
        loop {
            check_cancelled()?;
            buf.clear();
            let n = (&mut input)
                .take((MAX_LINE + 2) as u64)
                .read_until(b'\n', &mut buf)
                .map_err(|e| {
                    Error::at(3, &label, line_no + 1, format!("input read failed: {e}"))
                })?;
            if n == 0 {
                break;
            }
            line_no += 1;
            if buf.last() == Some(&b'\n') {
                buf.pop();
            }
            if buf.last() == Some(&b'\r') {
                buf.pop();
            }
            if buf.len() > MAX_LINE {
                let e = Error::at(2, &label, line_no, "line exceeds 1 MiB");
                if !can_recover(req, &e) {
                    return Ok(Some(Err(e)));
                }
                continue;
            }
            let s = match std::str::from_utf8(&buf) {
                Ok(s) => s,
                Err(_) => {
                    let e = Error::at(2, &label, line_no, "invalid UTF-8 tracefile record");
                    if !can_recover(req, &e) {
                        return Ok(Some(Err(e)));
                    }
                    continue;
                }
            };
            if s.is_empty() || s.starts_with('#') {
                continue;
            }
            if let Some(v) = s.strip_prefix("TN:") {
                if source.is_some() {
                    let e = Error::at(2, &label, line_no, "TN inside an open section");
                    if !can_recover(req, &e) {
                        return Ok(Some(Err(e)));
                    }
                    continue;
                }
                tc = v.to_owned();
                continue;
            }
            if let Some(sf) = s.strip_prefix("SF:") {
                if source.is_some() {
                    source = None;
                }
                if sf.is_empty() {
                    let e = Error::at(2, &label, line_no, "empty SF path");
                    if !can_recover(req, &e) {
                        return Ok(Some(Err(e)));
                    }
                    continue;
                }
                let rewritten = match crate::path::rewrite_path(o, sf) {
                    Ok(p) => p,
                    Err(e) => {
                        let e = Error::at(e.code, &label, line_no, e.message);
                        if !can_recover(req, &e) {
                            return Ok(Some(Err(e)));
                        }
                        continue;
                    }
                };
                selected = crate::path::path_selected(o, &rewritten);
                source = Some(rewritten);
                section += 1;
                ordinal = 0;
                section_tc = if req.forget_test_names {
                    String::new()
                } else {
                    tc.clone()
                };
                tc.clear();
                continue;
            }
            if s == "end_of_record" {
                source = None;
                tc.clear();
                section_tc.clear();
                continue;
            }
            if let Some(v) = s.strip_prefix("VER:") {
                if let Some(src) = source.as_ref() {
                    ordinal += 1;
                    spill.push(line_spill::Entry {
                        kind: 0,
                        source: src.clone(),
                        testcase: String::new(),
                        value: v.to_owned(),
                        line: 0,
                        count: 0,
                        input: i as u64,
                        section,
                        ordinal,
                        label: label.clone(),
                        line_no,
                    })?;
                }
                continue;
            }
            if let Some(payload) = s.strip_prefix("DA:") {
                let negative_recovered = if req.ignore_errors.contains("negative") {
                    let mut fields = payload.split(',');
                    let line_field = fields.next().unwrap_or("");
                    let count_field = fields.next().unwrap_or("");
                    if count_field.starts_with('-')
                        && count_field.len() > 1
                        && count_field[1..].bytes().all(|b| b.is_ascii_digit())
                        && fields.next().is_none()
                    {
                        match num(Some(line_field), &label, line_no) {
                            Ok(ln) if ln > 0 => Some(ln),
                            Ok(_) => {
                                let e = Error::at(2, &label, line_no, "DA line must be positive");
                                if !can_recover(req, &e) {
                                    return Ok(Some(Err(e)));
                                }
                                continue;
                            }
                            Err(e) => {
                                if !can_recover(req, &e) {
                                    return Ok(Some(Err(e)));
                                }
                                continue;
                            }
                        }
                    } else {
                        None
                    }
                } else {
                    None
                };
                let line_data = if let Some(ln) = negative_recovered {
                    Some((ln, (0, None)))
                } else {
                    let mut sec = Section::default();
                    match sec.add_line(payload, &label, line_no) {
                        Ok(()) => sec.lines.into_iter().next(),
                        Err(e) => {
                            if !can_recover(req, &e) {
                                return Ok(Some(Err(e)));
                            }
                            continue;
                        }
                    }
                };
                if let Some(src) = source.as_ref() {
                    if selected {
                        let (ln, (count, _sum)) = line_data.unwrap();
                        ordinal += 1;
                        spill.push(line_spill::Entry {
                            kind: 1,
                            source: src.clone(),
                            testcase: section_tc.clone(),
                            value: String::new(),
                            line: ln,
                            count,
                            input: i as u64,
                            section,
                            ordinal,
                            label: label.clone(),
                            line_no,
                        })?;
                    }
                }
            }
        }
    }
    let final_run = spill.finish()?;
    let mut out = if o.output.as_os_str() == "-" || o.output.as_os_str().is_empty() {
        None
    } else {
        Some(
            tempfile::NamedTempFile::new_in(
                o.output
                    .parent()
                    .filter(|p| !p.as_os_str().is_empty())
                    .unwrap_or(Path::new(".")),
            )
            .map_err(|e| Error::new(3, format!("cannot stage output: {e}")))?,
        )
    };
    if let Some(tmp) = out.as_mut() {
        if is_gzip_path(&o.output) {
            let mut encoder = GzEncoder::new(tmp.as_file_mut(), Compression::default());
            {
                let mut writer = BufWriter::new(&mut encoder);
                emit_spill(&final_run, &mut writer, req)?;
                writer.flush().map_err(ioout)?;
            }
            encoder
                .finish()
                .map_err(|e| Error::new(3, format!("gzip output failed: {e}")))?;
        } else {
            let mut writer = BufWriter::new(tmp.as_file_mut());
            emit_spill(&final_run, &mut writer, req)?;
            writer.flush().map_err(ioout)?;
        }
    } else {
        let mut writer = BufWriter::new(
            stdout_writer().map_err(|e| Error::new(3, format!("cannot write stdout: {e}")))?,
        );
        emit_spill(&final_run, &mut writer, req)?;
        writer.flush().map_err(ioout)?;
    }
    if let Some(tmp) = out {
        tmp.as_file()
            .sync_all()
            .map_err(|e| Error::new(3, format!("cannot sync staged output: {e}")))?;
        check_cancelled()?;
        tmp.persist(&o.output).map_err(|e| {
            Error::new(
                3,
                format!("cannot replace {}: {}", o.output.display(), e.error),
            )
        })?;
    }
    if o.verbose {
        eprintln!(
            "compat spill: runs={} passes={} spill-bytes={} fan-in={} workers=1",
            spill.run_count, spill.passes, spill.traffic, spill.fanin
        );
    }
    let _ = spill.dir();
    Ok(Some(Ok(())))
}

fn descriptor_fanin(memory_fanin: usize) -> Result<usize, Error> {
    #[cfg(unix)]
    {
        let mut limits = std::mem::MaybeUninit::<libc::rlimit>::uninit();
        // SAFETY: getrlimit initializes the provided rlimit on success and does not retain it.
        if unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, limits.as_mut_ptr()) } == 0 {
            // SAFETY: the successful getrlimit call initialized the value.
            let soft = unsafe { limits.assume_init() }.rlim_cur as usize;
            let available = soft.saturating_sub(4).min(32);
            if available < 2 {
                return Err(Error::new(
                    3,
                    "file descriptor limit is too low for bounded compatibility merge",
                ));
            }
            return Ok(memory_fanin.min(available));
        }
    }
    Ok(memory_fanin)
}

#[inline(never)]
fn emit_spill(path: &Path, out: &mut dyn Write, req: &Request) -> Result<(), Error> {
    let f = File::open(path)
        .map_err(|e| Error::new(3, format!("cannot read compatibility run: {e}")))?;
    let mut r = BufReader::new(f);
    let mut pending = line_spill::read_next_public(&mut r)?;
    while let Some(first) = pending.take() {
        check_cancelled()?;
        let src = first.source.clone();
        let mut version = None;
        let mut row = Some(first);
        while row.as_ref().is_some_and(|x| x.source == src && x.kind == 0) {
            let x = row.take().unwrap();
            if version.is_none() {
                version = Some(x.value.clone());
            } else if version.as_ref() != Some(&x.value) {
                let e = Error::at(2, &x.label, x.line_no, "conflicting source versions");
                if !can_recover(req, &e) {
                    return Err(e);
                }
            }
            row = line_spill::read_next_public(&mut r)?;
        }
        while row.as_ref().is_some_and(|x| x.source == src) {
            let tc = row.as_ref().unwrap().testcase.clone();
            writeln!(out, "TN:{tc}\nSF:{src}").map_err(ioout)?;
            if let Some(v) = &version {
                writeln!(out, "VER:{v}").map_err(ioout)?;
            }
            let mut lf = 0u64;
            let mut lh = 0u64;
            let mut line = 0u64;
            let mut count = 0u64;
            while row
                .as_ref()
                .is_some_and(|x| x.source == src && x.testcase == tc)
            {
                check_cancelled()?;
                let x = row.take().unwrap();
                if lf > 0 && x.line != line {
                    writeln!(out, "DA:{line},{}", count.min(i64::MAX as u64)).map_err(ioout)?;
                    if count > 0 {
                        lh = lh.checked_add(1).ok_or_else(|| {
                            Error::at(2, &x.label, x.line_no, "DA hit-line count overflow")
                        })?;
                    }
                }
                if lf == 0 || x.line != line {
                    line = x.line;
                    count = x.count.min(i64::MAX as u64);
                    lf = lf.checked_add(1).ok_or_else(|| {
                        Error::at(2, &x.label, x.line_no, "DA line count overflow")
                    })?;
                } else {
                    count = count.saturating_add(x.count).min(i64::MAX as u64);
                }
                row = line_spill::read_next_public(&mut r)?;
            }
            if lf > 0 {
                writeln!(out, "DA:{line},{}", count.min(i64::MAX as u64)).map_err(ioout)?;
                if count > 0 {
                    lh = lh
                        .checked_add(1)
                        .ok_or_else(|| Error::new(2, "DA hit-line count overflow"))?;
                }
            }
            writeln!(out, "LF:{lf}\nLH:{lh}\nend_of_record").map_err(ioout)?;
        }
        pending = row;
    }
    Ok(())
}
fn ioout(e: std::io::Error) -> Error {
    Error::new(3, format!("output write failed: {e}"))
}

fn parse_input(
    reader: Box<dyn Read>,
    label: &str,
    req: &Request,
    sections: &mut Vec<Section>,
    indexes: &mut HashMap<(String, String), usize>,
    held: &mut usize,
    limit: usize,
) -> Result<(), Error> {
    let source_budget = if req.checksum || req.resources.strict_checksum {
        (limit / 4).min(2 * MAX_LINE)
    } else {
        0
    };
    let mut source_index = source::SourceIndex::new(&req.resources.tmpdir, source_budget)?;
    let limit = limit.saturating_sub(source_budget);
    let mut input = BufReader::with_capacity(crate::common::IO_BLOCK, StopOnCancel(reader));
    let mut line = Vec::with_capacity(4096);
    let mut line_no = 0u64;
    let mut testcase = String::new();
    let mut current: Option<Section> = None;
    let mut modern_decls: HashMap<u64, (u64, Option<u64>)> = HashMap::new();
    let mut legacy_decls: HashMap<String, (u64, Option<u64>)> = HashMap::new();
    macro_rules! recover_line {
        ($expr:expr) => {
            if let Err(err) = $expr {
                if !can_recover(req, &err) {
                    return Err(err);
                } else {
                    continue;
                }
            }
        };
    }
    loop {
        check_cancelled()?;
        line.clear();
        let n = (&mut input)
            .take((MAX_LINE + 2) as u64)
            .read_until(b'\n', &mut line)
            .map_err(|e| Error::at(3, label, line_no + 1, format!("input read failed: {e}")))?;
        if n == 0 {
            break;
        }
        line_no += 1;
        if line.last() == Some(&b'\n') {
            line.pop();
        }
        if line.last() == Some(&b'\r') {
            line.pop();
        }
        if line.len() > MAX_LINE {
            let e = Error::at(2, label, line_no, "line exceeds 1 MiB");
            if can_recover(req, &e) {
                continue;
            } else {
                return Err(e);
            }
        }
        if line.contains(&0) {
            let e = Error::at(2, label, line_no, "NUL byte in tracefile");
            if can_recover(req, &e) {
                continue;
            } else {
                return Err(e);
            }
        }
        let s = std::str::from_utf8(&line)
            .map_err(|_| Error::at(2, label, line_no, "invalid UTF-8 tracefile record"));
        let s = match s {
            Ok(s) => s,
            Err(e) => {
                if can_recover(req, &e) {
                    continue;
                } else {
                    return Err(e);
                }
            }
        };
        if s.is_empty() || s.starts_with('#') {
            continue;
        }
        if let Some(tn) = s.strip_prefix("TN:") {
            if current.is_some() {
                let e = Error::at(2, label, line_no, "TN inside an open section");
                if can_recover(req, &e) {
                    continue;
                } else {
                    return Err(e);
                }
            }
            testcase = tn.to_owned();
            continue;
        }
        if let Some(sf) = s.strip_prefix("SF:") {
            if let Some(section) = current.take() {
                finish_section(section, sections, indexes, held, limit, label, line_no, req)?;
            }
            if sf.is_empty() {
                let e = Error::at(2, label, line_no, "empty SF path");
                if can_recover(req, &e) {
                    continue;
                } else {
                    return Err(e);
                }
            }
            let rewritten = match crate::path::rewrite_path(&req.resources, sf) {
                Ok(p) => p,
                Err(e) => {
                    let e = Error::at(e.code, label, line_no, e.message);
                    if can_recover(req, &e) {
                        continue;
                    } else {
                        return Err(e);
                    }
                }
            };
            current = Some(Section {
                testcase: if req.forget_test_names {
                    String::new()
                } else {
                    testcase.clone()
                },
                selected: crate::path::path_selected(&req.resources, &rewritten),
                source: rewritten,
                ..Section::default()
            });
            testcase.clear();
            modern_decls.clear();
            legacy_decls.clear();
            continue;
        }
        let Some(section) = current.as_mut() else {
            let e = Error::at(2, label, line_no, "record outside an SF section");
            if can_recover(req, &e) {
                continue;
            } else {
                return Err(e);
            }
        };
        if s == "end_of_record" {
            finish_section(
                current.take().unwrap(),
                sections,
                indexes,
                held,
                limit,
                label,
                line_no,
                req,
            )?;
            modern_decls.clear();
            legacy_decls.clear();
            continue;
        }
        if let Some(x) = s.strip_prefix("DA:") {
            match section.add_line(x, label, line_no) {
                Err(e) if e.message.contains("negative coverage count") && can_recover(req, &e) => {
                    let mut fields = x.splitn(3, ',');
                    let location = fields.next().unwrap_or("");
                    let _ = fields.next();
                    let checksum = fields.next().map(|v| format!(",{v}")).unwrap_or_default();
                    section.add_line(&format!("{location},0{checksum}"), label, line_no)?;
                }
                result => {
                    recover_line!(result);
                }
            }
            if req.resources.strict_checksum || req.checksum {
                validate_or_generate_checksum(section, x, req, label, line_no, &mut source_index)?;
            }
            recover_line!(ensure_budget(section, *held, limit, label, line_no));
            continue;
        }
        if let Some(x) = s.strip_prefix("VER:") {
            if section.version.as_deref().is_some_and(|v| v != x) {
                let e = Error::at(2, label, line_no, "conflicting source versions");
                if can_recover(req, &e) {
                    continue;
                } else {
                    return Err(e);
                }
            }
            section.version = Some(x.to_owned());
            continue;
        }
        if !req.resources.function_data
            && (s.starts_with("FN:")
                || s.starts_with("FNDA:")
                || s.starts_with("FNL:")
                || s.starts_with("FNA:"))
        {
            continue;
        }
        if let Some(x) = s.strip_prefix("FN:") {
            let (start, end, name) = match parse_fn(x, label, line_no) {
                Ok(v) => v,
                Err(e) => {
                    if can_recover(req, &e) {
                        continue;
                    } else {
                        return Err(e);
                    }
                }
            };
            legacy_decls.insert(name.clone(), (start, end));
            recover_line!(section.add_function(name, start, end, 0, label, line_no));
            recover_line!(ensure_budget(section, *held, limit, label, line_no));
            continue;
        }
        if let Some(x) = s.strip_prefix("FNDA:") {
            let (hits, name) = match parse_named_count(x, label, line_no) {
                Ok(v) => v,
                Err(e) => {
                    if can_recover(req, &e) {
                        continue;
                    } else {
                        return Err(e);
                    }
                }
            };
            let Some((start, end)) = legacy_decls.get(&name).copied() else {
                let e = Error::at(2, label, line_no, "FNDA references undeclared function");
                if can_recover(req, &e) {
                    continue;
                } else {
                    return Err(e);
                }
            };
            recover_line!(section.add_function(name, start, end, hits, label, line_no));
            recover_line!(ensure_budget(section, *held, limit, label, line_no));
            continue;
        }
        if let Some(x) = s.strip_prefix("FNL:") {
            let result = (|| {
                let mut f = x.split(',');
                let idx = num(f.next(), label, line_no)?;
                let start = num(f.next(), label, line_no)?;
                let end = match f.next() {
                    Some(v) => Some(num(Some(v), label, line_no)?),
                    None => None,
                };
                if f.next().is_some() || modern_decls.contains_key(&idx) {
                    return Err(Error::at(
                        2,
                        label,
                        line_no,
                        "invalid or duplicate FNL index",
                    ));
                }
                modern_decls.insert(idx, (start, end));
                Ok(())
            })();
            recover_line!(result);
            section.modern = true;
            continue;
        }
        if let Some(x) = s.strip_prefix("FNA:") {
            let result = (|| {
                let mut f = x.splitn(3, ',');
                let idx = num(f.next(), label, line_no)?;
                let hits = num(f.next(), label, line_no)?;
                let name = f
                    .next()
                    .filter(|x| !x.is_empty())
                    .ok_or_else(|| Error::at(2, label, line_no, "malformed FNA record"))?;
                let (start, end) = modern_decls.get(&idx).copied().ok_or_else(|| {
                    Error::at(2, label, line_no, "FNA references unknown function index")
                })?;
                section.add_function(name.to_owned(), start, end, hits, label, line_no)
            })();
            recover_line!(result);
            section.modern = true;
            ensure_budget(section, *held, limit, label, line_no)?;
            continue;
        }
        if let Some(x) = s.strip_prefix("BRDA:") {
            if req.resources.branch_coverage {
                recover_line!(parse_branch(section, x, label, line_no));
                recover_line!(ensure_budget(section, *held, limit, label, line_no));
            }
            continue;
        }
        if let Some(x) = s.strip_prefix("MCDC:") {
            if req.mcdc_coverage {
                recover_line!(parse_mcdc(section, x, label, line_no));
                recover_line!(ensure_budget(section, *held, limit, label, line_no));
            }
            continue;
        }
        if [
            "LF:", "LH:", "FNF:", "FNH:", "BRF:", "BRH:", "MD:", "MCF:", "MCH:",
        ]
        .iter()
        .any(|p| s.starts_with(p))
        {
            continue;
        }
        let e = Error::at(
            2,
            label,
            line_no,
            format!(
                "unsupported LCOV record '{}': refusing to discard semantics",
                s.split(':').next().unwrap_or(s)
            ),
        );
        if can_recover(req, &e) {
            continue;
        } else {
            return Err(e);
        }
    }
    if let Some(section) = current {
        finish_section(section, sections, indexes, held, limit, label, line_no, req)?;
    }
    Ok(())
}

fn ensure_budget(
    s: &Section,
    held: usize,
    limit: usize,
    label: &str,
    line: u64,
) -> Result<(), Error> {
    if held.saturating_add(s.memory_size()) > limit {
        return Err(Error::at(3,label,line,format!("LCOV compatibility working set exceeds --mem-limit ({limit} bytes); bounded spill runs are not yet implemented")));
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)] // Replaced by the bounded executor in the next integration phase.
fn finish_section(
    mut s: Section,
    sections: &mut Vec<Section>,
    indexes: &mut HashMap<(String, String), usize>,
    held: &mut usize,
    limit: usize,
    label: &str,
    line: u64,
    req: &Request,
) -> Result<(), Error> {
    if s.selected {
        let branch_lines: Vec<_> = s.branches.iter().map(|(&ln, b)| (ln, b.clone())).collect();
        for (branch_line, blocks) in branch_lines {
            let hits = blocks
                .iter()
                .flatten()
                .map(|b| u64::from(b.hits.is_some_and(|n| n > 0)))
                .try_fold(0u64, |a, b| a.checked_add(b))
                .ok_or_else(|| {
                    Error::at(2, label, branch_line, "branch-derived DA count overflow")
                })?;
            if let Some((line_hits, _)) = s.lines.get(&branch_line) {
                if req.check_data_consistency && (*line_hits > 0) != (hits > 0) {
                    let e = Error::at(
                        2,
                        label,
                        branch_line,
                        "inconsistent branch and line hit status",
                    );
                    if !can_recover(req, &e) {
                        return Err(e);
                    }
                }
            } else {
                let e = Error::at(
                    2,
                    label,
                    branch_line,
                    "inconsistent branch coverage without line coverage data",
                );
                if req.check_data_consistency && !can_recover(req, &e) {
                    return Err(e);
                }
                s.lines.insert(branch_line, (hits, None));
                s.saw_coverpoint = true;
            }
        }
        let mut pairs = Vec::new();
        for (&ln, items) in &s.mcdc {
            for (&(group, index, sense), item) in items {
                let other = if sense == 't' { 'f' } else { 't' };
                if !items.contains_key(&(group, index, other)) {
                    pairs.push((ln, group, index, other, item.expr.clone(), item.unreachable));
                }
            }
        }
        for (ln, g, i, sense, expr, unreachable) in pairs {
            s.mcdc.entry(ln).or_default().insert(
                (g, i, sense),
                record::Mcdc {
                    hits: 0,
                    expr,
                    unreachable,
                },
            );
        }
        for old in sections.iter().filter(|x| x.source == s.source) {
            if let Some(v) = &old.version {
                if s.version.as_ref().is_some_and(|n| n != v) {
                    let e = Error::at(2, label, line, "conflicting source versions");
                    if !can_recover(req, &e) {
                        return Err(e);
                    }
                }
                if s.version.is_none() {
                    s.version = Some(v.clone());
                }
            }
            for (name, function) in &s.functions {
                if old.testcase != s.testcase {
                    continue;
                }
                if old.functions.get(name).is_some_and(|existing| {
                    existing.line != function.line || existing.end_line != function.end_line
                }) {
                    let e = Error::at(
                        2,
                        label,
                        line,
                        format!("inconsistent function declaration for '{name}'"),
                    );
                    if !can_recover(req, &e) {
                        return Err(e);
                    }
                }
            }
            for (location, items) in &s.mcdc {
                if let Some(existing) = old.mcdc.get(location) {
                    for (key, item) in items {
                        if existing
                            .get(key)
                            .is_some_and(|prior| prior.expr != item.expr)
                        {
                            let e = Error::at(
                                2,
                                label,
                                line,
                                "inconsistent MC/DC expression across testcases",
                            );
                            if !can_recover(req, &e) {
                                return Err(e);
                            }
                        }
                    }
                }
            }
        }
    }
    if !s.saw_coverpoint || !s.selected {
        return Ok(());
    }
    let key = (s.testcase.clone(), s.source.clone());
    if let Some(&idx) = indexes.get(&key) {
        let before = sections[idx].memory_size();
        let mut merged = sections[idx].clone();
        if let Err(e) = merge_into(&mut merged, s, label) {
            if !can_recover(req, &e) {
                return Err(e);
            }
        } else {
            sections[idx] = merged;
        }
        let after = sections[idx].memory_size();
        *held = held.saturating_add(after.saturating_sub(before));
        if *held > limit {
            return Err(Error::at(3,label,line,format!("LCOV compatibility working set exceeds --mem-limit ({limit} bytes); bounded spill runs are not yet implemented")));
        }
    } else {
        *held = held
            .saturating_add(s.memory_size())
            .saturating_add(key.0.len() + key.1.len() + std::mem::size_of::<usize>());
        if *held > limit {
            return Err(Error::at(3,label,line,format!("LCOV compatibility working set exceeds --mem-limit ({limit} bytes); bounded spill runs are not yet implemented")));
        }
        indexes.insert(key, sections.len());
        sections.push(s);
    }
    Ok(())
}

fn is_gzip_path(p: &Path) -> bool {
    p.extension().is_some_and(|e| e.eq_ignore_ascii_case("gz"))
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::path::PathBuf;
    #[test]
    fn request_merges_tn_separately_and_regenerates_summary() {
        let mut r = Request::default();
        r.resources.inputs = vec![PathBuf::from("-")];
        let input =
            b"TN:a\nSF:x.c\nDA:1,2\nLF:99\nend_of_record\nTN:b\nSF:x.c\nDA:1,3\nend_of_record\n";
        let mut sections = Vec::new();
        let mut ix = HashMap::new();
        let mut bytes = 0;
        parse_input(
            Box::new(&input[..]),
            "fixture",
            &r,
            &mut sections,
            &mut ix,
            &mut bytes,
            1 << 20,
        )
        .unwrap();
        let mut out = Vec::new();
        write_sections(&mut out, &sections, false, true, false, false).unwrap();
        let out = String::from_utf8(out).unwrap();
        assert!(out.contains("TN:a\nSF:x.c\nDA:1,2\nLF:1\nLH:1"));
        assert!(out.contains("TN:b\nSF:x.c\nDA:1,3\n"));
        assert!(!out.contains("LF:99"));
    }
    #[test]
    fn checksum_preserves_first_available() {
        let mut a = Section::default();
        a.lines.insert(4, (1, None));
        let mut b = Section::default();
        b.lines.insert(4, (2, Some("sum".into())));
        merge_into(&mut a, b, "fixture").unwrap();
        assert_eq!(a.lines[&4], (3, Some("sum".into())));
    }

    #[test]
    fn modern_function_aliases_branches_and_mcdc_are_normalized() {
        let input=b"TN:case\nSF:x.c\nFNL:8,5,10\nFNA:8,3,zeta\nFNL:9,5,10\nFNA:9,0,alpha\nBRDA:5,0,0,2\nBRDA:5,0,1,0\nMCDC:5,2,t,1,0,A\nMCDC:5,2,f,0,0,A\nDA:5,2\nend_of_record\n";
        let mut req = Request::default();
        req.resources.branch_coverage = true;
        req.mcdc_coverage = true;
        let mut sections = Vec::new();
        let mut indexes = HashMap::new();
        let mut held = 0;
        parse_input(
            Box::new(&input[..]),
            "valid-info",
            &req,
            &mut sections,
            &mut indexes,
            &mut held,
            1 << 20,
        )
        .unwrap();
        let mut out = Vec::new();
        write_sections(&mut out, &sections, false, true, true, true).unwrap();
        let out = String::from_utf8(out).unwrap();
        assert_eq!(out,"TN:case\nSF:x.c\nFNL:0,5,10\nFNA:0,0,alpha\nFNA:0,3,zeta\nFNF:1\nFNH:1\nBRDA:5,0,0,2\nBRDA:5,0,1,0\nBRF:2\nBRH:1\nMCDC:5,2,t,1,0,A\nMCDC:5,2,f,0,0,A\nMCF:2\nMCH:1\nDA:5,2\nLF:1\nLH:1\nend_of_record\n");
    }

    #[test]
    fn ignored_format_error_skips_only_bad_record_and_disabled_functions_skip_payload() {
        let input =
            b"TN:x\nSF:x.c\nDA:not-a-line\nDA:2,4\nFN:garbage\nFNL:bad-payload\nend_of_record\n";
        let mut req = Request::default();
        req.resources.function_data = false;
        req.ignore_errors.insert("format".into());
        let mut sections = Vec::new();
        let mut indexes = HashMap::new();
        let mut held = 0;
        parse_input(
            Box::new(&input[..]),
            "recover-info",
            &req,
            &mut sections,
            &mut indexes,
            &mut held,
            1 << 20,
        )
        .unwrap();
        assert_eq!(sections[0].lines.get(&2).unwrap().0, 4);
        assert!(sections[0].functions.is_empty());
    }

    #[test]
    fn ignored_branch_line_inconsistency_synthesizes_da_from_taken_branches() {
        let input = b"TN:x\nSF:x.c\nBRDA:4,0,0,7\nBRDA:4,0,1,0\nend_of_record\n";
        let mut req = Request::default();
        req.resources.branch_coverage = true;
        req.ignore_errors.insert("inconsistent".into());
        let mut sections = Vec::new();
        let mut indexes = HashMap::new();
        let mut held = 0;
        parse_input(
            Box::new(&input[..]),
            "branch-only",
            &req,
            &mut sections,
            &mut indexes,
            &mut held,
            1 << 20,
        )
        .unwrap();
        assert_eq!(sections[0].lines.get(&4).unwrap().0, 1);
    }

    #[test]
    fn duplicate_branch_signatures_merge_by_occurrence_and_sort_exception_blocks() {
        // The BRDA ordering and totals are from the pinned lcov 2.6 oracle.
        let input=b"TN:dup\nSF:x.c\nDA:1,2\nBRDA:1,0,a,1\nBRDA:1,0,b,1\nBRDA:1,1,c,0\nBRDA:1,1,d,0\nBRDA:1,e2,ex,0\nend_of_record\nTN:dup\nSF:x.c\nDA:1,2\nBRDA:1,4,a2,2\nBRDA:1,4,b2,0\nBRDA:1,9,c2,1\nBRDA:1,9,d2,0\nBRDA:1,eU11,ex2,0\nend_of_record\n";
        let mut req = Request::default();
        req.resources.function_data = false;
        req.resources.branch_coverage = true;
        let mut sections = Vec::new();
        let mut indexes = HashMap::new();
        let mut held = 0;
        parse_input(
            Box::new(&input[..]),
            "duplicate-signatures",
            &req,
            &mut sections,
            &mut indexes,
            &mut held,
            1 << 20,
        )
        .unwrap();
        let mut out = Vec::new();
        write_sections(&mut out, &sections, false, false, true, false).unwrap();
        assert_eq!(String::from_utf8(out).unwrap(),"TN:dup\nSF:x.c\nBRDA:1,eU0,ex,0\nBRDA:1,1,a,3\nBRDA:1,1,b,1\nBRDA:1,2,c,1\nBRDA:1,2,d,0\nBRF:4\nBRH:3\nDA:1,4\nLF:1\nLH:1\nend_of_record\n");
    }

    #[test]
    fn request_runs_end_to_end_and_stages_output() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".cache/compat-engine");
        std::fs::create_dir_all(&root).unwrap();
        let input = root.join("request-run.info");
        let output = root.join("request-run.out");
        std::fs::write(
            &input,
            "TN:case\nSF:sample.c\nDA:3,2\nDA:3,4\nend_of_record\n",
        )
        .unwrap();
        let mut request = Request::default();
        request.resources.inputs = vec![input.clone()];
        request.resources.output = output.clone();
        run(request).unwrap();
        let bytes = std::fs::read_to_string(&output).unwrap();
        assert!(bytes.contains("DA:3,6\nLF:1\nLH:1\nend_of_record"));
        std::fs::remove_file(input).unwrap();
        std::fs::remove_file(output).unwrap();
    }

    #[test]
    fn oversized_single_source_or_testcase_section_spills_and_keeps_summaries() {
        for (source, testcase) in [
            ("s".repeat(1400), "t".to_owned()),
            ("s".to_owned(), "t".repeat(1400)),
        ] {
            let parent = tempfile::tempdir().unwrap();
            let mut spill = line_spill::Spill::new(parent.path(), 4096, 2).unwrap();
            for i in (1..=12).rev() {
                spill
                    .push(line_spill::Entry {
                        kind: 1,
                        source: source.clone(),
                        testcase: testcase.clone(),
                        value: String::new(),
                        line: i,
                        count: u64::from(i % 2 == 0),
                        input: 0,
                        section: 0,
                        ordinal: i,
                        label: "synthetic".into(),
                        line_no: i,
                    })
                    .unwrap();
            }
            let run = spill.finish().unwrap();
            assert!(spill.traffic > 3 * 4096);
            let mut output = Vec::new();
            emit_spill(&run, &mut output, &Request::default()).unwrap();
            let output = String::from_utf8(output).unwrap();
            assert!(output.contains("LF:12\nLH:6\nend_of_record"));
            assert_eq!(output.matches("\nDA:").count(), 12);
        }
    }

    #[test]
    fn spill_failure_cleans_tempfiles_and_preserves_existing_destination() {
        let root = tempfile::tempdir().unwrap();
        let temp = root.path().join("spill");
        let _ = std::fs::create_dir(&temp);
        let input = root.path().join("conflicting-ver.info");
        std::fs::write(
            &input,
            "TN:a\nSF:x.c\nVER:1\nDA:1,1\nend_of_record\nTN:b\nSF:x.c\nVER:2\nDA:2,1\nend_of_record\n",
        )
        .unwrap();
        let output = root.path().join("existing.info");
        std::fs::write(&output, "keep this destination\n").unwrap();
        let mut req = Request::default();
        req.resources.inputs = vec![input];
        req.resources.output = output.clone();
        req.resources.tmpdir = temp.clone();
        assert!(run(req).is_err());
        assert_eq!(
            std::fs::read_to_string(output).unwrap(),
            "keep this destination\n"
        );
        assert_eq!(std::fs::read_dir(temp).unwrap().count(), 0);
    }

    #[test]
    fn spill_matches_lcov_fractional_and_ignored_negative_da_counts() {
        let root = tempfile::tempdir().unwrap();
        let input = root.path().join("counts.info");
        let output = root.path().join("counts.out");
        std::fs::write(
            &input,
            "TN:fraction\nSF:x.c\nDA:1,1.9\nend_of_record\nTN:negative\nSF:x.c\nDA:1,-1\nend_of_record\n",
        )
        .unwrap();
        let mut req = Request::default();
        req.resources.inputs = vec![input];
        req.resources.output = output.clone();
        req.ignore_errors.insert("negative".into());
        run(req).unwrap();
        let output = std::fs::read_to_string(output).unwrap();
        assert!(output.contains("TN:fraction\nSF:x.c\nDA:1,1\nLF:1\nLH:1"));
        assert!(output.contains("TN:negative\nSF:x.c\nDA:1,0\nLF:1\nLH:0"));
    }

    #[test]
    fn checksum_generation_matches_lcov_md5_base64_and_gzip_round_trips() {
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("source.c");
        std::fs::write(&source, "if (a && b) return 1;\n").unwrap();
        let input = root.path().join("trace.info");
        let text = format!(
            "TN:checksum\nSF:{}\nDA:1,1\nend_of_record\n",
            source.display()
        );
        std::fs::write(&input, &text).unwrap();
        let mut req = Request::default();
        req.resources.inputs = vec![input.clone()];
        req.resources.output = root.path().join("out.info");
        req.checksum = true;
        req.ignore_errors.insert("version".into());
        run(req).unwrap();
        let out = std::fs::read_to_string(root.path().join("out.info")).unwrap();
        assert!(out.contains("DA:1,1,nNCtFK7xrOM2IVOXfEgG7g\n"));
        let gz_input = root.path().join("trace.info.gz");
        let file = File::create(&gz_input).unwrap();
        let mut enc = flate2::write::GzEncoder::new(file, Compression::default());
        enc.write_all(text.as_bytes()).unwrap();
        enc.finish().unwrap();
        let mut req = Request::default();
        req.resources.inputs = vec![gz_input];
        req.resources.output = root.path().join("out.info.gz");
        run(req).unwrap();
        let compressed = File::open(root.path().join("out.info.gz")).unwrap();
        let mut decoded = String::new();
        MultiGzDecoder::new(compressed)
            .read_to_string(&mut decoded)
            .unwrap();
        assert!(decoded.contains("DA:1,1\n"));
        let mut corrupted = std::fs::read(root.path().join("trace.info.gz")).unwrap();
        let last = corrupted.len() - 1;
        corrupted[last] ^= 0x80;
        let bad = root.path().join("corrupt.info.gz");
        std::fs::write(&bad, corrupted).unwrap();
        let mut req = Request::default();
        req.resources.inputs = vec![bad];
        req.resources.output = root.path().join("bad-output.info");
        assert!(run(req).is_err());
        assert!(!root.path().join("bad-output.info").exists());
    }
}
