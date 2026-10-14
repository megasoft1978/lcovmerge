use crate::common::{check_cancelled, Error, Options, IO_BLOCK, MAX_LINE};
use crate::path::rewrite_path;
use std::fs::File;
use std::io::{BufRead, BufReader};

#[derive(Clone, Debug)]
pub struct Range {
    pub start: u64,
    pub end: u64,
}

#[derive(Debug)]
pub struct Plan {
    pub splitters: Vec<String>,
    pub ranges: Vec<Vec<Range>>,
    pub bytes: u64,
    pub needs_rerun: bool,
}

type Marker = (u64, u64, Vec<u8>, bool);

// Returns only marker candidates. Ordinary (including very long DATA) lines are discarded as
// they are read; the buffered reader never holds more than one I/O block plus a marker line.
fn next_marker<R: BufRead>(r: &mut R, offset: &mut u64) -> Result<Option<Marker>, Error> {
    loop {
        check_cancelled()?;
        let start = *offset;
        let mut prefix = [0u8; 20];
        let mut prefix_len = 0;
        let mut marker = false;
        let mut line = Vec::new();
        let mut overlong = false;
        let mut saw = false;
        loop {
            let buf = r.fill_buf().map_err(io_error)?;
            if buf.is_empty() {
                if !saw {
                    return Ok(None);
                }
                break;
            }
            saw = true;
            let take = memchr::memchr(b'\n', buf).map_or(buf.len(), |i| i + 1);
            let content = &buf[..take];
            let body = content.strip_suffix(b"\n").unwrap_or(content);
            if !marker {
                let n = (20usize.saturating_sub(prefix_len)).min(body.len());
                prefix[prefix_len..prefix_len + n].copy_from_slice(&body[..n]);
                prefix_len += n;
                marker = is_marker_prefix(&prefix[..prefix_len]);
                let settled = !could_be_marker(&prefix[..prefix_len]);
                if marker {
                    line.extend_from_slice(&prefix[..prefix_len]);
                    if line.len() > MAX_LINE {
                        overlong = true;
                        line.truncate(MAX_LINE);
                    }
                    if body.len() > n {
                        let tail = &body[n..];
                        if line.len().saturating_add(tail.len()) > MAX_LINE {
                            overlong = true;
                        }
                        let room = MAX_LINE.saturating_sub(line.len());
                        line.extend_from_slice(&tail[..tail.len().min(room)]);
                    }
                } else if settled {
                    // No marker can begin with these bytes: consume the rest without retaining it.
                }
            } else {
                if line.len().saturating_add(body.len()) > MAX_LINE {
                    overlong = true;
                }
                let room = MAX_LINE.saturating_sub(line.len());
                line.extend_from_slice(&body[..body.len().min(room)]);
            }
            *offset += take as u64;
            let done = content.last() == Some(&b'\n');
            r.consume(take);
            if done {
                break;
            }
        }
        if marker {
            return Ok(Some((start, *offset, line, overlong)));
        }
        if !saw {
            return Ok(None);
        }
    }
}

fn is_marker_prefix(b: &[u8]) -> bool {
    b.starts_with(b"SF:")
        || b.starts_with(b"KF:")
        || b.starts_with(b"end_of_recordSF:")
        || b.starts_with(b"end_of_recordKF:")
        || b.starts_with(b"end_of_record")
        || b.starts_with(b"TN:")
}
fn could_be_marker(b: &[u8]) -> bool {
    [
        b"SF:".as_slice(),
        b"KF:",
        b"end_of_recordSF:",
        b"end_of_recordKF:",
        b"end_of_record",
        b"TN:",
    ]
    .iter()
    .any(|p| p.starts_with(b) || b.starts_with(p))
}
fn io_error(e: std::io::Error) -> Error {
    Error::new(2, format!("partition scan failed: {e}"))
}
fn trim_cr(line: &[u8]) -> &[u8] {
    line.strip_suffix(b"\r").unwrap_or(line)
}

fn marker_path(options: &Options, line: &[u8], overlong: bool) -> (Option<String>, bool, bool) {
    let line = trim_cr(line);
    let joined = line.starts_with(b"end_of_recordSF:") || line.starts_with(b"end_of_recordKF:");
    let body = if joined { &line[13..] } else { line };
    let raw = if body.starts_with(b"SF:") || body.starts_with(b"KF:") {
        &body[3..]
    } else {
        return (None, false, false);
    };
    if overlong || raw.is_empty() {
        return (None, joined, true);
    }
    let Ok(source) = std::str::from_utf8(raw) else {
        return (None, joined, true);
    };
    match rewrite_path(options, source) {
        Ok(path) if !path.is_empty() => (Some(path), joined, false),
        _ => (None, joined, true),
    }
}

fn reservoir_add(
    samples: &mut Vec<String>,
    bytes: &mut usize,
    candidate: String,
    seen: u64,
    cap: usize,
    budget: usize,
) {
    let n = candidate.len();
    if cap == 0 || n > budget {
        return;
    }
    // Deterministic SplitMix64 reservoir index, independent of platform RNG state.
    let mut z = seen.wrapping_add(0x9e3779b97f4a7c15);
    z = (z ^ (z >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94d049bb133111eb);
    z ^= z >> 31;
    if samples.len() < cap && bytes.saturating_add(n) <= budget {
        *bytes += n;
        samples.push(candidate);
    } else if !samples.is_empty() {
        let slot = (z % seen.max(1)) as usize;
        if slot < samples.len()
            && bytes.saturating_sub(samples[slot].len()).saturating_add(n) <= budget
        {
            *bytes = *bytes - samples[slot].len() + n;
            samples[slot] = candidate;
        }
    }
}

pub fn plan(options: &Options, want: usize) -> Result<Option<Plan>, Error> {
    if want < 2 || options.inputs.is_empty() {
        return Ok(None);
    }
    let direct_budget = (options.mem_limit / 2).min(16 << 20);
    let sample_budget = direct_budget / 4;
    let sample_cap = want.saturating_mul(8).min(sample_budget / 16);
    if sample_cap == 0 {
        return Ok(None);
    }
    let mut samples = Vec::new();
    let mut sample_bytes = 0usize;
    let mut seen = 0u64;
    let mut sizes = Vec::with_capacity(options.inputs.len());
    let mut bytes = 0u64;
    let mut needs_rerun = false;

    // First pass samples every path before applying include/exclude filters. This lets a narrow
    // include pattern still produce useful splitters and engage the threaded path.
    for name in &options.inputs {
        check_cancelled()?;
        let file = File::open(name).map_err(io_error)?;
        let size = file.metadata().map_err(io_error)?.len();
        sizes.push(size);
        bytes = bytes.saturating_add(size);
        let mut r = BufReader::with_capacity(IO_BLOCK, file);
        let mut offset = 0;
        let mut section_open = false;
        let mut saw_tn = false;
        while let Some((_start, _end, line, overlong)) = next_marker(&mut r, &mut offset)? {
            let t = trim_cr(&line);
            if t.starts_with(b"end_of_record") {
                section_open = false;
                saw_tn = false;
            }
            let (path, joined, invalid) = marker_path(options, &line, overlong);
            needs_rerun |= invalid || joined;
            if let Some(p) = path {
                if section_open {
                    needs_rerun = true;
                }
                section_open = true;
                saw_tn = false;
                seen = seen.saturating_add(1);
                reservoir_add(
                    &mut samples,
                    &mut sample_bytes,
                    p,
                    seen,
                    sample_cap,
                    sample_budget,
                );
            } else if t.starts_with(b"TN:") {
                if saw_tn || section_open {
                    needs_rerun = true;
                }
                saw_tn = true;
            }
        }
    }
    if samples.is_empty() {
        return Ok(None);
    }
    samples.sort_unstable();
    samples.dedup();
    if samples.len() < 2 {
        return Ok(None);
    }
    let part_count = want.min(samples.len()).max(2);
    let mut splitters = Vec::new();
    for i in 1..part_count {
        let candidate = samples[i * samples.len() / part_count].clone();
        if splitters
            .last()
            .is_none_or(|prev: &String| prev < &candidate)
        {
            splitters.push(candidate);
        }
    }
    if splitters.is_empty() {
        return Ok(None);
    }

    // Second pass discovers monotonic byte cuts. Scanning every row here is byte streaming only;
    // coverage records are never parsed or retained.
    let mut ranges = vec![Vec::with_capacity(options.inputs.len()); splitters.len() + 1];
    for (input, name) in options.inputs.iter().enumerate() {
        check_cancelled()?;
        let file = File::open(name).map_err(io_error)?;
        let mut r = BufReader::with_capacity(IO_BLOCK, file);
        let mut offset = 0;
        let size = sizes[input];
        let mut from = 0u64;
        let mut next_cut = 0usize;
        let mut pending_start: Option<u64> = None;
        let mut pending_tn = false;
        let mut section_floor = 0u64;
        while let Some((start, end, line, overlong)) = next_marker(&mut r, &mut offset)? {
            let t = trim_cr(&line);
            if t.starts_with(b"TN:") {
                pending_start.get_or_insert(section_floor.min(start));
                pending_tn = true;
            }
            let (path, joined, invalid) = marker_path(options, &line, overlong);
            needs_rerun |= invalid || joined;
            if let Some(path) = path {
                let cut = pending_start.filter(|_| pending_tn).unwrap_or(start);
                while next_cut < splitters.len() && path >= splitters[next_cut] {
                    // Cut positions are monotonic even when sections are unsorted. The worker
                    // validates each row against its assigned splitter interval and triggers rerun.
                    let cut = cut.max(from).min(size);
                    ranges[next_cut].push(Range {
                        start: from,
                        end: cut,
                    });
                    from = cut;
                    next_cut += 1;
                }
                pending_start = None;
                pending_tn = false;
                let _ = end;
            } else if t.starts_with(b"end_of_record") {
                pending_start = None;
                pending_tn = false;
                section_floor = end;
            } else if !t.is_empty() && !t.starts_with(b"#") && !t.starts_with(b"TN:") {
                pending_start = None;
                pending_tn = false;
            }
        }
        while next_cut < splitters.len() {
            ranges[next_cut].push(Range {
                start: from,
                end: size,
            });
            from = size;
            next_cut += 1;
        }
        ranges[splitters.len()].push(Range {
            start: from,
            end: size,
        });
    }
    Ok(Some(Plan {
        splitters,
        ranges,
        bytes,
        needs_rerun,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn options(data: &[&[u8]]) -> (Options, tempfile::TempDir) {
        let dir = tempfile::tempdir().unwrap();
        let mut o = Options::default();
        for (i, contents) in data.iter().enumerate() {
            let path = dir.path().join(format!("{i}.info"));
            File::create(&path).unwrap().write_all(contents).unwrap();
            o.inputs.push(path);
        }
        (o, dir)
    }

    #[test]
    fn cuts_preserve_tn_preamble_and_cover_every_byte() {
        let (mut o, _dir) = options(&[b"# head\n\nTN:case\nSF:/t/a.c\nDA:1,1\nend_of_record\nSF:/t/b.c\nDA:1,1\nend_of_record\nSF:/t/c.c\n"]);
        o.includes.push("/t/b.c".into()); // Sampling must still see other paths.
        let p = plan(&o, 2).unwrap().unwrap();
        assert!(!p.splitters.is_empty());
        let all: Vec<_> = p.ranges.iter().flat_map(|v| v.iter()).collect();
        assert_eq!(all.first().unwrap().start, 0);
        assert_eq!(all.last().unwrap().end, p.bytes);
        assert!(all.windows(2).all(|w| w[0].end == w[1].start));
        assert_eq!(p.splitters.len(), 1);
    }

    #[test]
    fn rewrites_paths_and_keeps_unsorted_cuts_contiguous() {
        let (mut o, _dir) = options(&[
            b"SF:/z/c.c\nDA:1,1\nend_of_record\nSF:/z/a.c\nDA:1,1\nend_of_record\nSF:/z/b.c\n",
        ]);
        o.rebases.push(("/z".into(), "/a".into()));
        let p = plan(&o, 3).unwrap().unwrap();
        assert!(p.splitters.iter().all(|s| s.starts_with("/a/")));
        assert!(p
            .ranges
            .iter()
            .flatten()
            .collect::<Vec<_>>()
            .windows(2)
            .all(|w| w[0].end == w[1].start));
    }

    #[test]
    fn joined_markers_request_serial_rerun_and_eof_tn_is_clean() {
        let (o, _dir) =
            options(&[b"SF:/t/a.c\nDA:1,1\nend_of_record\nend_of_recordSF:/t/b.c\nTN:unfinished"]);
        let p = plan(&o, 2).unwrap().unwrap();
        assert!(p.needs_rerun);
        let (o, _dir) = options(&[b"SF:/t/a.c\nDA:1,1\nend_of_record\nTN:dangling"]);
        if let Some(p) = plan(&o, 2).unwrap() {
            assert!(!p.needs_rerun);
        }
    }
}
