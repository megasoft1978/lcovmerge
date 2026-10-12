#include "lm_internal.h"

static void print_usage(FILE *stream) {
    fputs("usage: lcovmerge [options] a.info b.info ... -o out.info\n"
          "  -o, --output FILE           output file, or - for stdout\n"
          "  --mem-limit SIZE            arena cap; bytes or binary K/M/G, case-insensitive\n"
          "                              default 64M; minimum 8M per external-sort job\n"
          "  --tmpdir DIR                temporary run directory\n"
          "  -j, --jobs N                1-32 external-sort jobs (default min(4, cores), memory-limited)\n"
          "  --prefix-strip PREFIX       strip an SF path prefix at a path boundary\n"
          "  --rebase OLD=NEW            rewrite SF path prefixes at path boundaries\n"
          "  --include GLOB              include matching SF paths (repeatable)\n"
          "  --exclude GLOB              exclude matching SF paths; exclusions win\n"
          "  --branch-coverage on|off    retain or drop BRDA rows (default on)\n"
          "  --no-function-data          drop FN/FNDA/FNL/FNA data\n"
          "  --strict-checksum           exit 2 on conflict; otherwise warn/use lexical min\n"
          "  --warn-unknown              report preserved unknown records\n"
          "  -q                          suppress progress\n"
          "  -v                          show progress and merged statistics\n"
          "  --stats                     print merged LF/LH/FNF/FNH/BRF/BRH to stderr\n"
          "  --version                   print version and git commit\n"
          "  -h, --help                  show this text\n"
          "  inputs: shell expands globs; @file reads paths (8 levels), @@ escapes @\n"
          "  filters: rebase, prefix-strip, includes, then exclusions\n"
          "  sorted regular files use one stream job; fallback uses --jobs\n"
          "  exits: 0 success, 1 usage, 2 input/format, 3 I/O/allocation/interruption\n", stream);
}

typedef struct {
    uint32_t path_length, text_length, extra_length;
    uint8_t type, flags;
    uint16_t reserved;
    uint64_t a, b, c, value, origin_line;
    uint32_t input_id;
} DiskRecord;

static int write_record(Writer *writer, const Record *record) {
    size_t path_length = record->path_length;
    size_t text_length = record->text_length;
    size_t extra_length = record->extra_length;
    if (path_length == SIZE_MAX) return -1;
    DiskRecord disk;
    memset(&disk, 0, sizeof(disk));
    int new_path = !writer->run_path;
    if (!new_path) {
        if (record->path_id != 0 && writer->run_path_id != 0)
            new_path = record->path_id != writer->run_path_id;
        else
            new_path = path_length != writer->run_path_length ||
                       memcmp(writer->run_path, record->path, path_length) != 0;
    }
    if (new_path) {
        if (path_length + 1 > writer->run_path_capacity) {
            char *grown = realloc(writer->run_path, path_length + 1);
            if (!grown) return -1;
            writer->run_path = grown;
            writer->run_path_capacity = path_length + 1;
        }
        memcpy(writer->run_path, record->path, path_length + 1);
        writer->run_path_length = path_length;
        writer->run_path_id = record->path_id;
    }
    disk.path_length = new_path ? (uint32_t)path_length : 0;
    disk.text_length = (uint32_t)text_length;
    disk.extra_length = (uint32_t)extra_length;
    disk.type = record->type;
    disk.flags = record->flags;
    disk.a = record->a;
    disk.b = record->b;
    disk.c = record->c;
    disk.value = record->value;
    disk.origin_line = record->origin_line;
    disk.input_id = record->input_id;
    if (writer_bytes(writer, &disk, sizeof(disk)) != 0 ||
        (new_path && writer_bytes(writer, record->path, path_length) != 0) ||
        writer_bytes(writer, record->text, text_length) != 0 || writer_char(writer, '\0') != 0 ||
        writer_bytes(writer, record->extra, extra_length) != 0 || writer_char(writer, '\0') != 0) return -1;
    return 0;
}

static char *arena_copy(Chunk *chunk, const char *value, size_t length) {
    if (length > chunk->arena_size - chunk->arena_used - 1) return NULL;
    char *copy = chunk->arena + chunk->arena_used;
    if (length) memcpy(copy, value, length);
    copy[length] = '\0';
    chunk->arena_used += length + 1;
    return copy;
}

static int flush_chunk(Chunk *chunk, RunList *runs, const char *tmpdir) {
    if (interrupted) return -1;
    if (chunk->count == 0) return 0;
    if (!chunk->sorted) qsort(chunk->rows, chunk->count, sizeof(*chunk->rows), record_qsort_compare);
    uint64_t path_id = 0;
    for (size_t i = 0; i < chunk->count; ++i) {
        if (i == 0 || strcmp(chunk->rows[i - 1].path, chunk->rows[i].path) != 0) {
            if (path_id == UINT64_MAX) return -1;
            ++path_id;
        }
        chunk->rows[i].path_id = path_id;
    }
    if (interrupted) return -1;
    char *path = NULL;
    lm_handle handle = LM_INVALID_HANDLE;
    if (lm_create_temp(tmpdir, &path, &handle, NULL) != 0) return -1;
    if (interrupted) {
        (void)lm_close(handle);
        (void)lm_remove(path);
        free(path);
        return -1;
    }
    Writer writer;
    if (writer_init(&writer, handle) != 0) {
        (void)lm_close(handle);
        (void)lm_remove(path);
        free(path);
        return -1;
    }
    int result = 0;
    for (size_t i = 0; i < chunk->count; ++i) {
        if (write_record(&writer, &chunk->rows[i]) != 0) { result = -1; break; }
    }
    if (result == 0 && writer_flush(&writer) != 0) result = -1;
    writer_destroy(&writer);
    if (lm_close(handle) != 0) result = -1;
    if (interrupted) result = -1;
    if (result != 0) {
        (void)lm_remove(path);
        free(path);
        return -1;
    }
    if (run_list_add(runs, path) != 0) {
        (void)lm_remove(path);
        free(path);
        return -1;
    }
    if (interrupted) return -1;
    chunk->count = 0;
    chunk->arena_used = 0;
    chunk->have_last_row = 0;
    chunk->last_path = NULL;
    chunk->last_source_path = NULL;
    chunk->last_path_length = 0;
    chunk->sorted = 1;
    return 0;
}

static int chunk_add(Chunk *chunk, RunList *runs, const char *tmpdir, const Record *record) {
    size_t path_length = record->path_length;
    size_t text_length = record->text_length;
    size_t extra_length = record->extra_length;
    if (path_length == SIZE_MAX) return -1;
    int reuse_path = chunk->last_source_path == record->path ||
                     (chunk->last_path && path_length == chunk->last_path_length &&
                      memcmp(chunk->last_path, record->path, path_length) == 0);
    size_t need = 0;
    if (!reuse_path) need = path_length + 1;
    if (text_length) {
        if (text_length >= SIZE_MAX - need) return -1;
        need += text_length + 1;
    }
    if (extra_length) {
        if (extra_length >= SIZE_MAX - need) return -1;
        need += extra_length + 1;
    }
    if (chunk->count == chunk->capacity || need > chunk->arena_size - chunk->arena_used) {
        if (flush_chunk(chunk, runs, tmpdir) != 0) return -1;
        reuse_path = 0;
        need = path_length + 1;
        if (text_length) {
            if (text_length >= SIZE_MAX - need) return -1;
            need += text_length + 1;
        }
        if (extra_length) {
            if (extra_length >= SIZE_MAX - need) return -1;
            need += extra_length + 1;
        }
    }
    if (chunk->count == chunk->capacity || need > chunk->arena_size) return -2;
    Record *stored = &chunk->rows[chunk->count];
    *stored = *record;
    stored->path = reuse_path ? chunk->last_path : arena_copy(chunk, record->path, path_length);
    if (!reuse_path) chunk->last_path = stored->path;
    chunk->last_path_length = (uint32_t)path_length;
    stored->text = text_length ? arena_copy(chunk, record->text, text_length) : "";
    stored->extra = extra_length ? arena_copy(chunk, record->extra, extra_length) : "";
    if (!stored->path || !stored->text || !stored->extra) return -2;
    chunk->last_source_path = record->path;
    if (chunk->have_last_row && record_total_compare(&chunk->last_row, stored) > 0)
        chunk->sorted = 0;
    chunk->last_row = *stored;
    chunk->have_last_row = 1;
    ++chunk->count;
    return 0;
}

static int chunk_init(Chunk *chunk, size_t budget) {
    memset(chunk, 0, sizeof(*chunk));
    size_t row_budget = budget / 3;
    chunk->capacity = row_budget / sizeof(Record);
    if (chunk->capacity < 128) chunk->capacity = 128;
    if (chunk->capacity > SIZE_MAX / sizeof(Record)) return -1;
    chunk->arena_size = budget - chunk->capacity * sizeof(Record);
    size_t arena_cap = budget - budget / 3;
    if (chunk->arena_size > arena_cap) chunk->arena_size = arena_cap;
    chunk->rows = malloc(chunk->capacity * sizeof(*chunk->rows));
    chunk->arena = malloc(chunk->arena_size);
    if (!chunk->rows || !chunk->arena) {
        free(chunk->rows);
        free(chunk->arena);
        memset(chunk, 0, sizeof(*chunk));
        return -1;
    }
    chunk->budget = budget;
    chunk->sorted = 1;
    return 0;
}

static void chunk_destroy(Chunk *chunk) {
    free(chunk->rows);
    free(chunk->arena);
    memset(chunk, 0, sizeof(*chunk));
}

int wildcard_match(const char *pattern, const char *text) {
    const char *star = NULL;
    const char *retry = NULL;
    while (*text) {
        if (*pattern == '?' || *pattern == *text) { ++pattern; ++text; continue; }
        if (*pattern == '[') {
            const char *end = strchr(pattern + 1, ']');
            if (end) {
                const char *item = pattern + 1;
                int invert = 0, matched = 0;
                if (item < end && (*item == '!' || *item == '^')) { invert = 1; ++item; }
                while (item < end) {
                    if (item + 2 < end && item[1] == '-') {
                        if (*text >= item[0] && *text <= item[2]) matched = 1;
                        item += 3;
                    } else {
                        if (*text == *item) matched = 1;
                        ++item;
                    }
                }
                if ((matched != 0) != (invert != 0)) { pattern = end + 1; ++text; continue; }
            }
        }
        if (*pattern == '*') { star = pattern++; retry = text; continue; }
        if (star) { pattern = star + 1; text = ++retry; continue; }
        return 0;
    }
    while (*pattern == '*') ++pattern;
    return *pattern == '\0';
}

static int prefix_match(const char *path, const char *prefix, size_t *matched) {
    size_t length = strlen(prefix);
    if (length == 0) { *matched = 0; return 1; }
    if (strncmp(path, prefix, length) != 0) return 0;
    if (path[length] && path[length] != '/' && path[length] != '\\' &&
        prefix[length - 1] != '/' && prefix[length - 1] != '\\') return 0;
    *matched = length;
    while (path[length] == '/' || path[length] == '\\') ++length;
    *matched = length;
    return 1;
}

char *rewrite_path(const Options *options, const char *path) {
    char *current = duplicate_string(path);
    if (!current) return NULL;
    for (size_t i = 0; i < options->rebase_count; ++i) {
        size_t matched = 0;
        if (prefix_match(current, options->rebases[i].old_prefix, &matched)) {
            size_t new_length = strlen(options->rebases[i].new_prefix);
            size_t rest = strlen(current + matched);
            size_t old_length = strlen(options->rebases[i].old_prefix);
            char separator = current[old_length] == '\\' ? '\\' : '/';
            const char *new_prefix = options->rebases[i].new_prefix;
            if (strchr(new_prefix, '\\') && !strchr(new_prefix, '/')) separator = '\\';
            else if (strchr(new_prefix, '/') && !strchr(new_prefix, '\\')) separator = '/';
            int add_separator = rest != 0 && new_length != 0 &&
                options->rebases[i].new_prefix[new_length - 1] != '/' &&
                options->rebases[i].new_prefix[new_length - 1] != '\\';
            if (new_length > SIZE_MAX - rest - (size_t)add_separator - 1) { free(current); return NULL; }
            char *next = malloc(new_length + (size_t)add_separator + rest + 1);
            if (!next) { free(current); return NULL; }
            memcpy(next, options->rebases[i].new_prefix, new_length);
            size_t cursor = new_length;
            if (add_separator) next[cursor++] = separator;
            for (size_t character = 0; character < rest; ++character) {
                char value = current[matched + character];
                next[cursor + character] = value == '/' || value == '\\' ? separator : value;
            }
            next[cursor + rest] = '\0';
            free(current);
            current = next;
        }
    }
    if (options->prefix_strip) {
        size_t matched = 0;
        if (prefix_match(current, options->prefix_strip, &matched)) {
            char *next = duplicate_string(current + matched);
            if (!next) { free(current); return NULL; }
            free(current);
            current = next;
        }
    }
    if (strlen(current) > MAX_LINE) { free(current); return NULL; }
    return current;
}

int path_selected(const Options *options, const char *path) {
    int included = options->includes.count == 0;
    for (size_t i = 0; i < options->includes.count; ++i)
        if (wildcard_match(options->includes.items[i], path)) included = 1;
    for (size_t i = 0; i < options->excludes.count; ++i)
        if (wildcard_match(options->excludes.items[i], path)) return 0;
    return included;
}

static int worker_error(Worker *worker, int code, const char *file, uint64_t line, const char *format, ...) {
    if (worker->error_code != 0) return -1;
    worker->error_code = code;
    if (file) {
        size_t length = strlen(file);
        if (length > MAX_LINE) length = MAX_LINE;
        memcpy(worker->error_file, file, length);
        worker->error_file[length] = '\0';
    }
    worker->error_line = line;
    va_list arguments;
    va_start(arguments, format);
    (void)vsnprintf(worker->error_message, sizeof(worker->error_message), format, arguments);
    va_end(arguments);
    return -1;
}

static int worker_temp_run_error(Worker *worker, const char *file, uint64_t line) {
    return worker_error(worker, 3, file, line,
                        "cannot write temporary run in --tmpdir '%s'; it must exist and be writable",
                        worker->options->tmpdir);
}

static int direct_store_last(Worker *worker, const Record *source) {
    size_t needed = (size_t)source->text_length;
    if ((size_t)source->extra_length > SIZE_MAX - needed - 2u) return -1;
    needed += (size_t)source->extra_length + 2u;
    size_t previous_capacity = worker->direct_last_capacity;
    if (worker->direct_last_capacity < needed) {
        char *grown = realloc(worker->direct_last_storage, needed);
        if (!grown) return -1;
        worker->direct_last_storage = grown;
        worker->direct_last_capacity = needed;
    }
    char *cursor = worker->direct_last_storage;
    memcpy(cursor, source->text, source->text_length + 1u);
    worker->direct_last_row = *source;
    worker->direct_last_row.text = cursor;
    cursor += source->text_length + 1u;
    memcpy(cursor, source->extra, source->extra_length + 1u);
    worker->direct_last_row.extra = cursor;
    worker->have_direct_last_row = 1;
    if (worker->direct_memory_used && worker->direct_last_capacity > previous_capacity) {
        *worker->direct_memory_used = saturating_add((uint64_t)*worker->direct_memory_used,
            (uint64_t)(worker->direct_last_capacity - previous_capacity));
        if (*worker->direct_memory_used > worker->direct_memory_limit) {
            worker->direct_unsorted = 1;
            return 1;
        }
    }
    return 0;
}

static int direct_preserve_last_path(Worker *worker, const char *path) {
    if (!worker->direct_mode || !worker->have_direct_last_row ||
        worker->direct_last_row.path_id != worker->direct_path_generation) return 0;
    size_t needed = strlen(path) + 1u;
    size_t previous_capacity = worker->direct_path_capacity;
    if (worker->direct_path_capacity < needed) {
        char *grown = realloc(worker->direct_path_storage, needed);
        if (!grown) return worker_error(worker, 3, worker->current_file, 0, "out of memory");
        worker->direct_path_storage = grown;
        worker->direct_path_capacity = needed;
    }
    memcpy(worker->direct_path_storage, path, needed);
    worker->direct_last_row.path = worker->direct_path_storage;
    worker->direct_last_row.path_length = (uint32_t)(needed - 1u);
    if (worker->direct_memory_used && worker->direct_path_capacity > previous_capacity) {
        *worker->direct_memory_used = saturating_add((uint64_t)*worker->direct_memory_used,
            (uint64_t)(worker->direct_path_capacity - previous_capacity));
        if (*worker->direct_memory_used > worker->direct_memory_limit) {
            worker->direct_unsorted = 1;
            return 1;
        }
    }
    return 0;
}

static int direct_record_out_of_order(const Record *previous, const Record *current) {
    if (previous->path_id != 0 && previous->path_id == current->path_id &&
        previous->type == REC_DA && current->type == REC_DA && previous->a != current->a)
        return previous->a > current->a;
    if (previous->path_id != 0 && previous->path_id == current->path_id &&
        previous->type == REC_BRDA && current->type == REC_BRDA) {
        if (previous->a != current->a) return previous->a > current->a;
        if (previous->b != current->b) return previous->b > current->b;
        if (previous->c != current->c) return previous->c > current->c;
        return record_total_compare_same_path(previous, current) > 0;
    }
    return record_total_compare(previous, current) > 0;
}

static int add_row(Worker *worker, Chunk *chunk, const char *source_path, uint8_t type,
                   const char *text, const char *extra, uint64_t a, uint64_t b,
                   uint64_t c, uint64_t value, uint8_t flags,
                   uint32_t input_id, uint64_t input_line) {
    const char *row_text = text ? text : "";
    const char *row_extra = extra ? extra : "";
    size_t path_length = worker->direct_mode && source_path == worker->direct_current_path ?
                         worker->direct_current_path_length : strlen(source_path);
    size_t text_length = strlen(row_text);
    size_t extra_length = strlen(row_extra);
    if (path_length > UINT32_MAX || text_length > UINT32_MAX || extra_length > UINT32_MAX)
        return worker_error(worker, 2, worker->current_file, input_line, "record string exceeds supported size");
    Record record;
    record.path = (char *)source_path;
    record.text = (char *)row_text;
    record.extra = (char *)row_extra;
    record.path_length = (uint32_t)path_length;
    record.text_length = (uint32_t)text_length;
    record.extra_length = (uint32_t)extra_length;
    record.a = a;
    record.b = b;
    record.c = c;
    record.value = value;
    record.flags = flags;
    record.type = type;
    record.path_id = worker->direct_mode ? worker->direct_path_generation : 0;
    record.input_id = input_id;
    record.origin_line = input_line;
    if (worker->direct_mode) {
        if (worker->direct_count >= 2u) return worker_error(worker, 3, worker->current_file, input_line, "direct input row buffer overflow");
        if (worker->have_direct_last_row) {
            if (direct_record_out_of_order(&worker->direct_last_row, &record)) {
                worker->direct_unsorted = 1;
                return -2;
            }
        }
        size_t slot = (worker->direct_head + worker->direct_count) % 2u;
        worker->direct_rows[slot] = record;
        int stored = direct_store_last(worker, &record);
        if (stored < 0) return worker_error(worker, 3, worker->current_file, input_line, "out of memory");
        if (stored > 0) return -2;
        ++worker->direct_count;
        ++worker->records;
        return 0;
    }
    int result = chunk_add(chunk, &worker->runs, worker->options->tmpdir, &record);
    if (result == -2) return worker_error(worker, 2, worker->current_file, input_line, "record exceeds the configured memory limit");
    if (result != 0) {
        if (interrupted)
            return worker_error(worker, 3, worker->current_file, input_line, "interrupted");
        return worker_temp_run_error(worker, worker->current_file, input_line);
    }
    ++worker->records;
    return 0;
}

static int add_group_and_function(Worker *worker, Chunk *chunk, const char *path,
                                  uint64_t line, uint64_t end_line, uint64_t index,
                                  const char *group_name, const char *function_name,
                                  uint32_t input_id, uint64_t input_line) {
    if (add_row(worker, chunk, path, REC_GROUP, group_name, "", line, end_line, index,
                0, FLAG_FN_NEW, input_id, input_line) != 0) return -1;
    if (function_name && *function_name) {
        if (add_row(worker, chunk, path, REC_FN, function_name, group_name,
                    line, end_line, index, 0, FLAG_FN_NEW, input_id, input_line) != 0) return -1;
    }
    return 0;
}

static char *field_next(char **cursor, char delimiter) {
    char *start = *cursor;
    char *end = strchr(start, delimiter);
    if (end) { *end = '\0'; *cursor = end + 1; }
    else *cursor = NULL;
    return start;
}

static int parse_decimal_field(char *field, uint64_t *value) {
    if (!*field) return 0;
    uint64_t result = 0;
    const uint64_t max_div_10 = UINT64_MAX / UINT64_C(10);
    const uint64_t max_mod_10 = UINT64_MAX % UINT64_C(10);
    for (const unsigned char *cursor = (const unsigned char *)field; *cursor; ++cursor) {
        if (*cursor < (unsigned char)'0' || *cursor > (unsigned char)'9') return 0;
        uint64_t digit = (uint64_t)(*cursor - (unsigned char)'0');
        if (result > max_div_10 || (result == max_div_10 && digit > max_mod_10)) return 0;
        result = result * UINT64_C(10) + digit;
    }
    *value = result;
    return 1;
}

static int parse_block(char *field, uint64_t *block, uint8_t *flags) {
    char *cursor = field;
    while (*cursor == 'e' || *cursor == 'f' || *cursor == 'U') {
        if (*cursor == 'e') *flags |= FLAG_BR_EXCEPTION;
        if (*cursor == 'f') *flags |= FLAG_BR_FALLTHROUGH;
        if (*cursor == 'U') *flags |= FLAG_BR_UNREACHABLE;
        ++cursor;
    }
    return parse_decimal_field(cursor, block);
}

static int append_pending_row(Worker *worker, Chunk *chunk, const char *path,
                              const char *test_name, uint32_t input_id, uint64_t input_line) {
    return add_row(worker, chunk, path, REC_TN, test_name, "", 0, 0, 0, 0, 0,
                   input_id, input_line);
}

static int parse_record_line(Worker *worker, Chunk *chunk, char *line,
                             const char *filename, uint32_t input_id,
                             uint64_t line_no, size_t length, char **path, int *keep_path,
                             int *section_open, char **pending_tn,
                             uint64_t *fnl_index, uint64_t *fnl_line,
                             uint64_t *fnl_end, int *fnl_valid) {
    if (length && line[length - 1] == '\r') line[--length] = '\0';
    if (length == 0 || line[0] == '#') return 0;
    const size_t end_record_length = sizeof("end_of_record") - 1u;
    if (length >= end_record_length + 3u &&
        memcmp(line, "end_of_record", end_record_length) == 0 &&
        (memcmp(line + end_record_length, "SF:", 3u) == 0 ||
         memcmp(line + end_record_length, "KF:", 3u) == 0)) {
        if (!*section_open)
            return worker_error(worker, 2, filename, line_no,
                                "joined end_of_record/source record without SF record");
        if (*path && direct_preserve_last_path(worker, *path) != 0) return -1;
        *section_open = 0;
        *fnl_valid = 0;
        chunk->last_source_path = NULL;
        if (worker->direct_mode) worker->direct_current_path = NULL;
        free(*path);
        *path = NULL;
        *keep_path = 0;

        FILE *diagnostics = worker->diagnostic_file ? worker->diagnostic_file : stderr;
        fprintf(diagnostics,
                "lcovmerge: %s:%" PRIu64 ": warning: recovered joined end_of_record/source boundary after missing newline\n",
                filename, line_no);
        ++worker->warnings;

        memmove(line, line + end_record_length, length - end_record_length + 1u);
        length -= end_record_length;
    }
    if (strcmp(line, "end_of_record") == 0) {
        if (!*section_open) return worker_error(worker, 2, filename, line_no, "end_of_record without SF record");
        if (*path && direct_preserve_last_path(worker, *path) != 0) return -1;
        *section_open = 0;
        *fnl_valid = 0;
        chunk->last_source_path = NULL;
        if (worker->direct_mode) worker->direct_current_path = NULL;
        free(*path);
        *path = NULL;
        *keep_path = 0;
        return 0;
    }
    if (strncmp(line, "SF:", 3) == 0 || strncmp(line, "KF:", 3) == 0) {
        if (*section_open) return worker_error(worker, 2, filename, line_no, "SF record before end_of_record");
        if (line[3] == '\0') return worker_error(worker, 2, filename, line_no, "empty SF path");
        char *rewritten = rewrite_path(worker->options, line + 3);
        if (!rewritten) return worker_error(worker, 2, filename, line_no, "source path is too long or cannot be rewritten");
        if (!*rewritten) {
            free(rewritten);
            return worker_error(worker, 2, filename, line_no, "source path becomes empty after rewriting");
        }
        if (*path && direct_preserve_last_path(worker, *path) != 0) {
            free(rewritten);
            return -1;
        }
        if (worker->direct_mode) {
            if (worker->direct_path_generation == UINT64_MAX) {
                worker->direct_unsorted = 1;
                free(rewritten);
                return -1;
            }
            ++worker->direct_path_generation;
        }
        free(*path);
        *path = rewritten;
        *keep_path = path_selected(worker->options, *path);
        *section_open = 1;
        if (worker->direct_mode) {
            worker->direct_current_path = *path;
            worker->direct_current_path_length = strlen(*path);
        }
        *fnl_valid = 0;
        if (*keep_path) {
            if (append_pending_row(worker, chunk, *path, *pending_tn ? *pending_tn : "",
                                   input_id, line_no) != 0) return -1;
            if (add_row(worker, chunk, *path, REC_SECTION, "", "", 0, 0, 0, 0, 0,
                        input_id, line_no) != 0) return -1;
        }
        if (worker->direct_mode && *keep_path && *pending_tn) {
            worker->direct_deferred_text = *pending_tn;
            *pending_tn = NULL;
        } else free(*pending_tn);
        *pending_tn = NULL;
        return 0;
    }
    if (strncmp(line, "TN:", 3) == 0) {
        if (*section_open) return worker_error(worker, 2, filename, line_no, "TN record must precede SF");
        if (*pending_tn) return worker_error(worker, 2, filename, line_no, "multiple TN records before SF");
        *pending_tn = duplicate_string(line + 3);
        if (!*pending_tn) return worker_error(worker, 3, filename, line_no, "out of memory");
        return 0;
    }
    if (!*section_open) return worker_error(worker, 2, filename, line_no, "coverpoint outside an SF section");
    if (line[0] == 'D' && line[1] == 'A' && line[2] == ':') {
        char *cursor = line + 3;
        char *line_field = field_next(&cursor, ',');
        char *count_field = cursor ? field_next(&cursor, ',') : NULL;
        uint64_t line_number, count;
        if (!line_field || !count_field || !parse_decimal_field(line_field, &line_number) ||
            !parse_decimal_field(count_field, &count))
            return worker_error(worker, 2, filename, line_no, "invalid DA record");
        const char *checksum = cursor ? cursor : "";
        if (*keep_path && add_row(worker, chunk, *path, REC_DA, "", checksum, line_number, 0, 0,
                                  count, 0, input_id, line_no) != 0) return -1;
        return 0;
    }
    if (strncmp(line, "FNF:", 4) == 0 || strncmp(line, "FNH:", 4) == 0 ||
        strncmp(line, "LF:", 3) == 0 || strncmp(line, "LH:", 3) == 0 ||
        strncmp(line, "BRF:", 4) == 0 || strncmp(line, "BRH:", 4) == 0 ||
        strncmp(line, "MCF:", 4) == 0 || strncmp(line, "MCH:", 4) == 0) {
        char *colon = strchr(line, ':');
        uint64_t ignored;
        if (!colon || !parse_u64(colon + 1, strlen(colon + 1), &ignored))
            return worker_error(worker, 2, filename, line_no, "invalid summary count");
        return 0;
    }
    if (strncmp(line, "FNL:", 4) == 0) {
        if (!worker->options->function_data) { *fnl_valid = 0; return 0; }
        char *cursor = line + 4;
        char *index_field = field_next(&cursor, ',');
        char *line_field = cursor ? field_next(&cursor, ',') : NULL;
        char *end_field = cursor ? field_next(&cursor, ',') : NULL;
        uint64_t index, start, end = 0;
        if (!index_field || !line_field || !parse_decimal_field(index_field, &index) ||
            !parse_decimal_field(line_field, &start) || (end_field && !parse_decimal_field(end_field, &end)))
            return worker_error(worker, 2, filename, line_no, "invalid FNL record");
        *fnl_index = index;
        *fnl_line = start;
        *fnl_end = end;
        *fnl_valid = 1;
        if (*keep_path && add_group_and_function(worker, chunk, *path, start, end, index, "", NULL,
                                                 input_id, line_no) != 0) return -1;
        return 0;
    }
    if (strncmp(line, "FNA:", 4) == 0) {
        if (!worker->options->function_data) return 0;
        char *cursor = line + 4;
        char *index_field = field_next(&cursor, ',');
        char *count_field = cursor ? field_next(&cursor, ',') : NULL;
        uint64_t index, count;
        if (!index_field || !count_field || !cursor || !parse_decimal_field(index_field, &index) ||
            !parse_decimal_field(count_field, &count) || !*fnl_valid || index != *fnl_index)
            return worker_error(worker, 2, filename, line_no, "invalid FNA record or missing matching FNL");
        const char *name = cursor;
        if (*keep_path) {
            if (!*name) return worker_error(worker, 2, filename, line_no, "empty FNA function name");
            if (add_row(worker, chunk, *path, REC_GROUP, "", "", *fnl_line, *fnl_end, index,
                        0, FLAG_FN_NEW, input_id, line_no) != 0 ||
                add_row(worker, chunk, *path, REC_FN, name, "", *fnl_line, *fnl_end, index,
                        count, FLAG_FN_NEW, input_id, line_no) != 0) return -1;
        }
        return 0;
    }
    if (strncmp(line, "FN:", 3) == 0) {
        if (!worker->options->function_data) return 0;
        char *cursor = line + 3;
        char *start_field = field_next(&cursor, ',');
        char *next_field = cursor ? field_next(&cursor, ',') : NULL;
        uint64_t start, end = 0;
        const char *name;
        if (!start_field || !next_field || !parse_decimal_field(start_field, &start))
            return worker_error(worker, 2, filename, line_no, "invalid FN record");
        if (cursor) {
            if (!parse_decimal_field(next_field, &end)) return worker_error(worker, 2, filename, line_no, "invalid FN end line");
            name = cursor;
        } else name = next_field;
        if (*keep_path) {
            if (add_row(worker, chunk, *path, REC_GROUP, name, name, start, end, 0,
                        0, 0, input_id, line_no) != 0 ||
                add_row(worker, chunk, *path, REC_FN, name, name, start, end, 0,
                        0, 0, input_id, line_no) != 0) return -1;
        }
        return 0;
    }
    if (strncmp(line, "FNDA:", 5) == 0) {
        if (!worker->options->function_data) return 0;
        char *cursor = line + 5;
        char *count_field = field_next(&cursor, ',');
        uint64_t count;
        if (!count_field || !cursor || !parse_decimal_field(count_field, &count))
            return worker_error(worker, 2, filename, line_no, "invalid FNDA record");
        if (*keep_path) {
            if (add_row(worker, chunk, *path, REC_FNDA, cursor, cursor, 0, 0, 0,
                        count, 0, input_id, line_no) != 0) return -1;
        }
        return 0;
    }
    if (strncmp(line, "BRDA:", 5) == 0) {
        if (!worker->options->branch_coverage) return 0;
        char *cursor = line + 5;
        char *line_field = field_next(&cursor, ',');
        char *block_field = cursor ? field_next(&cursor, ',') : NULL;
        if (!line_field || !block_field || !cursor) return worker_error(worker, 2, filename, line_no, "invalid BRDA record");
        char *last_comma = strrchr(cursor, ',');
        if (!last_comma) return worker_error(worker, 2, filename, line_no, "invalid BRDA record");
        *last_comma++ = '\0';
        uint64_t line_number, block, branch = 0, count = 0;
        uint8_t flags = 0;
        if (!parse_decimal_field(line_field, &line_number) || !parse_block(block_field, &block, &flags))
            return worker_error(worker, 2, filename, line_no, "invalid BRDA line or block");
        char *branch_field = cursor;
        if (parse_decimal_field(branch_field, &branch)) flags |= FLAG_NUMERIC_BRANCH;
        else flags |= FLAG_BRANCH_TEXT;
        if (strcmp(last_comma, "-") == 0) flags |= FLAG_DASH;
        else if (!parse_decimal_field(last_comma, &count)) return worker_error(worker, 2, filename, line_no, "invalid BRDA count");
        if (*keep_path && add_row(worker, chunk, *path, REC_BRDA, branch_field, "", line_number,
                                  block, branch, count, flags, input_id, line_no) != 0) return -1;
        return 0;
    }
    if (strncmp(line, "MCDC:", 5) == 0) {
        char *cursor = line + 5;
        char *line_field = field_next(&cursor, ',');
        char *group_field = cursor ? field_next(&cursor, ',') : NULL;
        char *sense_field = cursor ? field_next(&cursor, ',') : NULL;
        char *count_field = cursor ? field_next(&cursor, ',') : NULL;
        char *index_field = cursor ? field_next(&cursor, ',') : NULL;
        uint64_t line_number, group_size, count, index;
        uint8_t flags = 0;
        int unreachable = group_field && group_field[0] == 'U';
        char *group_number = unreachable ? group_field + 1 : group_field;
        if (!line_field || !group_field || !sense_field || !count_field || !index_field || !cursor ||
            !parse_decimal_field(line_field, &line_number) || !parse_decimal_field(group_number, &group_size) ||
            !parse_decimal_field(count_field, &count) || !parse_decimal_field(index_field, &index) ||
            (strcmp(sense_field, "t") != 0 && strcmp(sense_field, "f") != 0))
            return worker_error(worker, 2, filename, line_no, "invalid MCDC record");
        if (sense_field[0] == 't') flags |= FLAG_MCDC_TRUE;
        if (unreachable) flags |= FLAG_MCDC_UNREACHABLE;
        if (*keep_path && add_row(worker, chunk, *path, REC_MCDC, cursor, "", line_number,
                                  group_size, index, count, flags, input_id, line_no) != 0) return -1;
        return 0;
    }
    if (strncmp(line, "VER:", 4) == 0) {
        if (*keep_path && add_row(worker, chunk, *path, REC_EXT, line, "", 0, 0, 0, 0, 0,
                                  input_id, line_no) != 0) return -1;
        return 0;
    }
    char *colon = strchr(line, ':');
    if (!colon || colon == line) return worker_error(worker, 2, filename, line_no, "unrecognized tracefile row");
    if (worker->options->warn_unknown) {
        fprintf(worker->diagnostic_file ? worker->diagnostic_file : stderr,
                "lcovmerge: %s:%" PRIu64 ": preserved unknown record %.*s\n",
                filename, line_no, (int)(colon - line), line);
        ++worker->warnings;
    }
    if (*keep_path && add_row(worker, chunk, *path, REC_EXT, line, "", 0, 0, 0, 0, 0,
                              input_id, line_no) != 0) return -1;
    return 0;
}

/* Returns 1 when a valid DA row was handled, 0 for general-parser fallback, -1 on error. */
static int parse_direct_da(Worker *worker, char *line, size_t length,
                           uint32_t input_id, uint64_t line_no, char *path, int keep_path) {
    if (length < 3u || line[0] != 'D' || line[1] != 'A' || line[2] != ':') return 0;
    const char *first_comma = strchr(line + 3, ',');
    if (!first_comma) return 0;
    const char *second_comma = strchr(first_comma + 1, ',');
    size_t line_length = (size_t)(first_comma - (line + 3));
    size_t count_length = second_comma ? (size_t)(second_comma - (first_comma + 1)) :
                                         strlen(first_comma + 1);
    uint64_t line_number, count;
    if (!parse_u64(line + 3, line_length, &line_number) ||
        !parse_u64(first_comma + 1, count_length, &count)) return 0;
    const char *checksum = second_comma ? second_comma + 1 : "";
    if (keep_path && add_row(worker, NULL, path, REC_DA, "", checksum, line_number, 0, 0,
                             count, 0, input_id, line_no) != 0) return -1;
    return 1;
}

static int parse_direct_block(const char *text, size_t length, uint64_t *block, uint8_t *flags) {
    size_t prefix = 0;
    while (prefix < length && (text[prefix] == 'e' || text[prefix] == 'f' || text[prefix] == 'U')) {
        if (text[prefix] == 'e') *flags |= FLAG_BR_EXCEPTION;
        if (text[prefix] == 'f') *flags |= FLAG_BR_FALLTHROUGH;
        if (text[prefix] == 'U') *flags |= FLAG_BR_UNREACHABLE;
        ++prefix;
    }
    return parse_u64(text + prefix, length - prefix, block);
}

/* As with DA, malformed rows fall back to the canonical parser for its exact diagnostics. */
static int parse_direct_brda(Worker *worker, char *line, size_t length,
                             uint32_t input_id, uint64_t line_no, char *path,
                             int keep_path) {
    if (length < 5u || memcmp(line, "BRDA:", 5u) != 0) return 0;
    char *line_end = line + length;
    char *first_comma = memchr(line + 5, ',', (size_t)(line_end - (line + 5)));
    if (!first_comma) return 0;
    char *second_comma = memchr(first_comma + 1, ',', (size_t)(line_end - (first_comma + 1)));
    if (!second_comma) return 0;
    char *last_comma = NULL;
    for (char *cursor = second_comma + 1; cursor < line_end; ++cursor)
        if (*cursor == ',') last_comma = cursor;
    if (!last_comma) return 0;
    uint64_t line_number, block, branch = 0, count = 0;
    size_t line_length = (size_t)(first_comma - (line + 5));
    size_t block_length = (size_t)(second_comma - (first_comma + 1));
    size_t branch_length = (size_t)(last_comma - (second_comma + 1));
    size_t count_length = (size_t)(line_end - (last_comma + 1));
    uint8_t flags = 0;
    if (!parse_u64(line + 5, line_length, &line_number) ||
        !parse_direct_block(first_comma + 1, block_length, &block, &flags)) return 0;
    if (parse_u64(second_comma + 1, branch_length, &branch)) flags |= FLAG_NUMERIC_BRANCH;
    else flags |= FLAG_BRANCH_TEXT;
    if (count_length == 1u && last_comma[1] == '-') flags |= FLAG_DASH;
    else if (!parse_u64(last_comma + 1, count_length, &count)) return 0;
    *last_comma = '\0';
    if (keep_path && add_row(worker, NULL, path, REC_BRDA, second_comma + 1, "", line_number,
                             block, branch, count, flags, input_id, line_no) != 0) return -1;
    return 1;
}

static int parse_input_file(Worker *worker, Chunk *chunk, const char *filename, uint32_t input_id) {
    worker->current_file = filename;
    lm_handle handle = LM_INVALID_HANDLE;
    int owns_handle = strcmp(filename, "-") != 0;
    if (owns_handle) {
        if (lm_open_read(filename, &handle) != 0)
            return worker_error(worker, 3, filename, 0, "cannot open input");
    } else handle = lm_stdin_handle();
    LineReader reader;
    if (line_reader_init(&reader, handle) != 0) {
        if (owns_handle) (void)lm_close(handle);
        return worker_error(worker, 3, filename, 0, "out of memory");
    }
    char *path = NULL;
    char *pending_tn = NULL;
    int keep_path = 0, section_open = 0, status = 0;
    uint64_t fnl_index = 0, fnl_line = 0, fnl_end = 0;
    int fnl_valid = 0;
    for (;;) {
        if (atomic_load_explicit(&interrupted, memory_order_relaxed)) {
            status = worker_error(worker, 3, filename, reader.line_no, "interrupted");
            break;
        }
        int got = line_reader_next(&reader);
        if (got == 0) break;
        if (got == -1) { status = worker_error(worker, 3, filename, reader.line_no + 1, "input read failed"); break; }
        if (got == -2 || got == -3) {
            const char *message = got == -3 ? "line exceeds 1 MiB" : "binary or invalid UTF-8 input";
            uint64_t error_line = reader.line_no + (got == -3 ? UINT64_C(1) : UINT64_C(0));
            status = worker_error(worker, 2, filename, error_line, "%s", message);
            break;
        }
        reader.line[reader.line_len] = '\0';
        if (parse_record_line(worker, chunk, reader.line, filename, input_id, reader.line_no,
                              reader.line_len, &path, &keep_path, &section_open, &pending_tn,
                              &fnl_index, &fnl_line, &fnl_end, &fnl_valid) != 0) {
            status = -1;
            break;
        }
    }
    if (status == 0 && section_open) {
        free(path);
        path = NULL;
    }
    worker->input_bytes = saturating_add(worker->input_bytes, reader.bytes_read);
    uint64_t final_line_no = reader.line_no;
    free(path);
    chunk->last_source_path = NULL;
    free(pending_tn);
    line_reader_destroy(&reader);
    if (owns_handle && lm_close(handle) != 0 && status == 0)
        status = worker_error(worker, 3, filename, final_line_no, "input close failed");
    worker->current_file = NULL;
    return status;
}

static int expand_input_argument(Options *options, const char *argument, unsigned depth) {
    if (depth > 8) {
        fprintf(stderr, "lcovmerge: @listfile nesting exceeds 8 levels\n");
        return 1;
    }
    if (argument[0] == '@' && argument[1] == '@')
        return list_add_string(&options->inputs, argument + 1) == 0 ? 0 : 3;
    if (argument[0] != '@')
        return list_add_string(&options->inputs, argument) == 0 ? 0 : 3;
    const char *list_path = argument + 1;
    if (!lm_is_regular_file(list_path)) {
        fprintf(stderr, "lcovmerge: %s:0: list file must be a regular file\n", list_path);
        return 3;
    }
    lm_handle handle = LM_INVALID_HANDLE;
    if (lm_open_read(list_path, &handle) != 0) {
        fprintf(stderr, "lcovmerge: %s:0: cannot open listfile\n", list_path);
        return 3;
    }
    LineReader reader;
    if (line_reader_init(&reader, handle) != 0) {
        (void)lm_close(handle);
        fprintf(stderr, "lcovmerge: %s:0: out of memory\n", list_path);
        return 3;
    }
    int result = 0;
    for (;;) {
        int got = line_reader_next(&reader);
        if (got == 0) break;
        if (got < 0) {
            fprintf(stderr, "lcovmerge: %s:%" PRIu64 ": invalid listfile line\n", list_path,
                    reader.line_no + (got == -3 ? UINT64_C(1) : UINT64_C(0)));
            result = got == -1 ? 3 : 2;
            break;
        }
        if (reader.line_len && reader.line[reader.line_len - 1] == '\r')
            reader.line[--reader.line_len] = '\0';
        if (reader.line_len == 0 || reader.line[0] == '#') continue;
        reader.line[reader.line_len] = '\0';
        result = expand_input_argument(options, reader.line, depth + 1);
        if (result != 0) break;
    }
    line_reader_destroy(&reader);
    if (lm_close(handle) != 0 && result == 0) {
        fprintf(stderr, "lcovmerge: %s:%" PRIu64 ": listfile close failed\n", list_path, reader.line_no);
        result = 3;
    }
    return result;
}

int add_rebase(Options *options, const char *mapping) {
    const char *equal = strchr(mapping, '=');
    if (!equal) return -1;
    if (options->rebase_count == options->rebase_capacity) {
        size_t next = options->rebase_capacity ? options->rebase_capacity * 2 : 4;
        if (next < options->rebase_capacity || next > SIZE_MAX / sizeof(*options->rebases)) return -1;
        Rebase *grown = realloc(options->rebases, next * sizeof(*options->rebases));
        if (!grown) return -1;
        options->rebases = grown;
        options->rebase_capacity = next;
    }
    Rebase *rebase = &options->rebases[options->rebase_count];
    rebase->old_prefix = duplicate_range(mapping, (size_t)(equal - mapping));
    rebase->new_prefix = duplicate_string(equal + 1);
    if (!rebase->old_prefix || !rebase->new_prefix) {
        free(rebase->old_prefix);
        free(rebase->new_prefix);
        return -1;
    }
    ++options->rebase_count;
    return 0;
}

void options_destroy(Options *options) {
    list_free(&options->inputs);
    list_free(&options->includes);
    list_free(&options->excludes);
    for (size_t i = 0; i < options->rebase_count; ++i) {
        free(options->rebases[i].old_prefix);
        free(options->rebases[i].new_prefix);
    }
    free(options->rebases);
    free(options->prefix_strip);
    free(options->output);
    free(options->tmpdir);
    memset(options, 0, sizeof(*options));
}

static int option_value(int argc, char **argv, int *index, const char **value) {
    if (*index + 1 >= argc) return -1;
    ++*index;
    *value = argv[*index];
    return 0;
}

static int parse_options(int argc, char **argv, Options *options) {
    memset(options, 0, sizeof(*options));
    options->mem_limit = DEFAULT_MEM_LIMIT;
    options->branch_coverage = 1;
    options->function_data = 1;
    unsigned cores = lm_cpu_count();
    options->jobs = cores < 4u ? cores : 4u;
    if (options->jobs == 0) options->jobs = 1;
    options->tmpdir = lm_default_tempdir();
    if (!options->tmpdir) return 3;
    int end_options = 0;
    for (int i = 1; i < argc; ++i) {
        const char *value = NULL;
        if (!end_options && strcmp(argv[i], "--") == 0) { end_options = 1; continue; }
        if (!end_options && (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0)) {
            print_usage(stdout);
            return 0;
        }
        if (!end_options && strcmp(argv[i], "--version") == 0) {
#ifndef LCOVMERGE_GIT_COMMIT
#define LCOVMERGE_GIT_COMMIT "unknown"
#endif
            printf("lcovmerge %s (git %s)\n", LCOVMERGE_VERSION, LCOVMERGE_GIT_COMMIT);
            return 0;
        }
        if (!end_options && (strcmp(argv[i], "-o") == 0 || strcmp(argv[i], "--output") == 0)) {
            if (option_value(argc, argv, &i, &value) != 0) goto usage_error;
            free(options->output);
            options->output = duplicate_string(value);
            if (!options->output) return 3;
        } else if (!end_options && strcmp(argv[i], "--mem-limit") == 0) {
            if (option_value(argc, argv, &i, &value) != 0 || !parse_size(value, &options->mem_limit)) goto usage_error;
        } else if (!end_options && strcmp(argv[i], "--tmpdir") == 0) {
            if (option_value(argc, argv, &i, &value) != 0) goto usage_error;
            char *copy = duplicate_string(value);
            if (!copy) return 3;
            free(options->tmpdir);
            options->tmpdir = copy;
        } else if (!end_options && (strcmp(argv[i], "-j") == 0 || strcmp(argv[i], "--jobs") == 0)) {
            uint64_t parsed;
            if (option_value(argc, argv, &i, &value) != 0 || !parse_u64(value, strlen(value), &parsed) || parsed == 0 || parsed > MAX_JOBS) goto usage_error;
            options->jobs = (unsigned)parsed;
            options->jobs_explicit = 1;
        } else if (!end_options && strcmp(argv[i], "--prefix-strip") == 0) {
            if (option_value(argc, argv, &i, &value) != 0) goto usage_error;
            char *copy = duplicate_string(value);
            if (!copy) return 3;
            free(options->prefix_strip);
            options->prefix_strip = copy;
        } else if (!end_options && strcmp(argv[i], "--rebase") == 0) {
            if (option_value(argc, argv, &i, &value) != 0 || add_rebase(options, value) != 0) goto usage_error;
        } else if (!end_options && (strcmp(argv[i], "--include") == 0 || strcmp(argv[i], "--exclude") == 0)) {
            int is_include = strcmp(argv[i], "--include") == 0;
            if (option_value(argc, argv, &i, &value) != 0) goto usage_error;
            StringList *list = is_include ? &options->includes : &options->excludes;
            if (list_add_string(list, value) != 0) return 3;
        } else if (!end_options && strcmp(argv[i], "--branch-coverage") == 0) {
            if (option_value(argc, argv, &i, &value) != 0) goto usage_error;
            if (strcmp(value, "on") == 0) options->branch_coverage = 1;
            else if (strcmp(value, "off") == 0) options->branch_coverage = 0;
            else goto usage_error;
        } else if (!end_options && strcmp(argv[i], "--no-function-data") == 0) {
            options->function_data = 0;
        } else if (!end_options && strcmp(argv[i], "--strict-checksum") == 0) {
            options->strict_checksum = 1;
        } else if (!end_options && strcmp(argv[i], "--warn-unknown") == 0) {
            options->warn_unknown = 1;
        } else if (!end_options && strcmp(argv[i], "-q") == 0) {
            options->quiet = 1;
        } else if (!end_options && strcmp(argv[i], "-v") == 0) {
            options->verbose = 1;
        } else if (!end_options && strcmp(argv[i], "--stats") == 0) {
            options->stats = 1;
        } else if (!end_options && argv[i][0] == '-' && strcmp(argv[i], "-") != 0) {
            goto usage_error;
        } else {
            if (list_add_string(&options->inputs, argv[i]) != 0) return 3;
        }
    }
    if (!options->jobs_explicit && options->jobs > options->mem_limit / MIN_WORKER_BUDGET)
        options->jobs = (unsigned)(options->mem_limit / MIN_WORKER_BUDGET);
    if (options->jobs == 0) options->jobs = 1;
    if (!options->output || options->inputs.count == 0 || options->mem_limit < MIN_WORKER_BUDGET ||
        options->jobs > options->mem_limit / MIN_WORKER_BUDGET) goto usage_error;
    return 99;

usage_error:
    fprintf(stderr, "lcovmerge: invalid or incomplete option near '%s'\n", argc > 1 ? argv[1] : "");
    print_usage(stderr);
    return 1;
}

static void *worker_main(void *raw) {
    Worker *worker = raw;
    Chunk chunk;
    if (chunk_init(&chunk, worker->budget) != 0) {
        (void)worker_error(worker, 3, NULL, 0, "cannot allocate working memory");
        return NULL;
    }
    for (size_t i = worker->worker_id; i < worker->options->inputs.count; i += worker->worker_count) {
        if (interrupted) {
            (void)worker_error(worker, 3, worker->options->inputs.items[i], 0, "interrupted");
            break;
        }
        const char *filename = worker->options->inputs.items[i];
        if (parse_input_file(worker, &chunk, filename, (uint32_t)i) != 0) break;
        if (flush_chunk(&chunk, &worker->runs, worker->options->tmpdir) != 0) {
            if (interrupted) (void)worker_error(worker, 3, filename, 0, "interrupted");
            else (void)worker_temp_run_error(worker, filename, 0);
            break;
        }
    }
    if (worker->error_code == 0) {
        if (flush_chunk(&chunk, &worker->runs, worker->options->tmpdir) != 0) {
            if (interrupted) (void)worker_error(worker, 3, worker->current_file, 0, "interrupted");
            else (void)worker_temp_run_error(worker, worker->current_file, 0);
        } else if (interrupted) {
            (void)worker_error(worker, 3, worker->current_file, 0, "interrupted");
        }
    }
    chunk_destroy(&chunk);
    return NULL;
}

static void print_worker_error(const Worker *worker) {
    if (!worker->error_code) return;
    if (worker->error_file[0])
        fprintf(stderr, "lcovmerge: %s:%" PRIu64 ": %s\n", worker->error_file,
                worker->error_line, worker->error_message);
    else fprintf(stderr, "lcovmerge: %s\n", worker->error_message);
}

static int merge_group(const char *const *paths, size_t count, const char *tmpdir,
                       char **out_path, int final_output, OutputState *output,
                       const Options *options);

static int reduce_runs(RunList *runs, const Options *options) {
    unsigned fanin = (unsigned)(options->mem_limit / (((size_t)2u << 20)));
    if (fanin < 2u) fanin = 2u;
    if (fanin > RUN_FANIN_MAX) fanin = RUN_FANIN_MAX;
    unsigned descriptor_limit = lm_max_open_files();
    unsigned descriptor_fanin = descriptor_limit > 16u ? descriptor_limit - 16u : 2u;
    if (fanin > descriptor_fanin) fanin = descriptor_fanin;
    if (fanin < 2u) fanin = 2u;
    while (runs->count > fanin) {
        RunList next = {0};
        int pass_failed = 0;
        for (size_t begin = 0; begin < runs->count; begin += fanin) {
            size_t count = runs->count - begin;
            if (count > fanin) count = fanin;
            if (count == 1) {
                char *keep = runs->paths[begin];
                runs->paths[begin] = NULL;
                if (run_list_add(&next, keep) != 0) { (void)lm_remove(keep); free(keep); pass_failed = 1; break; }
                continue;
            }
            char *merged = NULL;
            if (merge_group((const char *const *)(runs->paths + begin), count, options->tmpdir,
                            &merged, 0, NULL, options) != 0 || run_list_add(&next, merged) != 0) {
                if (merged) { (void)lm_remove(merged); free(merged); }
                pass_failed = 1;
                break;
            }
        }
        if (pass_failed) { run_list_cleanup(&next); return -1; }
        for (size_t i = 0; i < runs->count; ++i) {
            if (runs->paths[i]) { (void)lm_remove(runs->paths[i]); free(runs->paths[i]); }
        }
        free(runs->paths);
        *runs = next;
    }
    return 0;
}

static int run_reader_fill(RunReader *reader, void *destination, size_t length) {
    unsigned char *output = destination;
    size_t copied = 0;
    while (copied < length) {
        if (reader->pos == reader->len) {
            size_t amount = 0;
            if (lm_read(reader->handle, reader->block, sizeof(reader->block), &amount,
                        &interrupted) != 0) return -1;
            reader->pos = 0;
            reader->len = amount;
            if (amount == 0) return copied == 0 ? 0 : -1;
        }
        size_t available = reader->len - reader->pos;
        size_t amount = length - copied < available ? length - copied : available;
        memcpy(output + copied, reader->block + reader->pos, amount);
        reader->pos += amount;
        copied += amount;
    }
    return 1;
}

static int run_reader_set_path_id(RunReader *reader, size_t reader_index,
                                  size_t reader_count, uint64_t *next_path_id,
                                  RunReader *readers) {
    for (size_t i = 0; i < reader_count; ++i) {
        if (i != reader_index && readers[i].has_row &&
            strcmp(reader->path_storage, readers[i].row.path) == 0) {
            reader->path_id = readers[i].row.path_id;
            return 0;
        }
    }
    if (*next_path_id == UINT64_MAX) reader->path_id = 0;
    else reader->path_id = ++*next_path_id;
    return 0;
}

static int run_reader_next(RunReader *reader, size_t reader_index,
                           size_t reader_count, uint64_t *next_path_id,
                           RunReader *readers) {
    reader->has_row = 0;
    DiskRecord disk;
    int got = run_reader_fill(reader, &disk, sizeof(disk));
    if (got <= 0) { reader->has_row = 0; return got; }
    int path_changed = disk.path_length != 0;
    size_t path_length = disk.path_length;
    size_t text_length = disk.text_length;
    size_t extra_length = disk.extra_length;
    if (path_length > MAX_LINE || text_length > MAX_LINE || extra_length > MAX_LINE ||
        text_length > SIZE_MAX - extra_length - 2) return -1;
    if (path_length != 0) {
        if (path_length + 1 > reader->path_capacity) {
            char *grown = realloc(reader->path_storage, path_length + 1);
            if (!grown) return -1;
            reader->path_storage = grown;
            reader->path_capacity = path_length + 1;
        }
        if (run_reader_fill(reader, reader->path_storage, path_length) != 1) return -1;
        reader->path_storage[path_length] = '\0';
        reader->have_path = 1;
        reader->path_length = (uint32_t)path_length;
        if (reader->path_generation != UINT64_MAX) ++reader->path_generation;
        if (run_reader_set_path_id(reader, reader_index, reader_count,
                                   next_path_id, readers) != 0) return -1;
    } else if (!reader->have_path) return -1;
    size_t required = text_length + extra_length + 2;
    if (required > reader->storage_capacity) {
        unsigned char *grown = realloc(reader->storage, required);
        if (!grown) return -1;
        reader->storage = grown;
        reader->storage_capacity = required;
    }
    if (run_reader_fill(reader, reader->storage, required) != 1 ||
        reader->storage[text_length] != '\0' ||
        reader->storage[text_length + extra_length + 1] != '\0') return -1;
    reader->row.path = reader->path_storage;
    reader->row.text = (char *)reader->storage;
    reader->row.extra = (char *)reader->storage + text_length + 1;
    reader->row.path_length = reader->path_length;
    reader->row.text_length = (uint32_t)text_length;
    reader->row.extra_length = (uint32_t)extra_length;
    reader->row.type = disk.type;
    reader->row.flags = disk.flags;
    reader->row.a = disk.a;
    reader->row.b = disk.b;
    reader->row.c = disk.c;
    reader->row.value = disk.value;
    reader->row.origin_line = disk.origin_line;
    reader->row.input_id = disk.input_id;
    reader->row.path_id = reader->path_id;
    reader->has_row = 1;
    return path_changed ? 2 : 1;
}

static int heap_less(size_t left, size_t right, RunReader *readers,
                     PathCompareCache *path_cache, size_t reader_count) {
    RunReader *left_reader = &readers[left];
    RunReader *right_reader = &readers[right];
    PathCompareCache *entry = &path_cache[left * reader_count + right];
    int path_comparison;
    if (entry->valid && entry->generation_left == left_reader->path_generation &&
        entry->generation_right == right_reader->path_generation) {
        path_comparison = entry->comparison;
    } else {
        int raw_comparison = strcmp(left_reader->row.path, right_reader->row.path);
        path_comparison = (raw_comparison > 0) - (raw_comparison < 0);
        entry->generation_left = left_reader->path_generation;
        entry->generation_right = right_reader->path_generation;
        entry->comparison = path_comparison;
        entry->valid = 1;
        PathCompareCache *reverse = &path_cache[right * reader_count + left];
        reverse->generation_left = right_reader->path_generation;
        reverse->generation_right = left_reader->path_generation;
        reverse->comparison = -path_comparison;
        reverse->valid = 1;
    }
    if (path_comparison != 0) return path_comparison < 0;
    return record_total_compare_same_path(&left_reader->row, &right_reader->row) < 0;
}

static void heap_push(size_t *heap, size_t *count, size_t value, RunReader *readers,
                      PathCompareCache *path_cache, size_t reader_count) {
    size_t index = (*count)++;
    while (index != 0) {
        size_t parent = (index - 1) / 2;
        if (!heap_less(value, heap[parent], readers, path_cache, reader_count)) break;
        heap[index] = heap[parent];
        index = parent;
    }
    heap[index] = value;
}

static void heap_sift_down(size_t *heap, size_t count, size_t index, RunReader *readers,
                           PathCompareCache *path_cache, size_t reader_count) {
    size_t value = heap[index];
    while (index < count / 2) {
        size_t child = index * 2 + 1;
        if (child + 1 < count && heap_less(heap[child + 1], heap[child], readers,
                                            path_cache, reader_count)) ++child;
        if (!heap_less(heap[child], value, readers, path_cache, reader_count)) break;
        heap[index] = heap[child];
        index = child;
    }
    heap[index] = value;
}

static void heap_sift_down_root(size_t *heap, size_t count, RunReader *readers,
                                PathCompareCache *path_cache, size_t reader_count) {
    if (count != 0) heap_sift_down(heap, count, 0, readers, path_cache, reader_count);
}

static int ensure_source_record(OutputState *output) {
    if (output->source_written) return 0;
    output->source_written = 1;
    return writer_text(&output->writer, "SF:") == 0 &&
           writer_text(&output->writer, output->path) == 0 &&
           writer_text(&output->writer, "\n") == 0 ? 0 : -1;
}

static int start_output_file(OutputState *output, const char *path) {
    if (output->active) return 0;
    output->path = duplicate_string(path);
    if (!output->path) return -1;
    output->active = 1;
    output->source_written = 0;
    output->fnf = output->fnh = output->lf = output->lh = 0;
    output->brf = output->brh = output->mcf = output->mch = 0;
    output->have_fn_group = 0;
    output->fn_group_hit = 0;
    free(output->fn_group_name);
    output->fn_group_name = NULL;
    return 0;
}

static int same_function_group(const OutputState *output, const Record *record) {
    return output->have_fn_group && output->fn_group_a == record->a &&
           output->fn_group_b == record->b && output->fn_group_c == record->c &&
           strcmp(output->fn_group_name ? output->fn_group_name : "", record->extra) == 0;
}

static void finish_function_group(OutputState *output) {
    if (output->have_fn_group && output->fn_group_hit)
        output->fnh = saturating_add(output->fnh, UINT64_C(1));
    output->have_fn_group = 0;
    output->fn_group_hit = 0;
}

static int output_finish_file(OutputState *output);

static int add_function_hit(OutputState *output, const Record *record) {
    if (!same_function_group(output, record)) {
        finish_function_group(output);
        free(output->fn_group_name);
        output->fn_group_name = NULL;
        output->fn_group_name = duplicate_string(record->extra);
        if (!output->fn_group_name) return -1;
        output->fn_group_a = record->a;
        output->fn_group_b = record->b;
        output->fn_group_c = record->c;
        output->have_fn_group = 1;
    }
    if (record->value != 0) output->fn_group_hit = 1;
    return 0;
}

static int output_record(OutputState *output, const Record *record) {
    /* TN/SF markers delimit paths, so coverpoint rows need no repeated path compare. */
    int begins_source_section = record->type == REC_TN || record->type == REC_SECTION;
    if (begins_source_section && output->active && strcmp(output->path, record->path) != 0 &&
        output_finish_file(output) != 0) return -1;
    if (start_output_file(output, record->path) != 0) return -1;
    if (record->type == REC_TN) {
        if (record->value != 0 || record->text[0] == '\0') return 0;
        return writer_text(&output->writer, "TN:") == 0 &&
               writer_text(&output->writer, record->text) == 0 &&
               writer_text(&output->writer, "\n") == 0 ? 0 : -1;
    }
    if (record->type == REC_SECTION) return ensure_source_record(output);
    if (ensure_source_record(output) != 0) return -1;
    switch (record->type) {
        case REC_GROUP:
            output->fnf = saturating_add(output->fnf, UINT64_C(1));
            if (record->flags & FLAG_FN_NEW) {
                if (writer_text(&output->writer, "FNL:") != 0 ||
                    writer_u64(&output->writer, record->c) != 0 ||
                    writer_char(&output->writer, ',') != 0 ||
                    writer_u64(&output->writer, record->a) != 0) return -1;
                if (record->b != 0 && (writer_char(&output->writer, ',') != 0 ||
                    writer_u64(&output->writer, record->b) != 0)) return -1;
                return writer_char(&output->writer, '\n');
            }
            return 0;
        case REC_FN:
            if (record->flags & FLAG_FN_NEW) {
                if (add_function_hit(output, record) != 0) return -1;
                if (writer_text(&output->writer, "FNA:") != 0 ||
                    writer_u64(&output->writer, record->c) != 0 ||
                    writer_char(&output->writer, ',') != 0 ||
                    writer_u64(&output->writer, record->value) != 0 ||
                    writer_char(&output->writer, ',') != 0) return -1;
                return writer_text(&output->writer, record->text) == 0 &&
                       writer_text(&output->writer, "\n") == 0 ? 0 : -1;
            }
            if (writer_text(&output->writer, "FN:") != 0 ||
                writer_u64(&output->writer, record->a) != 0 ||
                writer_char(&output->writer, ',') != 0) return -1;
            if (record->b != 0 && (writer_u64(&output->writer, record->b) != 0 ||
                writer_char(&output->writer, ',') != 0)) return -1;
            return writer_text(&output->writer, record->text) == 0 && writer_text(&output->writer, "\n") == 0 ? 0 : -1;
        case REC_FNDA:
            if (add_function_hit(output, record) != 0) return -1;
            if (writer_text(&output->writer, "FNDA:") != 0 ||
                writer_u64(&output->writer, record->value) != 0 ||
                writer_char(&output->writer, ',') != 0) return -1;
            return writer_text(&output->writer, record->text) == 0 && writer_text(&output->writer, "\n") == 0 ? 0 : -1;
        case REC_BRDA: {
            if (writer_text(&output->writer, "BRDA:") != 0 ||
                writer_u64(&output->writer, record->a) != 0 ||
                writer_char(&output->writer, ',') != 0) return -1;
            if ((record->flags & FLAG_BR_EXCEPTION) && writer_text(&output->writer, "e") != 0) return -1;
            if ((record->flags & FLAG_BR_FALLTHROUGH) && writer_text(&output->writer, "f") != 0) return -1;
            if ((record->flags & FLAG_BR_UNREACHABLE) && writer_text(&output->writer, "U") != 0) return -1;
            if (writer_u64(&output->writer, record->b) != 0 || writer_char(&output->writer, ',') != 0) return -1;
            if (record->flags & FLAG_BRANCH_TEXT) {
                if (writer_text(&output->writer, record->text) != 0) return -1;
            } else if (writer_u64(&output->writer, record->c) != 0) return -1;
            if (record->flags & FLAG_DASH) {
                if (writer_text(&output->writer, ",-\n") != 0) return -1;
            } else if (writer_char(&output->writer, ',') != 0 ||
                       writer_u64(&output->writer, record->value) != 0 ||
                       writer_char(&output->writer, '\n') != 0) return -1;
            if (!(record->flags & FLAG_BR_UNREACHABLE)) {
                output->brf = saturating_add(output->brf, UINT64_C(1));
                if (record->value != 0 && !(record->flags & FLAG_DASH))
                    output->brh = saturating_add(output->brh, UINT64_C(1));
            }
            return 0;
        }
        case REC_MCDC:
            if (writer_text(&output->writer, "MCDC:") != 0 ||
                writer_u64(&output->writer, record->a) != 0 ||
                writer_char(&output->writer, ',') != 0 ||
                ((record->flags & FLAG_MCDC_UNREACHABLE) && writer_char(&output->writer, 'U') != 0) ||
                writer_u64(&output->writer, record->b) != 0 ||
                writer_char(&output->writer, ',') != 0 ||
                writer_char(&output->writer, (record->flags & FLAG_MCDC_TRUE) ? 't' : 'f') != 0 ||
                writer_char(&output->writer, ',') != 0 ||
                writer_u64(&output->writer, record->value) != 0 ||
                writer_char(&output->writer, ',') != 0 ||
                writer_u64(&output->writer, record->c) != 0 ||
                writer_char(&output->writer, ',') != 0 ||
                writer_text(&output->writer, record->text) != 0 || writer_text(&output->writer, "\n") != 0) return -1;
            if (!(record->flags & FLAG_MCDC_UNREACHABLE)) {
                output->mcf = saturating_add(output->mcf, UINT64_C(1));
                if (record->value != 0) output->mch = saturating_add(output->mch, UINT64_C(1));
            }
            return 0;
        case REC_DA:
            if (writer_text(&output->writer, "DA:") != 0 ||
                writer_u64(&output->writer, record->a) != 0 ||
                writer_char(&output->writer, ',') != 0 ||
                writer_u64(&output->writer, record->value) != 0) return -1;
            if (*record->extra && (writer_text(&output->writer, ",") != 0 || writer_text(&output->writer, record->extra) != 0)) return -1;
            if (writer_text(&output->writer, "\n") != 0) return -1;
            output->lf = saturating_add(output->lf, UINT64_C(1));
            if (record->value != 0) output->lh = saturating_add(output->lh, UINT64_C(1));
            return 0;
        case REC_EXT:
            return writer_text(&output->writer, record->text) == 0 && writer_text(&output->writer, "\n") == 0 ? 0 : -1;
        default:
            return -1;
    }
}

static int output_finish_file(OutputState *output) {
    if (!output->active) return 0;
    if (ensure_source_record(output) != 0) return -1;
    finish_function_group(output);
    if (writer_format(&output->writer,
                      "FNF:%" PRIu64 "\nFNH:%" PRIu64 "\n"
                      "BRF:%" PRIu64 "\nBRH:%" PRIu64 "\n"
                      "MCF:%" PRIu64 "\nMCH:%" PRIu64 "\n"
                      "LF:%" PRIu64 "\nLH:%" PRIu64 "\nend_of_record\n",
                      output->fnf, output->fnh, output->brf, output->brh,
                      output->mcf, output->mch, output->lf, output->lh) != 0) return -1;
    output->total_fnf = saturating_add(output->total_fnf, output->fnf);
    output->total_fnh = saturating_add(output->total_fnh, output->fnh);
    output->total_brf = saturating_add(output->total_brf, output->brf);
    output->total_brh = saturating_add(output->total_brh, output->brh);
    output->total_lf = saturating_add(output->total_lf, output->lf);
    output->total_lh = saturating_add(output->total_lh, output->lh);
    output->active = 0;
    free(output->path);
    output->path = NULL;
    free(output->fn_group_name);
    output->fn_group_name = NULL;
    output->have_fn_group = 0;
    output->fn_group_hit = 0;
    return 0;
}

static void aggregate_copy(Record *destination, const Record *source, char *storage) {
    size_t path_length = source->path_length;
    size_t text_length = source->text_length;
    size_t extra_length = source->extra_length;
    destination->path = storage;
    memcpy(destination->path, source->path, path_length + 1);
    destination->text = destination->path + path_length + 1;
    memcpy(destination->text, source->text, text_length + 1);
    destination->extra = destination->text + text_length + 1;
    memcpy(destination->extra, source->extra, extra_length + 1);
    destination->path_length = source->path_length;
    destination->text_length = source->text_length;
    destination->extra_length = source->extra_length;
    destination->a = source->a;
    destination->b = source->b;
    destination->c = source->c;
    destination->value = source->value;
    destination->origin_line = source->origin_line;
    destination->input_id = source->input_id;
    destination->type = source->type;
    destination->flags = source->flags;
    destination->path_id = source->path_id;
}

static const char *input_name_for(const Options *options, uint32_t input_id) {
    return (size_t)input_id < options->inputs.count ? options->inputs.items[input_id] : "<input>";
}

static int merge_aggregate(Record *aggregate, const Record *next, const Options *options,
                           FILE *diagnostics) {
    if (next->type == REC_TN) {
        if (strcmp(aggregate->text, next->text) != 0) aggregate->value = 1;
    } else if (next->type == REC_FNDA || next->type == REC_MCDC ||
        (next->type == REC_FN && (next->flags & FLAG_FN_NEW))) {
        aggregate->value = saturating_add(aggregate->value, next->value);
        if (next->type == REC_MCDC && strcmp(aggregate->text, next->text) != 0) {
            fprintf(diagnostics, "lcovmerge: %s:%" PRIu64 ": MC/DC expression mismatch for %s:%" PRIu64 " group %" PRIu64 " index %" PRIu64 "\n",
                    input_name_for(options, next->input_id), next->origin_line,
                    next->path, next->a, next->b, next->c);
            if (strcmp(next->text, aggregate->text) < 0) {
                size_t length = next->text_length;
                memcpy(aggregate->text, next->text, length + 1);
                aggregate->text_length = next->text_length;
            }
        }
    } else if (next->type == REC_BRDA) {
        if (aggregate->flags & FLAG_DASH) {
            if (!(next->flags & FLAG_DASH)) {
                aggregate->value = next->value;
                aggregate->flags &= (uint8_t)~FLAG_DASH;
            }
        } else if (!(next->flags & FLAG_DASH)) aggregate->value = saturating_add(aggregate->value, next->value);
    } else if (next->type == REC_DA) {
        aggregate->value = saturating_add(aggregate->value, next->value);
        if (aggregate->extra[0] && next->extra[0] && strcmp(aggregate->extra, next->extra) != 0) {
            if (options->strict_checksum) {
                fprintf(diagnostics, "lcovmerge: %s:%" PRIu64 ": checksum mismatch for %s:%" PRIu64 "\n",
                        input_name_for(options, next->input_id), next->origin_line,
                        next->path, next->a);
                return 2;
            }
            fprintf(diagnostics, "lcovmerge: %s:%" PRIu64 ": checksum mismatch for %s:%" PRIu64 "\n",
                    input_name_for(options, next->input_id), next->origin_line, next->path, next->a);
        }
        if (!aggregate->extra[0] || (next->extra[0] && strcmp(next->extra, aggregate->extra) < 0)) {
            size_t length = next->extra_length;
            memcpy(aggregate->extra, next->extra, length + 1);
            aggregate->extra_length = next->extra_length;
        }
    }
    return 0;
}

static int merge_group(const char *const *paths, size_t count, const char *tmpdir,
                       char **out_path, int final_output, OutputState *output,
                       const Options *options) {
    if (count == 0 || count > RUN_FANIN_MAX) return -1;
    RunReader *readers = calloc(count, sizeof(*readers));
    size_t *heap = malloc(count * sizeof(*heap));
    char *aggregate_storage = malloc(3u * MAX_LINE + 3u);
    if (!readers || !heap || !aggregate_storage) {
        free(readers); free(heap); free(aggregate_storage);
        return -1;
    }
    PathCompareCache path_cache[RUN_FANIN_MAX * RUN_FANIN_MAX] = {{0}};
    size_t heap_count = 0;
    lm_handle output_handle = LM_INVALID_HANDLE;
    Writer run_writer;
    int have_run_writer = 0;
    if (!final_output) {
        if (lm_create_temp(tmpdir, out_path, &output_handle, NULL) != 0 ||
            writer_init(&run_writer, output_handle) != 0) {
            if (output_handle != LM_INVALID_HANDLE) (void)lm_close(output_handle);
            if (out_path && *out_path) { (void)lm_remove(*out_path); free(*out_path); *out_path = NULL; }
            free(readers); free(heap); free(aggregate_storage);
            return -1;
        }
        have_run_writer = 1;
    }
    int status = 0;
    uint64_t next_path_id = 0;
    for (size_t i = 0; i < count; ++i) {
        readers[i].handle = LM_INVALID_HANDLE;
        readers[i].path = paths[i];
        readers[i].storage_capacity = 4096;
        readers[i].storage = malloc(readers[i].storage_capacity);
        if (!readers[i].storage || lm_open_read(paths[i], &readers[i].handle) != 0) {
            fprintf(stderr, "lcovmerge: %s:0: cannot read temporary run\n", paths[i]);
            status = 3;
            break;
        }
        int got = run_reader_next(&readers[i], i, count, &next_path_id, readers);
        if (got < 0) {
            fprintf(stderr, "lcovmerge: %s:0: corrupt temporary run\n", paths[i]);
            status = 3;
            break;
        }
    }
    if (status == 0) {
        for (size_t i = 0; i < count; ++i)
            if (readers[i].has_row)
                heap_push(heap, &heap_count, i, readers, path_cache, count);
    }
    Record aggregate;
    int have_aggregate = 0;
    while (status == 0 && heap_count != 0) {
        if (atomic_load_explicit(&interrupted, memory_order_relaxed)) { status = 3; break; }
        size_t reader_index = heap[0];
        Record *row = &readers[reader_index].row;
        if (!final_output) {
            if (write_record(&run_writer, row) != 0) { status = 3; break; }
        } else if (have_aggregate && same_key(&aggregate, row)) {
            if (merge_aggregate(&aggregate, row, options, stderr) != 0) { status = options->strict_checksum ? 2 : 3; break; }
        } else {
            if (have_aggregate && output_record(output, &aggregate) != 0) { status = 3; break; }
            aggregate_copy(&aggregate, row, aggregate_storage);
            have_aggregate = 1;
        }
        int got = run_reader_next(&readers[reader_index], reader_index, count,
                                  &next_path_id, readers);
        if (got < 0) {
            fprintf(stderr, "lcovmerge: %s:0: corrupt temporary run\n", readers[reader_index].path);
            status = 3;
            break;
        }
        if (got > 0) {
            heap_sift_down_root(heap, heap_count, readers, path_cache, count);
        }
        else {
            --heap_count;
            if (heap_count != 0) {
                heap[0] = heap[heap_count];
                heap_sift_down_root(heap, heap_count, readers, path_cache, count);
            }
        }
    }
    if (status == 0 && final_output && have_aggregate && output_record(output, &aggregate) != 0) status = 3;
    if (have_run_writer) {
        if (status == 0 && writer_flush(&run_writer) != 0) status = 3;
        writer_destroy(&run_writer);
        if (lm_close(output_handle) != 0 && status == 0) status = 3;
        if (status != 0 && out_path && *out_path) { (void)lm_remove(*out_path); free(*out_path); *out_path = NULL; }
    }
    for (size_t i = 0; i < count; ++i) {
        if (readers[i].handle != LM_INVALID_HANDLE) (void)lm_close(readers[i].handle);
        free(readers[i].storage);
        free(readers[i].path_storage);
    }
    free(readers);
    free(heap);
    free(aggregate_storage);
    return status;
}

static int output_directory(const char *filename, char **directory_out) {
    char *copy = duplicate_string(filename);
    if (!copy) return -1;
    char *separator = NULL;
    for (char *cursor = copy; *cursor; ++cursor)
        if (*cursor == '/' || *cursor == '\\') separator = cursor;
    if (!separator) {
        free(copy);
        *directory_out = duplicate_string(".");
        return *directory_out ? 0 : -1;
    }
    if (separator == copy || (separator == copy + 2 && copy[1] == ':')) separator[1] = '\0';
    else *separator = '\0';
    *directory_out = copy;
    return 0;
}

static int create_output_temp(const char *output_path, OutputFile *file) {
    char *directory = NULL;
    if (output_directory(output_path, &directory) != 0) {
        fprintf(stderr, "lcovmerge: %s:0: cannot determine output parent directory; "
                        "ensure it exists and is writable\n", output_path);
        return -1;
    }
    unsigned long system_error = 0;
    if (lm_create_temp(directory, &file->path, &file->handle, &system_error) != 0) {
        char reason[256];
        lm_format_error(system_error, reason, sizeof(reason));
        fprintf(stderr, "lcovmerge: %s:0: cannot create output temporary file: %s; "
                        "parent directory '%s' must exist and be writable\n",
                output_path, reason, directory);
        free(directory);
        return -1;
    }
    file->owns_handle = 1;
    free(directory);
    return 0;
}

static int combine_worker_runs(Worker *workers, unsigned count, RunList *runs) {
    for (unsigned worker_index = 0; worker_index < count; ++worker_index) {
        for (size_t i = 0; i < workers[worker_index].runs.count; ++i) {
            char *path = workers[worker_index].runs.paths[i];
            workers[worker_index].runs.paths[i] = NULL;
            if (run_list_add(runs, path) != 0) {
                (void)lm_remove(path);
                free(path);
                return -1;
            }
        }
        free(workers[worker_index].runs.paths);
        memset(&workers[worker_index].runs, 0, sizeof(workers[worker_index].runs));
    }
    return 0;
}

#define DIRECT_FALLBACK (-1)

static Record *direct_current_row(DirectInput *input) {
    return &input->worker.direct_rows[input->worker.direct_head];
}

static int direct_input_next(DirectInput *input, size_t *memory_used) {
    Worker *worker = &input->worker;
    if (worker->direct_count != 0) return 1;
    free(worker->direct_deferred_text);
    worker->direct_deferred_text = NULL;
    if (input->eof) return 0;
    worker->current_file = input->filename;
    for (;;) {
        if (atomic_load_explicit(&interrupted, memory_order_relaxed)) {
            (void)worker_error(worker, 3, input->filename, input->reader.line_no, "interrupted");
            return -1;
        }
        int got = line_reader_next(&input->reader);
        if (got == 0) {
            if (input->section_open) {
                free(input->path);
                input->path = NULL;
            }
            input->parser_chunk.last_source_path = NULL;
            input->eof = 1;
            worker->input_bytes = input->reader.bytes_read;
            worker->current_file = NULL;
            return 0;
        }
        if (got == -1) {
            (void)worker_error(worker, 3, input->filename, input->reader.line_no + 1u, "input read failed");
            return -1;
        }
        if (got == -2 || got == -3) {
            const char *message = got == -3 ? "line exceeds 1 MiB" : "binary or invalid UTF-8 input";
            uint64_t line = input->reader.line_no + (got == -3 ? UINT64_C(1) : UINT64_C(0));
            (void)worker_error(worker, 2, input->filename, line, "%s", message);
            return -1;
        }
        if (input->reader.line_capacity > worker->direct_max_line_bytes) {
            size_t growth = input->reader.line_capacity - worker->direct_max_line_bytes;
            worker->direct_max_line_bytes = input->reader.line_capacity;
            *memory_used = saturating_add((uint64_t)*memory_used, (uint64_t)growth);
            if (*memory_used > worker->direct_memory_limit) {
                worker->direct_unsorted = 1;
                return DIRECT_FALLBACK;
            }
        }
        input->reader.line[input->reader.line_len] = '\0';
        int parse_status = 0;
        int direct = 0;
        size_t parse_length = input->reader.line_len;
        char saved_cr = 0;
        if (parse_length != 0 && input->reader.line[parse_length - 1u] == '\r') {
            saved_cr = '\r';
            input->reader.line[--parse_length] = '\0';
        }
        if (input->section_open && parse_length >= 3u &&
            input->reader.line[0] == 'D' && input->reader.line[1] == 'A' &&
            input->reader.line[2] == ':') {
            direct = parse_direct_da(worker, input->reader.line, parse_length,
                                     worker->worker_id, input->reader.line_no,
                                     input->path, input->keep_path);
        } else if (input->section_open && worker->options->branch_coverage &&
                   parse_length >= 5u && memcmp(input->reader.line, "BRDA:", 5u) == 0) {
            direct = parse_direct_brda(worker, input->reader.line, parse_length,
                                       worker->worker_id, input->reader.line_no,
                                       input->path, input->keep_path);
        }
        if (direct == 0) {
            if (saved_cr) input->reader.line[parse_length] = saved_cr;
            parse_status = parse_record_line(worker, &input->parser_chunk, input->reader.line,
                                             input->filename, worker->worker_id, input->reader.line_no,
                                             input->reader.line_len, &input->path, &input->keep_path,
                                             &input->section_open, &input->pending_tn,
                                             &input->fnl_index, &input->fnl_line, &input->fnl_end,
                                             &input->fnl_valid);
        } else parse_status = direct < 0 ? -1 : 0;
        if (parse_status != 0) {
            if (worker->direct_unsorted) return DIRECT_FALLBACK;
            return -1;
        }
        if (worker->direct_count != 0) return 1;
    }
}

static int direct_heap_less(size_t left, size_t right, DirectInput *inputs,
                            PathCompareCache *cache, size_t count) {
    size_t low = left < right ? left : right;
    size_t high = left < right ? right : left;
    PathCompareCache *entry = &cache[low * count + high];
    uint64_t low_generation = inputs[low].worker.direct_path_generation;
    uint64_t high_generation = inputs[high].worker.direct_path_generation;
    if (!entry->valid || entry->generation_left != low_generation ||
        entry->generation_right != high_generation) {
        entry->comparison = string_compare(direct_current_row(&inputs[low])->path,
                                           direct_current_row(&inputs[high])->path);
        entry->generation_left = low_generation;
        entry->generation_right = high_generation;
        entry->valid = 1;
    }
    int path_order = left == low ? entry->comparison : -entry->comparison;
    if (path_order != 0) return path_order < 0;
    return record_total_compare_same_path(direct_current_row(&inputs[left]),
                                          direct_current_row(&inputs[right])) < 0;
}

static size_t direct_tree_winner(size_t left, size_t right, DirectInput *inputs,
                                 PathCompareCache *cache, size_t count) {
    if (left == SIZE_MAX) return right;
    if (right == SIZE_MAX) return left;
    return direct_heap_less(left, right, inputs, cache, count) ? left : right;
}

static void direct_tree_update(size_t *tree, size_t leaf_count, size_t input_index,
                               size_t winner, DirectInput *inputs,
                               PathCompareCache *cache, size_t count) {
    size_t node = leaf_count + input_index;
    tree[node] = winner;
    while (node > 1u) {
        node /= 2u;
        tree[node] = direct_tree_winner(tree[node * 2u], tree[node * 2u + 1u],
                                        inputs, cache, count);
    }
}

static int direct_same_key(const Record *left, const Record *right) {
    if (left->type == REC_EXT || right->type == REC_EXT ||
        strcmp(left->path, right->path) != 0) return 0;
    if (left->type == REC_TN && right->type == REC_TN) return 1;
    return record_key_compare_same_path(left, right) == 0;
}

static int replay_diagnostics(FILE *diagnostics) {
    if (fflush(diagnostics) != 0 || fseek(diagnostics, 0, SEEK_SET) != 0) return -1;
    unsigned char buffer[8192];
    size_t amount;
    while ((amount = fread(buffer, 1, sizeof(buffer), diagnostics)) != 0)
        if (fwrite(buffer, 1, amount, stderr) != amount) return -1;
    return ferror(diagnostics) || fflush(stderr) != 0 ? -1 : 0;
}

static void direct_input_destroy(DirectInput *input, int *close_failed) {
    if (input->reader_initialized) {
        line_reader_destroy(&input->reader);
        input->reader_initialized = 0;
    }
    if (input->opened) {
        if (lm_close(input->handle) != 0) *close_failed = 1;
        input->opened = 0;
    }
    free(input->path);
    free(input->pending_tn);
    free(input->worker.direct_last_storage);
    free(input->worker.direct_path_storage);
    free(input->worker.direct_deferred_text);
}

static int run_direct_merge(const Options *options) {
    size_t count = options->inputs.count;
    if (strcmp(options->output, "-") == 0 || count == 0 || count > UINT32_MAX) return DIRECT_FALLBACK;
    unsigned descriptor_limit = lm_max_open_files();
    if (descriptor_limit <= 8u || count > (size_t)(descriptor_limit - 8u))
        return DIRECT_FALLBACK;
    for (size_t i = 0; i < count; ++i)
        if (strcmp(options->inputs.items[i], "-") == 0 ||
            !lm_is_regular_file(options->inputs.items[i])) return DIRECT_FALLBACK;
    if (count > SIZE_MAX / count || count * count > SIZE_MAX / sizeof(PathCompareCache))
        return DIRECT_FALLBACK;
    FILE *diagnostics = tmpfile();
    if (!diagnostics) return DIRECT_FALLBACK;
    size_t leaf_count = 1u;
    while (leaf_count < count) {
        if (leaf_count > SIZE_MAX / 2u) { fclose(diagnostics); return DIRECT_FALLBACK; }
        leaf_count *= 2u;
    }
    if (leaf_count > SIZE_MAX / 2u / sizeof(size_t)) {
        fclose(diagnostics);
        return DIRECT_FALLBACK;
    }
    if (count > SIZE_MAX / sizeof(DirectInput) ||
        count > SIZE_MAX / LINE_INITIAL_CAPACITY) {
        fclose(diagnostics);
        return DIRECT_FALLBACK;
    }
    size_t *tree = malloc(leaf_count * 2u * sizeof(*tree));
    DirectInput *inputs = calloc(count, sizeof(*inputs));
    PathCompareCache *cache = calloc(count * count, sizeof(*cache));
    if (!inputs || !tree || !cache) {
        free(inputs); free(tree); free(cache); fclose(diagnostics);
        return DIRECT_FALLBACK;
    }
    size_t memory_limit = options->mem_limit / 2u;
    if (memory_limit > ((size_t)16u << 20)) memory_limit = (size_t)16u << 20;
    size_t memory_used = sizeof(*inputs) * count + sizeof(*tree) * leaf_count * 2u +
                         sizeof(*cache) * count * count + WRITE_BLOCK;
    if (memory_used > SIZE_MAX - count * LINE_INITIAL_CAPACITY) {
        free(inputs); free(tree); free(cache); fclose(diagnostics);
        return DIRECT_FALLBACK;
    }
    memory_used += count * LINE_INITIAL_CAPACITY;
    int fallback = memory_used > memory_limit;
    int status = 0;
    for (size_t i = 0; i < count && !fallback; ++i) {
        DirectInput *input = &inputs[i];
        input->handle = LM_INVALID_HANDLE;
        input->filename = options->inputs.items[i];
        input->worker.options = options;
        input->worker.worker_id = (unsigned)i;
        input->worker.worker_count = (unsigned)count;
        input->worker.direct_mode = 1;
        input->worker.diagnostic_file = diagnostics;
        input->worker.direct_memory_used = &memory_used;
        input->worker.direct_memory_limit = memory_limit;
        input->worker.direct_max_line_bytes = LINE_INITIAL_CAPACITY;
        if (lm_open_read(input->filename, &input->handle) != 0) {
            fallback = 1;
            break;
        }
        input->opened = 1;
        if (line_reader_init(&input->reader, input->handle) != 0) {
            fallback = 1;
            break;
        }
        input->reader_initialized = 1;
        if (memory_used > memory_limit) fallback = 1;
    }
    size_t active_count = 0;
    for (size_t i = 0; i < leaf_count * 2u; ++i) tree[i] = SIZE_MAX;
    for (size_t i = 0; i < count && !fallback && status == 0; ++i) {
        int got = direct_input_next(&inputs[i], &memory_used);
        if (got == DIRECT_FALLBACK) fallback = 1;
        else if (got < 0) status = inputs[i].worker.error_code ? inputs[i].worker.error_code : 3;
        else if (got > 0) { tree[leaf_count + i] = i; ++active_count; }
    }
    if (!fallback && status == 0)
        for (size_t node = leaf_count; node-- > 1u;)
            tree[node] = direct_tree_winner(tree[node * 2u], tree[node * 2u + 1u],
                                            inputs, cache, count);

    OutputFile file;
    memset(&file, 0, sizeof(file));
    file.handle = LM_INVALID_HANDLE;
    OutputState output;
    memset(&output, 0, sizeof(output));
    int writer_initialized = 0;
    if (!fallback && status == 0) {
        if (create_output_temp(options->output, &file) != 0) status = 3;
        if (status == 0 && writer_init(&output.writer, file.handle) != 0) {
            fprintf(stderr, "lcovmerge: %s:0: out of memory\n", options->output);
            status = 3;
        } else if (status == 0) writer_initialized = 1;
    }
    char *aggregate_storage = NULL;
    if (!fallback && status == 0) {
        aggregate_storage = malloc(3u * MAX_LINE + 3u);
        if (!aggregate_storage) status = 3;
    }
    Record aggregate;
    int have_aggregate = 0;
    while (!fallback && status == 0 && active_count != 0) {
        if (atomic_load_explicit(&interrupted, memory_order_relaxed)) { status = 3; break; }
        size_t input_index = tree[1];
        Worker *worker = &inputs[input_index].worker;
        Record *row = direct_current_row(&inputs[input_index]);
        if (have_aggregate && direct_same_key(&aggregate, row)) {
            int merged = merge_aggregate(&aggregate, row, options, diagnostics);
            if (merged != 0) { status = merged == 2 ? 2 : 3; break; }
        } else {
            if (have_aggregate && output_record(&output, &aggregate) != 0) { status = 3; break; }
            aggregate_copy(&aggregate, row, aggregate_storage);
            have_aggregate = 1;
        }
        worker->direct_head = (worker->direct_head + 1u) % 2u;
        --worker->direct_count;
        int got = direct_input_next(&inputs[input_index], &memory_used);
        if (got == DIRECT_FALLBACK) { fallback = 1; break; }
        if (got < 0) { status = worker->error_code ? worker->error_code : 3; break; }
        if (got == 0) {
            --active_count;
            direct_tree_update(tree, leaf_count, input_index, SIZE_MAX,
                               inputs, cache, count);
        } else {
            direct_tree_update(tree, leaf_count, input_index, input_index,
                               inputs, cache, count);
        }
    }
    if (!fallback && status == 0 && have_aggregate && output_record(&output, &aggregate) != 0) status = 3;
    if (!fallback && status == 0 && output_finish_file(&output) != 0) status = 3;
    if (writer_initialized && status == 0 && writer_flush(&output.writer) != 0) status = 3;
    if (writer_initialized) writer_destroy(&output.writer);
    free(output.path);
    free(output.fn_group_name);
    free(aggregate_storage);
    uint64_t total_bytes = 0, total_records = 0;
    int close_failed = 0;
    for (size_t i = 0; i < count; ++i) {
        total_bytes = saturating_add(total_bytes, inputs[i].worker.input_bytes);
        total_records = saturating_add(total_records, inputs[i].worker.records);
        direct_input_destroy(&inputs[i], &close_failed);
    }
    if (!fallback && close_failed && status == 0) status = 3;
    if (file.owns_handle && lm_close(file.handle) != 0 && status == 0) status = 3;
    if (!fallback && status == 0 && interrupted) {
        (void)worker_error(&inputs[0].worker, 3, NULL, 0, "interrupted");
        status = 3;
    }
    if (!fallback && status == 0 && file.owns_handle && lm_rename(file.path, options->output) != 0) {
        fprintf(stderr, "lcovmerge: %s:0: cannot replace output\n", options->output);
        status = 3;
    }
    if (file.path && (fallback || status != 0)) (void)lm_remove(file.path);
    if (!fallback && status != 0) {
        (void)replay_diagnostics(diagnostics);
        for (size_t i = 0; i < count; ++i)
            if (inputs[i].worker.error_code != 0) print_worker_error(&inputs[i].worker);
    } else if (!fallback && replay_diagnostics(diagnostics) != 0 && status == 0) status = 3;
    if (!fallback && status == 0 && options->verbose && !options->quiet)
        fprintf(stderr, "lcovmerge: parsed %" PRIu64 " input bytes into %" PRIu64 " records using 1 job (sorted-input fast path)\n",
                total_bytes, total_records);
    if (!fallback && status == 0 && (options->stats || (options->verbose && !options->quiet)))
        fprintf(stderr, "lcovmerge: stats LF=%" PRIu64 " LH=%" PRIu64
                " FNF=%" PRIu64 " FNH=%" PRIu64 " BRF=%" PRIu64 " BRH=%" PRIu64 "\n",
                output.total_lf, output.total_lh, output.total_fnf, output.total_fnh,
                output.total_brf, output.total_brh);
    free(file.path);
    free(inputs); free(tree); free(cache);
    fclose(diagnostics);
    return fallback ? DIRECT_FALLBACK : status;
}

static int run_merge(const Options *options) {
    for (size_t i = 0; i < options->inputs.count; ++i) {
        const char *input = options->inputs.items[i];
        if (strcmp(input, "-") != 0 && !lm_is_regular_file(input)) {
            fprintf(stderr, "lcovmerge: %s:0: input must be a regular file; use '-' for standard input\n",
                    input);
            return 3;
        }
    }
    int direct_status = run_direct_merge(options);
    if (direct_status != DIRECT_FALLBACK) return direct_status;
    size_t stdin_count = 0;
    for (size_t i = 0; i < options->inputs.count; ++i)
        if (strcmp(options->inputs.items[i], "-") == 0) ++stdin_count;
    if (stdin_count > 1) {
        fprintf(stderr, "lcovmerge: stdin may appear only once\n");
        return 1;
    }
    unsigned job_count = options->jobs;
    if ((size_t)job_count > options->inputs.count) job_count = (unsigned)options->inputs.count;
    size_t worker_floor = (size_t)job_count * MIN_WORKER_BUDGET;
    size_t arena_limit = options->mem_limit > ARENA_HEADROOM ? options->mem_limit - ARENA_HEADROOM : 0;
    if (arena_limit < worker_floor) arena_limit = worker_floor;
    size_t budget = arena_limit / job_count;
    Worker *workers = calloc(job_count, sizeof(*workers));
    lm_thread *threads = job_count > 1 ? calloc(job_count - 1u, sizeof(*threads)) : NULL;
    unsigned started = 0;
    if (!workers || (job_count > 1 && !threads)) {
        free(workers); free(threads);
        fprintf(stderr, "lcovmerge: out of memory\n");
        return 3;
    }
    for (unsigned i = 0; i < job_count; ++i) {
        workers[i].options = options;
        workers[i].worker_id = i;
        workers[i].worker_count = job_count;
        workers[i].budget = budget;
    }
    for (unsigned i = 1; i < job_count; ++i) {
        if (lm_thread_start(&threads[i - 1u], worker_main, &workers[i]) != 0) {
            fprintf(stderr, "lcovmerge: cannot start worker thread\n");
            for (unsigned j = 0; j < started; ++j) (void)lm_thread_join(threads[j]);
            for (unsigned j = 0; j < job_count; ++j) run_list_cleanup(&workers[j].runs);
            free(workers); free(threads);
            return 3;
        }
        ++started;
    }
    (void)worker_main(&workers[0]);
    for (unsigned i = 0; i < started; ++i) {
        if (lm_thread_join(threads[i]) != 0) workers[i + 1u].error_code = 3;
    }
    int status = 0;
    for (unsigned i = 0; i < job_count; ++i) {
        if (interrupted && workers[i].error_code == 0)
            (void)worker_error(&workers[i], 3, workers[i].current_file, 0, "interrupted");
        if (workers[i].error_code != 0) {
            print_worker_error(&workers[i]);
            if (status == 0 || workers[i].error_code == 2) status = workers[i].error_code;
        }
    }
    if (status != 0) {
        for (unsigned i = 0; i < job_count; ++i) run_list_cleanup(&workers[i].runs);
        free(workers); free(threads);
        return status;
    }
    if (options->verbose && !options->quiet) {
        uint64_t bytes = 0, records = 0;
        for (unsigned i = 0; i < job_count; ++i) {
            bytes = saturating_add(bytes, workers[i].input_bytes);
            records = saturating_add(records, workers[i].records);
        }
        fprintf(stderr, "lcovmerge: parsed %" PRIu64 " input bytes into %" PRIu64 " records using %u job(s)\n",
                bytes, records, job_count);
    }
    RunList runs = {0};
    if (combine_worker_runs(workers, job_count, &runs) != 0) status = 3;
    if (status != 0)
        for (unsigned i = 0; i < job_count; ++i) run_list_cleanup(&workers[i].runs);
    free(workers);
    free(threads);
    if (status == 0 && reduce_runs(&runs, options) != 0) status = 3;

    OutputFile file;
    memset(&file, 0, sizeof(file));
    file.handle = LM_INVALID_HANDLE;
    if (status == 0) {
        if (strcmp(options->output, "-") == 0) file.handle = lm_stdout_handle();
        else {
            if (create_output_temp(options->output, &file) != 0) status = 3;
        }
    }
    OutputState output;
    memset(&output, 0, sizeof(output));
    if (status == 0 && writer_init(&output.writer, file.handle) != 0) {
        fprintf(stderr, "lcovmerge: %s:0: out of memory\n", options->output);
        status = 3;
    }
    if (status == 0 && runs.count != 0)
        status = merge_group((const char *const *)runs.paths, runs.count,
                             options->tmpdir, NULL, 1, &output, options);
    if (status == 0 && output_finish_file(&output) != 0) status = 3;
    if (status == 0 && writer_flush(&output.writer) != 0) status = 3;
    writer_destroy(&output.writer);
    free(output.fn_group_name);
    free(output.path);
    if (file.owns_handle && lm_close(file.handle) != 0 && status == 0) status = 3;
    if (file.owns_handle) {
        if (status == 0 && interrupted) {
            fprintf(stderr, "lcovmerge: interrupted\n");
            status = 3;
        }
        if (status == 0) {
            if (lm_rename(file.path, options->output) != 0) {
                fprintf(stderr, "lcovmerge: %s:0: cannot replace output\n", options->output);
                status = 3;
            }
        }
        if (status != 0) (void)lm_remove(file.path);
    }
    if (status == 0 && (options->stats || (options->verbose && !options->quiet))) {
        fprintf(stderr, "lcovmerge: stats LF=%" PRIu64 " LH=%" PRIu64
                " FNF=%" PRIu64 " FNH=%" PRIu64 " BRF=%" PRIu64 " BRH=%" PRIu64 "\n",
                output.total_lf, output.total_lh, output.total_fnf, output.total_fnh,
                output.total_brf, output.total_brh);
    }
    free(file.path);
    run_list_cleanup(&runs);
    return status;
}

int lcovmerge_main(int argc, char **argv) {
    if (install_signal_handlers() != 0) {
        fprintf(stderr, "lcovmerge: cannot install signal handlers: %s\n", strerror(errno));
        return 3;
    }
    Options options;
    int option_status = parse_options(argc, argv, &options);
    if (option_status == 0) { options_destroy(&options); return 0; }
    if (option_status != 99) { options_destroy(&options); return option_status; }
    StringList raw_inputs = options.inputs;
    memset(&options.inputs, 0, sizeof(options.inputs));
    int expansion_status = 0;
    for (size_t i = 0; i < raw_inputs.count; ++i) {
        expansion_status = expand_input_argument(&options, raw_inputs.items[i], 0);
        if (expansion_status != 0) break;
    }
    list_free(&raw_inputs);
    if (expansion_status != 0) { options_destroy(&options); return expansion_status; }
    if (options.inputs.count == 0) {
        fprintf(stderr, "lcovmerge: no input files\n");
        options_destroy(&options);
        return 1;
    }
    int result = run_merge(&options);
    options_destroy(&options);
    return result;
}
