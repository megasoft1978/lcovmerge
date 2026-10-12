#define _POSIX_C_SOURCE 200809L

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../../src/lm_internal.h"

static unsigned assertions;

static const char *scratch_directory(void) {
    static const char *const names[] = {"TMPDIR", "TMP", "TEMP"};
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); ++i) {
        const char *value = getenv(names[i]);
        if (value && value[0]) return value;
    }
#ifdef _WIN32
    return ".";
#else
    return "/tmp";
#endif
}


static int check_at(int condition, const char *expression, int line) {
    ++assertions;
    if (!condition) {
        fprintf(stderr, "unit failure at line %d: %s\n", line, expression);
        return 0;
    }
    return 1;
}

#define CHECK(expression) do { \
    if (!check_at((expression), #expression, __LINE__)) return -1; \
} while (0)

#define CHECK_CLEANUP(expression) do { \
    if (!check_at((expression), #expression, __LINE__)) { result = -1; goto cleanup; } \
} while (0)

static int test_saturating_add(void) {
    CHECK(saturating_add(0, 0) == 0);
    CHECK(saturating_add(1, 2) == 3);
    CHECK(saturating_add(UINT64_MAX, 0) == UINT64_MAX);
    CHECK(saturating_add(UINT64_MAX - 1u, 1) == UINT64_MAX);
    CHECK(saturating_add(UINT64_MAX, 1) == UINT64_MAX);
    CHECK(saturating_add(UINT64_MAX - 4u, 5) == UINT64_MAX);
    return 0;
}

static int check_writer_u64(Writer *writer, uint64_t value) {
    char expected[32];
    int expected_length = snprintf(expected, sizeof(expected), "%" PRIu64, value);
    CHECK(expected_length > 0);
    writer->used = 0;
    CHECK(writer_u64(writer, value) == 0);
    CHECK(writer->used == (size_t)expected_length);
    CHECK(memcmp(writer->buffer, expected, (size_t)expected_length) == 0);
    return 0;
}

static int test_writer_u64(void) {
    Writer writer;
    CHECK(writer_init(&writer, LM_INVALID_HANDLE) == 0);
    int result = 0;
    for (uint64_t value = 0; value < UINT64_C(10000); ++value) {
        if (check_writer_u64(&writer, value) != 0) { result = -1; goto cleanup; }
    }
    const uint64_t edge_values[] = {
        0, 9, 10, 99, 100, 999, 1000, 9999, 10000,
        UINT64_C(1000000000000000000), UINT64_MAX - 100u,
        UINT64_MAX - 10u, UINT64_MAX - 1u, UINT64_MAX
    };
    for (size_t i = 0; i < sizeof(edge_values) / sizeof(edge_values[0]); ++i) {
        if (check_writer_u64(&writer, edge_values[i]) != 0) { result = -1; goto cleanup; }
    }
cleanup:
    writer_destroy(&writer);
    return result;
}

static int test_parse_size(void) {
    static const struct {
        const char *text;
        size_t value;
    } valid[] = {
        {"0", 0u}, {"1", 1u}, {"8M", (size_t)8u * 1024u * 1024u},
        {"2k", (size_t)2u * 1024u}, {"3G", (size_t)3u * 1024u * 1024u * 1024u},
        {"5m", (size_t)5u * 1024u * 1024u}
    };
    static const char *const invalid[] = {
        "", "k", "1T", "-1", "+1", " 1", "1 ",
        "18446744073709551616", "18446744073709551615K"
    };
    for (size_t i = 0; i < sizeof(valid) / sizeof(valid[0]); ++i) {
        size_t value = SIZE_MAX;
        CHECK(parse_size(valid[i].text, &value) == 1);
        CHECK(value == valid[i].value);
    }
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
        size_t value = 17u;
        CHECK(parse_size(invalid[i], &value) == 0);
        CHECK(value == 17u);
    }
#if SIZE_MAX == UINT64_MAX
    {
        size_t value = 0;
        CHECK(parse_size("18446744073709551615", &value) == 1);
        CHECK(value == SIZE_MAX);
    }
#endif
    return 0;
}

static Record make_record(uint8_t type, const char *path, const char *text,
                          const char *extra, uint64_t a, uint64_t b,
                          uint64_t c, uint64_t value, uint8_t flags) {
    Record record;
    memset(&record, 0, sizeof(record));
    record.path = (char *)path;
    record.text = (char *)text;
    record.extra = (char *)extra;
    record.path_length = (uint32_t)strlen(path);
    record.text_length = (uint32_t)strlen(text);
    record.extra_length = (uint32_t)strlen(extra);
    record.type = type;
    record.a = a;
    record.b = b;
    record.c = c;
    record.value = value;
    record.flags = flags;
    return record;
}

static int sign_of(int value) {
    return (value > 0) - (value < 0);
}

static uint32_t random_u32(uint32_t *state) {
    *state = *state * UINT32_C(1664525) + UINT32_C(1013904223);
    return *state;
}

static uint64_t random_u64(uint32_t *state, size_t index) {
    switch (index % 8u) {
        case 0: return 0;
        case 1: return 1;
        case 2: return UINT64_MAX;
        case 3: return UINT64_MAX - 1u;
        default:
            return ((uint64_t)random_u32(state) << 32) | (uint64_t)random_u32(state);
    }
}

static int check_comparator_laws(const Record *records, size_t count, int same_path) {
    for (size_t i = 0; i < count; ++i) {
        for (size_t j = 0; j < count; ++j) {
            int forward = same_path ? record_total_compare_same_path(&records[i], &records[j]) :
                                     record_total_compare(&records[i], &records[j]);
            int reverse = same_path ? record_total_compare_same_path(&records[j], &records[i]) :
                                     record_total_compare(&records[j], &records[i]);
            CHECK(sign_of(forward) == -sign_of(reverse));
            CHECK(sign_of(record_key_compare(&records[i], &records[j])) ==
                  -sign_of(record_key_compare(&records[j], &records[i])));
        }
    }
    for (size_t i = 0; i < count; ++i) {
        for (size_t j = 0; j < count; ++j) {
            int ij = same_path ? record_total_compare_same_path(&records[i], &records[j]) :
                                 record_total_compare(&records[i], &records[j]);
            int key_ij = record_key_compare(&records[i], &records[j]);
            for (size_t k = 0; k < count; ++k) {
                int jk = same_path ? record_total_compare_same_path(&records[j], &records[k]) :
                                     record_total_compare(&records[j], &records[k]);
                int ik = same_path ? record_total_compare_same_path(&records[i], &records[k]) :
                                     record_total_compare(&records[i], &records[k]);
                int key_jk = record_key_compare(&records[j], &records[k]);
                int key_ik = record_key_compare(&records[i], &records[k]);
                if (ij <= 0 && jk <= 0) CHECK(ik <= 0);
                if (key_ij <= 0 && key_jk <= 0) CHECK(key_ik <= 0);
            }
        }
    }
    return 0;
}

static int test_record_comparators(void) {
    Record left = make_record(REC_DA, "/a", "", "", 99, 0, 0, 0, 0);
    Record right = make_record(REC_DA, "/b", "", "", 1, 0, 0, 0, 0);
    CHECK(record_total_compare(&left, &right) < 0);

    Record classes[] = {
        make_record(REC_TN, "/same", "", "", 0, 0, 0, 0, 0),
        make_record(REC_SECTION, "/same", "", "", 0, 0, 0, 0, 0),
        make_record(REC_GROUP, "/same", "", "", 1, 0, 0, 0, FLAG_FN_NEW),
        make_record(REC_GROUP, "/same", "", "", 1, 0, 0, 0, 0),
        make_record(REC_FNDA, "/same", "f", "f", 0, 0, 0, 1, 0),
        make_record(REC_DA, "/same", "", "", 1, 0, 0, 1, 0),
        make_record(REC_BRDA, "/same", "0", "", 1, 0, 0, 1, 0),
        make_record(REC_MCDC, "/same", "term", "", 1, 0, 0, 1, 0),
        make_record(REC_EXT, "/same", "X:row", "", 0, 0, 0, 0, 0)
    };
    for (size_t i = 1; i < sizeof(classes) / sizeof(classes[0]); ++i)
        CHECK(record_total_compare(&classes[i - 1u], &classes[i]) < 0);

    left = make_record(REC_DA, "/same", "", "", 2, 0, 0, 100, 0);
    right = make_record(REC_DA, "/same", "", "", 10, 0, 0, 0, 0);
    CHECK(record_key_compare(&left, &right) < 0);
    left = make_record(REC_BRDA, "/same", "1", "", 4, 0, 0, 0, 0);
    right = make_record(REC_BRDA, "/same", "0", "", 4, 1, 0, 0, 0);
    CHECK(record_key_compare(&left, &right) < 0);
    left = make_record(REC_EXT, "/same", "A:row", "", 0, 0, 0, UINT64_MAX, 0);
    right = make_record(REC_EXT, "/same", "B:row", "", 0, 0, 0, 0, 0);
    CHECK(record_key_compare(&left, &right) < 0);
    left = make_record(REC_FNDA, "/same", "f", "f", 0, 0, 0, 1, 0);
    right = make_record(REC_FNDA, "/same", "f", "f", 0, 0, 0, 2, 0);
    CHECK(record_total_compare(&left, &right) < 0);
    left = make_record(REC_FN, "/same", "alpha", "alpha", 2, 4, 0, 0, FLAG_FN_NEW);
    right = make_record(REC_FN, "/same", "beta", "beta", 2, 5, 0, 0, FLAG_FN_NEW);
    CHECK(record_key_compare(&left, &right) < 0);

    static const char *const paths[] = {"/a", "/b", "/b", "/z/edge"};
    static const char *const texts[] = {"", "a", "aa", "2", "10", "z"};
    static const uint8_t types[] = {
        REC_TN, REC_SECTION, REC_GROUP, REC_FN, REC_FNDA,
        REC_BRDA, REC_MCDC, REC_DA, REC_EXT
    };
    Record randomized[96];
    Record same_path[96];
    uint32_t state = UINT32_C(0x4c434f56);
    for (size_t i = 0; i < sizeof(randomized) / sizeof(randomized[0]); ++i) {
        uint32_t path_index = random_u32(&state) % 4u;
        const char *text = texts[random_u32(&state) % 6u];
        const char *extra = texts[random_u32(&state) % 6u];
        uint8_t type = types[random_u32(&state) % (uint32_t)(sizeof(types) / sizeof(types[0]))];
        uint8_t flags = (uint8_t)(random_u32(&state) & UINT32_C(0xff));
        randomized[i] = make_record(type, paths[path_index], text, extra,
                                    random_u64(&state, i), random_u64(&state, i + 1u),
                                    random_u64(&state, i + 2u), random_u64(&state, i + 3u), flags);
        randomized[i].path_id = (uint64_t)path_index + 1u;
        randomized[i].input_id = random_u32(&state);
        randomized[i].origin_line = random_u64(&state, i + 8u);

        text = texts[random_u32(&state) % 6u];
        extra = texts[random_u32(&state) % 6u];
        type = types[random_u32(&state) % (uint32_t)(sizeof(types) / sizeof(types[0]))];
        flags = (uint8_t)(random_u32(&state) & UINT32_C(0xff));
        same_path[i] = make_record(type, "/one/path", text, extra,
                                   random_u64(&state, i + 4u), random_u64(&state, i + 5u),
                                   random_u64(&state, i + 6u), random_u64(&state, i + 7u), flags);
        same_path[i].input_id = random_u32(&state);
        same_path[i].origin_line = random_u64(&state, i + 9u);
    }
    CHECK(check_comparator_laws(randomized, sizeof(randomized) / sizeof(randomized[0]), 0) == 0);
    CHECK(check_comparator_laws(same_path, sizeof(same_path) / sizeof(same_path[0]), 1) == 0);
    return 0;
}

static int test_path_rewriting_and_globs(void) {
    Options options;
    memset(&options, 0, sizeof(options));
    CHECK(add_rebase(&options, "/work/project=/mapped/base") == 0);
    options.prefix_strip = duplicate_string("/mapped/base");
    CHECK(options.prefix_strip != NULL);

    char *rewritten = rewrite_path(&options, "/work/project/src/a.c");
    CHECK(rewritten != NULL);
    CHECK(strcmp(rewritten, "src/a.c") == 0);
    free(rewritten);
    rewritten = rewrite_path(&options, "/work/projectile/src/a.c");
    CHECK(rewritten != NULL);
    CHECK(strcmp(rewritten, "/work/projectile/src/a.c") == 0);
    free(rewritten);
    rewritten = rewrite_path(&options, "/work/project/");
    CHECK(rewritten != NULL);
    CHECK(strcmp(rewritten, "") == 0);
    free(rewritten);
    options_destroy(&options);

    memset(&options, 0, sizeof(options));
    CHECK(add_rebase(&options, "/old/root/=/new/root/") == 0);
    rewritten = rewrite_path(&options, "/old/root//src/a.c");
    CHECK(rewritten != NULL);
    CHECK(strcmp(rewritten, "/new/root/src/a.c") == 0);
    free(rewritten);
    options_destroy(&options);

    memset(&options, 0, sizeof(options));
    CHECK(add_rebase(&options, "/old\\root=C:\\new") == 0);
    rewritten = rewrite_path(&options, "/old\\root/src/a.c");
    CHECK(rewritten != NULL);
    CHECK(strcmp(rewritten, "C:\\new\\src\\a.c") == 0);
    free(rewritten);
    options.prefix_strip = duplicate_string("/work/project/");
    CHECK(options.prefix_strip != NULL);
    rewritten = rewrite_path(&options, "/work/project///src/a.c");
    CHECK(rewritten != NULL);
    CHECK(strcmp(rewritten, "src/a.c") == 0);
    free(rewritten);
    rewritten = rewrite_path(&options, "/work/projectile/src/a.c");
    CHECK(rewritten != NULL);
    CHECK(strcmp(rewritten, "/work/projectile/src/a.c") == 0);
    free(rewritten);
    options_destroy(&options);

    CHECK(wildcard_match("/work/project/[!x].c", "/work/project/a.c"));
    CHECK(!wildcard_match("/work/project/[!x].c", "/work/project/x.c"));
    CHECK(wildcard_match("/work/project/[a-c]?.c", "/work/project/b1.c"));
    CHECK(!wildcard_match("/work/project/[a-c]?.c", "/work/project/d1.c"));
    CHECK(wildcard_match("/work/project/dir/", "/work/project/dir/"));
    CHECK(!wildcard_match("/work/project/dir/", "/work/project/dir"));

    memset(&options, 0, sizeof(options));
    CHECK(list_add_string(&options.includes, "/work/project/[!x].c") == 0);
    CHECK(list_add_string(&options.excludes, "/work/project/private/*") == 0);
    CHECK(path_selected(&options, "/work/project/a.c"));
    CHECK(!path_selected(&options, "/work/project/x.c"));
    CHECK(!path_selected(&options, "/work/project/private/a.c"));
    options_destroy(&options);
    return 0;
}

static int test_utf8_validation(void) {
    static const unsigned char ascii[] = {'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'};
    static const unsigned char embedded_nul[] = {'a', 'b', 'c', 'd', 'e', 'f', 'g', 0};
    static const unsigned char valid_two[] = {0xc2u, 0xa2u};
    static const unsigned char valid_three[] = {0xe2u, 0x82u, 0xacu};
    static const unsigned char valid_four[] = {0xf0u, 0x9fu, 0x92u, 0xa9u};
    static const unsigned char invalid_continuation[] = {0x80u};
    static const unsigned char overlong_two[] = {0xc0u, 0xafu};
    static const unsigned char overlong_three[] = {0xe0u, 0x80u, 0x80u};
    static const unsigned char surrogate[] = {0xedu, 0xa0u, 0x80u};
    static const unsigned char too_large[] = {0xf4u, 0x90u, 0x80u, 0x80u};
    static const unsigned char invalid_lead[] = {0xf5u, 0x80u, 0x80u, 0x80u};
    static const unsigned char truncated_two[] = {0xc2u};
    static const unsigned char truncated_three[] = {0xe2u, 0x82u};
    static const unsigned char truncated_four[] = {0xf0u, 0x9fu, 0x92u};
    CHECK(utf8_valid(ascii, sizeof(ascii)));
    CHECK(utf8_valid(valid_two, sizeof(valid_two)));
    CHECK(utf8_valid(valid_three, sizeof(valid_three)));
    CHECK(utf8_valid(valid_four, sizeof(valid_four)));
    CHECK(utf8_valid((const unsigned char *)"", 0));
    CHECK(!utf8_valid(embedded_nul, sizeof(embedded_nul)));
    CHECK(!utf8_valid(invalid_continuation, sizeof(invalid_continuation)));
    CHECK(!utf8_valid(overlong_two, sizeof(overlong_two)));
    CHECK(!utf8_valid(overlong_three, sizeof(overlong_three)));
    CHECK(!utf8_valid(surrogate, sizeof(surrogate)));
    CHECK(!utf8_valid(too_large, sizeof(too_large)));
    CHECK(!utf8_valid(invalid_lead, sizeof(invalid_lead)));
    CHECK(!utf8_valid(truncated_two, sizeof(truncated_two)));
    CHECK(!utf8_valid(truncated_three, sizeof(truncated_three)));
    CHECK(!utf8_valid(truncated_four, sizeof(truncated_four)));
    return 0;
}

static int write_all(lm_handle handle, const void *buffer, size_t length) {
    const unsigned char *bytes = buffer;
    size_t offset = 0;
    while (offset < length) {
        size_t written = 0;
        if (lm_write(handle, bytes + offset, length - offset, &written, &interrupted) != 0 ||
            written == 0 || written > length - offset) return -1;
        offset += written;
    }
    return 0;
}

static int test_line_reader_boundaries(void) {
    char *path = NULL;
    lm_handle handle = LM_INVALID_HANDLE;
    lm_handle read_handle = LM_INVALID_HANDLE;
    unsigned char *payload = NULL;
    LineReader reader;
    memset(&reader, 0, sizeof(reader));
    int reader_initialized = 0;
    int result = 0;

    CHECK_CLEANUP(lm_create_temp(scratch_directory(), &path, &handle, NULL) == 0);
    payload = malloc(MAX_LINE + 2u);
    CHECK_CLEANUP(payload != NULL);

    memset(payload, 'a', MAX_LINE);
    CHECK_CLEANUP(write_all(handle, payload, MAX_LINE - 1u) == 0);
    CHECK_CLEANUP(write_all(handle, "\n", 1u) == 0);
    CHECK_CLEANUP(write_all(handle, payload, MAX_LINE) == 0);
    CHECK_CLEANUP(write_all(handle, "\n", 1u) == 0);
    CHECK_CLEANUP(write_all(handle, payload, MAX_LINE + 1u) == 0);
    CHECK_CLEANUP(write_all(handle, "\n", 1u) == 0);
    CHECK_CLEANUP(lm_close(handle) == 0);
    handle = LM_INVALID_HANDLE;
    CHECK_CLEANUP(lm_open_read(path, &read_handle) == 0);
    CHECK_CLEANUP(line_reader_init(&reader, read_handle) == 0);
    reader_initialized = 1;

    CHECK_CLEANUP(line_reader_next(&reader) == 1);
    CHECK_CLEANUP(reader.line_len == MAX_LINE - 1u);
    CHECK_CLEANUP(reader.line[0] == 'a' && reader.line[reader.line_len - 1u] == 'a');
    CHECK_CLEANUP(line_reader_next(&reader) == 1);
    CHECK_CLEANUP(reader.line_len == MAX_LINE);
    CHECK_CLEANUP(reader.line[0] == 'a' && reader.line[reader.line_len - 1u] == 'a');
    CHECK_CLEANUP(line_reader_next(&reader) == -3);

cleanup:
    if (reader_initialized) line_reader_destroy(&reader);
    if (read_handle != LM_INVALID_HANDLE && lm_close(read_handle) != 0) result = -1;
    if (handle != LM_INVALID_HANDLE && lm_close(handle) != 0) result = -1;
    if (path) {
        if (lm_remove(path) != 0) result = -1;
        free(path);
    }
    free(payload);
    return result;
}

static int test_line_reader_nul(void) {
    static const unsigned char line[] = {'a', 'b', 0, 'c', '\n'};
    char *path = NULL;
    lm_handle handle = LM_INVALID_HANDLE;
    lm_handle read_handle = LM_INVALID_HANDLE;
    LineReader reader;
    memset(&reader, 0, sizeof(reader));
    int reader_initialized = 0;
    int result = 0;
    CHECK_CLEANUP(lm_create_temp(scratch_directory(), &path, &handle, NULL) == 0);
    CHECK_CLEANUP(write_all(handle, line, sizeof(line)) == 0);
    CHECK_CLEANUP(lm_close(handle) == 0);
    handle = LM_INVALID_HANDLE;
    CHECK_CLEANUP(lm_open_read(path, &read_handle) == 0);
    CHECK_CLEANUP(line_reader_init(&reader, read_handle) == 0);
    reader_initialized = 1;
    CHECK_CLEANUP(line_reader_next(&reader) == -2);
cleanup:
    if (reader_initialized) line_reader_destroy(&reader);
    if (read_handle != LM_INVALID_HANDLE && lm_close(read_handle) != 0) result = -1;
    if (handle != LM_INVALID_HANDLE && lm_close(handle) != 0) result = -1;
    if (path) {
        if (lm_remove(path) != 0) result = -1;
        free(path);
    }
    return result;
}

/* ---- sorted-input partition finder vs a brute-force scan ---- */

typedef struct {
    uint64_t offset;
    char *path;
    char *tn;
    int joined;
} BruteLine;

static uint64_t unit_random_state = UINT64_C(88172645463325252);
static uint64_t unit_random(void) {
    unit_random_state ^= unit_random_state << 13;
    unit_random_state ^= unit_random_state >> 7;
    unit_random_state ^= unit_random_state << 17;
    return unit_random_state;
}

/* Builds a sorted synthetic tracefile exercising TN/blank/comment/CRLF/joined/KF/excluded lines. */
static unsigned char *build_partition_file(size_t sections, size_t *length_out) {
    size_t capacity = sections * 4096u + 4096u, length = 0;
    char *data = malloc(capacity);
    if (!data) return NULL;
    char line[512];
    int previous_unterminated = 0;
    for (size_t i = 0; i < sections; ++i) {
        unsigned flavor = (unsigned)(unit_random() % 12u);
        size_t extra_dup = unit_random() % 5u == 0 ? 1u : 0u;  /* equal consecutive paths */
        for (size_t copy = 0; copy <= extra_dup; ++copy) {
            const char *eol = flavor == 3u ? "\r\n" : "\n";
            int skipped = flavor == 4u;
            int n = 0;
            if (flavor == 5u) n = snprintf(line, sizeof(line), "SUM:unknown record\n");
            else n = 0;
            if (n > 0) { memcpy(data + length, line, (size_t)n); length += (size_t)n; }
            if (!previous_unterminated || flavor != 6u) {
                if (flavor == 7u || flavor == 8u) {
                    n = snprintf(line, sizeof(line), "TN:first%zu%s", i, eol);
                    memcpy(data + length, line, (size_t)n); length += (size_t)n;
                }
                if (flavor == 8u) {
                    n = snprintf(line, sizeof(line), "\n# comment\n%s\n", eol);
                    memcpy(data + length, line, (size_t)n); length += (size_t)n;
                }
                if (flavor == 9u) {
                    n = snprintf(line, sizeof(line), "TN:x\nTN:y%s", eol);
                    memcpy(data + length, line, (size_t)n); length += (size_t)n;
                }
            }
            const char *tag = flavor == 10u ? "KF" : "SF";
            if (previous_unterminated && flavor == 6u)
                n = snprintf(line, sizeof(line), "end_of_record%s:/p/%s/f%05zu.c%s", tag,
                             skipped ? "skip" : "keep", i, eol);
            else
                n = snprintf(line, sizeof(line), "%s:/p/%s/f%05zu.c%s", tag,
                             skipped ? "skip" : "keep", i, eol);
            memcpy(data + length, line, (size_t)n); length += (size_t)n;
            size_t rows = unit_random() % 60u;
            for (size_t row = 0; row < rows; ++row) {
                n = snprintf(line, sizeof(line), "DA:%zu,%u%s", row + 1u, (unsigned)(unit_random() % 9u), eol);
                memcpy(data + length, line, (size_t)n); length += (size_t)n;
            }
            n = snprintf(line, sizeof(line), "SUM:not-an-sf-line%s", eol);
            memcpy(data + length, line, (size_t)n); length += (size_t)n;
            /* Sometimes lose the final newline so the next SF is joined to end_of_record. */
            previous_unterminated = flavor == 11u;
            n = snprintf(line, sizeof(line), previous_unterminated ? "end_of_record" : "end_of_record%s", eol);
            memcpy(data + length, line, (size_t)n); length += (size_t)n;
            if (!previous_unterminated && flavor == 1u) { data[length++] = '\n'; }
        }
    }
    if (previous_unterminated) data[length++] = '\n';
    *length_out = length;
    return (unsigned char *)data;
}

static void brute_scan(const Options *options, const unsigned char *data, size_t length,
                       BruteLine **lines_out, size_t *count_out) {
    BruteLine *lines = calloc(length / 8u + 8u, sizeof(*lines));
    size_t count = 0;
    char *pending = NULL;
    size_t position = 0;
    while (position < length) {
        size_t end = position;
        while (end < length && data[end] != '\n') ++end;
        size_t line_length = end - position;
        const unsigned char *line = data + position;
        if (line_length != 0 && line[line_length - 1u] == '\r') --line_length;
        const char *source = NULL;
        int joined = 0;
        if (line_length >= 16u && memcmp(line, "end_of_record", 13u) == 0 &&
            (memcmp(line + 13, "SF:", 3u) == 0 || memcmp(line + 13, "KF:", 3u) == 0)) {
            source = (const char *)line + 16u; joined = 1;
        } else if (line_length >= 3u && (memcmp(line, "SF:", 3u) == 0 || memcmp(line, "KF:", 3u) == 0)) {
            source = (const char *)line + 3u;
        }
        if (source) {
            char *text = duplicate_range(source, line_length - (size_t)(source - (const char *)line));
            char *rewritten = rewrite_path(options, text);
            if (path_selected(options, rewritten)) {
                lines[count].offset = position;
                lines[count].path = rewritten;
                lines[count].joined = joined;
                lines[count].tn = joined || !pending ? NULL : duplicate_string(pending);
                ++count;
            } else free(rewritten);
            free(text);
            free(pending);
            pending = NULL;
        } else if (line_length >= 3u && memcmp(line, "TN:", 3u) == 0) {
            free(pending);
            pending = duplicate_range((const char *)line + 3u, line_length - 3u);
        } else if (line_length != 0 && line[0] != '#') {
            free(pending);
            pending = NULL;
        }
        position = end + 1u;
    }
    free(pending);
    *lines_out = lines;
    *count_out = count;
}

static int same_tn(const char *left, const char *right) {
    return (left == NULL && right == NULL) || (left && right && strcmp(left, right) == 0);
}

static int test_partition_finder(void) {
    Options options;
    memset(&options, 0, sizeof(options));
    CHECK(list_add_string(&options.excludes, "/p/skip/*") == 0);
    size_t length = 0;
    unsigned char *data = build_partition_file(1500u, &length);
    CHECK(data != NULL);
    char *path = NULL;
    lm_handle handle = LM_INVALID_HANDLE, read_handle = LM_INVALID_HANDLE;
    BruteLine *lines = NULL;
    size_t count = 0;
    PartScanner scanner;
    memset(&scanner, 0, sizeof(scanner));
    int scanner_ready = 0;
    int result = 0;
    CHECK_CLEANUP(lm_create_temp(scratch_directory(), &path, &handle, NULL) == 0);
    CHECK_CLEANUP(write_all(handle, data, length) == 0);
    CHECK_CLEANUP(lm_close(handle) == 0);
    handle = LM_INVALID_HANDLE;
    CHECK_CLEANUP(lm_open_read(path, &read_handle) == 0);
    uint64_t size = 0;
    CHECK_CLEANUP(lm_file_size(read_handle, &size) == 0 && size == (uint64_t)length);
    CHECK_CLEANUP(length > 3u * PART_CHUNK);  /* probes cross chunk boundaries */
    brute_scan(&options, data, length, &lines, &count);
    CHECK_CLEANUP(count > 100u);
    CHECK_CLEANUP(part_scanner_init(&scanner) == 0);
    scanner_ready = 1;

    /* First kept SF line at or after arbitrary offsets, with and without a splitter. */
    for (unsigned round = 0; round < 600u; ++round) {
        uint64_t from = round < 40u ? (uint64_t)round * 3u : unit_random() % (uint64_t)length;
        if (round % 7u == 0) from = lines[unit_random() % count].offset;
        if (round % 11u == 0) from = lines[unit_random() % count].offset + 1u;
        const char *splitter = NULL;
        size_t splitter_length = 0;
        if (round % 2u) {
            splitter = lines[unit_random() % count].path;
            splitter_length = strlen(splitter);
        }
        size_t expected = count;
        for (size_t i = 0; i < count; ++i)
            if (lines[i].offset >= from &&
                (!splitter || bytes_compare(lines[i].path, strlen(lines[i].path), splitter, splitter_length) >= 0)) {
                expected = i;
                break;
            }
        uint64_t start = 0;
        char *found_path = NULL;
        int joined = 0;
        int found = part_find_kept_sf(&scanner, &options, read_handle, size, from, size, splitter,
                                      splitter_length, &start, &found_path, &joined);
        CHECK_CLEANUP(found == (expected < count ? 1 : 0));
        if (found) {
            CHECK_CLEANUP(start == lines[expected].offset && joined == lines[expected].joined &&
                          strcmp(found_path, lines[expected].path) == 0);
            free(found_path);
        }
    }

    /* Limit is exclusive for the line start. */
    {
        uint64_t start = 0;
        char *found_path = NULL;
        int joined = 0;
        CHECK_CLEANUP(part_find_kept_sf(&scanner, &options, read_handle, size, 0, lines[0].offset,
                                        NULL, 0, &start, &found_path, &joined) == 0);
        CHECK_CLEANUP(part_find_kept_sf(&scanner, &options, read_handle, size, 0, lines[0].offset + 1u,
                                        NULL, 0, &start, &found_path, &joined) == 1 &&
                      start == lines[0].offset);
        free(found_path);
    }

    /* part_locate (binary search + linear tail + pending TN) for every kind of splitter. */
    static const uint64_t thresholds[] = {0u, 1u, 100u, 5000u, 65536u, UINT64_C(1) << 40};
    for (size_t t = 0; t < sizeof(thresholds) / sizeof(thresholds[0]); ++t) {
        scanner.linear_threshold = thresholds[t];
        for (size_t k = 0; k < count + 3u; k += (k < 40u ? 1u : 7u)) {
            char *splitter;
            if (k < count) splitter = duplicate_string(lines[k].path);
            else if (k == count) splitter = duplicate_string("");
            else if (k == count + 1u) splitter = duplicate_string("/p/keep/f99999.c");
            else splitter = duplicate_string("/p/keep/f00100.cz");
            uint64_t lo = k % 5u == 0 && k < count ? lines[k / 2u].offset : 0u;
            size_t expected = count;
            for (size_t i = 0; i < count; ++i)
                if (lines[i].offset >= lo &&
                    bytes_compare(lines[i].path, strlen(lines[i].path), splitter, strlen(splitter)) >= 0) {
                    expected = i;
                    break;
                }
            uint64_t offset = 0;
            char *tn = NULL;
            int status = part_locate(&scanner, &options, read_handle, size, splitter, strlen(splitter),
                                     lo, &offset, &tn);
            free(splitter);
            CHECK_CLEANUP(status == 0);
            if (expected == count) {
                CHECK_CLEANUP(offset == size && tn == NULL);
            } else {
                CHECK_CLEANUP(offset == lines[expected].offset && same_tn(tn, lines[expected].tn));
            }
            free(tn);
        }
    }

    /* Resource limits: tiny probe limit and tiny TN window give up or drop the splitter. */
    scanner.linear_threshold = 65536u;
    scanner.probe_limit = 16u;
    {
        uint64_t start = 0;
        char *found_path = NULL;
        int joined = 0;
        CHECK_CLEANUP(part_find_kept_sf(&scanner, &options, read_handle, size, lines[0].offset + 1u, size,
                                        NULL, 0, &start, &found_path, &joined) == -1);
    }
    scanner.probe_limit = MAX_LINE;
    scanner.budget = 100u;
    scanner.spent = 0;
    {
        uint64_t start = 0;
        char *found_path = NULL;
        int joined = 0;
        scanner.probe_limit = UINT64_MAX;  /* only the overall budget may trip here */
        CHECK_CLEANUP(part_find_kept_sf(&scanner, &options, read_handle, size, 0, size, "~", 1u,
                                        &start, &found_path, &joined) == -1);
        scanner.probe_limit = MAX_LINE;
    }
    scanner.budget = 0;
    scanner.tn_window = 8u;
    {
        int dropped = 0;
        for (size_t i = 0; i < count && !dropped; ++i) {
            if (lines[i].offset == 0 || lines[i].joined) continue;
            uint64_t offset;
            char *tn = NULL;
            int status = part_locate(&scanner, &options, read_handle, size, lines[i].path,
                                     strlen(lines[i].path), lines[i].offset, &offset, &tn);
            free(tn);
            if (status == -2) dropped = 1;
            else CHECK_CLEANUP(status == 0);
        }
        CHECK_CLEANUP(dropped);
    }

cleanup:
    for (size_t i = 0; lines && i < count; ++i) { free(lines[i].path); free(lines[i].tn); }
    free(lines);
    if (scanner_ready) part_scanner_destroy(&scanner);
    if (read_handle != LM_INVALID_HANDLE && lm_close(read_handle) != 0) result = -1;
    if (handle != LM_INVALID_HANDLE && lm_close(handle) != 0) result = -1;
    if (path) { if (lm_remove(path) != 0) result = -1; free(path); }
    free(data);
    list_free(&options.excludes);
    return result;
}

static int test_line_reader_ranges(void) {
    size_t length = 0;
    unsigned char *data = build_partition_file(80u, &length);
    CHECK(data != NULL);
    char *path = NULL;
    lm_handle handle = LM_INVALID_HANDLE, read_handle = LM_INVALID_HANDLE;
    LineReader sequential, ranged;
    memset(&sequential, 0, sizeof(sequential));
    memset(&ranged, 0, sizeof(ranged));
    int result = 0;
    CHECK_CLEANUP(lm_create_temp(scratch_directory(), &path, &handle, NULL) == 0);
    CHECK_CLEANUP(write_all(handle, data, length) == 0);
    CHECK_CLEANUP(lm_close(handle) == 0);
    handle = LM_INVALID_HANDLE;
    CHECK_CLEANUP(lm_open_read(path, &read_handle) == 0);
    CHECK_CLEANUP(line_reader_init(&sequential, read_handle) == 0);
    CHECK_CLEANUP(line_reader_init(&ranged, read_handle) == 0);
    /* Sequential pass records every line start; ranged readers must agree from any of them. */
    size_t capacity = length + 1u, lines = 0;
    uint64_t *starts = malloc(capacity * sizeof(*starts));
    CHECK_CLEANUP(starts != NULL);
    uint64_t offset = 0;
    for (;;) {
        int got = line_reader_next(&sequential);
        if (got == 0) break;
        CHECK_CLEANUP(got == 1);
        starts[lines++] = offset;
        offset += sequential.line_len + 1u;
    }
    CHECK_CLEANUP(lines > 100u);
    for (unsigned round = 0; round < 40u; ++round) {
        size_t first = (size_t)(unit_random() % lines);
        line_reader_seek(&ranged, starts[first]);
        for (size_t index = first; index < lines && index < first + 700u; ++index) {
            CHECK_CLEANUP(line_reader_offset(&ranged) == starts[index]);
            int got = line_reader_next(&ranged);
            CHECK_CLEANUP(got == 1);
            size_t expected_length = (index + 1u < lines ? (size_t)(starts[index + 1u] - starts[index]) - 1u
                                                           : length - (size_t)starts[index]);
            if (index + 1u == lines && length != 0 && data[length - 1u] == '\n') expected_length -= 1u;
            CHECK_CLEANUP(ranged.line_len == expected_length);
            CHECK_CLEANUP(memcmp(ranged.line, data + starts[index], expected_length) == 0);
            CHECK_CLEANUP(ranged.line_no == index - first + 1u);
        }
    }
    line_reader_seek(&ranged, (uint64_t)length);
    CHECK_CLEANUP(line_reader_next(&ranged) == 0);
    free(starts);
cleanup:
    line_reader_destroy(&sequential);
    line_reader_destroy(&ranged);
    if (read_handle != LM_INVALID_HANDLE && lm_close(read_handle) != 0) result = -1;
    if (path) { if (lm_remove(path) != 0) result = -1; free(path); }
    free(data);
    return result;
}

int main(void) {
    static const struct {
        const char *name;
        int (*run)(void);
    } tests[] = {
        {"saturating add", test_saturating_add},
        {"writer_u64", test_writer_u64},
        {"mem-limit size parsing", test_parse_size},
        {"record comparators", test_record_comparators},
        {"path rewriting and globs", test_path_rewriting_and_globs},
        {"UTF-8 and NUL validation", test_utf8_validation},
        {"line reader 1 MiB boundaries", test_line_reader_boundaries},
        {"line reader NUL rejection", test_line_reader_nul},
        {"line reader range mode", test_line_reader_ranges},
        {"partition finder vs brute force", test_partition_finder}
    };
    for (size_t i = 0; i < sizeof(tests) / sizeof(tests[0]); ++i) {
        if (tests[i].run() != 0) return 1;
    }
    printf("unit tests: PASS (%u assertions)\n", assertions);
    return 0;
}
