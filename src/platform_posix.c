#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE
#include "platform.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#ifdef __APPLE__
#include <sys/sysctl.h>
#endif

int lm_open_read(const char *path, lm_handle *out) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    *out = (lm_handle)fd;
    return 0;
}

int lm_create_temp(const char *directory, char **path_out, lm_handle *out) {
    const char *sep = directory[0] && directory[strlen(directory) - 1] == '/' ? "" : "/";
    size_t dlen = strlen(directory), slen = strlen(sep);
    static const char pattern[] = "lcovmerge-XXXXXX";
    if (dlen > SIZE_MAX - slen - sizeof(pattern)) return -1;
    size_t need = dlen + slen + sizeof(pattern);
    char *path = malloc(need);
    if (!path) return -1;
    memcpy(path, directory, dlen);
    memcpy(path + dlen, sep, slen);
    memcpy(path + dlen + slen, pattern, sizeof(pattern));
    int fd = mkstemp(path);
    if (fd < 0) { free(path); return -1; }
    *path_out = path;
    *out = (lm_handle)fd;
    return 0;
}

int lm_read(lm_handle handle, void *buffer, size_t capacity, size_t *read_out) {
    size_t request = capacity;
    if (request > (size_t)INT_MAX) request = (size_t)INT_MAX;
    ssize_t result;
    do { result = read((int)handle, buffer, request); } while (result < 0 && errno == EINTR);
    if (result < 0) return -1;
    *read_out = (size_t)result;
    return 0;
}

int lm_write(lm_handle handle, const void *buffer, size_t length, size_t *written_out) {
    size_t request = length;
    if (request > (size_t)INT_MAX) request = (size_t)INT_MAX;
    ssize_t result;
    do { result = write((int)handle, buffer, request); } while (result < 0 && errno == EINTR);
    if (result < 0) return -1;
    *written_out = (size_t)result;
    return 0;
}

int lm_close(lm_handle handle) { return close((int)handle); }
int lm_remove(const char *path) { return unlink(path); }
int lm_rename(const char *from, const char *to) { return rename(from, to); }
char *lm_default_tempdir(void) {
    const char *directory = getenv("TMPDIR");
    if (!directory || !*directory) directory = "/tmp";
    size_t length = strlen(directory);
    char *copy = malloc(length + 1);
    if (copy) memcpy(copy, directory, length + 1);
    return copy;
}
lm_handle lm_stdin_handle(void) { return (lm_handle)STDIN_FILENO; }
lm_handle lm_stdout_handle(void) { return (lm_handle)STDOUT_FILENO; }

unsigned lm_cpu_count(void) {
#ifdef __APPLE__
    unsigned count = 1;
    size_t size = sizeof(count);
    if (sysctlbyname("hw.ncpu", &count, &size, NULL, 0) == 0 && count != 0) return count;
#else
    long count = sysconf(_SC_NPROCESSORS_ONLN);
    return count > 0 ? (unsigned)count : 1u;
#endif
    return 1u;
}

int lm_thread_start(lm_thread *thread, lm_thread_fn function, void *argument) {
    return pthread_create(thread, NULL, function, argument);
}

int lm_thread_join(lm_thread thread) { return pthread_join(thread, NULL); }
