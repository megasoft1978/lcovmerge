use crate::common::{Error, Options};
use crate::model::{
    Record, BRANCH_TEXT, BRDA, BR_EXCEPTION, BR_FALLTHROUGH, BR_UNREACHABLE, DA, DASH, EXT, FN,
    FNDA, FN_NEW, GROUP, MCDC, MCDC_TRUE, MCDC_UNREACHABLE, SECTION, TN,
};
use std::io::{self, Write};
use std::sync::Arc;

#[derive(Default, Clone, Debug)]
pub struct Totals {
    pub fnf: u64,
    pub fnh: u64,
    pub lf: u64,
    pub lh: u64,
    pub brf: u64,
    pub brh: u64,
    pub mcf: u64,
    pub mch: u64,
}

fn sat(a: u64, b: u64) -> u64 {
    a.saturating_add(b)
}
fn u<W: Write>(w: &mut W, n: u64) -> io::Result<()> {
    w.write_all(itoa::Buffer::new().format(n).as_bytes())
}

pub struct OutputState<W: Write> {
    pub writer: W,
    path: Option<Arc<str>>,
    source_written: bool,
    counts: Totals,
    function_group: Option<(u64, u64, u64, String, bool)>,
    pub totals: Totals,
}

impl<W: Write> OutputState<W> {
    pub fn memory_size(&self) -> usize {
        self.path
            .as_ref()
            .map_or(0, |path| path.len())
            .saturating_add(
                self.function_group
                    .as_ref()
                    .map_or(0, |group| group.3.capacity()),
            )
    }
    pub fn new(writer: W) -> Self {
        Self {
            writer,
            path: None,
            source_written: false,
            counts: Totals::default(),
            function_group: None,
            totals: Totals::default(),
        }
    }
    fn ensure_source(&mut self) -> io::Result<()> {
        if !self.source_written {
            self.source_written = true;
            self.writer.write_all(b"SF:")?;
            self.writer
                .write_all(self.path.as_deref().unwrap_or("").as_bytes())?;
            self.writer.write_all(b"\n")?;
        }
        Ok(())
    }
    fn finish_function_group(&mut self) {
        if let Some((_, _, _, _, hit)) = self.function_group.take() {
            if hit {
                self.counts.fnh = sat(self.counts.fnh, 1);
            }
        }
    }
    fn add_function_hit(&mut self, r: &Record) {
        let same = self.function_group.as_ref().is_some_and(|(a, b, c, n, _)| {
            (*a, *b, *c, n.as_str()) == (r.a, r.b, r.c, r.extra.as_str())
        });
        if !same {
            self.finish_function_group();
            self.function_group = Some((r.a, r.b, r.c, r.extra.clone(), false));
        }
        if r.value != 0 {
            if let Some(g) = self.function_group.as_mut() {
                g.4 = true;
            }
        }
    }
    pub fn record(&mut self, r: &Record) -> io::Result<()> {
        if (r.kind == TN || r.kind == SECTION)
            && self.path.as_deref().is_some_and(|p| p != r.path.as_ref())
        {
            self.finish_file()?;
        }
        if self.path.is_none() {
            self.path = Some(r.path.clone());
            self.source_written = false;
            self.counts = Totals::default();
            self.function_group = None;
        }
        match r.kind {
            TN => {
                if r.value == 0 && !r.text.is_empty() {
                    self.writer.write_all(b"TN:")?;
                    self.writer.write_all(r.text.as_bytes())?;
                    self.writer.write_all(b"\n")?;
                }
            }
            SECTION => self.ensure_source()?,
            _ => {
                self.ensure_source()?;
                match r.kind {
                    GROUP => {
                        self.counts.fnf = sat(self.counts.fnf, 1);
                        if r.flags & FN_NEW != 0 {
                            self.writer.write_all(b"FNL:")?;
                            u(&mut self.writer, r.c)?;
                            self.writer.write_all(b",")?;
                            u(&mut self.writer, r.a)?;
                            if r.b != 0 {
                                self.writer.write_all(b",")?;
                                u(&mut self.writer, r.b)?;
                            }
                            self.writer.write_all(b"\n")?;
                        }
                    }
                    FN => {
                        if r.flags & FN_NEW != 0 {
                            self.add_function_hit(r);
                            self.writer.write_all(b"FNA:")?;
                            u(&mut self.writer, r.c)?;
                            self.writer.write_all(b",")?;
                            u(&mut self.writer, r.value)?;
                            self.writer.write_all(b",")?;
                            self.writer.write_all(r.text.as_bytes())?;
                        } else {
                            self.writer.write_all(b"FN:")?;
                            u(&mut self.writer, r.a)?;
                            self.writer.write_all(b",")?;
                            if r.b != 0 {
                                u(&mut self.writer, r.b)?;
                                self.writer.write_all(b",")?;
                            }
                            self.writer.write_all(r.text.as_bytes())?;
                        }
                        self.writer.write_all(b"\n")?;
                    }
                    FNDA => {
                        self.add_function_hit(r);
                        self.writer.write_all(b"FNDA:")?;
                        u(&mut self.writer, r.value)?;
                        self.writer.write_all(b",")?;
                        self.writer.write_all(r.text.as_bytes())?;
                        self.writer.write_all(b"\n")?;
                    }
                    BRDA => {
                        self.writer.write_all(b"BRDA:")?;
                        u(&mut self.writer, r.a)?;
                        self.writer.write_all(b",")?;
                        if r.flags & BR_EXCEPTION != 0 {
                            self.writer.write_all(b"e")?;
                        }
                        if r.flags & BR_FALLTHROUGH != 0 {
                            self.writer.write_all(b"f")?;
                        }
                        if r.flags & BR_UNREACHABLE != 0 {
                            self.writer.write_all(b"U")?;
                        }
                        u(&mut self.writer, r.b)?;
                        self.writer.write_all(b",")?;
                        if r.flags & BRANCH_TEXT != 0 {
                            self.writer.write_all(r.text.as_bytes())?;
                        } else {
                            u(&mut self.writer, r.c)?;
                        }
                        if r.flags & DASH != 0 {
                            self.writer.write_all(b",-\n")?;
                        } else {
                            self.writer.write_all(b",")?;
                            u(&mut self.writer, r.value)?;
                            self.writer.write_all(b"\n")?;
                        }
                        if r.flags & BR_UNREACHABLE == 0 {
                            self.counts.brf = sat(self.counts.brf, 1);
                            if r.value != 0 && r.flags & DASH == 0 {
                                self.counts.brh = sat(self.counts.brh, 1);
                            }
                        }
                    }
                    MCDC => {
                        self.writer.write_all(b"MCDC:")?;
                        u(&mut self.writer, r.a)?;
                        self.writer.write_all(b",")?;
                        if r.flags & MCDC_UNREACHABLE != 0 {
                            self.writer.write_all(b"U")?;
                        }
                        u(&mut self.writer, r.b)?;
                        self.writer.write_all(b",")?;
                        self.writer.write_all(if r.flags & MCDC_TRUE != 0 {
                            b"t"
                        } else {
                            b"f"
                        })?;
                        self.writer.write_all(b",")?;
                        u(&mut self.writer, r.value)?;
                        self.writer.write_all(b",")?;
                        u(&mut self.writer, r.c)?;
                        self.writer.write_all(b",")?;
                        self.writer.write_all(r.text.as_bytes())?;
                        self.writer.write_all(b"\n")?;
                        if r.flags & MCDC_UNREACHABLE == 0 {
                            self.counts.mcf = sat(self.counts.mcf, 1);
                            if r.value != 0 {
                                self.counts.mch = sat(self.counts.mch, 1);
                            }
                        }
                    }
                    DA => {
                        // Both u64 fields fit in 20 decimal bytes. Assemble
                        // the common DA row once instead of repeatedly entering
                        // the writer for tags, separators, and individual fields.
                        // Include a usual 32-byte checksum and the newline so
                        // common rows enter the buffered writer only once.
                        let mut line = [0u8; 78];
                        line[..3].copy_from_slice(b"DA:");
                        let mut end = 3;
                        let mut number = itoa::Buffer::new();
                        for value in [r.a, r.value] {
                            if end != 3 {
                                line[end] = b',';
                                end += 1;
                            }
                            let digits = number.format(value).as_bytes();
                            line[end..end + digits.len()].copy_from_slice(digits);
                            end += digits.len();
                        }
                        if r.extra.len() > 32 {
                            self.writer.write_all(&line[..end])?;
                            self.writer.write_all(b",")?;
                            self.writer.write_all(r.extra.as_bytes())?;
                            self.writer.write_all(b"\n")?;
                        } else {
                            if !r.extra.is_empty() {
                                line[end] = b',';
                                end += 1;
                                line[end..end + r.extra.len()].copy_from_slice(r.extra.as_bytes());
                                end += r.extra.len();
                            }
                            line[end] = b'\n';
                            self.writer.write_all(&line[..end + 1])?;
                        }
                        self.counts.lf = sat(self.counts.lf, 1);
                        if r.value != 0 {
                            self.counts.lh = sat(self.counts.lh, 1);
                        }
                    }
                    EXT => {
                        self.writer.write_all(r.text.as_bytes())?;
                        self.writer.write_all(b"\n")?;
                    }
                    _ => {
                        return Err(io::Error::new(
                            io::ErrorKind::InvalidData,
                            "unknown record type",
                        ))
                    }
                }
            }
        }
        Ok(())
    }
    pub fn finish_file(&mut self) -> io::Result<()> {
        if self.path.is_none() {
            return Ok(());
        }
        self.ensure_source()?;
        self.finish_function_group();
        for (name, n) in [
            ("FNF:", self.counts.fnf),
            ("FNH:", self.counts.fnh),
            ("BRF:", self.counts.brf),
            ("BRH:", self.counts.brh),
            ("MCF:", self.counts.mcf),
            ("MCH:", self.counts.mch),
            ("LF:", self.counts.lf),
            ("LH:", self.counts.lh),
        ] {
            self.writer.write_all(name.as_bytes())?;
            u(&mut self.writer, n)?;
            self.writer.write_all(b"\n")?;
        }
        self.writer.write_all(b"end_of_record\n")?;
        self.totals.fnf = sat(self.totals.fnf, self.counts.fnf);
        self.totals.fnh = sat(self.totals.fnh, self.counts.fnh);
        self.totals.brf = sat(self.totals.brf, self.counts.brf);
        self.totals.brh = sat(self.totals.brh, self.counts.brh);
        self.totals.mcf = sat(self.totals.mcf, self.counts.mcf);
        self.totals.mch = sat(self.totals.mch, self.counts.mch);
        self.totals.lf = sat(self.totals.lf, self.counts.lf);
        self.totals.lh = sat(self.totals.lh, self.counts.lh);
        self.path = None;
        self.source_written = false;
        self.counts = Totals::default();
        Ok(())
    }
}

pub fn merge_aggregate(
    aggregate: &mut Record,
    next: &Record,
    options: &Options,
    diagnostics: &mut Vec<u8>,
) -> Result<(), Error> {
    if next.kind == TN {
        if aggregate.text != next.text {
            aggregate.value = 1;
        }
    } else if next.kind == FNDA
        || next.kind == MCDC
        || (next.kind == FN && next.flags & FN_NEW != 0)
    {
        aggregate.value = aggregate.value.saturating_add(next.value);
        if next.kind == MCDC && aggregate.text != next.text {
            let input = options
                .inputs
                .get(next.input_id as usize)
                .map(|path| path.to_string_lossy())
                .unwrap_or_else(|| "<input>".into());
            diagnostics.extend_from_slice(format!("lcovmerge: {input}:{}: MC/DC expression mismatch for {}:{} group {} index {}\n",next.origin_line,next.path,next.a,next.b,next.c).as_bytes());
            if next.text < aggregate.text {
                aggregate.text.clone_from(&next.text);
            }
        }
    } else if next.kind == BRDA {
        if aggregate.flags & DASH != 0 {
            if next.flags & DASH == 0 {
                aggregate.value = next.value;
                aggregate.flags &= !DASH;
            }
        } else if next.flags & DASH == 0 {
            aggregate.value = aggregate.value.saturating_add(next.value);
        }
    } else if next.kind == DA {
        aggregate.value = aggregate.value.saturating_add(next.value);
        if aggregate.extra.is_empty() {
            aggregate.extra.clone_from(&next.extra);
        } else if !next.extra.is_empty() && aggregate.extra != next.extra {
            let input = options
                .inputs
                .get(next.input_id as usize)
                .map(|path| path.to_string_lossy())
                .unwrap_or_else(|| "<input>".into());
            diagnostics.extend_from_slice(
                format!(
                    "lcovmerge: {input}:{}: checksum mismatch for {}:{}\n",
                    next.origin_line, next.path, next.a
                )
                .as_bytes(),
            );
            if options.strict_checksum {
                return Err(Error::new(2, "checksum mismatch"));
            }
            if next.extra < aggregate.extra {
                aggregate.extra.clone_from(&next.extra);
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    fn row(kind: u8, path: &str) -> Record {
        Record {
            path: Arc::from(path),
            text: String::new(),
            extra: String::new(),
            a: 1,
            b: 0,
            c: 0,
            value: 1,
            origin_line: 1,
            input_id: 0,
            kind,
            flags: 0,
        }
    }
    #[test]
    fn da_maximum_width_fields_preserve_optional_checksum() {
        for checksum in [
            String::new(),
            "source-hash".into(),
            "x".repeat(32),
            "x".repeat(33),
        ] {
            let mut record = row(DA, "x.c");
            record.a = u64::MAX;
            record.value = u64::MAX;
            record.extra = checksum.clone();
            let mut output = OutputState::new(Vec::new());
            output.record(&record).unwrap();
            let suffix = if checksum.is_empty() {
                String::new()
            } else {
                format!(",{checksum}")
            };
            assert_eq!(
                String::from_utf8(output.writer).unwrap(),
                format!("SF:x.c\nDA:{},{}{suffix}\n", u64::MAX, u64::MAX)
            );
        }
    }

    #[test]
    fn emits_summary_and_saturates() {
        let mut o = OutputState::new(Vec::new());
        o.record(&row(SECTION, "x.c")).unwrap();
        o.record(&row(DA, "x.c")).unwrap();
        o.finish_file().unwrap();
        let s = String::from_utf8(o.writer).unwrap();
        assert!(s.contains("LF:1\nLH:1\nend_of_record\n"));
    }
}
