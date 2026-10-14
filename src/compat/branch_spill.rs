//! Bounded BRDA signature ranking and canonical folding pipeline.
use super::branch_fold::Fold;
use super::policy;
use super::signature_store;
use super::sort::{self, Sorted};
use super::Request;
use crate::common::{check_cancelled, Error};
use std::path::{Path, PathBuf};

const MAX_HITS: u64 = i64::MAX as u64;

pub(super) struct Reducer {
    raw: sort::Sorter,
    store: signature_store::Store,
    tmpdir: PathBuf,
    budget: usize,
    jobs: usize,
}

impl Reducer {
    pub(super) fn new(tmpdir: &Path, budget: usize, jobs: usize) -> Result<Self, Error> {
        let part = budget / 3;
        if part == 0 {
            return Err(corrupt("memory budget is too small for BRDA reduction"));
        }
        Ok(Self {
            raw: sort::Sorter::new(tmpdir, part, jobs)?,
            store: signature_store::Store::new(tmpdir, part)?,
            tmpdir: tmpdir.to_path_buf(),
            budget,
            jobs,
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn push(
        &mut self,
        source: &str,
        tc: &str,
        input: u64,
        section: u64,
        ordinal: u64,
        payload: &str,
        label: &str,
        line_no: u64,
        req: &Request,
    ) -> Result<Option<(u64, bool)>, Error> {
        check_cancelled()?;
        if source.as_bytes().contains(&0) || tc.as_bytes().contains(&0) {
            return Err(super::policy::at(
                super::policy::Kind::Format,
                label,
                line_no,
                "NUL in BRDA identity",
            ));
        }
        let parsed = match parse(payload, label, line_no, req) {
            Ok(parsed) => parsed,
            Err(error) if policy::can_recover(req, &error) => return Ok(None),
            Err(error) => return Err(error),
        };
        let mut key = Vec::with_capacity(source.len() + tc.len() + 42);
        key.extend_from_slice(source.as_bytes());
        key.push(0);
        key.extend_from_slice(tc.as_bytes());
        key.push(0);
        for n in [input, section, parsed.line, parsed.block, ordinal] {
            key.extend_from_slice(&n.to_be_bytes());
        }
        let mut value = Vec::with_capacity(parsed.expression.len() + 11);
        value.push(parsed.kind as u8);
        value.push(u8::from(parsed.unreachable));
        match parsed.hits {
            Some(hits) => {
                value.push(1);
                value.extend_from_slice(&hits.to_be_bytes());
            }
            None => {
                value.push(0);
                value.extend_from_slice(&0u64.to_be_bytes());
            }
        }
        value.extend_from_slice(parsed.expression.as_bytes());
        self.raw.push(key, value)?;
        Ok(Some((
            parsed.line,
            parsed.hits.is_some_and(|hits| hits > 0),
        )))
    }

    #[cfg(test)]
    pub(super) fn finish(self, req: &Request) -> Result<Sorted, Error> {
        self.finish_observed(req, |_, _, _, _, _, _, _, _| Ok(()))
    }

    pub(super) fn finish_observed(
        self,
        _req: &Request,
        mut observe: impl FnMut(&str, &str, u64, u64, u64, u64, u64, bool) -> Result<(), Error>,
    ) -> Result<Sorted, Error> {
        let Self {
            raw,
            mut store,
            tmpdir,
            budget,
            jobs,
        } = self;
        let part = budget / 3;
        let raw = raw.finish()?;
        let mut raw_cursor = raw.cursor()?;
        let mut rows = sort::Sorter::new(&tmpdir, part, jobs)?;

        let mut row = raw_cursor.next()?;
        let mut group: Option<Group> = None;
        let mut id = 0u64;
        let mut position = 0u64;
        while let Some(item) = row.take() {
            check_cancelled()?;
            let current = decode_raw(&item.key, &item.value)?;
            if group
                .as_ref()
                .is_none_or(|g| g.identity != current.identity)
            {
                if group.is_some() {
                    store.end()?;
                }
                id = id
                    .checked_add(1)
                    .ok_or_else(|| corrupt("block id overflow"))?;
                store.begin(id)?;
                position = 0;
                group = Some(Group {
                    identity: current.identity.clone(),
                });
            }
            let group_id = id;
            store.push_kind(current.kind as u8)?;
            let mut branch_key = Vec::with_capacity(16);
            branch_key.extend_from_slice(&group_id.to_be_bytes());
            branch_key.extend_from_slice(&position.to_be_bytes());
            rows.push(branch_key, encode_branch(&current, position)?)?;
            position = position
                .checked_add(1)
                .ok_or_else(|| corrupt("branch position overflow"))?;
            row = raw_cursor.next()?;
        }
        if group.is_some() {
            store.end()?;
        }
        drop(raw_cursor);
        drop(raw);

        let ranked = store.finish()?;
        let mut rank_cursor = ranked.cursor()?;
        let mut ranks = sort::Sorter::new(&tmpdir, part, 1)?;
        while let Some(rank) = rank_cursor.next()? {
            let mut key = Vec::with_capacity(8);
            key.extend_from_slice(&rank.id.to_be_bytes());
            let mut value = Vec::with_capacity(16);
            value.extend_from_slice(&rank.rank.to_be_bytes());
            value.extend_from_slice(&rank.length.to_be_bytes());
            ranks.push(key, value)?;
        }
        drop(rank_cursor);
        drop(ranked);
        let ranks = ranks.finish()?;

        let rows = rows.finish()?;
        let mut row_cursor = rows.cursor()?;
        let mut rank_cursor = ranks.cursor()?;
        let mut tagged = sort::Sorter::new(&tmpdir, part, jobs)?;
        let mut rank = rank_cursor.next()?;
        let mut row = row_cursor.next()?;
        while let Some(first) = row.take() {
            check_cancelled()?;
            let block_id = read_u64(&first.key, 0)?;
            let Some(r) = rank.as_ref() else {
                return Err(corrupt("missing signature rank for branch block"));
            };
            if r.key.len() != 8 || r.value.len() != 16 {
                return Err(corrupt("invalid signature rank frame"));
            }
            if read_u64(&r.key, 0)? != block_id {
                return Err(corrupt(
                    "signature rank ids are not aligned with branch rows",
                ));
            }
            let sig_rank = read_u64(&r.value, 0)?;
            let sig_length = read_u64(&r.value, 8)?;
            let mut expected_position = 0u64;
            let mut br = Some(first);
            while let Some(branch_row) = br.take() {
                if branch_row.key.len() != 16 {
                    return Err(corrupt("invalid branch row key"));
                }
                let position = read_u64(&branch_row.key, 8)?;
                if position != expected_position {
                    return Err(corrupt("branch block positions are not consecutive"));
                }
                expected_position = expected_position
                    .checked_add(1)
                    .ok_or_else(|| corrupt("branch position overflow"))?;
                let decoded = decode_branch(&branch_row.value)?;
                let mut key = Vec::with_capacity(decoded.source.len() + decoded.tc.len() + 58);
                key.extend_from_slice(decoded.source.as_bytes());
                key.push(0);
                key.extend_from_slice(decoded.tc.as_bytes());
                key.push(0);
                for n in [
                    decoded.input,
                    decoded.section,
                    decoded.line,
                    sig_rank,
                    block_id,
                    position,
                ] {
                    key.extend_from_slice(&n.to_be_bytes());
                }
                tagged.push(key, branch_row.value)?;
                let candidate = row_cursor.next()?;
                if candidate
                    .as_ref()
                    .is_some_and(|next| read_u64(&next.key, 0).ok() == Some(block_id))
                {
                    br = candidate;
                } else {
                    row = candidate;
                    break;
                }
            }
            if expected_position != sig_length {
                return Err(corrupt("signature length does not match branch rows"));
            }
            rank = rank_cursor.next()?;
        }
        if rank.is_some() {
            return Err(corrupt("orphan signature rank"));
        }
        drop(row_cursor);
        drop(rank_cursor);
        drop(rows);
        drop(ranks);
        let tagged = tagged.finish()?;

        let mut cursor = tagged.cursor()?;
        let mut fold = Fold::new(&tmpdir, budget, jobs)?;
        let mut row = cursor.next()?;
        let mut scope: Option<(String, String, u64, u64, u64)> = None;
        let mut sig: Option<u64> = None;
        let mut block: Option<u64> = None;
        let mut occurrence = 0u64;
        while let Some(item) = row.take() {
            check_cancelled()?;
            let tagged_row = decode_tagged(&item.key, &item.value)?;
            let current_scope = (
                tagged_row.source.clone(),
                tagged_row.tc.clone(),
                tagged_row.input,
                tagged_row.section,
                tagged_row.line,
            );
            let current_block = tagged_row.block_id;
            if scope.as_ref() != Some(&current_scope) || sig != Some(tagged_row.sig_rank) {
                scope = Some(current_scope);
                sig = Some(tagged_row.sig_rank);
                occurrence = 0;
                block = None;
            }
            if block != Some(current_block) {
                if block.is_some() {
                    occurrence = occurrence
                        .checked_add(1)
                        .ok_or_else(|| corrupt("signature occurrence overflow"))?;
                }
                block = Some(current_block);
            }
            observe(
                &tagged_row.source,
                &tagged_row.tc,
                tagged_row.input,
                tagged_row.line,
                tagged_row.sig_rank,
                occurrence,
                tagged_row.position,
                tagged_row.hits.is_some_and(|hits| hits > 0),
            )?;
            fold.push(
                &tagged_row.source,
                &tagged_row.tc,
                tagged_row.line,
                tagged_row.sig_rank,
                occurrence,
                tagged_row.position,
                tagged_row.input,
                tagged_row.section,
                tagged_row.ordinal,
                tagged_row.kind,
                tagged_row.unreachable,
                tagged_row.hits,
                &tagged_row.expression,
            )?;
            row = cursor.next()?;
        }
        drop(cursor);
        drop(tagged);
        fold.finish()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct Identity {
    source: String,
    tc: String,
    input: u64,
    section: u64,
    line: u64,
    block: u64,
}
struct Group {
    identity: Identity,
}
struct Parsed {
    line: u64,
    block: u64,
    kind: char,
    unreachable: bool,
    hits: Option<u64>,
    expression: String,
}
struct Raw {
    identity: Identity,
    ordinal: u64,
    kind: char,
    unreachable: bool,
    hits: Option<u64>,
    expression: String,
}
struct Branch {
    source: String,
    tc: String,
    input: u64,
    section: u64,
    line: u64,
}
struct Tagged {
    source: String,
    tc: String,
    input: u64,
    section: u64,
    line: u64,
    sig_rank: u64,
    block_id: u64,
    position: u64,
    ordinal: u64,
    kind: char,
    unreachable: bool,
    hits: Option<u64>,
    expression: String,
}

fn parse(payload: &str, label: &str, line_no: u64, req: &Request) -> Result<Parsed, Error> {
    let mut fields = payload.splitn(4, ',');
    let line = number(fields.next().unwrap_or(""), label, line_no)?;
    let block_field = fields.next().ok_or_else(|| {
        super::policy::at(
            super::policy::Kind::Format,
            label,
            line_no,
            "malformed BRDA record",
        )
    })?;
    let expression_count = fields
        .next()
        .zip(fields.next())
        .map(|(a, b)| format!("{a},{b}"))
        .ok_or_else(|| {
            super::policy::at(
                super::policy::Kind::Format,
                label,
                line_no,
                "malformed BRDA record",
            )
        })?;
    if line == 0 {
        return Err(super::policy::at(
            super::policy::Kind::Branch,
            label,
            line_no,
            "branch line must be positive",
        ));
    }
    let kind = match block_field.as_bytes().first() {
        Some(b'e') => 'e',
        Some(b'f') => 'f',
        _ => 'v',
    };
    let rest = if matches!(kind, 'e' | 'f') {
        &block_field[1..]
    } else {
        block_field
    };
    let unreachable = rest.starts_with('U');
    let block_text = rest.strip_prefix('U').unwrap_or(rest);
    let block = number(block_text, label, line_no)?;
    let (expression, count) = expression_count.rsplit_once(',').ok_or_else(|| {
        super::policy::at(
            super::policy::Kind::Format,
            label,
            line_no,
            "malformed BRDA count",
        )
    })?;
    if expression.is_empty() {
        return Err(super::policy::at(
            super::policy::Kind::Format,
            label,
            line_no,
            "empty BRDA expression",
        ));
    }
    let hits = if count == "-" {
        None
    } else {
        match count_value(count, label, line_no) {
            Ok(value) => Some(value),
            Err(error)
                if error.message.contains("negative coverage count")
                    && policy::can_recover(req, &error) =>
            {
                // The pinned oracle normalizes negative values to zero after
                // reporting an ignorable negative-count diagnostic.
                Some(0)
            }
            Err(error) => return Err(error),
        }
    };
    Ok(Parsed {
        line,
        block,
        kind,
        unreachable,
        hits,
        expression: expression.to_owned(),
    })
}

fn number(s: &str, label: &str, line: u64) -> Result<u64, Error> {
    if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
        return Err(super::policy::at(
            super::policy::Kind::Format,
            label,
            line,
            "invalid numeric LCOV field",
        ));
    }
    s.parse().map_err(|_| {
        super::policy::at(
            super::policy::Kind::Count,
            label,
            line,
            "numeric LCOV field overflow",
        )
    })
}
fn count_value(s: &str, label: &str, line: u64) -> Result<u64, Error> {
    if s.is_empty() {
        return Err(super::policy::at(
            super::policy::Kind::Format,
            label,
            line,
            "non-integer coverage count",
        ));
    }
    let digits = s.strip_prefix('+').unwrap_or(s);
    if !digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit()) {
        return Ok(digits.bytes().fold(0u64, |n, b| {
            n.saturating_mul(10)
                .saturating_add(u64::from(b - b'0'))
                .min(MAX_HITS)
        }));
    }
    let value = s.parse::<f64>().map_err(|_| {
        super::policy::at(
            super::policy::Kind::Format,
            label,
            line,
            "non-integer coverage count",
        )
    })?;
    if !value.is_finite() {
        return Err(super::policy::at(
            super::policy::Kind::Format,
            label,
            line,
            "non-integer coverage count",
        ));
    }
    if value < 0.0 {
        return Err(super::policy::at(
            super::policy::Kind::Negative,
            label,
            line,
            "negative coverage count",
        ));
    }
    Ok(if value >= MAX_HITS as f64 {
        MAX_HITS
    } else {
        value.trunc() as u64
    })
}
fn corrupt(message: impl std::fmt::Display) -> Error {
    Error::new(3, format!("corrupt BRDA spill: {message}"))
}
fn read_u64(bytes: &[u8], offset: usize) -> Result<u64, Error> {
    let slice = bytes
        .get(offset..offset + 8)
        .ok_or_else(|| corrupt("truncated integer"))?;
    Ok(u64::from_be_bytes(
        slice.try_into().map_err(|_| corrupt("truncated integer"))?,
    ))
}
fn decode_raw(key: &[u8], value: &[u8]) -> Result<Raw, Error> {
    let first = key
        .iter()
        .position(|b| *b == 0)
        .ok_or_else(|| corrupt("missing source delimiter"))?;
    let rest = &key[first + 1..];
    let second = rest
        .iter()
        .position(|b| *b == 0)
        .ok_or_else(|| corrupt("missing testcase delimiter"))?;
    let base = first + second + 2;
    if key.len() != base + 40 || value.len() < 11 {
        return Err(corrupt("invalid raw BRDA frame"));
    }
    let source = std::str::from_utf8(&key[..first])
        .map_err(|_| corrupt("invalid source UTF-8"))?
        .to_owned();
    let tc = std::str::from_utf8(&rest[..second])
        .map_err(|_| corrupt("invalid testcase UTF-8"))?
        .to_owned();
    let nums = [
        read_u64(key, base)?,
        read_u64(key, base + 8)?,
        read_u64(key, base + 16)?,
        read_u64(key, base + 24)?,
        read_u64(key, base + 32)?,
    ];
    if !matches!(value[0], b'e' | b'f' | b'v') || value[1] > 1 || value[2] > 1 {
        return Err(corrupt("invalid raw BRDA value"));
    }
    let count = read_u64(value, 3)?;
    if count > MAX_HITS || (value[2] == 0 && count != 0) {
        return Err(corrupt("invalid raw BRDA count"));
    }
    let expression = std::str::from_utf8(&value[11..])
        .map_err(|_| corrupt("invalid expression UTF-8"))?
        .to_owned();
    Ok(Raw {
        identity: Identity {
            source,
            tc,
            input: nums[0],
            section: nums[1],
            line: nums[2],
            block: nums[3],
        },
        ordinal: nums[4],
        kind: value[0] as char,
        unreachable: value[1] != 0,
        hits: (value[2] != 0).then_some(read_u64(value, 3)?),
        expression,
    })
}
fn encode_branch(row: &Raw, position: u64) -> Result<Vec<u8>, Error> {
    let mut out = Vec::with_capacity(
        row.identity.source.len() + row.identity.tc.len() + row.expression.len() + 76,
    );
    put_string(&mut out, &row.identity.source)?;
    put_string(&mut out, &row.identity.tc)?;
    for n in [
        row.identity.input,
        row.identity.section,
        row.identity.line,
        row.ordinal,
        position,
    ] {
        out.extend_from_slice(&n.to_be_bytes());
    }
    out.push(row.kind as u8);
    out.push(u8::from(row.unreachable));
    out.push(u8::from(row.hits.is_some()));
    out.extend_from_slice(&row.hits.unwrap_or(0).to_be_bytes());
    out.extend_from_slice(row.expression.as_bytes());
    Ok(out)
}
fn decode_branch(value: &[u8]) -> Result<Branch, Error> {
    let (mut at, source) = get_string(value, 0)?;
    let (_, tc) = get_string(value, at)?;
    at += 4 + tc.len();
    let input = read_u64(value, at)?;
    let section = read_u64(value, at + 8)?;
    let line = read_u64(value, at + 16)?;
    let _ordinal = read_u64(value, at + 24)?;
    let pos = at + 40;
    if value.len() < pos + 11 {
        return Err(corrupt("truncated branch row"));
    }
    let count = read_u64(value, pos + 3)?;
    if !matches!(value[pos], b'e' | b'f' | b'v')
        || value[pos + 1] > 1
        || value[pos + 2] > 1
        || count > MAX_HITS
        || (value[pos + 2] == 0 && count != 0)
    {
        return Err(corrupt("invalid branch row value"));
    }
    std::str::from_utf8(&value[pos + 11..]).map_err(|_| corrupt("invalid expression UTF-8"))?;
    Ok(Branch {
        source,
        tc,
        input,
        section,
        line,
    })
}
fn put_string(out: &mut Vec<u8>, s: &str) -> Result<(), Error> {
    let n = u32::try_from(s.len()).map_err(|_| corrupt("identity too long"))?;
    out.extend_from_slice(&n.to_be_bytes());
    out.extend_from_slice(s.as_bytes());
    Ok(())
}
fn get_string(value: &[u8], at: usize) -> Result<(usize, String), Error> {
    let n = u32::from_be_bytes(
        value
            .get(at..at + 4)
            .ok_or_else(|| corrupt("truncated string length"))?
            .try_into()
            .map_err(|_| corrupt("truncated string length"))?,
    ) as usize;
    let end = at
        .checked_add(4 + n)
        .ok_or_else(|| corrupt("string length overflow"))?;
    let s = std::str::from_utf8(
        value
            .get(at + 4..end)
            .ok_or_else(|| corrupt("truncated string"))?,
    )
    .map_err(|_| corrupt("invalid string UTF-8"))?
    .to_owned();
    Ok((end, s))
}
fn decode_tagged(key: &[u8], value: &[u8]) -> Result<Tagged, Error> {
    let a = key
        .iter()
        .position(|b| *b == 0)
        .ok_or_else(|| corrupt("missing tagged source delimiter"))?;
    let r = &key[a + 1..];
    let b = r
        .iter()
        .position(|x| *x == 0)
        .ok_or_else(|| corrupt("missing tagged testcase delimiter"))?;
    let base = a + b + 2;
    if key.len() != base + 48 {
        return Err(corrupt("invalid tagged key"));
    }
    let source = std::str::from_utf8(&key[..a])
        .map_err(|_| corrupt("invalid source UTF-8"))?
        .to_owned();
    let tc = std::str::from_utf8(&r[..b])
        .map_err(|_| corrupt("invalid testcase UTF-8"))?
        .to_owned();
    let nums = [
        read_u64(key, base)?,
        read_u64(key, base + 8)?,
        read_u64(key, base + 16)?,
        read_u64(key, base + 24)?,
        read_u64(key, base + 32)?,
        read_u64(key, base + 40)?,
    ];
    let (source_end, _) = get_string(value, 0)?;
    let (strings_end, _) = get_string(value, source_end)?;
    let at = strings_end;
    let ordinal = read_u64(value, at + 24)?;
    let pos = at + 40;
    if value.len() < pos + 11 {
        return Err(corrupt("truncated tagged value"));
    }
    let count = read_u64(value, pos + 3)?;
    if !matches!(value[pos], b'e' | b'f' | b'v')
        || value[pos + 1] > 1
        || value[pos + 2] > 1
        || count > MAX_HITS
        || (value[pos + 2] == 0 && count != 0)
    {
        return Err(corrupt("invalid tagged value"));
    }
    let kind = value[pos] as char;
    let unreachable = value[pos + 1] != 0;
    let hits = if value[pos + 2] != 0 {
        Some(read_u64(value, pos + 3)?)
    } else {
        None
    };
    let expression = std::str::from_utf8(&value[pos + 11..])
        .map_err(|_| corrupt("invalid expression UTF-8"))?
        .to_owned();
    Ok(Tagged {
        source,
        tc,
        input: nums[0],
        section: nums[1],
        line: nums[2],
        sig_rank: nums[3],
        block_id: nums[4],
        position: nums[5],
        ordinal,
        kind,
        unreachable,
        hits,
        expression,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn values(sorted: &Sorted) -> Vec<Vec<u8>> {
        let mut cursor = sorted.cursor().unwrap();
        let mut values = Vec::new();
        while let Some(row) = cursor.next().unwrap() {
            values.push(row.value);
        }
        values
    }

    #[test]
    fn matches_signatures_by_occurrence_and_sorts_sparse_blocks() {
        let tmp = tempfile::tempdir().unwrap();
        let req = Request::default();
        let mut reducer = Reducer::new(tmp.path(), 1 << 20, 2).unwrap();
        let rows = [
            (0, 5, "1,5,a,1"),
            (0, 5, "1,5,b,1"),
            (0, 9, "1,9,c,0"),
            (0, 9, "1,9,d,0"),
            (0, 100_000, "1,eU100000,x,2"),
            (0, 100_000, "1,eU100000,y,0"),
            (1, 700_000, "1,700000,a2,2"),
            (1, 700_000, "1,700000,b2,0"),
            (1, 800_000, "1,eU800000,x2,1"),
            (1, 800_000, "1,eU800000,y2,4"),
            (1, 900_000, "1,900000,c2,3"),
            (1, 900_000, "1,900000,d2,5"),
        ];
        for (index, (input, block, payload)) in rows.into_iter().enumerate() {
            let payload = payload.replace("BLOCK", &block.to_string());
            let metadata = reducer
                .push(
                    "x.c",
                    "tc",
                    input,
                    0,
                    index as u64,
                    &payload,
                    "trace.info",
                    index as u64 + 1,
                    &req,
                )
                .unwrap();
            assert!(metadata.is_some());
        }
        let sorted = reducer.finish(&req).unwrap();
        assert_eq!(
            values(&sorted),
            [
                b"BRDA:1,0,a,3".to_vec(),
                b"BRDA:1,0,b,1".to_vec(),
                b"BRDA:1,1,c,3".to_vec(),
                b"BRDA:1,1,d,5".to_vec(),
                b"BRDA:1,eU2,x,3".to_vec(),
                b"BRDA:1,eU2,y,4".to_vec(),
                b"4,4".to_vec(),
            ]
        );
        drop(sorted);
        assert_eq!(std::fs::read_dir(tmp.path()).unwrap().count(), 0);
    }

    #[test]
    fn huge_single_block_spills_and_malformed_input_cleans_up() {
        let tmp = tempfile::tempdir().unwrap();
        let req = Request::default();
        let mut reducer = Reducer::new(tmp.path(), 64 * 1024, 1).unwrap();
        let mut payload_bytes = 0usize;
        for position in (0..2400u64).rev() {
            let payload = format!("1,999999999,{position},1");
            payload_bytes += 6 + 1 + 2 + 1 + 40 + 11 + position.to_string().len() + 24;
            reducer
                .push(
                    "large.c",
                    "tc",
                    0,
                    0,
                    position,
                    &payload,
                    "large.info",
                    position + 1,
                    &req,
                )
                .unwrap();
        }
        assert!(payload_bytes > 3 * 64 * 1024);
        let sorted = reducer.finish(&req).unwrap();
        assert_eq!(values(&sorted).len(), 2401);
        drop(sorted);
        assert_eq!(std::fs::read_dir(tmp.path()).unwrap().count(), 0);

        let mut reducer = Reducer::new(tmp.path(), 64 * 1024, 1).unwrap();
        assert!(reducer
            .push("bad.c", "", 0, 0, 0, "1,0,expr,-1", "bad.info", 7, &req)
            .is_err());
        drop(reducer);
        assert_eq!(std::fs::read_dir(tmp.path()).unwrap().count(), 0);
    }

    #[test]
    fn parses_exception_kind_unknown_kind_commas_and_signed_count_limit() {
        let tmp = tempfile::tempdir().unwrap();
        let req = Request::default();
        let mut reducer = Reducer::new(tmp.path(), 1 << 20, 1).unwrap();
        reducer
            .push(
                "x.c",
                "tc",
                0,
                0,
                1,
                "1,fU999999999,exception,expr,9223372036854775808",
                "trace.info",
                1,
                &req,
            )
            .unwrap();
        reducer
            .push(
                "x.c",
                "tc",
                0,
                0,
                2,
                "1,700000,unknown,expr,+3",
                "trace.info",
                2,
                &req,
            )
            .unwrap();
        let sorted = reducer.finish(&req).unwrap();
        assert_eq!(
            values(&sorted),
            [
                b"BRDA:1,0,unknown,expr,3".to_vec(),
                b"BRDA:1,fU1,exception,expr,9223372036854775807".to_vec(),
                b"1,1".to_vec(),
            ]
        );
    }

    #[test]
    fn returns_consistency_metadata_and_recovers_negative_counts_as_zero() {
        let tmp = tempfile::tempdir().unwrap();
        let mut req = Request::default();
        req.ignore_errors.insert("negative".to_owned());
        req.ignore_errors.insert("format".to_owned());
        let mut reducer = Reducer::new(tmp.path(), 1 << 20, 1).unwrap();
        assert_eq!(
            reducer
                .push(
                    "x.c",
                    "tc",
                    0,
                    0,
                    0,
                    "8,U900000,x,-2",
                    "trace.info",
                    1,
                    &req
                )
                .unwrap(),
            Some((8, false))
        );
        assert_eq!(
            reducer
                .push("x.c", "tc", 0, 0, 1, "8,1,x,3", "trace.info", 2, &req)
                .unwrap(),
            Some((8, true))
        );
        assert_eq!(
            reducer
                .push("x.c", "tc", 0, 0, 2, "8,2,x,-", "trace.info", 3, &req)
                .unwrap(),
            Some((8, false))
        );
        assert_eq!(
            reducer
                .push("x.c", "tc", 0, 0, 3, "8,0", "trace.info", 4, &req)
                .unwrap(),
            None
        );
        let sorted = reducer.finish(&req).unwrap();
        assert_eq!(
            values(&sorted),
            [
                b"BRDA:8,0,x,3".to_vec(),
                b"BRDA:8,1,x,-".to_vec(),
                b"BRDA:8,U2,x,0".to_vec(),
                b"2,1".to_vec(),
            ]
        );
    }
}
