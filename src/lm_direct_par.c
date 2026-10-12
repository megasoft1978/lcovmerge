#include "lm_internal.h"

/*
 * Threaded driver for the sorted-input direct path (opt-in: explicit -j N >= 2). The partition
 * finder splits every input at the same SF-path splitters; partition p merges those byte ranges into
 * its own staged file, and the files are appended in path order. Any anomaly (error, warning,
 * out-of-order input, budget overflow, I/O failure) discards everything and reruns the unchanged
 * serial path, which then produces the exact diagnostics, so output bytes, stdout/stderr and exit
 * codes equal the single-job run. Interruption is the one case that never reruns.
 */

typedef struct {
    DirectJob job;
    DirectResult result;
} PartitionTask;

static void run_partition(PartitionTask *task) {
    direct_partition_run(&task->job, &task->result);
    const DirectResult *result = &task->result;
    if (result->fallback || result->status != 0 || result->had_diagnostics)
        atomic_store_explicit(task->job.abort_flag, 1, memory_order_relaxed);
}

static void *partition_thread(void *argument) {
    run_partition(argument);
    return NULL;
}

static int append_file(lm_handle destination, const char *path, unsigned char *buffer,
                       size_t capacity) {
    lm_handle source = LM_INVALID_HANDLE;
    if (lm_open_read(path, &source) != 0) return -1;
    int status = 0;
    while (status == 0) {
        size_t got = 0;
        if (atomic_load_explicit(&interrupted, memory_order_relaxed) ||
            lm_read(source, buffer, capacity, &got, &interrupted) != 0) { status = -1; break; }
        if (got == 0) break;
        for (size_t done = 0; done < got && status == 0;) {
            size_t written = 0;
            if (lm_write(destination, buffer + done, got - done, &written, &interrupted) != 0 ||
                written == 0) status = -1;
            done += written;
        }
    }
    if (lm_close(source) != 0 && status == 0) status = -1;
    return status;
}

/* Everything the attempt created: staged files, diagnostics, results, the plan. */
static void partition_cleanup(PartitionTask *tasks, OutputFile *outputs, lm_thread *threads,
                              PartitionPlan *plan, unsigned prepared) {
    for (unsigned p = 0; p < prepared; ++p) {
        if (outputs[p].owns_handle && outputs[p].handle != LM_INVALID_HANDLE)
            (void)lm_close(outputs[p].handle);
        if (outputs[p].path) (void)lm_remove(outputs[p].path);
        free(outputs[p].path);
        if (tasks) {
            if (tasks[p].job.diagnostics && tasks[p].result.diagnostics != tasks[p].job.diagnostics)
                fclose(tasks[p].job.diagnostics);
            direct_result_free(&tasks[p].result);
        }
    }
    free(tasks);
    free(outputs);
    free(threads);
    partition_plan_destroy(plan);
}

/* Returns the exit status, or DIRECT_RERUN when the serial path must run (or P_eff is 1). */
int direct_run_partitioned(const Options *options) {
    PartitionPlan plan;
    if (!partition_plan_build(options, &plan)) return DIRECT_RERUN;
    unsigned parts = plan.parts;
    PartitionTask *tasks = calloc(parts, sizeof(*tasks));
    OutputFile *outputs = calloc(parts, sizeof(*outputs));
    lm_thread *threads = calloc(parts - 1u, sizeof(*threads));
    unsigned prepared = 0;
    atomic_bool abort_flag;
    atomic_init(&abort_flag, 0);
    if (!tasks || !outputs || !threads) {
        partition_cleanup(tasks, outputs, threads, &plan, 0);
        return DIRECT_RERUN;
    }
    for (; prepared < parts; ++prepared) {
        outputs[prepared].handle = LM_INVALID_HANDLE;
        /* tmpfile() must not run concurrently: on macOS it blocks signals around mkstemp, and racing
         * calls leave INT/TERM/HUP blocked for good, so interrupts are never delivered. */
        FILE *diagnostics = tmpfile();
        if (!diagnostics) {
            partition_cleanup(tasks, outputs, threads, &plan, prepared);
            return DIRECT_RERUN;
        }
        if (create_output_temp_quiet(options->output, &outputs[prepared]) != 0) {
            fclose(diagnostics);
            partition_cleanup(tasks, outputs, threads, &plan, prepared);
            return DIRECT_RERUN;
        }
        tasks[prepared].job.options = options;
        tasks[prepared].job.plan = &plan;
        tasks[prepared].job.part = prepared;
        tasks[prepared].job.prepared_output = &outputs[prepared];
        tasks[prepared].job.abort_flag = &abort_flag;
        tasks[prepared].job.diagnostics = diagnostics;
    }
    unsigned started = 0;
    int thread_failed = 0;
    for (unsigned p = 1; p < parts; ++p) {
        if (lm_thread_start(&threads[p - 1u], partition_thread, &tasks[p]) != 0) {
            thread_failed = 1;
            atomic_store_explicit(&abort_flag, 1, memory_order_relaxed);
            break;
        }
        ++started;
    }
    if (!thread_failed) run_partition(&tasks[0]);
    for (unsigned i = 0; i < started; ++i)
        if (lm_thread_join(threads[i]) != 0) thread_failed = 1;
    if (thread_failed) {
        /* Results of unjoined or never-started partitions are unusable; started ones were joined. */
        partition_cleanup(tasks, outputs, threads, &plan, prepared);
        return DIRECT_RERUN;
    }

    if (interrupted) {
        partition_cleanup(tasks, outputs, threads, &plan, prepared);
        fprintf(stderr, "lcovmerge: interrupted\n");
        return 3;
    }
    int usable = 1;
    for (unsigned p = 0; p < parts; ++p) {
        const DirectResult *result = &tasks[p].result;
        if (result->fallback || result->status != 0 || result->had_diagnostics) usable = 0;
    }
    /* Whole-file memory decision: fixed cost plus, per input, the largest dynamic need of any range. */
    if (usable) {
        size_t memory_limit = options->mem_limit / 2u;
        if (memory_limit > ((size_t)16u << 20)) memory_limit = (size_t)16u << 20;
        uint64_t memory_used = tasks[0].result.fixed_cost;
        for (size_t i = 0; i < options->inputs.count; ++i) {
            size_t largest = 0;
            for (unsigned p = 0; p < parts; ++p)
                if (tasks[p].result.growth[i] > largest) largest = tasks[p].result.growth[i];
            memory_used = saturating_add(memory_used, (uint64_t)largest);
        }
        if (memory_used > memory_limit) usable = 0;
    }
    if (usable) {
        /* Close (and thereby flush) the later staged files; the first stays open as the target. */
        for (unsigned p = 1; p < parts; ++p) {
            if (lm_close(outputs[p].handle) != 0) usable = 0;
            outputs[p].handle = LM_INVALID_HANDLE;
            outputs[p].owns_handle = 0;
        }
        unsigned char *buffer = usable ? malloc(WRITE_BLOCK) : NULL;
        if (usable && !buffer) usable = 0;
        for (unsigned p = 1; p < parts && usable; ++p) {
            if (append_file(outputs[0].handle, outputs[p].path, buffer, WRITE_BLOCK) != 0 ||
                lm_remove(outputs[p].path) != 0) usable = 0;
            else { free(outputs[p].path); outputs[p].path = NULL; }
        }
        free(buffer);
        if (usable && lm_close(outputs[0].handle) != 0) usable = 0;
        outputs[0].handle = LM_INVALID_HANDLE;
        outputs[0].owns_handle = 0;
    }
    if (interrupted) {
        partition_cleanup(tasks, outputs, threads, &plan, prepared);
        fprintf(stderr, "lcovmerge: interrupted\n");
        return 3;
    }
    if (!usable) {
        partition_cleanup(tasks, outputs, threads, &plan, prepared);
        return DIRECT_RERUN;
    }

    int status = 0;
    if (lm_rename(outputs[0].path, options->output) != 0) {
        fprintf(stderr, "lcovmerge: %s:0: cannot replace output\n", options->output);
        status = 3;
    } else {
        free(outputs[0].path);
        outputs[0].path = NULL;
    }
    uint64_t records = 0, lf = 0, lh = 0, fnf = 0, fnh = 0, brf = 0, brh = 0;
    for (unsigned p = 0; p < parts; ++p) {
        const DirectResult *result = &tasks[p].result;
        records = saturating_add(records, result->records);
        lf = saturating_add(lf, result->total_lf);
        lh = saturating_add(lh, result->total_lh);
        fnf = saturating_add(fnf, result->total_fnf);
        fnh = saturating_add(fnh, result->total_fnh);
        brf = saturating_add(brf, result->total_brf);
        brh = saturating_add(brh, result->total_brh);
    }
    uint64_t bytes = plan.total_bytes;
    partition_cleanup(tasks, outputs, threads, &plan, prepared);
    if (status == 0 && options->verbose && !options->quiet)
        fprintf(stderr, "lcovmerge: parsed %" PRIu64 " input bytes into %" PRIu64 " records using %u jobs (sorted-input fast path)\n",
                bytes, records, parts);
    if (status == 0 && (options->stats || (options->verbose && !options->quiet)))
        fprintf(stderr, "lcovmerge: stats LF=%" PRIu64 " LH=%" PRIu64
                " FNF=%" PRIu64 " FNH=%" PRIu64 " BRF=%" PRIu64 " BRH=%" PRIu64 "\n",
                lf, lh, fnf, fnh, brf, brh);
    return status;
}
