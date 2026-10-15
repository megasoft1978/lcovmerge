use crate::common::{check_cancelled, Error, MAX_LINE};
use std::fs::{File, Metadata};
use std::io::{self, BufRead, BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

const READER_BUF: usize = 64 * 1024;

#[derive(Clone, Debug, Eq, PartialEq)]
struct Identity {
    len: u64,
    modified: Option<std::time::SystemTime>,
    #[cfg(unix)]
    inode: u64,
}

impl Identity {
    fn of(m: &Metadata) -> Self {
        Self {
            len: m.len(),
            modified: m.modified().ok(),
            #[cfg(unix)]
            inode: {
                use std::os::unix::fs::MetadataExt;
                m.ino()
            },
        }
    }
}

/// A one-source, disk-backed line checksum index.
pub(super) struct SourceIndex {
    tmpdir: PathBuf,
    working_limit: usize,
    cached: Option<Cached>,
}

struct Cached {
    path: PathBuf,
    identity: Identity,
    index: tempfile::NamedTempFile,
    lines: u64,
}

impl SourceIndex {
    pub(super) fn new(tmpdir: &Path, working_limit: usize) -> Result<Self, Error> {
        Ok(Self {
            tmpdir: tmpdir.to_path_buf(),
            working_limit,
            cached: None,
        })
    }

    pub(super) fn checksum(&mut self, source: &Path, line: u64) -> Result<Option<[u8; 16]>, Error> {
        check_cancelled()?;
        if self.cached.as_ref().is_some_and(|c| c.path != source) {
            self.cached = None;
        }
        let meta = std::fs::metadata(source).map_err(|e| source_error(source, e))?;
        if !meta.is_file() {
            return Err(super::policy::new(
                super::policy::Kind::Source,
                format!(
                    "cannot read source {}: not a regular file",
                    source.display()
                ),
            ));
        }
        let identity = Identity::of(&meta);
        let hit = self
            .cached
            .as_ref()
            .is_some_and(|c| c.path == source && c.identity == identity);
        if !hit {
            self.cached = None;
            self.build(source, identity)?;
        }
        check_cancelled()?;
        let c = self.cached.as_mut().expect("index built");
        if line == 0 || line > c.lines {
            return Ok(None);
        }
        let offset = (line - 1)
            .checked_mul(16)
            .ok_or_else(|| Error::new(3, "source index offset overflow"))?;
        c.index
            .as_file_mut()
            .seek(SeekFrom::Start(offset))
            .map_err(|e| index_error("seek", e))?;
        let mut digest = [0u8; 16];
        read_exact_cancel(c.index.as_file_mut(), &mut digest)?;
        Ok(Some(digest))
    }

    fn build(&mut self, source: &Path, before: Identity) -> Result<(), Error> {
        let file = File::open(source).map_err(|e| source_error(source, e))?;
        let mut reader = BufReader::with_capacity(READER_BUF, file);
        let path_bytes = source.as_os_str().as_encoded_bytes().len();
        let fixed = READER_BUF
            .saturating_add(path_bytes)
            .saturating_add(16 + 256);
        if fixed.saturating_add(1) > self.working_limit {
            return Err(Error::new(
                3,
                format!(
                    "source checksum working limit {} bytes is too small (at least {} required)",
                    self.working_limit,
                    fixed.saturating_add(1)
                ),
            ));
        }
        let mut line = Vec::with_capacity(1);
        let index = tempfile::NamedTempFile::new_in(&self.tmpdir)
            .map_err(|e| index_error("create temporary source index", e))?;
        let mut writer = BufWriter::new(
            index
                .reopen()
                .map_err(|e| index_error("open temporary source index", e))?,
        );
        let writer_cost = 8 * 1024;
        let mut count = 0u64;
        loop {
            check_cancelled()?;
            line.clear();
            let maxcap = self.working_limit.saturating_sub(fixed + writer_cost);
            if line.capacity() > maxcap {
                return Err(limit_error(self.working_limit));
            }
            let n = read_bounded_line(
                &mut reader,
                &mut line,
                self.working_limit.saturating_sub(fixed + writer_cost),
            )?;
            if n == 0 {
                break;
            }
            if line.len() > MAX_LINE + 1 {
                return Err(super::policy::new(
                    super::policy::Kind::Format,
                    format!(
                        "source {} line {} exceeds 1 MiB",
                        source.display(),
                        count + 1
                    ),
                ));
            }
            if line.last() == Some(&b'\n') {
                line.pop();
            }
            if line.last() == Some(&b'\r') {
                line.pop();
            }
            if line.len() > MAX_LINE {
                return Err(super::policy::new(
                    super::policy::Kind::Format,
                    format!(
                        "source {} line {} exceeds 1 MiB",
                        source.display(),
                        count + 1
                    ),
                ));
            }
            if fixed
                .saturating_add(writer_cost)
                .saturating_add(line.capacity())
                > self.working_limit
            {
                return Err(limit_error(self.working_limit));
            }
            let digest = md5::compute(&line).0;
            writer
                .write_all(&digest)
                .map_err(|e| index_error("write temporary source index", e))?;
            count += 1;
        }
        writer
            .flush()
            .map_err(|e| index_error("flush temporary source index", e))?;
        let after = std::fs::metadata(source).map_err(|e| source_error(source, e))?;
        if before != Identity::of(&after) {
            return Err(source_error(
                source,
                io::Error::other("source changed while building checksum index"),
            ));
        }
        self.cached = Some(Cached {
            path: source.to_path_buf(),
            identity: before,
            index,
            lines: count,
        });
        Ok(())
    }
}

fn read_bounded_line<R: BufRead>(
    reader: &mut R,
    buf: &mut Vec<u8>,
    budget: usize,
) -> Result<usize, Error> {
    let start = buf.len();
    loop {
        check_cancelled()?;
        let available = match reader.fill_buf() {
            Err(e) if e.kind() == io::ErrorKind::Interrupted => {
                check_cancelled()?;
                continue;
            }
            Err(e) => {
                return Err(super::policy::new(
                    super::policy::Kind::Source,
                    format!("source read failed: {e}"),
                ))
            }
            Ok(v) => v,
        };
        if available.is_empty() {
            break;
        }
        let take = available
            .iter()
            .position(|b| *b == b'\n')
            .map_or(available.len(), |i| i + 1);
        let next_len = buf.len().saturating_add(take);
        if next_len > MAX_LINE + 2 {
            return Err(super::policy::new(
                super::policy::Kind::Format,
                "source line exceeds 1 MiB",
            ));
        }
        if next_len > budget {
            return Err(limit_error(budget));
        }
        if next_len > buf.capacity() {
            let needed = next_len - buf.len();
            buf.try_reserve_exact(needed)
                .map_err(|_| limit_error(budget))?;
            if buf.capacity() > budget {
                return Err(limit_error(budget));
            }
        }
        buf.extend_from_slice(&available[..take]);
        let ended = available[take - 1] == b'\n';
        reader.consume(take);
        if ended {
            break;
        }
    }
    Ok(buf.len() - start)
}

fn read_exact_cancel(file: &mut File, buf: &mut [u8]) -> Result<(), Error> {
    loop {
        check_cancelled()?;
        match file.read_exact(buf) {
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(index_error("read temporary source index", e)),
            Ok(()) => return Ok(()),
        }
    }
}

fn source_error(path: &Path, e: io::Error) -> Error {
    super::policy::new(
        super::policy::Kind::Source,
        format!("cannot read source {}: {e}", path.display()),
    )
}
fn index_error(op: &str, e: io::Error) -> Error {
    Error::new(3, format!("{op} failed: {e}"))
}
fn limit_error(limit: usize) -> Error {
    Error::new(
        3,
        format!("source checksum working limit {limit} bytes exceeded"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn budget(path: &Path) -> usize {
        64 * 1024 + path.as_os_str().as_encoded_bytes().len() + 16 + 256 + 8192 + MAX_LINE + 2
    }

    #[test]
    fn literal_digest_crlf_no_final_newline_and_random_access() {
        let d = tempfile::tempdir().unwrap();
        let src = d.path().join("src");
        std::fs::write(&src, b"hello\r\nworld\nlast").unwrap();
        let mut ix = SourceIndex::new(d.path(), budget(&src)).unwrap();
        assert_eq!(
            ix.checksum(&src, 1).unwrap(),
            Some(md5::compute(b"hello").0)
        );
        assert_eq!(ix.checksum(&src, 3).unwrap(), Some(md5::compute(b"last").0));
        assert_eq!(
            ix.checksum(&src, 2).unwrap(),
            Some(md5::compute(b"world").0)
        );
        assert_eq!(ix.checksum(&src, 4).unwrap(), None);
    }

    #[test]
    fn invalidates_changed_source_and_path_and_cleans_index() {
        let d = tempfile::tempdir().unwrap();
        let a = d.path().join("a");
        let b = d.path().join("b");
        std::fs::write(&a, b"one").unwrap();
        std::fs::write(&b, b"two").unwrap();
        let mut ix = SourceIndex::new(d.path(), budget(&a)).unwrap();
        assert_eq!(ix.checksum(&a, 1).unwrap(), Some(md5::compute(b"one").0));
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 3);
        assert_eq!(ix.checksum(&b, 1).unwrap(), Some(md5::compute(b"two").0));
        std::fs::write(&b, b"new content").unwrap();
        assert_eq!(
            ix.checksum(&b, 1).unwrap(),
            Some(md5::compute(b"new content").0)
        );
        drop(ix);
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 2);
    }

    #[test]
    fn errors_are_classified_and_temporary_file_is_cleaned() {
        let d = tempfile::tempdir().unwrap();
        let src = d.path().join("src");
        std::fs::write(&src, b"x").unwrap();
        let mut ix = SourceIndex::new(d.path(), 16).unwrap();
        assert_eq!(ix.checksum(&src, 1).unwrap_err().code, 3);
        let mut ix = SourceIndex::new(d.path(), budget(&src)).unwrap();
        assert_eq!(
            super::super::policy::public_error(
                ix.checksum(&d.path().join("missing"), 1).unwrap_err()
            )
            .code,
            2
        );
        std::fs::write(&src, vec![b'x'; MAX_LINE + 1]).unwrap();
        assert_eq!(
            super::super::policy::public_error(ix.checksum(&src, 1).unwrap_err()).code,
            2
        );
        std::fs::remove_file(&src).unwrap();
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 0);
    }
}
