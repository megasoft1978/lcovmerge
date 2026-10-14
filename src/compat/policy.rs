use super::{record, Section};
use crate::common::Error;
use crate::compat::Request;
use base64::Engine;
use std::path::Path;
pub(super) fn parse_fn(
    s: &str,
    file: &str,
    line: u64,
) -> Result<(u64, Option<u64>, String), Error> {
    let mut f = s.splitn(3, ',');
    let first = f.next().unwrap_or("");
    let second = f.next();
    let third = f.next();
    match (second, third) {
        (Some(name), None) => Ok((num(Some(first), file, line)?, None, name.to_owned())),
        (Some(end), Some(name)) if !end.is_empty() && end.bytes().all(|b| b.is_ascii_digit()) => {
            Ok((
                num(Some(first), file, line)?,
                Some(num(Some(end), file, line)?),
                name.to_owned(),
            ))
        }
        (Some(first_name), Some(rest)) => Ok((
            num(Some(first), file, line)?,
            None,
            format!("{first_name},{rest}"),
        )),
        _ => Err(Error::at(2, file, line, "malformed FN record")),
    }
}

pub(super) fn parse_branch(
    s: &mut Section,
    payload: &str,
    file: &str,
    line: u64,
) -> Result<(), Error> {
    let mut f = payload.splitn(4, ',');
    let ln = num(f.next(), file, line)?;
    let block_field = f
        .next()
        .ok_or_else(|| Error::at(2, file, line, "malformed BRDA record"))?;
    let expr_first = f
        .next()
        .ok_or_else(|| Error::at(2, file, line, "malformed BRDA record"))?;
    let tail = f
        .next()
        .ok_or_else(|| Error::at(2, file, line, "malformed BRDA record"))?;
    if ln == 0 {
        return Err(Error::at(2, file, line, "branch line must be positive"));
    }
    let kind = block_field
        .chars()
        .next()
        .filter(|c| *c == 'e' || *c == 'f')
        .unwrap_or('v');
    let rest = if kind == 'v' {
        block_field
    } else {
        &block_field[1..]
    };
    let unreachable = rest.starts_with('U');
    let rest = rest.strip_prefix('U').unwrap_or(rest);
    let block = num(Some(rest), file, line)?;
    let combined = format!("{expr_first},{tail}");
    let (expr, count) = combined
        .rsplit_once(',')
        .ok_or_else(|| Error::at(2, file, line, "malformed BRDA count"))?;
    let expr = expr.to_owned();
    if expr.is_empty() {
        return Err(Error::at(2, file, line, "empty BRDA expression"));
    }
    let hits = if count == "-" {
        None
    } else {
        Some(parse_count(count, file, line)?)
    };
    let blocks = s.branches.entry(ln).or_default();
    let bi = usize::try_from(block)
        .map_err(|_| Error::at(2, file, line, "branch block index overflow"))?;
    if bi > 65535 {
        return Err(Error::at(
            2,
            file,
            line,
            "branch block index exceeds supported range",
        ));
    }
    while blocks.len() <= bi {
        blocks.push(Vec::new());
    }
    blocks[bi].push(record::Branch {
        expr,
        hits,
        kind,
        unreachable,
    });
    s.saw_coverpoint = true;
    Ok(())
}

pub(super) fn parse_mcdc(
    s: &mut Section,
    payload: &str,
    file: &str,
    line: u64,
) -> Result<(), Error> {
    let mut f = payload.splitn(6, ',');
    let ln = num(f.next(), file, line)?;
    let group = f
        .next()
        .ok_or_else(|| Error::at(2, file, line, "malformed MCDC record"))?;
    let sense = f.next().unwrap_or("");
    let count = parse_count(f.next().unwrap_or(""), file, line)?;
    let index = num(f.next(), file, line)?;
    let expr = f
        .next()
        .filter(|x| !x.is_empty())
        .ok_or_else(|| Error::at(2, file, line, "malformed MCDC expression"))?;
    if ln == 0 {
        return Err(Error::at(2, file, line, "MC/DC line must be positive"));
    }
    let unreachable = group.starts_with('U');
    let g = group.strip_prefix('U').unwrap_or(group);
    let group = num(Some(g), file, line)?;
    if group == 0 {
        return Err(Error::at(
            2,
            file,
            line,
            "MC/DC group size must be positive",
        ));
    }
    let sense = sense
        .chars()
        .next()
        .filter(|c| *c == 't' || *c == 'f')
        .ok_or_else(|| Error::at(2, file, line, "invalid MC/DC sense"))?;
    let key = (group, index, sense);
    let item = record::Mcdc {
        hits: count,
        expr: expr.to_owned(),
        unreachable,
    };
    if let Some(old) = s.mcdc.entry(ln).or_default().get_mut(&key) {
        if old.expr != item.expr {
            return Err(Error::at(2, file, line, "inconsistent MC/DC expression"));
        }
        old.hits = old.hits.saturating_add(item.hits).min(i64::MAX as u64);
        old.unreachable |= item.unreachable;
    } else {
        s.mcdc.entry(ln).or_default().insert(key, item);
    }
    s.saw_coverpoint = true;
    Ok(())
}

pub(super) fn validate_or_generate_checksum(
    s: &mut Section,
    payload: &str,
    req: &Request,
    file: &str,
    line: u64,
    source_index: &mut super::source::SourceIndex,
) -> Result<(), Error> {
    let line_no = num(payload.split(',').next(), file, line)?;
    let existing = s.lines.get(&line_no).and_then(|x| x.1.clone());
    let raw = match source_index.checksum(Path::new(&s.source), line_no) {
        Ok(raw) => raw,
        Err(e) if can_recover(req, &e) => return Ok(()),
        Err(e) => return Err(e),
    };
    let sum = raw.map(|bytes| base64::engine::general_purpose::STANDARD_NO_PAD.encode(bytes));
    let extension = Path::new(&s.source)
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("");
    let verifies = matches!(
        extension,
        "c" | "h" | "i" | "C" | "H" | "I" | "icc" | "cpp" | "cc" | "cxx" | "hh" | "hpp" | "hxx"
    );
    if verifies
        && (req.checksum || req.resources.strict_checksum)
        && (existing.is_none() || existing != sum)
    {
        let e = Error::at(
            2,
            file,
            line,
            format!(
                "source checksum mismatch or missing checksum for {}:{line_no}",
                s.source
            ),
        );
        if !can_recover(req, &e) {
            return Err(e);
        }
    }
    if existing.is_none() {
        if let Some(value) = s.lines.get_mut(&line_no) {
            value.1 = sum;
        }
    }
    Ok(())
}
pub(super) fn can_recover(req: &Request, error: &Error) -> bool {
    if error.code == 3 {
        return false;
    }
    if req.keep_going {
        return true;
    }
    let m = error.message.to_ascii_lowercase();
    let category = if m.contains("path") {
        "path"
    } else if m.contains("negative") {
        "negative"
    } else if m.contains("non-integer") {
        "format"
    } else if m.contains("overflow") {
        "count"
    } else if m.contains("checksum") || m.contains("source version") {
        "version"
    } else if m.contains("read source") || m.contains("source read") || m.contains("source changed")
    {
        "source"
    } else if m.contains("inconsistent") || m.contains("conflicting") {
        "inconsistent"
    } else if m.contains("branch") {
        "branch"
    } else {
        "format"
    };
    req.ignore_errors
        .iter()
        .any(|x| x.eq_ignore_ascii_case(category))
}
pub(super) fn parse_named_count(s: &str, file: &str, line: u64) -> Result<(u64, String), Error> {
    let (n, name) = s
        .split_once(',')
        .ok_or_else(|| Error::at(2, file, line, "malformed FNDA record"))?;
    if name.is_empty() {
        return Err(Error::at(2, file, line, "empty function name"));
    }
    Ok((parse_count(n, file, line)?, name.to_owned()))
}
pub(super) fn parse_count(s: &str, file: &str, line: u64) -> Result<u64, Error> {
    record::parse_count(s, file, line)
}
pub(super) fn function_count(req: &Request, s: &str, file: &str, line: u64) -> Result<u64, Error> {
    match parse_count(s, file, line) {
        Err(e) if e.message.contains("negative coverage count") && can_recover(req, &e) => Ok(0),
        result => result,
    }
}
pub(super) fn num(s: Option<&str>, file: &str, line: u64) -> Result<u64, Error> {
    let s = s.unwrap_or("");
    if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
        return Err(Error::at(2, file, line, "invalid numeric LCOV field"));
    }
    s.parse()
        .map_err(|_| Error::at(2, file, line, "numeric LCOV field overflow"))
}

/// LCOV derives missing end lines from the source-wide line-coverage map.
pub(super) fn derive_function_ends(sections: &mut [Section], req: &Request) -> Result<(), Error> {
    use std::collections::{BTreeMap, BTreeSet};
    if !req.derive_function_end_line || !req.resources.function_data {
        return Ok(());
    }
    let sources: BTreeSet<_> = sections.iter().map(|s| s.source.clone()).collect();
    for source in sources {
        let extension = std::path::Path::new(&source)
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("");
        if !matches!(
            extension,
            "c" | "C"
                | "i"
                | "I"
                | "icc"
                | "cc"
                | "cpp"
                | "cxx"
                | "h"
                | "H"
                | "hh"
                | "hpp"
                | "hxx"
                | "java"
                | "pl"
                | "pm"
        ) {
            continue;
        }
        for s in sections.iter_mut().filter(|s| s.source == source) {
            let lines: BTreeSet<_> = s.lines.keys().copied().collect();
            let mut groups: BTreeMap<u64, Option<u64>> = BTreeMap::new();
            for (name, f) in &s.functions {
                if name.contains("{lambda(") || (extension == "java" && name.contains(".lambda$")) {
                    continue;
                }
                let end = groups.entry(f.line).or_insert(f.end_line);
                if end.is_none() {
                    *end = f.end_line;
                }
            }
            let starts: Vec<_> = groups.keys().copied().collect();
            for (i, start) in starts.iter().copied().enumerate() {
                if groups[&start].is_some() {
                    continue;
                }
                let next = starts.get(i + 1).copied().unwrap_or(u64::MAX);
                let end = lines.range(start..next).next_back().copied();
                if let Some(end) = end {
                    groups.insert(start, Some(end));
                } else {
                    let error = Error::new(2, format!("inconsistent line coverage for function at {source}:{start}; cannot derive end line"));
                    if !can_recover(req, &error) {
                        return Err(error);
                    }
                }
            }
            for (name, f) in &mut s.functions {
                if name.contains("{lambda(") || (extension == "java" && name.contains(".lambda$")) {
                    continue;
                }
                if f.end_line.is_none() {
                    f.end_line = groups.get(&f.line).copied().flatten();
                }
            }
        }
    }
    Ok(())
}
