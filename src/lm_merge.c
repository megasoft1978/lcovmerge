#include "lm_internal.h"

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

void print_worker_error(const Worker *worker) {
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
            readers[i].row.path_length == reader->path_length &&
            memcmp(reader->path_storage, readers[i].row.path, reader->path_length) == 0) {
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
        path_comparison = FIELD_COMPARE(&left_reader->row, &right_reader->row, path);
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
            if (merge_aggregate(&aggregate, row, options, stderr, NULL) != 0) { status = options->strict_checksum ? 2 : 3; break; }
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

int run_merge(const Options *options) {
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
