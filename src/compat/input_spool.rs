//! Bounded disk staging for a non-seekable trace input.
use crate::common::{check_cancelled, Error};
use std::io::{BufWriter, Read, Write};
use std::path::Path;

pub(super) fn stage(
    mut reader: impl Read,
    parent: &Path,
) -> Result<tempfile::NamedTempFile, Error> {
    let mut file = tempfile::Builder::new()
        .prefix("lcov-compat-stdin-")
        .suffix(".info")
        .tempfile_in(parent)
        .map_err(|e| Error::new(3, format!("cannot stage stdin: {e}")))?;
    {
        let mut writer = BufWriter::with_capacity(8192, file.as_file_mut());
        let mut buffer = [0u8; 8192];
        loop {
            check_cancelled()?;
            let n = match reader.read(&mut buffer) {
                Ok(n) => n,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(Error::new(3, format!("stdin read failed: {e}"))),
            };
            if n == 0 {
                break;
            }
            writer
                .write_all(&buffer[..n])
                .map_err(|e| Error::new(3, format!("stdin staging write failed: {e}")))?;
        }
        writer
            .flush()
            .map_err(|e| Error::new(3, format!("stdin staging write failed: {e}")))?;
    }
    check_cancelled()?;
    Ok(file)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::io;

    struct Synthetic {
        remaining: usize,
        fail: bool,
    }
    impl Read for Synthetic {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            if self.remaining == 0 {
                return if self.fail {
                    Err(io::Error::other("synthetic failure"))
                } else {
                    Ok(0)
                };
            }
            let n = self.remaining.min(buffer.len());
            buffer[..n].fill(b'x');
            self.remaining -= n;
            Ok(n)
        }
    }

    #[test]
    fn stages_large_stream_and_removes_file_on_drop() {
        let directory = tempfile::tempdir().unwrap();
        let n = 3 * 1024 * 1024 + 17;
        let file = stage(
            Synthetic {
                remaining: n,
                fail: false,
            },
            directory.path(),
        )
        .unwrap();
        assert_eq!(file.as_file().metadata().unwrap().len(), n as u64);
        let mut reader = fs::File::open(file.path()).unwrap();
        let mut buffer = [0u8; 4096];
        let mut count = 0;
        loop {
            let read = reader.read(&mut buffer).unwrap();
            if read == 0 {
                break;
            }
            assert!(buffer[..read].iter().all(|b| *b == b'x'));
            count += read;
        }
        assert_eq!(count, n);
        drop(reader);
        drop(file);
        assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 0);
    }

    #[test]
    fn removes_partial_file_after_read_failure() {
        let directory = tempfile::tempdir().unwrap();
        let error = stage(
            Synthetic {
                remaining: 8193,
                fail: true,
            },
            directory.path(),
        )
        .unwrap_err();
        assert_eq!(error.code, 3);
        assert!(error.message.contains("synthetic failure"));
        assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 0);
    }
}
