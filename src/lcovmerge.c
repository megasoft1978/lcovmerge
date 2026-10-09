#define _POSIX_C_SOURCE 200809L
#include "platform.h"
#include "version.h"

#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_LINE ((size_t)1u << 20)
#define IO_BLOCK ((size_t)1u << 16)
#define WRITE_BLOCK ((size_t)1u << 18)
#define RUN_FANIN_MAX 16u
#define MAX_JOBS 32u
#define DEFAULT_MEM_LIMIT ((size_t)64u << 20)

enum RecordType {
    REC_TN = 1,
    REC_SECTION,
    REC_GROUP,
    REC_FN,
    REC_FNDA,
    REC_BRDA,
    REC_MCDC,
    REC_DA,
    REC_EXT
};

enum RecordFlags {
    FLAG_FN_NEW = 1u,
    FLAG_NUMERIC_BRANCH = 1u,
    FLAG_BRANCH_TEXT = 2u,
    FLAG_DASH = 4u,
    FLAG_BR_EXCEPTION = 8u,
    FLAG_BR_FALLTHROUGH = 16u,
    FLAG_BR_UNREACHABLE = 32u,
    FLAG_MCDC_TRUE = 64u,
    FLAG_MCDC_UNREACHABLE = 128u
};

typedef struct {
    char *path;
    char *text;
    char *extra;
    uint64_t a, b, c, value, origin_line;
    uint32_t input_id;
    uint8_t type, flags;
} Record;

typedef struct {
    Record *rows;
    size_t count, capacity;
    char *arena;
    size_t arena_size, arena_used;
    size_t budget;
} Chunk;

typedef struct {
    char **items;
    size_t count, capacity;
} StringList;

typedef struct {
    char **paths;
    size_t count, capacity;
} RunList;

typedef struct {
    lm_handle handle;
    unsigned char block[IO_BLOCK];
    size_t pos, len;
    char *line;
    size_t line_len;
    uint64_t line_no;
    uint64_t bytes_read;
} LineReader;

typedef struct {
    lm_handle handle;
    unsigned char *buffer;
    size_t used, capacity;
    int failed;
} Writer;

typedef struct {
    lm_handle handle;
    unsigned char block[IO_BLOCK];
    size_t pos, len;
    unsigned char *storage;
    size_t storage_capacity;
    Record row;
    int has_row;
    const char *path;
} RunReader;

typedef struct {
    char *old_prefix;
    char *new_prefix;
} Rebase;

typedef struct {
    StringList inputs;
    StringList includes;
    StringList excludes;
    Rebase *rebases;
    size_t rebase_count, rebase_capacity;
    char *prefix_strip;
    char *output;
    char *tmpdir;
    size_t mem_limit;
    unsigned jobs;
    int jobs_explicit;
    int branch_coverage;
    int function_data;
    int quiet;
    int verbose;
    int stats;
    int warn_unknown;
    int strict_checksum;
} Options;

typedef struct {
    const Options *options;
    unsigned worker_id, worker_count;
    size_t budget;
    RunList runs;
    int error_code;
    char error_file[4096];
    char error_message[256];
    uint64_t error_line;
    uint64_t input_bytes;
    uint64_t records;
    uint64_t warnings;
    const char *current_file;
} Worker;

typedef struct {
    lm_handle handle;
    char *path;
    int owns_handle;
} OutputFile;

typedef struct {
    Writer writer;
    char *path;
    int active;
    int source_written;
    uint64_t fnf, fnh, lf, lh, brf, brh, mcf, mch;
    uint64_t total_fnf, total_fnh, total_lf, total_lh, total_brf, total_brh;
    uint64_t fn_group_a, fn_group_b, fn_group_c;
    char *fn_group_name;
    int have_fn_group, fn_group_hit;
} OutputState;

static volatile sig_atomic_t interrupted;

static void on_signal(int signal_number) {
    (void)signal_number;
    interrupted = 1;
}

static void print_usage(FILE *stream) {
    fputs("usage: lcovmerge [options] a.info b.info ... -o out.info\n"
          "  -o, --output FILE           output file, or - for stdout\n"
          "  --mem-limit SIZE            working arena cap (K, M, or G; default 64M)\n"
          "  --tmpdir DIR                temporary run directory\n"
          "  -j, --jobs N                parallel run generation (default min(4, cores))\n"
          "  --prefix-strip PREFIX       remove this source path prefix\n"
          "  --rebase OLD=NEW            rewrite source path prefixes (repeatable)\n"
          "  --include GLOB              include matching SF paths (repeatable)\n"
          "  --exclude GLOB              exclude matching SF paths (repeatable)\n"
          "  --branch-coverage on|off    retain or drop BRDA rows (default on)\n"
          "  --no-function-data          drop FN/FNDA/FNL/FNA data\n"
          "  --strict-checksum           fail if checksums disagree\n"
          "  --warn-unknown              report preserved unknown X: rows\n"
          "  -q                          suppress progress\n"
          "  -v                          show progress and merged statistics\n"
          "  --stats                     print merged LF/LH/FNF/FNH/BRF/BRH to stderr\n"
          "  --version                   print version and git commit\n"
          "  --help                      show this text\n", stream);
}

static char *duplicate_range(const char *text, size_t length) {
    if (length == SIZE_MAX) return NULL;
    char *copy = malloc(length + 1);
    if (!copy) return NULL;
    if (length) memcpy(copy, text, length);
    copy[length] = '\0';
    return copy;
}

static char *duplicate_string(const char *text) {
    return duplicate_range(text, strlen(text));
}

static int list_add(StringList *list, char *item) {
    if (list->count == list->capacity) {
        size_t next = list->capacity ? list->capacity * 2 : 16;
        if (next < list->capacity || next > SIZE_MAX / sizeof(*list->items)) return -1;
        char **grown = realloc(list->items, next * sizeof(*list->items));
        if (!grown) return -1;
        list->items = grown;
        list->capacity = next;
    }
    list->items[list->count++] = item;
    return 0;
}

static int list_add_string(StringList *list, const char *item) {
    char *copy = duplicate_string(item);
    if (!copy) return -1;
    if (list_add(list, copy) != 0) { free(copy); return -1; }
    return 0;
}

static void list_free(StringList *list) {
    for (size_t i = 0; i < list->count; ++i) free(list->items[i]);
    free(list->items);
    memset(list, 0, sizeof(*list));
}

static int parse_u64(const char *text, size_t length, uint64_t *value) {
    if (length == 0) return 0;
    uint64_t result = 0;
    for (size_t i = 0; i < length; ++i) {
        unsigned char ch = (unsigned char)text[i];
        if (ch < (unsigned char)'0' || ch > (unsigned char)'9') return 0;
        uint64_t digit = (uint64_t)(ch - (unsigned char)'0');
        if (result > (UINT64_MAX - digit) / UINT64_C(10)) return 0;
        result = result * UINT64_C(10) + digit;
    }
    *value = result;
    return 1;
}

static int parse_size(const char *text, size_t *value) {
    size_t length = strlen(text);
    uint64_t multiplier = 1;
    if (length == 0) return 0;
    unsigned char suffix = (unsigned char)text[length - 1];
    if (suffix == (unsigned char)'k' || suffix == (unsigned char)'K') { multiplier = UINT64_C(1024); --length; }
    else if (suffix == (unsigned char)'m' || suffix == (unsigned char)'M') { multiplier = UINT64_C(1024) * UINT64_C(1024); --length; }
    else if (suffix == (unsigned char)'g' || suffix == (unsigned char)'G') { multiplier = UINT64_C(1024) * UINT64_C(1024) * UINT64_C(1024); --length; }
    uint64_t amount;
    if (!parse_u64(text, length, &amount) || amount > (uint64_t)SIZE_MAX / multiplier) return 0;
    *value = (size_t)(amount * multiplier);
    return 1;
}

static uint64_t saturating_add(uint64_t left, uint64_t right) {
    return left > UINT64_MAX - right ? UINT64_MAX : left + right;
}

static int writer_flush(Writer *writer) {
    size_t pos = 0;
    while (pos < writer->used) {
        size_t amount = 0;
        if (lm_write(writer->handle, writer->buffer + pos, writer->used - pos, &amount) != 0 || amount == 0) {
            writer->failed = 1;
            return -1;
        }
        pos += amount;
    }
    writer->used = 0;
    return 0;
}

static int writer_init(Writer *writer, lm_handle handle) {
    memset(writer, 0, sizeof(*writer));
    writer->capacity = WRITE_BLOCK;
    writer->buffer = malloc(writer->capacity);
    if (!writer->buffer) return -1;
    writer->handle = handle;
    return 0;
}

static int writer_bytes(Writer *writer, const void *data, size_t length) {
    const unsigned char *bytes = data;
    while (length) {
        size_t available = writer->capacity - writer->used;
        if (available == 0 && writer_flush(writer) != 0) return -1;
        available = writer->capacity - writer->used;
        size_t amount = length < available ? length : available;
        memcpy(writer->buffer + writer->used, bytes, amount);
        writer->used += amount;
        bytes += amount;
        length -= amount;
    }
    return 0;
}

static int writer_text(Writer *writer, const char *text) {
    return writer_bytes(writer, text, strlen(text));
}

static int writer_format(Writer *writer, const char *format, ...) {
    char buffer[256];
    va_list arguments;
    va_start(arguments, format);
    int count = vsnprintf(buffer, sizeof(buffer), format, arguments);
    va_end(arguments);
    if (count < 0 || (size_t)count >= sizeof(buffer)) return -1;
    return writer_bytes(writer, buffer, (size_t)count);
}

static void writer_destroy(Writer *writer) {
    free(writer->buffer);
    memset(writer, 0, sizeof(*writer));
}

static int utf8_valid(const unsigned char *bytes, size_t length) {
    size_t i = 0;
    while (i < length) {
        unsigned char c = bytes[i++];
        if (c == 0) return 0;
        if (c < 0x80) continue;
        unsigned need;
        uint32_t code;
        if (c >= 0xc2 && c <= 0xdf) { need = 1; code = (uint32_t)(c & 0x1f); }
        else if (c >= 0xe0 && c <= 0xef) { need = 2; code = (uint32_t)(c & 0x0f); }
        else if (c >= 0xf0 && c <= 0xf4) { need = 3; code = (uint32_t)(c & 0x07); }
        else return 0;
        if ((size_t)need > length - i) return 0;
        for (unsigned j = 0; j < need; ++j) {
            unsigned char tail = bytes[i++];
            if ((tail & 0xc0u) != 0x80u) return 0;
            code = (code << 6) | (uint32_t)(tail & 0x3fu);
        }
        if ((need == 1 && code < 0x80u) || (need == 2 && code < 0x800u) ||
            (need == 3 && code < 0x10000u) || code > 0x10ffffu ||
            (code >= 0xd800u && code <= 0xdfffu)) return 0;
    }
    return 1;
}

static int line_reader_init(LineReader *reader, lm_handle handle) {
    memset(reader, 0, sizeof(*reader));
    reader->line = malloc(MAX_LINE + 1);
    if (!reader->line) return -1;
    reader->handle = handle;
    return 0;
}

static void line_reader_destroy(LineReader *reader) {
    free(reader->line);
    memset(reader, 0, sizeof(*reader));
}

/* Returns 1 for a line, 0 at EOF, -1 for I/O, and -2 for an invalid line. */
static int line_reader_next(LineReader *reader) {
    reader->line_len = 0;
    for (;;) {
        if (reader->pos == reader->len) {
            size_t amount = 0;
            if (lm_read(reader->handle, reader->block, sizeof(reader->block), &amount) != 0) return -1;
            reader->pos = 0;
            reader->len = amount;
            if (amount == 0) {
                if (reader->line_len == 0) return 0;
                ++reader->line_no;
                return utf8_valid((const unsigned char *)reader->line, reader->line_len) ? 1 : -2;
            }
            reader->bytes_read = saturating_add(reader->bytes_read, (uint64_t)amount);
        }
        unsigned char *newline = memchr(reader->block + reader->pos, '\n', reader->len - reader->pos);
        size_t segment = newline ? (size_t)(newline - (reader->block + reader->pos)) : reader->len - reader->pos;
        if (segment > MAX_LINE - reader->line_len) return -3;
        if (segment) memcpy(reader->line + reader->line_len, reader->block + reader->pos, segment);
        reader->line_len += segment;
        reader->pos += segment + (newline ? 1u : 0u);
        if (newline) {
            ++reader->line_no;
            if (!utf8_valid((const unsigned char *)reader->line, reader->line_len)) return -2;
            reader->line[reader->line_len] = '\0';
            return 1;
        }
    }
}

static int string_compare(const char *left, const char *right) {
    int result = strcmp(left, right);
    return (result > 0) - (result < 0);
}

static int new_function_event(const Record *record) {
    return (record->type == REC_GROUP || record->type == REC_FN) &&
           (record->flags & FLAG_FN_NEW) != 0;
}

static unsigned record_order(const Record *record) {
    if (record->type == REC_TN) return 0u;
    if (record->type == REC_SECTION) return 1u;
    if (record->type == REC_GROUP && !new_function_event(record)) return 2u;
    if (new_function_event(record)) return 3u;
    if (record->type == REC_FN) return 4u;
    if (record->type == REC_FNDA) return 5u;
    if (record->type == REC_BRDA) return 6u;
    if (record->type == REC_MCDC) return 7u;
    if (record->type == REC_DA) return 8u;
    return 9u;
}

static int record_key_compare(const Record *left, const Record *right) {
    int result = string_compare(left->path, right->path);
    if (result) return result;
    int left_new = new_function_event(left);
    int right_new = new_function_event(right);
    if (left_new && right_new) {
        if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
        if (left->b != right->b) return (left->b > right->b) - (left->b < right->b);
        if (left->c != right->c) return (left->c > right->c) - (left->c < right->c);
        if (left->type != right->type) return left->type == REC_GROUP ? -1 : 1;
        if (left->type == REC_GROUP) return string_compare(left->extra, right->extra);
        return string_compare(left->text, right->text);
    }
    unsigned left_order = record_order(left), right_order = record_order(right);
    if (left_order != right_order) return (left_order > right_order) - (left_order < right_order);
    if (left->type != right->type) return (left->type > right->type) - (left->type < right->type);
    switch (left->type) {
        case REC_TN:
        case REC_EXT:
            return string_compare(left->text, right->text);
        case REC_SECTION:
            return 0;
        case REC_GROUP:
            if ((left->flags & FLAG_FN_NEW) != (right->flags & FLAG_FN_NEW))
                return (left->flags > right->flags) - (left->flags < right->flags);
            if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
            if (left->b != right->b) return (left->b > right->b) - (left->b < right->b);
            if (left->c != right->c) return (left->c > right->c) - (left->c < right->c);
            return string_compare(left->extra, right->extra);
        case REC_FN:
            if ((left->flags & FLAG_FN_NEW) != (right->flags & FLAG_FN_NEW))
                return (left->flags > right->flags) - (left->flags < right->flags);
            if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
            if (left->b != right->b) return (left->b > right->b) - (left->b < right->b);
            if (left->c != right->c) return (left->c > right->c) - (left->c < right->c);
            result = string_compare(left->extra, right->extra);
            return result ? result : string_compare(left->text, right->text);
        case REC_FNDA:
            if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
            if (left->b != right->b) return (left->b > right->b) - (left->b < right->b);
            if (left->c != right->c) return (left->c > right->c) - (left->c < right->c);
            result = string_compare(left->extra, right->extra);
            return result ? result : string_compare(left->text, right->text);
        case REC_BRDA:
            if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
            if (left->b != right->b) return (left->b > right->b) - (left->b < right->b);
            if (left->c != right->c) return (left->c > right->c) - (left->c < right->c);
            if ((left->flags & (FLAG_BR_EXCEPTION | FLAG_BR_FALLTHROUGH | FLAG_BR_UNREACHABLE)) !=
                (right->flags & (FLAG_BR_EXCEPTION | FLAG_BR_FALLTHROUGH | FLAG_BR_UNREACHABLE)))
                return (left->flags > right->flags) - (left->flags < right->flags);
            return string_compare(left->text, right->text);
        case REC_MCDC:
            if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
            if (left->b != right->b) return (left->b > right->b) - (left->b < right->b);
            if (left->c != right->c) return (left->c > right->c) - (left->c < right->c);
            if (left->flags != right->flags) return (left->flags > right->flags) - (left->flags < right->flags);
            return 0;
        case REC_DA:
            if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
            return 0;
        default:
            return 0;
    }
}

static int record_total_compare(const Record *left, const Record *right) {
    int result = record_key_compare(left, right);
    if (result) return result;
    if (left->type == REC_DA) {
        result = string_compare(left->extra, right->extra);
        if (result) return result;
    }
    if (left->flags != right->flags) return (left->flags > right->flags) - (left->flags < right->flags);
    if (left->value != right->value) return (left->value > right->value) - (left->value < right->value);
    result = string_compare(left->text, right->text);
    if (result) return result;
    result = string_compare(left->extra, right->extra);
    if (result) return result;
    if (left->input_id != right->input_id) return (left->input_id > right->input_id) - (left->input_id < right->input_id);
    return (left->origin_line > right->origin_line) - (left->origin_line < right->origin_line);
}

static int record_qsort_compare(const void *left, const void *right) {
    return record_total_compare((const Record *)left, (const Record *)right);
}

static int same_key(const Record *left, const Record *right) {
    /* Unknown extensions have no defined merge operator; preserve every row. */
    if (left->type == REC_EXT || right->type == REC_EXT) return 0;
    return record_key_compare(left, right) == 0;
}

static int run_list_add(RunList *list, char *path) {
    if (list->count == list->capacity) {
        size_t next = list->capacity ? list->capacity * 2 : 16;
        if (next < list->capacity || next > SIZE_MAX / sizeof(*list->paths)) return -1;
        char **grown = realloc(list->paths, next * sizeof(*list->paths));
        if (!grown) return -1;
        list->paths = grown;
        list->capacity = next;
    }
    list->paths[list->count++] = path;
    return 0;
}

static void run_list_cleanup(RunList *list) {
    for (size_t i = 0; i < list->count; ++i) {
        if (list->paths[i]) {
            (void)lm_remove(list->paths[i]);
            free(list->paths[i]);
        }
    }
    free(list->paths);
    memset(list, 0, sizeof(*list));
}

typedef struct {
    uint32_t path_length, text_length, extra_length;
    uint8_t type, flags;
    uint16_t reserved;
    uint64_t a, b, c, value, origin_line;
    uint32_t input_id;
} DiskRecord;

static int write_record(Writer *writer, const Record *record) {
    size_t path_length = strlen(record->path);
    size_t text_length = strlen(record->text);
    size_t extra_length = strlen(record->extra);
    if (path_length > UINT32_MAX || text_length > UINT32_MAX || extra_length > UINT32_MAX) return -1;
    DiskRecord disk;
    memset(&disk, 0, sizeof(disk));
    disk.path_length = (uint32_t)path_length;
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
        writer_bytes(writer, record->path, path_length) != 0 ||
        writer_bytes(writer, record->text, text_length) != 0 ||
        writer_bytes(writer, record->extra, extra_length) != 0) return -1;
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
    if (chunk->count == 0) return 0;
    qsort(chunk->rows, chunk->count, sizeof(*chunk->rows), record_qsort_compare);
    char *path = NULL;
    lm_handle handle = LM_INVALID_HANDLE;
    if (lm_create_temp(tmpdir, &path, &handle) != 0) return -1;
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
    if (result != 0 || run_list_add(runs, path) != 0) {
        (void)lm_remove(path);
        free(path);
        return -1;
    }
    chunk->count = 0;
    chunk->arena_used = 0;
    return 0;
}

static int chunk_add(Chunk *chunk, RunList *runs, const char *tmpdir, const Record *record) {
    size_t path_length = strlen(record->path);
    size_t text_length = strlen(record->text);
    size_t extra_length = strlen(record->extra);
    if (path_length > SIZE_MAX - text_length - extra_length - 3) return -1;
    size_t need = path_length + text_length + extra_length + 3;
    if (chunk->count == chunk->capacity || need > chunk->arena_size - chunk->arena_used) {
        if (flush_chunk(chunk, runs, tmpdir) != 0) return -1;
    }
    if (chunk->count == chunk->capacity || need > chunk->arena_size) return -2;
    Record *stored = &chunk->rows[chunk->count];
    *stored = *record;
    stored->path = arena_copy(chunk, record->path, path_length);
    stored->text = arena_copy(chunk, record->text, text_length);
    stored->extra = arena_copy(chunk, record->extra, extra_length);
    if (!stored->path || !stored->text || !stored->extra) return -2;
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
    return 0;
}

static void chunk_destroy(Chunk *chunk) {
    free(chunk->rows);
    free(chunk->arena);
    memset(chunk, 0, sizeof(*chunk));
}

static int wildcard_match(const char *pattern, const char *text) {
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

static char *rewrite_path(const Options *options, const char *path) {
    char *current = duplicate_string(path);
    if (!current) return NULL;
    for (size_t i = 0; i < options->rebase_count; ++i) {
        size_t matched = 0;
        if (prefix_match(current, options->rebases[i].old_prefix, &matched)) {
            size_t new_length = strlen(options->rebases[i].new_prefix);
            size_t rest = strlen(current + matched);
            size_t old_length = strlen(options->rebases[i].old_prefix);
            char separator = current[old_length] == '\\' ? '\\' : '/';
            int add_separator = rest != 0 && new_length != 0 &&
                options->rebases[i].new_prefix[new_length - 1] != '/' &&
                options->rebases[i].new_prefix[new_length - 1] != '\\';
            if (new_length > SIZE_MAX - rest - (size_t)add_separator - 1) { free(current); return NULL; }
            char *next = malloc(new_length + (size_t)add_separator + rest + 1);
            if (!next) { free(current); return NULL; }
            memcpy(next, options->rebases[i].new_prefix, new_length);
            size_t cursor = new_length;
            if (add_separator) next[cursor++] = separator;
            memcpy(next + cursor, current + matched, rest + 1);
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

static int path_selected(const Options *options, const char *path) {
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

static int add_row(Worker *worker, Chunk *chunk, const char *source_path, uint8_t type,
                   const char *text, const char *extra, uint64_t a, uint64_t b,
                   uint64_t c, uint64_t value, uint8_t flags,
                   uint32_t input_id, uint64_t input_line) {
    Record record;
    memset(&record, 0, sizeof(record));
    record.path = (char *)source_path;
    record.text = (char *)(text ? text : "");
    record.extra = (char *)(extra ? extra : "");
    record.a = a;
    record.b = b;
    record.c = c;
    record.value = value;
    record.flags = flags;
    record.type = type;
    record.input_id = input_id;
    record.origin_line = input_line;
    int result = chunk_add(chunk, &worker->runs, worker->options->tmpdir, &record);
    if (result == -2) return worker_error(worker, 2, worker->current_file, input_line, "record exceeds the configured memory limit");
    if (result != 0) return worker_error(worker, 3, worker->current_file, input_line, "cannot write temporary run");
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
    return parse_u64(field, strlen(field), value);
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
                             uint64_t line_no, char **path, int *keep_path,
                             int *section_open, char **pending_tn,
                             uint64_t *fnl_index, uint64_t *fnl_line,
                             uint64_t *fnl_end, int *fnl_valid) {
    size_t length = strlen(line);
    if (length && line[length - 1] == '\r') line[--length] = '\0';
    if (length == 0 || line[0] == '#') return 0;
    if (strcmp(line, "end_of_record") == 0) {
        if (!*section_open) return worker_error(worker, 2, filename, line_no, "end_of_record without SF record");
        *section_open = 0;
        *fnl_valid = 0;
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
        free(*path);
        *path = rewritten;
        *keep_path = path_selected(worker->options, *path);
        *section_open = 1;
        *fnl_valid = 0;
        if (*keep_path) {
            if (add_row(worker, chunk, *path, REC_SECTION, "", "", 0, 0, 0, 0, 0,
                        input_id, line_no) != 0) return -1;
            if (*pending_tn && append_pending_row(worker, chunk, *path, *pending_tn, input_id, line_no) != 0) return -1;
        }
        free(*pending_tn);
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
            if (add_row(worker, chunk, *path, REC_GROUP, name, name, 0, 0, 0,
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
    if (strncmp(line, "DA:", 3) == 0) {
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
        fprintf(stderr, "lcovmerge: %s:%" PRIu64 ": preserved unknown record %.*s\n",
                filename, line_no, (int)(colon - line), line);
        ++worker->warnings;
    }
    if (*keep_path && add_row(worker, chunk, *path, REC_EXT, line, "", 0, 0, 0, 0, 0,
                              input_id, line_no) != 0) return -1;
    return 0;
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
        if (interrupted) { status = worker_error(worker, 3, filename, reader.line_no, "interrupted"); break; }
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
                              &path, &keep_path, &section_open, &pending_tn,
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

static int add_rebase(Options *options, const char *mapping) {
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

static void options_destroy(Options *options) {
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
    if (!options->jobs_explicit && options->jobs > options->mem_limit / (((size_t)8u << 20)))
        options->jobs = (unsigned)(options->mem_limit / (((size_t)8u << 20)));
    if (options->jobs == 0) options->jobs = 1;
    if (!options->output || options->inputs.count == 0 || options->mem_limit < ((size_t)8u << 20) ||
        options->jobs > options->mem_limit / (((size_t)8u << 20))) goto usage_error;
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
        if (parse_input_file(worker, &chunk, worker->options->inputs.items[i], (uint32_t)i) != 0) break;
    }
    if (worker->error_code == 0 && flush_chunk(&chunk, &worker->runs, worker->options->tmpdir) != 0)
        (void)worker_error(worker, 3, worker->current_file, 0, "cannot write temporary run");
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
    unsigned fanin = (unsigned)(options->mem_limit / (((size_t)4u << 20)));
    if (fanin < 2u) fanin = 2u;
    if (fanin > RUN_FANIN_MAX) fanin = RUN_FANIN_MAX;
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
            if (lm_read(reader->handle, reader->block, sizeof(reader->block), &amount) != 0) return -1;
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

static int run_reader_next(RunReader *reader) {
    DiskRecord disk;
    int got = run_reader_fill(reader, &disk, sizeof(disk));
    if (got <= 0) { reader->has_row = 0; return got; }
    size_t path_length = disk.path_length;
    size_t text_length = disk.text_length;
    size_t extra_length = disk.extra_length;
    if (path_length > MAX_LINE || text_length > MAX_LINE || extra_length > MAX_LINE ||
        text_length > SIZE_MAX - extra_length - 3 ||
        path_length > SIZE_MAX - text_length - extra_length - 3) return -1;
    size_t required = path_length + text_length + extra_length + 3;
    if (required > reader->storage_capacity) {
        unsigned char *grown = realloc(reader->storage, required);
        if (!grown) return -1;
        reader->storage = grown;
        reader->storage_capacity = required;
    }
    size_t total = path_length + text_length + extra_length;
    if (run_reader_fill(reader, reader->storage, total) != 1) return -1;
    /* Shift fields from right to left to make room for one NUL per field. */
    if (extra_length)
        memmove(reader->storage + path_length + text_length + 2,
                reader->storage + path_length + text_length, extra_length);
    if (text_length)
        memmove(reader->storage + path_length + 1, reader->storage + path_length, text_length);
    reader->storage[path_length] = '\0';
    reader->storage[path_length + text_length + 1] = '\0';
    reader->storage[path_length + text_length + extra_length + 2] = '\0';
    reader->row.path = (char *)reader->storage;
    reader->row.text = (char *)reader->storage + path_length + 1;
    reader->row.extra = (char *)reader->storage + path_length + text_length + 2;
    reader->row.type = disk.type;
    reader->row.flags = disk.flags;
    reader->row.a = disk.a;
    reader->row.b = disk.b;
    reader->row.c = disk.c;
    reader->row.value = disk.value;
    reader->row.origin_line = disk.origin_line;
    reader->row.input_id = disk.input_id;
    reader->has_row = 1;
    return 1;
}

static int heap_less(size_t left, size_t right, RunReader *readers) {
    return record_total_compare(&readers[left].row, &readers[right].row) < 0;
}

static void heap_push(size_t *heap, size_t *count, size_t value, RunReader *readers) {
    size_t index = (*count)++;
    while (index != 0) {
        size_t parent = (index - 1) / 2;
        if (!heap_less(value, heap[parent], readers)) break;
        heap[index] = heap[parent];
        index = parent;
    }
    heap[index] = value;
}

static size_t heap_pop(size_t *heap, size_t *count, RunReader *readers) {
    size_t first = heap[0];
    size_t last = heap[--(*count)];
    if (*count != 0) {
        size_t index = 0;
        while (index <= (*count - 1) / 2) {
            size_t child = index * 2 + 1;
            if (child >= *count) break;
            if (child + 1 < *count && heap_less(heap[child + 1], heap[child], readers)) ++child;
            if (!heap_less(heap[child], last, readers)) break;
            heap[index] = heap[child];
            index = child;
        }
        heap[index] = last;
    }
    return first;
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
    if (output->active && strcmp(output->path, record->path) != 0 && output_finish_file(output) != 0) return -1;
    if (start_output_file(output, record->path) != 0) return -1;
    if (record->type == REC_TN) {
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
                if (record->b != 0)
                    return writer_format(&output->writer, "FNL:%" PRIu64 ",%" PRIu64 ",%" PRIu64 "\n",
                                         record->c, record->a, record->b);
                return writer_format(&output->writer, "FNL:%" PRIu64 ",%" PRIu64 "\n",
                                     record->c, record->a);
            }
            return 0;
        case REC_FN:
            if (record->flags & FLAG_FN_NEW) {
                if (add_function_hit(output, record) != 0) return -1;
                if (writer_format(&output->writer, "FNA:%" PRIu64 ",%" PRIu64 ",",
                                  record->c, record->value) != 0) return -1;
                return writer_text(&output->writer, record->text) == 0 &&
                       writer_text(&output->writer, "\n") == 0 ? 0 : -1;
            }
            if (record->b != 0) {
                if (writer_format(&output->writer, "FN:%" PRIu64 ",%" PRIu64 ",", record->a, record->b) != 0) return -1;
            } else if (writer_format(&output->writer, "FN:%" PRIu64 ",", record->a) != 0) return -1;
            return writer_text(&output->writer, record->text) == 0 && writer_text(&output->writer, "\n") == 0 ? 0 : -1;
        case REC_FNDA:
            if (add_function_hit(output, record) != 0) return -1;
            if (writer_format(&output->writer, "FNDA:%" PRIu64 ",", record->value) != 0) return -1;
            return writer_text(&output->writer, record->text) == 0 && writer_text(&output->writer, "\n") == 0 ? 0 : -1;
        case REC_BRDA: {
            if (writer_format(&output->writer, "BRDA:%" PRIu64 ",", record->a) != 0) return -1;
            if ((record->flags & FLAG_BR_EXCEPTION) && writer_text(&output->writer, "e") != 0) return -1;
            if ((record->flags & FLAG_BR_FALLTHROUGH) && writer_text(&output->writer, "f") != 0) return -1;
            if ((record->flags & FLAG_BR_UNREACHABLE) && writer_text(&output->writer, "U") != 0) return -1;
            if (writer_format(&output->writer, "%" PRIu64 ",", record->b) != 0) return -1;
            if (record->flags & FLAG_BRANCH_TEXT) {
                if (writer_text(&output->writer, record->text) != 0) return -1;
            } else if (writer_format(&output->writer, "%" PRIu64, record->c) != 0) return -1;
            if (record->flags & FLAG_DASH) {
                if (writer_text(&output->writer, ",-\n") != 0) return -1;
            } else if (writer_format(&output->writer, ",%" PRIu64 "\n", record->value) != 0) return -1;
            if (!(record->flags & FLAG_BR_UNREACHABLE)) {
                output->brf = saturating_add(output->brf, UINT64_C(1));
                if (record->value != 0 && !(record->flags & FLAG_DASH))
                    output->brh = saturating_add(output->brh, UINT64_C(1));
            }
            return 0;
        }
        case REC_MCDC:
            if (writer_format(&output->writer, "MCDC:%" PRIu64 ",%s%" PRIu64 ",%c,%" PRIu64 ",%" PRIu64 ",",
                              record->a, (record->flags & FLAG_MCDC_UNREACHABLE) ? "U" : "",
                              record->b, (record->flags & FLAG_MCDC_TRUE) ? 't' : 'f',
                              record->value, record->c) != 0 ||
                writer_text(&output->writer, record->text) != 0 || writer_text(&output->writer, "\n") != 0) return -1;
            if (!(record->flags & FLAG_MCDC_UNREACHABLE)) {
                output->mcf = saturating_add(output->mcf, UINT64_C(1));
                if (record->value != 0) output->mch = saturating_add(output->mch, UINT64_C(1));
            }
            return 0;
        case REC_DA:
            if (writer_format(&output->writer, "DA:%" PRIu64 ",%" PRIu64, record->a, record->value) != 0) return -1;
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
    size_t path_length = strlen(source->path);
    size_t text_length = strlen(source->text);
    size_t extra_length = strlen(source->extra);
    destination->path = storage;
    memcpy(destination->path, source->path, path_length + 1);
    destination->text = destination->path + path_length + 1;
    memcpy(destination->text, source->text, text_length + 1);
    destination->extra = destination->text + text_length + 1;
    memcpy(destination->extra, source->extra, extra_length + 1);
    destination->a = source->a;
    destination->b = source->b;
    destination->c = source->c;
    destination->value = source->value;
    destination->origin_line = source->origin_line;
    destination->input_id = source->input_id;
    destination->type = source->type;
    destination->flags = source->flags;
}

static const char *input_name_for(const Options *options, uint32_t input_id) {
    return (size_t)input_id < options->inputs.count ? options->inputs.items[input_id] : "<input>";
}

static int merge_aggregate(Record *aggregate, const Record *next, const Options *options) {
    if (next->type == REC_FNDA || next->type == REC_MCDC ||
        (next->type == REC_FN && (next->flags & FLAG_FN_NEW))) {
        aggregate->value = saturating_add(aggregate->value, next->value);
        if (next->type == REC_MCDC && strcmp(aggregate->text, next->text) != 0) {
            fprintf(stderr, "lcovmerge: %s:%" PRIu64 ": MC/DC expression mismatch for %s:%" PRIu64 " group %" PRIu64 " index %" PRIu64 "\n",
                    input_name_for(options, next->input_id), next->origin_line,
                    next->path, next->a, next->b, next->c);
            if (strcmp(next->text, aggregate->text) < 0) {
                size_t length = strlen(next->text);
                memcpy(aggregate->text, next->text, length + 1);
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
                fprintf(stderr, "lcovmerge: %s:%" PRIu64 ": checksum mismatch for %s:%" PRIu64 "\n",
                        input_name_for(options, next->input_id), next->origin_line,
                        next->path, next->a);
                return 2;
            }
            fprintf(stderr, "lcovmerge: %s:%" PRIu64 ": checksum mismatch for %s:%" PRIu64 "\n",
                    input_name_for(options, next->input_id), next->origin_line, next->path, next->a);
        }
        if (!aggregate->extra[0] || (next->extra[0] && strcmp(next->extra, aggregate->extra) < 0)) {
            size_t length = strlen(next->extra);
            memcpy(aggregate->extra, next->extra, length + 1);
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
    char *aggregate_storage = malloc(2 * MAX_LINE + 3);
    if (!readers || !heap || !aggregate_storage) {
        free(readers); free(heap); free(aggregate_storage);
        return -1;
    }
    size_t heap_count = 0;
    lm_handle output_handle = LM_INVALID_HANDLE;
    Writer run_writer;
    int have_run_writer = 0;
    if (!final_output) {
        if (lm_create_temp(tmpdir, out_path, &output_handle) != 0 || writer_init(&run_writer, output_handle) != 0) {
            if (output_handle != LM_INVALID_HANDLE) (void)lm_close(output_handle);
            if (out_path && *out_path) { (void)lm_remove(*out_path); free(*out_path); *out_path = NULL; }
            free(readers); free(heap); free(aggregate_storage);
            return -1;
        }
        have_run_writer = 1;
    }
    int status = 0;
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
        int got = run_reader_next(&readers[i]);
        if (got < 0) {
            fprintf(stderr, "lcovmerge: %s:0: corrupt temporary run\n", paths[i]);
            status = 3;
            break;
        }
        if (got > 0) heap_push(heap, &heap_count, i, readers);
    }
    Record aggregate;
    int have_aggregate = 0;
    while (status == 0 && heap_count != 0) {
        if (interrupted) { status = 3; break; }
        size_t reader_index = heap_pop(heap, &heap_count, readers);
        Record *row = &readers[reader_index].row;
        if (!final_output) {
            if (write_record(&run_writer, row) != 0) { status = 3; break; }
        } else if (have_aggregate && same_key(&aggregate, row)) {
            if (merge_aggregate(&aggregate, row, options) != 0) { status = options->strict_checksum ? 2 : 3; break; }
        } else {
            if (have_aggregate && output_record(output, &aggregate) != 0) { status = 3; break; }
            aggregate_copy(&aggregate, row, aggregate_storage);
            have_aggregate = 1;
        }
        int got = run_reader_next(&readers[reader_index]);
        if (got < 0) {
            fprintf(stderr, "lcovmerge: %s:0: corrupt temporary run\n", readers[reader_index].path);
            status = 3;
            break;
        }
        if (got > 0) heap_push(heap, &heap_count, reader_index, readers);
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

static int run_merge(const Options *options) {
    size_t stdin_count = 0;
    for (size_t i = 0; i < options->inputs.count; ++i)
        if (strcmp(options->inputs.items[i], "-") == 0) ++stdin_count;
    if (stdin_count > 1) {
        fprintf(stderr, "lcovmerge: stdin may appear only once\n");
        return 1;
    }
    unsigned job_count = options->jobs;
    if ((size_t)job_count > options->inputs.count) job_count = (unsigned)options->inputs.count;
    size_t budget = options->mem_limit / job_count;
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
            char *directory = NULL;
            if (output_directory(options->output, &directory) != 0 ||
                lm_create_temp(directory ? directory : ".", &file.path, &file.handle) != 0) {
                fprintf(stderr, "lcovmerge: %s:0: cannot create output temporary file\n", options->output);
                status = 3;
            } else file.owns_handle = 1;
            free(directory);
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
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
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
