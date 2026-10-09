#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_INPUT 4096u
#define SEED_COUNT 11u

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

static uint64_t random_state = UINT64_C(0x6c636f766d657267);

static uint64_t random_u64(void) {
    uint64_t value = random_state;
    value ^= value >> 12;
    value ^= value << 25;
    value ^= value >> 27;
    random_state = value;
    return value * UINT64_C(2685821657736338717);
}

static size_t load_seed(const char *directory, const char *name, uint8_t buffer[MAX_INPUT]) {
    char path[1024];
    int length = snprintf(path, sizeof(path), "%s/%s", directory, name);
    if (length < 0 || (size_t)length >= sizeof(path)) return SIZE_MAX;
    FILE *stream = fopen(path, "rb");
    if (!stream) return SIZE_MAX;
    size_t amount = fread(buffer, 1, MAX_INPUT, stream);
    int extra = fgetc(stream);
    int failed = ferror(stream);
    (void)fclose(stream);
    return extra == EOF && !failed ? amount : SIZE_MAX;
}

static size_t mutate(const uint8_t *seed, size_t seed_size, uint8_t output[MAX_INPUT]) {
    size_t length = seed_size;
    if (length) memcpy(output, seed, length);
    unsigned operation = (unsigned)(random_u64() % UINT64_C(7));
    if (operation == 0 && length) {
        size_t at = (size_t)(random_u64() % (uint64_t)length);
        output[at] ^= (uint8_t)(1u << (unsigned)(random_u64() % UINT64_C(8)));
    } else if (operation == 1 && length < MAX_INPUT) {
        size_t at = (size_t)(random_u64() % (uint64_t)(length + 1));
        memmove(output + at + 1, output + at, length - at);
        output[at] = (uint8_t)random_u64();
        ++length;
    } else if (operation == 2 && length) {
        size_t at = (size_t)(random_u64() % (uint64_t)length);
        memmove(output + at, output + at + 1, length - at - 1);
        --length;
    } else if (operation == 3 && length < MAX_INPUT) {
        output[length++] = (uint8_t)(random_u64() & UINT64_C(0x7f));
    } else if (operation == 4 && length) {
        length = (size_t)(random_u64() % (uint64_t)(length + 1));
    } else if (operation == 5 && length) {
        size_t at = (size_t)(random_u64() % (uint64_t)length);
        static const uint8_t alphabet[] = "0123456789,:-FNSDABRMCX\n\r";
        output[at] = alphabet[random_u64() % (sizeof(alphabet) - 1)];
    } else if (operation == 6 && length && length < MAX_INPUT) {
        size_t begin = (size_t)(random_u64() % (uint64_t)length);
        size_t amount = (size_t)(random_u64() % (uint64_t)(length - begin + 1));
        if (amount > MAX_INPUT - length) amount = MAX_INPUT - length;
        memcpy(output + length, output + begin, amount);
        length += amount;
    }
    return length;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s CORPUS_DIR EXECUTIONS\n", argv[0]);
        return 2;
    }
    char *end = NULL;
    unsigned long long executions = strtoull(argv[2], &end, 10);
    if (!end || *end || executions == 0) return 2;

    static const char *const names[SEED_COUNT] = {
        "basic.info", "empty.info", "invalid.info", "mcdc.info",
        "u64-max.info", "branch-dash.info", "crlf-eof.info",
        "truncated.info", "nul.info", "invalid-utf8.info", "unknown-checksum.info"
    };
    uint8_t seeds[SEED_COUNT][MAX_INPUT];
    size_t lengths[SEED_COUNT];
    for (size_t i = 0; i < SEED_COUNT; ++i) {
        lengths[i] = load_seed(argv[1], names[i], seeds[i]);
        if (lengths[i] == SIZE_MAX) {
            fprintf(stderr, "cannot load fuzz seed %s/%s\n", argv[1], names[i]);
            return 2;
        }
    }

    uint8_t input[MAX_INPUT];
    for (unsigned long long iteration = 0; iteration < executions; ++iteration) {
        size_t index = (size_t)(iteration % SEED_COUNT);
        size_t length = mutate(seeds[index], lengths[index], input);
        (void)LLVMFuzzerTestOneInput(input, length);
        if ((iteration + 1) % 100000 == 0)
            fprintf(stderr, "fuzz executions=%llu\n", iteration + 1);
    }
    fprintf(stderr, "fuzz complete executions=%llu seeds=%u\n", executions, SEED_COUNT);
    return 0;
}
