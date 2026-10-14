use lcovmerge::{
    common::Options,
    model::*,
    parser::Parser,
    path::{rewrite_path, wildcard_match},
};
use std::{
    cmp::Ordering,
    io::{BufRead, Read},
    panic::{catch_unwind, AssertUnwindSafe},
    sync::Arc,
};

fn rec(seed: u64, kind: u8) -> Record {
    let mut x = seed.wrapping_add(0x9e3779b97f4a7c15);
    let mut next = || {
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        x
    };
    let paths = ["/a", "/b", "C:\\src\\x.c", "/z/edge"];
    let texts = ["", "a", "aa", "2", "10", "z"];
    Record {
        path: Arc::from(paths[next() as usize % paths.len()]),
        text: texts[next() as usize % texts.len()].into(),
        extra: texts[next() as usize % texts.len()].into(),
        a: next(),
        b: next(),
        c: next(),
        value: next(),
        origin_line: next(),
        input_id: next() as u32,
        kind,
        flags: (next() as u8)
            | if matches!(kind, GROUP | FN) && seed % 3 == 0 {
                FN_NEW
            } else {
                0
            },
    }
}
fn sign(o: Ordering) -> i8 {
    match o {
        Ordering::Less => -1,
        Ordering::Equal => 0,
        Ordering::Greater => 1,
    }
}

#[test]
fn comparator_order_laws_seeded_mixed_families_and_u64_edges() {
    let kinds = [TN, SECTION, GROUP, FN, FNDA, BRDA, MCDC, DA, EXT];
    let mut rows: Vec<_> = (0..84)
        .map(|i| rec(i * 7919 + 0x4c434f56, kinds[i as usize % kinds.len()]))
        .collect();
    for (i, r) in rows.iter_mut().enumerate() {
        if i % 7 == 0 {
            r.a = u64::MAX - (i as u64 % 3);
        }
    }
    for i in 0..rows.len() {
        for j in 0..rows.len() {
            assert_eq!(
                sign(key_cmp(&rows[i], &rows[j])),
                -sign(key_cmp(&rows[j], &rows[i]))
            );
            assert_eq!(
                sign(total_cmp(&rows[i], &rows[j])),
                -sign(total_cmp(&rows[j], &rows[i]))
            );
            for k in (j..rows.len()).step_by(3) {
                if key_cmp(&rows[i], &rows[j]) != Ordering::Greater
                    && key_cmp(&rows[j], &rows[k]) != Ordering::Greater
                {
                    assert_ne!(key_cmp(&rows[i], &rows[k]), Ordering::Greater);
                }
                if total_cmp(&rows[i], &rows[j]) != Ordering::Greater
                    && total_cmp(&rows[j], &rows[k]) != Ordering::Greater
                {
                    assert_ne!(total_cmp(&rows[i], &rows[k]), Ordering::Greater);
                }
            }
        }
    }
}

#[test]
fn byte_globs_and_path_boundaries_cover_both_separator_styles() {
    assert!(wildcard_match("src/[a-c]?.c", "src/b1.c"));
    assert!(!wildcard_match("src/[!a-c]*", "src/beta"));
    assert!(wildcard_match("x\\*.c", "x\\a.c"));
    assert!(!wildcard_match("dir/", "dir"));
    let mut o = Options::default();
    o.rebases.push(("/work/project".into(), "C:\\new".into()));
    assert_eq!(
        rewrite_path(&o, "/work/projectile/a.c").unwrap(),
        "/work/projectile/a.c"
    );
    assert_eq!(
        rewrite_path(&o, "/work/project//src/a.c").unwrap(),
        "C:\\new\\src\\a.c"
    );
    o.rebases.clear();
    o.rebases.push(("C:\\work".into(), "/mapped".into()));
    assert_eq!(
        rewrite_path(&o, "C:\\work\\src\\a.c").unwrap(),
        "/mapped/src/a.c"
    );
}

#[derive(Clone)]
struct Chunks {
    data: Arc<[u8]>,
    pos: usize,
    max: usize,
}
impl Read for Chunks {
    fn read(&mut self, out: &mut [u8]) -> std::io::Result<usize> {
        let n = self.fill_buf()?.len().min(out.len());
        out[..n].copy_from_slice(&self.fill_buf()?[..n]);
        self.consume(n);
        Ok(n)
    }
}
impl BufRead for Chunks {
    fn fill_buf(&mut self) -> std::io::Result<&[u8]> {
        let end = (self.pos + self.max).min(self.data.len());
        Ok(&self.data[self.pos..end])
    }
    fn consume(&mut self, n: usize) {
        self.pos = (self.pos + n).min(self.data.len());
    }
}
fn parser(data: Vec<u8>, chunk: usize) -> Parser<Chunks> {
    Parser::new(
        Chunks {
            data: data.into(),
            pos: 0,
            max: chunk.max(1),
        },
        "property.info".into(),
        0,
        Arc::new(Options::default()),
    )
}
fn parse_all(p: &mut Parser<Chunks>) -> Result<(), i32> {
    while p.next_record().map_err(|e| e.code)?.is_some() {}
    Ok(())
}

#[test]
fn parser_bytes_boundaries_and_summary_validation() {
    for chunk in [1, 2, 3, 7, 16, 4096] {
        let mut p = parser(b"TN:x\r\nSF:a.c\r\nDA:1,2\r\nLF:1\r\nLH:1".to_vec(), chunk);
        assert_eq!(parse_all(&mut p), Ok(()));
    }
    for bytes in [b"\0".as_slice(), &[0xff], &[0xc0, 0xaf]] {
        let mut p = parser(bytes.to_vec(), 1);
        assert!(parse_all(&mut p).is_err());
    }
    let mut p = parser([b"SF:a.c\n".as_slice(), &vec![b'x'; 1 << 20]].concat(), 113);
    assert!(parse_all(&mut p).is_err());
    for bad in [
        "LF:x\n",
        "FNF:-1\n",
        "FNL:1,x\n",
        "FNL:1,2,3\nFNA:2,1,name\n",
    ] {
        let mut p = parser(format!("SF:a.c\n{bad}").into_bytes(), 2);
        assert!(parse_all(&mut p).is_err(), "{bad}");
    }
}

#[test]
fn arbitrary_bounded_parser_input_never_panics() {
    let mut state = 0x51ed270bu32;
    for n in 0..256usize {
        let len = n % 257;
        let mut bytes = Vec::with_capacity(len);
        for _ in 0..len {
            state ^= state << 13;
            state ^= state >> 17;
            state ^= state << 5;
            bytes.push(state as u8);
        }
        let result = catch_unwind(AssertUnwindSafe(|| {
            let mut p = parser(bytes, n % 13 + 1);
            let _ = parse_all(&mut p);
        }));
        assert!(result.is_ok());
    }
}

#[test]
#[ignore = "deterministic mutation soak; run with make fuzz"]
fn fuzz_corpus_mutations() {
    let corpus: &[&[u8]] = &[
        include_bytes!("fuzz-corpus/basic.info"),
        include_bytes!("fuzz-corpus/mcdc.info"),
        include_bytes!("fuzz-corpus/u64-max.info"),
        include_bytes!("fuzz-corpus/branch-dash.info"),
        include_bytes!("fuzz-corpus/crlf-eof.info"),
        include_bytes!("fuzz-corpus/unknown-checksum.info"),
        b"TN:x\nSF:/fn.c\nFNL:0,1,3\nFNA:0,1,name,with,commas\nend_of_record\n",
    ];
    let runs: usize = std::env::var("FUZZ_RUNS")
        .ok()
        .and_then(|n| n.parse().ok())
        .unwrap_or(250_000);
    let mut state = 0x513fa9e2u32;
    let mut random = || {
        state ^= state << 13;
        state ^= state >> 17;
        state ^= state << 5;
        state as usize
    };
    let base_options = Options::default();
    for index in 0..runs {
        let mut bytes = corpus[random() % corpus.len()].to_vec();
        for _ in 0..(random() % 8 + 1) {
            match random() % 4 {
                0 if !bytes.is_empty() => {
                    let at = random() % bytes.len();
                    bytes.remove(at);
                }
                1 => {
                    let at = random() % (bytes.len() + 1);
                    bytes.insert(at, random() as u8);
                }
                2 if !bytes.is_empty() => {
                    let at = random() % bytes.len();
                    bytes[at] = random() as u8;
                }
                _ => bytes.extend_from_slice(corpus[random() % corpus.len()]),
            }
        }
        let mut options = Options {
            function_data: index % 4 != 0,
            branch_coverage: index % 3 != 0,
            ..base_options.clone()
        };
        if index % 7 == 0 {
            options.excludes.push("*".into());
        }
        let mut p = Parser::new(
            bytes.as_slice(),
            "mutation.info".into(),
            0,
            Arc::new(options),
        );
        loop {
            match p.next_record() {
                Ok(Some(row)) => {
                    assert!((TN..=EXT).contains(&row.kind));
                    assert!(
                        row.memory_size()
                            <= 3 * lcovmerge::common::MAX_LINE + std::mem::size_of::<Record>()
                    );
                    p.recycle_record(row);
                }
                Ok(None) => break,
                Err(error) => {
                    assert_eq!(error.code, 2);
                    break;
                }
            }
        }
    }
    println!("corpus_mutations={runs} PASS");
}
