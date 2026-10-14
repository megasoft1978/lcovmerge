use super::record::Section;
use crate::common::check_cancelled;
use crate::common::Error;
use std::collections::HashMap;
use std::io::Write;

pub type Sections = Vec<Section>;

pub fn merge_into(dst: &mut Section, src: Section, file: &str) -> Result<(), Error> {
    for (line, (count, checksum)) in src.lines {
        let e = dst.lines.entry(line).or_insert((0, None));
        e.0 = e
            .0
            .checked_add(count)
            .ok_or_else(|| Error::new(2, format!("{file}: DA count overflow at line {line}")))?;
        if let Some(checksum) = checksum {
            e.1 = Some(checksum);
        }
    }
    for (name, f) in src.functions {
        let e = dst
            .functions
            .entry(name.clone())
            .or_insert_with(|| f.clone());
        if e.line != f.line || e.end_line != f.end_line {
            return Err(Error::new(
                2,
                format!("{file}: inconsistent function declaration for '{name}'"),
            ));
        }
        e.hits = e.hits.checked_add(f.hits).ok_or_else(|| {
            Error::new(2, format!("{file}: function count overflow for '{name}'"))
        })?;
    }
    if let (Some(a), Some(b)) = (&dst.version, &src.version) {
        if a != b {
            return Err(Error::new(
                2,
                format!("{file}: conflicting source versions"),
            ));
        }
    } else if dst.version.is_none() {
        dst.version = src.version;
    }
    dst.saw_coverpoint |= src.saw_coverpoint;
    dst.modern |= src.modern;
    Ok(())
}

pub fn write_sections(
    mut out: impl Write,
    sections: &[Section],
    checksums: bool,
    functions: bool,
) -> Result<(), Error> {
    for s in sections {
        check_cancelled()?;
        if !s.saw_coverpoint {
            continue;
        }
        writeln!(out, "TN:{}", s.testcase)
            .and_then(|_| writeln!(out, "SF:{}", s.source))
            .map_err(io_error)?;
        if let Some(v) = &s.version {
            writeln!(out, "VER:{v}").map_err(io_error)?;
        }
        if functions {
            for (idx, (_, f)) in s.functions.iter().enumerate() {
                check_cancelled()?;
                writeln!(
                    out,
                    "FNL:{idx},{},{}",
                    f.line,
                    f.end_line.map_or(String::new(), |n| format!(",{n}"))
                )
                .map_err(io_error)?;
            }
            for (idx, (name, f)) in s.functions.iter().enumerate() {
                check_cancelled()?;
                writeln!(out, "FNA:{idx},{},{}", f.hits, name).map_err(io_error)?;
            }
            if !s.functions.is_empty() {
                let found = s.functions.len();
                let hit = s.functions.values().filter(|f| f.hits > 0).count();
                writeln!(out, "FNF:{found}\nFNH:{hit}").map_err(io_error)?;
            }
        }
        let found = s.lines.len();
        let hit = s.lines.values().filter(|(n, _)| *n > 0).count();
        for (line, (count, checksum)) in &s.lines {
            check_cancelled()?;
            if checksums {
                if let Some(c) = checksum {
                    writeln!(out, "DA:{line},{count},{c}").map_err(io_error)?;
                } else {
                    writeln!(out, "DA:{line},{count}").map_err(io_error)?;
                }
            } else {
                writeln!(out, "DA:{line},{count}").map_err(io_error)?;
            }
        }
        writeln!(out, "LF:{found}\nLH:{hit}\nend_of_record").map_err(io_error)?;
    }
    out.flush().map_err(io_error)
}

#[allow(dead_code)]
pub fn index(sections: Sections) -> (Vec<(String, String)>, HashMap<(String, String), Section>) {
    let mut order = Vec::new();
    let mut map = HashMap::new();
    for section in sections {
        let key = (section.testcase.clone(), section.source.clone());
        if !map.contains_key(&key) {
            order.push(key.clone());
            map.insert(key, section);
        }
    }
    (order, map)
}

fn io_error(e: std::io::Error) -> Error {
    Error::new(3, format!("output write failed: {e}"))
}
