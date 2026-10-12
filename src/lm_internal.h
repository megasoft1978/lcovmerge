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

#endif
