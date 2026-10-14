use crate::common::{check_cancelled, Error, Options, IO_BLOCK, MAX_LINE, MIN_WORKER_BUDGET};
use crate::emit::{merge_aggregate, OutputState};
use crate::model::{
    same_key, same_path_total_cmp, total_cmp, Record, BRANCH_TEXT, BRDA, BR_EXCEPTION,
    BR_FALLTHROUGH, BR_UNREACHABLE, DA, DASH, EXT, FN, FNDA, FN_NEW, GROUP, MCDC, MCDC_TRUE,
    MCDC_UNREACHABLE, NUMERIC_BRANCH, SECTION, TN,
};
use crate::parser::Parser;
use std::cmp::Ordering;
#[cfg(feature = "test-partition")]
use std::fs::OpenOptions;
use std::fs::{self, File};
use std::io::{self, BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::Path;
use std::sync::{
    atomic::{AtomicUsize, Ordering as AtomicOrdering},
    Arc,
};

const RUN_FANIN: usize = 8; // reserves descriptor headroom for low-RLIMIT environments

fn error(message: impl Into<String>) -> Error {
    Error::new(3, message.into())
}
fn output_creation_error(output: &Path, e: io::Error) -> Error {
    let target = Path::new(output);
    let parent = target
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let detail = match e.kind() {
        io::ErrorKind::NotFound => "No such file or directory".to_owned(),
        io::ErrorKind::PermissionDenied => "Permission denied".to_owned(),
        io::ErrorKind::AlreadyExists => "File exists".to_owned(),
        _ => e
            .to_string()
            .split(" (os error")
            .next()
            .unwrap_or("I/O error")
            .to_owned(),
    };
    Error::at(3,output,0,format!("cannot create output temporary file: {detail}; parent directory '{}' must exist and be writable",parent.display()))
}
fn descriptor_limit() -> usize {
    #[cfg(unix)]
    unsafe {
        let mut limit: libc::rlimit = std::mem::zeroed();
        if libc::getrlimit(libc::RLIMIT_NOFILE, &mut limit) == 0 {
            return usize::try_from(limit.rlim_cur).unwrap_or(usize::MAX);
        }
    }
    256
}

struct TempPath(tempfile::TempPath);
impl TempPath {
    fn path(&self) -> &Path {
        self.0.as_ref()
    }
}

fn make_temp(dir: &Path, prefix: &str) -> io::Result<(TempPath, File)> {
    let named = tempfile::Builder::new()
        .prefix(&format!("{prefix}-"))
        .tempfile_in(dir)?;
    let (file, path) = named.into_parts();
    Ok((TempPath(path), file))
}

fn output_stage(output: &Path) -> io::Result<(TempPath, File)> {
    let target = Path::new(output);
    let parent = target
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    make_temp(parent, "lcovmerge")
}

fn commit_stage(stage: TempPath, output: &Path) -> Result<(), Error> {
    check_cancelled()?;
    fs::rename(stage.path(), output)
        .map_err(|_| error(format!("{}:0: cannot replace output", output.display())))?;
    Ok(())
}

fn input_reader(name: &Path) -> io::Result<Box<dyn Read + Send>> {
    if name == Path::new("-") {
        crate::common::stdin_reader()
    } else {
        Ok(Box::new(File::open(name)?))
    }
}
fn open_parser(
    name: &Path,
    id: u32,
    options: Arc<Options>,
) -> io::Result<Parser<BufReader<Box<dyn Read + Send>>>> {
    let capacity = read_capacity(&options);
    let mut parser = Parser::new(
        BufReader::with_capacity(capacity, input_reader(name)?),
        name.to_string_lossy().into_owned(),
        id,
        options,
    );
    parser.account_reader_capacity(capacity);
    Ok(parser)
}

fn read_capacity(options: &Options) -> usize {
    if options.inputs.len() <= 32 {
        IO_BLOCK * 4
    } else {
        IO_BLOCK
    }
}

const RUN_HEADER: usize = 58;

#[inline]
fn header_u32(header: &[u8; RUN_HEADER], offset: usize) -> u32 {
    u32::from_le_bytes(header[offset..offset + 4].try_into().unwrap())
}
#[inline]
fn header_u64(header: &[u8; RUN_HEADER], offset: usize) -> u64 {
    u64::from_le_bytes(header[offset..offset + 8].try_into().unwrap())
}
fn write_record(w: &mut impl Write, r: &Record, previous: &mut String) -> io::Result<()> {
    let path = r.path.as_bytes();
    let text = r.text.as_bytes();
    let extra = r.extra.as_bytes();
    if path.is_empty() || path.contains(&0) || text.contains(&0) || extra.contains(&0) {
        return Err(io::ErrorKind::InvalidData.into());
    }
    let new_path = path != previous.as_bytes();
    let mut header = [0u8; RUN_HEADER];
    let lengths = [
        if new_path { path.len() } else { 0 },
        text.len(),
        extra.len(),
    ];
    for (i, length) in lengths.into_iter().enumerate() {
        let length = u32::try_from(length).map_err(|_| io::ErrorKind::InvalidData)?;
        header[i * 4..i * 4 + 4].copy_from_slice(&length.to_le_bytes());
    }
    header[12] = r.kind;
    header[13] = r.flags;
    for (i, number) in [r.a, r.b, r.c, r.value, r.origin_line]
        .into_iter()
        .enumerate()
    {
        header[14 + i * 8..22 + i * 8].copy_from_slice(&number.to_le_bytes());
    }
    header[54..58].copy_from_slice(&r.input_id.to_le_bytes());
    w.write_all(&header)?;
    if new_path {
        w.write_all(path)?;
        previous.clear();
        previous.push_str(&r.path);
    }
    w.write_all(text)?;
    w.write_all(&[0])?;
    w.write_all(extra)?;
    w.write_all(&[0])
}

#[derive(Debug)]
struct RunReader {
    file: BufReader<File>,
    path: Arc<str>,
    row: Option<Record>,
    spare_text: Vec<u8>,
    spare_extra: Vec<u8>,
}

fn read_payload(reader: &mut impl Read, spare: &mut Vec<u8>, length: usize) -> io::Result<String> {
    if length == 0 {
        let mut nul = [0];
        reader.read_exact(&mut nul)?;
        return if nul[0] == 0 {
            Ok(String::new())
        } else {
            Err(io::ErrorKind::InvalidData.into())
        };
    }
    let mut bytes = std::mem::take(spare);
    bytes.resize(length + 1, 0);
    reader.read_exact(&mut bytes)?;
    if bytes[length] != 0 || bytes[..length].contains(&0) {
        return Err(io::ErrorKind::InvalidData.into());
    }
    bytes.truncate(length);
    String::from_utf8(bytes).map_err(|_| io::ErrorKind::InvalidData.into())
}

impl RunReader {
    fn new(path: &Path) -> io::Result<Self> {
        let f = File::open(path)?;
        let mut x = Self {
            file: BufReader::with_capacity(IO_BLOCK, f),
            path: Arc::from(""),
            row: None,
            spare_text: Vec::new(),
            spare_extra: Vec::new(),
        };
        x.next()?;
        Ok(x)
    }
    fn recycle(&mut self, row: Record) {
        if row.text.capacity() > self.spare_text.capacity() {
            self.spare_text = row.text.into_bytes();
        }
        if row.extra.capacity() > self.spare_extra.capacity() {
            self.spare_extra = row.extra.into_bytes();
        }
    }
    fn next(&mut self) -> io::Result<bool> {
        let mut first = [0u8; 1];
        if self.file.read(&mut first)? == 0 {
            self.row = None;
            return Ok(false);
        }
        let mut header = [0u8; RUN_HEADER];
        header[0] = first[0];
        self.file.read_exact(&mut header[1..])?;
        let path_len = header_u32(&header, 0) as usize;
        let text_len = header_u32(&header, 4) as usize;
        let extra_len = header_u32(&header, 8) as usize;
        if path_len > MAX_LINE || text_len > MAX_LINE || extra_len > MAX_LINE {
            return Err(io::ErrorKind::InvalidData.into());
        }
        let kind_flags = [header[12], header[13]];
        let allowed = match kind_flags[0] {
            TN | SECTION | FNDA | DA => 0,
            GROUP | FN => FN_NEW,
            BRDA => {
                NUMERIC_BRANCH | BRANCH_TEXT | DASH | BR_EXCEPTION | BR_FALLTHROUGH | BR_UNREACHABLE
            }
            MCDC => MCDC_TRUE | MCDC_UNREACHABLE,
            EXT => 0,
            _ => return Err(io::ErrorKind::InvalidData.into()),
        };
        if kind_flags[1] & !allowed != 0 {
            return Err(io::ErrorKind::InvalidData.into());
        }
        let a = header_u64(&header, 14);
        let b = header_u64(&header, 22);
        let c = header_u64(&header, 30);
        let value = header_u64(&header, 38);
        let origin_line = header_u64(&header, 46);
        let input_id = header_u32(&header, 54);
        if path_len != 0 {
            let mut raw = vec![0; path_len];
            self.file.read_exact(&mut raw)?;
            if raw.contains(&0) {
                return Err(io::ErrorKind::InvalidData.into());
            }
            self.path = Arc::from(String::from_utf8(raw).map_err(|_| io::ErrorKind::InvalidData)?);
        }
        if self.path.is_empty() {
            return Err(io::ErrorKind::InvalidData.into());
        }
        let text = read_payload(&mut self.file, &mut self.spare_text, text_len)?;
        let extra = read_payload(&mut self.file, &mut self.spare_extra, extra_len)?;
        self.row = Some(Record {
            path: self.path.clone(),
            text,
            extra,
            a,
            b,
            c,
            value,
            origin_line,
            input_id,
            kind: kind_flags[0],
            flags: kind_flags[1],
        });
        Ok(true)
    }
}

fn sort_flush(rows: &mut Vec<Record>, dir: &Path, runs: &mut Vec<TempPath>) -> io::Result<()> {
    if rows.is_empty() {
        return Ok(());
    }
    rows.sort_unstable_by(total_cmp);
    let (path, file) = make_temp(dir, "lcovmerge-run")?;
    let mut w = BufWriter::with_capacity(IO_BLOCK, file);
    let mut previous = String::new();
    for r in rows.iter() {
        write_record(&mut w, r, &mut previous)?;
    }
    w.flush()?;
    runs.push(path);
    rows.clear();
    Ok(())
}

fn heap_push(heap: &mut Vec<usize>, value: usize, readers: &[RunReader]) {
    let mut i = heap.len();
    heap.push(value);
    while i > 0 {
        let p = (i - 1) / 2;
        if total_cmp(
            readers[heap[p]].row.as_ref().unwrap(),
            readers[value].row.as_ref().unwrap(),
        ) != Ordering::Greater
        {
            break;
        }
        heap[i] = heap[p];
        i = p;
    }
    heap[i] = value;
}
fn heap_down(heap: &mut [usize], mut i: usize, readers: &[RunReader]) {
    let n = heap.len();
    loop {
        let l = i * 2 + 1;
        if l >= n {
            break;
        }
        let mut c = l;
        if l + 1 < n
            && total_cmp(
                readers[heap[l + 1]].row.as_ref().unwrap(),
                readers[heap[l]].row.as_ref().unwrap(),
            ) == Ordering::Less
        {
            c = l + 1;
        }
        if total_cmp(
            readers[heap[i]].row.as_ref().unwrap(),
            readers[heap[c]].row.as_ref().unwrap(),
        ) != Ordering::Greater
        {
            break;
        }
        heap.swap(i, c);
        i = c;
    }
}
fn pop_heap(heap: &mut Vec<usize>, readers: &[RunReader]) -> Option<usize> {
    if heap.is_empty() {
        return None;
    }
    let first = heap[0];
    let last = heap.pop().unwrap();
    if !heap.is_empty() {
        heap[0] = last;
        heap_down(heap, 0, readers);
    }
    Some(first)
}

fn merge_runs(paths: &[TempPath], output: &mut impl Write) -> io::Result<()> {
    let mut readers = paths
        .iter()
        .map(|p| RunReader::new(p.path()))
        .collect::<io::Result<Vec<_>>>()?;
    let mut heap = Vec::with_capacity(readers.len());
    for i in 0..readers.len() {
        if readers[i].row.is_some() {
            heap_push(&mut heap, i, &readers);
        }
    }
    let mut previous = String::new();
    while let Some(i) = pop_heap(&mut heap, &readers) {
        check_cancelled().map_err(|_| io::Error::new(io::ErrorKind::Interrupted, "interrupted"))?;
        let row = readers[i].row.take().unwrap();
        write_record(output, &row, &mut previous)?;
        readers[i].recycle(row);
        if readers[i].next()? {
            heap_push(&mut heap, i, &readers);
        }
    }
    let _ = previous;
    Ok(())
}

fn merge_final(
    paths: &[TempPath],
    writer: &mut impl Write,
    options: &Options,
    diag: &mut impl Write,
) -> io::Result<crate::emit::Totals> {
    let mut readers = paths
        .iter()
        .map(|p| RunReader::new(p.path()))
        .collect::<io::Result<Vec<_>>>()?;
    let mut heap = Vec::with_capacity(readers.len());
    for i in 0..readers.len() {
        if readers[i].row.is_some() {
            heap_push(&mut heap, i, &readers);
        }
    }
    let mut output = OutputState::new(writer);
    let mut aggregate: Option<Record> = None;
    while let Some(i) = pop_heap(&mut heap, &readers) {
        check_cancelled().map_err(|_| io::Error::new(io::ErrorKind::Interrupted, "interrupted"))?;
        let mut consumed = readers[i].row.take();
        let row = consumed.as_ref().unwrap();
        if let Some(ref mut a) = aggregate {
            if (row.kind == SECTION || (row.kind == crate::model::TN && row.text.is_empty()))
                && a.path == row.path
                && a.kind != SECTION
                && a.kind != crate::model::TN
            {
                // Duplicate source marker; keep the current data key active.
            } else if same_key(a, row) {
                let mut d = Vec::new();
                let merged = merge_aggregate(a, row, options, &mut d);
                diag.write_all(&d)?;
                if let Err(e) = merged {
                    diag.flush()?;
                    let detail = String::from_utf8_lossy(&d).trim().to_owned();
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        if detail.is_empty() {
                            e.to_string()
                        } else {
                            detail
                        },
                    ));
                }
            } else {
                output.record(a)?;
                let old = aggregate.replace(consumed.take().unwrap()).unwrap();
                readers[i].recycle(old);
            }
        } else {
            aggregate = consumed.take();
        }
        if let Some(row) = consumed {
            readers[i].recycle(row);
        }
        if readers[i].next()? {
            heap_push(&mut heap, i, &readers);
        }
    }
    if let Some(a) = aggregate {
        output.record(&a)?;
    }
    output.finish_file()?;
    Ok(output.totals.clone())
}

fn reduce_runs(runs: &mut Vec<TempPath>, options: &Options) -> io::Result<()> {
    if runs.len() < 2 {
        return Ok(());
    }
    let mem_fanin = options.mem_limit / (2usize << 20);
    let fd_fanin = descriptor_limit().saturating_sub(16);
    let fanin = mem_fanin.min(fd_fanin).min(RUN_FANIN);
    if fanin < 2 {
        return Err(io::Error::other(
            "memory or descriptor limit is too small to merge temporary runs",
        ));
    }
    while runs.len() > fanin {
        let mut next = Vec::new();
        for group in runs.chunks(fanin) {
            if group.len() == 1 {
                let (new, file) = make_temp(&options.tmpdir, "lcovmerge-keep")?;
                drop(file);
                fs::rename(group[0].path(), new.path())?;
                next.push(new);
                continue;
            }
            let (path, file) = make_temp(&options.tmpdir, "lcovmerge-merge")?;
            {
                let mut w = BufWriter::with_capacity(IO_BLOCK, file);
                merge_runs(group, &mut w)?;
                w.flush()?;
            }
            next.push(path);
        }
        *runs = next;
    }
    Ok(())
}

fn parser_next_to_spool(
    p: &mut Parser<BufReader<Box<dyn Read + Send>>>,
    spool: &mut impl Write,
) -> Result<Option<Record>, Error> {
    let mut record = None;
    parser_next_into_spool(p, spool, &mut record)?;
    Ok(record)
}

fn parser_next_into_spool(
    p: &mut InputParser,
    spool: &mut impl Write,
    record: &mut Option<Record>,
) -> Result<bool, Error> {
    check_cancelled()?;
    let mut sink = |diagnostic: &str| {
        spool
            .write_all(diagnostic.as_bytes())
            .map_err(|e| error(e.to_string()))?;
        spool.write_all(b"\n").map_err(|e| error(e.to_string()))
    };
    p.next_into_with_diagnostics(record, &mut sink)
}

#[inline(always)]
fn heads_cmp(a: usize, b: usize, heads: &[Option<Record>], ranks: &[usize]) -> Ordering {
    ranks[a]
        .cmp(&ranks[b])
        .then_with(|| same_path_total_cmp(heads[a].as_ref().unwrap(), heads[b].as_ref().unwrap()))
}

fn share_head_path<R: io::BufRead>(
    row: &mut Record,
    parser: &mut Parser<R>,
    heads: &[Option<Record>],
) {
    if let Some(peer) = heads.iter().flatten().find(|peer| peer.path == row.path) {
        row.path.clone_from(&peer.path);
        parser.share_current_path(&row.path);
    }
}

fn rank_heads(heads: &[Option<Record>], order: &mut Vec<usize>, ranks: &mut [usize]) {
    order.clear();
    order.extend((0..heads.len()).filter(|&i| heads[i].is_some()));
    order.sort_unstable_by(|&a, &b| {
        heads[a]
            .as_ref()
            .unwrap()
            .path
            .cmp(&heads[b].as_ref().unwrap().path)
    });
    let mut rank = 0;
    for pos in 0..order.len() {
        if pos > 0
            && heads[order[pos - 1]].as_ref().unwrap().path
                != heads[order[pos]].as_ref().unwrap().path
        {
            rank += 1;
        }
        ranks[order[pos]] = rank;
    }
}

struct HeadTree {
    nodes: Vec<usize>,
    leaves: usize,
}

impl HeadTree {
    fn new(heads: &[Option<Record>], ranks: &[usize]) -> Self {
        let leaves = heads.len().next_power_of_two();
        let mut nodes = vec![usize::MAX; leaves * 2];
        for (i, head) in heads.iter().enumerate() {
            if head.is_some() {
                nodes[leaves + i] = i;
            }
        }
        for node in (1..leaves).rev() {
            nodes[node] = Self::winner(nodes[node * 2], nodes[node * 2 + 1], heads, ranks);
        }
        Self { nodes, leaves }
    }

    #[inline(always)]
    fn winner(a: usize, b: usize, heads: &[Option<Record>], ranks: &[usize]) -> usize {
        if a == usize::MAX {
            b
        } else if b == usize::MAX || heads_cmp(a, b, heads, ranks) != Ordering::Greater {
            a
        } else {
            b
        }
    }

    #[inline(always)]
    fn first(&self) -> Option<usize> {
        let first = self.nodes[1];
        (first != usize::MAX).then_some(first)
    }

    #[inline(always)]
    fn update(&mut self, input: usize, heads: &[Option<Record>], ranks: &[usize]) {
        let leaf = self.leaves + input;
        self.nodes[leaf] = if heads[input].is_some() {
            input
        } else {
            usize::MAX
        };
        let mut node = leaf / 2;
        while node != 0 {
            self.nodes[node] =
                Self::winner(self.nodes[node * 2], self.nodes[node * 2 + 1], heads, ranks);
            node /= 2;
        }
    }
}

type DirectResult = Option<(TempPath, u64, u64, TempPath, crate::emit::Totals)>;
type PartitionResult = Option<(TempPath, u64, u64, usize, crate::emit::Totals)>;

fn direct(options: &Options) -> Result<DirectResult, Error> {
    if options.output == Path::new("-")
        || options.inputs.is_empty()
        || options.inputs.iter().any(|s| s.as_os_str() == "-")
        || descriptor_limit() <= 8
        || options.inputs.len() > descriptor_limit().saturating_sub(8)
    {
        return Ok(None);
    }
    let direct_budget = (options.mem_limit / 2).min(16 << 20);
    let direct_fixed = options
        .inputs
        .len()
        .saturating_mul(IO_BLOCK + std::mem::size_of::<Record>() * 2)
        .saturating_add(3 * MAX_LINE);
    if direct_fixed > direct_budget {
        return Ok(None);
    }
    let options_arc = Arc::new(options.clone());
    let mut parsers = Vec::with_capacity(options.inputs.len());
    for (i, name) in options.inputs.iter().enumerate() {
        let p = match open_parser(name, i as u32, options_arc.clone()) {
            Ok(p) => p,
            Err(e) if e.raw_os_error() == Some(libc::EMFILE) => return Ok(None),
            Err(e) => return Err(Error::at(3, name, 0, format!("cannot open input: {e}"))),
        };
        parsers.push(p);
    }
    let (stage, file) = match output_stage(&options.output) {
        Ok(v) => v,
        Err(error) => return Err(output_creation_error(&options.output, error)),
    };
    let (diag_path, diag_file) =
        match make_temp(output_parent(&options.output), "lcovmerge-diagnostics") {
            Ok(v) => v,
            Err(_) => return Ok(None),
        };
    let mut diag_writer = BufWriter::with_capacity(IO_BLOCK, diag_file);
    let mut writer = BufWriter::with_capacity(IO_BLOCK, file);
    let mut states = OutputState::new(&mut writer);
    let mut heads: Vec<Option<Record>> = Vec::with_capacity(parsers.len());
    let mut previous: Vec<Option<Record>> = (0..parsers.len()).map(|_| None).collect();
    let mut records = 0u64;
    for p in &mut parsers {
        match parser_next_to_spool(p, &mut diag_writer)? {
            Some(mut r) => {
                share_head_path(&mut r, p, &heads);
                heads.push(Some(r));
            }
            None => heads.push(None),
        }
    }
    let mut input_usage: Vec<usize> = (0..parsers.len())
        .map(|i| input_memory_use(&parsers[i], heads[i].as_ref(), previous[i].as_ref()))
        .collect();
    let mut total_input_usage = input_usage.iter().sum::<usize>();
    let fixed_usage = 2 * IO_BLOCK
        + (heads.len() * 4 + heads.len().next_power_of_two() * 2) * std::mem::size_of::<usize>();
    if total_input_usage.saturating_add(fixed_usage) > direct_budget {
        return Ok(None);
    }
    let mut ranks = vec![0; heads.len()];
    let mut rank_order = Vec::with_capacity(heads.len());
    rank_heads(&heads, &mut rank_order, &mut ranks);
    let mut tree = HeadTree::new(&heads, &ranks);
    let mut aggregate: Option<Record> = None;
    loop {
        check_cancelled()?;
        let Some(i) = tree.first() else {
            break;
        };
        let row = heads[i].take().unwrap();
        if previous[i]
            .as_ref()
            .is_some_and(|p| total_cmp(p, &row) == Ordering::Greater)
        {
            return Ok(None);
        }
        records = records.saturating_add(1);
        if let Some(ref mut a) = aggregate {
            if (row.kind == SECTION || (row.kind == crate::model::TN && row.text.is_empty()))
                && a.path == row.path
                && a.kind != SECTION
                && a.kind != crate::model::TN
            {
                // Duplicate source marker; keep the current data key active.
            } else if same_key(a, &row) {
                let mut d = Vec::new();
                let merged = merge_aggregate(a, &row, options, &mut d);
                diag_writer
                    .write_all(&d)
                    .map_err(|e| error(e.to_string()))?;
                if merged.is_err() {
                    return Ok(None);
                }
            } else {
                states
                    .record(a)
                    .map_err(|e| error(format!("cannot write output: {e}")))?;
                a.clone_from(&row);
            }
        } else {
            aggregate = Some(row.clone());
        }
        if let Some(old) = previous[i].replace(row) {
            parsers[i].recycle_record(old);
        }
        parser_next_into_spool(&mut parsers[i], &mut diag_writer, &mut heads[i])?;
        let changed_path = heads[i].as_ref().is_some_and(|next| {
            previous[i]
                .as_ref()
                .is_none_or(|old| !Arc::ptr_eq(&old.path, &next.path))
        });
        if changed_path {
            let mut next = heads[i].take().unwrap();
            share_head_path(&mut next, &mut parsers[i], &heads);
            heads[i] = Some(next);
            rank_heads(&heads, &mut rank_order, &mut ranks);
        }
        tree.update(i, &heads, &ranks);
        let current = input_memory_use(&parsers[i], heads[i].as_ref(), previous[i].as_ref());
        total_input_usage = total_input_usage
            .saturating_sub(input_usage[i])
            .saturating_add(current);
        input_usage[i] = current;
        let need = total_input_usage
            .saturating_add(fixed_usage)
            .saturating_add(aggregate.as_ref().map_or(0, record_memory_use))
            .saturating_add(states.memory_size());
        if need > direct_budget {
            return Ok(None);
        }
    }
    if let Some(a) = aggregate {
        states
            .record(&a)
            .map_err(|e| error(format!("cannot write output: {e}")))?;
    }
    states
        .finish_file()
        .map_err(|e| error(format!("cannot finish output: {e}")))?;
    let totals = states.totals.clone();
    let bytes = parsers.iter().fold(0u64, |n, p| n.saturating_add(p.bytes));
    drop(states);
    writer
        .flush()
        .map_err(|e| error(format!("cannot flush output: {e}")))?;
    diag_writer.flush().map_err(|e| error(e.to_string()))?;
    Ok(Some((stage, bytes, records, diag_path, totals)))
}

fn record_memory_use(record: &Record) -> usize {
    std::mem::size_of::<Record>()
        .saturating_add(record.path.len())
        .saturating_add(record.text.capacity())
        .saturating_add(record.extra.capacity())
}

type InputParser = Parser<BufReader<Box<dyn Read + Send>>>;

fn input_memory_use(
    parser: &InputParser,
    head: Option<&Record>,
    previous: Option<&Record>,
) -> usize {
    parser
        .buffer_capacity()
        .saturating_add(IO_BLOCK)
        .saturating_add(head.map_or(0, record_memory_use))
        .saturating_add(previous.map_or(0, record_memory_use))
}

fn partition_trace(event: &str) {
    #[cfg(feature = "test-partition")]
    if let Ok(path) = std::env::var("LCOVMERGE_TEST_PARTITION_TRACE") {
        if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(path) {
            let _ = writeln!(f, "{event}");
        }
    }
    #[cfg(not(feature = "test-partition"))]
    let _ = event;
}

fn partition_want(options: &Options) -> usize {
    #[cfg(feature = "test-partition")]
    if let Ok(s) = std::env::var("LCOVMERGE_TEST_PARTITION") {
        if let Ok(n) = s.parse::<usize>() {
            if (2..=32).contains(&n) {
                return n;
            }
        }
    }
    if options.jobs_explicit && options.jobs >= 2 {
        options.jobs.min(32)
    } else {
        0
    }
}

struct PartitionOutput {
    stage: TempPath,
    totals: crate::emit::Totals,
    records: u64,
}

fn partition_next(
    parser: &mut Parser<BufReader<Box<dyn Read + Send>>>,
    diagnosed: &mut bool,
) -> Option<Record> {
    let mut sink = |_d: &str| {
        *diagnosed = true;
        Ok(())
    };
    match parser.next_record_with_diagnostics(&mut sink) {
        Ok(row) => row,
        Err(_) => {
            *diagnosed = true;
            None
        }
    }
}

struct BudgetAccount {
    shared: Arc<AtomicUsize>,
    used: usize,
}

impl BudgetAccount {
    fn update(&mut self, current: usize, limit: usize) -> bool {
        if current <= self.used {
            return true;
        }
        let mut total = self.shared.load(AtomicOrdering::Acquire);
        loop {
            let Some(next) = total
                .checked_sub(self.used)
                .and_then(|n| n.checked_add(current))
            else {
                return false;
            };
            if next > limit {
                return false;
            }
            match self.shared.compare_exchange_weak(
                total,
                next,
                AtomicOrdering::AcqRel,
                AtomicOrdering::Acquire,
            ) {
                Ok(_) => {
                    self.used = current;
                    return true;
                }
                Err(actual) => total = actual,
            }
        }
    }
}

impl Drop for BudgetAccount {
    fn drop(&mut self) {
        self.shared.fetch_sub(self.used, AtomicOrdering::AcqRel);
    }
}

fn merge_partition(
    options: &Options,
    plan: &crate::partition::Plan,
    part: usize,
    budget: &Arc<AtomicUsize>,
) -> Option<PartitionOutput> {
    let low = (part > 0).then(|| plan.splitters[part - 1].as_str());
    let high = plan.splitters.get(part).map(String::as_str);
    let options_arc = Arc::new(options.clone());
    let mut parsers = Vec::with_capacity(options.inputs.len());
    let mut heads = Vec::with_capacity(options.inputs.len());
    let mut previous: Vec<Option<Record>> = (0..options.inputs.len()).map(|_| None).collect();
    let mut local_used = 0usize;
    let mut accounted = BudgetAccount {
        shared: budget.clone(),
        used: 0,
    };
    let mut diagnosed = false;
    for (i, name) in options.inputs.iter().enumerate() {
        let range = &plan.ranges[part][i];
        let mut file = File::open(name).ok()?;
        file.seek(SeekFrom::Start(range.start)).ok()?;
        let limited = file.take(range.end.saturating_sub(range.start));
        let reader: Box<dyn Read + Send> = Box::new(limited);
        let capacity = read_capacity(options);
        let mut parser = Parser::new(
            BufReader::with_capacity(capacity, reader),
            name.to_string_lossy().into_owned(),
            i as u32,
            options_arc.clone(),
        );
        parser.account_reader_capacity(capacity);
        let mut row = partition_next(&mut parser, &mut diagnosed);
        if let Some(ref mut row) = row {
            share_head_path(row, &mut parser, &heads);
        }
        local_used = local_used
            .saturating_add(parser.buffer_capacity())
            .saturating_add(IO_BLOCK);
        if let Some(ref row) = row {
            local_used = local_used.saturating_add(record_memory_use(row));
        }
        if !accounted.update(local_used, (options.mem_limit / 2).min(16 << 20)) {
            return None;
        }
        parsers.push(parser);
        heads.push(row);
    }
    if diagnosed {
        return None;
    }
    let mut input_usage: Vec<usize> = (0..parsers.len())
        .map(|i| input_memory_use(&parsers[i], heads[i].as_ref(), previous[i].as_ref()))
        .collect();
    local_used = input_usage.iter().sum();
    let fixed_usage = 2 * IO_BLOCK
        + (heads.len() * 4 + heads.len().next_power_of_two() * 2) * std::mem::size_of::<usize>();
    let mut ranks = vec![0; heads.len()];
    let mut rank_order = Vec::with_capacity(heads.len());
    rank_heads(&heads, &mut rank_order, &mut ranks);
    let mut tree = HeadTree::new(&heads, &ranks);
    let (stage, file) = make_temp(output_parent(&options.output), "lcovmerge-part").ok()?;
    // Hold live stages for signal tests without requiring large, timing-sensitive inputs.
    // This hook is absent from production builds and remains cancellable and bounded.
    #[cfg(feature = "test-partition")]
    if std::env::var_os("LCOVMERGE_TEST_PARTITION_HOLD").is_some() {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(60);
        while std::time::Instant::now() < deadline {
            check_cancelled().ok()?;
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
    }
    let mut output = OutputState::new(BufWriter::with_capacity(IO_BLOCK, file));
    let mut aggregate: Option<Record> = None;
    let mut records = 0u64;
    while let Some(i) = tree.first() {
        check_cancelled().ok()?;
        let row = heads[i].take()?;
        if low.is_some_and(|l| row.path.as_ref() < l)
            || high.is_some_and(|h| row.path.as_ref() >= h)
        {
            return None;
        }
        if previous[i]
            .as_ref()
            .is_some_and(|p| total_cmp(p, &row) == Ordering::Greater)
        {
            return None;
        }
        records = records.saturating_add(1);
        if let Some(ref mut agg) = aggregate {
            if (row.kind == SECTION || (row.kind == crate::model::TN && row.text.is_empty()))
                && agg.path == row.path
                && agg.kind != SECTION
                && agg.kind != crate::model::TN
            {
                // Duplicate source marker; keep the current data key active.
            } else if same_key(agg, &row) {
                let mut diagnostics = Vec::new();
                if merge_aggregate(agg, &row, options, &mut diagnostics).is_err()
                    || !diagnostics.is_empty()
                {
                    return None;
                }
            } else {
                output.record(agg).ok()?;
                agg.clone_from(&row);
            }
        } else {
            aggregate = Some(row.clone());
        }
        if let Some(old) = previous[i].replace(row) {
            parsers[i].recycle_record(old);
        }
        let mut next = partition_next(&mut parsers[i], &mut diagnosed);
        if diagnosed {
            return None;
        }
        let changed_path = next.as_ref().is_some_and(|next| {
            previous[i]
                .as_ref()
                .is_none_or(|old| !Arc::ptr_eq(&old.path, &next.path))
        });
        if changed_path {
            share_head_path(next.as_mut().unwrap(), &mut parsers[i], &heads);
        }
        heads[i] = next;
        if changed_path {
            rank_heads(&heads, &mut rank_order, &mut ranks);
        }
        tree.update(i, &heads, &ranks);
        let current = input_memory_use(&parsers[i], heads[i].as_ref(), previous[i].as_ref());
        local_used = local_used
            .saturating_sub(input_usage[i])
            .saturating_add(current);
        input_usage[i] = current;
        let need = local_used
            .saturating_add(fixed_usage)
            .saturating_add(aggregate.as_ref().map_or(0, record_memory_use))
            .saturating_add(output.memory_size());
        if !accounted.update(need, (options.mem_limit / 2).min(16 << 20)) {
            return None;
        }
    }
    if diagnosed {
        return None;
    }
    if let Some(row) = aggregate {
        output.record(&row).ok()?;
    }
    output.finish_file().ok()?;
    let totals = output.totals.clone();
    output.writer.flush().ok()?;
    Some(PartitionOutput {
        stage,
        totals,
        records,
    })
}

fn output_parent(output: &Path) -> &Path {
    Path::new(output)
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
}

fn direct_partitioned(options: &Options) -> Result<PartitionResult, Error> {
    let mut want = partition_want(options);
    if want < 2
        || options.output == Path::new("-")
        || options.inputs.iter().any(|p| p.as_os_str() == "-")
    {
        return Ok(None);
    }
    let fd = descriptor_limit();
    if fd <= 8 || options.inputs.len() > fd.saturating_sub(8) {
        partition_trace("partition: not engaged");
        return Ok(None);
    }
    want = want.min(fd.saturating_sub(16) / options.inputs.len().max(1));
    if want < 2 {
        return Ok(None);
    }
    let unit = options.inputs.len().saturating_mul(IO_BLOCK);
    let mem_parts = ((options.mem_limit / 2).min(16 << 20)) / unit.max(1);
    want = want.min(mem_parts);
    if want < 2 {
        return Ok(None);
    }
    let mut total_size = 0u64;
    for name in &options.inputs {
        let Ok(metadata) = fs::metadata(name) else {
            return Ok(None);
        };
        total_size = total_size.saturating_add(metadata.len());
    }
    let forced =
        cfg!(feature = "test-partition") && std::env::var("LCOVMERGE_TEST_PARTITION").is_ok();
    if total_size < 64 * 1024 * 1024 && !forced {
        return Ok(None);
    }
    let plan = match crate::partition::plan(options, want) {
        Ok(Some(plan)) => plan,
        Ok(None) | Err(_) => {
            partition_trace("partition: not engaged");
            return Ok(None);
        }
    };
    let parts = plan.splitters.len() + 1;
    if parts < 2 {
        partition_trace("partition: not engaged");
        return Ok(None);
    }
    partition_trace(&format!("partition: engaged P={parts}"));
    let shared_options = Arc::new(options.clone());
    let shared_budget = Arc::new(AtomicUsize::new(0));
    let result = std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for part in 0..parts {
            let opts = shared_options.clone();
            let plan_ref = &plan;
            let budget = shared_budget.clone();
            handles.push(scope.spawn(move || merge_partition(&opts, plan_ref, part, &budget)));
        }
        handles
            .into_iter()
            .map(|h| h.join().unwrap_or(None))
            .collect::<Vec<_>>()
    });
    if result.iter().any(Option::is_none) || plan.needs_rerun {
        partition_trace("partition: anomaly in a worker");
        partition_trace("partition: rerun serial anomaly");
        return Ok(None);
    }
    let outputs: Vec<PartitionOutput> = result.into_iter().map(Option::unwrap).collect();
    let (stage, file) = match output_stage(&options.output) {
        Ok(v) => v,
        Err(_) => return Ok(None),
    };
    let mut w = BufWriter::with_capacity(IO_BLOCK, file);
    let mut buf = vec![0u8; IO_BLOCK];
    let mut records = 0u64;
    let mut totals = crate::emit::Totals::default();
    for output in &outputs {
        records = records.saturating_add(output.records);
        totals.fnf = totals.fnf.saturating_add(output.totals.fnf);
        totals.fnh = totals.fnh.saturating_add(output.totals.fnh);
        totals.lf = totals.lf.saturating_add(output.totals.lf);
        totals.lh = totals.lh.saturating_add(output.totals.lh);
        totals.brf = totals.brf.saturating_add(output.totals.brf);
        totals.brh = totals.brh.saturating_add(output.totals.brh);
        totals.mcf = totals.mcf.saturating_add(output.totals.mcf);
        totals.mch = totals.mch.saturating_add(output.totals.mch);
        let copied = (|| -> io::Result<()> {
            let mut r = BufReader::with_capacity(IO_BLOCK, File::open(output.stage.path())?);
            loop {
                let n = r.read(&mut buf)?;
                if n == 0 {
                    break;
                }
                w.write_all(&buf[..n])?;
            }
            Ok(())
        })();
        if copied.is_err() {
            return Ok(None);
        }
    }
    if w.flush().is_err() {
        return Ok(None);
    }
    drop(w);
    partition_trace("partition: complete");
    Ok(Some((stage, plan.bytes, records, parts, totals)))
}

pub fn run(options: Options) -> Result<(), Error> {
    if options.inputs.is_empty()
        || options.output.as_os_str().is_empty()
        || !(1..=32).contains(&options.jobs)
        || options.mem_limit < MIN_WORKER_BUDGET
        || options.jobs > options.mem_limit / MIN_WORKER_BUDGET
    {
        return Err(Error::new(1, "invalid merge options"));
    }
    let stdin_count = options
        .inputs
        .iter()
        .filter(|s| s.as_os_str() == "-")
        .count();
    if stdin_count > 1 {
        return Err(Error::new(1, "stdin may appear only once"));
    }
    for name in options.inputs.iter().filter(|s| s.as_os_str() != "-") {
        if !fs::metadata(name).is_ok_and(|m| m.is_file()) {
            return Err(Error::at(
                3,
                name,
                0,
                "input must be a regular file; use '-' for standard input",
            ));
        }
    }
    if stdin_count == 0 {
        if let Some((stage, bytes, records, parts, totals)) = direct_partitioned(&options)? {
            commit_stage(stage, &options.output)?;
            if options.verbose && !options.quiet {
                eprintln!("lcovmerge: parsed {bytes} input bytes into {records} records using {parts} jobs (sorted-input fast path)");
            }
            if options.stats || (options.verbose && !options.quiet) {
                eprintln!(
                    "lcovmerge: stats LF={} LH={} FNF={} FNH={} BRF={} BRH={}",
                    totals.lf, totals.lh, totals.fnf, totals.fnh, totals.brf, totals.brh
                );
            }
            return Ok(());
        }
    }
    // Sorted input direct path; an anomaly drops the stage and diagnostics before the external-sort restart.
    if stdin_count == 0 {
        if let Some((stage, bytes, records, diagnostics, totals)) = direct(&options)? {
            if let Ok(f) = File::open(diagnostics.path()) {
                let mut r = BufReader::with_capacity(IO_BLOCK, f);
                io::copy(&mut r, &mut io::stderr()).map_err(|e| error(e.to_string()))?;
            }
            commit_stage(stage, &options.output)?;
            if options.verbose && !options.quiet {
                eprintln!("lcovmerge: parsed {bytes} input bytes into {records} records using 1 job (sorted-input fast path)");
            }
            if options.stats || (options.verbose && !options.quiet) {
                eprintln!(
                    "lcovmerge: stats LF={} LH={} FNF={} FNH={} BRF={} BRH={}",
                    totals.lf, totals.lh, totals.fnf, totals.fnh, totals.brf, totals.brh
                );
            }
            return Ok(());
        }
    }
    external_sort(options)
}

fn external_sort(options: Options) -> Result<(), Error> {
    let jobs = options
        .jobs
        .max(1)
        .min(options.inputs.len().max(1))
        .min((options.mem_limit / MIN_WORKER_BUDGET).max(1));
    let floor = MIN_WORKER_BUDGET.max(1);
    let budget = (options.mem_limit.saturating_sub(MIN_WORKER_BUDGET) / jobs).max(floor);
    let shared = Arc::new(options.clone());
    let mut all_runs = Vec::new();
    let mut totals_bytes = 0u64;
    let mut totals_records = 0u64;
    // Input workers own disjoint inputs and bounded chunks. A single stdin occurrence is consumed once.
    let results = std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for worker in 0..jobs {
            let o = shared.clone();
            handles.push(scope.spawn(move || -> Result<(Vec<TempPath>, u64, u64), Error> {
                let mut runs = Vec::new();
                let mut chunk: Vec<Record> = Vec::new();
                let mut payload_bytes = 0usize;
                let mut last_path: Option<Arc<str>> = None;
                let mut bytes = 0u64;
                let mut records = 0u64;
                for i in (worker..o.inputs.len()).step_by(jobs) {
                    let name = &o.inputs[i];
                    let mut parser = open_parser(name, i as u32, o.clone())
                        .map_err(|e| Error::at(3, name, 0, format!("cannot open input: {e}")))?;
                    let mut diagnostics = |line: &str| {
                        let stderr = io::stderr();
                        writeln!(stderr.lock(), "{line}").map_err(|e| error(e.to_string()))
                    };
                    while let Some(row) = parser.next_record_with_diagnostics(&mut diagnostics)? {
                        let payload = row.text.capacity().saturating_add(row.extra.capacity());
                        let overhead = parser.buffer_capacity().saturating_add(IO_BLOCK);
                        let new_path = last_path.as_ref().is_none_or(|path| !Arc::ptr_eq(path, &row.path));
                        let path_bytes = if new_path { row.path.len() } else { 0 };
                        let slot_bytes = std::mem::size_of::<Record>();
                        let need = payload_bytes.saturating_add(payload).saturating_add(path_bytes);
                        let next_capacity = if chunk.len() == chunk.capacity() {
                            chunk.capacity().max(32).saturating_mul(2)
                        } else { chunk.capacity() };
                        if !chunk.is_empty() && need.saturating_add(overhead)
                            .saturating_add(next_capacity.saturating_mul(slot_bytes)) > budget {
                            sort_flush(&mut chunk, &o.tmpdir, &mut runs).map_err(|_| Error::at(
                                3, name, 0, format!("cannot write temporary run in --tmpdir '{}'; it must exist and be writable", o.tmpdir.display())))?;
                            payload_bytes = 0;
                            last_path = None;
                        }
                        let path_bytes = if last_path.as_ref().is_none_or(|p| !Arc::ptr_eq(p, &row.path)) {
                            row.path.len()
                        } else { 0 };
                        let need = payload_bytes.saturating_add(payload).saturating_add(path_bytes);
                        let available_slots = budget.saturating_sub(need).saturating_sub(overhead) / slot_bytes;
                        if chunk.capacity() > available_slots && chunk.is_empty() {
                            chunk.shrink_to(available_slots);
                        }
                        if chunk.len() >= available_slots {
                            return Err(Error::at(2, name, row.origin_line, "record exceeds the configured memory limit"));
                        }
                        if chunk.len() == chunk.capacity() {
                            let capacity = chunk.capacity().max(32).saturating_mul(2).min(available_slots);
                            chunk.try_reserve_exact(capacity - chunk.len())
                                .map_err(|_| error("cannot allocate record storage"))?;
                        }
                        payload_bytes = need;
                        if path_bytes != 0 { last_path = Some(row.path.clone()); }
                        chunk.push(row);
                        records = records.saturating_add(1);
                    }
                    bytes = bytes.saturating_add(parser.bytes);
                }
                sort_flush(&mut chunk, &o.tmpdir, &mut runs).map_err(|_| Error::at(
                    3, o.inputs.get(worker).map_or(Path::new("<input>"), |p| p.as_path()), 0,
                    format!("cannot write temporary run in --tmpdir '{}'; it must exist and be writable", o.tmpdir.display())))?;
                Ok((runs, bytes, records))
            }));
        }
        handles
            .into_iter()
            .map(|h| {
                h.join()
                    .unwrap_or_else(|_| Err(error("worker thread failed")))
            })
            .collect::<Vec<_>>()
    });
    for result in results {
        let (r, b, n) = result?;
        all_runs.extend(r);
        totals_bytes = totals_bytes.saturating_add(b);
        totals_records = totals_records.saturating_add(n);
    }
    reduce_runs(&mut all_runs, &options)
        .map_err(|e| error(format!("cannot merge temporary runs: {e}")))?;
    let (stage, file): (Option<TempPath>, Box<dyn Write>) = if options.output == Path::new("-") {
        (
            None,
            crate::common::stdout_writer().map_err(|e| error(e.to_string()))?,
        )
    } else {
        let (s, f) =
            output_stage(&options.output).map_err(|e| output_creation_error(&options.output, e))?;
        (Some(s), Box::new(f))
    };
    let diag_dir = if options.output == Path::new("-") {
        std::env::temp_dir()
    } else {
        output_parent(&options.output).to_path_buf()
    };
    let (mut diag_path, diag_file) = make_temp(&diag_dir, "lcovmerge-diagnostics")
        .map_err(|e| error(format!("cannot create diagnostic spool: {e}")))?;
    let mut diag = BufWriter::with_capacity(IO_BLOCK, diag_file);
    let mut writer = BufWriter::with_capacity(IO_BLOCK, file);
    let totals = if !all_runs.is_empty() {
        merge_final(&all_runs, &mut writer, &options, &mut diag).map_err(|e| {
            if options.strict_checksum && e.kind() == io::ErrorKind::InvalidData {
                let detail = e.to_string();
                Error::new(2, detail.strip_prefix("lcovmerge: ").unwrap_or(&detail))
            } else {
                error(format!("cannot merge temporary runs: {e}"))
            }
        })?
    } else {
        crate::emit::Totals::default()
    };
    writer
        .flush()
        .map_err(|e| error(format!("cannot write output: {e}")))?;
    diag.flush().map_err(|e| error(e.to_string()))?;
    drop(writer);
    drop(diag);
    if let Some(s) = stage {
        commit_stage(s, &options.output)?;
    }
    if let Ok(f) = File::open(diag_path.path()) {
        io::copy(
            &mut BufReader::with_capacity(IO_BLOCK, f),
            &mut io::stderr(),
        )
        .map_err(|e| error(e.to_string()))?;
    }
    if options.verbose && !options.quiet {
        eprintln!("lcovmerge: parsed {totals_bytes} input bytes into {totals_records} records using {jobs} job(s)");
    }
    if options.stats || (options.verbose && !options.quiet) {
        eprintln!(
            "lcovmerge: stats LF={} LH={} FNF={} FNH={} BRF={} BRH={}",
            totals.lf, totals.lh, totals.fnf, totals.fnh, totals.brf, totals.brh
        );
    }
    let _ = &mut diag_path;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn encoded_record() -> Vec<u8> {
        let mut bytes = Vec::new();
        let record = Record {
            path: Arc::from("/roundtrip.c"),
            text: "value".into(),
            kind: DA,
            a: 7,
            value: 3,
            ..Record::default()
        };
        write_record(&mut bytes, &record, &mut String::new()).unwrap();
        bytes
    }

    #[test]
    fn run_record_roundtrip_and_temp_cleanup() {
        let dir = tempfile::tempdir().unwrap();
        let bytes = encoded_record();
        let (temp, mut file) = make_temp(dir.path(), "lcovmerge-test").unwrap();
        file.write_all(&bytes).unwrap();
        file.flush().unwrap();
        let path = temp.path().to_path_buf();
        drop(file);
        let reader = RunReader::new(&path).unwrap();
        let row = reader.row.unwrap();
        assert_eq!(
            (&*row.path, row.text.as_str(), row.a, row.value),
            ("/roundtrip.c", "value", 7, 3)
        );
        drop(temp);
        assert!(!path.exists());
    }

    #[test]
    fn run_reader_rejects_truncated_header_and_invalid_kind() {
        let dir = tempfile::tempdir().unwrap();
        let short = dir.path().join("short");
        File::create(&short).unwrap().write_all(&[1, 2, 3]).unwrap();
        assert_eq!(
            RunReader::new(&short).unwrap_err().kind(),
            io::ErrorKind::UnexpectedEof
        );
        let bad = dir.path().join("bad-kind");
        let mut bytes = encoded_record();
        bytes[12] = 255;
        File::create(&bad).unwrap().write_all(&bytes).unwrap();
        assert_eq!(
            RunReader::new(&bad).unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
    }

    #[test]
    fn run_reader_rejects_embedded_string_nul() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("embedded-nul.run");
        let mut bytes = encoded_record();
        let path_len = u32::from_le_bytes(bytes[..4].try_into().unwrap()) as usize;
        bytes[58 + path_len] = 0;
        File::create(&path).unwrap().write_all(&bytes).unwrap();
        assert_eq!(
            RunReader::new(&path).unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
    }

    #[test]
    fn head_path_ranks_group_equal_paths_and_update_on_transition() {
        let record = |path: &str| Record {
            path: Arc::from(path),
            ..Record::default()
        };
        let mut heads = vec![Some(record("b")), Some(record("a")), Some(record("a"))];
        let mut ranks = vec![0; heads.len()];
        let mut order = Vec::with_capacity(heads.len());
        rank_heads(&heads, &mut order, &mut ranks);
        assert_eq!(ranks[1], ranks[2]);
        assert!(ranks[1] < ranks[0]);

        heads[0] = Some(record("a"));
        rank_heads(&heads, &mut order, &mut ranks);
        assert_eq!(ranks, vec![0, 0, 0]);
    }
}
