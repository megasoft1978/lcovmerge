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

static char fuzz_directory[128];
static char fuzz_input[192];
static char fuzz_output[192];
static int saved_stderr = -1;
static int null_stderr = -1;

static void cleanup(void) {
    if (saved_stderr >= 0) (void)dup2(saved_stderr, STDERR_FILENO);
    if (saved_stderr >= 0) (void)close(saved_stderr);
    if (null_stderr >= 0) (void)close(null_stderr);
    if (fuzz_input[0]) (void)unlink(fuzz_input);
    if (fuzz_output[0]) (void)unlink(fuzz_output);
    if (fuzz_directory[0]) (void)rmdir(fuzz_directory);
}

static int initialize(void) {
    if (fuzz_directory[0]) return 0;
    memcpy(fuzz_directory, "/tmp/lcovmerge-fuzz-XXXXXX", sizeof("/tmp/lcovmerge-fuzz-XXXXXX"));
    if (!mkdtemp(fuzz_directory)) return -1;
    if (snprintf(fuzz_input, sizeof(fuzz_input), "%s/input.info", fuzz_directory) < 0 ||
        snprintf(fuzz_output, sizeof(fuzz_output), "%s/output.info", fuzz_directory) < 0) return -1;
    saved_stderr = dup(STDERR_FILENO);
    null_stderr = open("/dev/null", O_WRONLY);
    if (saved_stderr < 0 || null_stderr < 0) return -1;
    if (atexit(cleanup) != 0) return -1;
    return 0;
}

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

    (void)dup2(null_stderr, STDERR_FILENO);
    char *arguments[] = {"lcovmerge-fuzz", "--jobs", "1", "--mem-limit", "8M",
                         fuzz_input, "-o", fuzz_output, NULL};
    (void)lcovmerge_main(8, arguments);
    (void)dup2(saved_stderr, STDERR_FILENO);
    return 0;
}
