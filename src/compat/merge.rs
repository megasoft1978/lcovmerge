use super::record::{checked_add, Branch, Section};
use crate::common::check_cancelled;
use crate::common::Error;
use std::collections::HashMap;
use std::io::Write;

type FunctionGroup<'a> = (
    u64,
    Option<u64>,
    Vec<(&'a String, &'a super::record::Function)>,
);

fn branch_sig(block: &[Branch]) -> Vec<char> {
    block
        .iter()
        .map(|b| if b.kind == 'v' { 'b' } else { b.kind })
        .collect()
}
fn add_branch(a: &mut Branch, b: &Branch, file: &str) -> Result<(), Error> {
    a.unreachable |= b.unreachable;
    a.hits = match (a.hits, b.hits) {
        (None, None) => None,
        (Some(x), Some(y)) => Some(checked_add(x, y, file, "branch")?),
        (None, Some(y)) => Some(y),
        (Some(x), None) => Some(x),
    };
    Ok(())
}
pub fn merge_into(dst: &mut Section, src: Section, file: &str) -> Result<(), Error> {
    for (line, (count, checksum)) in src.lines {
        let e = dst.lines.entry(line).or_insert((0, None));
        e.0 = checked_add(e.0, count, file, "DA")?;
        if let Some(c) = checksum {
            if e.1.as_ref().is_some_and(|x| x != &c) {
                return Err(Error::new(
                    2,
                    format!("{file}: checksum mismatch at {}:{}", dst.source, line),
                ));
            }
            e.1 = Some(c);
        }
    }
    for (name, f) in src.functions {
        let e = dst
            .functions
            .entry(name.clone())
            .or_insert_with(|| super::record::Function {
                line: f.line,
                end_line: f.end_line,
                hits: 0,
            });
        if e.line != f.line || e.end_line != f.end_line {
            return Err(Error::new(
                2,
                format!("{file}: inconsistent function declaration for '{name}'"),
            ));
        }
        e.hits = checked_add(e.hits, f.hits, file, "function")?;
    }
    for (line, blocks) in src.branches {
        let dest = dst.branches.entry(line).or_default();
        let mut seen: HashMap<Vec<char>, usize> = HashMap::new();
        for block in blocks {
            if block.is_empty() {
                continue;
            }
            let sig = branch_sig(&block);
            let occurrence = seen.entry(sig.clone()).or_default();
            let target = dest
                .iter_mut()
                .filter(|b| branch_sig(b) == sig)
                .nth(*occurrence);
            *occurrence += 1;
            if let Some(old) = target {
                for (a, b) in old.iter_mut().zip(block.iter()) {
                    add_branch(a, b, file)?;
                }
            } else {
                dest.push(block);
            }
        }
    }
    for (line, items) in src.mcdc {
        let dest = dst.mcdc.entry(line).or_default();
        for (key, item) in items {
            if let Some(old) = dest.get_mut(&key) {
                if old.expr != item.expr {
                    return Err(Error::new(
                        2,
                        format!(
                            "{file}: inconsistent MC/DC expression at {}:{}",
                            dst.source, line
                        ),
                    ));
                }
                old.hits = checked_add(old.hits, item.hits, file, "MC/DC")?;
                old.unreachable |= item.unreachable;
            } else {
                dest.insert(key, item);
            }
        }
    }
    if let (Some(a), Some(b)) = (&dst.version, &src.version) {
        if a != b {
            return Err(Error::new(
                2,
                format!("{file}: conflicting source versions"),
            ));
        }
    } else if dst.version.is_none() {
        dst.version = src.version;
    }
    dst.saw_coverpoint |= src.saw_coverpoint;
    dst.modern |= src.modern;
    Ok(())
}

pub fn write_sections(
    mut out: impl Write,
    sections: &[Section],
    checksums: bool,
    functions: bool,
    branches: bool,
    mcdc: bool,
) -> Result<(), Error> {
    for s in sections {
        check_cancelled()?;
        if !s.saw_coverpoint {
            continue;
        }
        writeln!(out, "TN:{}\nSF:{}", s.testcase, s.source).map_err(io_error)?;
        if let Some(v) = &s.version {
            writeln!(out, "VER:{v}").map_err(io_error)?;
        }
        if functions {
            let mut groups: Vec<FunctionGroup<'_>> = Vec::new();
            for (n, f) in &s.functions {
                if let Some(g) = groups
                    .iter_mut()
                    .find(|g| g.0 == f.line && g.1 == f.end_line)
                {
                    g.2.push((n, f));
                } else {
                    groups.push((f.line, f.end_line, vec![(n, f)]));
                }
            }
            groups.sort_by(|a, b| a.0.cmp(&b.0).then_with(|| a.2[0].0.cmp(b.2[0].0)));
            for (idx, (line, end, aliases)) in groups.iter().enumerate() {
                writeln!(
                    out,
                    "FNL:{idx},{line}{}",
                    end.map_or(String::new(), |n| format!(",{n}")).as_str()
                )
                .map_err(io_error)?;
                for (name, f) in aliases {
                    writeln!(out, "FNA:{idx},{},{}", f.hits, name).map_err(io_error)?;
                }
            }
            if !groups.is_empty() {
                let hit = groups
                    .iter()
                    .filter(|g| g.2.iter().any(|(_, f)| f.hits > 0))
                    .count();
                writeln!(out, "FNF:{}\nFNH:{hit}", groups.len()).map_err(io_error)?;
            }
        }
        if branches {
            let mut found = 0;
            let mut hit = 0;
            for (line, blocks) in &s.branches {
                let mut ordered: Vec<_> = blocks.iter().filter(|b| !b.is_empty()).collect();
                ordered.sort_by(|a, b| {
                    a.len()
                        .cmp(&b.len())
                        .then_with(|| branch_sig(a).cmp(&branch_sig(b)))
                });
                for (bi, block) in ordered.into_iter().enumerate() {
                    for b in block {
                        if !b.unreachable {
                            found += 1;
                            hit += usize::from(b.hits.is_some_and(|x| x > 0));
                        }
                        writeln!(
                            out,
                            "BRDA:{line},{}{}{bi},{},{}",
                            if b.kind == 'v' {
                                ""
                            } else if b.kind == 'e' {
                                "e"
                            } else {
                                "f"
                            },
                            if b.unreachable { "U" } else { "" },
                            b.expr,
                            b.hits.map_or("-".into(), |x| x.to_string())
                        )
                        .map_err(io_error)?;
                    }
                }
            }
            if found > 0 {
                writeln!(out, "BRF:{found}\nBRH:{hit}").map_err(io_error)?;
            }
        }
        if mcdc {
            let mut found = 0;
            let mut hit = 0;
            for (line, items) in &s.mcdc {
                let mut ids: Vec<(u64, u64)> = items.keys().map(|(g, i, _)| (*g, *i)).collect();
                ids.dedup();
                let mut active_group = None;
                let mut normalized_index = 0u64;
                for (group, index) in ids {
                    if active_group != Some(group) {
                        active_group = Some(group);
                        normalized_index = 0;
                    }
                    for sense in ['t', 'f'] {
                        if let Some(m) = items.get(&(group, index, sense)) {
                            found += 1;
                            hit += usize::from(m.hits > 0);
                            writeln!(
                                out,
                                "MCDC:{line},{}{group},{sense},{},{normalized_index},{}",
                                if m.unreachable { "U" } else { "" },
                                m.hits,
                                m.expr
                            )
                            .map_err(io_error)?;
                        }
                    }
                    normalized_index += 1;
                }
            }
            if found > 0 {
                writeln!(out, "MCF:{found}\nMCH:{hit}").map_err(io_error)?;
            }
        }
        let found = s.lines.len();
        let hit = s.lines.values().filter(|(n, _)| *n > 0).count();
        for (line, (count, checksum)) in &s.lines {
            check_cancelled()?;
            if checksums {
                if let Some(c) = checksum {
                    writeln!(out, "DA:{line},{count},{c}").map_err(io_error)?;
                } else {
                    writeln!(out, "DA:{line},{count}").map_err(io_error)?;
                }
            } else {
                writeln!(out, "DA:{line},{count}").map_err(io_error)?;
            }
        }
        writeln!(out, "LF:{found}\nLH:{hit}\nend_of_record").map_err(io_error)?;
    }
    out.flush().map_err(io_error)
}
fn io_error(e: std::io::Error) -> Error {
    Error::new(3, format!("output write failed: {e}"))
}
