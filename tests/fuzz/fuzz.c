#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE
#include <fcntl.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int lcovmerge_main(int argc, char **argv);

static char fuzz_directory[1024];
static char fuzz_input[1152];
static char fuzz_output[1152];
#ifndef LCOVMERGE_LIBFUZZER
static int saved_stderr = -1;
static int null_stderr = -1;
#endif

static void cleanup(void) {
#ifndef LCOVMERGE_LIBFUZZER
    if (saved_stderr >= 0) (void)dup2(saved_stderr, STDERR_FILENO);
    if (saved_stderr >= 0) (void)close(saved_stderr);
    if (null_stderr >= 0) (void)close(null_stderr);
#endif
    if (fuzz_input[0]) (void)unlink(fuzz_input);
    if (fuzz_output[0]) (void)unlink(fuzz_output);
    if (fuzz_directory[0]) (void)rmdir(fuzz_directory);
}

static int initialize(void) {
    if (fuzz_directory[0]) return 0;
    const char *temporary = getenv("TMPDIR");
    if (!temporary || !temporary[0]) temporary = "/tmp";
    int directory_length = snprintf(fuzz_directory, sizeof(fuzz_directory),
                                    "%s/lcovmerge-fuzz-XXXXXX", temporary);
    if (directory_length < 0 || (size_t)directory_length >= sizeof(fuzz_directory)) return -1;
    if (!mkdtemp(fuzz_directory)) return -1;
    int input_length = snprintf(fuzz_input, sizeof(fuzz_input), "%s/input.info", fuzz_directory);
    int output_length = snprintf(fuzz_output, sizeof(fuzz_output), "%s/output.info", fuzz_directory);
    if (input_length < 0 || (size_t)input_length >= sizeof(fuzz_input) ||
        output_length < 0 || (size_t)output_length >= sizeof(fuzz_output)) return -1;
#ifndef LCOVMERGE_LIBFUZZER
    saved_stderr = dup(STDERR_FILENO);
    null_stderr = open("/dev/null", O_WRONLY);
    if (saved_stderr < 0 || null_stderr < 0) return -1;
#endif
    if (atexit(cleanup) != 0) return -1;
    return 0;
}

#ifdef LM_TEST_PARTITION
/* Reads a whole file; *data is NULL when it does not exist. */
static int read_output(const char *path, unsigned char **data, size_t *size) {
    *data = NULL;
    *size = 0;
    FILE *file = fopen(path, "rb");
    if (!file) return 0;
    size_t capacity = 4096;
    unsigned char *buffer = malloc(capacity);
    if (!buffer) { fclose(file); return -1; }
    size_t used = 0, amount;
    while ((amount = fread(buffer + used, 1, capacity - used, file)) != 0) {
        used += amount;
        if (used == capacity) {
            capacity *= 2;
            unsigned char *grown = realloc(buffer, capacity);
            if (!grown) { free(buffer); fclose(file); return -1; }
            buffer = grown;
        }
    }
    fclose(file);
    *data = buffer;
    *size = used;
    return 0;
}

/* Differential: the same two-copy merge, single job vs forced partitions, must agree exactly. */
static void check_partitions(void) {
    char *arguments[] = {"lcovmerge-fuzz", "--mem-limit", "64M", fuzz_input, fuzz_input,
                         "-o", fuzz_output, NULL};
    unsigned char *serial = NULL, *threaded = NULL;
    size_t serial_size = 0, threaded_size = 0;
    (void)unsetenv("LCOVMERGE_TEST_PARTITION");
    (void)unlink(fuzz_output);
    int serial_status = lcovmerge_main(7, arguments);
    if (read_output(fuzz_output, &serial, &serial_size) != 0) return;
    (void)setenv("LCOVMERGE_TEST_PARTITION", "3", 1);
    (void)unlink(fuzz_output);
    int threaded_status = lcovmerge_main(7, arguments);
    (void)unsetenv("LCOVMERGE_TEST_PARTITION");
    if (read_output(fuzz_output, &threaded, &threaded_size) != 0) { free(serial); return; }
    if (serial_status != threaded_status || serial_size != threaded_size ||
        (serial == NULL) != (threaded == NULL) ||
        (serial && memcmp(serial, threaded, serial_size) != 0)) abort();
    free(serial);
    free(threaded);
}
#endif

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (initialize() != 0) return 0;
    int input_fd = open(fuzz_input, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (input_fd < 0) return 0;

    size_t offset = 0;
    while (offset < size) {
        ssize_t count = write(input_fd, data + offset, size - offset);
        if (count <= 0) break;
        offset += (size_t)count;
    }
    (void)close(input_fd);

#ifndef LCOVMERGE_LIBFUZZER
    (void)dup2(null_stderr, STDERR_FILENO);
#endif
    char *arguments[] = {"lcovmerge-fuzz", "--jobs", "1", "--mem-limit", "8M",
                         fuzz_input, "-o", fuzz_output, NULL};
    (void)lcovmerge_main(8, arguments);
#ifdef LM_TEST_PARTITION
    check_partitions();
#endif
#ifndef LCOVMERGE_LIBFUZZER
    (void)dup2(saved_stderr, STDERR_FILENO);
#endif
    return 0;
}
