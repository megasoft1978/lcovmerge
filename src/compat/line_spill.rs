//! Bounded external sorting for the compatible DA-only profile.
use super::codec;
use crate::common::{check_cancelled, Error, IO_BLOCK};
use std::cmp::Ordering;
use std::collections::BinaryHeap;
use std::fs::{self, File};
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};

const ROW_OVERHEAD: usize = 512;

#[derive(Clone, Debug)]
pub(super) struct Entry {
    pub kind: u8, // metadata (VER) sorts before DA rows
    pub source: String,
    pub testcase: String,
    pub value: String,
    pub line: u64,
    pub count: f64,
    pub input: u64,
    pub section: u64,
    pub ordinal: u64,
    pub label: String,
    pub line_no: u64,
}
impl PartialEq for Entry {
    fn eq(&self, o: &Self) -> bool {
        self.cmp(o) == Ordering::Equal
    }
}
impl Eq for Entry {}
impl Ord for Entry {
    fn cmp(&self, o: &Self) -> Ordering {
        self.source
            .cmp(&o.source)
            .then(self.kind.cmp(&o.kind))
            .then(self.testcase.cmp(&o.testcase))
            .then(self.line.cmp(&o.line))
            .then(self.input.cmp(&o.input))
            .then(self.section.cmp(&o.section))
            .then(self.ordinal.cmp(&o.ordinal))
    }
}
impl PartialOrd for Entry {
    fn partial_cmp(&self, o: &Self) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}

pub(super) struct Spill {
    dir: tempfile::TempDir,
    budget: usize,
    batch: Vec<Entry>,
    held: usize,
    runs: Vec<PathBuf>,
    pub run_count: usize,
    pub passes: usize,
    pub traffic: u64,
    pub fanin: usize,
}
impl Spill {
    pub fn new(parent: &Path, budget: usize, fanin: usize) -> Result<Self, Error> {
        let dir = tempfile::Builder::new()
            .prefix("lcov-compat-")
            .tempdir_in(parent)
            .map_err(|e| Error::new(3, format!("cannot create compatibility tempdir: {e}")))?;
        Ok(Self {
            dir,
            budget,
            batch: Vec::new(),
            held: 0,
            runs: Vec::new(),
            run_count: 0,
            passes: 0,
            traffic: 0,
            fanin,
        })
    }
    pub fn push(&mut self, row: Entry) -> Result<(), Error> {
        let n = row
            .source
            .capacity()
            .checked_add(row.testcase.capacity())
            .and_then(|n| n.checked_add(row.value.capacity()))
            .and_then(|n| n.checked_add(row.label.capacity()))
            .and_then(|n| n.checked_add(ROW_OVERHEAD))
            .ok_or_else(|| Error::new(3, "compatibility row size overflow"))?;
        if n > self.budget {
            return Err(Error::new(3, format!("largest atomic compatibility row requires at least {n} bytes; increase --mem-limit")));
        }
        if self.held.saturating_add(n) > self.budget && !self.batch.is_empty() {
            self.flush()?;
        }
        self.held += n;
        self.batch.push(row);
        Ok(())
    }
    fn path(&self, pass: usize, n: usize) -> PathBuf {
        self.dir.path().join(format!("run-{pass:04}-{n:016}.bin"))
    }
    fn flush(&mut self) -> Result<(), Error> {
        if self.batch.is_empty() {
            return Ok(());
        }
        check_cancelled()?;
        self.batch.sort_unstable();
        let path = self.path(0, self.run_count);
        let mut out = BufWriter::with_capacity(IO_BLOCK, File::create(&path).map_err(ioerr)?);
        for row in self.batch.drain(..) {
            self.traffic += write_entry(&mut out, &row)?;
        }
        out.flush().map_err(ioerr)?;
        self.runs.push(path);
        self.run_count += 1;
        self.batch = Vec::new();
        self.held = 0;
        Ok(())
    }
    pub fn finish(&mut self) -> Result<PathBuf, Error> {
        self.flush()?;
        let mut runs = std::mem::take(&mut self.runs);
        let mut pass = 1;
        while runs.len() > 1 {
            let mut next = Vec::new();
            for (chunk_no, chunk) in runs.chunks(self.fanin).enumerate() {
                check_cancelled()?;
                let path = self.path(pass, chunk_no);
                merge_runs(chunk, &path, &mut self.traffic)?;
                next.push(path);
            }
            self.run_count += next.len();
            for p in &runs {
                let _ = fs::remove_file(p);
            }
            runs = next;
            pass += 1;
            self.passes += 1;
        }
        if let Some(p) = runs.pop() {
            Ok(p)
        } else {
            let p = self.path(pass, 0);
            File::create(&p).map_err(ioerr)?;
            Ok(p)
        }
    }
    pub fn dir(&self) -> &Path {
        self.dir.path()
    }
}

#[derive(Eq)]
struct Head {
    row: Entry,
    run: usize,
}
impl PartialEq for Head {
    fn eq(&self, o: &Self) -> bool {
        self.row == o.row && self.run == o.run
    }
}
impl Ord for Head {
    fn cmp(&self, o: &Self) -> Ordering {
        o.row.cmp(&self.row).then_with(|| o.run.cmp(&self.run))
    }
}
impl PartialOrd for Head {
    fn partial_cmp(&self, o: &Self) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}
pub(super) fn read_next_public(r: &mut BufReader<File>) -> Result<Option<Entry>, Error> {
    let mut first = [0u8; 1];
    match r.read(&mut first) {
        Ok(0) => return Ok(None),
        Ok(1) => {}
        Ok(_) => unreachable!(),
        Err(e) => return Err(ioerr(e)),
    }
    struct Prefix<'a> {
        first: Option<u8>,
        rest: &'a mut BufReader<File>,
    }
    impl Read for Prefix<'_> {
        fn read(&mut self, b: &mut [u8]) -> std::io::Result<usize> {
            if let Some(x) = self.first.take() {
                if b.is_empty() {
                    self.first = Some(x);
                    return Ok(0);
                }
                b[0] = x;
                if b.len() == 1 {
                    return Ok(1);
                }
                return self.rest.read(&mut b[1..]).map(|n| n + 1);
            }
            self.rest.read(b)
        }
    }
    let mut prefixed = Prefix {
        first: Some(first[0]),
        rest: r,
    };
    let fields = codec::read_record(&mut prefixed, 11)?;
    decode_fields(fields).map(Some)
}
fn merge_runs(paths: &[PathBuf], dst: &Path, traffic: &mut u64) -> Result<(), Error> {
    let mut readers: Vec<_> = paths
        .iter()
        .map(|p| {
            File::open(p)
                .map(|f| BufReader::with_capacity(IO_BLOCK, f))
                .map_err(ioerr)
        })
        .collect::<Result<_, _>>()?;
    let mut heads = BinaryHeap::new();
    for (i, r) in readers.iter_mut().enumerate() {
        if let Some(row) = read_next_public(r)? {
            heads.push(Head { row, run: i });
        }
    }
    let mut out = BufWriter::with_capacity(IO_BLOCK, File::create(dst).map_err(ioerr)?);
    while let Some(Head { row, run }) = heads.pop() {
        check_cancelled()?;
        *traffic += write_entry(&mut out, &row)?;
        if let Some(row) = read_next_public(&mut readers[run])? {
            heads.push(Head { row, run });
        }
    }
    out.flush().map_err(ioerr)
}
fn write_entry(out: &mut impl Write, e: &Entry) -> Result<u64, Error> {
    let kind = [e.kind];
    let numbers = [
        e.line,
        e.count.to_bits(),
        e.input,
        e.section,
        e.ordinal,
        e.line_no,
    ];
    let encoded_numbers: Vec<_> = numbers.map(u64::to_le_bytes).into_iter().collect();
    let mut fields: Vec<&[u8]> = Vec::with_capacity(11);
    fields.push(&kind);
    fields.extend([
        e.source.as_bytes(),
        e.testcase.as_bytes(),
        e.value.as_bytes(),
        e.label.as_bytes(),
    ]);
    fields.extend(encoded_numbers.iter().map(|n| n.as_slice()));
    let traffic = 20u64 + fields.iter().map(|f| 4u64 + f.len() as u64).sum::<u64>();
    codec::write_record(out, &fields)?;
    Ok(traffic)
}
fn decode_fields(mut f: Vec<Vec<u8>>) -> Result<Entry, Error> {
    if f.len() != 11 || f[0].len() != 1 {
        return Err(corrupt());
    }
    let kind = f.remove(0)[0];
    if kind > 1 {
        return Err(corrupt());
    }
    let source = String::from_utf8(f.remove(0)).map_err(|_| corrupt())?;
    let testcase = String::from_utf8(f.remove(0)).map_err(|_| corrupt())?;
    let value = String::from_utf8(f.remove(0)).map_err(|_| corrupt())?;
    let label = String::from_utf8(f.remove(0)).map_err(|_| corrupt())?;
    let mut nums = [0u64; 6];
    for (dst, bytes) in nums.iter_mut().zip(f) {
        if bytes.len() != 8 {
            return Err(corrupt());
        }
        *dst = u64::from_le_bytes(bytes.try_into().map_err(|_| corrupt())?);
    }
    let count = f64::from_bits(nums[1]);
    if !count.is_finite() || count < 0.0 {
        return Err(corrupt());
    }
    Ok(Entry {
        kind,
        source,
        testcase,
        value,
        label,
        line: nums[0],
        count,
        input: nums[2],
        section: nums[3],
        ordinal: nums[4],
        line_no: nums[5],
    })
}
fn corrupt() -> Error {
    Error::new(2, "corrupt compatibility spill record")
}
fn ioerr(e: std::io::Error) -> Error {
    Error::new(3, format!("compatibility spill I/O failed: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(source: &str, testcase: &str, line: u64, ordinal: u64) -> Entry {
        Entry {
            kind: 1,
            source: source.into(),
            testcase: testcase.into(),
            value: String::new(),
            line,
            count: ordinal as f64,
            input: 0,
            section: 0,
            ordinal,
            label: "fixture".into(),
            line_no: ordinal,
        }
    }

    #[test]
    fn multi_pass_merge_sorts_more_than_three_batch_budgets_and_cleans_runs() {
        let parent = tempfile::tempdir().unwrap();
        let mut spill = Spill::new(parent.path(), 700, 2).unwrap();
        for i in (1..=24).rev() {
            spill.push(row("z.c", "tn", i, i)).unwrap();
        }
        assert!(24 * ROW_OVERHEAD > 3 * spill.budget);
        let dir = spill.dir().to_owned();
        let path = spill.finish().unwrap();
        assert!(spill.run_count >= 12);
        assert!(spill.passes >= 3);
        let mut reader = BufReader::new(File::open(path).unwrap());
        for expected in 1..=24 {
            assert_eq!(
                read_next_public(&mut reader).unwrap().unwrap().line,
                expected
            );
        }
        assert!(read_next_public(&mut reader).unwrap().is_none());
        drop(spill);
        assert!(!dir.exists());
    }

    #[test]
    fn atomic_source_testcase_row_over_budget_has_actionable_error() {
        let parent = tempfile::tempdir().unwrap();
        let mut spill = Spill::new(parent.path(), 1024, 2).unwrap();
        let e = spill
            .push(row(&"s".repeat(900), &"t".repeat(200), 1, 1))
            .unwrap_err();
        assert!(e.message.contains("largest atomic compatibility row"));
        assert!(e.message.contains("increase --mem-limit"));
    }
}
