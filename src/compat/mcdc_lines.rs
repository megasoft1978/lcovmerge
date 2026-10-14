//! Bounded generation of DA rows for MC/DC-only lines.
use super::{line_spill, sort, Request};
use crate::common::{check_cancelled, Error};
use std::fs::File;
use std::io::BufReader;
use std::path::Path;

pub(super) struct Generated {
    pub master: sort::Sorted,
    pub testcases: sort::Sorted,
}

fn err(s: &str) -> Error {
    Error::new(3, format!("corrupt MC/DC line input: {s}"))
}
fn base(source: &[u8], input: u64, line: u64) -> Vec<u8> {
    let mut k = Vec::with_capacity(source.len() + 17);
    k.extend_from_slice(source);
    k.push(0);
    k.extend_from_slice(&input.to_be_bytes());
    k.extend_from_slice(&line.to_be_bytes());
    k
}
fn decode_master(k: &[u8], v: &[u8]) -> Result<Option<(Vec<u8>, u64, u64, Vec<u8>)>, Error> {
    let sep = k
        .iter()
        .position(|b| *b == 0)
        .ok_or_else(|| err("missing source"))?;
    let (source, tail) = k.split_at(sep);
    let rest = &tail[1..];
    let (id, rank) = rest
        .split_at_checked(20)
        .ok_or_else(|| err("short input id"))?;
    let input = id.iter().try_fold(0u64, |n, b| {
        if !b.is_ascii_digit() {
            return Err(err("invalid input id"));
        }
        n.checked_mul(10)
            .and_then(|x| x.checked_add(u64::from(b - b'0')))
            .ok_or_else(|| err("input id overflow"))
    })?;
    if rank.first() != Some(&5) {
        return Ok(None);
    }
    if rank.len() != 33 || v.len() < 7 || !v.starts_with(b"MCDC:") {
        return Err(err("invalid point row"));
    }
    let value = std::str::from_utf8(v).map_err(|_| err("non-UTF8 row"))?;
    let payload = value
        .strip_prefix("MCDC:")
        .ok_or_else(|| err("invalid point row"))?;
    let mut f = payload.splitn(6, ',');
    let line: u64 = f
        .next()
        .ok_or_else(|| err("missing line"))?
        .parse()
        .map_err(|_| err("invalid line"))?;
    let group = f.next().ok_or_else(|| err("missing group"))?;
    if group.starts_with('U') {
        return Ok(None);
    }
    let sense = f.next().ok_or_else(|| err("missing sense"))?;
    if sense != "t" && sense != "f" {
        return Err(err("invalid sense"));
    }
    let count: u64 = f
        .next()
        .ok_or_else(|| err("missing count"))?
        .parse()
        .map_err(|_| err("invalid count"))?;
    let index: u64 = f
        .next()
        .ok_or_else(|| err("missing index"))?
        .parse()
        .map_err(|_| err("invalid index"))?;
    let group: u64 = group.parse().map_err(|_| err("invalid group"))?;
    let mut p = base(source, input, line);
    p.extend_from_slice(&group.to_be_bytes());
    p.extend_from_slice(&index.to_be_bytes());
    p.push(u8::from(sense == "f"));
    Ok((count > 0).then(|| (p, input, line, source.to_vec())))
}

/// Generate source-wide MC/DC DA counts and fan them out to eligible members.
pub(super) fn generate(
    req: &Request,
    budget: usize,
    master: &sort::Sorted,
    members: &sort::Sorted,
    original_da: &Path,
) -> Result<Generated, Error> {
    let tmp = &req.resources.tmpdir;
    let jobs = req.resources.jobs.max(1);
    let each = (budget / 4).max(1);
    let mut points = sort::Sorter::new(tmp, each, jobs)?;
    let mut c = master.cursor()?;
    while let Some(row) = c.next()? {
        check_cancelled()?;
        if let Some((k, _, _, _)) = decode_master(&row.key, &row.value)? {
            points.push(k, Vec::new())?;
        }
    }
    drop(c);
    let points = points.finish()?;
    let mut unique = sort::Sorter::new(tmp, each, jobs)?;
    let mut pc = points.cursor()?;
    let mut prior: Option<Vec<u8>> = None;
    while let Some(row) = pc.next()? {
        check_cancelled()?;
        if prior.as_ref() != Some(&row.key) {
            unique.push(row.key.clone(), Vec::new())?;
            prior = Some(row.key);
        }
    }
    drop(pc);
    drop(points);
    let unique = unique.finish()?;
    let mut counts = sort::Sorter::new(tmp, each, jobs)?;
    let mut uc = unique.cursor()?;
    let mut current: Option<(Vec<u8>, u64)> = None;
    while let Some(row) = uc.next()? {
        check_cancelled()?;
        if row.key.len() < 17 {
            return Err(err("short point key"));
        }
        let line_key = row.key[..row.key.len() - 17].to_vec();
        match current.as_mut() {
            Some((k, n)) if *k == line_key => *n = n.saturating_add(1),
            Some((k, n)) => {
                counts.push(k.clone(), n.to_be_bytes().to_vec())?;
                *k = line_key;
                *n = 1;
            }
            None => current = Some((line_key, 1)),
        }
    }
    if let Some((k, n)) = current {
        counts.push(k, n.to_be_bytes().to_vec())?;
    }
    drop(uc);
    drop(unique);
    let counts = counts.finish()?;
    let mut da = sort::Sorter::new(tmp, each, jobs)?;
    let mut original = BufReader::new(
        File::open(original_da)
            .map_err(|e| Error::new(3, format!("cannot read original DA run: {e}")))?,
    );
    let mut presence = sort::Sorter::new(tmp, each, jobs)?;
    while let Some(e) = line_spill::read_next_public(&mut original)? {
        check_cancelled()?;
        if e.kind == 1 {
            let mut k = e.source.into_bytes();
            k.push(0);
            k.extend_from_slice(&e.input.to_be_bytes());
            k.extend_from_slice(&e.line.to_be_bytes());
            presence.push(k, Vec::new())?;
        }
    }
    let presence = presence.finish()?;
    let mut c = counts.cursor()?;
    let mut old = presence.cursor()?;
    let mut existing = old.next()?;
    while let Some(row) = c.next()? {
        check_cancelled()?;
        while existing.as_ref().is_some_and(|x| x.key < row.key) {
            existing = old.next()?;
        }
        if existing.as_ref().is_some_and(|x| x.key == row.key) {
            continue;
        }
        da.push(row.key.clone(), row.value.clone())?;
    }
    drop(c);
    drop(old);
    drop(presence);
    drop(counts);
    let da = da.finish()?;
    let generated_master = da;
    let mut dc = sort::Sorter::new(tmp, each, jobs)?;
    let mut mc = generated_master.cursor()?;
    let mut tc = members.cursor()?;
    let mut member = tc.next()?;
    let mut last_member: Option<Vec<u8>> = None;
    while let Some(row) = mc.next()? {
        check_cancelled()?;
        let count = row.value.clone();
        while member.as_ref().is_some_and(|m| m.key < row.key) {
            member = tc.next()?;
        }
        while member.as_ref().is_some_and(|m| m.key.starts_with(&row.key)) {
            let m = member.take().unwrap();
            if last_member.as_ref() != Some(&m.key) {
                dc.push(m.key.clone(), count.clone())?;
                last_member = Some(m.key);
            }
            member = tc.next()?;
        }
    }
    drop(mc);
    drop(tc);
    Ok(Generated {
        master: generated_master,
        testcases: dc.finish()?,
    })
}

#[cfg(test)]
mod tests {
    use super::decode_master;

    fn row(value: &str) -> (Vec<u8>, Vec<u8>) {
        let mut key = b"src.c\0".to_vec();
        key.extend_from_slice(b"00000000000000000007");
        key.extend_from_slice(&[5]);
        key.extend_from_slice(&4u64.to_be_bytes());
        key.resize(key.len() + 24, 0);
        (key, format!("MCDC:{value}").into_bytes())
    }
    #[test]
    fn positive_points_countable_but_zero_and_unreachable_are_skipped() {
        let (k, v) = row("4,2,t,1,0,A");
        let (point, input, line, source) = decode_master(&k, &v).unwrap().unwrap();
        assert_eq!((input, line, source.as_slice()), (7, 4, &b"src.c"[..]));
        assert_eq!(point.len(), b"src.c\0".len() + 8 + 8 + 17);
        let (k, v) = row("4,2,f,0,1,A");
        assert!(decode_master(&k, &v).unwrap().is_none());
        let (k, v) = row("4,U2,t,2,0,A");
        assert!(decode_master(&k, &v).unwrap().is_none());
    }
}
