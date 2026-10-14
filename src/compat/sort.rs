//! Bounded external byte-key sorting for compatibility metadata.
use crate::common::{check_cancelled, Error};
use std::cmp::Ordering;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;

const MAX_KEY: usize = 4 << 20;
const MAX_VALUE: usize = 2 << 20;
const MAX_FRAME: usize = 6 << 20;
const HEADER: usize = 24;
const IO: usize = 4096;
const OVERHEAD: usize = std::mem::size_of::<Row>() + 32;

pub(super) struct Row {
    pub key: Vec<u8>,
    pub value: Vec<u8>,
}
pub(super) struct Stats {
    pub runs: u64,
    pub passes: u64,
    pub bytes_read: u64,
    pub bytes_written: u64,
    pub workers: usize,
}
struct Item {
    row: Row,
    ordinal: u64,
}
pub(super) struct Sorter {
    dir: tempfile::TempDir,
    budget: usize,
    held: usize,
    batch: Vec<Item>,
    batch_cap: usize,
    runs: u64,
    passes: u64,
    read: u64,
    written: u64,
    ordinal: u64,
}
pub(super) struct Sorted {
    dir: Arc<tempfile::TempDir>,
    final_runs: u64,
    stats: Stats,
}
pub(super) struct Cursor {
    _dir: Arc<tempfile::TempDir>,
    runs: Vec<RunReader>,
    heads: Vec<Option<Item>>,
}

fn err(s: impl std::fmt::Display) -> Error {
    Error::new(3, format!("metadata sort failed: {s}"))
}
fn cancelled() -> Result<(), Error> {
    check_cancelled()
}
fn run_path(dir: &Path, pass: u64, id: u64) -> PathBuf {
    dir.join(format!("r-{pass}-{id}"))
}
fn write_item(w: &mut File, i: &Item, bytes: &mut u64) -> Result<(), Error> {
    cancelled()?;
    let k = u32::try_from(i.row.key.len()).map_err(err)?;
    let v = u32::try_from(i.row.value.len()).map_err(err)?;
    let mut h = [0u8; HEADER];
    h[..4].copy_from_slice(b"LCS1");
    h[4..8].copy_from_slice(&k.to_le_bytes());
    h[8..12].copy_from_slice(&v.to_le_bytes());
    h[12..20].copy_from_slice(&i.ordinal.to_le_bytes());
    w.write_all(&h).map_err(err)?;
    for data in [&i.row.key[..], &i.row.value[..]] {
        for chunk in data.chunks(IO) {
            cancelled()?;
            w.write_all(chunk).map_err(err)?;
        }
    }
    *bytes = bytes.saturating_add((HEADER + k as usize + v as usize) as u64);
    Ok(())
}
struct RunReader {
    file: File,
    bytes: u64,
}
impl RunReader {
    fn open(p: &Path) -> Result<Self, Error> {
        Ok(Self {
            file: File::open(p).map_err(err)?,
            bytes: 0,
        })
    }
    fn next(&mut self) -> Result<Option<Item>, Error> {
        cancelled()?;
        let mut h = [0u8; HEADER];
        let mut got = 0;
        while got < HEADER {
            cancelled()?;
            match self.file.read(&mut h[got..]) {
                Ok(0) if got == 0 => return Ok(None),
                Ok(0) => return Err(err("truncated frame header")),
                Ok(n) => got += n,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(err(e)),
            }
        }
        if &h[..4] != b"LCS1" || h[20..].iter().any(|b| *b != 0) {
            return Err(err("invalid frame header"));
        }
        let k = u32::from_le_bytes(h[4..8].try_into().unwrap()) as usize;
        let v = u32::from_le_bytes(h[8..12].try_into().unwrap()) as usize;
        if k > MAX_KEY || v > MAX_VALUE || k.checked_add(v).is_none_or(|n| n + HEADER > MAX_FRAME) {
            return Err(err("frame exceeds field or total limit"));
        }
        let mut key = vec![0; k];
        let mut value = vec![0; v];
        for data in [&mut key[..], &mut value[..]] {
            for chunk in data.chunks_mut(IO) {
                cancelled()?;
                self.file
                    .read_exact(chunk)
                    .map_err(|e| err(format!("truncated frame payload: {e}")))?;
            }
        }
        self.bytes += (HEADER + k + v) as u64;
        Ok(Some(Item {
            row: Row { key, value },
            ordinal: u64::from_le_bytes(h[12..20].try_into().unwrap()),
        }))
    }
}
fn cmp(a: &Item, b: &Item) -> Ordering {
    a.row.key.cmp(&b.row.key).then(a.ordinal.cmp(&b.ordinal))
}

impl Sorter {
    pub(super) fn new(tmpdir: &Path, budget: usize, jobs: usize) -> Result<Self, Error> {
        let dir = tempfile::Builder::new()
            .prefix("lcov-meta-")
            .tempdir_in(tmpdir)
            .map_err(err)?;
        let _requested_workers = jobs.max(1);
        Ok(Self {
            dir,
            budget,
            held: 0,
            batch: Vec::new(),
            batch_cap: 0,
            runs: 0,
            passes: 0,
            read: 0,
            written: 0,
            ordinal: 0,
        })
    }
    pub(super) fn push(&mut self, key: Vec<u8>, value: Vec<u8>) -> Result<(), Error> {
        cancelled()?;
        let n = key
            .capacity()
            .checked_add(value.capacity())
            .and_then(|x| x.checked_add(OVERHEAD))
            .ok_or_else(|| err("row size overflow"))?;
        if key.len() > MAX_KEY
            || value.len() > MAX_VALUE
            || key
                .len()
                .checked_add(value.len())
                .is_none_or(|x| x + HEADER > MAX_FRAME)
        {
            return Err(err("row exceeds key, value, or total limit"));
        }
        let minimum = n.checked_add(IO).ok_or_else(|| err("row size overflow"))?;
        if minimum > self.budget {
            return Err(err(format!("atomic row and merge buffers require at least {minimum} bytes; increase --mem-limit")));
        }
        self.batch.try_reserve(1).map_err(err)?;
        let delta =
            self.batch.capacity().saturating_sub(self.batch_cap) * std::mem::size_of::<Item>();
        if self
            .held
            .saturating_add(n)
            .saturating_add(delta)
            .saturating_add(IO)
            > self.budget
        {
            if !self.batch.is_empty() {
                self.flush()?;
            }
            if self.held.saturating_add(n).saturating_add(IO) > self.budget {
                return Err(err(format!("atomic row and batch allocation require at least {} bytes; increase --mem-limit", self.held.saturating_add(n).saturating_add(IO))));
            }
        }
        let delta =
            self.batch.capacity().saturating_sub(self.batch_cap) * std::mem::size_of::<Item>();
        self.batch_cap = self.batch.capacity();
        self.held = self.held.saturating_add(n).saturating_add(delta);
        let ordinal = self.ordinal;
        self.ordinal = self
            .ordinal
            .checked_add(1)
            .ok_or_else(|| err("insertion ordinal overflow"))?;
        self.batch.push(Item {
            row: Row { key, value },
            ordinal,
        });
        Ok(())
    }
    fn flush(&mut self) -> Result<(), Error> {
        if self.batch.is_empty() {
            return Ok(());
        }
        cancelled()?;
        self.batch.sort_by(cmp);
        let p = run_path(self.dir.path(), 0, self.runs);
        let mut f = File::create(p).map_err(err)?;
        for item in &self.batch {
            write_item(&mut f, item, &mut self.written)?;
        }
        f.sync_all().map_err(err)?;
        self.runs += 1;
        self.batch.clear();
        self.batch_cap = self.batch.capacity();
        self.held = self.batch_cap * std::mem::size_of::<Item>();
        self.passes = 1;
        Ok(())
    }
    pub(super) fn finish(mut self) -> Result<Sorted, Error> {
        self.flush()?;
        let mut count = self.runs;
        let mut pass = 0u64;
        // Reserve two complete maximum-sized row heads, their vector overhead, and I/O space.
        let per_head = MAX_FRAME + OVERHEAD + 256;
        let fanin = (self.budget.saturating_sub(IO) / per_head).min(64);
        if count > 1 && fanin < 2 {
            return Err(err(format!(
                "merge needs at least {} bytes for two row heads; increase --mem-limit",
                per_head * 2 + IO
            )));
        }
        while count > 1 {
            cancelled()?;
            pass += 1;
            let mut out_id = 0;
            let mut start = 0;
            while start < count {
                let n = (count - start).min(fanin as u64);
                let mut readers = Vec::new();
                for id in start..start + n {
                    readers.push(RunReader::open(&run_path(self.dir.path(), pass - 1, id))?);
                }
                let mut heads = Vec::new();
                for r in &mut readers {
                    heads.push(r.next()?);
                }
                let mut out = File::create(run_path(self.dir.path(), pass, out_id)).map_err(err)?;
                loop {
                    cancelled()?;
                    let best = heads
                        .iter()
                        .enumerate()
                        .filter_map(|(i, x)| x.as_ref().map(|v| (i, v)))
                        .min_by(|a, b| cmp(a.1, b.1))
                        .map(|x| x.0);
                    let Some(i) = best else { break };
                    write_item(&mut out, heads[i].as_ref().unwrap(), &mut self.written)?;
                    heads[i] = readers[i].next()?;
                }
                for r in &readers {
                    self.read += r.bytes;
                }
                out.sync_all().map_err(err)?;
                out_id += 1;
                start += n;
            }
            for id in 0..count {
                fs::remove_file(run_path(self.dir.path(), pass - 1, id)).map_err(err)?;
            }
            count = out_id;
            self.passes += 1;
        }
        Ok(Sorted {
            dir: Arc::new(self.dir),
            final_runs: count,
            stats: Stats {
                runs: self.runs,
                passes: self.passes,
                bytes_read: self.read,
                bytes_written: self.written,
                workers: 1,
            },
        })
    }
}
impl Sorted {
    pub(super) fn cursor(&self) -> Result<Cursor, Error> {
        let mut runs = Vec::new();
        let mut heads = Vec::new();
        if self.final_runs != 0 {
            runs.push(RunReader::open(&run_path(
                self.dir.path(),
                self.stats.passes.saturating_sub(1),
                0,
            ))?);
            heads.push(runs[0].next()?);
        }
        Ok(Cursor {
            _dir: self.dir.clone(),
            runs,
            heads,
        })
    }
    pub(super) fn stats(&self) -> &Stats {
        &self.stats
    }
}
impl Cursor {
    pub(super) fn next(&mut self) -> Result<Option<Row>, Error> {
        cancelled()?;
        let Some(i) = self.heads.iter().position(Option::is_some) else {
            return Ok(None);
        };
        let row = self.heads[i].take().unwrap().row;
        self.heads[i] = self.runs[i].next()?;
        Ok(Some(row))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stable_byte_order_and_cursor_keeps_tempdir_alive() {
        let parent = tempfile::tempdir().unwrap();
        let mut s = Sorter::new(parent.path(), 1 << 20, 8).unwrap();
        for (key, value) in [
            (b"b".as_slice(), b"1".as_slice()),
            (b"a", b"x"),
            (b"b", b"2"),
        ] {
            s.push(key.to_vec(), value.to_vec()).unwrap();
        }
        let sorted = s.finish().unwrap();
        let mut cursor = sorted.cursor().unwrap();
        drop(sorted);
        let mut rows = Vec::new();
        while let Some(row) = cursor.next().unwrap() {
            rows.push(row);
        }
        assert_eq!(
            rows.iter()
                .map(|r| (&r.key[..], &r.value[..]))
                .collect::<Vec<_>>(),
            [
                (b"a".as_slice(), b"x".as_slice()),
                (b"b", b"1"),
                (b"b", b"2")
            ]
        );
    }

    #[test]
    fn rejects_oversized_atomic_row_with_budget_guidance() {
        let parent = tempfile::tempdir().unwrap();
        let mut s = Sorter::new(parent.path(), 4096, 1).unwrap();
        let e = s.push(vec![0; 4097], vec![]).unwrap_err();
        assert!(e.message.contains("increase --mem-limit"));
    }

    #[test]
    fn detects_truncated_frames() {
        let parent = tempfile::tempdir().unwrap();
        let path = parent.path().join("bad");
        let mut f = File::create(&path).unwrap();
        f.write_all(b"LCS1").unwrap();
        let mut r = RunReader::open(&path).unwrap();
        match r.next() {
            Err(e) => assert!(e.message.contains("truncated")),
            Ok(_) => panic!("truncated frame accepted"),
        }
    }
}
