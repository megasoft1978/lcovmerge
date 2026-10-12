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
