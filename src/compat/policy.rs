use super::{record, Section};
use crate::common::Error;
use crate::compat::Request;
use base64::Engine;
use std::path::Path;

/// LCOV reads TN as bytes: normalize the name before the first comma, retaining
/// only the special `,diff` suffix. UTF-8 non-ASCII bytes each become `_`.
pub(super) fn testcase_name(payload: &str) -> String {
    let payload = payload.trim_end_matches(|c| matches!(c, ' ' | '\t'..='\r'));
    let (name, suffix) = payload.split_once(',').unwrap_or((payload, ""));
    let mut normalized: String = name
        .bytes()
        .map(|b| {
            if b.is_ascii_alphanumeric() || b == b'_' {
                char::from(b)
            } else {
                '_'
            }
        })
        .collect();
    if suffix.starts_with("diff") {
        normalized.push_str(",diff");
    }
    normalized
}

#[cfg(test)]
mod testcase_tests {
    #[test]
    fn matches_byte_normalization_and_diff_suffix() {
        for (input, expected) in [
            ("a-b.c!", "a_b_c_"),
            (" a b\t", "_a_b"),
            ("a ,ignored ", "a_"),
            ("café", "caf__"),
            ("alpha,ignored", "alpha"),
            ("a-b,different,rest", "a_b,diff"),
            (",diff", ",diff"),
        ] {
            assert_eq!(super::testcase_name(input), expected);
        }
    }
}
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
        _ => Err(super::policy::at(
            super::policy::Kind::Format,
            file,
            line,
            "malformed FN record",
        )),
    }
}

pub(super) fn parse_branch(
    s: &mut Section,
    payload: &str,
    file: &str,
    line: u64,
    req: &Request,
) -> Result<(), Error> {
    let mut f = payload.splitn(4, ',');
    let ln = num(f.next(), file, line)?;
    let block_field = f.next().ok_or_else(|| {
        super::policy::at(
            super::policy::Kind::Format,
            file,
            line,
            "malformed BRDA record",
        )
    })?;
    let expr_first = f.next().ok_or_else(|| {
        super::policy::at(
            super::policy::Kind::Format,
            file,
            line,
            "malformed BRDA record",
        )
    })?;
    let tail = f.next().ok_or_else(|| {
        super::policy::at(
            super::policy::Kind::Format,
            file,
            line,
            "malformed BRDA record",
        )
    })?;
    if ln == 0 {
        return Err(super::policy::at(
            super::policy::Kind::Branch,
            file,
            line,
            "branch line must be positive",
        ));
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
    let (expr, count) = combined.rsplit_once(',').ok_or_else(|| {
        super::policy::at(
            super::policy::Kind::Format,
            file,
            line,
            "malformed BRDA count",
        )
    })?;
    let expr = expr.to_owned();
    if expr.is_empty() {
        return Err(super::policy::at(
            super::policy::Kind::Format,
            file,
            line,
            "empty BRDA expression",
        ));
    }
    let hits = if count == "-" {
        None
    } else {
        Some(match parse_count(count, file, line) {
            Ok(hits) => hits,
            Err(e) if e.message.ends_with("negative coverage count") && can_recover(req, &e) => 0,
            Err(e) => return Err(e),
        })
    };
    let blocks = s.branches.entry(ln).or_default();
    let bi = usize::try_from(block).map_err(|_| {
        super::policy::at(
            super::policy::Kind::Count,
            file,
            line,
            "branch block index overflow",
        )
    })?;
    if bi > 65535 {
        return Err(super::policy::at(
            super::policy::Kind::Branch,
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
    let group = f.next().ok_or_else(|| {
        super::policy::at(
            super::policy::Kind::Format,
            file,
            line,
            "malformed MCDC record",
        )
    })?;
    let sense = f.next().unwrap_or("");
    let count = parse_count(f.next().unwrap_or(""), file, line)?;
    let index = num(f.next(), file, line)?;
    let expr = f.next().filter(|x| !x.is_empty()).ok_or_else(|| {
        super::policy::at(
            super::policy::Kind::Format,
            file,
            line,
            "malformed MCDC expression",
        )
    })?;
    if ln == 0 {
        return Err(super::policy::at(
            super::policy::Kind::Format,
            file,
            line,
            "MC/DC line must be positive",
        ));
    }
    let unreachable = group.starts_with('U');
    let g = group.strip_prefix('U').unwrap_or(group);
    let group = num(Some(g), file, line)?;
    if group == 0 {
        return Err(super::policy::at(
            super::policy::Kind::Format,
            file,
            line,
            "MC/DC group size must be positive",
        ));
    }
    let sense = sense
        .chars()
        .next()
        .filter(|c| *c == 't' || *c == 'f')
        .ok_or_else(|| {
            super::policy::at(
                super::policy::Kind::Format,
                file,
                line,
                "invalid MC/DC sense",
            )
        })?;
    let key = (group, index, sense);
    let item = record::Mcdc {
        hits: count,
        expr: expr.to_owned(),
        unreachable,
    };
    if let Some(old) = s.mcdc.entry(ln).or_default().get_mut(&key) {
        if old.expr != item.expr {
            return Err(super::policy::at(
                super::policy::Kind::Inconsistent,
                file,
                line,
                "inconsistent MC/DC expression",
            ));
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
        let e = super::policy::at(
            super::policy::Kind::Version,
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
/// Private error tags are normalized to the public data-error status at run's
/// boundary. Recovery never inspects paths, function names, or rendered text.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(i32)]
pub(super) enum Kind {
    Path = -101,
    Negative = -102,
    Format = -103,
    Count = -104,
    Version = -105,
    Source = -106,
    Inconsistent = -107,
    Branch = -108,
    Mismatch = -109,
    Empty = -110,
}
impl Kind {
    fn from_code(code: i32) -> Option<Self> {
        Some(match code {
            -101 => Self::Path,
            -102 => Self::Negative,
            -103 => Self::Format,
            -104 => Self::Count,
            -105 => Self::Version,
            -106 => Self::Source,
            -107 => Self::Inconsistent,
            -108 => Self::Branch,
            -109 => Self::Mismatch,
            -110 => Self::Empty,
            _ => return None,
        })
    }
    fn category(self) -> &'static str {
        match self {
            Self::Path => "path",
            Self::Negative => "negative",
            Self::Format => "format",
            Self::Count => "count",
            Self::Version => "version",
            Self::Source => "source",
            Self::Inconsistent => "inconsistent",
            Self::Branch => "branch",
            Self::Mismatch => "mismatch",
            Self::Empty => "empty",
        }
    }
}
pub(super) fn new(kind: Kind, message: impl Into<String>) -> Error {
    Error::new(kind as i32, message)
}
pub(super) fn at(
    kind: Kind,
    file: impl AsRef<Path>,
    line: u64,
    message: impl Into<String>,
) -> Error {
    Error::at(kind as i32, file, line, message)
}
pub(super) fn public_error(mut error: Error) -> Error {
    if Kind::from_code(error.code).is_some() {
        error.code = 2;
    }
    error
}
pub(super) fn path_error(error: Error, file: &str, line: u64) -> Error {
    if error.code == 2 {
        at(Kind::Path, file, line, error.message)
    } else {
        Error::at(error.code, file, line, error.message)
    }
}
pub(super) fn can_recover(req: &Request, error: &Error) -> bool {
    if error.code == 3 {
        return false;
    }
    if req.keep_going {
        return true;
    }
    let Some(kind) = Kind::from_code(error.code) else {
        return false;
    };
    req.ignore_errors
        .iter()
        .any(|x| x.eq_ignore_ascii_case(kind.category()))
}
pub(super) fn parse_named_count(s: &str, file: &str, line: u64) -> Result<(u64, String), Error> {
    let (n, name) = s.split_once(',').ok_or_else(|| {
        super::policy::at(
            super::policy::Kind::Format,
            file,
            line,
            "malformed FNDA record",
        )
    })?;
    if name.is_empty() {
        return Err(super::policy::at(
            super::policy::Kind::Format,
            file,
            line,
            "empty function name",
        ));
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
        return Err(super::policy::at(
            super::policy::Kind::Format,
            file,
            line,
            "invalid numeric LCOV field",
        ));
    }
    s.parse().map_err(|_| {
        super::policy::at(
            super::policy::Kind::Count,
            file,
            line,
            "numeric LCOV field overflow",
        )
    })
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
                    let error = super::policy::new(super::policy::Kind::Inconsistent, format!("inconsistent line coverage for function at {source}:{start}; cannot derive end line"));
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
