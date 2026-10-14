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

impl Section {
    pub fn add_line(&mut self, payload: &str, file: &str, line: u64) -> Result<(), Error> {
        let mut fields = payload.split(',');
        let n = number(fields.next().unwrap_or(""), file, line)?;
        if n == 0 {
            return Err(Error::at(2, file, line, "DA line must be positive"));
        }
        let count = number(fields.next().unwrap_or(""), file, line)?;
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
        f.hits = f
            .hits
            .checked_add(hits)
            .ok_or_else(|| Error::at(2, file, line, "function count overflow"))?;
        self.saw_coverpoint = true;
        Ok(())
    }
    pub fn memory_size(&self) -> usize {
        std::mem::size_of::<Self>()
            + self.testcase.len()
            + self.source.len()
            + self.lines.len() * 64
            + self
                .functions
                .iter()
                .map(|(n, _)| n.len() + 96)
                .sum::<usize>()
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
    a.checked_add(b)
        .ok_or_else(|| Error::new(2, format!("{file}: {what} count overflow")))
}
