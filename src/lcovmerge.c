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
