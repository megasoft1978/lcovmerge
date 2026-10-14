//! Exact, disk-backed input/source/testcase eligibility without group-sized maps.
use super::{policy, sort, Request};
use crate::common::{check_cancelled, Error, MAX_LINE};
use std::fs::File;
use std::io::{BufWriter, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

pub(super) struct Recorder {
    raw: sort::Sorter,
    parent: PathBuf,
    budget: usize,
}
#[derive(Clone, Copy, Default)]
pub(super) struct Status {
    pub alive: bool,
    pub multiple: bool,
}
pub(super) struct Index {
    offsets: File,
    keys: File,
    _dir: tempfile::TempDir,
    count: u64,
    last: Option<(Vec<u8>, u8)>,
    last_source: Option<(String, u64, Status)>,
}
fn corrupt() -> Error {
    Error::new(3, "corrupt eligibility index")
}
fn io(e: std::io::Error) -> Error {
    Error::new(3, format!("eligibility index I/O failed: {e}"))
}
fn source_key(source: &str, input: u64) -> Vec<u8> {
    let mut key = Vec::with_capacity(source.len() + 10);
    key.push(1);
    key.extend_from_slice(source.as_bytes());
    key.push(0);
    key.extend_from_slice(&input.to_be_bytes());
    key
}
impl Recorder {
    pub fn new(parent: &Path, budget: usize) -> Result<Self, Error> {
        Ok(Self {
            raw: sort::Sorter::new(parent, budget / 2, 1)?,
            parent: parent.to_owned(),
            budget,
        })
    }
    pub fn push(&mut self, source: &str, input: u64, tc: &str, has_da: bool) -> Result<(), Error> {
        if source.as_bytes().contains(&0) || tc.as_bytes().contains(&0) {
            return Err(policy::new(policy::Kind::Format, "NUL in LCOV identity"));
        }
        let mut key = source_key(source, input);
        key.extend_from_slice(tc.as_bytes());
        key.push(0);
        self.raw.push(key, vec![u8::from(has_da)])
    }
    pub fn finish(self, req: &Request) -> Result<Index, Error> {
        let sorted = self.raw.finish()?;
        let mut cursor = sorted.cursor()?;
        let mut reduced = sort::Sorter::new(&self.parent, self.budget / 2, 1)?;
        let mut next = cursor.next()?;
        let mut group: Option<(Vec<u8>, bool, u64)> = None;
        while let Some(first) = next.take() {
            check_cancelled()?;
            if first.key.len() < 11 || first.key[0] != 1 || first.key.last() != Some(&0) {
                return Err(corrupt());
            }
            let end = first.key[1..]
                .iter()
                .position(|b| *b == 0)
                .ok_or_else(corrupt)?
                + 2
                + 8;
            if end >= first.key.len() {
                return Err(corrupt());
            }
            let scope = first.key[..end].to_vec();
            if group.as_ref().is_some_and(|(key, _, _)| key != &scope) {
                flush_scope(&mut reduced, group.take().unwrap())?;
            }
            let mut alive = flag(&first.value)?;
            next = cursor.next()?;
            while next.as_ref().is_some_and(|row| row.key == first.key) {
                alive |= flag(&next.take().unwrap().value)?;
                next = cursor.next()?;
            }
            let g = group.get_or_insert((scope.clone(), false, 0));
            g.1 |= alive;
            g.2 = g.2.saturating_add(1);
            let mut key = scope;
            key.push(1);
            key.extend_from_slice(&first.key[end..]);
            reduced.push(key, vec![u8::from(alive)])?;
        }
        if let Some(group) = group {
            flush_scope(&mut reduced, group)?;
        }
        drop(cursor);
        drop(sorted);
        let sorted = reduced.finish()?;
        let dir = tempfile::Builder::new()
            .prefix("lcov-eligibility-")
            .tempdir_in(&self.parent)
            .map_err(io)?;
        let mut offsets = BufWriter::new(File::create(dir.path().join("offsets")).map_err(io)?);
        let mut keys = BufWriter::new(File::create(dir.path().join("keys")).map_err(io)?);
        let mut position = 0u64;
        let mut count = 0u64;
        let mut cursor = sorted.cursor()?;
        let mut previous: Option<Vec<u8>> = None;
        while let Some(row) = cursor.next()? {
            if previous.as_ref() == Some(&row.key) {
                continue;
            }
            if row.key.len() > 2 * MAX_LINE + 32 || row.value.len() != 1 {
                return Err(corrupt());
            }
            offsets.write_all(&position.to_be_bytes()).map_err(io)?;
            keys.write_all(&(row.key.len() as u32).to_be_bytes())
                .map_err(io)?;
            keys.write_all(&row.value).map_err(io)?;
            keys.write_all(&row.key).map_err(io)?;
            position = position
                .checked_add(row.key.len() as u64 + 5)
                .ok_or_else(corrupt)?;
            count += 1;
            previous = Some(row.key);
        }
        offsets.flush().map_err(io)?;
        keys.flush().map_err(io)?;
        drop(offsets);
        drop(keys);
        let mut index = Index {
            offsets: File::open(dir.path().join("offsets")).map_err(io)?,
            keys: File::open(dir.path().join("keys")).map_err(io)?,
            _dir: dir,
            count,
            last: None,
            last_source: None,
        };
        for (input, label) in req.resources.inputs.iter().enumerate() {
            let mut key = vec![0];
            key.extend_from_slice(&(input as u64).to_be_bytes());
            if index.get(key)? == 0 {
                let error = policy::at(
                    policy::Kind::Empty,
                    label,
                    0,
                    "empty LCOV input: no valid records",
                );
                if !policy::can_recover(req, &error) {
                    return Err(error);
                }
            }
        }
        Ok(index)
    }
}
fn flag(bytes: &[u8]) -> Result<bool, Error> {
    match bytes {
        [0] => Ok(false),
        [1] => Ok(true),
        _ => Err(corrupt()),
    }
}
fn flush_scope(
    out: &mut sort::Sorter,
    (mut key, alive, testcases): (Vec<u8>, bool, u64),
) -> Result<(), Error> {
    if alive {
        let mut input = vec![0];
        input.extend_from_slice(&key[key.len() - 8..]);
        out.push(input, vec![1])?;
    }
    key.push(0);
    out.push(key, vec![u8::from(alive) | (u8::from(testcases > 1) << 1)])
}
impl Index {
    fn get(&mut self, key: Vec<u8>) -> Result<u8, Error> {
        if let Some((old, value)) = &self.last {
            if *old == key {
                return Ok(*value);
            }
        }
        let (mut lo, mut hi) = (0, self.count);
        let mut value = 0;
        while lo < hi {
            check_cancelled()?;
            let mid = lo + (hi - lo) / 2;
            self.offsets.seek(SeekFrom::Start(mid * 8)).map_err(io)?;
            let mut offset = [0; 8];
            self.offsets.read_exact(&mut offset).map_err(io)?;
            self.keys
                .seek(SeekFrom::Start(u64::from_be_bytes(offset)))
                .map_err(io)?;
            let mut header = [0; 5];
            self.keys.read_exact(&mut header).map_err(io)?;
            let len = u32::from_be_bytes(header[..4].try_into().unwrap()) as usize;
            if len > 2 * MAX_LINE + 32 {
                return Err(corrupt());
            }
            let mut found = vec![0; len];
            self.keys.read_exact(&mut found).map_err(io)?;
            match found.cmp(&key) {
                std::cmp::Ordering::Less => lo = mid + 1,
                std::cmp::Ordering::Greater => hi = mid,
                std::cmp::Ordering::Equal => {
                    value = header[4];
                    break;
                }
            }
        }
        self.last = Some((key, value));
        Ok(value)
    }
    pub fn source(&mut self, source: &str, input: u64) -> Result<Status, Error> {
        if let Some((old, id, status)) = &self.last_source {
            if old == source && *id == input {
                return Ok(*status);
            }
        }
        let mut key = source_key(source, input);
        key.push(0);
        let flags = self.get(key)?;
        let status = Status {
            alive: flags & 1 != 0,
            multiple: flags & 2 != 0,
        };
        self.last_source = Some((source.to_owned(), input, status));
        Ok(status)
    }
    pub fn testcase(&mut self, source: &str, input: u64, tc: &str) -> Result<bool, Error> {
        let mut key = source_key(source, input);
        key.push(1);
        key.extend_from_slice(tc.as_bytes());
        key.push(0);
        Ok(self.get(key)? & 1 != 0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn corrupt_internal_markers_are_resource_errors_and_remove_spills() {
        let dir = tempfile::tempdir().unwrap();
        let mut key = source_key("unit.c", 0);
        key.push(0);
        for (key, value) in [(Vec::new(), vec![1]), (key, vec![2])] {
            let mut recorder = Recorder::new(dir.path(), 64 * 1024).unwrap();
            recorder.raw.push(key, value).unwrap();
            let error = match recorder.finish(&Request::default()) {
                Ok(_) => panic!("corrupt marker accepted"),
                Err(error) => error,
            };
            assert_eq!(error.code, 3);
            assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 0);
        }
    }

    #[test]
    fn many_spilled_testcases_preserve_empty_maps_and_input_boundaries() {
        let dir = tempfile::tempdir().unwrap();
        let mut req = Request::default();
        req.resources.inputs = vec!["input-zero".into(), "input-one".into()];
        let mut recorder = Recorder::new(dir.path(), 64 * 1024).unwrap();
        for n in (0..4096).rev() {
            let tc = format!("case_{n:05}");
            recorder.push("unit.c", 0, &tc, false).unwrap();
            if n % 2 == 0 {
                recorder.push("unit.c", 0, &tc, true).unwrap();
            }
        }
        recorder.push("unit.c", 1, "ghost", false).unwrap();
        recorder.push("other.c", 1, "keep", true).unwrap();
        let mut index = recorder.finish(&req).unwrap();
        assert!(index.source("unit.c", 0).unwrap().multiple);
        assert!(index.source("unit.c", 0).unwrap().alive);
        assert!(!index.source("unit.c", 1).unwrap().alive);
        assert!(index.source("other.c", 1).unwrap().alive);
        for n in 0..4096 {
            assert_eq!(
                index
                    .testcase("unit.c", 0, &format!("case_{n:05}"))
                    .unwrap(),
                n % 2 == 0
            );
        }
        assert!(!index.testcase("unit.c", 0, "missing").unwrap());
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
        drop(index);
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 0);
    }

    #[test]
    fn empty_input_is_not_rescued_by_a_later_input_and_failure_cleans_up() {
        let dir = tempfile::tempdir().unwrap();
        let mut req = Request::default();
        req.resources.inputs = vec!["empty-first".into(), "valid-second".into()];
        let mut recorder = Recorder::new(dir.path(), 64 * 1024).unwrap();
        recorder.push("unit.c", 0, "ghost", false).unwrap();
        recorder.push("unit.c", 1, "keep", true).unwrap();
        let error = match recorder.finish(&req) {
            Ok(_) => panic!("empty input accepted"),
            Err(error) => error,
        };
        assert!(error.message.contains("empty-first"));
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 0);
    }
}
