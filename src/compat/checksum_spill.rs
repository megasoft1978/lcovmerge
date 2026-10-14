//! Bounded source-wide DA checksum propagation for the compatibility profile.
use super::{line_spill, policy, sort, source, Request};
use crate::common::{check_cancelled, Error};
use std::path::{Path, PathBuf};

/// Processes DA rows. A nonempty input `Entry.value` is its provided checksum;
/// output values contain the selected checksum (or are empty if unavailable).
pub(super) struct Processor {
    raw: sort::Sorter,
    tmpdir: PathBuf,
    budget: usize,
    fanin: usize,
}
impl Processor {
    pub(super) fn new(tmpdir: &Path, budget: usize, fanin: usize) -> Result<Self, Error> {
        let part = (budget / 3).max(1);
        Ok(Self {
            raw: sort::Sorter::new(tmpdir, part, 1)?,
            tmpdir: tmpdir.to_path_buf(),
            budget,
            fanin,
        })
    }
    pub(super) fn push(&mut self, row: line_spill::Entry) -> Result<(), Error> {
        check_cancelled()?;
        let mut key = Vec::with_capacity(row.source.len() + 40);
        key.extend_from_slice(row.source.as_bytes());
        key.push(0);
        for n in [row.line, row.input, row.section, row.ordinal] {
            key.extend_from_slice(&n.to_be_bytes());
        }
        let mut value = Vec::new();
        put(&mut value, row.testcase.as_bytes())?;
        put(&mut value, row.value.as_bytes())?;
        put(&mut value, row.label.as_bytes())?;
        value.extend_from_slice(&row.count.to_be_bytes());
        value.extend_from_slice(&row.line_no.to_be_bytes());
        for n in [row.input, row.section, row.ordinal] {
            value.extend_from_slice(&n.to_be_bytes());
        }
        self.raw.push(key, value)
    }
    /// Returns a bounded output spill and its completed run path. Caller keeps the spill alive while reading the path.
    pub(super) fn finish(self, req: &Request) -> Result<(line_spill::Spill, PathBuf), Error> {
        let Self {
            raw,
            tmpdir,
            budget,
            fanin,
        } = self;
        let part = (budget / 3).max(1);
        let sorted = raw.finish()?;
        let mut scan = sorted.cursor()?;
        let mut emit = sorted.cursor()?;
        let mut next = scan.next()?;
        let mut emit_next = emit.next()?;
        let mut index = source::SourceIndex::new(&tmpdir, part)?;
        let mut output = line_spill::Spill::new(&tmpdir, part, fanin)?;
        // One cursor resolves each checksum; the second replays that group's
        // producer rows. Even a single group larger than memory stays on disk.
        while let Some(first) = next.as_ref() {
            let (src, line) = decode_key(&first.key)?;
            let prefix = first.key[..first.key.len() - 24].to_vec();
            let mut chosen: Option<String> = None;
            while next
                .as_ref()
                .is_some_and(|row| row.key.starts_with(&prefix))
            {
                check_cancelled()?;
                let row = decode_entry(next.take().unwrap())?;
                let mut section = super::record::Section {
                    source: src.clone(),
                    ..Default::default()
                };
                section.lines.insert(
                    line,
                    (row.count, (!row.value.is_empty()).then_some(row.value)),
                );
                let payload = format!("{line},{}", row.count);
                policy::validate_or_generate_checksum(
                    &mut section,
                    &payload,
                    req,
                    &row.label,
                    row.line_no,
                    &mut index,
                )
                .map_err(|e| Error::at(e.code, &row.label, row.line_no, e.message))?;
                if let Some(value) = section.lines.remove(&line).and_then(|(_, sum)| sum) {
                    if chosen.as_ref().is_some_and(|old| old != &value) {
                        let error = super::policy::at(
                            super::policy::Kind::Version,
                            &row.label,
                            row.line_no,
                            format!("checksum mismatch at {src}:{line}"),
                        );
                        if !policy::can_recover(req, &error) {
                            return Err(error);
                        }
                    } else if chosen.is_none() {
                        chosen = Some(value);
                    }
                }
                next = scan.next()?;
            }
            while emit_next
                .as_ref()
                .is_some_and(|row| row.key.starts_with(&prefix))
            {
                let mut row = decode_entry(emit_next.take().unwrap())?;
                row.value = chosen.clone().unwrap_or_default();
                row.label.clear();
                row.line_no = 0;
                output.push(row)?;
                emit_next = emit.next()?;
            }
        }
        drop(scan);
        drop(emit);
        drop(sorted);
        drop(index);
        let path = output.finish()?;
        Ok((output, path))
    }
}

fn decode_entry(row: sort::Row) -> Result<line_spill::Entry, Error> {
    let (source, line) = decode_key(&row.key)?;
    let (testcase, value, label, count, line_no, input, section, ordinal) =
        decode_value(&row.value)?;
    Ok(line_spill::Entry {
        kind: 1,
        source,
        testcase,
        value,
        line,
        count,
        input,
        section,
        ordinal,
        label,
        line_no,
    })
}

fn put(out: &mut Vec<u8>, bytes: &[u8]) -> Result<(), Error> {
    let n = u32::try_from(bytes.len()).map_err(|_| Error::new(3, "checksum field too large"))?;
    out.extend_from_slice(&n.to_be_bytes());
    out.extend_from_slice(bytes);
    Ok(())
}
fn take<'a>(b: &'a [u8], p: &mut usize) -> Result<&'a [u8], Error> {
    if b.len().saturating_sub(*p) < 4 {
        return Err(Error::new(3, "invalid checksum sort row"));
    }
    let n = u32::from_be_bytes(b[*p..*p + 4].try_into().unwrap()) as usize;
    *p += 4;
    if b.len().saturating_sub(*p) < n {
        return Err(Error::new(3, "invalid checksum sort row"));
    }
    let v = &b[*p..*p + n];
    *p += n;
    Ok(v)
}
fn decode_key(b: &[u8]) -> Result<(String, u64), Error> {
    let p = b
        .iter()
        .position(|x| *x == 0)
        .ok_or_else(|| Error::new(3, "invalid checksum key"))?;
    if b.len() != p + 33 {
        return Err(Error::new(3, "invalid checksum key"));
    }
    let s =
        String::from_utf8(b[..p].to_vec()).map_err(|_| Error::new(3, "invalid source UTF-8"))?;
    Ok((s, u64::from_be_bytes(b[p + 1..p + 9].try_into().unwrap())))
}
type Fields = (String, String, String, u64, u64, u64, u64, u64);

fn decode_value(b: &[u8]) -> Result<Fields, Error> {
    let mut p = 0;
    let tc = String::from_utf8(take(b, &mut p)?.to_vec())
        .map_err(|_| Error::new(3, "invalid testcase UTF-8"))?;
    let sum = String::from_utf8(take(b, &mut p)?.to_vec())
        .map_err(|_| Error::new(3, "invalid checksum UTF-8"))?;
    let label = String::from_utf8(take(b, &mut p)?.to_vec())
        .map_err(|_| Error::new(3, "invalid label UTF-8"))?;
    if b.len() != p + 40 {
        return Err(Error::new(3, "invalid checksum value"));
    }
    Ok((
        tc,
        sum,
        label,
        u64::from_be_bytes(b[p..p + 8].try_into().unwrap()),
        u64::from_be_bytes(b[p + 8..p + 16].try_into().unwrap()),
        u64::from_be_bytes(b[p + 16..p + 24].try_into().unwrap()),
        u64::from_be_bytes(b[p + 24..p + 32].try_into().unwrap()),
        u64::from_be_bytes(b[p + 32..p + 40].try_into().unwrap()),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs, io::BufReader};
    #[test]
    fn single_source_line_group_larger_than_budget_streams_and_cleans_up() {
        let dir = tempfile::tempdir().unwrap();
        let runs = dir.path().join("runs");
        fs::create_dir(&runs).unwrap();
        let source = dir.path().join("unit.c");
        fs::write(&source, "hello\n").unwrap();
        let mut processor = Processor::new(&runs, 256 * 1024, 4).unwrap();
        for input in 0..4096 {
            processor
                .push(line_spill::Entry {
                    kind: 1,
                    source: source.to_string_lossy().into_owned(),
                    testcase: "same".into(),
                    value: String::new(),
                    line: 1,
                    count: 1,
                    input,
                    section: 0,
                    ordinal: 0,
                    label: "trace.info".into(),
                    line_no: 3,
                })
                .unwrap();
        }
        let (spill, path) = processor.finish(&Request::default()).unwrap();
        let mut reader = BufReader::new(fs::File::open(path).unwrap());
        let mut rows = 0;
        while let Some(row) = line_spill::read_next_public(&mut reader).unwrap() {
            assert_eq!(row.count, 1);
            assert_eq!(row.input, rows);
            assert_eq!(row.value, "XUFAKrxLKna5cZ2REBfFkg");
            rows += 1;
        }
        assert_eq!(rows, 4096);
        drop(reader);
        drop(spill);
        assert_eq!(fs::read_dir(runs).unwrap().count(), 0);
    }

    #[test]
    fn propagates_checksum_across_testcases_and_runs() {
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("unit.c");
        fs::write(&source, "hello\nworld\n").unwrap();
        let mut p = Processor::new(dir.path(), 1 << 20, 4).unwrap();
        for (i, tc) in [(0, "a"), (1, "b")] {
            p.push(line_spill::Entry {
                kind: 1,
                source: source.to_string_lossy().into_owned(),
                testcase: tc.into(),
                value: String::new(),
                line: 1,
                count: i + 2,
                input: i,
                section: 0,
                ordinal: 0,
                label: "fixture".into(),
                line_no: i + 1,
            })
            .unwrap();
        }
        let (spill, path) = p.finish(&Request::default()).unwrap();
        let mut f = BufReader::new(std::fs::File::open(path).unwrap());
        let mut rows = Vec::new();
        while let Some(r) = line_spill::read_next_public(&mut f).unwrap() {
            rows.push(r)
        }
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].value, "XUFAKrxLKna5cZ2REBfFkg");
        assert_eq!(rows[1].value, rows[0].value);
        drop(spill);
    }

    #[test]
    fn rejects_conflicting_producer_checksums() {
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("unit.c");
        fs::write(&source, "hello\n").unwrap();
        let mut p = Processor::new(dir.path(), 1 << 20, 4).unwrap();
        for (input, sum) in [(0, "first"), (1, "second")] {
            p.push(line_spill::Entry {
                kind: 1,
                source: source.to_string_lossy().into_owned(),
                testcase: input.to_string(),
                value: sum.into(),
                line: 1,
                count: 1,
                input,
                section: 0,
                ordinal: 0,
                label: "fixture".into(),
                line_no: input + 1,
            })
            .unwrap();
        }
        let error = match p.finish(&Request::default()) {
            Ok(_) => panic!("expected conflict"),
            Err(e) => e,
        };
        assert!(error.message.contains("checksum mismatch"));
    }

    #[test]
    fn reports_missing_source_with_input_label() {
        let dir = tempfile::tempdir().unwrap();
        let mut p = Processor::new(dir.path(), 1 << 20, 4).unwrap();
        p.push(line_spill::Entry {
            kind: 1,
            source: dir.path().join("gone.c").to_string_lossy().into_owned(),
            testcase: String::new(),
            value: String::new(),
            line: 1,
            count: 1,
            input: 0,
            section: 0,
            ordinal: 0,
            label: "trace.info".into(),
            line_no: 9,
        })
        .unwrap();
        let error = match p.finish(&Request::default()) {
            Ok(_) => panic!("expected missing source"),
            Err(e) => e,
        };
        assert!(error.message.contains("trace.info"));
    }
}
