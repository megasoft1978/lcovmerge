//! Private, versioned framing for compatibility spill records.
use crate::common::Error;
use std::io::{Read, Write};

const MAGIC: &[u8; 8] = b"LCVCRUN\0";
const VERSION: u32 = 2;
pub const MAX_FIELD: usize = 1 << 20;
pub const MAX_FIELDS: usize = 64;

pub fn write_record(mut out: impl Write, fields: &[&[u8]]) -> Result<(), Error> {
    if fields.len() > MAX_FIELDS {
        return Err(Error::new(2, "compatibility run has too many fields"));
    }
    if fields.iter().any(|field| field.len() > MAX_FIELD) {
        return Err(Error::new(2, "compatibility run field exceeds 1 MiB"));
    }
    out.write_all(MAGIC)
        .and_then(|_| out.write_all(&VERSION.to_le_bytes()))
        .map_err(|e| Error::new(3, format!("compatibility run write failed: {e}")))?;
    for field in fields {
        out.write_all(&(field.len() as u32).to_le_bytes())
            .and_then(|_| out.write_all(field))
            .map_err(|e| Error::new(3, format!("compatibility run write failed: {e}")))?;
    }
    out.write_all(&u32::MAX.to_le_bytes())
        .map_err(|e| Error::new(3, format!("compatibility run write failed: {e}")))?;
    Ok(())
}

pub fn read_record(mut input: impl Read, field_count: usize) -> Result<Vec<Vec<u8>>, Error> {
    if field_count > MAX_FIELDS {
        return Err(Error::new(2, "compatibility run has too many fields"));
    }
    let mut magic = [0; 8];
    input
        .read_exact(&mut magic)
        .map_err(|_| Error::new(2, "truncated compatibility run header"))?;
    if &magic != MAGIC {
        return Err(Error::new(2, "invalid compatibility run magic"));
    }
    let mut ver = [0; 4];
    input
        .read_exact(&mut ver)
        .map_err(|_| Error::new(2, "truncated compatibility run version"))?;
    if u32::from_le_bytes(ver) != VERSION {
        return Err(Error::new(2, "unsupported compatibility run version"));
    }
    let mut result = Vec::with_capacity(field_count);
    let mut frame_bytes = 0usize;
    for _ in 0..field_count {
        let mut len = [0; 4];
        input
            .read_exact(&mut len)
            .map_err(|_| Error::new(2, "truncated compatibility run field length"))?;
        let n = u32::from_le_bytes(len);
        if n == u32::MAX {
            return Err(Error::new(2, "compatibility run has too few fields"));
        }
        let n = n as usize;
        if n > MAX_FIELD {
            return Err(Error::new(2, "compatibility run field exceeds 1 MiB"));
        }
        frame_bytes = frame_bytes
            .checked_add(n)
            .ok_or_else(|| Error::new(2, "compatibility run frame overflow"))?;
        if frame_bytes > 4 * MAX_FIELD {
            return Err(Error::new(2, "compatibility run frame exceeds 4 MiB"));
        }
        let mut field = vec![0; n];
        input
            .read_exact(&mut field)
            .map_err(|_| Error::new(2, "truncated compatibility run field"))?;
        result.push(field);
    }
    let mut end = [0; 4];
    input
        .read_exact(&mut end)
        .map_err(|_| Error::new(2, "missing compatibility run terminator"))?;
    if u32::from_le_bytes(end) != u32::MAX {
        return Err(Error::new(2, "invalid compatibility run field count"));
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn versioned_record_round_trip_and_truncation_rejection() {
        let mut data = Vec::new();
        write_record(&mut data, &[b"key", b"payload"]).unwrap();
        assert_eq!(
            read_record(&data[..], 2).unwrap(),
            vec![b"key".to_vec(), b"payload".to_vec()]
        );
        assert!(read_record(&data[..data.len() - 1], 2).is_err());
    }
}
