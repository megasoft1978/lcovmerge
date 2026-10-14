//! Disk-backed exact ranking of streamed branch signatures.
use crate::common::{check_cancelled, Error};
use std::cmp::Ordering;
use std::fs::{self, File, OpenOptions};
use std::io::{BufWriter, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;

const IO: usize = 1024;
const WRITE_BUF: usize = 1024;
const DESC: usize = 32;
const MIN_BUDGET: usize = DESC * 2 + IO * 2 + WRITE_BUF;

#[derive(Clone, Copy, Default)]
struct D {
    off: u64,
    len: u64,
    id: u64,
    ordinal: u64,
}
fn err(e: impl std::fmt::Display) -> Error {
    Error::new(3, format!("signature store failed: {e}"))
}
fn cancelled() -> Result<(), Error> {
    check_cancelled()
}
fn path(dir: &Path, p: u64, n: u64) -> PathBuf {
    dir.join(format!("r-{p}-{n}"))
}
fn encode(d: D) -> [u8; DESC] {
    let mut b = [0; DESC];
    b[..8].copy_from_slice(&d.off.to_le_bytes());
    b[8..16].copy_from_slice(&d.len.to_le_bytes());
    b[16..24].copy_from_slice(&d.id.to_le_bytes());
    b[24..].copy_from_slice(&d.ordinal.to_le_bytes());
    b
}
fn decode(b: [u8; DESC]) -> D {
    D {
        off: u64::from_le_bytes(b[..8].try_into().unwrap()),
        len: u64::from_le_bytes(b[8..16].try_into().unwrap()),
        id: u64::from_le_bytes(b[16..24].try_into().unwrap()),
        ordinal: u64::from_le_bytes(b[24..].try_into().unwrap()),
    }
}
fn read_d(f: &mut File) -> Result<Option<D>, Error> {
    cancelled()?;
    let mut b = [0; DESC];
    let mut n = 0;
    while n < DESC {
        match f.read(&mut b[n..]) {
            Ok(0) if n == 0 => return Ok(None),
            Ok(0) => return Err(err("truncated descriptor")),
            Ok(k) => n += k,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(err(e)),
        }
    }
    Ok(Some(decode(b)))
}
fn write_d(f: &mut File, d: D) -> Result<(), Error> {
    cancelled()?;
    f.write_all(&encode(d)).map_err(err)
}
fn cmp_sig(file: &mut File, a: D, b: D, x: &mut [u8], y: &mut [u8]) -> Result<Ordering, Error> {
    let n = a.len.min(b.len);
    let mut at = 0u64;
    while at < n {
        cancelled()?;
        let k = (n - at).min(x.len() as u64) as usize;
        file.seek(SeekFrom::Start(a.off + at)).map_err(err)?;
        file.read_exact(&mut x[..k])
            .map_err(|e| err(format!("truncated signature: {e}")))?;
        file.seek(SeekFrom::Start(b.off + at)).map_err(err)?;
        file.read_exact(&mut y[..k])
            .map_err(|e| err(format!("truncated signature: {e}")))?;
        match x[..k].cmp(&y[..k]) {
            Ordering::Equal => at += k as u64,
            o => return Ok(o),
        }
    }
    Ok(a.len.cmp(&b.len))
}
fn cmp_order(file: &mut File, a: D, b: D, x: &mut [u8], y: &mut [u8]) -> Result<Ordering, Error> {
    Ok(a.len
        .cmp(&b.len)
        .then(cmp_sig(file, a, b, x, y)?)
        .then(a.ordinal.cmp(&b.ordinal)))
}

pub struct Store {
    data: BufWriter<File>,
    meta: File,
    dir: Arc<tempfile::TempDir>,
    budget: usize,
    offset: u64,
    ordinal: u64,
    active: Option<(u64, u64)>,
}
pub struct Ranked {
    dir: Arc<tempfile::TempDir>,
    data: PathBuf,
    runs: u64,
    pass: u64,
    budget: usize,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rank {
    pub id: u64,
    pub rank: u64,
    pub length: u64,
}
pub struct Cursor {
    data: File,
    desc: File,
    _dir: Arc<tempfile::TempDir>,
    budget: usize,
    prev: Option<D>,
    rank: u64,
    done: bool,
}
impl Store {
    pub fn new(tmpdir: impl AsRef<Path>, budget: usize) -> Result<Self, Error> {
        if budget < MIN_BUDGET {
            return Err(err(format!("budget must be at least {MIN_BUDGET} bytes")));
        }
        let dir = Arc::new(
            tempfile::Builder::new()
                .prefix("lcov-signature-")
                .tempdir_in(tmpdir)
                .map_err(err)?,
        );
        let data = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(dir.path().join("data"))
            .map_err(err)?;
        let meta = File::create(dir.path().join("meta")).map_err(err)?;
        Ok(Self {
            data: BufWriter::with_capacity(WRITE_BUF, data),
            meta,
            dir,
            budget,
            offset: 0,
            ordinal: 0,
            active: None,
        })
    }
    pub fn begin(&mut self, id: u64) -> Result<(), Error> {
        if self.active.is_some() {
            return Err(err("begin inside signature"));
        }
        self.active = Some((id, 0));
        Ok(())
    }
    pub fn push_kind(&mut self, k: u8) -> Result<(), Error> {
        cancelled()?;
        let (id, n) = self
            .active
            .as_mut()
            .ok_or_else(|| err("push_kind outside signature"))?;
        let b = match k {
            b'v' => b'b',
            b'b' | b'e' | b'f' => k,
            _ => return Err(err("invalid signature kind")),
        };
        let next = n
            .checked_add(1)
            .ok_or_else(|| err("signature length overflow"))?;
        self.offset
            .checked_add(next)
            .ok_or_else(|| err("signature offset overflow"))?;
        self.data.write_all(&[b]).map_err(err)?;
        *n = next;
        let _ = id;
        Ok(())
    }
    pub fn end(&mut self) -> Result<(), Error> {
        cancelled()?;
        let (id, len) = self
            .active
            .take()
            .ok_or_else(|| err("end outside signature"))?;
        self.data.flush().map_err(err)?;
        write_d(
            &mut self.meta,
            D {
                off: self.offset,
                len,
                id,
                ordinal: self.ordinal,
            },
        )?;
        self.offset = self
            .offset
            .checked_add(len)
            .ok_or_else(|| err("signature offset overflow"))?;
        self.ordinal = self
            .ordinal
            .checked_add(1)
            .ok_or_else(|| err("insertion ordinal overflow"))?;
        Ok(())
    }
    pub fn finish(mut self) -> Result<Ranked, Error> {
        if self.active.is_some() {
            return Err(err("unfinished signature"));
        }
        self.data.flush().map_err(err)?;
        self.meta.flush().map_err(err)?;
        let cap = (self.budget - IO * 2 - WRITE_BUF) / (DESC * 2);
        if cap == 0 {
            return Err(err(
                "budget cannot hold descriptor chunks and comparison buffers",
            ));
        }
        let mut src = File::open(self.dir.path().join("meta")).map_err(err)?;
        let mut runs = 0u64;
        loop {
            let mut v = Vec::with_capacity(cap);
            while v.len() < cap {
                match read_d(&mut src)? {
                    Some(d) => {
                        d.off
                            .checked_add(d.len)
                            .filter(|end| *end <= self.offset)
                            .ok_or_else(|| err("invalid signature range"))?;
                        v.push(d)
                    }
                    None => break,
                }
            }
            if v.is_empty() {
                break;
            }
            let mut tmp = vec![D::default(); v.len()];
            let mut x = vec![0; IO];
            let mut y = vec![0; IO];
            merge_sort(&mut v, &mut tmp, &mut self.data, &mut x, &mut y)?;
            let mut out = File::create(path(self.dir.path(), 0, runs)).map_err(err)?;
            for d in v {
                write_d(&mut out, d)?
            }
            runs += 1;
        }
        let mut fan = ((self.budget - IO * 2 - WRITE_BUF)
            / (DESC + std::mem::size_of::<Option<D>>() + std::mem::size_of::<File>()))
        .min(32)
        .max(2);
        #[cfg(unix)]
        {
            let mut limit = std::mem::MaybeUninit::<libc::rlimit>::uninit();
            if unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, limit.as_mut_ptr()) } == 0 {
                let soft = unsafe { limit.assume_init().rlim_cur };
                fan = fan.min((soft as usize).saturating_sub(16).max(2));
            }
        }
        let mut pass = 0;
        while runs > 1 {
            cancelled()?;
            let mut outid = 0;
            let mut start = 0;
            while start < runs {
                let end = (start + fan as u64).min(runs);
                let mut readers = Vec::new();
                let mut heads = Vec::new();
                for i in start..end {
                    let mut f = File::open(path(self.dir.path(), pass, i)).map_err(err)?;
                    heads.push(read_d(&mut f)?);
                    readers.push(f)
                }
                let mut x = vec![0; IO];
                let mut y = vec![0; IO];
                let mut data = File::open(self.dir.path().join("data")).map_err(err)?;
                let mut out = File::create(path(self.dir.path(), pass + 1, outid)).map_err(err)?;
                loop {
                    cancelled()?;
                    let mut best = None;
                    for i in 0..heads.len() {
                        if let Some(d) = heads[i] {
                            best = match best {
                                None => Some(i),
                                Some(j) => {
                                    if cmp_order(&mut data, d, heads[j].unwrap(), &mut x, &mut y)?
                                        == Ordering::Less
                                    {
                                        Some(i)
                                    } else {
                                        Some(j)
                                    }
                                }
                            }
                        }
                    }
                    let Some(i) = best else { break };
                    write_d(&mut out, heads[i].unwrap())?;
                    heads[i] = read_d(&mut readers[i])?;
                }
                outid += 1;
                start = end;
            }
            for i in 0..runs {
                fs::remove_file(path(self.dir.path(), pass, i)).map_err(err)?
            }
            runs = outid;
            pass += 1;
        }
        Ok(Ranked {
            dir: self.dir.clone(),
            data: self.dir.path().join("data"),
            runs,
            pass,
            budget: self.budget,
        })
    }
}
fn merge_sort(
    v: &mut [D],
    t: &mut [D],
    f: &mut File,
    x: &mut [u8],
    y: &mut [u8],
) -> Result<(), Error> {
    let n = v.len();
    let mut w = 1;
    while w < n {
        let mut s = 0;
        while s < n {
            cancelled()?;
            let m = (s + w).min(n);
            let e = (s + 2 * w).min(n);
            let (mut i, mut j, mut k) = (s, m, s);
            while i < m && j < e {
                if cmp_order(f, v[i], v[j], x, y)? != Ordering::Greater {
                    t[k] = v[i];
                    i += 1
                } else {
                    t[k] = v[j];
                    j += 1
                }
                k += 1
            }
            while i < m {
                t[k] = v[i];
                i += 1;
                k += 1
            }
            while j < e {
                t[k] = v[j];
                j += 1;
                k += 1
            }
            s = e;
        }
        v.copy_from_slice(t);
        w = w.saturating_mul(2);
    }
    Ok(())
}
impl Ranked {
    pub fn cursor(&self) -> Result<Cursor, Error> {
        let desc = if self.runs == 0 {
            File::open(self.dir.path().join("meta")).map_err(err)?
        } else {
            File::open(path(self.dir.path(), self.pass, 0)).map_err(err)?
        };
        Ok(Cursor {
            data: File::open(&self.data).map_err(err)?,
            desc,
            _dir: self.dir.clone(),
            budget: self.budget,
            prev: None,
            rank: 0,
            done: false,
        })
    }
}
impl Cursor {
    pub fn next(&mut self) -> Result<Option<Rank>, Error> {
        if self.done {
            return Ok(None);
        }
        let Some(d) = read_d(&mut self.desc)? else {
            self.done = true;
            return Ok(None);
        };
        let size = self.data.metadata().map_err(err)?.len();
        let end = d
            .off
            .checked_add(d.len)
            .ok_or_else(|| err("signature range overflow"))?;
        if end > size {
            return Err(err("truncated signature range"));
        }
        if self.prev.is_none() || self.prev.is_some() {
            let mut left = d.len;
            self.data.seek(SeekFrom::Start(d.off)).map_err(err)?;
            let mut buf = [0; IO];
            while left > 0 {
                let n = left.min(IO as u64) as usize;
                self.data.read_exact(&mut buf[..n]).map_err(err)?;
                if buf[..n].iter().any(|b| !matches!(*b, b'b' | b'e' | b'f')) {
                    return Err(err("invalid kind in signature data"));
                }
                left -= n as u64;
            }
        }
        if let Some(p) = self.prev {
            let mut x = vec![0; IO];
            let mut y = vec![0; IO];
            if cmp_sig(&mut self.data, p, d, &mut x, &mut y)? != Ordering::Equal {
                self.rank = self
                    .rank
                    .checked_add(1)
                    .ok_or_else(|| err("rank overflow"))?
            }
        }
        self.prev = Some(d);
        let _ = self.budget;
        Ok(Some(Rank {
            id: d.id,
            rank: self.rank,
            length: d.len,
        }))
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn long_exact_rank_and_cursor_lifetime() {
        let p = tempfile::tempdir().unwrap();
        let mut s = Store::new(p.path(), 3136).unwrap();
        let a = vec![b'e'; 30000];
        for (id, bytes) in [
            (1, a.clone()),
            (2, a.clone()),
            (3, {
                let mut z = a.clone();
                z[20000] = b'f';
                z
            }),
            (4, vec![b'b'; 8]),
            (5, vec![b'f'; 2]),
        ] {
            s.begin(id).unwrap();
            for b in bytes {
                s.push_kind(b).unwrap()
            }
            s.end().unwrap()
        }
        let r = s.finish().unwrap();
        let mut c = r.cursor().unwrap();
        drop(r);
        assert!(p.path().read_dir().unwrap().next().is_some());
        let mut got = Vec::new();
        while let Some(x) = c.next().unwrap() {
            got.push(x)
        }
        assert_eq!(
            got.iter()
                .filter(|x| x.id == 1 || x.id == 2)
                .map(|x| x.rank)
                .collect::<Vec<_>>(),
            [1, 1]
        );
        assert_ne!(got.iter().find(|x| x.id == 3).unwrap().rank, 1);
        drop(c);
        assert_eq!(p.path().read_dir().unwrap().count(), 0);
    }
    #[test]
    fn normalize_and_truncated_data_is_rejected() {
        let p = tempfile::tempdir().unwrap();
        let mut s = Store::new(p.path(), 3136).unwrap();
        for (id, k) in [(1, b'v'), (2, b'b')] {
            s.begin(id).unwrap();
            s.push_kind(k).unwrap();
            s.end().unwrap()
        }
        let r = s.finish().unwrap();
        let mut c = r.cursor().unwrap();
        let f = OpenOptions::new().write(true).open(&r.data).unwrap();
        f.set_len(0).unwrap();
        c.next().unwrap().unwrap();
        assert!(c.next().is_err());
    }
    #[test]
    fn many_runs_and_mixed_lengths() {
        let p = tempfile::tempdir().unwrap();
        let mut s = Store::new(p.path(), 3136).unwrap();
        for i in (0..500u64).rev() {
            s.begin(i).unwrap();
            for b in 0..(i % 13) {
                s.push_kind(if b % 2 == 0 { b'e' } else { b'f' }).unwrap()
            }
            s.end().unwrap()
        }
        let r = s.finish().unwrap();
        let mut c = r.cursor().unwrap();
        let mut n = 0;
        while c.next().unwrap().is_some() {
            n += 1
        }
        assert_eq!(n, 500);
    }
    #[test]
    fn shorter_signature_sorts_first_and_nested_begin_fails() {
        let p = tempfile::tempdir().unwrap();
        let mut s = Store::new(p.path(), 3136).unwrap();
        s.begin(1).unwrap();
        assert!(s.begin(9).is_err());
        for b in b"fff" {
            s.push_kind(*b).unwrap();
        }
        s.end().unwrap();
        s.begin(2).unwrap();
        for b in b"bfff" {
            s.push_kind(*b).unwrap();
        }
        s.end().unwrap();
        let r = s.finish().unwrap();
        let mut c = r.cursor().unwrap();
        assert_eq!(c.next().unwrap().unwrap().id, 1);
        assert_eq!(c.next().unwrap().unwrap().id, 2);
    }
}
