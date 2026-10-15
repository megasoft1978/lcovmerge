//! Bounded source-master MC/DC line synthesis and testcase projection.
use super::{function_check, line_spill, sort, Request};
use crate::common::{check_cancelled, Error};
use std::fs::File;
use std::io::BufReader;
use std::path::Path;

pub(super) struct Generated {
    pub master: sort::Sorted,
    pub testcases: sort::Sorted,
}
fn corrupt() -> Error {
    Error::new(3, "corrupt MC/DC line projection")
}
fn point(row: &sort::Row) -> Result<Option<(Vec<u8>, bool)>, Error> {
    let mut fields = row.key.splitn(3, |b| *b == 0);
    let source = std::str::from_utf8(fields.next().ok_or_else(corrupt)?).map_err(|_| corrupt())?;
    let input = fields.next().ok_or_else(corrupt)?;
    if input.len() != 20 || !input.iter().all(u8::is_ascii_digit) {
        return Err(corrupt());
    }
    let input: u64 = std::str::from_utf8(input)
        .map_err(|_| corrupt())?
        .parse()
        .map_err(|_| corrupt())?;
    let frame = fields.next().ok_or_else(corrupt)?;
    if frame == [6] {
        return Ok(None);
    }
    if frame.len() != 33 || frame[0] != 5 {
        return Err(corrupt());
    }
    let line = u64::from_be_bytes(frame[1..9].try_into().map_err(|_| corrupt())?);
    let value = std::str::from_utf8(&row.value).map_err(|_| corrupt())?;
    let mut fields = value
        .strip_prefix("MCDC:")
        .ok_or_else(corrupt)?
        .splitn(6, ',');
    if fields
        .next()
        .ok_or_else(corrupt)?
        .parse::<u64>()
        .map_err(|_| corrupt())?
        != line
    {
        return Err(corrupt());
    }
    let group = fields.next().ok_or_else(corrupt)?;
    let excluded = group.starts_with('U');
    group
        .strip_prefix('U')
        .unwrap_or(group)
        .parse::<u64>()
        .map_err(|_| corrupt())?;
    if !matches!(fields.next(), Some("t" | "f")) {
        return Err(corrupt());
    }
    let hits = fields
        .next()
        .ok_or_else(corrupt)?
        .parse::<u64>()
        .map_err(|_| corrupt())?;
    fields
        .next()
        .ok_or_else(corrupt)?
        .parse::<u64>()
        .map_err(|_| corrupt())?;
    fields.next().ok_or_else(corrupt)?;
    let mut key = function_check::prefix(source, input)?;
    key.extend_from_slice(&line.to_be_bytes());
    Ok(Some((key, hits > 0 && !excluded)))
}
/// Master rows use private, fixed-width decimal input IDs as their testcase.
/// Members use source-NUL/input-BE/line-BE/testcase-NUL keys.
pub(super) fn generate(
    req: &Request,
    budget: usize,
    master: &sort::Sorted,
    members: &sort::Sorted,
    original_da: &Path,
) -> Result<Generated, Error> {
    let each = budget / 3;
    let jobs = req.resources.jobs.max(1);
    let tmp = &req.resources.tmpdir;
    let mut counts = sort::Sorter::new(tmp, each, jobs)?;
    let mut cursor = master.cursor()?;
    let mut pending: Option<(Vec<u8>, u64)> = None;
    while let Some(row) = cursor.next()? {
        check_cancelled()?;
        let Some((key, hit)) = point(&row)? else {
            continue;
        };
        if pending.as_ref().is_some_and(|(old, _)| old != &key) {
            let (key, count) = pending.take().unwrap();
            counts.push(key, count.to_be_bytes().to_vec())?;
        }
        let group = pending.get_or_insert((key, 0));
        group.1 = group.1.saturating_add(u64::from(hit));
    }
    if let Some((key, count)) = pending {
        counts.push(key, count.to_be_bytes().to_vec())?;
    }
    drop(cursor);
    let counts = counts.finish()?;
    let mut presence = sort::Sorter::new(tmp, each, jobs)?;
    let mut reader =
        BufReader::new(File::open(original_da).map_err(|e| Error::new(3, e.to_string()))?);
    while let Some(row) = line_spill::read_next_public(&mut reader)? {
        check_cancelled()?;
        if row.kind == 1 {
            let mut key = function_check::prefix(&row.source, row.input)?;
            key.extend_from_slice(&row.line.to_be_bytes());
            presence.push(key, Vec::new())?;
        }
    }
    drop(reader);
    let presence = presence.finish()?;
    let mut existing = presence.cursor()?;
    let mut old = existing.next()?;
    let mut memberships = members.cursor()?;
    let mut member = memberships.next()?;
    let mut generated = sort::Sorter::new(tmp, each, jobs)?;
    let mut testcases = sort::Sorter::new(tmp, each, jobs)?;
    let mut counts_cursor = counts.cursor()?;
    while let Some(row) = counts_cursor.next()? {
        check_cancelled()?;
        while old.as_ref().is_some_and(|old| old.key < row.key) {
            old = existing.next()?;
        }
        if old.as_ref().is_some_and(|old| old.key == row.key) {
            continue;
        }
        generated.push(row.key.clone(), row.value.clone())?;
        while member.as_ref().is_some_and(|member| member.key < row.key) {
            member = memberships.next()?;
        }
        let mut previous = None;
        while member
            .as_ref()
            .is_some_and(|member| member.key.starts_with(&row.key))
        {
            let current = member.take().unwrap();
            if previous.as_ref() != Some(&current.key) {
                testcases.push(current.key.clone(), row.value.clone())?;
                previous = Some(current.key);
            }
            member = memberships.next()?;
        }
    }
    drop(existing);
    drop(memberships);
    drop(counts_cursor);
    Ok(Generated {
        master: generated.finish()?,
        testcases: testcases.finish()?,
    })
}
pub(super) fn decode(row: &sort::Row) -> Result<(String, u64, u64, u64, Option<String>), Error> {
    let end = row.key.iter().position(|b| *b == 0).ok_or_else(corrupt)?;
    let source = std::str::from_utf8(&row.key[..end])
        .map_err(|_| corrupt())?
        .to_owned();
    let rest = row.key.get(end + 1..).ok_or_else(corrupt)?;
    if rest.len() < 16 || row.value.len() != 8 {
        return Err(corrupt());
    }
    let input = u64::from_be_bytes(rest[..8].try_into().map_err(|_| corrupt())?);
    let line = u64::from_be_bytes(rest[8..16].try_into().map_err(|_| corrupt())?);
    let count = u64::from_be_bytes(row.value[..].try_into().map_err(|_| corrupt())?);
    let tc = if rest.len() == 16 {
        None
    } else {
        let text = rest[16..].strip_suffix(&[0]).ok_or_else(corrupt)?;
        if text.contains(&0) {
            return Err(corrupt());
        }
        Some(std::str::from_utf8(text).map_err(|_| corrupt())?.to_owned())
    };
    Ok((source, input, line, count, tc))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn master_row(sorter: &mut sort::Sorter, line: u64, index: u64, hits: u64, excluded: bool) {
        let mut key = b"unit.c\0".to_vec();
        key.extend_from_slice(b"00000000000000000000\0");
        key.push(5);
        for n in [line, 2, index, 0] {
            key.extend_from_slice(&n.to_be_bytes());
        }
        sorter
            .push(
                key,
                format!(
                    "MCDC:{line},{}2,t,{hits},{index},condition",
                    if excluded { "U" } else { "" }
                )
                .into_bytes(),
            )
            .unwrap();
    }
    #[test]
    fn source_counts_project_once_per_member_and_keep_zero_and_ghost_lines() {
        let dir = tempfile::tempdir().unwrap();
        let mut req = Request::default();
        req.resources.tmpdir = dir.path().to_owned();
        let mut master = sort::Sorter::new(dir.path(), 64 * 1024, 1).unwrap();
        master_row(&mut master, 2, 0, 7, false);
        master_row(&mut master, 2, 1, 9, false);
        master_row(&mut master, 3, 0, 9, true);
        master_row(&mut master, 4, 0, 1, false);
        master_row(&mut master, 5, 0, 1, false);
        let master = master.finish().unwrap();
        let mut members = sort::Sorter::new(dir.path(), 64 * 1024, 1).unwrap();
        for line in [2u64, 3, 5] {
            for tc in ["a", "a", "b"] {
                let mut key = function_check::prefix("unit.c", 0).unwrap();
                key.extend_from_slice(&line.to_be_bytes());
                key.extend_from_slice(tc.as_bytes());
                key.push(0);
                members.push(key, Vec::new()).unwrap();
            }
        }
        let members = members.finish().unwrap();
        let mut lines = line_spill::Spill::new(dir.path(), 64 * 1024, 2).unwrap();
        lines
            .push(line_spill::Entry {
                kind: 1,
                source: "unit.c".into(),
                testcase: "other".into(),
                value: String::new(),
                line: 5,
                count: 0,
                input: 0,
                section: 0,
                ordinal: 0,
                label: String::new(),
                line_no: 0,
            })
            .unwrap();
        let path = lines.finish().unwrap();
        let generated = generate(&req, 256 * 1024, &master, &members, &path).unwrap();
        let mut cursor = generated.master.cursor().unwrap();
        let mut actual = Vec::new();
        while let Some(row) = cursor.next().unwrap() {
            let (_, _, line, count, _) = decode(&row).unwrap();
            actual.push((line, count));
        }
        assert_eq!(actual, [(2, 2), (3, 0), (4, 1)]);
        let mut cursor = generated.testcases.cursor().unwrap();
        let mut actual = Vec::new();
        while let Some(row) = cursor.next().unwrap() {
            let (_, _, line, count, tc) = decode(&row).unwrap();
            actual.push((line, count, tc.unwrap()));
        }
        assert_eq!(
            actual,
            [
                (2, 2, "a".into()),
                (2, 2, "b".into()),
                (3, 0, "a".into()),
                (3, 0, "b".into())
            ]
        );
    }
}
