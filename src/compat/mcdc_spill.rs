//! Bounded external reducer for LCOV MC/DC records.
use super::policy::{can_recover, num, parse_count};
use super::sort::{Sorted, Sorter};
use crate::common::{check_cancelled, Error, MAX_LINE};
use crate::compat::Request;
use std::path::Path;

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
type ParsedKey<'a> = (&'a [u8], &'a [u8], u64, u64, u64, u64);
fn split_key(k: &[u8]) -> Result<ParsedKey<'_>, Error> {
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
                .filter(|c| *c == "t" || *c == "f")
                .and_then(|c| c.chars().next())
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
        numeric(&mut ek, if sense == 't' { 0 } else { 1 });
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
        drop(ec);
        drop(expr_sorted);
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
                if let Some(k) = key {
                    out.push(k, format!("{found},{hit}").into_bytes())?;
                }
                Ok(())
            };
        let mut pending: Option<(Vec<u8>, Vec<u8>)> = None;
        let emit = |r: (Vec<u8>, Vec<u8>),
                    out: &mut Sorter,
                    summary_key: &mut Option<Vec<u8>>,
                    found: &mut i64,
                    hit: &mut i64,
                    group_state: &mut Option<(Vec<u8>, u64, u64)>,
                    normalized: &mut u64|
         -> Result<(), Error> {
            let (raw_key, raw_value) = r;
            let (s, t, line, g, index, sense) = split_key(&raw_key)?;
            let mut sk = Vec::new();
            sk.extend_from_slice(s);
            sk.push(0);
            sk.extend_from_slice(t);
            sk.push(0);
            sk.push(6);
            if summary_key.as_ref().is_some_and(|k| *k != sk) {
                flush_summary(out, summary_key.take(), *found, *hit)?;
                *found = 0;
                *hit = 0;
            }
            *summary_key = Some(sk);
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
                    *normalized = normalized.saturating_add(1)
                } else {
                    *normalized = 0
                }
                *group_state = Some(gs);
            }
            if raw_value.len() < 9 {
                return Err(Error::new(3, "corrupt MC/DC value"));
            }
            let count = u64::from_be_bytes(
                raw_value[..8]
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
            numeric(&mut key, *normalized);
            numeric(&mut key, sense);
            let mut value = format!(
                "MCDC:{line},{}{g},{},{count},{normalized},",
                if raw_value[8] != 0 { "U" } else { "" },
                if sense == 0 { "t" } else { "f" }
            )
            .into_bytes();
            value.extend_from_slice(&raw_value[9..]);
            out.push(key, value)?;
            *found = found.saturating_add(1);
            if count > 0 {
                *hit = hit.saturating_add(1);
            }
            Ok(())
        };
        while let Some(r) = cur.next()? {
            check_cancelled()?;
            if let Some((pk, mut pv)) = pending.take() {
                if pk == r.key {
                    if &pv[9..]
                        != r.value
                            .get(9..)
                            .ok_or_else(|| Error::new(3, "corrupt MC/DC value"))?
                    {
                        let e = Error::new(2, "inconsistent MC/DC expression");
                        if !can_recover(request, &e) {
                            return Err(e);
                        }
                        pending = Some((pk, pv));
                        continue;
                    }
                    if r.value.len() < 9 {
                        return Err(Error::new(3, "corrupt MC/DC value"));
                    }
                    let a = u64::from_be_bytes(
                        pv[..8]
                            .try_into()
                            .map_err(|_| Error::new(3, "corrupt MC/DC value"))?,
                    );
                    let b = u64::from_be_bytes(
                        r.value[..8]
                            .try_into()
                            .map_err(|_| Error::new(3, "corrupt MC/DC value"))?,
                    );
                    pv[..8]
                        .copy_from_slice(&a.saturating_add(b).min(i64::MAX as u64).to_be_bytes());
                    pv[8] |= r.value[8];
                    pending = Some((pk, pv));
                    continue;
                }
                emit(
                    (pk, pv),
                    &mut out,
                    &mut summary_key,
                    &mut found,
                    &mut hit,
                    &mut group_state,
                    &mut normalized,
                )?;
            }
            if r.value.len() < 9 {
                return Err(Error::new(3, "corrupt MC/DC value"));
            }
            pending = Some((r.key, r.value));
        }
        if let Some(r) = pending {
            emit(
                r,
                &mut out,
                &mut summary_key,
                &mut found,
                &mut hit,
                &mut group_state,
                &mut normalized,
            )?;
        }
        drop(cur);
        flush_summary(&mut out, summary_key, found, hit)?;
        out.finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reducer() -> (tempfile::TempDir, Reducer) {
        let d = tempfile::tempdir().unwrap();
        let r = Reducer::new(d.path(), 1 << 20, 1).unwrap();
        (d, r)
    }
    fn push(r: &mut Reducer, tn: &str, payload: &str) {
        r.push("x.c", tn, payload, "fixture", 1, &Request::default())
            .unwrap();
    }
    fn collect(sorted: Sorted) -> Vec<(Vec<u8>, Vec<u8>)> {
        let mut c = sorted.cursor().unwrap();
        let mut v = Vec::new();
        while let Some(row) = c.next().unwrap() {
            v.push((row.key, row.value));
        }
        v
    }
    fn rank(key: &[u8]) -> u8 {
        let first = key.iter().position(|b| *b == 0).unwrap();
        let second = key[first + 1..].iter().position(|b| *b == 0).unwrap() + first + 1;
        key[second + 1]
    }
    fn recover() -> Request {
        let mut r = Request::default();
        r.ignore_errors.insert("inconsistent".into());
        r
    }

    #[test]
    fn folds_duplicates_and_emits_ranked_summary() {
        let (_d, mut r) = reducer();
        push(&mut r, "t", "4,2,t,3,0,A");
        push(&mut r, "t", "4,2,t,5,0,A");
        push(&mut r, "t", "4,2,f,0,0,A");
        push(&mut r, "t", "4,U2,f,8,0,A");
        let rows = collect(r.finish(&Request::default()).unwrap());
        assert_eq!(rows.len(), 3);
        assert_eq!(rows[0].1, b"MCDC:4,2,t,8,0,A");
        assert_eq!(rows[1].1, b"MCDC:4,U2,f,8,0,A");
        assert_eq!(rows[2].1, b"2,2");
        assert_eq!(rank(&rows[0].0), 5);
        assert_eq!(rank(&rows[2].0), 6);
    }

    #[test]
    fn sparse_indexes_normalize_and_keep_senses_adjacent() {
        let (_d, mut r) = reducer();
        for i in [0, 7, 19] {
            push(&mut r, "t", &format!("4,3,t,1,{i},A"));
            push(&mut r, "t", &format!("4,3,f,0,{i},A"));
        }
        let rows = collect(r.finish(&Request::default()).unwrap());
        let records: Vec<_> = rows
            .iter()
            .filter(|(k, _)| rank(k) == 5)
            .map(|(_, v)| v.as_slice())
            .collect();
        assert_eq!(
            records,
            [
                b"MCDC:4,3,t,1,0,A".as_slice(),
                b"MCDC:4,3,f,0,0,A",
                b"MCDC:4,3,t,1,1,A",
                b"MCDC:4,3,f,0,1,A",
                b"MCDC:4,3,t,1,2,A",
                b"MCDC:4,3,f,0,2,A"
            ]
        );
    }

    #[test]
    fn duplicate_count_saturates_at_i64_and_recovery_keeps_first_expression() {
        let (_d, mut r) = reducer();
        r.push(
            "x.c",
            "t",
            "4,2,t,9223372036854775807,0,A",
            "fixture",
            1,
            &Request::default(),
        )
        .unwrap();
        r.push("x.c", "t", "4,2,t,1,0,A", "fixture", 2, &Request::default())
            .unwrap();
        r.push("x.c", "t", "4,2,t,8,0,B", "fixture", 3, &recover())
            .unwrap();
        let rows = collect(r.finish(&recover()).unwrap());
        assert_eq!(rows[0].1, b"MCDC:4,2,t,9223372036854775807,0,A");
    }

    #[test]
    fn mismatch_is_strict_error_and_recovery_skips_mismatched_hits() {
        let (d, mut strict) = reducer();
        push(&mut strict, "t", "4,2,t,1,0,A");
        push(&mut strict, "t", "4,2,t,9,0,B");
        let error = match strict.finish(&Request::default()) {
            Ok(_) => panic!("expected inconsistent expression error"),
            Err(error) => error,
        };
        assert!(error.message.contains("inconsistent"));
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 0);
        let (_d, mut r) = reducer();
        r.push("x.c", "t", "4,2,t,1,0,A", "fixture", 1, &recover())
            .unwrap();
        r.push("x.c", "t", "4,2,t,9,0,B", "fixture", 2, &recover())
            .unwrap();
        let rows = collect(r.finish(&recover()).unwrap());
        assert_eq!(rows[0].1, b"MCDC:4,2,t,1,0,A");
    }

    #[test]
    fn expressions_are_checked_per_source_line_group_index_and_sense() {
        let (_d, mut r) = reducer();
        r.push("x.c", "a", "4,2,t,1,0,A", "fixture", 1, &Request::default())
            .unwrap();
        r.push("x.c", "b", "4,2,t,1,0,A", "fixture", 2, &Request::default())
            .unwrap();
        r.push("x.c", "a", "4,2,f,1,0,A", "fixture", 3, &Request::default())
            .unwrap();
        r.push("x.c", "a", "4,3,t,1,0,C", "fixture", 4, &Request::default())
            .unwrap();
        let rows = collect(r.finish(&Request::default()).unwrap());
        assert_eq!(rows.iter().filter(|(k, _)| rank(k) == 5).count(), 4);
        assert!(rows.iter().any(|(_, v)| v.ends_with(b",A")));
    }

    #[test]
    fn cross_testcase_expression_conflict_is_rejected_and_results_hold_tempdir() {
        let (d, mut r) = reducer();
        r.push("x.c", "a", "4,2,t,1,0,A", "fixture", 1, &Request::default())
            .unwrap();
        r.push("x.c", "b", "4,2,t,1,0,B", "fixture", 2, &Request::default())
            .unwrap();
        let error = match r.finish(&Request::default()) {
            Ok(_) => panic!("expected cross-testcase expression error"),
            Err(error) => error,
        };
        assert!(error.message.contains("inconsistent"));
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 0);

        let mut r = Reducer::new(d.path(), 1 << 20, 1).unwrap();
        push(&mut r, "a", "4,2,t,1,0,A");
        let sorted = r.finish(&Request::default()).unwrap();
        let path_count = std::fs::read_dir(d.path()).unwrap().count();
        assert!(path_count > 0);
        let cursor = sorted.cursor().unwrap();
        drop(cursor);
        drop(sorted);
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 0);
    }

    #[test]
    fn sense_must_be_exactly_one_character() {
        let (_d, mut r) = reducer();
        let error = r
            .push(
                "x.c",
                "t",
                "4,2,true,1,0,A",
                "fixture",
                1,
                &Request::default(),
            )
            .unwrap_err();
        assert!(error.message.contains("sense"));
    }

    #[test]
    fn spilling_large_group_emits_each_normalized_row_and_cleans_tempdirs() {
        let d = tempfile::tempdir().unwrap();
        let mut r = Reducer::new(d.path(), 32 << 10, 1).unwrap();
        for i in 0..90 {
            r.push(
                "x.c",
                "t",
                &format!("4,90,t,1,{i},{}", "e".repeat(1500)),
                "fixture",
                i + 1,
                &Request::default(),
            )
            .unwrap();
        }
        let rows = collect(r.finish(&Request::default()).unwrap());
        assert_eq!(rows.len(), 91);
        assert_eq!(rows.iter().filter(|(k, _)| rank(k) == 5).count(), 90);
        drop(rows);
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 0);
    }
}
