//! Bounded external byte-key sorting for compatibility metadata.
use crate::common::{check_cancelled, Error};
use std::cmp::Ordering;
use std::collections::BinaryHeap;
use std::fs::{self, File};
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering as AtomicOrdering};
use std::sync::Arc;
use std::thread::{self, JoinHandle};

const MAX_KEY: usize = 4 << 20;
const MAX_VALUE: usize = 2 << 20;
const MAX_FRAME: usize = 6 << 20;
const HEADER: usize = 24;
const IO: usize = 4096;
const MIN_BATCH: usize = 4 << 20;
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
    #[cfg(test)]
    pub fanin: usize,
}
struct Item {
    row: Row,
    ordinal: u64,
}
// BinaryHeap is a max-heap; reverse the full stable merge ordering.
struct Head {
    item: Item,
    run: usize,
}
impl Ord for Head {
    fn cmp(&self, other: &Self) -> Ordering {
        cmp(&other.item, &self.item).then_with(|| other.run.cmp(&self.run))
    }
}
impl PartialOrd for Head {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl PartialEq for Head {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}
impl Eq for Head {}

pub(super) struct Sorter {
    dir: Arc<tempfile::TempDir>,
    budget: usize,
    held: usize,
    batch: Vec<Item>,
    batch_cap: usize,
    batch_budget: usize,
    requested: usize,
    handles: Vec<JoinHandle<Result<u64, Error>>>,
    workers: usize,
    active: Arc<AtomicUsize>,
    max_workers: Arc<AtomicUsize>,
    runs: u64,
    passes: u64,
    read: u64,
    written: u64,
    ordinal: u64,
    max_frame: usize,
}
pub(super) struct Sorted {
    dir: Arc<tempfile::TempDir>,
    final_runs: u64,
    final_pass: u64,
    max_frame: usize,
    stats: Stats,
}
pub(super) struct Cursor {
    _dir: Arc<tempfile::TempDir>,
    reader: Option<RunReader>,
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
fn write_item(w: &mut impl Write, i: &Item, bytes: &mut u64) -> Result<(), Error> {
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
    file: BufReader<File>,
    bytes: u64,
    max_frame: usize,
}
impl RunReader {
    fn open(p: &Path, max_frame: usize) -> Result<Self, Error> {
        let file = File::open(p).map_err(err)?;
        if file.metadata().map_err(err)?.len() < HEADER as u64 {
            return Err(err("empty or truncated run header"));
        }
        Ok(Self {
            file: BufReader::with_capacity(IO, file),
            bytes: 0,
            max_frame,
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
        if k > MAX_KEY
            || v > MAX_VALUE
            || k.checked_add(v)
                .is_none_or(|n| n + HEADER > MAX_FRAME || n + HEADER > self.max_frame)
        {
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
        // Parallel batches share the component budget with the producer. The
        // division bounds requested + 1 without risking addition overflow.
        let requested = jobs
            .max(1)
            .min((budget / MIN_BATCH).saturating_sub(1).max(1));
        Ok(Self {
            dir: Arc::new(dir),
            budget,
            held: 0,
            batch: Vec::new(),
            batch_cap: 0,
            batch_budget: if requested == 1 {
                budget
            } else {
                budget / (requested + 1)
            },
            requested,
            handles: Vec::with_capacity(requested),
            workers: 0,
            active: Arc::new(AtomicUsize::new(0)),
            max_workers: Arc::new(AtomicUsize::new(0)),
            runs: 0,
            passes: 0,
            read: 0,
            written: 0,
            ordinal: 0,
            max_frame: HEADER,
        })
    }
    pub(super) fn push(&mut self, key: Vec<u8>, value: Vec<u8>) -> Result<(), Error> {
        cancelled()?;
        self.max_frame = self
            .max_frame
            .max(key.len().saturating_add(value.len()).saturating_add(HEADER));
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
        if minimum > self.batch_budget {
            while !self.handles.is_empty() {
                self.join_one()?;
            }
            self.requested = 1;
            self.batch_budget = self.budget;
        }
        let item_size = std::mem::size_of::<Item>();
        let needs_growth = self.batch.len() == self.batch.capacity();
        let requested = if needs_growth {
            self.batch.capacity().saturating_mul(2).max(4)
        } else {
            self.batch.capacity()
        };
        let growth = requested
            .saturating_sub(self.batch.capacity())
            .saturating_mul(item_size);
        let relocation = if needs_growth {
            self.batch.capacity().saturating_mul(item_size)
        } else {
            0
        };
        if self
            .held
            .saturating_add(n)
            .saturating_add(growth)
            .saturating_add(relocation)
            .saturating_add(IO)
            > self.batch_budget
        {
            self.flush()?;
            self.batch = Vec::new();
            self.batch_cap = 0;
            self.held = 0;
        }
        let old_capacity = self.batch.capacity();
        if self.batch.len() == old_capacity {
            let requested = old_capacity.saturating_mul(2).max(4);
            let peak = self
                .held
                .saturating_add(n)
                .saturating_add(requested.saturating_mul(item_size))
                .saturating_add(IO);
            if peak > self.batch_budget && self.handles.is_empty() && self.requested > 1 {
                self.requested = 1;
                self.batch_budget = self.budget;
            }
            if peak > self.batch_budget {
                return Err(err(
                    "atomic row plus vector growth exceeds budget; increase --mem-limit",
                ));
            }
            self.batch
                .try_reserve_exact(requested - self.batch.len())
                .map_err(err)?;
        }
        let delta = self.batch.capacity().saturating_sub(old_capacity) * item_size;
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
        while self.handles.len() >= self.requested {
            self.join_one()?;
        }
        let id = self.runs;
        self.runs += 1;
        let path = run_path(self.dir.path(), 0, id);
        let batch = std::mem::take(&mut self.batch);
        let active = self.active.clone();
        let max_workers = self.max_workers.clone();
        self.held = 0;
        self.batch_cap = 0;
        if self.requested == 1 {
            let mut batch = batch;
            batch.sort_unstable_by(cmp);
            let mut bytes = 0;
            let mut out = BufWriter::with_capacity(IO, File::create(path).map_err(err)?);
            for item in &batch {
                write_item(&mut out, item, &mut bytes)?;
            }
            out.flush().map_err(err)?;
            self.written = self.written.saturating_add(bytes);
            self.max_workers.fetch_max(1, AtomicOrdering::Relaxed);
            return Ok(());
        }
        self.handles.push(
            thread::Builder::new()
                .name("lcov-sort".into())
                .spawn(move || {
                    let now = active.fetch_add(1, AtomicOrdering::Relaxed) + 1;
                    max_workers.fetch_max(now, AtomicOrdering::Relaxed);
                    let result = (|| {
                        cancelled()?;
                        let mut batch = batch;
                        batch.sort_unstable_by(cmp);
                        let mut bytes = 0;
                        let mut f = BufWriter::with_capacity(IO, File::create(path).map_err(err)?);
                        for item in &batch {
                            write_item(&mut f, item, &mut bytes)?;
                        }
                        f.flush().map_err(err)?;
                        Ok(bytes)
                    })();
                    active.fetch_sub(1, AtomicOrdering::Relaxed);
                    result
                })
                .map_err(err)?,
        );
        self.workers = self.max_workers.load(AtomicOrdering::Relaxed);
        Ok(())
    }
    fn join_one(&mut self) -> Result<(), Error> {
        let h = self.handles.remove(0);
        match h.join() {
            Ok(result) => {
                self.written = self.written.saturating_add(result?);
                Ok(())
            }
            Err(_) => Err(err("sort worker panicked")),
        }
    }
    pub(super) fn finish(mut self) -> Result<Sorted, Error> {
        self.flush()?;
        while !self.handles.is_empty() {
            self.join_one()?;
        }
        let mut count = self.runs;
        let mut pass = 0u64;
        #[cfg(test)]
        let mut used_fanin = 0usize;
        // Charge each reader, its input buffer, and its owned heap head,
        // including a complete maximum-sized frame. Reserve output I/O too.
        let per_head =
            self.max_frame + std::mem::size_of::<RunReader>() + std::mem::size_of::<Head>() + IO;
        let fanin = descriptor_fanin((self.budget.saturating_sub(IO) / per_head).min(64));
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
                #[cfg(test)]
                {
                    used_fanin = used_fanin.max(n as usize);
                }
                let mut readers = Vec::with_capacity(n as usize);
                for id in start..start + n {
                    readers.push(RunReader::open(
                        &run_path(self.dir.path(), pass - 1, id),
                        self.max_frame,
                    )?);
                }
                let mut heads = BinaryHeap::with_capacity(n as usize);
                for (run, reader) in readers.iter_mut().enumerate() {
                    if let Some(item) = reader.next()? {
                        heads.push(Head { item, run });
                    }
                }
                let mut out = BufWriter::with_capacity(
                    IO,
                    File::create(run_path(self.dir.path(), pass, out_id)).map_err(err)?,
                );
                while let Some(Head { item, run }) = heads.pop() {
                    cancelled()?;
                    write_item(&mut out, &item, &mut self.written)?;
                    drop(item);
                    if let Some(item) = readers[run].next()? {
                        heads.push(Head { item, run });
                    }
                }
                for r in &readers {
                    self.read += r.bytes;
                }
                out.flush().map_err(err)?;
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
            dir: self.dir.clone(),
            final_runs: count,
            final_pass: pass,
            max_frame: self.max_frame,
            stats: Stats {
                runs: self.runs,
                passes: self.passes,
                bytes_read: self.read,
                bytes_written: self.written,
                workers: self.max_workers.load(AtomicOrdering::Relaxed).max(1),
                #[cfg(test)]
                fanin: used_fanin,
            },
        })
    }
}
impl Drop for Sorter {
    fn drop(&mut self) {
        for handle in self.handles.drain(..) {
            let _ = handle.join();
        }
    }
}
impl Sorted {
    pub(super) fn cursor(&self) -> Result<Cursor, Error> {
        let reader = if self.final_runs != 0 {
            Some(RunReader::open(
                &run_path(self.dir.path(), self.final_pass, 0),
                self.max_frame,
            )?)
        } else {
            None
        };
        Ok(Cursor {
            _dir: self.dir.clone(),
            reader,
        })
    }
    pub(super) fn stats(&self) -> &Stats {
        &self.stats
    }
}
impl Cursor {
    pub(super) fn next(&mut self) -> Result<Option<Row>, Error> {
        cancelled()?;
        match self.reader.as_mut() {
            Some(reader) => reader.next().map(|item| item.map(|item| item.row)),
            None => Ok(None),
        }
    }
}
fn descriptor_fanin(proposed: usize) -> usize {
    #[cfg(unix)]
    {
        let mut limit = std::mem::MaybeUninit::<libc::rlimit>::uninit();
        // SAFETY: getrlimit initializes one valid rlimit on success.
        if unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, limit.as_mut_ptr()) } == 0 {
            // SAFETY: successful getrlimit initialized the value.
            let soft = unsafe { limit.assume_init() }.rlim_cur;
            return proposed.min(
                usize::try_from(soft)
                    .unwrap_or(usize::MAX)
                    .saturating_sub(8),
            );
        }
    }
    proposed.min(32)
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
    fn many_runs_sort_with_small_budget_and_correct_final_pass() {
        let parent = tempfile::tempdir().unwrap();
        let mut sorter = Sorter::new(parent.path(), 32768, 1).unwrap();
        for i in (0..4096u32).rev() {
            sorter
                .push(i.to_be_bytes().to_vec(), i.to_le_bytes().to_vec())
                .unwrap();
        }
        let sorted = sorter.finish().unwrap();
        assert!(sorted.stats().runs > 3);
        assert!(sorted.stats().passes > 0);
        assert!(sorted.stats().bytes_written >= 4096 * (HEADER as u64 + 8));
        assert!(sorted.stats().bytes_read >= 4096 * (HEADER as u64 + 8));
        assert_eq!(sorted.stats().workers, 1);
        let mut cursor = sorted.cursor().unwrap();
        drop(sorted);
        for i in 0..4096u32 {
            let row = cursor.next().unwrap().unwrap();
            assert_eq!(row.key, i.to_be_bytes());
            assert_eq!(row.value, i.to_le_bytes());
        }
        assert!(cursor.next().unwrap().is_none());
        drop(cursor);
        assert_eq!(std::fs::read_dir(parent.path()).unwrap().count(), 0);
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
        match RunReader::open(&path, MAX_FRAME) {
            Err(e) => assert!(e.message.contains("truncated")),
            Ok(_) => panic!("truncated frame accepted"),
        }
    }

    #[test]
    fn parallel_batches_are_deterministic_and_report_workers() {
        let parent = tempfile::tempdir().unwrap();
        let mut expected = None;
        for jobs in [1, 2, 4] {
            let mut sorter = Sorter::new(parent.path(), 20 << 20, jobs).unwrap();
            assert_eq!(sorter.requested, jobs);
            for i in (0..4096u32).rev() {
                sorter
                    .push((i % 17).to_be_bytes().to_vec(), i.to_le_bytes().to_vec())
                    .unwrap();
                if i % 128 == 0 {
                    sorter.flush().unwrap();
                }
            }
            let sorted = sorter.finish().unwrap();
            assert!(sorted.stats().runs > 3);
            // Actual overlap depends on scheduling and producer speed. The
            // metric reports observed concurrency, not the requested limit.
            assert!((1..=jobs).contains(&sorted.stats().workers));
            let mut cursor = sorted.cursor().unwrap();
            let mut rows = Vec::new();
            while let Some(row) = cursor.next().unwrap() {
                rows.push((row.key, row.value));
            }
            if let Some(ref prior) = expected {
                assert_eq!(&rows, prior);
            }
            expected = Some(rows);
        }
        assert_eq!(fs::read_dir(parent.path()).unwrap().count(), 0);
    }

    #[test]
    fn small_budget_jobs_use_identical_serial_batches() {
        let parent = tempfile::tempdir().unwrap();
        let mut expected = None;
        for jobs in [1, 4] {
            let mut sorter = Sorter::new(parent.path(), 32768, jobs).unwrap();
            assert_eq!(sorter.requested, 1);
            assert_eq!(sorter.batch_budget, sorter.budget);
            for i in (0..4096u32).rev() {
                sorter
                    .push((i % 17).to_be_bytes().to_vec(), i.to_le_bytes().to_vec())
                    .unwrap();
            }
            let sorted = sorter.finish().unwrap();
            assert_eq!(sorted.stats().workers, 1);
            assert!(sorted.stats().runs > 3);
            let mut cursor = sorted.cursor().unwrap();
            let mut rows = Vec::new();
            while let Some(row) = cursor.next().unwrap() {
                rows.push((row.key, row.value));
            }
            let result = (sorted.stats().runs, rows);
            if let Some(ref prior) = expected {
                assert_eq!(&result, prior);
            }
            expected = Some(result);
        }
        assert_eq!(fs::read_dir(parent.path()).unwrap().count(), 0);
    }

    #[test]
    fn interleaved_equal_keys_keep_global_ordinals_across_many_runs() {
        let parent = tempfile::tempdir().unwrap();
        let mut sorter = Sorter::new(parent.path(), 32768, 4).unwrap();
        for i in 0..384u32 {
            sorter
                .push(vec![(i % 3) as u8], i.to_le_bytes().to_vec())
                .unwrap();
            if i % 4 == 3 {
                sorter.flush().unwrap();
            }
        }
        let sorted = sorter.finish().unwrap();
        assert_eq!(sorted.stats().runs, 96);
        assert!(sorted.stats().passes > 1);
        let mut cursor = sorted.cursor().unwrap();
        for key in 0..3u8 {
            for i in (0..384u32).filter(|i| i % 3 == u32::from(key)) {
                let row = cursor.next().unwrap().unwrap();
                assert_eq!(row.key, [key]);
                assert_eq!(row.value, i.to_le_bytes());
            }
        }
        assert!(cursor.next().unwrap().is_none());
    }

    #[test]
    fn empty_internal_run_is_fatal_and_cleans_up() {
        let parent = tempfile::tempdir().unwrap();
        let mut sorter = Sorter::new(parent.path(), 32768, 1).unwrap();
        sorter.push(b"a".to_vec(), vec![]).unwrap();
        sorter.flush().unwrap();
        File::create(run_path(sorter.dir.path(), 0, 0)).unwrap();
        let sorted = sorter.finish().unwrap();
        match sorted.cursor() {
            Err(e) => {
                assert_eq!(e.code, 3);
                assert!(e.message.contains("empty or truncated"));
            }
            Ok(_) => panic!("empty internal run accepted"),
        }
        drop(sorted);
        assert_eq!(fs::read_dir(parent.path()).unwrap().count(), 0);
        let empty = Sorter::new(parent.path(), 32768, 4)
            .unwrap()
            .finish()
            .unwrap();
        assert!(empty.cursor().unwrap().next().unwrap().is_none());
        drop(empty);

        // A missing head in a merge must also fail rather than lose that run.
        let mut sorter = Sorter::new(parent.path(), 32768, 1).unwrap();
        for key in [b"a", b"b"] {
            sorter.push(key.to_vec(), vec![]).unwrap();
            sorter.flush().unwrap();
        }
        File::create(run_path(sorter.dir.path(), 0, 0)).unwrap();
        match sorter.finish() {
            Err(e) => assert_eq!(e.code, 3),
            Ok(_) => panic!("merge accepted empty internal run"),
        }
        assert_eq!(fs::read_dir(parent.path()).unwrap().count(), 0);
    }

    #[test]
    fn worker_write_error_joins_and_cleans_tempdir() {
        let parent = tempfile::tempdir().unwrap();
        let mut sorter = Sorter::new(parent.path(), 12 << 20, 2).unwrap();
        sorter.push(b"a".to_vec(), b"b".to_vec()).unwrap();
        fs::create_dir(run_path(sorter.dir.path(), 0, 0)).unwrap();
        sorter.flush().unwrap();
        assert!(sorter.finish().is_err());
        assert_eq!(fs::read_dir(parent.path()).unwrap().count(), 0);
    }

    /// Deterministic work/IO probe, deliberately excluded from routine tests.
    /// Final cursor reads belong to the consumer, not sort merge IO counters.
    #[test]
    #[ignore]
    fn metadata_single_source_io_probe() {
        let parent = tempfile::tempdir().unwrap();
        let rows = 100_000u64;
        let budget = 1 << 20;
        for jobs in [1, 4] {
            let mut sorter = Sorter::new(parent.path(), budget, jobs).unwrap();
            for line in (0..rows).rev() {
                let mut key = b"single-source.c\0".to_vec();
                key.extend_from_slice(&line.to_be_bytes());
                let mut value = vec![b'x'; 256];
                value[..8].copy_from_slice(&line.to_be_bytes());
                sorter.push(key, value).unwrap();
            }
            let sorted = sorter.finish().unwrap();
            let stats = sorted.stats();
            assert!(stats.runs > 1 && stats.passes > 0 && stats.fanin >= 2);
            assert!(stats.bytes_written > 24 << 20 && stats.bytes_read > 0);
            println!(
                concat!(
                    "{{\"probe\":\"metadata-single-source\",\"rows\":{},",
                    "\"value_bytes\":256,\"input_order\":\"descending\",",
                    "\"budget_bytes\":{},\"jobs\":{},\"runs\":{},\"passes\":{},",
                    "\"merge_bytes_read\":{},\"bytes_written\":{},\"fanin\":{},\"workers\":{}}}"
                ),
                rows,
                budget,
                jobs,
                stats.runs,
                stats.passes,
                stats.bytes_read,
                stats.bytes_written,
                stats.fanin,
                stats.workers
            );
            let mut cursor = sorted.cursor().unwrap();
            for line in 0..rows {
                let row = cursor.next().unwrap().unwrap();
                assert_eq!(&row.key[16..], &line.to_be_bytes());
                assert_eq!(&row.value[..8], &line.to_be_bytes());
                assert!(row.value[8..].iter().all(|b| *b == b'x'));
            }
            assert!(cursor.next().unwrap().is_none());
            drop(cursor);
            drop(sorted);
            assert_eq!(fs::read_dir(parent.path()).unwrap().count(), 0);
        }
    }
}
