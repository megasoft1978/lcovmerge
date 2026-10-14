use self::merge::{merge_into, write_sections};
use self::record::Section;
use super::Request;
use crate::common::{check_cancelled, stdin_reader, stdout_writer, Error, MAX_LINE};
use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Read};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

#[allow(dead_code)] // Kept private until spill execution is introduced.
#[path = "codec.rs"]
mod codec;
#[path = "merge.rs"]
mod merge;
#[path = "record.rs"]
mod record;

static TEMP_ID: AtomicU64 = AtomicU64::new(0);

pub fn run(request: Request) -> Result<(), Error> {
    let opt = &request.resources;
    if opt.inputs.is_empty() {
        return Err(Error::new(1, "LCOV profile requires at least one -a input"));
    }
    if !opt.includes.is_empty()
        || !opt.excludes.is_empty()
        || !opt.rebases.is_empty()
        || opt.prefix_strip.is_some()
    {
        return Err(Error::new(
            1,
            "LCOV-compatible include/exclude and path rewrite controls are not implemented",
        ));
    }
    if opt.strict_checksum {
        return Err(Error::new(
            1,
            "LCOV source checksum verification is not implemented",
        ));
    }
    if request.checksum {
        return Err(Error::new(
            1,
            "LCOV checksum generation and conflict validation are not implemented",
        ));
    }
    if !request.ignore_errors.is_empty() || request.keep_going {
        return Err(Error::new(
            1,
            "LCOV error recovery and keep-going are not implemented",
        ));
    }
    if request.mcdc_coverage {
        return Err(Error::new(1, "LCOV MC/DC aggregation is not implemented"));
    }
    if is_gzip_path(&opt.output) {
        return Err(Error::new(
            1,
            "gzip output is unsupported: no approved streaming codec dependency",
        ));
    }
    if opt.inputs.iter().any(|p| is_gzip_path(p)) {
        return Err(Error::new(
            1,
            "gzip input is unsupported: no approved streaming codec dependency",
        ));
    }
    if opt.inputs.iter().filter(|p| p.as_os_str() == "-").count() > 1 {
        return Err(Error::new(1, "stdin may appear only once in LCOV inputs"));
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
            Box::new(
                File::open(path).map_err(|e| Error::new(3, format!("cannot open {label}: {e}")))?,
            )
        };
        parse_input(
            reader,
            &label,
            &request,
            &mut sections,
            &mut indexes,
            &mut held,
            opt.mem_limit,
        )?;
    }
    if opt.output.as_os_str() == "-" || opt.output.as_os_str().is_empty() {
        let stdout =
            stdout_writer().map_err(|e| Error::new(3, format!("cannot write stdout: {e}")))?;
        write_sections(
            BufWriter::new(stdout),
            &sections,
            request.checksum,
            opt.function_data,
        )
    } else {
        let (mut file, temp) = create_stage(&opt.output)?;
        let result = write_sections(
            BufWriter::new(&mut file),
            &sections,
            request.checksum,
            opt.function_data,
        )
        .and_then(|_| {
            file.sync_all()
                .map_err(|e| Error::new(3, format!("cannot sync staged output: {e}")))
        });
        if let Err(e) = result {
            let _ = fs::remove_file(&temp);
            return Err(e);
        }
        drop(file);
        if let Err(e) = fs::rename(&temp, &opt.output) {
            let _ = fs::remove_file(&temp);
            return Err(Error::new(
                3,
                format!("cannot replace {}: {e}", opt.output.display()),
            ));
        }
        Ok(())
    }
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
    let mut input = BufReader::with_capacity(crate::common::IO_BLOCK, reader);
    let mut line = Vec::with_capacity(4096);
    let mut line_no = 0u64;
    let mut testcase = String::new();
    let mut current: Option<Section> = None;
    let mut modern_decls: HashMap<u64, (u64, Option<u64>)> = HashMap::new();
    let mut legacy_decls: HashMap<String, (u64, Option<u64>)> = HashMap::new();
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
        if line.len() > MAX_LINE + 1 {
            return Err(Error::at(2, label, line_no, "line exceeds 1 MiB"));
        }
        if line.last() == Some(&b'\n') {
            line.pop();
        }
        if line.last() == Some(&b'\r') {
            line.pop();
        }
        if line.contains(&0) {
            return Err(Error::at(2, label, line_no, "NUL byte in tracefile"));
        }
        let s = std::str::from_utf8(&line)
            .map_err(|_| Error::at(2, label, line_no, "invalid UTF-8 tracefile record"))?;
        if s.is_empty() || s.starts_with('#') {
            continue;
        }
        if let Some(tn) = s.strip_prefix("TN:") {
            if current.is_some() {
                return Err(Error::at(2, label, line_no, "TN inside an open section"));
            }
            testcase = tn.to_owned();
            continue;
        }
        if let Some(sf) = s.strip_prefix("SF:") {
            if let Some(section) = current.take() {
                finish_section(section, sections, indexes, held, limit, label, line_no)?;
            }
            if sf.is_empty() {
                return Err(Error::at(2, label, line_no, "empty SF path"));
            }
            current = Some(Section {
                testcase: testcase.clone(),
                source: sf.to_owned(),
                ..Section::default()
            });
            testcase.clear();
            modern_decls.clear();
            legacy_decls.clear();
            continue;
        }
        let Some(section) = current.as_mut() else {
            return Err(Error::at(2, label, line_no, "record outside an SF section"));
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
            )?;
            modern_decls.clear();
            legacy_decls.clear();
            continue;
        }
        if let Some(x) = s.strip_prefix("DA:") {
            section.add_line(x, label, line_no)?;
            ensure_budget(section, *held, limit, label, line_no)?;
            continue;
        }
        if let Some(x) = s.strip_prefix("VER:") {
            if section.version.as_deref().is_some_and(|v| v != x) {
                return Err(Error::at(2, label, line_no, "conflicting source versions"));
            }
            section.version = Some(x.to_owned());
            continue;
        }
        if let Some(x) = s.strip_prefix("FN:") {
            let (start, end, name) = parse_fn(x, label, line_no)?;
            legacy_decls.insert(name.clone(), (start, end));
            section.add_function(name, start, end, 0, label, line_no)?;
            ensure_budget(section, *held, limit, label, line_no)?;
            continue;
        }
        if let Some(x) = s.strip_prefix("FNDA:") {
            let (hits, name) = parse_named_count(x, label, line_no)?;
            let (start, end) = legacy_decls.get(&name).copied().ok_or_else(|| {
                Error::at(2, label, line_no, "FNDA references undeclared function")
            })?;
            section.add_function(name, start, end, hits, label, line_no)?;
            ensure_budget(section, *held, limit, label, line_no)?;
            continue;
        }
        if let Some(x) = s.strip_prefix("FNL:") {
            let mut f = x.split(',');
            let idx = num(f.next(), label, line_no)?;
            let start = num(f.next(), label, line_no)?;
            let end = match f.next() {
                Some(v) => Some(num(Some(v), label, line_no)?),
                None => None,
            };
            if f.next().is_some() || modern_decls.insert(idx, (start, end)).is_some() {
                return Err(Error::at(
                    2,
                    label,
                    line_no,
                    "invalid or duplicate FNL index",
                ));
            }
            section.modern = true;
            continue;
        }
        if let Some(x) = s.strip_prefix("FNA:") {
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
            section.add_function(name.to_owned(), start, end, hits, label, line_no)?;
            section.modern = true;
            ensure_budget(section, *held, limit, label, line_no)?;
            continue;
        }
        if s.starts_with("BRDA:") {
            if req.resources.branch_coverage {
                return Err(Error::at(
                    1,
                    label,
                    line_no,
                    "LCOV branch signature aggregation is not implemented",
                ));
            }
            continue;
        }
        if s.starts_with("MCDC:") {
            if req.mcdc_coverage {
                return Err(Error::at(
                    1,
                    label,
                    line_no,
                    "LCOV MC/DC aggregation is not implemented",
                ));
            }
            continue;
        }
        if [
            "LF:", "LH:", "FNF:", "FNH:", "BRF:", "BRH:", "MD:", "DA:", "TN:", "SF:", "MCF:",
            "MCH:",
        ]
        .iter()
        .any(|p| s.starts_with(p))
        {
            continue;
        }
        return Err(Error::at(
            1,
            label,
            line_no,
            format!(
                "unsupported LCOV record '{}': refusing to discard semantics",
                s.split(':').next().unwrap_or(s)
            ),
        ));
    }
    if let Some(section) = current {
        finish_section(section, sections, indexes, held, limit, label, line_no)?;
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

fn finish_section(
    s: Section,
    sections: &mut Vec<Section>,
    indexes: &mut HashMap<(String, String), usize>,
    held: &mut usize,
    limit: usize,
    label: &str,
    line: u64,
) -> Result<(), Error> {
    if !s.saw_coverpoint {
        return Ok(());
    }
    let key = (s.testcase.clone(), s.source.clone());
    if let Some(&idx) = indexes.get(&key) {
        let before = sections[idx].memory_size();
        merge_into(&mut sections[idx], s, label)?;
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

fn parse_fn(s: &str, file: &str, line: u64) -> Result<(u64, Option<u64>, String), Error> {
    let mut f = s.splitn(3, ',');
    let first = f.next().unwrap_or("");
    let second = f.next();
    let third = f.next();
    match (second, third) {
        (Some(name), None) => Ok((num(Some(first), file, line)?, None, name.to_owned())),
        (Some(end), Some(name)) => Ok((
            num(Some(first), file, line)?,
            Some(num(Some(end), file, line)?),
            name.to_owned(),
        )),
        _ => Err(Error::at(2, file, line, "malformed FN record")),
    }
}
fn parse_named_count(s: &str, file: &str, line: u64) -> Result<(u64, String), Error> {
    let (n, name) = s
        .split_once(',')
        .ok_or_else(|| Error::at(2, file, line, "malformed FNDA record"))?;
    if name.is_empty() {
        return Err(Error::at(2, file, line, "empty function name"));
    }
    Ok((num(Some(n), file, line)?, name.to_owned()))
}
fn num(s: Option<&str>, file: &str, line: u64) -> Result<u64, Error> {
    let s = s.unwrap_or("");
    if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
        return Err(Error::at(2, file, line, "invalid numeric LCOV field"));
    }
    s.parse()
        .map_err(|_| Error::at(2, file, line, "numeric LCOV field overflow"))
}
fn is_gzip_path(p: &Path) -> bool {
    p.extension().is_some_and(|e| e.eq_ignore_ascii_case("gz"))
}
fn create_stage(dst: &Path) -> Result<(File, PathBuf), Error> {
    let parent = dst
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    for _ in 0..100 {
        let id = TEMP_ID.fetch_add(1, Ordering::Relaxed);
        let name = format!(".lcovmerge-stage-{}-{id}", std::process::id());
        let path = parent.join(name);
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(f) => return Ok((f, path)),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => {
                return Err(Error::new(
                    3,
                    format!("cannot stage output near {}: {e}", dst.display()),
                ))
            }
        }
    }
    Err(Error::new(3, "cannot allocate unique staged output"))
}

#[cfg(test)]
mod tests {
    use super::*;
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
        write_sections(&mut out, &sections, false, true).unwrap();
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
}
