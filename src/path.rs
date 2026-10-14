use crate::common::{Error, Options, MAX_LINE};

pub fn wildcard_match(pattern: &str, text: &str) -> bool {
    let p = pattern.as_bytes();
    let t = text.as_bytes();
    let (mut i, mut j) = (0usize, 0usize);
    let (mut star, mut retry) = (None, 0usize);
    while j < t.len() {
        if i < p.len() && (p[i] == b'?' || p[i] == t[j]) {
            i += 1;
            j += 1;
            continue;
        }
        if i < p.len() && p[i] == b'[' {
            if let Some(rel_end) = p[i + 1..].iter().position(|&x| x == b']') {
                let end = i + 1 + rel_end;
                let mut k = i + 1;
                let invert = k < end && (p[k] == b'!' || p[k] == b'^');
                if invert {
                    k += 1;
                }
                let mut matched = false;
                while k < end {
                    if k + 2 < end && p[k + 1] == b'-' {
                        matched |= p[k] <= t[j] && t[j] <= p[k + 2];
                        k += 3;
                    } else {
                        matched |= p[k] == t[j];
                        k += 1;
                    }
                }
                if matched != invert {
                    i = end + 1;
                    j += 1;
                    continue;
                }
            }
        }
        if i < p.len() && p[i] == b'*' {
            star = Some(i);
            i += 1;
            retry = j;
            continue;
        }
        if let Some(s) = star {
            i = s + 1;
            retry += 1;
            j = retry;
            continue;
        }
        return false;
    }
    while i < p.len() && p[i] == b'*' {
        i += 1;
    }
    i == p.len()
}

fn prefix_match(path: &str, prefix: &str) -> Option<usize> {
    if prefix.is_empty() {
        return Some(0);
    }
    if !path.starts_with(prefix) {
        return None;
    }
    let bytes = path.as_bytes();
    let mut len = prefix.len();
    if len < bytes.len()
        && bytes[len] != b'/'
        && bytes[len] != b'\\'
        && !(prefix.ends_with('/') || prefix.ends_with('\\'))
    {
        return None;
    }
    while len < bytes.len() && (bytes[len] == b'/' || bytes[len] == b'\\') {
        len += 1;
    }
    Some(len)
}

pub fn rewrite_path(options: &Options, path: &str) -> Result<String, Error> {
    let mut current = path.to_owned();
    for (old, new) in &options.rebases {
        if let Some(matched) = prefix_match(&current, old) {
            let rest = &current[matched..];
            let old_sep = current.as_bytes().get(old.len()).copied() == Some(b'\\');
            let new_sep = if new.contains('\\') && !new.contains('/') {
                '\\'
            } else if new.contains('/') && !new.contains('\\') {
                '/'
            } else if old_sep {
                '\\'
            } else {
                '/'
            };
            let mut tail = String::with_capacity(rest.len());
            for c in rest.chars() {
                tail.push(if c == '/' || c == '\\' { new_sep } else { c });
            }
            current = if !tail.is_empty()
                && !new.is_empty()
                && !(new.ends_with('/') || new.ends_with('\\'))
            {
                format!("{new}{new_sep}{tail}")
            } else {
                format!("{new}{tail}")
            };
        }
    }
    if let Some(prefix) = &options.prefix_strip {
        if let Some(matched) = prefix_match(&current, prefix) {
            current = current[matched..].to_owned();
        }
    }
    if current.len() > MAX_LINE {
        return Err(Error::new(
            2,
            "source path is too long or cannot be rewritten",
        ));
    }
    Ok(current)
}

pub fn path_selected(options: &Options, path: &str) -> bool {
    (options.includes.is_empty() || options.includes.iter().any(|p| wildcard_match(p, path)))
        && !options.excludes.iter().any(|p| wildcard_match(p, path))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn glob_classes_and_star() {
        assert!(wildcard_match("src/[a-c]*.c", "src/beta.c"));
        assert!(!wildcard_match("src/[!a-c]*", "src/beta"));
    }
}
