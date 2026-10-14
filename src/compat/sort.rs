//! Bounded byte-key sorting shared by compatibility metadata passes.
use crate::common::Error;
use std::path::Path;
pub(super) struct Row { pub key: Vec<u8>, pub value: Vec<u8> }
pub(super) struct Stats { pub runs: u64, pub passes: u64, pub bytes_read: u64, pub bytes_written: u64, pub workers: usize }
pub(super) struct Sorter;
pub(super) struct Sorted;
pub(super) struct Cursor;
impl Sorter {
    pub(super) fn new(_tmpdir: &Path, _budget: usize, _jobs: usize) -> Result<Self, Error> { Err(Error::new(3, "metadata sorter implementation pending")) }
    pub(super) fn push(&mut self, _key: Vec<u8>, _value: Vec<u8>) -> Result<(), Error> { Err(Error::new(3, "metadata sorter implementation pending")) }
    pub(super) fn finish(self) -> Result<Sorted, Error> { Err(Error::new(3, "metadata sorter implementation pending")) }
}
impl Sorted {
    pub(super) fn cursor(&self) -> Result<Cursor, Error> { Err(Error::new(3, "metadata sorter implementation pending")) }
    pub(super) fn stats(&self) -> &Stats { unimplemented!() }
}
impl Cursor { pub(super) fn next(&mut self) -> Result<Option<Row>, Error> { Err(Error::new(3, "metadata sorter implementation pending")) } }
