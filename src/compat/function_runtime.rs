//! Bounded DA and function compatibility executor.
use super::{function_spill, line_spill, mcdc_spill, policy, sort, Request, StopOnCancel};
use crate::common::{check_cancelled, Error, MAX_LINE};
use flate2::read::MultiGzDecoder;
use flate2::write::GzEncoder;
use flate2::Compression;
use std::fs::File;
use std::io::{BufRead, BufReader, BufWriter, Read, Write};
use std::path::Path;

pub(super) fn try_run(req: &Request) -> Result<Option<Result<(), Error>>, Error> {
    let o = &req.resources;
    if req.checksum || o.strict_checksum || o.inputs.iter().any(|p| p.as_os_str() == "-") {
        return Ok(None);
    }
    if !scan(req)? {
        return Ok(None);
    }
    #[cfg(test)]
    let budget = o.mem_limit;
    #[cfg(not(test))]
    let budget = o.mem_limit.max(crate::common::MIN_WORKER_BUDGET);
    execute(req, budget).map(|result| Some(Ok(result)))
}

fn scan(req: &Request) -> Result<bool, Error> {
    use std::io::BufRead;
    for path in &req.resources.inputs {
        if !path.metadata().is_ok_and(|m| m.is_file()) {
            return Ok(false);
        }
        let file = match File::open(path) {
            Ok(f) => f,
            Err(_) => return Ok(false),
        };
        let reader: Box<dyn Read> = if super::is_gzip_path(path) {
            Box::new(MultiGzDecoder::new(file))
        } else {
            Box::new(file)
        };
        let label = path.display().to_string();
        let mut reader = BufReader::new(StopOnCancel(reader));
        let mut bytes = Vec::with_capacity(4096);
        let mut open = false;
        let mut saw_da = false;
        let mut saw_mcdc = false;
        let mut line = 0u64;
        loop {
            check_cancelled()?;
            bytes.clear();
            let n = reader
                .by_ref()
                .take((MAX_LINE + 2) as u64)
                .read_until(b'\n', &mut bytes)
                .map_err(|e| Error::at(3, &label, line + 1, format!("input read failed: {e}")))?;
            if n == 0 {
                break;
            }
            line += 1;
            if bytes.last() == Some(&b'\n') {
                bytes.pop();
            }
            if bytes.last() == Some(&b'\r') {
                bytes.pop();
            }
            if bytes.len() > MAX_LINE || bytes.contains(&0) {
                return Ok(false);
            }
            let Ok(s) = std::str::from_utf8(&bytes) else {
                return Ok(false);
            };
            if s.is_empty() || s.starts_with('#') {
                continue;
            }
            if s.starts_with("TN:") {
                if open {
                    return Ok(false);
                }
            } else if s.starts_with("SF:") {
                if open {
                    return Ok(false);
                }
                open = true;
                saw_da = false;
                saw_mcdc = false;
            } else if s == "end_of_record" {
                if !open {
                    return Ok(false);
                }
                // Sections without DA currently require the collector's
                // source/testcase metadata propagation path.
                if saw_mcdc && !saw_da {
                    return Ok(false);
                }
                open = false;
            } else if s.starts_with("VER:") {
                return Ok(false);
            } else if [
                "DA:", "FN:", "FNDA:", "FNL:", "FNA:", "LF:", "LH:", "FNF:", "FNH:", "MCDC:",
                "MCF:", "MCH:",
            ]
            .iter()
            .any(|p| s.starts_with(p))
                || (!req.resources.branch_coverage && s.starts_with("BRDA:"))
            {
                if !open {
                    return Ok(false);
                }
                saw_da |= s.starts_with("DA:");
                saw_mcdc |= req.mcdc_coverage && s.starts_with("MCDC:");
            } else {
                return Ok(false);
            }
        }
        if open {
            return Ok(false);
        }
    }
    Ok(true)
}

fn execute(req: &Request, memory: usize) -> Result<(), Error> {
    let o = &req.resources;
    // Reserve input lines/readers and divide the remainder among concurrently live sorters.
    let reserve = 3 * MAX_LINE + 3 * crate::common::IO_BLOCK + 64 * 1024;
    let shared = memory
        .saturating_sub(reserve)
        .max(3 * (crate::common::IO_BLOCK + 4096));
    let slots = if req.mcdc_coverage { 4 } else { 3 };
    let each = (shared / slots).max(crate::common::IO_BLOCK + 4096);
    let fanin = super::descriptor_fanin(
        (memory / (3 * (MAX_LINE + crate::common::IO_BLOCK + 4096))).clamp(2, 32),
    )?;
    let mut lines = line_spill::Spill::new(&o.tmpdir, each, fanin)?;
    let mut raw_functions = function_spill::Records::new(&o.tmpdir, each, o.jobs.max(1))?;
    let mut function_sources = sort::Sorter::new(&o.tmpdir, each, o.jobs.max(1))?;
    let mut mcdc = if req.mcdc_coverage {
        Some(mcdc_spill::Reducer::new(&o.tmpdir, each, o.jobs.max(1))?)
    } else {
        None
    };
    for (input_id, path) in o.inputs.iter().enumerate() {
        check_cancelled()?;
        let label = path.display().to_string();
        let file =
            File::open(path).map_err(|e| Error::new(3, format!("cannot open {label}: {e}")))?;
        let reader: Box<dyn Read> = if super::is_gzip_path(path) {
            Box::new(MultiGzDecoder::new(file))
        } else {
            Box::new(file)
        };
        let mut reader = BufReader::new(StopOnCancel(reader));
        let mut buf = Vec::with_capacity(4096);
        let mut tc = String::new();
        let mut section_tc = String::new();
        let mut source: Option<String> = None;
        let mut selected = false;
        let mut section = 0u64;
        let mut ordinal = 0u64;
        let mut line_no = 0u64;
        loop {
            check_cancelled()?;
            buf.clear();
            let n = reader
                .by_ref()
                .take((MAX_LINE + 2) as u64)
                .read_until(b'\n', &mut buf)
                .map_err(|e| {
                    Error::at(3, &label, line_no + 1, format!("input read failed: {e}"))
                })?;
            if n == 0 {
                break;
            }
            line_no += 1;
            if buf.last() == Some(&b'\n') {
                buf.pop();
            }
            if buf.last() == Some(&b'\r') {
                buf.pop();
            }
            let error = |message: &str| Error::at(2, &label, line_no, message);
            if buf.len() > MAX_LINE && recover(req, error("line exceeds 1 MiB"))? {
                continue;
            }
            let s = match std::str::from_utf8(&buf) {
                Ok(s) => s,
                Err(_) => {
                    if recover(req, error("invalid UTF-8 tracefile record"))? {
                        continue;
                    } else {
                        unreachable!()
                    }
                }
            };
            if s.is_empty() || s.starts_with('#') {
                continue;
            }
            if let Some(v) = s.strip_prefix("TN:") {
                tc = v.to_owned();
                continue;
            }
            if let Some(sf) = s.strip_prefix("SF:") {
                let rewritten = match crate::path::rewrite_path(o, sf) {
                    Ok(p) => p,
                    Err(e) => {
                        if recover(req, Error::at(e.code, &label, line_no, e.message))? {
                            source = None;
                            continue;
                        } else {
                            unreachable!()
                        }
                    }
                };
                selected = crate::path::path_selected(o, &rewritten);
                source = Some(rewritten);
                section += 1;
                ordinal = 0;
                section_tc = if req.forget_test_names {
                    String::new()
                } else {
                    std::mem::take(&mut tc)
                };
                continue;
            }
            if s == "end_of_record" {
                source = None;
                tc.clear();
                section_tc.clear();
                continue;
            }
            let Some(src) = source.as_ref() else {
                continue;
            };
            if let Some(v) = s.strip_prefix("VER:") {
                let _ = v;
                continue;
            }
            if !selected {
                continue;
            }
            if let Some(payload) = s.strip_prefix("MCDC:") {
                if let Some(reducer) = &mut mcdc {
                    reducer.push(src, &section_tc, payload, &label, line_no, req)?;
                }
                continue;
            }
            if !o.function_data
                && ["FN:", "FNDA:", "FNL:", "FNA:"]
                    .iter()
                    .any(|p| s.starts_with(p))
            {
                continue;
            }
            if ["FN:", "FNDA:", "FNL:", "FNA:"]
                .iter()
                .any(|prefix| s.starts_with(prefix))
            {
                let mut source_key = Vec::new();
                push_str(&mut source_key, src)?;
                function_sources.push(source_key, Vec::new())?;
            }
            ordinal += 1;
            if let Some(x) = s.strip_prefix("DA:") {
                let mut sec = super::record::Section::default();
                let negative_recovered = if req
                    .ignore_errors
                    .iter()
                    .any(|v| v.eq_ignore_ascii_case("negative"))
                {
                    let mut fields = x.split(',');
                    let line_field = fields.next().unwrap_or("");
                    let count_field = fields.next().unwrap_or("");
                    if count_field.starts_with('-') && fields.next().is_none() {
                        match policy::num(Some(line_field), &label, line_no) {
                            Ok(n) if n > 0 => Some(n),
                            Ok(_) => None,
                            Err(e) => {
                                if recover(req, e)? {
                                    continue;
                                }
                                None
                            }
                        }
                    } else {
                        None
                    }
                } else {
                    None
                };
                if let Some(ln) = negative_recovered {
                    lines.push(line_spill::Entry {
                        kind: 1,
                        source: src.clone(),
                        testcase: section_tc.clone(),
                        value: String::new(),
                        line: ln,
                        count: 0,
                        input: input_id as u64,
                        section,
                        ordinal,
                        // DA validation already reported its original input
                        // location; the count-only join cannot fail semantically.
                        label: String::new(),
                        line_no: 0,
                    })?;
                    continue;
                }
                if let Err(e) = sec.add_line(x, &label, line_no) {
                    if recover(req, e)? {
                        continue;
                    }
                }
                if let Some((ln, (count, _))) = sec.lines.into_iter().next() {
                    lines.push(line_spill::Entry {
                        kind: 1,
                        source: src.clone(),
                        testcase: section_tc.clone(),
                        value: String::new(),
                        line: ln,
                        count,
                        input: input_id as u64,
                        section,
                        ordinal,
                        label: String::new(),
                        line_no: 0,
                    })?;
                }
                continue;
            }
            if !o.function_data {
                continue;
            }
            if let Some(x) = s.strip_prefix("FN:") {
                match policy::parse_fn(x, &label, line_no) {
                    Ok((start, end, name)) => raw_functions.push(
                        src,
                        &section_tc,
                        input_id as u64,
                        section,
                        function_spill::Family::Legacy,
                        name.as_bytes(),
                        ordinal,
                        function_spill::Kind::Declaration,
                        start,
                        end,
                        0,
                        &name,
                        &label,
                        line_no,
                    )?,
                    Err(e) => {
                        if recover(req, e)? {
                            continue;
                        }
                    }
                }
            } else if let Some(x) = s.strip_prefix("FNDA:") {
                match policy::parse_named_count(x, &label, line_no) {
                    Ok((hits, name)) => raw_functions.push(
                        src,
                        &section_tc,
                        input_id as u64,
                        section,
                        function_spill::Family::Legacy,
                        name.as_bytes(),
                        ordinal,
                        function_spill::Kind::Hit,
                        0,
                        None,
                        hits,
                        &name,
                        &label,
                        line_no,
                    )?,
                    Err(e) => {
                        if recover(req, e)? {
                            continue;
                        }
                    }
                }
            } else if let Some(x) = s.strip_prefix("FNL:") {
                let result = (|| {
                    let mut f = x.split(',');
                    let idx = policy::num(f.next(), &label, line_no)?;
                    let start = policy::num(f.next(), &label, line_no)?;
                    let end = match f.next() {
                        Some(v) => Some(policy::num(Some(v), &label, line_no)?),
                        None => None,
                    };
                    if f.next().is_some() {
                        return Err(error("invalid or duplicate FNL index"));
                    }
                    Ok((idx, start, end))
                })();
                match result {
                    Ok((idx, start, end)) => raw_functions.push(
                        src,
                        &section_tc,
                        input_id as u64,
                        section,
                        function_spill::Family::Modern,
                        &idx.to_be_bytes(),
                        ordinal,
                        function_spill::Kind::Declaration,
                        start,
                        end,
                        0,
                        "",
                        &label,
                        line_no,
                    )?,
                    Err(e) => {
                        if recover(req, e)? {
                            continue;
                        }
                    }
                }
            } else if let Some(x) = s.strip_prefix("FNA:") {
                let result = (|| {
                    let mut f = x.splitn(3, ',');
                    let idx = policy::num(f.next(), &label, line_no)?;
                    let hits =
                        policy::function_count(req, f.next().unwrap_or(""), &label, line_no)?;
                    let name = f
                        .next()
                        .filter(|v| !v.is_empty())
                        .ok_or_else(|| error("malformed FNA record"))?;
                    Ok((idx, hits, name.to_owned()))
                })();
                match result {
                    Ok((idx, hits, name)) => raw_functions.push(
                        src,
                        &section_tc,
                        input_id as u64,
                        section,
                        function_spill::Family::Modern,
                        &idx.to_be_bytes(),
                        ordinal,
                        function_spill::Kind::Hit,
                        0,
                        None,
                        hits,
                        &name,
                        &label,
                        line_no,
                    )?,
                    Err(e) => {
                        if recover(req, e)? {
                            continue;
                        }
                    }
                }
            }
        }
    }
    let mcdc_rows = mcdc.map(|r| r.finish(req)).transpose()?;
    // Release the DA batch before opening the three normalization sorters.
    // The spill directory stays alive until all line cursors have finished.
    let line_path = lines.finish()?;
    let mut functions = sort::Sorter::new(&o.tmpdir, each, o.jobs.max(1))?;
    let mut function_profiles = sort::Sorter::new(&o.tmpdir, each, o.jobs.max(1))?;
    let mut linked = raw_functions.finish()?;
    loop {
        let row = match linked.next_linked() {
            Ok(Some(row)) => row,
            Ok(None) => break,
            Err(e) => {
                if recover(req, e)? {
                    continue;
                }
                unreachable!()
            }
        };
        if row.name.is_empty() {
            continue;
        }
        if (row.start == 0 || row.end == Some(0))
            && recover(
                req,
                Error::at(2, &row.label, row.line, "function line must be positive"),
            )?
        {
            continue;
        }
        function_profiles.push(key_prefix(&row.source, &row.testcase)?, Vec::new())?;
        let mut key = key_prefix(&row.source, &row.testcase)?;
        push_str(&mut key, &row.name)?;
        let mut val = Vec::with_capacity(25);
        val.extend_from_slice(&row.start.to_be_bytes());
        val.extend_from_slice(&row.end.unwrap_or(0).to_be_bytes());
        val.push(u8::from(row.end.is_some()));
        val.extend_from_slice(&row.hits.to_be_bytes());
        functions.push(key, val)?;
    }
    drop(linked);
    let mut names = functions.finish()?.cursor()?;
    let mut normalized = sort::Sorter::new(&o.tmpdir, each, o.jobs.max(1))?;
    let mut pending = names.next()?;
    while let Some(first) = pending.take() {
        let (source, testcase, name) = decode_name_key(&first.key)?;
        let start = read_u64(&first.value, 0)?;
        let end = decode_end(&first.value)?;
        let mut hits = read_u64(&first.value, 17)?;
        pending = names.next()?;
        while pending.as_ref().is_some_and(|r| {
            decode_name_key(&r.key).is_ok_and(|(s, t, n)| s == source && t == testcase && n == name)
        }) {
            let row = pending.take().unwrap();
            let st = read_u64(&row.value, 0)?;
            let en = decode_end(&row.value)?;
            if st != start || en != end {
                let e = Error::new(2, format!("inconsistent function declaration for '{name}'"));
                if !recover(req, e)? {
                    unreachable!();
                }
            }
            hits = hits
                .saturating_add(read_u64(&row.value, 17)?)
                .min(i64::MAX as u64);
            pending = names.next()?;
        }
        let mut key = key_prefix(&source, &testcase)?;
        key.extend_from_slice(&start.to_be_bytes());
        key.extend_from_slice(&end.unwrap_or(0).to_be_bytes());
        key.push(u8::from(end.is_some()));
        push_str(&mut key, &name)?;
        normalized.push(key, hits.to_be_bytes().to_vec())?;
    }
    drop(names);
    let mut final_sort = sort::Sorter::new(&o.tmpdir, each, o.jobs.max(1))?;
    if let Some(rows) = mcdc_rows {
        let mut cursor = rows.cursor()?;
        while let Some(row) = cursor.next()? {
            final_sort.push(row.key, row.value)?;
        }
    }
    let mut function_profiles = function_profiles.finish()?.cursor()?;
    let mut function_profile = function_profiles.next()?;
    let mut function_sources = function_sources.finish()?.cursor()?;
    let mut function_source = function_sources.next()?;
    let mut lr = BufReader::new(File::open(&line_path).map_err(|e| Error::new(3, e.to_string()))?);
    let mut row = line_spill::read_next_public(&mut lr)?;
    while let Some(first) = row.take() {
        let (src, tc) = (first.source.clone(), first.testcase.clone());
        let profile_key = key_prefix(&src, &tc)?;
        while function_profile
            .as_ref()
            .is_some_and(|p| p.key < profile_key)
        {
            function_profile = function_profiles.next()?;
        }
        let has_functions = function_profile
            .as_ref()
            .is_some_and(|p| p.key == profile_key);
        let mut source_only = Vec::new();
        push_str(&mut source_only, &src)?;
        while function_source
            .as_ref()
            .is_some_and(|p| p.key < source_only)
        {
            function_source = function_sources.next()?;
        }
        let has_source_functions = function_source
            .as_ref()
            .is_some_and(|p| p.key == source_only);
        let mut line = first.line;
        let mut count = first.count.min(i64::MAX as u64);
        let mut lf = 0u64;
        let mut lh = 0u64;
        loop {
            lf = lf.saturating_add(1);
            row = line_spill::read_next_public(&mut lr)?;
            while row
                .as_ref()
                .is_some_and(|x| x.source == src && x.testcase == tc && x.line == line)
            {
                count = count
                    .saturating_add(row.as_ref().unwrap().count)
                    .min(i64::MAX as u64);
                row = line_spill::read_next_public(&mut lr)?;
            }
            let mut k = key_prefix(&src, &tc)?;
            k.push(7);
            k.extend_from_slice(&line.to_be_bytes());
            final_sort.push(k, count.to_be_bytes().to_vec())?;
            if count > 0 {
                lh = lh.saturating_add(1);
            }
            match row.as_ref() {
                Some(x) if x.source == src && x.testcase == tc => {
                    line = x.line;
                    count = x.count.min(i64::MAX as u64);
                }
                _ => break,
            }
        }
        let mut k = key_prefix(&src, &tc)?;
        k.push(8);
        final_sort.push(k, format!("{lf},{lh}").into_bytes())?;
        if has_source_functions && !has_functions {
            let mut k = key_prefix(&src, &tc)?;
            k.push(2);
            final_sort.push(k, b"0,0".to_vec())?;
        }
    }
    drop(lr);
    drop(function_profiles);
    drop(function_sources);
    drop(function_profile);
    drop(function_source);
    let normalized = normalized.finish()?;
    let mut end_map = sort::Sorter::new(&o.tmpdir, each, o.jobs.max(1))?;
    let mut map_input = normalized.cursor()?;
    let mut map_row = map_input.next()?;
    while let Some(first) = map_row.take() {
        let (source, testcase, start, end, name) = decode_fn_key(&first.key)?;
        let mut chosen = end;
        map_row = map_input.next()?;
        while map_row.as_ref().is_some_and(|r| {
            decode_fn_key(&r.key)
                .is_ok_and(|(s, t, st, _, _)| s == source && t == testcase && st == start)
        }) {
            let row = map_row.take().unwrap();
            if let Some(candidate) = decode_fn_key(&row.key)?.3 {
                if chosen.is_some_and(|old| old != candidate) {
                    let e = Error::new(
                        2,
                        format!("inconsistent function declaration: mismatched end line for {name} at {source}:{start}"),
                    );
                    if !recover(req, e)? {
                        unreachable!();
                    }
                }
                chosen = Some(chosen.map_or(candidate, |old| old.max(candidate)));
            }
            map_row = map_input.next()?;
        }
        let mut key = key_prefix(&source, &testcase)?;
        key.extend_from_slice(&start.to_be_bytes());
        let mut value = vec![0; 9];
        value[..8].copy_from_slice(&chosen.unwrap_or(0).to_be_bytes());
        value[8] = u8::from(chosen.is_some());
        end_map.push(key, value)?;
    }
    drop(map_input);
    let end_map = end_map.finish()?;
    let mut end_cursor = end_map.cursor()?;
    let mut end_row = end_cursor.next()?;
    let mut normalized = normalized.cursor()?;
    let mut canonical = sort::Sorter::new(&o.tmpdir, each, o.jobs.max(1))?;
    let mut nrow = normalized.next()?;
    while let Some(row) = nrow.take() {
        let (src, tc, start, _old_end, name) = decode_fn_key(&row.key)?;
        let mut map_key = key_prefix(&src, &tc)?;
        map_key.extend_from_slice(&start.to_be_bytes());
        while end_row.as_ref().is_some_and(|r| r.key < map_key) {
            end_row = end_cursor.next()?;
        }
        let map = end_row
            .as_ref()
            .filter(|r| r.key == map_key)
            .ok_or_else(|| Error::new(3, "missing function end-line mapping"))?;
        let end = if map.value[8] == 0 {
            None
        } else {
            Some(read_u64(&map.value, 0)?)
        };
        let mut key = key_prefix(&src, &tc)?;
        key.extend_from_slice(&start.to_be_bytes());
        key.extend_from_slice(&end.unwrap_or(0).to_be_bytes());
        key.push(u8::from(end.is_some()));
        push_str(&mut key, &name)?;
        canonical.push(key, row.value)?;
        nrow = normalized.next()?;
    }
    drop(normalized);
    drop(end_cursor);
    drop(end_row);
    drop(end_map);
    let mut ordered = sort::Sorter::new(&o.tmpdir, each, o.jobs.max(1))?;
    let mut normalized = canonical.finish()?.cursor()?;
    let mut nrow = normalized.next()?;
    let mut last_group: Option<(String, String, u64, Option<u64>)> = None;
    let mut first_alias = String::new();
    while let Some(row) = nrow.take() {
        let (src, tc, start, end, name) = decode_fn_key(&row.key)?;
        let group = (src.clone(), tc.clone(), start, end);
        if last_group.as_ref() != Some(&group) {
            first_alias = name.clone();
            last_group = Some(group);
        }
        let mut key = key_prefix(&src, &tc)?;
        key.extend_from_slice(&start.to_be_bytes());
        push_str(&mut key, &first_alias)?;
        key.extend_from_slice(&end.unwrap_or(0).to_be_bytes());
        key.push(u8::from(end.is_some()));
        push_str(&mut key, &name)?;
        ordered.push(key, row.value)?;
        nrow = normalized.next()?;
    }
    drop(normalized);
    let ordered = ordered.finish()?;
    let mut ordered_cursor = ordered.cursor()?;
    let mut ahead_cursor = ordered.cursor()?;
    let mut ahead_row = ahead_cursor.next()?;
    let mut fn_rows = ordered_cursor.next()?;
    let mut active_group: Option<(String, String, u64, String, Option<u64>)> = None;
    let mut active_testcase: Option<(String, String)> = None;
    let mut fnf = 0u64;
    let mut fnh = 0u64;
    let mut any_hit = false;
    let mut line_ends = LineEnds::new(&line_path)?;
    while let Some(first) = fn_rows.take() {
        let (src, tc, start, first_name, declared_end, name) = decode_ordered_fn_key(&first.key)?;
        let mut end = declared_end;
        let mut next_start = None;
        while let Some(ahead) = ahead_row.as_ref() {
            let (asrc, atc, astart, _, _, _) = decode_ordered_fn_key(&ahead.key)?;
            if asrc == src && atc == tc && astart <= start {
                ahead_row = ahead_cursor.next()?;
                continue;
            }
            if asrc == src && atc == tc && astart > start {
                next_start = Some(astart);
            }
            break;
        }
        if end.is_none() && req.derive_function_end_line && derive_supported(&src, &name) {
            end = line_ends.derive(&src, &tc, start, next_start)?;
            if end.is_none() {
                let e=Error::new(2,format!("inconsistent line coverage for function at {src}:{start}; cannot derive end line"));
                if !recover(req, e)? {
                    unreachable!();
                }
            }
        }
        let group = (src.clone(), tc.clone(), start, first_name.clone(), end);
        if active_group.as_ref() != Some(&group) {
            if active_group.take().is_some() {
                fnh = fnh.saturating_add(u64::from(any_hit));
            }
            if active_testcase.as_ref() != Some(&(src.clone(), tc.clone())) {
                if let Some((ps, pt)) = active_testcase.take() {
                    let mut k = key_prefix(&ps, &pt)?;
                    k.push(2);
                    final_sort.push(k, format!("{fnf},{fnh}").into_bytes())?;
                }
                active_testcase = Some((src.clone(), tc.clone()));
                fnf = 0;
                fnh = 0;
            }
            fnf = fnf.saturating_add(1);
            active_group = Some(group.clone());
            any_hit = false;
            let mut k = key_prefix(&src, &tc)?;
            k.push(1);
            k.extend_from_slice(&start.to_be_bytes());
            push_str(&mut k, &first_name)?;
            k.extend_from_slice(&end.unwrap_or(0).to_be_bytes());
            k.push(u8::from(end.is_some()));
            final_sort.push(
                k,
                format!(
                    "FNL:{},{}{}",
                    fnf - 1,
                    start,
                    end.map_or(String::new(), |v| format!(",{v}"))
                )
                .into_bytes(),
            )?;
        }
        let hits = read_u64(&first.value, 0)?;
        any_hit |= hits > 0;
        let mut k = key_prefix(&src, &tc)?;
        k.push(1);
        k.extend_from_slice(&start.to_be_bytes());
        push_str(&mut k, &first_name)?;
        k.extend_from_slice(&end.unwrap_or(0).to_be_bytes());
        k.push(u8::from(end.is_some()));
        k.push(1);
        push_str(&mut k, &name)?;
        final_sort.push(k, format!("FNA:{},{},{}", fnf - 1, hits, name).into_bytes())?;
        fn_rows = ordered_cursor.next()?;
    }
    if active_group.is_some() {
        fnh = fnh.saturating_add(u64::from(any_hit));
    }
    if let Some((src, tc)) = active_testcase {
        let mut k = key_prefix(&src, &tc)?;
        k.push(2);
        final_sort.push(k, format!("{fnf},{fnh}").into_bytes())?;
    }
    drop(line_ends);
    drop(lines);
    drop(ordered_cursor);
    drop(ahead_cursor);
    drop(ahead_row);
    drop(ordered);
    let final_rows = final_sort.finish()?;
    if o.verbose && !o.quiet {
        let stats = final_rows.stats();
        eprintln!(
            "output sort: runs={}, passes={}, bytes read={}, bytes written={}, workers={}",
            stats.runs, stats.passes, stats.bytes_read, stats.bytes_written, stats.workers
        );
    }
    write_final(req, final_rows.cursor()?)
}

fn write_final(req: &Request, mut cursor: sort::Cursor) -> Result<(), Error> {
    let o = &req.resources;
    let mut staged = if o.output.as_os_str() == "-" || o.output.as_os_str().is_empty() {
        None
    } else {
        Some(
            tempfile::NamedTempFile::new_in(
                o.output
                    .parent()
                    .filter(|p| !p.as_os_str().is_empty())
                    .unwrap_or(Path::new(".")),
            )
            .map_err(|e| Error::new(3, format!("cannot stage output: {e}")))?,
        )
    };
    if let Some(tmp) = staged.as_mut() {
        if super::is_gzip_path(&o.output) {
            let mut gz = GzEncoder::new(tmp.as_file_mut(), Compression::default());
            {
                let mut w = BufWriter::new(&mut gz);
                emit_rows(&mut cursor, &mut w, req)?;
                w.flush().map_err(ioerr)?;
            }
            gz.finish().map_err(ioerr)?;
        } else {
            let mut w = BufWriter::new(tmp.as_file_mut());
            emit_rows(&mut cursor, &mut w, req)?;
            w.flush().map_err(ioerr)?;
        }
    } else {
        let mut w = BufWriter::new(crate::common::stdout_writer().map_err(ioerr)?);
        emit_rows(&mut cursor, &mut w, req)?;
        w.flush().map_err(ioerr)?;
    }
    if let Some(tmp) = staged {
        tmp.as_file().sync_all().map_err(ioerr)?;
        check_cancelled()?;
        tmp.persist(&o.output).map_err(|e| {
            Error::new(
                3,
                format!("cannot replace {}: {}", o.output.display(), e.error),
            )
        })?;
    }
    Ok(())
}
fn emit_rows(c: &mut sort::Cursor, out: &mut dyn Write, _req: &Request) -> Result<(), Error> {
    let mut r = c.next()?;
    let mut active: Option<(String, String)> = None;
    while let Some(row) = r.take() {
        check_cancelled()?;
        let (src, tc, rank) = decode_output_key(&row.key)?;
        let identity = (src.clone(), tc.clone());
        if active.as_ref() != Some(&identity) {
            if active.take().is_some() {
                writeln!(out, "end_of_record").map_err(ioerr)?;
            }
            writeln!(out, "TN:{tc}\nSF:{src}").map_err(ioerr)?;
            active = Some(identity);
        }
        match rank {
            0 => {}
            1 | 5 => writeln!(
                out,
                "{}",
                String::from_utf8(row.value).map_err(|_| Error::new(3, "invalid function row"))?
            )
            .map_err(ioerr)?,
            2 => {
                let x =
                    String::from_utf8(row.value).map_err(|_| Error::new(3, "invalid summary"))?;
                let mut p = x.split(',');
                let nf = p.next().unwrap_or("0");
                let nh = p.next().unwrap_or("0");
                writeln!(out, "FNF:{nf}\nFNH:{nh}").map_err(ioerr)?;
            }
            6 => {
                let x = String::from_utf8(row.value)
                    .map_err(|_| Error::new(3, "invalid MC/DC summary"))?;
                let mut p = x.split(',');
                let found = p.next().unwrap_or("0");
                let hit = p.next().unwrap_or("0");
                writeln!(out, "MCF:{found}\nMCH:{hit}").map_err(ioerr)?;
            }
            7 => {
                let line = read_u64(&row.key, row.key.len() - 8)?;
                let hits = read_u64(&row.value, 0)?;
                writeln!(out, "DA:{line},{hits}").map_err(ioerr)?;
            }
            8 => {
                let x = String::from_utf8(row.value)
                    .map_err(|_| Error::new(3, "invalid line summary"))?;
                let mut p = x.split(',');
                let lf = p.next().unwrap_or("0");
                let lh = p.next().unwrap_or("0");
                writeln!(out, "LF:{lf}\nLH:{lh}").map_err(ioerr)?;
            }
            _ => return Err(Error::new(3, "invalid output sort row")),
        }
        r = c.next()?;
    }
    if active.is_some() {
        writeln!(out, "end_of_record").map_err(ioerr)?;
    }
    Ok(())
}
fn recover(req: &Request, e: Error) -> Result<bool, Error> {
    let lower = e.message.to_ascii_lowercase();
    let function_reference = lower.contains("references undeclared function")
        || lower.contains("references unknown function index");
    if (function_reference
        && (req.keep_going
            || req
                .ignore_errors
                .iter()
                .any(|x| x.eq_ignore_ascii_case("mismatch") || x.eq_ignore_ascii_case("empty"))))
        || super::policy::can_recover(req, &e)
    {
        Ok(true)
    } else {
        Err(e)
    }
}
fn key_prefix(source: &str, tc: &str) -> Result<Vec<u8>, Error> {
    let mut k = Vec::with_capacity(source.len() + tc.len() + 2);
    push_str(&mut k, source)?;
    push_str(&mut k, tc)?;
    Ok(k)
}
fn push_str(k: &mut Vec<u8>, s: &str) -> Result<(), Error> {
    if s.as_bytes().contains(&0) {
        return Err(Error::new(2, "NUL in compatibility key"));
    }
    k.extend_from_slice(s.as_bytes());
    k.push(0);
    Ok(())
}
fn decode_prefix(k: &[u8]) -> Result<(String, String), Error> {
    let mut x = k;
    let a = take_str(&mut x)?;
    let b = take_str(&mut x)?;
    Ok((a, b))
}
fn decode_name_key(k: &[u8]) -> Result<(String, String, String), Error> {
    let mut x = k;
    Ok((take_str(&mut x)?, take_str(&mut x)?, take_str(&mut x)?))
}
fn decode_fn_key(k: &[u8]) -> Result<(String, String, u64, Option<u64>, String), Error> {
    let mut x = k;
    let s = take_str(&mut x)?;
    let t = take_str(&mut x)?;
    if x.len() < 17 {
        return Err(Error::new(3, "corrupt normalized function key"));
    }
    let start = u64::from_be_bytes(x[..8].try_into().unwrap());
    let endv = u64::from_be_bytes(x[8..16].try_into().unwrap());
    let end = if x[16] == 0 { None } else { Some(endv) };
    x = &x[17..];
    Ok((s, t, start, end, take_str(&mut x)?))
}
type OrderedFunctionKey = (String, String, u64, String, Option<u64>, String);
fn decode_ordered_fn_key(k: &[u8]) -> Result<OrderedFunctionKey, Error> {
    let mut x = k;
    let s = take_str(&mut x)?;
    let t = take_str(&mut x)?;
    if x.len() < 8 {
        return Err(Error::new(3, "corrupt ordered function key"));
    }
    let start = u64::from_be_bytes(x[..8].try_into().unwrap());
    x = &x[8..];
    let first = take_str(&mut x)?;
    if x.len() < 9 {
        return Err(Error::new(3, "corrupt ordered function key"));
    }
    let endv = u64::from_be_bytes(x[..8].try_into().unwrap());
    let end = if x[8] == 0 { None } else { Some(endv) };
    x = &x[9..];
    Ok((s, t, start, first, end, take_str(&mut x)?))
}
fn decode_output_key(k: &[u8]) -> Result<(String, String, u8), Error> {
    let (s, t) = decode_prefix(k)?;
    let n = s.len() + 1 + t.len() + 1;
    if k.len() <= n {
        return Err(Error::new(3, "corrupt output key"));
    }
    Ok((s, t, k[n]))
}
fn derive_supported(source: &str, name: &str) -> bool {
    let ext = Path::new(source)
        .extension()
        .and_then(|x| x.to_str())
        .unwrap_or("");
    matches!(
        ext,
        "c" | "C"
            | "i"
            | "I"
            | "icc"
            | "cc"
            | "cpp"
            | "cxx"
            | "h"
            | "H"
            | "hh"
            | "hpp"
            | "hxx"
            | "java"
            | "pl"
            | "pm"
    ) && !name.contains("{lambda(")
        && !(ext == "java" && name.contains(".lambda$"))
}
struct LineEnds {
    reader: BufReader<File>,
    pending: Option<line_spill::Entry>,
    cached: Option<EndCache>,
    #[cfg(test)]
    rows_read: usize,
}
struct EndCache {
    source: String,
    testcase: String,
    start: u64,
    next: Option<u64>,
    end: Option<u64>,
}
impl LineEnds {
    fn new(path: &Path) -> Result<Self, Error> {
        let mut result = Self {
            reader: BufReader::new(File::open(path).map_err(ioerr)?),
            pending: None,
            cached: None,
            #[cfg(test)]
            rows_read: 0,
        };
        result.advance()?;
        Ok(result)
    }
    fn advance(&mut self) -> Result<(), Error> {
        check_cancelled()?;
        self.pending = None;
        self.pending = line_spill::read_next_public(&mut self.reader)?;
        #[cfg(test)]
        if self.pending.is_some() {
            self.rows_read += 1;
        }
        Ok(())
    }
    // Function groups and DA rows share source/testcase/start ordering. Cache
    // equal-start aliases, then consume each DA row at most once.
    fn derive(
        &mut self,
        source: &str,
        testcase: &str,
        start: u64,
        next: Option<u64>,
    ) -> Result<Option<u64>, Error> {
        if let Some(cache) = &self.cached {
            if (
                cache.source.as_str(),
                cache.testcase.as_str(),
                cache.start,
                cache.next,
            ) == (source, testcase, start, next)
            {
                return Ok(cache.end);
            }
        }
        let mut best = None;
        let upper = next.unwrap_or(u64::MAX);
        while let Some(row) = &self.pending {
            let identity = (row.source.as_str(), row.testcase.as_str());
            if identity > (source, testcase)
                || (identity == (source, testcase) && row.line >= upper)
            {
                break;
            }
            if identity == (source, testcase) && row.line >= start {
                best = Some(row.line);
            }
            self.advance()?;
        }
        self.cached = Some(EndCache {
            source: source.into(),
            testcase: testcase.into(),
            start,
            next,
            end: best,
        });
        Ok(best)
    }
}
fn take_str(x: &mut &[u8]) -> Result<String, Error> {
    let n = x
        .iter()
        .position(|b| *b == 0)
        .ok_or_else(|| Error::new(3, "corrupt output key"))?;
    let (a, b) = x.split_at(n);
    *x = &b[1..];
    String::from_utf8(a.to_vec()).map_err(|_| Error::new(3, "invalid UTF-8 sort key"))
}
fn read_u64(b: &[u8], i: usize) -> Result<u64, Error> {
    let x = b
        .get(i..i + 8)
        .ok_or_else(|| Error::new(3, "corrupt sort value"))?;
    Ok(u64::from_be_bytes(x.try_into().unwrap()))
}
fn decode_end(b: &[u8]) -> Result<Option<u64>, Error> {
    Ok(
        if *b
            .get(16)
            .ok_or_else(|| Error::new(3, "corrupt function value"))?
            == 0
        {
            None
        } else {
            Some(read_u64(b, 8)?)
        },
    )
}
fn ioerr(e: std::io::Error) -> Error {
    Error::new(3, format!("compat output failed: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn end_line_join_reads_once_and_preserves_testcase_boundaries() {
        let root = tempfile::tempdir().unwrap();
        let mut spill = line_spill::Spill::new(root.path(), 32768, 2).unwrap();
        for (source, testcase, line) in [
            ("a.c", "a", 1),
            ("a.c", "a", 3),
            ("a.c", "a", 8),
            ("a.c", "b", 2),
            ("a.c", "b", 7),
            ("b.c", "a", 4),
        ] {
            spill
                .push(line_spill::Entry {
                    kind: 1,
                    source: source.into(),
                    testcase: testcase.into(),
                    value: String::new(),
                    line,
                    count: 1,
                    input: 0,
                    section: 0,
                    ordinal: line,
                    label: String::new(),
                    line_no: line,
                })
                .unwrap();
        }
        let mut ends = LineEnds::new(&spill.finish().unwrap()).unwrap();
        assert_eq!(ends.derive("a.c", "a", 1, Some(5)).unwrap(), Some(3));
        let reads = ends.rows_read;
        for _ in 0..1000 {
            assert_eq!(ends.derive("a.c", "a", 1, Some(5)).unwrap(), Some(3));
        }
        assert_eq!(ends.rows_read, reads);
        assert_eq!(ends.derive("a.c", "a", 5, None).unwrap(), Some(8));
        assert_eq!(ends.derive("a.c", "b", 1, Some(6)).unwrap(), Some(2));
        assert_eq!(ends.derive("a.c", "b", 6, None).unwrap(), Some(7));
        assert_eq!(ends.derive("b.c", "a", 1, Some(3)).unwrap(), None);
        assert_eq!(ends.derive("b.c", "a", 3, None).unwrap(), Some(4));
        assert_eq!(ends.rows_read, 6);
    }
}
