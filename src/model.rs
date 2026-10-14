use std::cmp::Ordering;
use std::sync::Arc;

pub const TN: u8 = 1;
pub const SECTION: u8 = 2;
pub const GROUP: u8 = 3;
pub const FN: u8 = 4;
pub const FNDA: u8 = 5;
pub const BRDA: u8 = 6;
pub const MCDC: u8 = 7;
pub const DA: u8 = 8;
pub const EXT: u8 = 9;

pub const FN_NEW: u8 = 1;
pub const NUMERIC_BRANCH: u8 = 1;
pub const BRANCH_TEXT: u8 = 2;
pub const DASH: u8 = 4;
pub const BR_EXCEPTION: u8 = 8;
pub const BR_FALLTHROUGH: u8 = 16;
pub const BR_UNREACHABLE: u8 = 32;
pub const MCDC_TRUE: u8 = 64;
pub const MCDC_UNREACHABLE: u8 = 128;

#[derive(Debug, Default)]
pub struct Record {
    pub path: Arc<str>,
    pub text: String,
    pub extra: String,
    pub a: u64,
    pub b: u64,
    pub c: u64,
    pub value: u64,
    pub origin_line: u64,
    pub input_id: u32,
    pub kind: u8,
    pub flags: u8,
}

impl Clone for Record {
    fn clone(&self) -> Self {
        Self {
            path: self.path.clone(),
            text: self.text.clone(),
            extra: self.extra.clone(),
            a: self.a,
            b: self.b,
            c: self.c,
            value: self.value,
            origin_line: self.origin_line,
            input_id: self.input_id,
            kind: self.kind,
            flags: self.flags,
        }
    }

    fn clone_from(&mut self, source: &Self) {
        if !Arc::ptr_eq(&self.path, &source.path) {
            self.path.clone_from(&source.path);
        }
        self.text.clone_from(&source.text);
        self.extra.clone_from(&source.extra);
        self.a = source.a;
        self.b = source.b;
        self.c = source.c;
        self.value = source.value;
        self.origin_line = source.origin_line;
        self.input_id = source.input_id;
        self.kind = source.kind;
        self.flags = source.flags;
    }
}

impl Record {
    pub fn memory_size(&self) -> usize {
        std::mem::size_of::<Self>()
            .saturating_add(self.path.len())
            .saturating_add(self.text.len())
            .saturating_add(self.extra.len())
    }
}

fn new_fn(r: &Record) -> bool {
    (r.kind == GROUP || r.kind == FN) && r.flags & FN_NEW != 0
}
fn legacy_fn(r: &Record) -> bool {
    (r.kind == GROUP || r.kind == FN) && r.flags & FN_NEW == 0
}
fn order(r: &Record) -> u8 {
    match r.kind {
        TN => 0,
        SECTION => 1,
        GROUP | FN if new_fn(r) => 3,
        GROUP | FN => 4,
        FNDA => 5,
        DA => 6,
        BRDA => 7,
        MCDC => 8,
        _ => 9,
    }
}
#[inline(always)]
fn same_path_cmp(l: &Record, r: &Record) -> Ordering {
    if l.kind == DA && r.kind == DA {
        return l.a.cmp(&r.a);
    }
    if new_fn(l) && new_fn(r) {
        return l
            .a
            .cmp(&r.a)
            .then_with(|| l.b.cmp(&r.b))
            .then_with(|| l.c.cmp(&r.c))
            .then_with(|| match (l.kind, r.kind) {
                (GROUP, FN) => Ordering::Less,
                (FN, GROUP) => Ordering::Greater,
                _ => Ordering::Equal,
            })
            .then_with(|| {
                if l.kind == GROUP {
                    l.extra.cmp(&r.extra)
                } else {
                    l.text.cmp(&r.text)
                }
            });
    }
    if legacy_fn(l) && legacy_fn(r) {
        return l
            .a
            .cmp(&r.a)
            .then_with(|| l.b.cmp(&r.b))
            .then_with(|| l.c.cmp(&r.c))
            .then_with(|| l.extra.cmp(&r.extra))
            .then_with(|| match (l.kind, r.kind) {
                (GROUP, FN) => Ordering::Less,
                (FN, GROUP) => Ordering::Greater,
                _ => Ordering::Equal,
            })
            .then_with(|| l.text.cmp(&r.text));
    }
    let initial = order(l).cmp(&order(r)).then_with(|| l.kind.cmp(&r.kind));
    if initial != Ordering::Equal {
        return initial;
    }
    match l.kind {
        TN | EXT => l.text.cmp(&r.text),
        SECTION => Ordering::Equal,
        GROUP => l
            .flags
            .cmp(&r.flags)
            .then_with(|| l.a.cmp(&r.a))
            .then_with(|| l.b.cmp(&r.b))
            .then_with(|| l.c.cmp(&r.c))
            .then_with(|| l.extra.cmp(&r.extra)),
        FN => l
            .flags
            .cmp(&r.flags)
            .then_with(|| l.a.cmp(&r.a))
            .then_with(|| l.b.cmp(&r.b))
            .then_with(|| l.c.cmp(&r.c))
            .then_with(|| l.extra.cmp(&r.extra))
            .then_with(|| l.text.cmp(&r.text)),
        FNDA => {
            l.a.cmp(&r.a)
                .then_with(|| l.b.cmp(&r.b))
                .then_with(|| l.c.cmp(&r.c))
                .then_with(|| l.extra.cmp(&r.extra))
                .then_with(|| l.text.cmp(&r.text))
        }
        BRDA => {
            let mut c = l.a.cmp(&r.a).then_with(|| l.b.cmp(&r.b));
            let ln = l.flags & NUMERIC_BRANCH != 0;
            let rn = r.flags & NUMERIC_BRANCH != 0;
            if c == Ordering::Equal {
                c = match (ln, rn) {
                    (true, false) => Ordering::Less,
                    (false, true) => Ordering::Greater,
                    _ => Ordering::Equal,
                };
            }
            if c == Ordering::Equal {
                c = if ln {
                    l.c.cmp(&r.c)
                } else {
                    l.text.cmp(&r.text)
                };
            }
            if c == Ordering::Equal {
                c = (l.flags & (BR_EXCEPTION | BR_FALLTHROUGH | BR_UNREACHABLE))
                    .cmp(&(r.flags & (BR_EXCEPTION | BR_FALLTHROUGH | BR_UNREACHABLE)));
            }
            c
        }
        MCDC => {
            l.a.cmp(&r.a)
                .then_with(|| l.b.cmp(&r.b))
                .then_with(|| l.c.cmp(&r.c))
                .then_with(|| l.flags.cmp(&r.flags))
        }
        DA => l.a.cmp(&r.a),
        _ => Ordering::Equal,
    }
}

#[inline(always)]
pub fn key_cmp(l: &Record, r: &Record) -> Ordering {
    if Arc::ptr_eq(&l.path, &r.path) {
        same_path_cmp(l, r)
    } else {
        l.path.cmp(&r.path).then_with(|| same_path_cmp(l, r))
    }
}
#[inline(always)]
fn tie_cmp(l: &Record, r: &Record) -> Ordering {
    if l.kind == DA {
        l.extra.cmp(&r.extra)
    } else {
        Ordering::Equal
    }
    .then_with(|| l.flags.cmp(&r.flags))
    .then_with(|| l.value.cmp(&r.value))
    .then_with(|| l.text.cmp(&r.text))
    .then_with(|| {
        if l.kind == DA {
            Ordering::Equal
        } else {
            l.extra.cmp(&r.extra)
        }
    })
    .then_with(|| l.input_id.cmp(&r.input_id))
    .then_with(|| l.origin_line.cmp(&r.origin_line))
}
#[inline(always)]
pub fn total_cmp(l: &Record, r: &Record) -> Ordering {
    key_cmp(l, r).then_with(|| tie_cmp(l, r))
}
/// The caller has already established equal source paths.
#[inline(always)]
pub(crate) fn same_path_total_cmp(l: &Record, r: &Record) -> Ordering {
    if l.kind == DA && r.kind == DA {
        return l
            .a
            .cmp(&r.a)
            .then_with(|| l.extra.cmp(&r.extra))
            .then_with(|| l.flags.cmp(&r.flags))
            .then_with(|| l.value.cmp(&r.value))
            .then_with(|| l.text.cmp(&r.text))
            .then_with(|| l.input_id.cmp(&r.input_id))
            .then_with(|| l.origin_line.cmp(&r.origin_line));
    }
    same_path_cmp(l, r).then_with(|| tie_cmp(l, r))
}

#[inline(always)]
pub fn same_key(l: &Record, r: &Record) -> bool {
    if l.kind == EXT || r.kind == EXT {
        return false;
    }
    if l.kind == TN && r.kind == TN {
        return l.path == r.path;
    }
    key_cmp(l, r) == Ordering::Equal
}

#[cfg(test)]
mod tests {
    use super::*;
    fn rec(kind: u8, a: u64) -> Record {
        Record {
            kind,
            a,
            ..Record::default()
        }
    }
    #[test]
    fn unknown_records_are_never_merged() {
        let a = Record {
            kind: EXT,
            text: "X:1".into(),
            ..Record::default()
        };
        assert!(!same_key(&a, &a));
    }
    #[test]
    fn numeric_branches_sort_before_text_branches() {
        let numeric = Record {
            kind: BRDA,
            flags: NUMERIC_BRANCH,
            c: 10,
            ..Record::default()
        };
        let named = Record {
            kind: BRDA,
            flags: BRANCH_TEXT,
            text: "a".into(),
            ..Record::default()
        };
        assert_eq!(key_cmp(&numeric, &named), Ordering::Less);
    }
    #[test]
    fn coverage_rows_sort_by_line() {
        assert_eq!(key_cmp(&rec(DA, 2), &rec(DA, 10)), Ordering::Less);
    }
}
