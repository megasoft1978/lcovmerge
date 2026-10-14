//! Typed, externally sorted function records used by the bounded LCOV executor.
//!
//! Keys contain only sortable identity fields.  Payloads retain input coordinates so that
//! diagnostics remain deterministic after records have been reordered for joins.
use super::sort::{Cursor, Row, Sorted, Sorter};
use crate::common::Error;
use std::path::Path;

const VERSION: u8 = 1;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum Family {
    Legacy = 0,
    Modern = 1,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum Kind {
    Declaration = 0,
    Hit = 1,
}

/// A record's sortable identity is (source, testcase, input, section, family, symbol, ordinal).
/// `symbol` is a function name for legacy records and an original FNL index for modern records.
pub(super) struct Records {
    sorter: Sorter,
}

pub(super) struct Rows {
    cursor: Cursor,
    group: Option<Group>,
    declaration: Option<(u64, Option<u64>, u64)>,
}

#[derive(Eq, PartialEq)]
struct Group {
    source: String,
    testcase: String,
    input: u64,
    section: u64,
    family: Family,
    symbol: Vec<u8>,
}

#[derive(Debug)]
pub(super) struct FunctionRow {
    pub source: String,
    pub testcase: String,
    pub input: u64,
    pub section: u64,
    pub family: Family,
    pub symbol: Vec<u8>,
    pub ordinal: u64,
    pub kind: Kind,
    pub start: u64,
    pub end: Option<u64>,
    pub hits: u64,
    /// Legacy declarations carry their name here; modern hits carry the FNA alias.
    pub name: String,
    pub label: String,
    pub line: u64,
}

/// A declaration or hit whose reference has been resolved in input order.
#[derive(Debug)]
pub(super) struct LinkedRow {
    pub source: String,
    pub testcase: String,
    pub start: u64,
    pub end: Option<u64>,
    pub hits: u64,
    pub name: String,
    pub label: String,
    pub line: u64,
}

impl Records {
    pub fn new(tmpdir: &Path, budget: usize, jobs: usize) -> Result<Self, Error> {
        Ok(Self {
            sorter: Sorter::new(tmpdir, budget, jobs)?,
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub fn push(
        &mut self,
        source: &str,
        testcase: &str,
        input: u64,
        section: u64,
        family: Family,
        symbol: &[u8],
        ordinal: u64,
        kind: Kind,
        start: u64,
        end: Option<u64>,
        hits: u64,
        name: &str,
        label: &str,
        line: u64,
    ) -> Result<(), Error> {
        let mut key = Vec::with_capacity(source.len() + testcase.len() + symbol.len() + 42);
        push_string(&mut key, source)?;
        push_string(&mut key, testcase)?;
        key.extend_from_slice(&input.to_be_bytes());
        key.extend_from_slice(&section.to_be_bytes());
        key.push(family as u8);
        if family == Family::Modern {
            if symbol.len() != 8 {
                return Err(Error::new(
                    2,
                    "modern function symbol must be a big-endian u64",
                ));
            }
            key.extend_from_slice(symbol);
        } else {
            push_string(
                &mut key,
                std::str::from_utf8(symbol)
                    .map_err(|_| Error::new(2, "legacy function symbol is not UTF-8"))?,
            )?;
        }
        key.extend_from_slice(&ordinal.to_be_bytes());

        let mut value = Vec::with_capacity(name.len() + label.len() + 43);
        value.push(VERSION);
        value.push(kind as u8);
        value.extend_from_slice(&start.to_be_bytes());
        value.extend_from_slice(&end.unwrap_or(0).to_be_bytes());
        value.push(u8::from(end.is_some()));
        value.extend_from_slice(&hits.to_be_bytes());
        value.extend_from_slice(&line.to_be_bytes());
        push_string(&mut value, name)?;
        push_string(&mut value, label)?;
        self.sorter.push(key, value)
    }

    pub fn finish(self) -> Result<Rows, Error> {
        let sorted: Sorted = self.sorter.finish()?;
        Ok(Rows {
            cursor: sorted.cursor()?,
            group: None,
            declaration: None,
        })
    }
}

impl Rows {
    pub fn next(&mut self) -> Result<Option<FunctionRow>, Error> {
        self.cursor.next()?.map(decode).transpose()
    }

    /// Join each hit to its immediately preceding declaration without an ID/name map.
    /// Records are keyed by section and symbol, then ordered by their original line ordinal.
    pub fn next_linked(&mut self) -> Result<Option<LinkedRow>, Error> {
        let Some(row) = self.next()? else {
            return Ok(None);
        };
        let group = Group {
            source: row.source.clone(),
            testcase: row.testcase.clone(),
            input: row.input,
            section: row.section,
            family: row.family,
            symbol: row.symbol.clone(),
        };
        if self.group.as_ref() != Some(&group) {
            self.group = Some(group);
            self.declaration = None;
        }
        match row.kind {
            Kind::Declaration => {
                if row.family == Family::Modern && self.declaration.is_some() {
                    return Err(record_error(&row, "invalid or duplicate FNL index"));
                }
                if let Some((start, end, _)) = self.declaration {
                    if start != row.start || end != row.end {
                        return Err(record_error(&row, "inconsistent function declaration"));
                    }
                } else {
                    self.declaration = Some((row.start, row.end, row.ordinal));
                }
                let name = if row.family == Family::Legacy {
                    row.name.clone()
                } else {
                    String::new()
                };
                Ok(Some(LinkedRow {
                    source: row.source,
                    testcase: row.testcase,
                    start: row.start,
                    end: row.end,
                    hits: 0,
                    name,
                    label: row.label,
                    line: row.line,
                }))
            }
            Kind::Hit => {
                let Some((start, end, declaration_ordinal)) = self.declaration else {
                    let message = if row.family == Family::Legacy {
                        "FNDA references undeclared function"
                    } else {
                        "FNA references unknown function index"
                    };
                    return Err(record_error(&row, message));
                };
                if row.ordinal < declaration_ordinal {
                    let message = if row.family == Family::Legacy {
                        "FNDA references undeclared function"
                    } else {
                        "FNA references unknown function index"
                    };
                    return Err(record_error(&row, message));
                }
                Ok(Some(LinkedRow {
                    source: row.source,
                    testcase: row.testcase,
                    start,
                    end,
                    hits: row.hits,
                    name: row.name,
                    label: row.label,
                    line: row.line,
                }))
            }
        }
    }
}

fn record_error(row: &FunctionRow, message: &str) -> Error {
    Error::at(2, &row.label, row.line, message)
}

fn push_string(dst: &mut Vec<u8>, s: &str) -> Result<(), Error> {
    if s.as_bytes().contains(&0) {
        return Err(Error::new(2, "NUL in function spill key"));
    }
    dst.extend_from_slice(s.as_bytes());
    dst.push(0);
    Ok(())
}

fn take_string(data: &mut &[u8]) -> Result<String, Error> {
    let end = data.iter().position(|b| *b == 0).ok_or_else(corrupt)?;
    let (bytes, rest) = data.split_at(end);
    *data = &rest[1..];
    String::from_utf8(bytes.to_vec()).map_err(|_| corrupt())
}

fn take_u64(data: &mut &[u8]) -> Result<u64, Error> {
    if data.len() < 8 {
        return Err(corrupt());
    }
    let (head, rest) = data.split_at(8);
    *data = rest;
    Ok(u64::from_be_bytes(head.try_into().map_err(|_| corrupt())?))
}

fn decode(row: Row) -> Result<FunctionRow, Error> {
    let mut key = row.key.as_slice();
    let source = take_string(&mut key)?;
    let testcase = take_string(&mut key)?;
    let input = take_u64(&mut key)?;
    let section = take_u64(&mut key)?;
    if key.is_empty() {
        return Err(corrupt());
    }
    let family = match key[0] {
        0 => Family::Legacy,
        1 => Family::Modern,
        _ => return Err(corrupt()),
    };
    key = &key[1..];
    let symbol = if family == Family::Modern {
        if key.len() < 8 {
            return Err(corrupt());
        }
        let symbol = key[..8].to_vec();
        key = &key[8..];
        symbol
    } else {
        let nul = key.iter().position(|b| *b == 0).ok_or_else(corrupt)?;
        let symbol = key[..nul].to_vec();
        key = &key[nul + 1..];
        symbol
    };
    let ordinal = take_u64(&mut key)?;
    if !key.is_empty() {
        return Err(corrupt());
    }

    let mut value = row.value.as_slice();
    if value.len() < 35 || value[0] != VERSION {
        return Err(corrupt());
    }
    let kind = match value[1] {
        0 => Kind::Declaration,
        1 => Kind::Hit,
        _ => return Err(corrupt()),
    };
    value = &value[2..];
    let start = take_u64(&mut value)?;
    let end_value = take_u64(&mut value)?;
    let has_end = *value.first().ok_or_else(corrupt)?;
    value = &value[1..];
    if has_end > 1 {
        return Err(corrupt());
    }
    let hits = take_u64(&mut value)?;
    let line = take_u64(&mut value)?;
    let name = take_string(&mut value)?;
    let label = take_string(&mut value)?;
    if !value.is_empty() {
        return Err(corrupt());
    }
    Ok(FunctionRow {
        source,
        testcase,
        input,
        section,
        family,
        symbol,
        ordinal,
        kind,
        start,
        end: (has_end == 1).then_some(end_value),
        hits,
        name,
        label,
        line,
    })
}

fn corrupt() -> Error {
    Error::new(3, "corrupt function spill record")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn spill_round_trips_typed_rows_across_multiple_passes_and_cleans_up() {
        let parent = tempfile::tempdir().unwrap();
        let mut records = Records::new(parent.path(), 32 * 1024, 1).unwrap();
        for i in (0..4096u64).rev() {
            records
                .push(
                    "src.c",
                    "test",
                    0,
                    0,
                    Family::Legacy,
                    b"large_fn",
                    i,
                    if i == 0 { Kind::Declaration } else { Kind::Hit },
                    17,
                    Some(99),
                    i,
                    "large_fn",
                    "fixture.info",
                    i + 1,
                )
                .unwrap();
        }
        let mut rows = records.finish().unwrap();
        for expected in 0..4096u64 {
            let row = rows.next().unwrap().unwrap();
            assert_eq!(row.ordinal, expected);
            assert_eq!(row.source, "src.c");
            assert_eq!(row.start, 17);
            assert_eq!(row.end, Some(99));
        }
        assert!(rows.next().unwrap().is_none());
        drop(rows);
        assert_eq!(fs::read_dir(parent.path()).unwrap().count(), 0);
    }

    #[test]
    fn rejects_nul_in_identity_fields() {
        let parent = tempfile::tempdir().unwrap();
        let mut records = Records::new(parent.path(), 1 << 20, 1).unwrap();
        let error = records
            .push(
                "x\0.c",
                "",
                0,
                0,
                Family::Legacy,
                b"f",
                0,
                Kind::Declaration,
                1,
                None,
                0,
                "f",
                "fixture",
                1,
            )
            .unwrap_err();
        assert!(error.message.contains("NUL"));
    }

    #[test]
    fn modern_ids_and_large_alias_groups_join_one_row_at_a_time() {
        let parent = tempfile::tempdir().unwrap();
        let mut records = Records::new(parent.path(), 32 * 1024, 1).unwrap();
        let id = u64::MAX - 7;
        records
            .push(
                "large.c",
                "case",
                2,
                9,
                Family::Modern,
                &id.to_be_bytes(),
                1,
                Kind::Declaration,
                4,
                Some(90),
                0,
                "",
                "large.info",
                3,
            )
            .unwrap();
        for n in (0..4096u64).rev() {
            records
                .push(
                    "large.c",
                    "case",
                    2,
                    9,
                    Family::Modern,
                    &id.to_be_bytes(),
                    n + 2,
                    Kind::Hit,
                    4,
                    Some(90),
                    n,
                    &format!("alias_{n:04}"),
                    "large.info",
                    n + 4,
                )
                .unwrap();
        }
        let mut rows = records.finish().unwrap();
        let declaration = rows.next_linked().unwrap().unwrap();
        assert_eq!(declaration.start, 4);
        assert_eq!(declaration.name, "");
        for expected in 0..4096u64 {
            let hit = rows.next_linked().unwrap().unwrap();
            assert_eq!(hit.name, format!("alias_{expected:04}"));
            assert_eq!(hit.hits, expected);
        }
        assert!(rows.next_linked().unwrap().is_none());
    }

    #[test]
    fn sorted_join_rejects_forward_and_orphan_references() {
        for declaration in [false, true] {
            let parent = tempfile::tempdir().unwrap();
            let mut records = Records::new(parent.path(), 1 << 20, 1).unwrap();
            if declaration {
                records
                    .push(
                        "x.c",
                        "t",
                        0,
                        1,
                        Family::Legacy,
                        b"f",
                        2,
                        Kind::Declaration,
                        1,
                        None,
                        0,
                        "f",
                        "x.info",
                        2,
                    )
                    .unwrap();
            }
            records
                .push(
                    "x.c",
                    "t",
                    0,
                    1,
                    Family::Legacy,
                    b"f",
                    1,
                    Kind::Hit,
                    1,
                    None,
                    1,
                    "f",
                    "x.info",
                    1,
                )
                .unwrap();
            let mut rows = records.finish().unwrap();
            assert!(rows.next_linked().unwrap_err().message.contains("FNDA"));
        }
    }
}
