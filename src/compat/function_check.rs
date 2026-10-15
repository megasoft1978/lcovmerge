//! Bounded input-local and merged source-wide function consistency checks.
use super::{policy, sort, Request};
use crate::common::{check_cancelled, Error};
use std::path::{Path, PathBuf};

const MAX_COUNT: u64 = i64::MAX as u64;

pub(super) struct Checker {
    rows: sort::Sorter,
    tmpdir: PathBuf,
    each: usize,
    jobs: usize,
    global: bool,
}
struct Group {
    key: Vec<u8>,
    start: u64,
    end: Option<u64>,
    hit: bool,
    lambda: bool,
    name: String,
    label: String,
    line_no: u64,
}
impl Checker {
    pub(super) fn new(tmpdir: &Path, budget: usize, jobs: usize) -> Result<Self, Error> {
        let each = budget / 2;
        Ok(Self {
            rows: sort::Sorter::new(tmpdir, each, jobs)?,
            tmpdir: tmpdir.to_owned(),
            each,
            jobs,
            global: false,
        })
    }
    pub(super) fn new_global(tmpdir: &Path, budget: usize, jobs: usize) -> Result<Self, Error> {
        let mut checker = Self::new(tmpdir, budget, jobs)?;
        checker.global = true;
        Ok(checker)
    }
    pub(super) fn push_line(
        &mut self,
        source: &str,
        input: u64,
        line: u64,
        hits: u64,
    ) -> Result<(), Error> {
        let mut key = prefix(source, input)?;
        key.push(0);
        key.extend_from_slice(&line.to_be_bytes());
        self.rows
            .push(key, hits.min(MAX_COUNT).to_be_bytes().to_vec())
    }
    #[allow(clippy::too_many_arguments)]
    pub(super) fn push_function(
        &mut self,
        source: &str,
        input: u64,
        start: u64,
        end: Option<u64>,
        hits: u64,
        name: &str,
        label: &str,
        line_no: u64,
    ) -> Result<(), Error> {
        let mut key = prefix(source, input)?;
        key.push(1);
        key.extend_from_slice(&start.to_be_bytes());
        put_string(&mut key, name)?;
        let mut value = vec![u8::from(end.is_some())];
        value.extend_from_slice(&end.unwrap_or(0).to_be_bytes());
        value.extend_from_slice(&hits.min(MAX_COUNT).to_be_bytes());
        put_string(&mut value, label)?;
        value.extend_from_slice(&line_no.to_be_bytes());
        self.rows.push(key, value)
    }
    /// Return per-input/start count additions for LCOV's default recovery.
    /// Every alias in every testcase receives the first contained hit line's
    /// source-wide count. A positive function reset adds zero to its aliases.
    pub(super) fn finish(self, req: &Request) -> Result<sort::Sorted, Error> {
        let mut additions = sort::Sorter::new(&self.tmpdir, self.each, self.jobs)?;
        let input = self.rows.finish()?;
        let mut cursor = input.cursor()?;
        let mut events = sort::Sorter::new(&self.tmpdir, self.each, self.jobs)?;
        let mut scope: Option<Vec<u8>> = None;
        let mut maximum = None;
        let mut group: Option<Group> = None;
        let mut previous: Option<Group> = None;
        let mut ends = LineEnds {
            cursor: input.cursor()?,
            pending: None,
        };
        ends.pending = ends.cursor.next()?;
        while let Some(row) = cursor.next()? {
            check_cancelled()?;
            let mut key = row.key.as_slice();
            let source = take_string(&mut key)?;
            let input_id = take_u64(&mut key)?;
            let identity = prefix(&source, input_id)?;
            if scope.as_ref() != Some(&identity) {
                if let Some(g) = group.take() {
                    stage_group(
                        g,
                        &mut previous,
                        &mut ends,
                        maximum,
                        req,
                        self.global,
                        &mut events,
                        &mut additions,
                    )?;
                    if let Some(g) = previous.take() {
                        resolve_group(
                            g,
                            None,
                            &mut ends,
                            maximum,
                            req,
                            self.global,
                            &mut events,
                            &mut additions,
                        )?;
                    }
                }
                scope = Some(identity.clone());
                maximum = None;
            }
            let kind = take_byte(&mut key)?;
            let position = take_u64(&mut key)?;
            if kind == 0 {
                if !key.is_empty() || row.value.len() != 8 || position == 0 {
                    return Err(corrupt());
                }
                maximum = Some(position);
                let mut event = identity;
                event.extend_from_slice(&(!position).to_be_bytes());
                event.push(0); // Lines precede queries at the same position.
                events.push(event, row.value)?;
                continue;
            }
            if kind != 1 || position == 0 {
                return Err(corrupt());
            }
            let name = take_string(&mut key)?;
            let mut value = row.value.as_slice();
            let has_end = take_bool(&mut value)?;
            let end = take_u64(&mut value)?;
            let end = has_end.then_some(end);
            let hit = take_u64(&mut value)? > 0;
            let label = take_string(&mut value)?;
            let line_no = take_u64(&mut value)?;
            if !key.is_empty() || !value.is_empty() {
                return Err(corrupt());
            }
            if group.as_ref().is_some_and(|g| g.start != position) {
                emit_group(
                    group.take().unwrap(),
                    maximum,
                    req,
                    &mut events,
                    &mut additions,
                )?;
            }
            if group.is_none() {
                group = Some(Group {
                    key: identity,
                    start: position,
                    end: None,
                    hit: false,
                    lambda: true,
                    name: name.clone(),
                    label: label.clone(),
                    line_no,
                });
            }
            let g = group.as_mut().unwrap();
            if let Some(end) = end {
                if !self.global && g.end.is_some_and(|old| old != end) {
                    recover(
                        req,
                        super::policy::at(
                            super::policy::Kind::Inconsistent,
                            &label,
                            line_no,
                            "inconsistent function end lines",
                        ),
                    )?;
                }
                g.end = Some(g.end.map_or(end, |old| old.max(end)));
            }
            g.hit |= hit;
            // LCOV prefers a non-lambda representative if any alias is ordinary.
            g.lambda &= is_lambda(&source, &name);
        }
        if let Some(g) = group {
            stage_group(
                g,
                &mut previous,
                &mut ends,
                maximum,
                req,
                self.global,
                &mut events,
                &mut additions,
            )?;
        }
        if let Some(g) = previous {
            resolve_group(
                g,
                None,
                &mut ends,
                maximum,
                req,
                self.global,
                &mut events,
                &mut additions,
            )?;
        }
        drop(ends);
        drop(cursor);
        drop(input);
        let events = events.finish()?;
        let mut cursor = events.cursor()?;
        let mut pending = cursor.next()?;
        let mut scope: Option<Vec<u8>> = None;
        let mut nearest_hit: Option<(u64, u64)> = None;
        while let Some(row) = pending.take() {
            check_cancelled()?;
            let mut key = row.key.as_slice();
            let source = take_string(&mut key)?;
            let input_id = take_u64(&mut key)?;
            let identity = prefix(&source, input_id)?;
            if scope.as_ref() != Some(&identity) {
                scope = Some(identity.clone());
                nearest_hit = None;
            }
            let position = !take_u64(&mut key)?;
            let kind = take_byte(&mut key)?;
            if kind == 0 {
                if !key.is_empty() || row.value.len() != 8 {
                    return Err(corrupt());
                }
                let mut value = row.value.as_slice();
                let mut hits = take_u64(&mut value)?;
                pending = cursor.next()?;
                while pending.as_ref().is_some_and(|next| next.key == row.key) {
                    let next = pending.take().unwrap();
                    let mut v = next.value.as_slice();
                    hits = hits.saturating_add(take_u64(&mut v)?).min(MAX_COUNT);
                    if !v.is_empty() {
                        return Err(corrupt());
                    }
                    pending = cursor.next()?;
                }
                if hits > 0 {
                    nearest_hit = Some((position, hits));
                }
                continue;
            }
            if kind != 1 || !key.is_empty() {
                return Err(corrupt());
            }
            let mut value = row.value.as_slice();
            let start = take_u64(&mut value)?;
            let end = take_u64(&mut value)?;
            let function_hit = take_bool(&mut value)?;
            let label = take_string(&mut value)?;
            let line_no = take_u64(&mut value)?;
            let name = take_string(&mut value)?;
            if !value.is_empty() {
                return Err(corrupt());
            }
            let line_hit = nearest_hit.filter(|(line, _)| *line <= end);
            let mut addition = 0;
            if req.check_data_consistency && function_hit != line_hit.is_some() {
                recover(
                    req,
                    super::policy::at(
                        super::policy::Kind::Inconsistent,
                        &label,
                        line_no,
                        format!("inconsistent function and line coverage for '{name}'"),
                    ),
                )?;
                if !function_hit {
                    addition = line_hit.unwrap().1;
                }
            }
            let mut key = identity;
            key.extend_from_slice(&start.to_be_bytes());
            additions.push(key, adjustment(true, addition, Some(end)))?;
            pending = cursor.next()?;
        }
        drop(cursor);
        drop(events);
        additions.finish()
    }
}
struct LineEnds {
    cursor: sort::Cursor,
    pending: Option<sort::Row>,
}
impl LineEnds {
    fn derive(
        &mut self,
        scope: &[u8],
        start: u64,
        next: Option<u64>,
    ) -> Result<Option<u64>, Error> {
        let mut last = None;
        while let Some(row) = self.pending.as_ref() {
            check_cancelled()?;
            let mut bytes = row.key.as_slice();
            let source = take_string(&mut bytes)?;
            let input = take_u64(&mut bytes)?;
            let identity = prefix(&source, input)?;
            let kind = take_byte(&mut bytes)?;
            let line = take_u64(&mut bytes)?;
            if identity.as_slice() > scope
                || (identity.as_slice() == scope
                    && kind == 0
                    && next.is_some_and(|upper| line >= upper))
            {
                break;
            }
            if identity.as_slice() == scope && kind == 0 && line >= start {
                last = Some(line);
            }
            self.pending = self.cursor.next()?;
        }
        Ok(last)
    }
}
#[allow(clippy::too_many_arguments)]
fn stage_group(
    g: Group,
    previous: &mut Option<Group>,
    ends: &mut LineEnds,
    maximum: Option<u64>,
    req: &Request,
    global: bool,
    events: &mut sort::Sorter,
    additions: &mut sort::Sorter,
) -> Result<(), Error> {
    if g.lambda {
        return emit_group(g, maximum, req, events, additions);
    }
    if let Some(old) = previous.take() {
        resolve_group(
            old,
            Some(g.start),
            ends,
            maximum,
            req,
            global,
            events,
            additions,
        )?;
    }
    *previous = Some(g);
    Ok(())
}
#[allow(clippy::too_many_arguments)]
fn resolve_group(
    mut g: Group,
    next: Option<u64>,
    ends: &mut LineEnds,
    maximum: Option<u64>,
    req: &Request,
    global: bool,
    events: &mut sort::Sorter,
    additions: &mut sort::Sorter,
) -> Result<(), Error> {
    // Input loading derives ends before merging. The global pass only checks
    // the merged summary, retaining testcase-local ends from the input stage.
    if g.end.is_none() && req.derive_function_end_line && !global && derive_supported(&g.key) {
        g.end = ends.derive(&g.key, g.start, next)?;
        if g.end.is_none() && maximum.is_some() {
            recover(
                req,
                super::policy::at(
                    super::policy::Kind::Inconsistent,
                    &g.label,
                    g.line_no,
                    "inconsistent line coverage: cannot derive function end",
                ),
            )?;
        }
    }
    emit_group(g, maximum, req, events, additions)
}
fn derive_supported(key: &[u8]) -> bool {
    let mut bytes = key;
    let Ok(source) = take_string(&mut bytes) else {
        return false;
    };
    let extension = Path::new(&source)
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("");
    matches!(
        extension,
        "c" | "h"
            | "i"
            | "C"
            | "H"
            | "I"
            | "icc"
            | "cpp"
            | "cc"
            | "cxx"
            | "hh"
            | "hpp"
            | "hxx"
            | "java"
            | "pl"
            | "pm"
    )
}
fn adjustment(keep: bool, count: u64, end: Option<u64>) -> Vec<u8> {
    let mut value = vec![u8::from(keep)];
    value.extend_from_slice(&count.to_be_bytes());
    value.push(u8::from(end.is_some()));
    value.extend_from_slice(&end.unwrap_or(0).to_be_bytes());
    value
}
fn emit_group(
    g: Group,
    maximum: Option<u64>,
    req: &Request,
    events: &mut sort::Sorter,
    additions: &mut sort::Sorter,
) -> Result<(), Error> {
    // LCOV discards sources without any DA data before function checks.
    if maximum.is_none() {
        let mut key = g.key;
        key.extend_from_slice(&g.start.to_be_bytes());
        return additions.push(key, adjustment(false, 0, None));
    }
    let Some(end) = g.end else {
        return Ok(());
    };
    if end < g.start {
        return recover(
            req,
            super::policy::at(
                super::policy::Kind::Inconsistent,
                &g.label,
                g.line_no,
                "inconsistent function end before start",
            ),
        );
    }
    if req.check_data_consistency && maximum.is_none_or(|last| last < g.start) {
        return recover(
            req,
            super::policy::at(
                super::policy::Kind::Inconsistent,
                &g.label,
                g.line_no,
                "inconsistent file line coverage and function coverage",
            ),
        );
    }
    let position = if g.lambda && !g.hit {
        let Some(next) = g.start.checked_add(1) else {
            return Ok(());
        };
        if next > end {
            let mut key = g.key;
            key.extend_from_slice(&g.start.to_be_bytes());
            return additions.push(key, adjustment(true, 0, Some(end)));
        }
        next
    } else {
        g.start
    };
    let mut key = g.key;
    key.extend_from_slice(&(!position).to_be_bytes());
    key.push(1);
    let mut value = g.start.to_be_bytes().to_vec();
    value.extend_from_slice(&end.to_be_bytes());
    value.push(u8::from(g.hit));
    put_string(&mut value, &g.label)?;
    value.extend_from_slice(&g.line_no.to_be_bytes());
    put_string(&mut value, &g.name)?;
    events.push(key, value)
}
fn recover(req: &Request, error: Error) -> Result<(), Error> {
    if policy::can_recover(req, &error) {
        Ok(())
    } else {
        Err(error)
    }
}
pub(super) fn prefix(source: &str, input: u64) -> Result<Vec<u8>, Error> {
    let mut key = Vec::new();
    put_string(&mut key, source)?;
    key.extend_from_slice(&input.to_be_bytes());
    Ok(key)
}
fn is_lambda(source: &str, name: &str) -> bool {
    let extension = Path::new(source)
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("");
    if extension == "java" {
        return name.contains(".lambda$");
    }
    matches!(
        extension,
        "c" | "h" | "i" | "C" | "H" | "I" | "icc" | "cpp" | "cc" | "cxx" | "hh" | "hpp" | "hxx"
    ) && name.contains("{lambda(")
}
fn put_string(bytes: &mut Vec<u8>, s: &str) -> Result<(), Error> {
    if s.as_bytes().contains(&0) {
        return Err(corrupt());
    }
    bytes.extend_from_slice(s.as_bytes());
    bytes.push(0);
    Ok(())
}
fn take_string(bytes: &mut &[u8]) -> Result<String, Error> {
    let n = bytes.iter().position(|b| *b == 0).ok_or_else(corrupt)?;
    let s = String::from_utf8(bytes[..n].to_vec()).map_err(|_| corrupt())?;
    *bytes = &bytes[n + 1..];
    Ok(s)
}
fn take_u64(bytes: &mut &[u8]) -> Result<u64, Error> {
    if bytes.len() < 8 {
        return Err(corrupt());
    }
    let n = u64::from_be_bytes(bytes[..8].try_into().map_err(|_| corrupt())?);
    *bytes = &bytes[8..];
    Ok(n)
}
fn take_byte(bytes: &mut &[u8]) -> Result<u8, Error> {
    let b = *bytes.first().ok_or_else(corrupt)?;
    *bytes = &bytes[1..];
    Ok(b)
}
fn take_bool(bytes: &mut &[u8]) -> Result<bool, Error> {
    match take_byte(bytes)? {
        0 => Ok(false),
        1 => Ok(true),
        _ => Err(corrupt()),
    }
}
fn corrupt() -> Error {
    Error::new(3, "corrupt function consistency sort row")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn request(ignore: bool) -> Request {
        let mut req = Request::default();
        if ignore {
            req.ignore_errors.insert("inconsistent".into());
        }
        req
    }
    fn run(
        lines: &[(u64, u64, u64)],
        funcs: &[(u64, u64, u64, u64, &str)],
        ignore: bool,
    ) -> Result<(), Error> {
        let dir = tempfile::tempdir().unwrap();
        let mut c = Checker::new(dir.path(), 1 << 20, 1)?;
        for &(input, line, hits) in lines {
            c.push_line("src.c", input, line, hits)?;
        }
        for &(input, start, end, hits, name) in funcs {
            c.push_function(
                "src.c",
                input,
                start,
                Some(end),
                hits,
                name,
                "input.info",
                start,
            )?;
        }
        c.finish(&request(ignore)).map(drop)
    }

    #[test]
    fn reports_both_mismatch_directions() {
        assert!(run(&[(1, 11, 1)], &[(1, 10, 12, 0, "f")], false).is_err());
        assert!(run(&[(1, 11, 0)], &[(1, 10, 12, 1, "f")], false).is_err());
        assert!(run(&[(1, 11, 1)], &[(1, 10, 12, 1, "f")], false).is_ok());
    }

    #[test]
    fn missing_line_data_is_strictly_inconsistent() {
        assert!(run(&[(1, 2, 0)], &[(1, 10, 12, 1, "f")], false).is_err());
    }

    #[test]
    fn aliases_use_any_hit_and_lambda_first_line_exception() {
        let d = tempfile::tempdir().unwrap();
        let mut c = Checker::new(d.path(), 1 << 20, 1).unwrap();
        c.push_line("src.cc", 1, 20, 1).unwrap();
        c.push_function("src.cc", 1, 10, Some(21), 0, "plain", "in.info", 5)
            .unwrap();
        c.push_function("src.cc", 1, 10, Some(21), 1, "z_alias", "in.info", 6)
            .unwrap();
        assert!(c.finish(&request(false)).is_ok());

        let d = tempfile::tempdir().unwrap();
        let mut c = Checker::new(d.path(), 1 << 20, 1).unwrap();
        c.push_line("src.cc", 1, 10, 1).unwrap();
        c.push_function("src.cc", 1, 10, Some(12), 0, "{lambda(0)", "in.info", 5)
            .unwrap();
        assert!(c.finish(&request(false)).is_ok());
    }

    #[test]
    fn lambda_ignores_first_line_but_checks_later_lines() {
        assert!(run(&[(1, 10, 1)], &[(1, 10, 12, 0, "{lambda(0)")], false).is_ok());
        assert!(run(
            &[(1, 10, 1), (1, 11, 1)],
            &[(1, 10, 12, 0, "{lambda(0)")],
            false
        )
        .is_err());
    }

    #[test]
    fn same_input_sources_rescue_across_testcases_but_other_inputs_do_not() {
        let d = tempfile::tempdir().unwrap();
        let mut c = Checker::new(d.path(), 1 << 20, 1).unwrap();
        c.push_line("src.c", 7, 10, 1).unwrap();
        c.push_function("src.c", 7, 10, Some(12), 1, "f", "in.info", 1)
            .unwrap();
        assert!(c.finish(&request(false)).is_ok());

        let d = tempfile::tempdir().unwrap();
        let mut c = Checker::new(d.path(), 1 << 20, 1).unwrap();
        c.push_line("src.c", 7, 11, 0).unwrap();
        c.push_line("src.c", 8, 11, 1).unwrap();
        c.push_function("src.c", 7, 10, Some(12), 1, "f", "in.info", 1)
            .unwrap();
        assert!(c.finish(&request(false)).is_err());
        let d = tempfile::tempdir().unwrap();
        let mut c = Checker::new(d.path(), 1 << 20, 1).unwrap();
        c.push_line("src.c", 7, 10, 1).unwrap();
        c.push_line("src.c", 7, 11, 1).unwrap();
        c.push_function("src.c", 7, 10, Some(12), 0, "f", "in.info", 1)
            .unwrap();
        assert!(c.finish(&request(false)).is_err());
    }

    #[test]
    fn overlapping_intervals_are_queried_independently() {
        assert!(run(
            &[(1, 11, 1), (1, 21, 1)],
            &[(1, 10, 15, 0, "a"), (1, 20, 25, 0, "b")],
            false
        )
        .is_err());
        assert!(run(
            &[(1, 11, 1)],
            &[(1, 10, 15, 0, "a"), (1, 10, 20, 0, "b")],
            false
        )
        .is_err());
    }

    #[test]
    fn many_rows_spill_and_error_paths_clean_temporary_directories() {
        let d = tempfile::tempdir().unwrap();
        let mut c = Checker::new(d.path(), 64 * 1024, 1).unwrap();
        for i in 0..2500u64 {
            c.push_line("large.c", 1, i + 1, u64::from(i % 7 == 0))
                .unwrap();
            c.push_function(
                "large.c",
                1,
                i + 1,
                Some(i + 1),
                0,
                "fn",
                "large.info",
                i + 1,
            )
            .unwrap();
        }
        assert!(c.finish(&request(true)).is_ok());
        assert_eq!(fs::read_dir(d.path()).unwrap().count(), 0);

        let d = tempfile::tempdir().unwrap();
        let mut c = Checker::new(d.path(), 1 << 20, 1).unwrap();
        c.push_line("src.c", 1, 2, 1).unwrap();
        c.push_function("src.c", 1, 1, Some(3), 0, "f", "bad.info", 1)
            .unwrap();
        assert!(c.finish(&request(false)).is_err());
        assert_eq!(fs::read_dir(d.path()).unwrap().count(), 0);
    }

    #[test]
    fn disabled_or_ignored_consistency_bypasses_errors() {
        assert!(run(&[(1, 2, 1)], &[(1, 1, 3, 0, "f")], true).is_ok());
        let d = tempfile::tempdir().unwrap();
        let mut req = request(false);
        req.check_data_consistency = false;
        let mut c = Checker::new(d.path(), 1 << 20, 1).unwrap();
        c.push_line("src.c", 1, 2, 1).unwrap();
        c.push_function("src.c", 1, 1, Some(3), 0, "f", "in.info", 1)
            .unwrap();
        assert!(c.finish(&req).is_ok());
    }
}
