#include "lm_internal.h"

int worker_error(Worker *worker, int code, const char *file, uint64_t line, const char *format, ...) {
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

int worker_temp_run_error(Worker *worker, const char *file, uint64_t line) {
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

int parse_record_line(Worker *worker, Chunk *chunk, char *line,
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
int parse_direct_da(Worker *worker, char *line, size_t length,
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
int parse_direct_brda(Worker *worker, char *line, size_t length,
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

int parse_input_file(Worker *worker, Chunk *chunk, const char *filename, uint32_t input_id) {
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
