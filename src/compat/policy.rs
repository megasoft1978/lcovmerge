use super::{record, Section};
use crate::common::{Error, MAX_LINE};
use crate::compat::Request;
use base64::Engine;
use std::fs::File;
use std::io::{BufRead, BufReader, Read};
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
        (Some(end), Some(name)) => Ok((
            num(Some(first), file, line)?,
            Some(num(Some(end), file, line)?),
            name.to_owned(),
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
        Some(num(Some(count), file, line)?)
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
    let count = num(f.next(), file, line)?;
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
        old.hits = old
            .hits
            .checked_add(item.hits)
            .ok_or_else(|| Error::at(2, file, line, "MC/DC count overflow"))?;
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
) -> Result<(), Error> {
    let line_no = num(payload.split(',').next(), file, line)?;
    let existing = s.lines.get(&line_no).and_then(|x| x.1.clone());
    let src = Path::new(&s.source);
    let source = match File::open(src) {
        Ok(source) => source,
        Err(e) => {
            let error = Error::at(
                2,
                file,
                line,
                format!("cannot read source {}: {e}", src.display()),
            );
            if can_recover(req, &error) {
                return Ok(());
            }
            return Err(error);
        }
    };
    let mut reader = BufReader::new(source);
    let mut buf = Vec::new();
    let mut found = true;
    for n in 1..=line_no {
        buf.clear();
        let read = (&mut reader)
            .take((MAX_LINE + 2) as u64)
            .read_until(b'\n', &mut buf)
            .map_err(|e| Error::at(3, file, line, format!("source read failed: {e}")))?;
        if read == 0 {
            found = false;
            break;
        }
        if buf.len() > MAX_LINE + 1 {
            return Err(Error::at(
                2,
                file,
                line,
                "source line exceeds 1 MiB while computing checksum",
            ));
        }
        if n == line_no {
            break;
        }
    }
    if buf.last() == Some(&b'\n') {
        buf.pop();
    }
    if buf.last() == Some(&b'\r') {
        buf.pop();
    }
    if buf.len() > MAX_LINE {
        return Err(Error::at(
            2,
            file,
            line,
            "source line exceeds 1 MiB while computing checksum",
        ));
    }
    let sum = found
        .then(|| base64::engine::general_purpose::STANDARD_NO_PAD.encode(md5::compute(&buf).0));
    let mismatch = existing
        .as_deref()
        .is_some_and(|c| Some(c) != sum.as_deref());
    let missing = existing.is_none();
    if req.checksum || req.resources.strict_checksum {
        if let Some(v) = s.lines.get_mut(&line_no) {
            if v.1.is_none() {
                v.1 = sum.clone();
            }
        }
    }
    if (req.resources.strict_checksum || req.checksum) && (mismatch || missing) {
        let message = if mismatch {
            format!("source checksum mismatch for {}:{}", s.source, line_no)
        } else {
            format!("no checksum for {}:{}", s.source, line_no)
        };
        let e = Error::at(2, file, line, message);
        if !can_recover(req, &e) {
            return Err(e);
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
    } else if m.contains("overflow") {
        "count"
    } else if m.contains("checksum") || m.contains("source version") {
        "version"
    } else if m.contains("read source") || m.contains("source read") {
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
    Ok((num(Some(n), file, line)?, name.to_owned()))
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
        let mut lines = BTreeSet::new();
        let mut groups: BTreeMap<u64, Option<u64>> = BTreeMap::new();
        for s in sections.iter().filter(|s| s.source == source) {
            lines.extend(s.lines.keys().copied());
            for (name, f) in &s.functions {
                if name.contains("{lambda(") || (extension == "java" && name.contains(".lambda$")) {
                    continue;
                }
                let end = groups.entry(f.line).or_insert(f.end_line);
                if end.is_none() {
                    *end = f.end_line;
                }
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
        for s in sections.iter_mut().filter(|s| s.source == source) {
            for f in s.functions.values_mut() {
                if f.end_line.is_none() {
                    f.end_line = groups.get(&f.line).copied().flatten();
                }
            }
        }
    }
    Ok(())
}
