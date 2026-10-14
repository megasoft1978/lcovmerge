//! Bounded, source-wide reduction of VER records.
//!
//! Output keys use UTF-8 source bytes followed by a NUL terminator. Values are the selected UTF-8 version. Decode with [`decode_source`].
use super::{policy, sort};
use crate::common::Error;
use std::path::Path;

const PRODUCER: usize = 24;

/// Decode the source portion of a row key produced by [`Reducer::finish`].
pub(super) fn decode_source(key: &[u8]) -> Result<&str, Error> {
    let Some(source) = key.strip_suffix(&[0]) else {
        return Err(Error::new(3, "invalid version reducer source key"));
    };
    if source.contains(&0) {
        return Err(Error::new(3, "invalid version reducer source key"));
    }
    std::str::from_utf8(source)
        .map_err(|_| Error::new(3, "version reducer source key is not UTF-8"))
}

/// Collect versions using external sorting, retaining no source-indexed map.
pub(super) struct Reducer {
    sorter: sort::Sorter,
    budget: usize,
    jobs: usize,
}

impl Reducer {
    pub(super) fn new(tmpdir: &Path, budget: usize, jobs: usize) -> Result<Self, Error> {
        Ok(Self {
            sorter: sort::Sorter::new(tmpdir, budget, jobs)?,
            budget,
            jobs,
        })
    }

    /// Producer coordinates are compared lexicographically as `(input, section, ordinal)`.
    #[allow(clippy::too_many_arguments)]
    pub(super) fn push(
        &mut self,
        source: &str,
        input: u64,
        section: u64,
        ordinal: u64,
        version: &str,
        label: &str,
        line_no: u64,
    ) -> Result<(), Error> {
        let source = source.as_bytes();
        if source.contains(&0) {
            return Err(super::policy::at(
                super::policy::Kind::Format,
                label,
                line_no,
                "NUL in version source path",
            ));
        }
        let mut key = Vec::with_capacity(source.len() + 1 + PRODUCER);
        key.extend_from_slice(source);
        key.push(0);
        for n in [input, section, ordinal] {
            key.extend_from_slice(&n.to_be_bytes());
        }
        // The value is framed internally as version length + version + label + line.
        let version = version.as_bytes();
        let label = label.as_bytes();
        let vlen = u32::try_from(version.len())
            .map_err(|_| super::policy::new(super::policy::Kind::Format, "version too long"))?;
        let llen = u32::try_from(label.len())
            .map_err(|_| super::policy::new(super::policy::Kind::Format, "input label too long"))?;
        let mut value = Vec::with_capacity(8 + version.len() + label.len() + 8);
        value.extend_from_slice(&vlen.to_be_bytes());
        value.extend_from_slice(version);
        value.extend_from_slice(&llen.to_be_bytes());
        value.extend_from_slice(label);
        value.extend_from_slice(&line_no.to_be_bytes());
        self.sorter.push(key, value)
    }

    /// Return one row per source. Conflicting later producers are skipped when
    /// the request policy allows recovery; the first producer always wins.
    pub(super) fn finish(self, req: &super::Request) -> Result<sort::Sorted, Error> {
        let sorted = self.sorter.finish()?;
        let mut cursor = sorted.cursor()?;
        let mut reduced = sort::Sorter::new(&req.resources.tmpdir, self.budget, self.jobs)?;
        let mut group: Option<(Vec<u8>, Vec<u8>)> = None;
        while let Some(row) = cursor.next()? {
            let end = row
                .key
                .iter()
                .position(|b| *b == 0)
                .ok_or_else(|| Error::new(3, "invalid version reducer row key"))?
                + 1;
            if end + PRODUCER != row.key.len() {
                return Err(Error::new(3, "invalid version reducer row key"));
            }
            let source_key = row.key[..end].to_vec();
            let (_vlen, version, label, line) = decode_value(&row.value)?;
            if let Some((prior_key, prior_version)) = group.as_ref() {
                if *prior_key != source_key {
                    reduced.push(prior_key.clone(), prior_version.clone())?;
                    group = Some((source_key, version));
                } else if prior_version != &version {
                    let e = super::policy::at(
                        super::policy::Kind::Version,
                        label,
                        line,
                        format!(
                            "conflicting source versions for {}",
                            decode_source(&source_key)?
                        ),
                    );
                    if !policy::can_recover(req, &e) {
                        return Err(e);
                    }
                }
            } else {
                group = Some((source_key, version));
            }
        }
        if let Some((key, value)) = group {
            reduced.push(key, value)?;
        }
        reduced.finish()
    }
}

fn decode_value(value: &[u8]) -> Result<(usize, Vec<u8>, &str, u64), Error> {
    if value.len() < 16 {
        return Err(Error::new(3, "invalid version reducer value"));
    }
    let n = u32::from_be_bytes(value[..4].try_into().unwrap()) as usize;
    let lend = 4usize
        .checked_add(n)
        .ok_or_else(|| Error::new(3, "version value overflow"))?;
    if lend + 12 > value.len() {
        return Err(Error::new(3, "invalid version reducer value"));
    }
    let version = value[4..lend].to_vec();
    std::str::from_utf8(&version).map_err(|_| Error::new(3, "version is not UTF-8"))?;
    let llen = u32::from_be_bytes(value[lend..lend + 4].try_into().unwrap()) as usize;
    let label_end = lend + 4 + llen;
    if label_end + 8 != value.len() {
        return Err(Error::new(3, "invalid version reducer value"));
    }
    let label = std::str::from_utf8(&value[lend + 4..label_end])
        .map_err(|_| Error::new(3, "label is not UTF-8"))?;
    let line = u64::from_be_bytes(value[label_end..].try_into().unwrap());
    Ok((n, version, label, line))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::compat::Request;

    fn collect(sorted: &sort::Sorted) -> Vec<(String, String)> {
        let mut cursor = sorted.cursor().unwrap();
        let mut rows = Vec::new();
        while let Some(row) = cursor.next().unwrap() {
            rows.push((
                decode_source(&row.key).unwrap().to_owned(),
                String::from_utf8(row.value).unwrap(),
            ));
        }
        rows
    }

    #[test]
    fn spills_many_runs_and_orders_by_producer_coordinates() {
        let dir = tempfile::tempdir().unwrap();
        let mut req = Request::default();
        req.resources.tmpdir = dir.path().to_owned();
        req.resources.mem_limit = 32 * 1024;
        req.resources.jobs = 1;
        req.keep_going = true;
        let mut r = Reducer::new(dir.path(), 32 * 1024, 1).unwrap();
        for i in (0..1200u64).rev() {
            r.push("src.c", 2, i, 0, "later", "in", i + 1).unwrap();
        }
        r.push("src.c", 0, 9, 0, "first", "first-input", 77)
            .unwrap();
        let sorted = r.finish(&req).unwrap();
        assert_eq!(collect(&sorted), [("src.c".into(), "first".into())]);
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1); // Sorted owns its run.
        drop(sorted);
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 0);
    }

    #[test]
    fn strict_conflict_errors_at_later_producer_and_recovery_keeps_first() {
        let dir = tempfile::tempdir().unwrap();
        let mut req = Request::default();
        req.resources.tmpdir = dir.path().to_owned();
        req.resources.mem_limit = 1 << 20;
        req.resources.jobs = 1;
        let mut r = Reducer::new(dir.path(), 1 << 20, 1).unwrap();
        r.push("src.c", 0, 0, 0, "v1", "first", 3).unwrap();
        r.push("src.c", 1, 0, 0, "v2", "second", 19).unwrap();
        let err = match r.finish(&req) {
            Ok(_) => panic!("strict conflict accepted"),
            Err(e) => e,
        };
        assert!(err.message.contains("second:19"));
        assert_eq!(policy::public_error(err).code, 2);

        req.keep_going = true;
        let mut r = Reducer::new(dir.path(), 1 << 20, 1).unwrap();
        r.push("src.c", 1, 0, 0, "v2", "second", 19).unwrap();
        r.push("src.c", 0, 0, 0, "v1", "first", 3).unwrap();
        let sorted = r.finish(&req).unwrap();
        assert_eq!(collect(&sorted), [("src.c".into(), "v1".into())]);
    }
}
