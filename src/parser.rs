use crate::common::{check_cancelled, Error, Options, MAX_LINE};
use crate::model::*;
use crate::path::{path_selected, rewrite_path};
use std::collections::VecDeque;
use std::io::BufRead;
use std::sync::Arc;

type DiagnosticSink<'a> = &'a mut dyn FnMut(&str) -> Result<(), Error>;

pub struct Parser<R: BufRead> {
    reader: Option<Box<R>>,
    reader_extra_capacity: usize,
    filename: String,
    input_id: u32,
    options: Arc<Options>,
    line: Vec<u8>,
    spare_text: String,
    spare_extra: String,
    spare_path: Option<Arc<str>>,
    payload_capacity: usize,
    line_no: u64,
    pub bytes: u64,
    pub records: u64,
    path: Option<Arc<str>>,
    keep_path: bool,
    section_open: bool,
    pending_tn: Option<String>,
    fnl: Option<(u64, u64, u64)>,
    queue: VecDeque<Record>,
    diagnostics: Vec<String>,
    eof: bool,
}

impl<R: BufRead> Parser<R> {
    pub fn new(reader: R, filename: String, input_id: u32, options: Arc<Options>) -> Self {
        Self {
            reader: Some(Box::new(reader)),
            reader_extra_capacity: 0,
            filename,
            input_id,
            options,
            line: Vec::with_capacity(4096),
            spare_text: String::new(),
            spare_extra: String::new(),
            spare_path: None,
            payload_capacity: 0,
            line_no: 0,
            bytes: 0,
            records: 0,
            path: None,
            keep_path: false,
            section_open: false,
            pending_tn: None,
            fnl: None,
            queue: VecDeque::with_capacity(3),
            diagnostics: Vec::new(),
            eof: false,
        }
    }

    pub fn take_diagnostics(&mut self) -> Vec<String> {
        std::mem::take(&mut self.diagnostics)
    }

    pub fn buffer_capacity(&self) -> usize {
        self.line
            .capacity()
            .saturating_add(self.reader_extra_capacity)
            .saturating_add(self.payload_capacity)
            .saturating_add(self.spare_path.as_ref().map_or(0, |path| path.len()))
            .saturating_add(self.pending_tn.as_ref().map_or(0, String::capacity))
            .saturating_add(self.path.as_ref().map_or(0, |path| path.len()))
            .saturating_add(self.queue.capacity() * std::mem::size_of::<Record>())
    }

    pub fn next_record(&mut self) -> Result<Option<Record>, Error> {
        self.next_record_impl(None)
    }

    pub(crate) fn account_reader_capacity(&mut self, capacity: usize) {
        self.reader_extra_capacity = capacity.saturating_sub(crate::common::IO_BLOCK);
    }

    /// Reuses consumed payload storage without retaining records or source paths.
    pub fn recycle_record(&mut self, record: Record) {
        if record.text.capacity() > self.spare_text.capacity() {
            self.payload_capacity += record.text.capacity() - self.spare_text.capacity();
            self.spare_text = record.text;
        }
        if record.extra.capacity() > self.spare_extra.capacity() {
            self.payload_capacity += record.extra.capacity() - self.spare_extra.capacity();
            self.spare_extra = record.extra;
        }
        self.spare_path = Some(record.path);
    }

    /// Shares the current source path with another active input without retaining past paths.
    pub(crate) fn share_current_path(&mut self, path: &Arc<str>) {
        debug_assert_eq!(self.path.as_deref(), Some(path.as_ref()));
        self.path = Some(path.clone());
        for row in &mut self.queue {
            row.path.clone_from(path);
        }
    }

    /// Streams diagnostics even when filtering discards an arbitrarily long section.
    pub fn next_record_with_diagnostics(
        &mut self,
        sink: &mut impl FnMut(&str) -> Result<(), Error>,
    ) -> Result<Option<Record>, Error> {
        self.next_record_impl(Some(sink))
    }

    fn next_record_impl(
        &mut self,
        sink: Option<DiagnosticSink<'_>>,
    ) -> Result<Option<Record>, Error> {
        let mut record = None;
        self.next_into_impl(&mut record, sink)?;
        Ok(record)
    }

    pub(crate) fn next_into_with_diagnostics(
        &mut self,
        record: &mut Option<Record>,
        sink: &mut impl FnMut(&str) -> Result<(), Error>,
    ) -> Result<bool, Error> {
        self.next_into_impl(record, Some(sink))
    }

    fn next_into_impl(
        &mut self,
        record: &mut Option<Record>,
        sink: Option<DiagnosticSink<'_>>,
    ) -> Result<bool, Error> {
        *record = None;
        let mut reader = self.reader.take().expect("parser reader is present");
        let result = self.next_from_reader(&mut reader, sink, record);
        self.reader = Some(reader);
        result
    }

    fn next_from_reader(
        &mut self,
        reader: &mut R,
        mut sink: Option<DiagnosticSink<'_>>,
        record: &mut Option<Record>,
    ) -> Result<bool, Error> {
        loop {
            // Normal records have no diagnostics. Avoid replacing and dropping
            // an empty vector on every pass through the streaming parser.
            if !self.diagnostics.is_empty() {
                if let Some(sink) = sink.as_mut() {
                    for diagnostic in self.take_diagnostics() {
                        sink(&diagnostic)?;
                    }
                }
            }
            if let Some(row) = self.queue.pop_front() {
                self.payload_capacity -= row.text.capacity() + row.extra.capacity();
                self.records = self.records.saturating_add(1);
                *record = Some(row);
                return Ok(true);
            }
            if self.eof {
                return Ok(false);
            }
            check_cancelled()?;
            let available = reader.fill_buf().map_err(|_| {
                check_cancelled().err().unwrap_or_else(|| {
                    Error::at(
                        3,
                        &self.filename,
                        self.line_no.saturating_add(1),
                        "input read failed",
                    )
                })
            })?;
            if let Some(newline) = memchr::memchr(b'\n', available) {
                if newline > MAX_LINE {
                    return Err(Error::at(
                        2,
                        &self.filename,
                        self.line_no.saturating_add(1),
                        "line exceeds 1 MiB",
                    ));
                }
                self.line_no = self.line_no.saturating_add(1);
                self.bytes = self.bytes.saturating_add((newline + 1) as u64);
                let result = self.parse_buffer_line(&available[..newline]);
                reader.consume(newline + 1);
                result?;
                continue;
            }
            self.read_line(reader)?;
            if self.line.is_empty() && self.eof {
                return Ok(false);
            }
            self.line_no = self.line_no.saturating_add(1);
            let line = std::mem::take(&mut self.line);
            let result = self.parse_buffer_line(&line);
            self.line = line;
            result?;
        }
    }

    fn err(&self, code: i32, message: impl Into<String>) -> Error {
        Error::at(code, &self.filename, self.line_no, message)
    }

    #[inline(always)]
    fn parse_buffer_line(&mut self, raw: &[u8]) -> Result<(), Error> {
        let raw = raw.strip_suffix(b"\r").unwrap_or(raw);
        if self.section_open && raw.starts_with(b"DA:") && self.parse_da_bytes(&raw[3..])? {
            return Ok(());
        }
        if raw.contains(&0) {
            return Err(self.err(2, "binary or invalid UTF-8 input"));
        }
        let line =
            std::str::from_utf8(raw).map_err(|_| self.err(2, "binary or invalid UTF-8 input"))?;
        if line.is_empty() || line.starts_with('#') {
            Ok(())
        } else if self.section_open && line.starts_with("DA:") {
            self.parse_da(&line[3..])
        } else {
            self.parse_line(line)
        }
    }

    #[inline(always)]
    fn parse_da_bytes(&mut self, fields: &[u8]) -> Result<bool, Error> {
        let Some(comma) = memchr::memchr(b',', fields) else {
            return Ok(false);
        };
        let tail = &fields[comma + 1..];
        let (count, checksum) = match memchr::memchr(b',', tail) {
            Some(comma) => (&tail[..comma], &tail[comma + 1..]),
            None => (tail, &[][..]),
        };
        let (Some(line), Some(value)) = (decimal_bytes(&fields[..comma]), decimal_bytes(count))
        else {
            // Fall back to full line validation so malformed UTF-8 and NUL diagnostics stay identical.
            return Ok(false);
        };
        if checksum.contains(&0) {
            return Err(self.err(2, "binary or invalid UTF-8 input"));
        }
        let checksum = std::str::from_utf8(checksum)
            .map_err(|_| self.err(2, "binary or invalid UTF-8 input"))?;
        self.emit(DA, "", checksum, line, 0, 0, value, 0);
        Ok(true)
    }

    fn read_line(&mut self, reader: &mut R) -> Result<(), Error> {
        self.line.clear();
        loop {
            check_cancelled()?;
            let available = match reader.fill_buf() {
                Ok(buf) => buf,
                Err(_) => {
                    check_cancelled()?;
                    return Err(Error::at(
                        3,
                        &self.filename,
                        self.line_no.saturating_add(1),
                        "input read failed",
                    ));
                }
            };
            if available.is_empty() {
                self.eof = true;
                return Ok(());
            }
            let has_newline = memchr::memchr(b'\n', available);
            let take = has_newline.map_or(available.len(), |i| i + 1);
            let content_len = take - usize::from(has_newline.is_some());
            if content_len > MAX_LINE.saturating_sub(self.line.len()) {
                return Err(Error::at(
                    2,
                    &self.filename,
                    self.line_no.saturating_add(1),
                    "line exceeds 1 MiB",
                ));
            }
            self.line.extend_from_slice(&available[..content_len]);
            self.bytes = self.bytes.saturating_add(take as u64);
            reader.consume(take);
            if has_newline.is_some() {
                return Ok(());
            }
        }
    }

    // These arguments map directly to the record's typed LCOV keys and payload fields.
    #[allow(clippy::too_many_arguments)]
    #[inline(always)]
    fn emit(
        &mut self,
        kind: u8,
        text: &str,
        extra: &str,
        a: u64,
        b: u64,
        c: u64,
        value: u64,
        flags: u8,
    ) {
        if !self.keep_path {
            return;
        }
        let mut owned_text = if text.is_empty() {
            String::new()
        } else {
            std::mem::take(&mut self.spare_text)
        };
        let mut owned_extra = if extra.is_empty() {
            String::new()
        } else {
            std::mem::take(&mut self.spare_extra)
        };
        let old_capacity = owned_text.capacity() + owned_extra.capacity();
        owned_text.clear();
        owned_extra.clear();
        owned_text.push_str(text);
        owned_extra.push_str(extra);
        let growth = owned_text.capacity() + owned_extra.capacity() - old_capacity;
        self.payload_capacity += growth;
        let current_path = self.path.as_ref().expect("open kept section");
        let path = match self.spare_path.take() {
            Some(path) if Arc::ptr_eq(&path, current_path) => path,
            _ => current_path.clone(),
        };
        self.queue.push_back(Record {
            path,
            text: owned_text,
            extra: owned_extra,
            a,
            b,
            c,
            value,
            origin_line: self.line_no,
            input_id: self.input_id,
            kind,
            flags,
        });
    }
    fn parse_line(&mut self, line: &str) -> Result<(), Error> {
        let mut line = line;
        const EOR: &str = "end_of_record";
        if line.len() >= EOR.len() + 3
            && line.starts_with(EOR)
            && (line[EOR.len()..].starts_with("SF:") || line[EOR.len()..].starts_with("KF:"))
        {
            if !self.section_open {
                return Err(self.err(2, "joined end_of_record/source record without SF record"));
            }
            self.close_section();
            self.diagnostics.push(format!("lcovmerge: {}:{}: warning: recovered joined end_of_record/source boundary after missing newline", self.filename, self.line_no));
            line = &line[EOR.len()..];
        }
        if line == EOR {
            if !self.section_open {
                return Err(self.err(2, "end_of_record without SF record"));
            }
            self.close_section();
            return Ok(());
        }
        if line.starts_with("SF:") || line.starts_with("KF:") {
            if self.section_open {
                return Err(self.err(2, "SF record before end_of_record"));
            }
            let input_path = &line[3..];
            if input_path.is_empty() {
                return Err(self.err(2, "empty SF path"));
            }
            let rewritten = rewrite_path(&self.options, input_path)
                .map_err(|_| self.err(2, "source path is too long or cannot be rewritten"))?;
            if rewritten.is_empty() {
                return Err(self.err(2, "source path becomes empty after rewriting"));
            }
            let path: Arc<str> = Arc::from(rewritten);
            self.keep_path = path_selected(&self.options, &path);
            self.path = Some(path);
            self.section_open = true;
            self.fnl = None;
            if self.keep_path {
                let tn = self.pending_tn.take().unwrap_or_default();
                self.emit(TN, &tn, "", 0, 0, 0, 0, 0);
                self.emit(SECTION, "", "", 0, 0, 0, 0, 0);
            } else {
                self.pending_tn = None;
            }
            return Ok(());
        }
        if let Some(tn) = line.strip_prefix("TN:") {
            if self.section_open {
                return Err(self.err(2, "TN record must precede SF"));
            }
            if self.pending_tn.is_some() {
                return Err(self.err(2, "multiple TN records before SF"));
            }
            self.pending_tn = Some(tn.to_owned());
            return Ok(());
        }
        if !self.section_open {
            return Err(self.err(2, "coverpoint outside an SF section"));
        }
        if let Some(fields) = line.strip_prefix("DA:") {
            return self.parse_da(fields);
        }
        for p in ["FNF:", "FNH:", "LF:", "LH:", "BRF:", "BRH:", "MCF:", "MCH:"] {
            if let Some(n) = line.strip_prefix(p) {
                if decimal(n).is_none() {
                    return Err(self.err(2, "invalid summary count"));
                }
                return Ok(());
            }
        }
        if let Some(fields) = line.strip_prefix("FNL:") {
            if !self.options.function_data {
                self.fnl = None;
                return Ok(());
            }
            let mut it = fields.split(',');
            let idx = it.next().unwrap_or("");
            let start = it.next().unwrap_or("");
            let end = it.next();
            let (Some(i), Some(s)) = (decimal(idx), decimal(start)) else {
                return Err(self.err(2, "invalid FNL record"));
            };
            let e = match end {
                Some(v) => decimal(v).ok_or_else(|| self.err(2, "invalid FNL record"))?,
                None => 0,
            };
            self.fnl = Some((i, s, e));
            self.emit(GROUP, "", "", s, e, i, 0, FN_NEW);
            return Ok(());
        }
        if let Some(fields) = line.strip_prefix("FNA:") {
            if !self.options.function_data {
                return Ok(());
            }
            let mut it = fields.splitn(3, ',');
            let idx = it.next().unwrap_or("");
            let count = it.next().unwrap_or("");
            let name = it.next();
            let (Some(i), Some(v), Some(name), Some((fi, start, end))) =
                (decimal(idx), decimal(count), name, self.fnl)
            else {
                return Err(self.err(2, "invalid FNA record or missing matching FNL"));
            };
            if fi != i {
                return Err(self.err(2, "invalid FNA record or missing matching FNL"));
            }
            if self.keep_path && name.is_empty() {
                return Err(self.err(2, "empty FNA function name"));
            }
            self.emit(GROUP, "", "", start, end, i, 0, FN_NEW);
            self.emit(FN, name, "", start, end, i, v, FN_NEW);
            return Ok(());
        }
        if let Some(fields) = line.strip_prefix("FN:") {
            if !self.options.function_data {
                return Ok(());
            }
            let mut it = fields.splitn(3, ',');
            let first = it.next().unwrap_or("");
            let second = it.next();
            let third = it.next();
            let Some(start) = decimal(first) else {
                return Err(self.err(2, "invalid FN record"));
            };
            let (end, name) = match (second, third) {
                (Some(end), Some(name)) => (
                    decimal(end).ok_or_else(|| self.err(2, "invalid FN end line"))?,
                    name,
                ),
                (Some(name), None) => (0, name),
                _ => return Err(self.err(2, "invalid FN record")),
            };
            self.emit(GROUP, name, name, start, end, 0, 0, 0);
            self.emit(FN, name, name, start, end, 0, 0, 0);
            return Ok(());
        }
        if let Some(fields) = line.strip_prefix("FNDA:") {
            if !self.options.function_data {
                return Ok(());
            }
            let Some((count, name)) = fields.split_once(',') else {
                return Err(self.err(2, "invalid FNDA record"));
            };
            let Some(v) = decimal(count) else {
                return Err(self.err(2, "invalid FNDA record"));
            };
            self.emit(FNDA, name, name, 0, 0, 0, v, 0);
            return Ok(());
        }
        if let Some(fields) = line.strip_prefix("BRDA:") {
            if !self.options.branch_coverage {
                return Ok(());
            }
            let Some((ln, tail)) = fields.split_once(',') else {
                return Err(self.err(2, "invalid BRDA record"));
            };
            let Some((block, tail)) = tail.split_once(',') else {
                return Err(self.err(2, "invalid BRDA record"));
            };
            let Some((branch, count)) = tail.rsplit_once(',') else {
                return Err(self.err(2, "invalid BRDA record"));
            };
            let (Some(a), Some(b)) = (decimal(ln), parse_block(block)) else {
                return Err(self.err(2, "invalid BRDA line or block"));
            };
            let branch_n = decimal(branch);
            let (c, mut flags) = match branch_n {
                Some(v) => (v, NUMERIC_BRANCH),
                None => (0, BRANCH_TEXT),
            };
            let value = if count == "-" {
                flags |= DASH;
                0
            } else {
                decimal(count).ok_or_else(|| self.err(2, "invalid BRDA count"))?
            };
            self.emit(BRDA, branch, "", a, b, c, value, flags | block_flags(block));
            return Ok(());
        }
        if let Some(fields) = line.strip_prefix("MCDC:") {
            let mut it = fields.splitn(6, ',');
            let ln = it.next().unwrap_or("");
            let group = it.next().unwrap_or("");
            let sense = it.next().unwrap_or("");
            let count = it.next().unwrap_or("");
            let index = it.next().unwrap_or("");
            let expression = it.next();
            let unreachable = group.starts_with('U');
            let group_digits = if unreachable { &group[1..] } else { group };
            let (Some(a), Some(b), Some(v), Some(c), Some(expr)) = (
                decimal(ln),
                decimal(group_digits),
                decimal(count),
                decimal(index),
                expression,
            ) else {
                return Err(self.err(2, "invalid MCDC record"));
            };
            if sense != "t" && sense != "f" {
                return Err(self.err(2, "invalid MCDC record"));
            }
            let flags = (if sense == "t" { MCDC_TRUE } else { 0 })
                | (if unreachable { MCDC_UNREACHABLE } else { 0 });
            self.emit(MCDC, expr, "", a, b, c, v, flags);
            return Ok(());
        }
        if line.starts_with("VER:") {
            self.emit(EXT, line, "", 0, 0, 0, 0, 0);
            return Ok(());
        }
        let Some((tag, _)) = line.split_once(':') else {
            return Err(self.err(2, "unrecognized tracefile row"));
        };
        if tag.is_empty() {
            return Err(self.err(2, "unrecognized tracefile row"));
        }
        if self.options.warn_unknown {
            self.diagnostics.push(format!(
                "lcovmerge: {}:{}: preserved unknown record {}",
                self.filename, self.line_no, tag
            ));
        }
        self.emit(EXT, line, "", 0, 0, 0, 0, 0);
        Ok(())
    }

    fn close_section(&mut self) {
        self.section_open = false;
        self.fnl = None;
        self.path = None;
        self.keep_path = false;
    }

    #[inline(always)]
    fn parse_da(&mut self, fields: &str) -> Result<(), Error> {
        let Some(comma) = memchr::memchr(b',', fields.as_bytes()) else {
            return Err(self.err(2, "invalid DA record"));
        };
        let line = &fields[..comma];
        let tail = &fields[comma + 1..];
        let (count, checksum) = match memchr::memchr(b',', tail.as_bytes()) {
            Some(comma) => (&tail[..comma], &tail[comma + 1..]),
            None => (tail, ""),
        };
        let (Some(a), Some(value)) = (decimal(line), decimal(count)) else {
            return Err(self.err(2, "invalid DA record"));
        };
        self.emit(DA, "", checksum, a, 0, 0, value, 0);
        Ok(())
    }
}

#[inline(always)]
fn decimal(s: &str) -> Option<u64> {
    decimal_bytes(s.as_bytes())
}

#[inline(always)]
fn decimal_bytes(s: &[u8]) -> Option<u64> {
    if s.is_empty() {
        return None;
    }
    let mut n = 0u64;
    if s.len() <= 19 {
        // Every sequence of at most nineteen decimal digits fits in u64.
        for &b in s {
            let digit = b.wrapping_sub(b'0');
            if digit > 9 {
                return None;
            }
            n = n * 10 + u64::from(digit);
        }
        return Some(n);
    }
    for &b in s {
        if !b.is_ascii_digit() {
            return None;
        }
        n = n.checked_mul(10)?.checked_add((b - b'0') as u64)?;
    }
    Some(n)
}
fn block_flags(s: &str) -> u8 {
    s.bytes()
        .take_while(|b| matches!(b, b'e' | b'f' | b'U'))
        .fold(0, |f, b| {
            f | match b {
                b'e' => BR_EXCEPTION,
                b'f' => BR_FALLTHROUGH,
                _ => BR_UNREACHABLE,
            }
        })
}
fn parse_block(s: &str) -> Option<u64> {
    let n = s
        .bytes()
        .take_while(|b| matches!(b, b'e' | b'f' | b'U'))
        .count();
    decimal(&s[n..])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cached_payload_capacity_matches_owned_buffers_during_recycling() {
        let input = format!(
            "SF:/a.c\nFN:1,{}\nFNDA:1,{}\nDA:1,2,{}\nDA:2,3\nextra:{}\nend_of_record\nSF:/b.c\nDA:1,4,short\nend_of_record\n",
            "function".repeat(200), "function".repeat(200), "x".repeat(8192), "unknown".repeat(500)
        );
        for recycle in [false, true] {
            let mut parser = Parser::new(
                std::io::BufReader::with_capacity(31, std::io::Cursor::new(input.as_bytes())),
                "capacity".into(),
                0,
                Arc::new(Options::default()),
            );
            while let Some(row) = parser.next_record().unwrap() {
                if recycle {
                    parser.recycle_record(row);
                }
                let actual = parser.spare_text.capacity()
                    + parser.spare_extra.capacity()
                    + parser
                        .queue
                        .iter()
                        .map(|row| row.text.capacity() + row.extra.capacity())
                        .sum::<usize>();
                assert_eq!(parser.payload_capacity, actual);
            }
        }
    }
    fn opts() -> Arc<Options> {
        Arc::new(Options {
            inputs: vec![],
            includes: vec![],
            excludes: vec![],
            rebases: vec![],
            prefix_strip: None,
            output: Default::default(),
            tmpdir: ".".into(),
            mem_limit: 0,
            jobs: 1,
            jobs_explicit: false,
            branch_coverage: true,
            function_data: true,
            quiet: false,
            verbose: false,
            stats: false,
            warn_unknown: false,
            strict_checksum: false,
        })
    }
    #[test]
    fn parses_and_interns_section_path() {
        let data = b"SF:/x.c\nDA:4,9,sum\nBRDA:4,e0,1,-\nend_of_record\n";
        let mut p = Parser::new(&data[..], "input".into(), 7, opts());
        let tn = p.next_record().unwrap().unwrap();
        let section = p.next_record().unwrap().unwrap();
        let da = p.next_record().unwrap().unwrap();
        let branch = p.next_record().unwrap().unwrap();
        assert_eq!((&tn.path, &section.path), (&section.path, &da.path));
        assert_eq!(da.extra, "sum");
        assert_eq!(branch.flags & (BR_EXCEPTION | DASH), BR_EXCEPTION | DASH);
        assert!(p.next_record().unwrap().is_none());
    }
    #[test]
    fn rejects_overflow_and_bad_numbers() {
        assert!(decimal("18446744073709551616").is_none());
        assert_eq!(decimal("18446744073709551615"), Some(u64::MAX));
    }

    #[test]
    fn streams_diagnostics_through_excluded_sections() {
        let mut options = (*opts()).clone();
        options.warn_unknown = true;
        options.excludes.push("*".into());
        let input = format!(
            "SF:/excluded.c\n{}end_of_record\n",
            "X:opaque\n".repeat(10_000)
        );
        let mut parser = Parser::new(
            input.as_bytes(),
            "filtered.info".into(),
            0,
            Arc::new(options),
        );
        let mut warnings = 0;
        assert!(parser
            .next_record_with_diagnostics(&mut |_| {
                warnings += 1;
                Ok(())
            })
            .unwrap()
            .is_none());
        assert_eq!(warnings, 10_000);
        assert!(parser.take_diagnostics().is_empty());
    }

    #[test]
    fn parses_function_aliases_and_preserves_unknown_rows() {
        let mut options = (*opts()).clone();
        options.warn_unknown = true;
        let input = b"SF:/f.c\r\nFNL:4,8,12\nFNA:4,2,run\nCUSTOM:x\nend_of_record";
        let mut p = Parser::new(&input[..], "f.info".into(), 3, Arc::new(options));
        let rows: Vec<_> = std::iter::from_fn(|| p.next_record().unwrap()).collect();
        assert_eq!(
            rows.iter().map(|r| r.kind).collect::<Vec<_>>(),
            [TN, SECTION, GROUP, GROUP, FN, EXT]
        );
        assert_eq!(rows[4].text, "run");
        assert_eq!(rows[5].text, "CUSTOM:x");
        assert_eq!(p.records, 6);
        assert!(p.take_diagnostics()[0].contains("preserved unknown record CUSTOM"));
    }

    #[test]
    fn recovers_joined_boundary_and_warns() {
        let input = b"SF:/a.c\nDA:1,1\nend_of_recordSF:/b.c\nDA:2,2\nend_of_record\n";
        let mut p = Parser::new(&input[..], "joined.info".into(), 0, opts());
        let rows: Vec<_> = std::iter::from_fn(|| p.next_record().unwrap()).collect();
        assert_eq!(rows.iter().filter(|r| r.kind == SECTION).count(), 2);
        assert!(p.take_diagnostics()[0].contains("recovered joined end_of_record/source boundary"));
    }

    #[test]
    fn rejects_invalid_utf8_and_oversized_lines() {
        let invalid = b"SF:/x.c\nX:\xff\n";
        let mut p = Parser::new(&invalid[..], "bad.info".into(), 0, opts());
        assert!(p.next_record().unwrap().is_some());
        assert!(p.next_record().unwrap().is_some());
        assert_eq!(p.next_record().unwrap_err().code, 2);

        let oversized = vec![b'a'; MAX_LINE + 1];
        let mut p = Parser::new(&oversized[..], "long.info".into(), 0, opts());
        assert_eq!(p.next_record().unwrap_err().code, 2);
    }
}
