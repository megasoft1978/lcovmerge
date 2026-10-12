#include "lm_internal.h"

/*
 * Partition finder for the threaded sorted-input direct path (see docs/ARCHITECTURE.md).
 * It locates, per input, byte offsets of "kept SF lines" (lines the parser would turn into a
 * section after rewrite_path and path_selected) by positional reads only. Anything odd makes the
 * caller fall back to the unchanged single-threaded path, which reproduces today's diagnostics.
 */

#ifdef LM_TEST_PARTITION
#include <stdarg.h>
void partition_trace(const char *format, ...) {
    const char *path = getenv("LCOVMERGE_TEST_PARTITION_TRACE");
    if (!path || !*path) return;
    FILE *file = fopen(path, "a");
    if (!file) return;
    va_list arguments;
    va_start(arguments, format);
    (void)vfprintf(file, format, arguments);
    va_end(arguments);
    (void)fclose(file);
}
#endif

#define PART_SAMPLES_PER_PART 128u
#define PART_MIN_PARTITION_BYTES ((uint64_t)32u << 20)
#define PART_FINDER_CAP ((uint64_t)1u << 30)
#define PART_FINDER_DIVISOR 2u
#define PART_JOINED_PREFIX_LENGTH 13u

int part_scanner_init(PartScanner *scanner) {
    memset(scanner, 0, sizeof(*scanner));
    scanner->chunk = malloc(PART_CHUNK + PART_LOOKBEHIND);
    scanner->line_capacity = LINE_INITIAL_CAPACITY;
    scanner->line = malloc(scanner->line_capacity);
    scanner->probe_limit = MAX_LINE;
    scanner->linear_threshold = (uint64_t)64u << 10;
    scanner->tn_window = (uint64_t)64u << 10;
    if (!scanner->chunk || !scanner->line) {
        part_scanner_destroy(scanner);
        return -1;
    }
    return 0;
}

void part_scanner_destroy(PartScanner *scanner) {
    free(scanner->chunk);
    free(scanner->line);
    memset(scanner, 0, sizeof(*scanner));
}

static int pread_full(lm_handle handle, void *buffer, size_t length, uint64_t offset,
                      size_t *got_out) {
    unsigned char *bytes = buffer;
    size_t total = 0;
    while (total < length) {
        size_t got = 0;
        if (lm_pread(handle, bytes + total, length - total, offset + total, &got,
                     &interrupted) != 0) return -1;
        if (got == 0) break;
        total += got;
    }
    *got_out = total;
    return 0;
}

static int charge(PartScanner *scanner, uint64_t bytes) {
    scanner->used = saturating_add(scanner->used, bytes);
    scanner->spent = saturating_add(scanner->spent, bytes);
    if (scanner->used > scanner->probe_limit) return -1;
    if (scanner->budget != 0 && scanner->spent > scanner->budget) return -1;
    return 0;
}

/* Reads the line starting at `start` (without its newline) into scanner->line. */
static int read_line(PartScanner *scanner, lm_handle handle, uint64_t size, uint64_t start,
                     size_t *length_out) {
    size_t length = 0;
    uint64_t position = start;
    unsigned char piece[4096];
    while (position < size) {
        size_t got = 0;
        if (pread_full(handle, piece, sizeof(piece), position, &got) != 0) return -1;
        if (got == 0) break;
        unsigned char *newline = memchr(piece, '\n', got);
        size_t take = newline ? (size_t)(newline - piece) : got;
        if (take > MAX_LINE - length) return -1;
        if (length + take + 1u > scanner->line_capacity) {
            size_t capacity = scanner->line_capacity;
            while (capacity < length + take + 1u) capacity *= 2u;
            char *grown = realloc(scanner->line, capacity);
            if (!grown) return -1;
            scanner->line = grown;
            scanner->line_capacity = capacity;
        }
        memcpy(scanner->line + length, piece, take);
        length += take;
        if (newline) break;
        position += got;
    }
    scanner->line[length] = '\0';
    if (charge(scanner, (uint64_t)length + 1u) != 0) return -1;
    *length_out = length;
    return 0;
}

/* 1 kept SF line (rewritten path in *path), 0 not a kept SF line, -1 give up. */
static int classify_line(const Options *options, char *line, size_t length, char **path,
                         int *joined) {
    if (length != 0 && line[length - 1u] == '\r') line[--length] = '\0';
    const char *source;
    *joined = 0;
    static const char prefix[] = "end_of_record";
    if (length >= PART_JOINED_PREFIX_LENGTH + 3u &&
        memcmp(line, prefix, PART_JOINED_PREFIX_LENGTH) == 0 &&
        (memcmp(line + PART_JOINED_PREFIX_LENGTH, "SF:", 3u) == 0 ||
         memcmp(line + PART_JOINED_PREFIX_LENGTH, "KF:", 3u) == 0)) {
        *joined = 1;
        source = line + PART_JOINED_PREFIX_LENGTH + 3u;
    } else if (length >= 3u && (memcmp(line, "SF:", 3u) == 0 || memcmp(line, "KF:", 3u) == 0)) {
        source = line + 3u;
    } else return 0;
    if (*source == '\0') return -1;
    char *rewritten = rewrite_path(options, source);
    if (!rewritten) return -1;
    if (*rewritten == '\0') { free(rewritten); return -1; }
    if (!path_selected(options, rewritten)) { free(rewritten); return 0; }
    *path = rewritten;
    return 1;
}

int part_find_kept_sf(PartScanner *scanner, const Options *options, lm_handle handle,
                      uint64_t size, uint64_t from, uint64_t limit, const char *splitter,
                      size_t splitter_length, uint64_t *start_out, char **path_out,
                      int *joined_out) {
    static const char prefix[] = "end_of_record";
    if (limit > size) limit = size;
    scanner->used = 0;
    uint64_t position = from;
    while (position < limit) {
        if (atomic_load_explicit(&interrupted, memory_order_relaxed)) return -1;
        uint64_t window = position >= PART_LOOKBEHIND ? position - PART_LOOKBEHIND : 0;
        size_t skip = (size_t)(position - window);
        size_t got = 0;
        uint64_t needed = limit - position + 32u;  /* joined form reaches 13 bytes past limit */
        size_t request = needed < PART_CHUNK ? (size_t)needed : PART_CHUNK;
        if (pread_full(handle, scanner->chunk, request + skip, window, &got) != 0) return -1;
        if (got <= skip) return 0;
        int at_end = window + got >= size;
        size_t stop = got;
        if (!at_end) stop -= 2u;  /* a candidate needs "F:" after the S/K */
        /* The joined form starts 13 bytes before its S; its line start must be below limit. */
        if (limit + PART_JOINED_PREFIX_LENGTH < window + stop)
            stop = (size_t)(limit + PART_JOINED_PREFIX_LENGTH - window);
        if (stop <= skip) {
            if (at_end) return 0;
            position = window + skip + 1u;  /* cannot happen with a 64 KiB chunk */
            continue;
        }
        size_t index = skip;
        unsigned char *next_s = memchr(scanner->chunk + index, 'S', stop - index);
        unsigned char *next_k = memchr(scanner->chunk + index, 'K', stop - index);
        while (next_s || next_k) {
            unsigned char *candidate = !next_k || (next_s && next_s < next_k) ? next_s : next_k;
            size_t at = (size_t)(candidate - scanner->chunk);
            uint64_t line_start = UINT64_MAX;
            if (at + 2u < got && candidate[1] == 'F' && candidate[2] == ':') {
                uint64_t offset = window + at;
                if (offset == 0 || candidate[-1] == '\n') line_start = offset;
                else if (offset >= PART_JOINED_PREFIX_LENGTH && at >= PART_JOINED_PREFIX_LENGTH &&
                         memcmp(candidate - PART_JOINED_PREFIX_LENGTH, prefix,
                                PART_JOINED_PREFIX_LENGTH) == 0 &&
                         (offset == PART_JOINED_PREFIX_LENGTH ||
                          candidate[-(ptrdiff_t)PART_JOINED_PREFIX_LENGTH - 1] == '\n'))
                    line_start = offset - PART_JOINED_PREFIX_LENGTH;
            }
            if (line_start != UINT64_MAX && line_start >= from && line_start < limit) {
                size_t length = 0;
                if (read_line(scanner, handle, size, line_start, &length) != 0) return -1;
                char *path = NULL;
                int joined = 0;
                int kind = classify_line(options, scanner->line, length, &path, &joined);
                if (kind < 0) return -1;
                if (kind > 0) {
                    if (!splitter ||
                        bytes_compare(path, strlen(path), splitter, splitter_length) >= 0) {
                        if (charge(scanner, (uint64_t)(at - skip)) != 0) { free(path); return -1; }
                        *start_out = line_start;
                        *path_out = path;
                        *joined_out = joined;
                        return 1;
                    }
                    free(path);
                }
            }
            index = at + 1u;
            if (next_s && next_s <= candidate)
                next_s = index < stop ? memchr(scanner->chunk + index, 'S', stop - index) : NULL;
            if (next_k && next_k <= candidate)
                next_k = index < stop ? memchr(scanner->chunk + index, 'K', stop - index) : NULL;
        }
        if (charge(scanner, (uint64_t)(stop - skip)) != 0) return -1;
        position = window + stop;
        if (at_end) break;
    }
    return 0;
}

/* One attempt with a given window; -2 when the previous lines do not fit in it. */
static int pending_tn_window(PartScanner *scanner, lm_handle handle, uint64_t at,
                             uint64_t window_limit, char **tn_out) {
    size_t window = at < window_limit ? (size_t)at : (size_t)window_limit;
    unsigned char *buffer = malloc(window);
    if (!buffer) return -1;
    size_t got = 0;
    if (pread_full(handle, buffer, window, at - window, &got) != 0 || got != window ||
        charge(scanner, (uint64_t)window) != 0) {
        free(buffer);
        return -1;
    }
    int status = 0;
    size_t end = window;  /* buffer[end - 1] is the newline that ends the previous line */
    if (buffer[end - 1u] != '\n') { free(buffer); return -1; }
    end -= 1u;
    for (;;) {
        size_t begin = end;
        while (begin > 0 && buffer[begin - 1u] != '\n') --begin;
        if (begin == 0 && window < at) { status = -2; break; }  /* line may start earlier */
        size_t length = end - begin;
        const char *line = (const char *)buffer + begin;
        if (length != 0 && line[length - 1u] == '\r') --length;
        if (length != 0 && line[0] != '#') {
            if (length >= 3u && memcmp(line, "TN:", 3u) == 0) {
                *tn_out = duplicate_range(line + 3u, length - 3u);
                if (!*tn_out) status = -1;
            }
            break;  /* end_of_record or anything else: no pending TN */
        }
        if (begin == 0) break;
        end = begin - 1u;
    }
    free(buffer);
    return status;
}

/* TN text on the last non-blank, non-comment line before `at` (a line start), if any. */
static int pending_tn_before(PartScanner *scanner, lm_handle handle, uint64_t at, int joined,
                             char **tn_out) {
    *tn_out = NULL;
    if (joined || at == 0) return 0;
    if (scanner->tn_window > 1024u) {
        int status = pending_tn_window(scanner, handle, at, 1024u, tn_out);
        if (status != -2) return status;
    }
    return pending_tn_window(scanner, handle, at, scanner->tn_window, tn_out);
}

int part_locate(PartScanner *scanner, const Options *options, lm_handle handle, uint64_t size,
                const char *splitter, size_t splitter_length, uint64_t lo,
                uint64_t *offset_out, char **pending_tn_out) {
    *offset_out = size;
    *pending_tn_out = NULL;
    uint64_t best = size, hi = size, start = 0;
    int best_joined = 0, joined = 0;
    char *path = NULL;
    if (lo > hi) lo = hi;
    while (hi - lo > scanner->linear_threshold) {
        uint64_t middle = lo + (hi - lo) / 2u;
        int found = part_find_kept_sf(scanner, options, handle, size, middle, hi, NULL, 0,
                                      &start, &path, &joined);
        if (found < 0) return -1;
        if (found == 0) { hi = middle; continue; }
        int order = bytes_compare(path, strlen(path), splitter, splitter_length);
        free(path);
        if (order >= 0) { best = start; best_joined = joined; hi = middle; }
        else lo = start + 1u;
    }
    if (lo < hi) {
        int found = part_find_kept_sf(scanner, options, handle, size, lo, hi, splitter,
                                      splitter_length, &start, &path, &joined);
        if (found < 0) return -1;
        if (found > 0) { free(path); best = start; best_joined = joined; }
    }
    if (best == size) return 0;
    int status = pending_tn_before(scanner, handle, best, best_joined, pending_tn_out);
    if (status != 0) return status;
    *offset_out = best;
    return 0;
}

static int sample_compare(const void *left, const void *right) {
    const char *const *a = left;
    const char *const *b = right;
    return bytes_compare(*a, strlen(*a), *b, strlen(*b));
}

void partition_plan_destroy(PartitionPlan *plan) {
    if (plan->points)
        for (size_t i = 0; i < plan->input_count * plan->stride; ++i) free(plan->points[i].pending_tn);
    if (plan->splitters)
        for (unsigned i = 0; i + 1u < plan->parts; ++i) free(plan->splitters[i]);
    if (plan->handles)
        for (size_t i = 0; i < plan->input_count; ++i)
            if (plan->handles[i] != LM_INVALID_HANDLE) (void)lm_close(plan->handles[i]);
    free(plan->points);
    free(plan->splitters);
    free(plan->handles);
    free(plan->sizes);
    memset(plan, 0, sizeof(*plan));
}

/* Evenly spaced probes over the concatenated inputs; quantiles of the sampled paths. */
static int choose_splitters(PartScanner *scanner, const Options *options,
                            const PartitionPlan *plan, unsigned want, char ***out,
                            size_t *out_count) {
    uint64_t sample_limit = (uint64_t)want * PART_SAMPLES_PER_PART;
    if (sample_limit > plan->total_bytes / 2u) sample_limit = plan->total_bytes / 2u;  /* tiny test inputs */
    if (sample_limit < (uint64_t)want) return -1;
    size_t sample_target = (size_t)sample_limit;
    char **samples = calloc(sample_target, sizeof(*samples));
    if (!samples) return -1;
    size_t taken = 0;
    uint64_t step = plan->total_bytes / sample_target;
    int status = 0;
    size_t input = 0;
    uint64_t base = 0;
    for (size_t k = 0; k < sample_target && status == 0; ++k) {
        uint64_t global = step * k + step / 2u;
        while (input < plan->input_count && global >= base + plan->sizes[input]) {
            base += plan->sizes[input];
            ++input;
        }
        if (input >= plan->input_count) break;
        uint64_t start;
        int joined;
        char *path = NULL;
        int found = part_find_kept_sf(scanner, options, plan->handles[input], plan->sizes[input],
                                      global - base, plan->sizes[input], NULL, 0, &start, &path,
                                      &joined);
        if (found < 0) status = -1;
        else if (found > 0) samples[taken++] = path;
    }
    char **chosen = status == 0 ? calloc((size_t)want, sizeof(*chosen)) : NULL;
    size_t chosen_count = 0;
    if (status == 0 && !chosen) status = -1;
    if (status == 0 && taken != 0) {
        qsort(samples, taken, sizeof(*samples), sample_compare);
        for (unsigned j = 1; j < want; ++j) {
            const char *candidate = samples[(size_t)j * taken / want];
            if (chosen_count != 0 &&
                bytes_compare(candidate, strlen(candidate), chosen[chosen_count - 1u],
                              strlen(chosen[chosen_count - 1u])) <= 0) continue;
            chosen[chosen_count] = duplicate_string(candidate);
            if (!chosen[chosen_count]) { status = -1; break; }
            ++chosen_count;
        }
    }
    for (size_t k = 0; k < taken; ++k) free(samples[k]);
    free(samples);
    if (status != 0) {
        for (size_t k = 0; k < chosen_count; ++k) free(chosen[k]);
        free(chosen);
        return -1;
    }
    *out = chosen;
    *out_count = chosen_count;
    return 0;
}

static int partition_forced(unsigned *want) {
#ifdef LM_TEST_PARTITION
    /* Test builds only (see Makefile ptest targets): force P regardless of -j and size limits. */
    const char *value = getenv("LCOVMERGE_TEST_PARTITION");
    uint64_t parsed;
    if (value && parse_u64(value, strlen(value), &parsed) && parsed >= 2u && parsed <= MAX_JOBS) {
        *want = (unsigned)parsed;
        return 1;
    }
#else
    (void)want;
#endif
    return 0;
}

int partition_plan_build(const Options *options, PartitionPlan *plan) {
    memset(plan, 0, sizeof(*plan));
    unsigned want = 0;
    int forced = partition_forced(&want);
    if (!forced) {
        if (!options->jobs_explicit || options->jobs < 2u) return 0;
        want = options->jobs;
    }
    size_t count = options->inputs.count;
    if (want > MAX_JOBS) want = MAX_JOBS;
    unsigned descriptor_limit = lm_max_open_files();
    if (count == 0 || descriptor_limit <= 8u || count > (size_t)(descriptor_limit - 8u)) return 0;
    /* N shared inputs + one staged output and one diagnostics file per partition + slack. */
    unsigned fd_parts = (unsigned)(((size_t)(descriptor_limit - 8u) - count) / 2u);
    if (want > fd_parts) want = fd_parts;
    if (want < 2u) return 0;
    size_t part_cost = direct_fixed_cost(count);
    /* The aggregate buffer is reserved for 3 MiB but touched only as far as records grow. */
    if (part_cost == SIZE_MAX || part_cost > SIZE_MAX - MAX_LINE) return 0;
    part_cost += MAX_LINE;
    size_t memory_parts = options->mem_limit / 2u / part_cost;
    if ((size_t)want > memory_parts) want = (unsigned)memory_parts;
    if (want < 2u) return 0;
    if (count > SIZE_MAX / sizeof(*plan->handles) || count > SIZE_MAX / sizeof(*plan->sizes))
        return 0;
    plan->input_count = count;
    plan->handles = malloc(count * sizeof(*plan->handles));
    plan->sizes = calloc(count, sizeof(*plan->sizes));
    if (!plan->handles || !plan->sizes) { free(plan->handles); free(plan->sizes); memset(plan, 0, sizeof(*plan)); return 0; }
    for (size_t i = 0; i < count; ++i) plan->handles[i] = LM_INVALID_HANDLE;
    for (size_t i = 0; i < count; ++i) {
        if (lm_open_read(options->inputs.items[i], &plan->handles[i]) != 0 ||
            lm_file_size(plan->handles[i], &plan->sizes[i]) != 0) goto fail;
        plan->total_bytes = saturating_add(plan->total_bytes, plan->sizes[i]);
    }
    if (!forced) {
        uint64_t by_size = plan->total_bytes / PART_MIN_PARTITION_BYTES;
        if (by_size < want) want = (unsigned)by_size;
    }
    if (want < 2u) goto fail;

    PartScanner scanner;
    if (part_scanner_init(&scanner) != 0) goto fail;
    if (!forced) {
        uint64_t budget = plan->total_bytes / PART_FINDER_DIVISOR;
        scanner.budget = budget < PART_FINDER_CAP ? budget : PART_FINDER_CAP;
    }
#ifdef LM_TEST_PARTITION
    {
        const char *linear = getenv("LCOVMERGE_TEST_PARTITION_LINEAR");
        uint64_t parsed;
        if (linear && parse_u64(linear, strlen(linear), &parsed)) scanner.linear_threshold = parsed;
    }
#endif
    char **candidates = NULL;
    size_t candidate_count = 0;
    if (choose_splitters(&scanner, options, plan, want, &candidates, &candidate_count) != 0 ||
        candidate_count == 0) {
        part_scanner_destroy(&scanner);
        goto fail;
    }
    size_t stride = (size_t)want + 1u;
    if (count > SIZE_MAX / stride / sizeof(*plan->points)) {
        for (size_t k = 0; k < candidate_count; ++k) free(candidates[k]);
        free(candidates);
        part_scanner_destroy(&scanner);
        goto fail;
    }
    plan->stride = stride;
    plan->points = calloc(count * stride, sizeof(*plan->points));
    plan->splitters = calloc((size_t)want, sizeof(*plan->splitters));
    if (!plan->points || !plan->splitters) {
        for (size_t k = 0; k < candidate_count; ++k) free(candidates[k]);
        free(candidates);
        part_scanner_destroy(&scanner);
        goto fail;
    }
    unsigned accepted = 0;
    int give_up = 0;
    for (size_t k = 0; k < candidate_count && !give_up; ++k) {
        size_t column = (size_t)accepted + 1u;
        size_t done = 0;
        int dropped = 0;
        for (; done < count; ++done) {
            PartPoint *point = &plan->points[done * stride + column];
            int status = part_locate(&scanner, options, plan->handles[done], plan->sizes[done],
                                     candidates[k], strlen(candidates[k]),
                                     plan->points[done * stride + accepted].offset,
                                     &point->offset, &point->pending_tn);
            if (status == -2) { dropped = 1; break; }
            if (status != 0) { give_up = 1; break; }
        }
        if (dropped || give_up) {
            for (size_t i = 0; i <= done && i < count; ++i) {
                free(plan->points[i * stride + column].pending_tn);
                plan->points[i * stride + column].pending_tn = NULL;
            }
        }
        if (dropped || give_up) continue;
        plan->splitters[accepted++] = candidates[k];
        candidates[k] = NULL;
    }
    for (size_t k = 0; k < candidate_count; ++k) free(candidates[k]);
    free(candidates);
    part_scanner_destroy(&scanner);
    plan->parts = accepted + 1u;
    if (give_up || accepted == 0) goto fail;
    for (size_t i = 0; i < count; ++i) plan->points[i * stride + plan->parts].offset = plan->sizes[i];
    return 1;
fail:
    partition_plan_destroy(plan);
    return 0;
}
