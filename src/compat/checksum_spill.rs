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
        let mut cursor = sorted.cursor()?;
        let mut index = source::SourceIndex::new(&tmpdir, part)?;
        let mut output = line_spill::Spill::new(&tmpdir, part, fanin)?;
        let mut group: Option<(String, u64, Option<String>)> = None;
        let mut pending = Vec::new();
        // Process one source/line group; its producer rows are ordered by input/section/ordinal.
        while let Some(row) = cursor.next()? {
            check_cancelled()?;
            let (source, line) = decode_key(&row.key)?;
            let (tc, provided, label, count, line_no, input, section, ordinal) =
                decode_value(&row.value)?;
            if group
                .as_ref()
                .is_some_and(|(s, l, _)| s != &source || *l != line)
            {
                flush_group_with_policy(&mut group, &mut pending, &mut output, req, &mut index)?;
            }
            if group.is_none() {
                group = Some((source.clone(), line, None));
            }
            let g = group.as_mut().unwrap();
            if g.2.is_none() && !provided.is_empty() {
                g.2 = Some(provided.clone());
            } else if !provided.is_empty() && g.2.as_deref().is_some_and(|old| old != provided) {
                let e = Error::at(
                    2,
                    &label,
                    line_no,
                    format!("checksum mismatch at {source}:{line}"),
                );
                if !policy::can_recover(req, &e) {
                    return Err(e);
                }
            }
            pending.push(line_spill::Entry {
                kind: 1,
                source,
                testcase: tc,
                value: provided,
                line,
                count,
                input,
                section,
                ordinal,
                label,
                line_no,
            });
            // Bound group retention using sort budget; oversized producer groups fail clearly.
            if pending
                .len()
                .saturating_mul(std::mem::size_of::<line_spill::Entry>())
                > part / 2
            {
                return Err(Error::new(
                    3,
                    "checksum source-line group exceeds memory budget; increase --mem-limit",
                ));
            }
            let _ = line_no;
        }
        flush_group_with_policy(&mut group, &mut pending, &mut output, req, &mut index)?;
        drop(cursor);
        drop(sorted);
        drop(index);
        let path = output.finish()?;
        Ok((output, path))
    }
}
fn flush_group_with_policy(
    g: &mut Option<(String, u64, Option<String>)>,
    rows: &mut Vec<line_spill::Entry>,
    out: &mut line_spill::Spill,
    req: &Request,
    index: &mut source::SourceIndex,
) -> Result<(), Error> {
    if let Some((src, ln, chosen)) = g.as_ref() {
        let mut section = super::record::Section::default();
        section.source = src.clone();
        section.lines.insert(*ln, (0, chosen.clone()));
        let label = rows.first().map(|r| r.label.as_str()).unwrap_or(src);
        let line_no = rows.first().map_or(0, |r| r.line_no);
        let payload = format!(
            "{ln},0{}",
            chosen.as_ref().map(|s| format!(",{s}")).unwrap_or_default()
        );
        policy::validate_or_generate_checksum(&mut section, &payload, req, label, line_no, index)
            .map_err(|e| Error::at(e.code, label, line_no, e.message))?;
        let value = section.lines.get(ln).and_then(|x| x.1.clone());
        for mut r in rows.drain(..) {
            r.value = value.clone().unwrap_or_default();
            out.push(r)?;
        }
    }
    *g = None;
    Ok(())
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
fn decode_value(b: &[u8]) -> Result<(String, String, String, u64, u64, u64, u64, u64), Error> {
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
                count: i as u64 + 2,
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
