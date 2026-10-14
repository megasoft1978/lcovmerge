use crate::common::Error;
use std::collections::BTreeMap;

#[derive(Clone, Debug, Default)]
pub struct Function {
    pub line: u64,
    pub end_line: Option<u64>,
    pub hits: u64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Branch {
    pub expr: String,
    pub hits: Option<u64>,
    pub kind: char,
    pub unreachable: bool,
}

#[derive(Clone, Debug, Default)]
pub struct Mcdc {
    pub hits: u64,
    pub expr: String,
    pub unreachable: bool,
}

#[derive(Clone, Debug, Default)]
pub struct Section {
    pub testcase: String,
    pub source: String,
    pub lines: BTreeMap<u64, (u64, Option<String>)>,
    pub functions: BTreeMap<String, Function>,
    pub branches: BTreeMap<u64, Vec<Vec<Branch>>>,
    pub mcdc: BTreeMap<u64, BTreeMap<(u64, u64, char), Mcdc>>,
    pub modern: bool,
    pub version: Option<String>,
    pub saw_coverpoint: bool,
    pub selected: bool,
}

fn number(s: &str, file: &str, line: u64) -> Result<u64, Error> {
    if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
        return Err(Error::at(2, file, line, "invalid numeric LCOV field"));
    }
    s.parse()
        .map_err(|_| Error::at(2, file, line, "numeric LCOV field overflow"))
}

pub(super) fn parse_count(s: &str, file: &str, line: u64) -> Result<u64, Error> {
    const MAX_COUNT: u64 = i64::MAX as u64;
    if s.is_empty() {
        return Err(Error::at(2, file, line, "non-integer coverage count"));
    }
    let digits = s.strip_prefix('+').unwrap_or(s);
    if !digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit()) {
        return Ok(digits.bytes().fold(0u64, |value, digit| {
            value
                .saturating_mul(10)
                .saturating_add(u64::from(digit - b'0'))
                .min(MAX_COUNT)
        }));
    }
    let value = s
        .parse::<f64>()
        .map_err(|_| Error::at(2, file, line, "non-integer coverage count"))?;
    if !value.is_finite() {
        return Err(Error::at(2, file, line, "non-integer coverage count"));
    }
    if value < 0.0 {
        return Err(Error::at(2, file, line, "negative coverage count"));
    }
    if value >= MAX_COUNT as f64 {
        return Ok(MAX_COUNT);
    }
    Ok(value.trunc() as u64)
}

impl Section {
    pub fn add_line(&mut self, payload: &str, file: &str, line: u64) -> Result<(), Error> {
        let mut fields = payload.split(',');
        let n = number(fields.next().unwrap_or(""), file, line)?;
        if n == 0 {
            return Err(Error::at(2, file, line, "DA line must be positive"));
        }
        let count = parse_count(fields.next().unwrap_or(""), file, line)?;
        let checksum = fields.next().map(str::to_owned);
        if fields.next().is_some()
            || checksum
                .as_ref()
                .is_some_and(|s| s.is_empty() || s.chars().any(char::is_whitespace))
        {
            return Err(Error::at(2, file, line, "malformed DA record"));
        }
        let entry = self.lines.entry(n).or_insert((0, None));
        if let Some(c) = checksum.as_ref() {
            if entry.1.as_ref().is_some_and(|old| old != c) {
                return Err(Error::at(
                    2,
                    file,
                    line,
                    "conflicting checksums for source line",
                ));
            }
        }
        entry.0 = entry
            .0
            .checked_add(count)
            .ok_or_else(|| Error::at(2, file, line, "DA count overflow"))?;
        if let Some(c) = checksum {
            entry.1 = Some(c);
        }
        self.saw_coverpoint = true;
        Ok(())
    }
    pub fn add_function(
        &mut self,
        name: String,
        start: u64,
        end: Option<u64>,
        hits: u64,
        file: &str,
        line: u64,
    ) -> Result<(), Error> {
        if start == 0 || end == Some(0) {
            return Err(Error::at(2, file, line, "function line must be positive"));
        }
        let f = self.functions.entry(name).or_insert(Function {
            line: start,
            end_line: end,
            hits: 0,
        });
        if f.line != start || f.end_line != end {
            return Err(Error::at(
                2,
                file,
                line,
                "inconsistent function declaration",
            ));
        }
        f.hits = f.hits.saturating_add(hits).min(i64::MAX as u64);
        self.saw_coverpoint = true;
        Ok(())
    }
    pub fn memory_size(&self) -> usize {
        std::mem::size_of::<Self>()
            + self.testcase.len()
            + self.source.len()
            + self.lines.len() * 64
            + self.functions.keys().map(|n| n.len() + 96).sum::<usize>()
            + self
                .branches
                .values()
                .flatten()
                .flatten()
                .map(|b| b.expr.len() + 48)
                .sum::<usize>()
            + self
                .mcdc
                .values()
                .flat_map(|m| m.values())
                .map(|m| m.expr.len() + 48)
                .sum::<usize>()
            + self.version.as_ref().map_or(0, String::len)
    }
}

pub fn checked_add(a: u64, b: u64, file: &str, what: &str) -> Result<u64, Error> {
    let _ = (file, what);
    Ok(a.saturating_add(b).min(i64::MAX as u64))
}
