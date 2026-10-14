//! Bounded source-wide branch/DA validation before and after input merging.
use super::{function_check, policy, sort, Request};
use crate::common::{check_cancelled, Error};
use std::path::{Path, PathBuf};

pub(super) struct Checker {
    rows: sort::Sorter,
    tmpdir: PathBuf,
    each: usize,
    jobs: usize,
    last_multiple: Option<(String, u64)>,
}
struct Scope {
    key: Vec<u8>,
    testcase: Option<String>,
    multiple: bool,
}
struct Line {
    number: u64,
    da: Option<bool>,
    branches: bool,
    hit_count: u64,
    branch: Option<(u64, u64, u64)>,
    branch_hit: bool,
}
impl Line {
    fn new(number: u64) -> Self {
        Self {
            number,
            da: None,
            branches: false,
            hit_count: 0,
            branch: None,
            branch_hit: false,
        }
    }
    fn fold_branch(&mut self) {
        self.hit_count = self.hit_count.saturating_add(u64::from(self.branch_hit));
        self.branch_hit = false;
    }
}
impl Checker {
    pub(super) fn new(tmpdir: &Path, budget: usize, jobs: usize) -> Result<Self, Error> {
        let each = budget / 2;
        Ok(Self {
            rows: sort::Sorter::new(tmpdir, each, jobs)?,
            tmpdir: tmpdir.to_owned(),
            each,
            jobs,
            last_multiple: None,
        })
    }
    pub(super) fn mark_multiple(&mut self, source: &str, input: u64) -> Result<(), Error> {
        if self
            .last_multiple
            .as_ref()
            .is_some_and(|(old, id)| old == source && *id == input)
        {
            return Ok(());
        }
        let mut key = function_check::prefix(source, input)?;
        key.push(0);
        put_string(&mut key, "")?;
        self.rows.push(key, vec![1])?;
        self.last_multiple = Some((source.to_owned(), input));
        Ok(())
    }
    pub(super) fn push_line(
        &mut self,
        source: &str,
        tc: &str,
        input: u64,
        line: u64,
        hits: u64,
    ) -> Result<(), Error> {
        let mut header = function_check::prefix(source, input)?;
        header.push(0);
        put_string(&mut header, tc)?;
        self.rows.push(header, Vec::new())?;
        let mut key = function_check::prefix(source, input)?;
        key.push(1);
        key.extend_from_slice(&line.to_be_bytes());
        key.push(0);
        self.rows.push(key, vec![u8::from(hits > 0)])
    }
    #[allow(clippy::too_many_arguments)]
    pub(super) fn push_branch(
        &mut self,
        source: &str,
        tc: &str,
        input: u64,
        line: u64,
        signature: u64,
        occurrence: u64,
        position: u64,
        hit: bool,
    ) -> Result<(), Error> {
        let mut key = function_check::prefix(source, input)?;
        key.push(1);
        key.extend_from_slice(&line.to_be_bytes());
        key.push(1);
        for n in [signature, occurrence, position] {
            key.extend_from_slice(&n.to_be_bytes());
        }
        put_string(&mut key, tc)?;
        self.rows.push(key, vec![u8::from(hit)])
    }
    /// Generated rows encode source/input/line, then serialization flag,
    /// count, and testcase. Summary-only rows are retained for global checks.
    pub(super) fn finish(self, req: &Request) -> Result<sort::Sorted, Error> {
        let mut generated = sort::Sorter::new(&self.tmpdir, self.each, self.jobs)?;
        if !req.check_data_consistency {
            return generated.finish();
        }
        let input = self.rows.finish()?;
        let mut cursor = input.cursor()?;
        let mut global = sort::Sorter::new(&self.tmpdir, self.each, self.jobs)?;
        walk(&mut cursor, req, Some(&mut global), &mut generated)?;
        drop(cursor);
        drop(input);
        let global = global.finish()?;
        let mut cursor = global.cursor()?;
        walk(&mut cursor, req, None, &mut generated)?;
        drop(cursor);
        drop(global);
        generated.finish()
    }
}
fn walk(
    cursor: &mut sort::Cursor,
    req: &Request,
    mut global: Option<&mut sort::Sorter>,
    generated: &mut sort::Sorter,
) -> Result<(), Error> {
    let mut scope: Option<Scope> = None;
    let mut line: Option<Line> = None;
    let mut current_branch_key: Option<Vec<u8>> = None;
    let mut current_branch_hit = false;
    while let Some(row) = cursor.next()? {
        check_cancelled()?;
        let (identity, source, input, mut key) = decode_scope(&row.key)?;
        if scope.as_ref().is_none_or(|s| s.key != identity) {
            relay_branch(
                &mut global,
                &mut current_branch_key,
                &mut current_branch_hit,
            )?;
            if let (Some(scope), Some(line)) = (scope.as_ref(), line.take()) {
                finish_line(scope, line, req, &mut global, generated)?;
            }
            scope = Some(Scope {
                key: identity,
                testcase: None,
                multiple: false,
            });
        }
        let rank = byte(&mut key)?;
        if rank == 0 {
            let tc = string(&mut key)?;
            if key.is_empty() && row.value == [1] {
                scope.as_mut().unwrap().multiple = true;
                continue;
            }
            if !key.is_empty() || !row.value.is_empty() {
                return Err(corrupt());
            }
            let s = scope.as_mut().unwrap();
            if s.testcase.as_ref().is_some_and(|old| old != &tc) {
                s.multiple = true;
            }
            if s.testcase.is_none() {
                s.testcase = Some(tc);
            }
            continue;
        }
        if rank != 1 || row.value.len() != 1 || row.value[0] > 1 {
            return Err(corrupt());
        }
        let number = u64_field(&mut key)?;
        let kind = byte(&mut key)?;
        if line.as_ref().is_some_and(|l| l.number != number) {
            relay_branch(
                &mut global,
                &mut current_branch_key,
                &mut current_branch_hit,
            )?;
            finish_line(
                scope.as_ref().unwrap(),
                line.take().unwrap(),
                req,
                &mut global,
                generated,
            )?;
        }
        let l = line.get_or_insert_with(|| Line::new(number));
        if kind == 0 {
            if !key.is_empty() {
                return Err(corrupt());
            }
            l.da = Some(l.da.unwrap_or(false) || row.value[0] != 0);
        } else if kind == 1 {
            let signature = u64_field(&mut key)?;
            let occurrence = u64_field(&mut key)?;
            let position = u64_field(&mut key)?;
            let _tc = string(&mut key)?;
            if !key.is_empty() {
                return Err(corrupt());
            }
            let branch = (signature, occurrence, position);
            if l.branch.is_some_and(|old| old != branch) {
                l.fold_branch();
            }
            l.branch = Some(branch);
            l.branch_hit |= row.value[0] != 0;
            l.branches = true;
            // Preserve canonical identity for the merged source-wide pass.
            let mut global_key = function_check::prefix(&source, 0)?;
            global_key.push(1);
            global_key.extend_from_slice(&number.to_be_bytes());
            global_key.push(1);
            for n in [signature, occurrence, position] {
                global_key.extend_from_slice(&n.to_be_bytes());
            }
            put_string(&mut global_key, "")?;
            if current_branch_key
                .as_ref()
                .is_some_and(|old| old != &global_key)
            {
                relay_branch(
                    &mut global,
                    &mut current_branch_key,
                    &mut current_branch_hit,
                )?;
            }
            current_branch_key = Some(global_key);
            current_branch_hit |= row.value[0] != 0;
        } else {
            return Err(corrupt());
        }
        let _ = input;
    }
    relay_branch(
        &mut global,
        &mut current_branch_key,
        &mut current_branch_hit,
    )?;
    if let (Some(scope), Some(line)) = (scope.as_ref(), line) {
        finish_line(scope, line, req, &mut global, generated)?;
    }
    Ok(())
}
fn relay_branch(
    global: &mut Option<&mut sort::Sorter>,
    key: &mut Option<Vec<u8>>,
    hit: &mut bool,
) -> Result<(), Error> {
    if let (Some(global), Some(key)) = (global.as_deref_mut(), key.take()) {
        global.push(key, vec![u8::from(*hit)])?;
    }
    *hit = false;
    Ok(())
}
fn finish_line(
    scope: &Scope,
    mut line: Line,
    req: &Request,
    global: &mut Option<&mut sort::Sorter>,
    generated: &mut sort::Sorter,
) -> Result<(), Error> {
    if line.branch.is_some() {
        line.fold_branch();
    }
    let (_, source, _, _) = decode_scope(&scope.key)?;
    if line.branches {
        let error = match line.da {
            None => Some("inconsistent branch coverage without line coverage data"),
            Some(false) if line.hit_count > 0 => Some("inconsistent branch and line hit status"),
            Some(true) if line.hit_count == 0 && !is_perl(&source) => {
                Some("inconsistent line hit with unevaluated branches")
            }
            _ => None,
        };
        if let Some(message) = error {
            let error = super::policy::at(
                super::policy::Kind::Inconsistent,
                &source,
                line.number,
                message,
            );
            if !policy::can_recover(req, &error) {
                return Err(error);
            }
            if line.da.is_none() {
                // A single testcase shares the source summary map. Multiple
                // testcases keep the synthesized point in the summary only.
                let serialize = global.is_some() && !scope.multiple && scope.testcase.is_some();
                let mut key = scope.key.clone();
                key.extend_from_slice(&line.number.to_be_bytes());
                let mut value = vec![u8::from(serialize)];
                value.extend_from_slice(&line.hit_count.to_be_bytes());
                put_string(&mut value, scope.testcase.as_deref().unwrap_or(""))?;
                generated.push(key, value)?;
                line.da = Some(line.hit_count > 0);
            }
        }
    }
    if let (Some(global), Some(hit)) = (global.as_deref_mut(), line.da) {
        let mut key = function_check::prefix(&source, 0)?;
        key.push(1);
        key.extend_from_slice(&line.number.to_be_bytes());
        key.push(0);
        global.push(key, vec![u8::from(hit)])?;
    }
    Ok(())
}
pub(super) fn decode_generated(
    row: &sort::Row,
) -> Result<(String, u64, u64, bool, u64, String), Error> {
    let mut key = row.key.as_slice();
    let source = string(&mut key)?;
    let input = u64_field(&mut key)?;
    let line = u64_field(&mut key)?;
    let mut value = row.value.as_slice();
    let serialize = byte(&mut value)?;
    let count = u64_field(&mut value)?;
    let tc = string(&mut value)?;
    if !key.is_empty() || !value.is_empty() || serialize > 1 {
        return Err(corrupt());
    }
    Ok((source, input, line, serialize != 0, count, tc))
}
type ScopeFields<'a> = (Vec<u8>, String, u64, &'a [u8]);
fn decode_scope(bytes: &[u8]) -> Result<ScopeFields<'_>, Error> {
    let mut key = bytes;
    let source = string(&mut key)?;
    let input = u64_field(&mut key)?;
    Ok((function_check::prefix(&source, input)?, source, input, key))
}
fn is_perl(source: &str) -> bool {
    matches!(
        Path::new(source).extension().and_then(|s| s.to_str()),
        Some("pl" | "pm")
    )
}
fn put_string(bytes: &mut Vec<u8>, s: &str) -> Result<(), Error> {
    if s.as_bytes().contains(&0) {
        return Err(corrupt());
    }
    bytes.extend_from_slice(s.as_bytes());
    bytes.push(0);
    Ok(())
}
fn string(bytes: &mut &[u8]) -> Result<String, Error> {
    let n = bytes.iter().position(|b| *b == 0).ok_or_else(corrupt)?;
    let result = String::from_utf8(bytes[..n].to_vec()).map_err(|_| corrupt())?;
    *bytes = &bytes[n + 1..];
    Ok(result)
}
fn u64_field(bytes: &mut &[u8]) -> Result<u64, Error> {
    if bytes.len() < 8 {
        return Err(corrupt());
    }
    let n = u64::from_be_bytes(bytes[..8].try_into().map_err(|_| corrupt())?);
    *bytes = &bytes[8..];
    Ok(n)
}
fn byte(bytes: &mut &[u8]) -> Result<u8, Error> {
    let b = *bytes.first().ok_or_else(corrupt)?;
    *bytes = &bytes[1..];
    Ok(b)
}
fn corrupt() -> Error {
    Error::new(3, "corrupt branch consistency row")
}

#[cfg(test)]
mod tests {
    use super::*;
    fn recover() -> Request {
        let mut req = Request::default();
        req.ignore_errors.insert("inconsistent".into());
        req
    }
    #[test]
    fn recovery_counts_canonical_coverpoints_once_and_keeps_u_observations() {
        let root = tempfile::tempdir().unwrap();
        let mut checker = Checker::new(root.path(), 64 * 1024, 1).unwrap();
        checker.push_line("source.c", "a", 0, 1, 0).unwrap();
        for _ in 0..2 {
            checker
                .push_branch("source.c", "a", 0, 2, 1, 0, 0, true)
                .unwrap();
        }
        checker
            .push_branch("source.c", "a", 0, 2, 1, 0, 1, true)
            .unwrap();
        let sorted = checker.finish(&recover()).unwrap();
        let mut cursor = sorted.cursor().unwrap();
        let row = decode_generated(&cursor.next().unwrap().unwrap()).unwrap();
        assert_eq!(row, ("source.c".into(), 0, 2, true, 2, "a".into()));
        assert!(cursor.next().unwrap().is_none());
        drop(cursor);
        drop(sorted);
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
    }
    #[test]
    fn multiple_testcases_keep_generated_points_in_the_source_summary() {
        let root = tempfile::tempdir().unwrap();
        let mut checker = Checker::new(root.path(), 64 * 1024, 1).unwrap();
        checker.push_line("source.c", "a", 0, 1, 0).unwrap();
        checker.push_line("source.c", "b", 0, 1, 1).unwrap();
        checker
            .push_branch("source.c", "a", 0, 2, 1, 0, 0, true)
            .unwrap();
        let sorted = checker.finish(&recover()).unwrap();
        let mut cursor = sorted.cursor().unwrap();
        let row = decode_generated(&cursor.next().unwrap().unwrap()).unwrap();
        assert!(!row.3);
        assert_eq!(row.4, 1);
        assert!(cursor.next().unwrap().is_none());
    }
    #[test]
    fn merged_summary_is_checked_and_failures_clean_up() {
        let root = tempfile::tempdir().unwrap();
        let mut checker = Checker::new(root.path(), 64 * 1024, 1).unwrap();
        checker.push_line("source.c", "a", 0, 1, 1).unwrap();
        checker.push_line("source.c", "b", 1, 1, 0).unwrap();
        checker
            .push_branch("source.c", "b", 1, 1, 1, 0, 0, false)
            .unwrap();
        assert!(checker.finish(&Request::default()).is_err());
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
    }
}
