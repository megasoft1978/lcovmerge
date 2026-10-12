#include "lm_internal.h"

int write_record(Writer *writer, const Record *record) {
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

int flush_chunk(Chunk *chunk, RunList *runs, const char *tmpdir) {
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

int chunk_add(Chunk *chunk, RunList *runs, const char *tmpdir, const Record *record) {
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

int chunk_init(Chunk *chunk, size_t budget) {
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

void chunk_destroy(Chunk *chunk) {
    free(chunk->rows);
    free(chunk->arena);
    memset(chunk, 0, sizeof(*chunk));
}
