//! Bounded physical-line reader for the compatibility parser.
use crate::common::{check_cancelled, Error, MAX_LINE};
use std::io::{BufRead, Read};

#[derive(Debug, PartialEq, Eq)]
pub(super) enum State {
    End,
    Line,
    Overlong,
}

/// Consume exactly one physical line, even when its retained prefix exceeds
/// the line cap. A recoverable long line must not become several new records.
pub(super) fn next(
    reader: &mut impl BufRead,
    buf: &mut Vec<u8>,
    label: &str,
    line: u64,
) -> Result<State, Error> {
    check_cancelled()?;
    buf.clear();
    let ioerr = |e| Error::at(3, label, line, format!("input read failed: {e}"));
    let size = reader
        .by_ref()
        .take((MAX_LINE + 2) as u64)
        .read_until(b'\n', buf)
        .map_err(ioerr)?;
    if size == 0 {
        return Ok(State::End);
    }
    let mut drained = false;
    if size == MAX_LINE + 2 && buf.last() != Some(&b'\n') {
        loop {
            check_cancelled()?;
            let bytes = reader.fill_buf().map_err(ioerr)?;
            if bytes.is_empty() {
                break;
            }
            drained = true;
            let newline = bytes.iter().position(|b| *b == b'\n');
            let consumed = newline.map_or(bytes.len(), |n| n + 1);
            reader.consume(consumed);
            if newline.is_some() {
                break;
            }
        }
    }
    if buf.last() == Some(&b'\n') {
        buf.pop();
    }
    if buf.last() == Some(&b'\r') {
        buf.pop();
    }
    if drained || buf.len() > MAX_LINE {
        buf.clear();
        return Ok(State::Overlong);
    }
    // LCOV trims trailing byte whitespace before dispatching every record.
    while buf
        .last()
        .is_some_and(|b| matches!(b, b' ' | b'\t'..=b'\r'))
    {
        buf.pop();
    }
    Ok(State::Line)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufReader, Cursor};

    #[test]
    fn trims_crlf_padding_and_final_unterminated_line() {
        let mut reader = BufReader::with_capacity(3, Cursor::new(b"TN: a \t\r\n \x0b\nDA:1,2"));
        let mut buf = Vec::new();
        for (line, expected) in [b"TN: a".as_slice(), b"", b"DA:1,2"].iter().enumerate() {
            assert_eq!(
                next(&mut reader, &mut buf, "fixture", line as u64 + 1).unwrap(),
                State::Line
            );
            assert_eq!(&buf, expected);
        }
        assert_eq!(
            next(&mut reader, &mut buf, "fixture", 4).unwrap(),
            State::End
        );
    }

    #[test]
    fn drains_overlong_record_without_dispatching_its_tail() {
        let mut input = vec![b'x'; 3 * MAX_LINE];
        input.extend_from_slice(b"SF:injected.c\nDA:1,2\n");
        let mut reader = BufReader::with_capacity(19, Cursor::new(input));
        let mut buf = Vec::new();
        assert_eq!(
            next(&mut reader, &mut buf, "fixture", 1).unwrap(),
            State::Overlong
        );
        assert!(buf.is_empty());
        assert!(buf.capacity() <= 2 * (MAX_LINE + 2));
        assert_eq!(
            next(&mut reader, &mut buf, "fixture", 2).unwrap(),
            State::Line
        );
        assert_eq!(buf, b"DA:1,2");
    }

    #[test]
    fn line_cap_excludes_lf_and_crlf_but_precedes_padding_trim() {
        for ending in [b"\n".as_slice(), b"\r\n", b""] {
            for (length, expected) in [(MAX_LINE, State::Line), (MAX_LINE + 1, State::Overlong)] {
                let mut input = vec![b' '; length];
                input.extend_from_slice(ending);
                let mut reader = BufReader::new(Cursor::new(input));
                assert_eq!(
                    next(&mut reader, &mut Vec::new(), "fixture", 1).unwrap(),
                    expected
                );
            }
        }
    }
}
