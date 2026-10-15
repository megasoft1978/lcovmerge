//! Bounded canonical fold for already signature-ranked BRDA records.
use super::sort::{self, Sorted};
use crate::common::{check_cancelled, Error};
use std::path::{Path, PathBuf};

const MAX_HITS: u64 = i64::MAX as u64;

/// Rank bytes used by the compatibility executor's final output sorter.
#[derive(Clone, Copy, Debug)]
pub(super) struct OutputLayout {
    pub branch_rank: u8,
    pub summary_rank: u8,
}

impl Default for OutputLayout {
    fn default() -> Self {
        Self {
            branch_rank: 3,
            summary_rank: 4,
        }
    }
}

pub(super) struct Fold {
    sorter: sort::Sorter,
    tmpdir: PathBuf,
    stage_budget: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct Branch {
    source: String,
    testcase: String,
    line: u64,
    sig_rank: u64,
    occurrence: u64,
    position: u64,
    input: u64,
    section: u64,
    ordinal: u64,
    kind: u8,
    unreachable: bool,
    hits: Option<u64>,
    expression: String,
}

impl Fold {
    pub(super) fn new(tmpdir: &Path, budget: usize, jobs: usize) -> Result<Self, Error> {
        let stage_budget = budget / 2;
        Ok(Self {
            sorter: sort::Sorter::new(tmpdir, stage_budget, jobs)?,
            tmpdir: tmpdir.to_path_buf(),
            stage_budget,
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn push(
        &mut self,
        source: &str,
        tc: &str,
        line: u64,
        sig_rank: u64,
        occurrence: u64,
        position: u64,
        input: u64,
        section: u64,
        ordinal: u64,
        kind: char,
        unreachable: bool,
        hits: Option<u64>,
        expression: &str,
    ) -> Result<(), Error> {
        check_cancelled()?;
        if source.as_bytes().contains(&0) || tc.as_bytes().contains(&0) {
            return Err(corrupt("NUL in branch output identity"));
        }
        if line == 0 || !matches!(kind, 'e' | 'f' | 'v' | 'b') {
            return Err(corrupt("invalid branch row"));
        }
        let kind = kind as u8;
        let mut key = Vec::with_capacity(source.len() + tc.len() + 58);
        key.extend_from_slice(source.as_bytes());
        key.push(0);
        key.extend_from_slice(tc.as_bytes());
        key.push(0);
        for n in [
            line, sig_rank, occurrence, position, input, section, ordinal,
        ] {
            key.extend_from_slice(&n.to_be_bytes());
        }
        let mut value = Vec::with_capacity(expression.len() + 11);
        value.push(kind);
        value.push(u8::from(unreachable));
        match hits {
            Some(n) => {
                value.push(1);
                value.extend_from_slice(&n.min(MAX_HITS).to_be_bytes());
            }
            None => {
                value.push(0);
                value.extend_from_slice(&0u64.to_be_bytes());
            }
        }
        value.extend_from_slice(expression.as_bytes());
        self.sorter.push(key, value)
    }

    pub(super) fn finish(self) -> Result<Sorted, Error> {
        self.finish_with_layout(OutputLayout::default())
    }

    pub(super) fn finish_with_layout(self, layout: OutputLayout) -> Result<Sorted, Error> {
        if layout.branch_rank == layout.summary_rank {
            return Err(corrupt("branch and summary ranks must differ"));
        }
        let input = self.sorter.finish()?;
        let mut cursor = input.cursor()?;
        let mut output = sort::Sorter::new(&self.tmpdir, self.stage_budget, 1)?;
        let mut next = cursor.next()?;
        let mut active_testcase: Option<(String, String)> = None;
        let mut found = 0u64;
        let mut hit = 0u64;
        let mut block: Option<(String, String, u64, u64, u64)> = None;
        let mut block_id = 0u64;
        let mut expected_position = 0u64;

        while let Some(row) = next.take() {
            check_cancelled()?;
            let current = decode(&row.key, &row.value)?;
            let testcase = (current.source.clone(), current.testcase.clone());
            if active_testcase.as_ref() != Some(&testcase) {
                if let Some((source, tc)) = active_testcase.take() {
                    push_summary(&mut output, &source, &tc, layout, found, hit)?;
                }
                active_testcase = Some(testcase);
                found = 0;
                hit = 0;
                block = None;
                block_id = 0;
                expected_position = 0;
            }
            let identity = (
                current.source.clone(),
                current.testcase.clone(),
                current.line,
                current.sig_rank,
                current.occurrence,
            );
            if block.as_ref() != Some(&identity) {
                if block.as_ref().is_some_and(|old| old.2 == current.line) {
                    block_id = block_id
                        .checked_add(1)
                        .ok_or_else(|| corrupt("block id overflow"))?;
                } else {
                    block_id = 0;
                }
                block = Some(identity);
                expected_position = 0;
            }
            if current.position != expected_position {
                return Err(corrupt("branch positions are not consecutive"));
            }
            expected_position = expected_position
                .checked_add(1)
                .ok_or_else(|| corrupt("branch position overflow"))?;

            let mut aggregate = current;
            next = cursor.next()?;
            while let Some(row) = next.as_ref() {
                let candidate = decode(&row.key, &row.value)?;
                if (
                    candidate.source.as_str(),
                    candidate.testcase.as_str(),
                    candidate.line,
                    candidate.sig_rank,
                    candidate.occurrence,
                    candidate.position,
                ) != (
                    aggregate.source.as_str(),
                    aggregate.testcase.as_str(),
                    aggregate.line,
                    aggregate.sig_rank,
                    aggregate.occurrence,
                    aggregate.position,
                ) {
                    break;
                }
                fold_value(&mut aggregate, candidate);
                next = cursor.next()?;
            }
            if !aggregate.unreachable {
                found = found.saturating_add(1).min(MAX_HITS);
                hit = hit
                    .saturating_add(u64::from(aggregate.hits.is_some_and(|n| n > 0)))
                    .min(MAX_HITS);
            }
            let prefix = key_prefix(&aggregate.source, &aggregate.testcase)?;
            let mut key = prefix;
            key.push(layout.branch_rank);
            key.extend_from_slice(&aggregate.line.to_be_bytes());
            key.extend_from_slice(&block_id.to_be_bytes());
            key.extend_from_slice(&aggregate.position.to_be_bytes());
            let kind = match aggregate.kind {
                b'e' => "e",
                b'f' => "f",
                b'v' | b'b' => "",
                _ => return Err(corrupt("invalid branch kind")),
            };
            let u = if aggregate.unreachable { "U" } else { "" };
            let hits = aggregate
                .hits
                .map_or_else(|| "-".to_owned(), |n| n.to_string());
            let value = format!(
                "BRDA:{},{kind}{u}{block_id},{},{}",
                aggregate.line, aggregate.expression, hits
            )
            .into_bytes();
            output.push(key, value)?;
        }
        if let Some((source, tc)) = active_testcase {
            push_summary(&mut output, &source, &tc, layout, found, hit)?;
        }
        drop(cursor);
        drop(input);
        output.finish()
    }
}

fn key_prefix(source: &str, testcase: &str) -> Result<Vec<u8>, Error> {
    if source.as_bytes().contains(&0) || testcase.as_bytes().contains(&0) {
        return Err(corrupt("NUL in branch output identity"));
    }
    let mut key = Vec::with_capacity(source.len() + testcase.len() + 3);
    key.extend_from_slice(source.as_bytes());
    key.push(0);
    key.extend_from_slice(testcase.as_bytes());
    key.push(0);
    Ok(key)
}

fn push_summary(
    output: &mut sort::Sorter,
    source: &str,
    testcase: &str,
    layout: OutputLayout,
    found: u64,
    hit: u64,
) -> Result<(), Error> {
    if found == 0 {
        return Ok(());
    }
    let mut key = key_prefix(source, testcase)?;
    key.push(layout.summary_rank);
    output.push(key, format!("{found},{hit}").into_bytes())
}

fn decode(key: &[u8], value: &[u8]) -> Result<Branch, Error> {
    let first = key
        .iter()
        .position(|b| *b == 0)
        .ok_or_else(|| corrupt("missing source delimiter"))?;
    let rest = &key[first + 1..];
    let second = rest
        .iter()
        .position(|b| *b == 0)
        .ok_or_else(|| corrupt("missing testcase delimiter"))?;
    let prefix_len = first + 1 + second + 1;
    if key.len() != prefix_len + 56 {
        return Err(corrupt("invalid branch key length"));
    }
    let source = std::str::from_utf8(&key[..first]).map_err(|_| corrupt("invalid source UTF-8"))?;
    let testcase =
        std::str::from_utf8(&rest[..second]).map_err(|_| corrupt("invalid testcase UTF-8"))?;
    let mut nums = [0u64; 7];
    for (index, n) in nums.iter_mut().enumerate() {
        let start = prefix_len + index * 8;
        *n = u64::from_be_bytes(
            key[start..start + 8]
                .try_into()
                .map_err(|_| corrupt("invalid branch key number"))?,
        );
    }
    if nums[0] == 0
        || value.len() < 11
        || !matches!(value[0], b'e' | b'f' | b'v' | b'b')
        || value[1] > 1
        || value[2] > 1
    {
        return Err(corrupt("invalid branch value"));
    }
    let raw_hits = u64::from_be_bytes(
        value[3..11]
            .try_into()
            .map_err(|_| corrupt("invalid branch count"))?,
    );
    if raw_hits > MAX_HITS || (value[2] == 0 && raw_hits != 0) {
        return Err(corrupt("absent branch count has a nonzero payload"));
    }
    let expression = std::str::from_utf8(&value[11..])
        .map_err(|_| corrupt("invalid expression UTF-8"))?
        .to_owned();
    Ok(Branch {
        source: source.to_owned(),
        testcase: testcase.to_owned(),
        line: nums[0],
        sig_rank: nums[1],
        occurrence: nums[2],
        position: nums[3],
        input: nums[4],
        section: nums[5],
        ordinal: nums[6],
        kind: value[0],
        unreachable: value[1] != 0,
        hits: (value[2] != 0).then_some(raw_hits),
        expression,
    })
}

fn fold_value(first: &mut Branch, next: Branch) {
    if (next.input, next.section, next.ordinal) < (first.input, first.section, first.ordinal) {
        first.kind = next.kind;
        first.expression = next.expression.clone();
    }
    first.unreachable |= next.unreachable;
    first.hits = match (first.hits, next.hits) {
        (None, None) => None,
        (Some(a), Some(b)) => Some(a.saturating_add(b).min(MAX_HITS)),
        (None, Some(b)) | (Some(b), None) => Some(b),
    };
}

fn corrupt(message: &str) -> Error {
    Error::new(3, format!("corrupt branch fold row: {message}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn read_all(sorted: &Sorted) -> Vec<(Vec<u8>, Vec<u8>)> {
        let mut cursor = sorted.cursor().unwrap();
        let mut rows = Vec::new();
        while let Some(row) = cursor.next().unwrap() {
            rows.push((row.key, row.value));
        }
        rows
    }

    fn identity(key: &[u8]) -> (&str, &str, u8, u64, u64, u64) {
        let a = key.iter().position(|b| *b == 0).unwrap();
        let b = key[a + 1..].iter().position(|b| *b == 0).unwrap() + a + 1;
        let n = b + 1;
        let u = |i: usize| {
            u64::from_be_bytes(key[n + 1 + i * 8..n + 1 + (i + 1) * 8].try_into().unwrap())
        };
        (
            std::str::from_utf8(&key[..a]).unwrap(),
            std::str::from_utf8(&key[a + 1..b]).unwrap(),
            key[n],
            u(0),
            u(1),
            u(2),
        )
    }

    fn rank(key: &[u8]) -> u8 {
        let first = key.iter().position(|b| *b == 0).unwrap();
        let second = key[first + 1..].iter().position(|b| *b == 0).unwrap() + first + 1;
        key[second + 1]
    }

    #[test]
    fn folds_matching_occurrences_and_keeps_distinct_occurrences() {
        let tmp = tempfile::tempdir().unwrap();
        let mut fold = Fold::new(tmp.path(), 1 << 20, 2).unwrap();
        fold.push("s.c", "tc", 7, 0, 0, 0, 1, 0, 4, 'b', false, Some(2), "a")
            .unwrap();
        fold.push(
            "s.c",
            "tc",
            7,
            0,
            0,
            0,
            0,
            0,
            9,
            'v',
            false,
            Some(3),
            "first,expr",
        )
        .unwrap();
        fold.push(
            "s.c",
            "tc",
            7,
            0,
            1,
            0,
            0,
            0,
            10,
            'e',
            false,
            Some(4),
            "other",
        )
        .unwrap();
        let sorted = fold.finish().unwrap();
        let rows = read_all(&sorted);
        assert_eq!(rows.len(), 3);
        assert_eq!(identity(&rows[0].0), ("s.c", "tc", 3, 7, 0, 0));
        assert_eq!(rows[0].1, b"BRDA:7,0,first,expr,5");
        assert_eq!(identity(&rows[1].0), ("s.c", "tc", 3, 7, 1, 0));
        assert_eq!(rows[1].1, b"BRDA:7,e1,other,4");
        assert_eq!(rank(&rows[2].0), 4);
        assert_eq!(rows[2].1, b"2,2");
    }

    #[test]
    fn absent_zero_saturating_counts_kinds_and_unreachable_summary() {
        let tmp = tempfile::tempdir().unwrap();
        let mut fold = Fold::new(tmp.path(), 1 << 20, 1).unwrap();
        fold.push(
            "s",
            "",
            1,
            0,
            0,
            0,
            1,
            0,
            0,
            'f',
            false,
            Some(u64::MAX),
            "f",
        )
        .unwrap();
        fold.push("s", "", 1, 0, 0, 0, 0, 0, 0, 'e', false, Some(7), "e")
            .unwrap();
        fold.push("s", "", 1, 0, 0, 1, 0, 0, 1, 'v', false, None, "absent")
            .unwrap();
        fold.push("s", "", 1, 0, 0, 1, 1, 0, 1, 'b', false, Some(0), "zero")
            .unwrap();
        fold.push("s", "", 1, 0, 0, 2, 0, 0, 2, 'e', true, Some(9), "U")
            .unwrap();
        let rows = read_all(&fold.finish().unwrap());
        assert_eq!(rows[0].1, format!("BRDA:1,e0,e,{}", i64::MAX).as_bytes());
        assert_eq!(rows[1].1, b"BRDA:1,0,absent,0");
        assert_eq!(rows[2].1, b"BRDA:1,eU0,U,9");
        assert_eq!(rows[3].1, b"2,1");
    }

    #[test]
    fn huge_single_block_streams_with_bounded_sort_buffers_and_cleans_up() {
        let tmp = tempfile::tempdir().unwrap();
        let mut fold = Fold::new(tmp.path(), 64 << 10, 1).unwrap();
        for position in (0..2048u64).rev() {
            fold.push(
                "src",
                "tc",
                10,
                0,
                0,
                position,
                0,
                0,
                position,
                'b',
                false,
                Some(1),
                "expr",
            )
            .unwrap();
        }
        let sorted = fold.finish().unwrap();
        let rows = read_all(&sorted);
        assert_eq!(rows.len(), 2049);
        assert_eq!(rows[0].1, b"BRDA:10,0,expr,1");
        assert_eq!(rows[2047].1, b"BRDA:10,0,expr,1");
        assert_eq!(rows[2048].1, b"2048,2048");
        drop(sorted);
        assert_eq!(fs::read_dir(tmp.path()).unwrap().count(), 0);
    }

    #[test]
    fn reports_custom_rank_layout_and_rejects_corrupt_rows() {
        let tmp = tempfile::tempdir().unwrap();
        let mut fold = Fold::new(tmp.path(), 1 << 20, 1).unwrap();
        fold.push("s", "t", 3, 0, 0, 0, 0, 0, 0, 'b', false, None, "x")
            .unwrap();
        let rows = read_all(
            &fold
                .finish_with_layout(OutputLayout {
                    branch_rank: 5,
                    summary_rank: 6,
                })
                .unwrap(),
        );
        assert_eq!(identity(&rows[0].0).2, 5);
        assert_eq!(rank(&rows[1].0), 6);
        assert!(decode(b"bad", b"").is_err());
        assert!(decode(b"s\0t\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0", b"").is_err());
    }
}
