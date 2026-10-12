#ifndef LM_INTERNAL_H
#define LM_INTERNAL_H

#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif
#include "platform.h"
#include "version.h"

#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <signal.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_LINE ((size_t)1u << 20)
#define IO_BLOCK ((size_t)1u << 14)
#define WRITE_BLOCK ((size_t)1u << 18)
#define RUN_FANIN_MAX 32u
#define MAX_JOBS 32u
#define DEFAULT_MEM_LIMIT ((size_t)64u << 20)
#define MIN_WORKER_BUDGET ((size_t)8u << 20)
#define ARENA_HEADROOM ((size_t)24u << 20)
#define LINE_INITIAL_CAPACITY ((size_t)4096u)

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
    uint32_t path_length, text_length, extra_length;
    uint64_t a, b, c, value, origin_line;
    uint32_t input_id;
    uint8_t type, flags;
    uint64_t path_id;
} Record;

typedef struct {
    Record *rows;
    Record last_row;
    char *last_path;
    const char *last_source_path;
    uint32_t last_path_length;
    size_t count, capacity;
    char *arena;
    size_t arena_size, arena_used;
    size_t budget;
    int have_last_row;
    int sorted;
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
    size_t line_len, line_capacity;
    uint64_t line_no;
    uint64_t bytes_read;
} LineReader;

typedef struct {
    lm_handle handle;
    unsigned char *buffer;
    size_t used, capacity;
    char *run_path;
    size_t run_path_capacity;
    size_t run_path_length;
    uint64_t run_path_id;
    int failed;
} Writer;

typedef struct {
    lm_handle handle;
    unsigned char block[IO_BLOCK];
    size_t pos, len;
    unsigned char *storage;
    size_t storage_capacity;
    char *path_storage;
    size_t path_capacity;
    Record row;
    uint32_t path_length;
    uint64_t path_id;
    uint64_t path_generation;
    int have_path;
    int has_row;
    const char *path;
} RunReader;

typedef struct {
    uint64_t generation_left, generation_right;
    int comparison;
    int valid;
} PathCompareCache;

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
    char error_message[4096];
    uint64_t error_line;
    uint64_t input_bytes;
    uint64_t records;
    uint64_t warnings;
    const char *current_file;
    FILE *diagnostic_file;
    Record direct_rows[2], direct_last_row;
    char *direct_last_storage, *direct_path_storage;
    char *direct_deferred_text;
    const char *direct_current_path;
    size_t direct_current_path_length;
    size_t direct_last_capacity, direct_path_capacity;
    size_t direct_count, direct_head;
    size_t *direct_memory_used;
    size_t direct_memory_limit, direct_max_line_bytes;
    uint64_t direct_path_generation;
    int direct_mode, direct_unsorted, have_direct_last_row;
} Worker;

typedef struct {
    Worker worker;
    Chunk parser_chunk;
    LineReader reader;
    lm_handle handle;
    const char *filename;
    char *path, *pending_tn;
    uint64_t fnl_index, fnl_line, fnl_end;
    int fnl_valid, keep_path, section_open, opened, reader_initialized, eof;
} DirectInput;

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

typedef struct {
    uint32_t path_length, text_length, extra_length;
    uint8_t type, flags;
    uint16_t reserved;
    uint64_t a, b, c, value, origin_line;
    uint32_t input_id;
} DiskRecord;

#define DIRECT_FALLBACK (-1)

extern atomic_bool interrupted;

int record_key_compare(const Record *left, const Record *right);
int wildcard_match(const char *pattern, const char *text);
int utf8_valid(const unsigned char *bytes, size_t length);
int add_rebase(Options *options, const char *mapping);
void options_destroy(Options *options);
int list_add_string(StringList *list, const char *item);
int writer_init(Writer *writer, lm_handle handle);
int writer_u64(Writer *writer, uint64_t value);
void writer_destroy(Writer *writer);
int parse_size(const char *text, size_t *value);
int record_total_compare(const Record *left, const Record *right);
int record_total_compare_same_path(const Record *left, const Record *right);
char *rewrite_path(const Options *options, const char *path);
int path_selected(const Options *options, const char *path);
char *duplicate_string(const char *text);
int line_reader_init(LineReader *reader, lm_handle handle);
int line_reader_next(LineReader *reader);
void line_reader_destroy(LineReader *reader);

static inline uint64_t saturating_add(uint64_t left, uint64_t right) {
    return left > UINT64_MAX - right ? UINT64_MAX : left + right;
}

static inline int string_compare(const char *left, const char *right) {
    int result = strcmp(left, right);
    return (result > 0) - (result < 0);
}

int install_signal_handlers(void);
char *duplicate_range(const char *text, size_t length);
void list_free(StringList *list);
int run_list_add(RunList *list, char *path);
void run_list_cleanup(RunList *list);

int writer_flush(Writer *writer);
int writer_format(Writer *writer, const char *format, ...);

int record_key_compare_same_path(const Record *left, const Record *right);
int record_qsort_compare(const void *left, const void *right);
int same_key(const Record *left, const Record *right);

int write_record(Writer *writer, const Record *record);
int flush_chunk(Chunk *chunk, RunList *runs, const char *tmpdir);
int chunk_add(Chunk *chunk, RunList *runs, const char *tmpdir, const Record *record);
int chunk_init(Chunk *chunk, size_t budget);
void chunk_destroy(Chunk *chunk);

int output_record(OutputState *output, const Record *record);
int output_finish_file(OutputState *output);
int merge_aggregate(Record *aggregate, const Record *next, const Options *options,
                           FILE *diagnostics);
int create_output_temp(const char *output_path, OutputFile *file);

int worker_error(Worker *worker, int code, const char *file, uint64_t line, const char *format, ...);
int worker_temp_run_error(Worker *worker, const char *file, uint64_t line);
int parse_record_line(Worker *worker, Chunk *chunk, char *line,
                             const char *filename, uint32_t input_id,
                             uint64_t line_no, size_t length, char **path, int *keep_path,
                             int *section_open, char **pending_tn,
                             uint64_t *fnl_index, uint64_t *fnl_line,
                             uint64_t *fnl_end, int *fnl_valid);
int parse_direct_da(Worker *worker, char *line, size_t length,
                           uint32_t input_id, uint64_t line_no, char *path, int keep_path);
int parse_direct_brda(Worker *worker, char *line, size_t length,
                             uint32_t input_id, uint64_t line_no, char *path,
                             int keep_path);
int parse_input_file(Worker *worker, Chunk *chunk, const char *filename, uint32_t input_id);

void print_worker_error(const Worker *worker);
int run_direct_merge(const Options *options);

int run_merge(const Options *options);

int writer_bytes_slow(Writer *writer, const void *data, size_t length);

static inline int writer_bytes(Writer *writer, const void *data, size_t length) {
    if (length <= writer->capacity - writer->used) {
        memcpy(writer->buffer + writer->used, data, length);
        writer->used += length;
        return 0;
    }
    return writer_bytes_slow(writer, data, length);
}

static inline int writer_text(Writer *writer, const char *text) {
    return writer_bytes(writer, text, strlen(text));
}

static inline int writer_char(Writer *writer, char value) {
    if (writer->used < writer->capacity) {
        writer->buffer[writer->used++] = (unsigned char)value;
        return 0;
    }
    return writer_bytes_slow(writer, &value, 1);
}

static inline int parse_u64(const char *text, size_t length, uint64_t *value) {
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

static inline void aggregate_copy(Record *destination, const Record *source, char *storage) {
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

#endif
