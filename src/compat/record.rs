use crate::common::Error;
use std::collections::BTreeMap;

#[derive(Clone, Debug, Default)]
pub struct Function {
    pub line: u64,
    pub end_line: Option<u64>,
    pub hits: u64,
}

#[derive(Clone, Debug, Default)]
pub struct Section {
    pub testcase: String,
    pub source: String,
    pub lines: BTreeMap<u64, (u64, Option<String>)>,
    pub functions: BTreeMap<String, Function>,
    pub modern: bool,
    pub version: Option<String>,
    pub saw_coverpoint: bool,
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
                .is_some_and(|s| s.is_empty() || s.contains(','))
        {
            return Err(Error::at(2, file, line, "malformed DA record"));
        }
        let entry = self.lines.entry(n).or_insert((0, None));
        entry.0 = entry
            .0
            .checked_add(count)
            .ok_or_else(|| Error::at(2, file, line, "DA count overflow"))?;
        if let Some(checksum) = checksum {
            entry.1 = Some(checksum);
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
            + self.version.as_ref().map_or(0, String::len)
    }
}
