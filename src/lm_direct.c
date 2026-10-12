#include "lm_internal.h"

static Record *direct_current_row(DirectInput *input) {
    return &input->worker.direct_rows[input->worker.direct_head];
}

/* Rows of a partition's SECTION rows must lie in [low, high) of the partition's splitters. */
static int direct_rows_in_range(const DirectInput *input) {
    const Worker *worker = &input->worker;
    for (size_t i = 0; i < worker->direct_count; ++i) {
        const Record *row = &worker->direct_rows[(worker->direct_head + i) % 2u];
        if (row->type != REC_SECTION) continue;
        if (input->range_low &&
            bytes_compare(row->path, row->path_length, input->range_low, input->range_low_length) < 0)
            return 0;
        if (input->range_high &&
            bytes_compare(row->path, row->path_length, input->range_high, input->range_high_length) >= 0)
            return 0;
    }
    return 1;
}

static int direct_input_next(DirectInput *input) {
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
        int peeking = 0;
        if (input->range_mode) {
            if (input->abort_flag && atomic_load_explicit(input->abort_flag, memory_order_relaxed)) {
                (void)worker_error(worker, 3, input->filename, input->reader.line_no, "partition run aborted");
                return -1;
            }
            /* The line at the range end is what the whole-file path reads next; parse it for its
               diagnostics and order check, then drop its rows (the next partition owns them). */
            peeking = input->range_peek && line_reader_offset(&input->reader) >= input->range_end;
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
            if (direct_charge(worker, growth)) return DIRECT_FALLBACK;
        }
        input->reader.line[input->reader.line_len] = '\0';
        if (input->range_fix_first) {
            /* First line of a partition: parse silently as a section start; a joined
               end_of_recordSF: line loses its prefix (the previous partition owns the warning). */
            input->range_fix_first = 0;
            static const char joined_prefix[] = "end_of_record";
            size_t prefix_length = sizeof(joined_prefix) - 1u;
            if (input->reader.line_len >= prefix_length + 3u &&
                memcmp(input->reader.line, joined_prefix, prefix_length) == 0 &&
                (memcmp(input->reader.line + prefix_length, "SF:", 3u) == 0 ||
                 memcmp(input->reader.line + prefix_length, "KF:", 3u) == 0)) {
                memmove(input->reader.line, input->reader.line + prefix_length,
                        input->reader.line_len - prefix_length + 1u);
                input->reader.line_len -= prefix_length;
            }
        }
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
        if (peeking) {
            if (worker->direct_count == 0) {
                (void)worker_error(worker, 3, input->filename, input->reader.line_no,
                                   "partition boundary is not a section start");
                return -1;
            }
            worker->records -= worker->direct_count;
            worker->direct_count = 0;
            free(worker->direct_deferred_text);
            worker->direct_deferred_text = NULL;
            input->eof = 1;
            worker->current_file = NULL;
            return 0;
        }
        if (worker->direct_count != 0) {
            if (input->range_mode && !direct_rows_in_range(input)) {
                (void)worker_error(worker, 3, input->filename, input->reader.line_no,
                                   "section outside its partition range");
                return -1;
            }
            return 1;
        }
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
        const Record *low_row = direct_current_row(&inputs[low]);
        const Record *high_row = direct_current_row(&inputs[high]);
        entry->comparison = FIELD_COMPARE(low_row, high_row, path);
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
        left->path_length != right->path_length ||
        memcmp(left->path, right->path, left->path_length) != 0) return 0;
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

static void direct_input_set_range(DirectInput *input, const PartitionPlan *plan,
                                   size_t index, unsigned part, atomic_bool *abort_flag) {
    const PartPoint *begin = &plan->points[index * plan->stride + part];
    const PartPoint *end = &plan->points[index * plan->stride + part + 1u];
    input->range_mode = 1;
    input->abort_flag = abort_flag;
    input->range_end = end->offset;
    input->range_peek = end->offset < plan->sizes[index] &&
                        (part == 0 || end->offset > begin->offset);
    if (part > 0) {
        input->range_low = plan->splitters[part - 1u];
        input->range_low_length = strlen(input->range_low);
    }
    if (part + 1u < plan->parts) {
        input->range_high = plan->splitters[part];
        input->range_high_length = strlen(input->range_high);
    }
    line_reader_seek(&input->reader, begin->offset);
    if (part > 0 && begin->offset == end->offset) input->eof = 1;
    else if (part > 0) input->range_fix_first = 1;
}

void direct_result_free(DirectResult *result) {
    free(result->file.path);
    free(result->inputs);
    free(result->growth);
    if (result->diagnostics) fclose(result->diagnostics);
    memset(result, 0, sizeof(*result));
}

/*
 * Charges buffer growth to the run's budget; returns 1 (and marks the input unsorted, which makes
 * the run give up) when the budget is exceeded. Partitioned runs also add to one total shared by all
 * partitions, so their combined growth, not just each partition's own, stays inside the budget.
 */
int direct_charge(Worker *worker, size_t growth) {
    *worker->direct_memory_used = saturating_add((uint64_t)*worker->direct_memory_used, (uint64_t)growth);
    int over = *worker->direct_memory_used > worker->direct_memory_limit;
    if (worker->direct_shared_growth) {
        uint64_t total = (uint64_t)atomic_fetch_add_explicit(worker->direct_shared_growth, (uint_fast64_t)growth,
                                                             memory_order_relaxed) + growth;
        if (total > worker->direct_shared_limit) over = 1;
    }
    if (over) worker->direct_unsorted = 1;
    return over;
}

/*
 * One pass of the sorted-input tournament merge. Without a plan it reads whole files on private
 * handles and is the single-job path; with a plan it merges partition job->part (byte ranges of the
 * shared handles, rows restricted to the partition's path range) into the caller's staged file.
 * Partitioned runs print nothing: any anomaly makes the caller rerun the serial path.
 */
void direct_partition_run(const DirectJob *job, DirectResult *result) {
    const Options *options = job->options;
    const PartitionPlan *plan = job->plan;
    int partitioned = plan != NULL;
    size_t count = options->inputs.count;
    memset(result, 0, sizeof(*result));
    result->file.handle = LM_INVALID_HANDLE;
    FILE *diagnostics = job->diagnostics ? job->diagnostics : tmpfile();
    result->diagnostics = diagnostics;
    if (!diagnostics) { result->fallback = 1; return; }
    size_t fixed_cost = direct_fixed_cost(count);
    if (fixed_cost == SIZE_MAX) { result->fallback = 1; return; }
    size_t leaf_count = 1u;
    while (leaf_count < count) leaf_count *= 2u;
    size_t *tree = malloc(leaf_count * 2u * sizeof(*tree));
    DirectInput *inputs = calloc(count, sizeof(*inputs));
    PathCompareCache *cache = calloc(count * count, sizeof(*cache));
    if (!inputs || !tree || !cache) {
        free(inputs); free(tree); free(cache);
        result->fallback = 1;
        return;
    }
    size_t memory_limit = options->mem_limit / 2u;
    if (memory_limit > ((size_t)16u << 20)) memory_limit = (size_t)16u << 20;
    size_t memory_used = fixed_cost;
    result->fixed_cost = fixed_cost;
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
        input->worker.direct_shared_growth = job->shared_growth;
        input->worker.direct_shared_limit = memory_limit - fixed_cost;
        input->worker.direct_max_line_bytes = LINE_INITIAL_CAPACITY;
        if (partitioned) input->handle = plan->handles[i];
        else if (lm_open_read(input->filename, &input->handle) != 0) {
            fallback = 1;
            break;
        } else input->opened = 1;
        if (line_reader_init(&input->reader, input->handle) != 0) {
            fallback = 1;
            break;
        }
        input->reader_initialized = 1;
        if (partitioned) {
            direct_input_set_range(input, plan, i, job->part, job->abort_flag);
            const char *pending = plan->points[i * plan->stride + job->part].pending_tn;
            if (input->range_fix_first && pending) {
                input->pending_tn = duplicate_string(pending);
                if (!input->pending_tn) { fallback = 1; break; }
            }
        }
        if (memory_used > memory_limit) fallback = 1;
    }
    size_t active_count = 0;
    for (size_t i = 0; i < leaf_count * 2u; ++i) tree[i] = SIZE_MAX;
    for (size_t i = 0; i < count && !fallback && status == 0; ++i) {
        int got = direct_input_next(&inputs[i]);
        if (got == DIRECT_FALLBACK) fallback = 1;
        else if (got < 0) status = inputs[i].worker.error_code ? inputs[i].worker.error_code : 3;
        else if (got > 0) { tree[leaf_count + i] = i; ++active_count; }
    }
    if (!fallback && status == 0)
        for (size_t node = leaf_count; node-- > 1u;)
            tree[node] = direct_tree_winner(tree[node * 2u], tree[node * 2u + 1u],
                                            inputs, cache, count);

    OutputFile *file = partitioned ? job->prepared_output : &result->file;
    OutputState output;
    memset(&output, 0, sizeof(output));
    int writer_initialized = 0;
    if (!fallback && status == 0) {
        if (!partitioned && create_output_temp(options->output, file) != 0) status = 3;
        if (status == 0 && writer_init(&output.writer, file->handle) != 0) {
            if (!partitioned) fprintf(stderr, "lcovmerge: %s:0: out of memory\n", options->output);
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
        if (job->abort_flag && atomic_load_explicit(job->abort_flag, memory_order_relaxed)) {
            status = 3;
            break;
        }
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
        int got = direct_input_next(&inputs[input_index]);
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
    result->total_fnf = output.total_fnf;
    result->total_fnh = output.total_fnh;
    result->total_lf = output.total_lf;
    result->total_lh = output.total_lh;
    result->total_brf = output.total_brf;
    result->total_brh = output.total_brh;
    if (partitioned && !fallback) result->growth = calloc(count, sizeof(*result->growth));
    int close_failed = 0;
    for (size_t i = 0; i < count; ++i) {
        result->input_bytes = saturating_add(result->input_bytes, inputs[i].worker.input_bytes);
        result->records = saturating_add(result->records, inputs[i].worker.records);
        if (result->growth) {
            const Worker *worker = &inputs[i].worker;
            result->growth[i] = worker->direct_max_line_bytes - LINE_INITIAL_CAPACITY +
                                worker->direct_last_capacity + worker->direct_path_capacity;
        }
        direct_input_destroy(&inputs[i], &close_failed);
    }
    if (!fallback && close_failed && status == 0) status = 3;
    if (partitioned && !fallback && !result->growth) status = 3;
    if (partitioned) {
        if (fflush(diagnostics) != 0 || ftell(diagnostics) != 0) result->had_diagnostics = 1;
        free(inputs);
    } else {
        result->inputs = inputs;
        result->count = count;
    }
    free(tree);
    free(cache);
    result->fallback = fallback;
    result->status = status;
}

static int run_direct_serial(const Options *options) {
    DirectJob job;
    memset(&job, 0, sizeof(job));
    job.options = options;
    DirectResult result;
    direct_partition_run(&job, &result);
    int fallback = result.fallback;
    int status = result.status;
    OutputFile *file = &result.file;
    if (file->owns_handle && lm_close(file->handle) != 0 && status == 0) status = 3;
    if (!fallback && status == 0 && interrupted) {
        (void)worker_error(&result.inputs[0].worker, 3, NULL, 0, "interrupted");
        status = 3;
    }
    if (!fallback && status == 0 && file->owns_handle && lm_rename(file->path, options->output) != 0) {
        fprintf(stderr, "lcovmerge: %s:0: cannot replace output\n", options->output);
        status = 3;
    }
    if (file->path && (fallback || status != 0)) (void)lm_remove(file->path);
    if (!fallback && status != 0) {
        (void)replay_diagnostics(result.diagnostics);
        for (size_t i = 0; i < result.count; ++i)
            if (result.inputs[i].worker.error_code != 0) print_worker_error(&result.inputs[i].worker);
    } else if (!fallback && replay_diagnostics(result.diagnostics) != 0 && status == 0) status = 3;
    if (!fallback && status == 0 && options->verbose && !options->quiet)
        fprintf(stderr, "lcovmerge: parsed %" PRIu64 " input bytes into %" PRIu64 " records using 1 job (sorted-input fast path)\n",
                result.input_bytes, result.records);
    if (!fallback && status == 0 && (options->stats || (options->verbose && !options->quiet)))
        fprintf(stderr, "lcovmerge: stats LF=%" PRIu64 " LH=%" PRIu64
                " FNF=%" PRIu64 " FNH=%" PRIu64 " BRF=%" PRIu64 " BRH=%" PRIu64 "\n",
                result.total_lf, result.total_lh, result.total_fnf, result.total_fnh,
                result.total_brf, result.total_brh);
    direct_result_free(&result);
    return fallback ? DIRECT_FALLBACK : status;
}

int run_direct_merge(const Options *options) {
    size_t count = options->inputs.count;
    if (strcmp(options->output, "-") == 0 || count == 0 || count > UINT32_MAX) return DIRECT_FALLBACK;
    unsigned descriptor_limit = lm_max_open_files();
    if (descriptor_limit <= 8u || count > (size_t)(descriptor_limit - 8u))
        return DIRECT_FALLBACK;
    for (size_t i = 0; i < count; ++i)
        if (strcmp(options->inputs.items[i], "-") == 0 ||
            !lm_is_regular_file(options->inputs.items[i])) return DIRECT_FALLBACK;
    if (direct_fixed_cost(count) == SIZE_MAX) return DIRECT_FALLBACK;
    /* Opt-in: partitions only for an explicit -j N >= 2 with P_eff > 1; otherwise DIRECT_RERUN. */
    int status = direct_run_partitioned(options);
    if (status != DIRECT_RERUN) return status;
    return run_direct_serial(options);
}

/* Per-partition fixed memory of the direct path; SIZE_MAX if the sizes overflow. */
size_t direct_fixed_cost(size_t count) {
    if (count == 0 || count > UINT32_MAX || count > SIZE_MAX / count ||
        count * count > SIZE_MAX / sizeof(PathCompareCache)) return SIZE_MAX;
    size_t leaf_count = 1u;
    while (leaf_count < count) {
        if (leaf_count > SIZE_MAX / 2u) return SIZE_MAX;
        leaf_count *= 2u;
    }
    if (leaf_count > SIZE_MAX / 2u / sizeof(size_t) || count > SIZE_MAX / sizeof(DirectInput) ||
        count > SIZE_MAX / LINE_INITIAL_CAPACITY) return SIZE_MAX;
    size_t cost = sizeof(DirectInput) * count + sizeof(size_t) * leaf_count * 2u +
                  sizeof(PathCompareCache) * count * count + WRITE_BLOCK;
    if (cost > SIZE_MAX - count * LINE_INITIAL_CAPACITY) return SIZE_MAX;
    return cost + count * LINE_INITIAL_CAPACITY;
}
