//! Bounded external reducer for LCOV MC/DC records.
use super::policy::{can_recover, num, parse_count};
use super::sort::{Sorted, Sorter};
use crate::common::{check_cancelled, Error, MAX_LINE};
use crate::compat::Request;
use std::path::Path;

const IO: usize = 4096;
const OVERHEAD: usize = 256;

pub(super) struct Reducer {
    rows: Sorter,
    expressions: Sorter,
    budget: usize,
    jobs: usize,
    tmpdir: std::path::PathBuf,
}

fn err(file: &str, line: u64, message: impl Into<String>) -> Error {
    Error::at(2, file, line, message)
}
fn field(out: &mut Vec<u8>, s: &str, file: &str, line: u64) -> Result<(), Error> {
    if s.as_bytes().contains(&0) || s.len() > MAX_LINE {
        return Err(err(file, line, "invalid or oversized MC/DC field"));
    }
    out.extend_from_slice(s.as_bytes());
    out.push(0);
    Ok(())
}
fn numeric(out: &mut Vec<u8>, n: u64) {
    out.extend_from_slice(&n.to_be_bytes());
}
fn read_num(b: &[u8], at: &mut usize) -> Result<u64, Error> {
    let end = at
        .checked_add(8)
        .ok_or_else(|| Error::new(3, "corrupt MC/DC frame"))?;
    let bytes = b
        .get(*at..end)
        .ok_or_else(|| Error::new(3, "corrupt MC/DC frame"))?;
    *at = end;
    Ok(u64::from_be_bytes(
        bytes
            .try_into()
            .map_err(|_| Error::new(3, "corrupt MC/DC frame"))?,
    ))
}
fn split_key(k: &[u8]) -> Result<(&[u8], &[u8], u64, u64, u64, u64), Error> {
    let mut fields = k.splitn(3, |b| *b == 0);
    let s = fields
        .next()
        .ok_or_else(|| Error::new(3, "corrupt MC/DC key"))?;
    let t = fields
        .next()
        .ok_or_else(|| Error::new(3, "corrupt MC/DC key"))?;
    let rest = fields
        .next()
        .ok_or_else(|| Error::new(3, "corrupt MC/DC key"))?;
    if rest.len() != 33 || rest[0] != 5 {
        return Err(Error::new(3, "corrupt MC/DC key"));
    }
    let mut at = 1;
    let line = read_num(rest, &mut at)?;
    let group = read_num(rest, &mut at)?;
    let index = read_num(rest, &mut at)?;
    let sense = read_num(rest, &mut at)?;
    Ok((s, t, line, group, index, sense))
}

impl Reducer {
    pub(super) fn new(tmpdir: &Path, budget: usize, jobs: usize) -> Result<Self, Error> {
        let each = budget / 2;
        Ok(Self {
            rows: Sorter::new(tmpdir, each, jobs)?,
            expressions: Sorter::new(tmpdir, each, jobs)?,
            budget,
            jobs,
            tmpdir: tmpdir.to_path_buf(),
        })
    }
    pub(super) fn push(
        &mut self,
        source: &str,
        testcase: &str,
        payload: &str,
        label: &str,
        line_no: u64,
        request: &Request,
    ) -> Result<(), Error> {
        check_cancelled()?;
        let recover = |e: Error| -> Result<(), Error> {
            if can_recover(request, &e) {
                Ok(())
            } else {
                Err(e)
            }
        };
        let parsed = (|| {
            if payload.len() > MAX_LINE
                || source.len() > MAX_LINE
                || testcase.len() > MAX_LINE
                || label.len() > MAX_LINE
            {
                return Err(err(label, line_no, "MC/DC field exceeds 1 MiB"));
            }
            let mut f = payload.splitn(6, ',');
            let line = num(f.next(), label, line_no)?;
            let g = f
                .next()
                .ok_or_else(|| err(label, line_no, "malformed MCDC record"))?;
            let sense = f
                .next()
                .unwrap_or("")
                .chars()
                .next()
                .filter(|c| *c == 't' || *c == 'f')
                .ok_or_else(|| err(label, line_no, "invalid MC/DC sense"))?;
            let raw = f.next().unwrap_or("");
            let count = match parse_count(raw, label, line_no) {
                Err(e) if e.message.contains("negative") && can_recover(request, &e) => 0,
                x => x?,
            };
            let index = num(f.next(), label, line_no)?;
            let expr = f
                .next()
                .filter(|x| !x.is_empty())
                .ok_or_else(|| err(label, line_no, "malformed MC/DC expression"))?;
            if line == 0 {
                return Err(err(label, line_no, "MC/DC line must be positive"));
            }
            let unreachable = g.starts_with('U');
            let group = num(Some(g.strip_prefix('U').unwrap_or(g)), label, line_no)?;
            if group == 0 {
                return Err(err(label, line_no, "MC/DC group size must be positive"));
            }
            if expr.len() > MAX_LINE || expr.as_bytes().contains(&0) {
                return Err(err(label, line_no, "invalid MC/DC expression"));
            }
            Ok((line, group, index, sense, count, unreachable, expr))
        })();
        let (line, group, index, sense, count, unreachable, expr) = match parsed {
            Ok(v) => v,
            Err(e) => return recover(e),
        };
        let mut key = Vec::new();
        field(&mut key, source, label, line_no)?;
        field(&mut key, testcase, label, line_no)?;
        key.push(5);
        numeric(&mut key, line);
        numeric(&mut key, group);
        numeric(&mut key, index);
        numeric(&mut key, if sense == 't' { 0 } else { 1 });
        let mut value = Vec::with_capacity(expr.len() + 9);
        value.extend_from_slice(&count.to_be_bytes());
        value.push(u8::from(unreachable));
        value.extend_from_slice(expr.as_bytes());
        let mut ek = Vec::new();
        field(&mut ek, source, label, line_no)?;
        numeric(&mut ek, line);
        numeric(&mut ek, group);
        numeric(&mut ek, index);
        self.rows.push(key, value)?;
        self.expressions.push(ek, expr.as_bytes().to_vec())
    }
    pub(super) fn finish(self, request: &Request) -> Result<Sorted, Error> {
        let budget = self.budget;
        let jobs = self.jobs;
        let tmpdir = self.tmpdir.clone();
        let expr_sorted = self.expressions.finish()?;
        let mut ec = expr_sorted.cursor()?;
        let mut last: Option<(Vec<u8>, Vec<u8>)> = None;
        while let Some(r) = ec.next()? {
            check_cancelled()?;
            if let Some((k, v)) = &last {
                if *k == r.key && *v != r.value {
                    let e = Error::new(2, "inconsistent MC/DC expression");
                    if !can_recover(request, &e) {
                        return Err(e);
                    }
                }
            }
            last = Some((r.key, r.value));
        }
        let sorted = self.rows.finish()?;
        let mut cur = sorted.cursor()?;
        let mut out = Sorter::new(&tmpdir, budget / 2, jobs)?;
        let mut group_state: Option<(Vec<u8>, u64, u64)> = None;
        let mut normalized = 0u64;
        let mut found = 0i64;
        let mut hit = 0i64;
        let mut summary_key: Option<Vec<u8>> = None;
        let flush_summary =
            |out: &mut Sorter, key: Option<Vec<u8>>, found: i64, hit: i64| -> Result<(), Error> {
                if let Some(mut k) = key {
                    k.push(6);
                    out.push(k, format!("{found},{hit}").into_bytes())?;
                }
                Ok(())
            };
        while let Some(r) = cur.next()? {
            check_cancelled()?;
            let (s, t, line, g, index, sense) = split_key(&r.key)?;
            let mut sk = Vec::new();
            sk.extend_from_slice(s);
            sk.push(0);
            sk.extend_from_slice(t);
            sk.push(0);
            sk.push(6);
            if summary_key.as_ref().is_some_and(|k| *k != sk) {
                flush_summary(&mut out, summary_key.take(), found, hit)?;
                found = 0;
                hit = 0;
            }
            summary_key = Some(sk);
            let mut identity = Vec::new();
            identity.extend_from_slice(s);
            identity.push(0);
            identity.extend_from_slice(t);
            identity.push(0);
            numeric(&mut identity, line);
            numeric(&mut identity, g);
            let gs = (identity.clone(), g, index);
            if group_state
                .as_ref()
                .is_none_or(|(prev, pg, pi)| *prev != identity || *pg != g || *pi != index)
            {
                if group_state
                    .as_ref()
                    .is_some_and(|(prev, pg, _)| *prev == identity && *pg == g)
                {
                    normalized = normalized.saturating_add(1)
                } else {
                    normalized = 0
                }
                group_state = Some(gs);
            }
            if r.value.len() < 9 {
                return Err(Error::new(3, "corrupt MC/DC value"));
            }
            let count = u64::from_be_bytes(
                r.value[..8]
                    .try_into()
                    .map_err(|_| Error::new(3, "corrupt MC/DC value"))?,
            );
            let mut key = Vec::new();
            key.extend_from_slice(s);
            key.push(0);
            key.extend_from_slice(t);
            key.push(0);
            key.push(5);
            numeric(&mut key, line);
            numeric(&mut key, g);
            numeric(&mut key, normalized);
            numeric(&mut key, sense);
            let mut value = format!(
                "MCDC:{line},{}{g},{},{count},{normalized},",
                if r.value[8] != 0 { "U" } else { "" },
                if sense == 0 { "t" } else { "f" }
            )
            .into_bytes();
            value.extend_from_slice(&r.value[9..]);
            out.push(key, value)?;
            found = found.saturating_add(1);
            if count > 0 {
                hit = hit.saturating_add(1)
            }
        }
        flush_summary(&mut out, summary_key, found, hit)?;
        out.finish()
    }
}
