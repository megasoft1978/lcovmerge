//! Input-aware, bounded staging for MC/DC records.
use super::{eligibility, function_check, mcdc_spill, sort, Request};
use crate::common::{check_cancelled, Error};
use std::path::{Path, PathBuf};

pub(super) struct Records {
    raw: sort::Sorter,
    expressions: sort::Sorter,
    tmpdir: PathBuf,
    budget: usize,
    jobs: usize,
}
pub(super) struct Reduced {
    pub public: sort::Sorted,
    pub master: sort::Sorted,
    pub members: sort::Sorted,
}
fn put_string(v: &mut Vec<u8>, s: &str) {
    v.extend_from_slice(s.as_bytes());
    v.push(0);
}
fn take_string<'a>(v: &'a [u8], at: &mut usize) -> Result<&'a str, Error> {
    let end = v
        .get(*at..)
        .and_then(|s| s.iter().position(|b| *b == 0))
        .map(|n| *at + n)
        .ok_or_else(|| Error::new(3, "corrupt MC/DC staging row"))?;
    let out = std::str::from_utf8(&v[*at..end])
        .map_err(|_| Error::new(3, "corrupt MC/DC staging row"))?;
    *at = end + 1;
    Ok(out)
}
impl Records {
    pub(super) fn new(tmpdir: &Path, budget: usize, jobs: usize) -> Result<Self, Error> {
        Ok(Self {
            raw: sort::Sorter::new(tmpdir, budget / 3, jobs)?,
            expressions: sort::Sorter::new(tmpdir, budget / 3, jobs)?,
            tmpdir: tmpdir.into(),
            budget,
            jobs,
        })
    }
    pub(super) fn push(
        &mut self,
        source: &str,
        tc: &str,
        input: u64,
        payload: &str,
        label: &str,
        line_no: u64,
        req: &Request,
    ) -> Result<(), Error> {
        check_cancelled()?;
        if [source, tc, label]
            .iter()
            .any(|s| s.len() > crate::common::MAX_LINE || s.as_bytes().contains(&0))
        {
            return Err(super::policy::at(
                super::policy::Kind::Format,
                label,
                line_no,
                "invalid or oversized MC/DC field",
            ));
        }
        let Some(p) = mcdc_spill::parse_point(payload, label, line_no, req)? else {
            return Ok(());
        };
        let mut key = Vec::new();
        put_string(&mut key, source);
        key.extend_from_slice(&input.to_be_bytes());
        put_string(&mut key, tc);
        key.extend_from_slice(&p.line.to_be_bytes());
        let mut val = Vec::new();
        val.extend_from_slice(&p.group.to_be_bytes());
        val.extend_from_slice(&p.index.to_be_bytes());
        val.extend_from_slice(&p.sense.to_be_bytes());
        val.extend_from_slice(&p.count.to_be_bytes());
        val.push(u8::from(p.unreachable));
        put_string(&mut val, &p.expression);
        put_string(&mut val, label);
        val.extend_from_slice(&line_no.to_be_bytes());
        let mut ek = Vec::new();
        put_string(&mut ek, source);
        ek.extend_from_slice(&input.to_be_bytes());
        ek.extend_from_slice(&p.line.to_be_bytes());
        ek.extend_from_slice(&p.group.to_be_bytes());
        ek.extend_from_slice(&p.index.to_be_bytes());
        ek.extend_from_slice(&p.sense.to_be_bytes());
        self.raw.push(key, val)?;
        self.expressions.push(ek, p.expression.into_bytes())
    }
    pub(super) fn finish(
        self,
        req: &Request,
        eligibility: &mut eligibility::Index,
    ) -> Result<Reduced, Error> {
        let mut ec = self.expressions.finish()?.cursor()?;
        let mut last: Option<(Vec<u8>, Vec<u8>)> = None;
        while let Some(r) = ec.next()? {
            check_cancelled()?;
            if let Some((k, v)) = &last {
                if *k == r.key && *v != r.value {
                    let e = super::policy::new(
                        super::policy::Kind::Inconsistent,
                        "inconsistent MC/DC expression",
                    );
                    if !super::policy::can_recover(req, &e) {
                        return Err(e);
                    }
                }
            }
            last = Some((r.key, r.value));
        }
        drop(ec);
        drop(last);
        let each = self.budget / 3;
        let mut public = mcdc_spill::Reducer::new(&self.tmpdir, each, self.jobs)?;
        let mut master = mcdc_spill::Reducer::new(&self.tmpdir, each, self.jobs)?;
        let mut members = sort::Sorter::new(&self.tmpdir, each, self.jobs)?;
        let sorted = self.raw.finish()?;
        let mut c = sorted.cursor()?;
        while let Some(r) = c.next()? {
            check_cancelled()?;
            let mut at = 0;
            let source = take_string(&r.key, &mut at)?;
            let input = u64::from_be_bytes(
                r.key
                    .get(at..at + 8)
                    .ok_or_else(|| Error::new(3, "corrupt MC/DC staging row"))?
                    .try_into()
                    .unwrap(),
            );
            at += 8;
            let tc = take_string(&r.key, &mut at)?;
            let mut va = 0;
            let group = u64::from_be_bytes(
                r.value[va..va + 8]
                    .try_into()
                    .map_err(|_| Error::new(3, "corrupt MC/DC staging row"))?,
            );
            va += 8;
            let index = u64::from_be_bytes(r.value[va..va + 8].try_into().unwrap());
            va += 8;
            let sense = u64::from_be_bytes(r.value[va..va + 8].try_into().unwrap());
            va += 8;
            let count = u64::from_be_bytes(r.value[va..va + 8].try_into().unwrap());
            va += 8;
            let unreachable = r.value[va] != 0;
            va += 1;
            let expr = take_string(&r.value, &mut va)?.to_owned();
            let label = take_string(&r.value, &mut va)?;
            let line_no = u64::from_be_bytes(r.value[va..va + 8].try_into().unwrap());
            let point_line = u64::from_be_bytes(r.key[at..at + 8].try_into().unwrap());
            let point = mcdc_spill::Point {
                line: point_line,
                group,
                index,
                sense,
                count,
                unreachable,
                expression: expr,
            };
            let status = eligibility.source(source, input)?;
            if !status.alive {
                continue;
            }
            master.push_point(source, &format!("{input:020}"), &point, label, line_no)?;
            if eligibility.testcase(source, input, tc)? {
                public.push_point(source, tc, &point, label, line_no)?;
                let mut mk = function_check::prefix(source, input)?;
                mk.extend_from_slice(&point_line.to_be_bytes());
                mk.extend_from_slice(tc.as_bytes());
                mk.push(0);
                members.push(mk, Vec::new())?;
            }
        }
        drop(c);
        drop(sorted);
        Ok(Reduced {
            public: public.finish(req)?,
            master: master.finish(req)?,
            members: members.finish()?,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::super::eligibility::Recorder;
    use super::*;
    fn index(dir: &Path) -> eligibility::Index {
        let mut req = Request::default();
        req.resources.inputs = vec!["zero".into(), "one".into()];
        let mut r = Recorder::new(dir, 1 << 20).unwrap();
        r.push("a.c", 0, "live", true).unwrap();
        r.push("a.c", 0, "ghost", false).unwrap();
        r.push("a.c", 1, "drop", false).unwrap();
        r.push("b.c", 1, "keep", true).unwrap();
        r.finish(&req).unwrap()
    }
    #[test]
    fn routes_live_and_master_rows_and_retains_input_membership() {
        let d = tempfile::tempdir().unwrap();
        let mut idx = index(d.path());
        let mut req = Request::default();
        req.resources.inputs = vec!["zero".into(), "one".into()];
        let mut r = Records::new(d.path(), 1 << 20, 1).unwrap();
        r.push("a.c", "live", 0, "4,2,t,1,0,A", "fixture", 1, &req)
            .unwrap();
        r.push("a.c", "ghost", 0, "4,2,t,7,0,A", "fixture", 2, &req)
            .unwrap();
        r.push("a.c", "drop", 1, "4,2,t,9,0,A", "fixture", 3, &req)
            .unwrap();
        let out = r.finish(&req, &mut idx).unwrap();
        let rows = |s: sort::Sorted| {
            let mut c = s.cursor().unwrap();
            let mut n = 0;
            while c.next().unwrap().is_some() {
                n += 1;
            }
            n
        };
        assert_eq!(rows(out.public), 3);
        assert_eq!(rows(out.master), 3);
        let mut c = out.members.cursor().unwrap();
        assert!(c.next().unwrap().is_some());
        assert!(c.next().unwrap().is_none());
    }
    #[test]
    fn within_input_expression_conflict_is_checked_even_for_dropped_source() {
        let d = tempfile::tempdir().unwrap();
        let mut idx = index(d.path());
        let mut req = Request::default();
        req.resources.inputs = vec!["zero".into(), "one".into()];
        let mut r = Records::new(d.path(), 1 << 20, 1).unwrap();
        r.push("a.c", "drop", 1, "4,2,t,1,0,A", "fixture", 1, &req)
            .unwrap();
        r.push("a.c", "drop", 1, "4,2,t,1,0,B", "fixture", 2, &req)
            .unwrap();
        let error = match r.finish(&req, &mut idx) {
            Ok(_) => panic!("expected expression conflict"),
            Err(e) => e,
        };
        assert!(error.message.contains("inconsistent"));
    }
}
