#define WIN32_LEAN_AND_MEAN
#include "platform.h"

#include <windows.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    lm_thread_fn function;
    void *argument;
} ThreadStart;

static wchar_t *wide_path(const char *path) {
    int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, NULL, 0);
    if (count <= 0) return NULL;
    size_t bytes = (size_t)count * sizeof(wchar_t);
    wchar_t *wide = malloc(bytes);
    if (!wide) return NULL;
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, wide, count) != count) {
        free(wide);
        return NULL;
    }
    return wide;
}

static char *utf8_path(const wchar_t *path) {
    int count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, path, -1, NULL, 0, NULL, NULL);
    if (count <= 0) return NULL;
    char *utf8 = malloc((size_t)count);
    if (!utf8) return NULL;
    if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, path, -1, utf8, count, NULL, NULL) != count) {
        free(utf8);
        return NULL;
    }
    return utf8;
}

int lm_open_read(const char *path, lm_handle *out) {
    wchar_t *wide = wide_path(path);
    if (!wide) return -1;
    HANDLE handle = CreateFileW(wide, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    free(wide);
    if (handle == INVALID_HANDLE_VALUE) return -1;
    *out = (lm_handle)(intptr_t)handle;
    return 0;
}

int lm_is_regular_file(const char *path) {
    wchar_t *wide = wide_path(path);
    if (!wide) return 0;
    HANDLE handle = CreateFileW(wide, FILE_READ_ATTRIBUTES,
                                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    free(wide);
    if (handle == INVALID_HANDLE_VALUE) return 0;
    BY_HANDLE_FILE_INFORMATION info;
    int regular = GetFileType(handle) == FILE_TYPE_DISK &&
                  GetFileInformationByHandle(handle, &info) &&
                  (info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0;
    CloseHandle(handle);
    return regular;
}

int lm_create_temp(const char *directory, char **path_out, lm_handle *out) {
    wchar_t *wide_dir = wide_path(directory);
    if (!wide_dir) return -1;
    wchar_t name[MAX_PATH];
    if (!GetTempFileNameW(wide_dir, L"lcm", 0, name)) { free(wide_dir); return -1; }
    free(wide_dir);
    HANDLE handle = CreateFileW(name, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
                                NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_TEMPORARY, NULL);
    if (handle == INVALID_HANDLE_VALUE) { DeleteFileW(name); return -1; }
    char *utf8 = utf8_path(name);
    if (!utf8) { CloseHandle(handle); DeleteFileW(name); return -1; }
    *path_out = utf8;
    *out = (lm_handle)(intptr_t)handle;
    return 0;
}

int lm_read(lm_handle raw, void *buffer, size_t capacity, size_t *read_out) {
    HANDLE handle = (HANDLE)(intptr_t)raw;
    DWORD request = capacity > UINT32_MAX ? UINT32_MAX : (DWORD)capacity;
    DWORD result = 0;
    if (!ReadFile(handle, buffer, request, &result, NULL)) return -1;
    *read_out = (size_t)result;
    return 0;
}

int lm_write(lm_handle raw, const void *buffer, size_t length, size_t *written_out) {
    HANDLE handle = (HANDLE)(intptr_t)raw;
    DWORD request = length > UINT32_MAX ? UINT32_MAX : (DWORD)length;
    DWORD result = 0;
    if (!WriteFile(handle, buffer, request, &result, NULL)) return -1;
    *written_out = (size_t)result;
    return 0;
}

int lm_close(lm_handle raw) { return CloseHandle((HANDLE)(intptr_t)raw) ? 0 : -1; }

int lm_remove(const char *path) {
    wchar_t *wide = wide_path(path);
    if (!wide) return -1;
    int result = DeleteFileW(wide) ? 0 : -1;
    free(wide);
    return result;
}

int lm_rename(const char *from, const char *to) {
    wchar_t *wide_from = wide_path(from);
    wchar_t *wide_to = wide_path(to);
    if (!wide_from || !wide_to) { free(wide_from); free(wide_to); return -1; }
    int result = MoveFileExW(wide_from, wide_to, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) ? 0 : -1;
    free(wide_from);
    free(wide_to);
    return result;
}

char *lm_default_tempdir(void) {
    DWORD capacity = GetTempPathW(0, NULL);
    if (capacity == 0) return NULL;
    wchar_t *wide = malloc(((size_t)capacity + 1) * sizeof(*wide));
    if (!wide) return NULL;
    DWORD length = GetTempPathW(capacity + 1, wide);
    if (length == 0 || length > capacity) { free(wide); return NULL; }
    char *utf8 = utf8_path(wide);
    free(wide);
    return utf8;
}

lm_handle lm_stdin_handle(void) {
    return (lm_handle)(intptr_t)GetStdHandle(STD_INPUT_HANDLE);
}
lm_handle lm_stdout_handle(void) {
    return (lm_handle)(intptr_t)GetStdHandle(STD_OUTPUT_HANDLE);
}

unsigned lm_cpu_count(void) {
    SYSTEM_INFO info;
    GetSystemInfo(&info);
    return info.dwNumberOfProcessors ? (unsigned)info.dwNumberOfProcessors : 1u;
}

static DWORD WINAPI thread_entry(LPVOID raw) {
    ThreadStart *start = raw;
    lm_thread_fn function = start->function;
    void *argument = start->argument;
    free(start);
    (void)function(argument);
    return 0;
}

int lm_thread_start(lm_thread *thread, lm_thread_fn function, void *argument) {
    ThreadStart *start = malloc(sizeof(*start));
    if (!start) return -1;
    start->function = function;
    start->argument = argument;
    HANDLE handle = CreateThread(NULL, 0, thread_entry, start, 0, NULL);
    if (!handle) { free(start); return -1; }
    *thread = handle;
    return 0;
}

int lm_thread_join(lm_thread thread) {
    DWORD result = WaitForSingleObject(thread, INFINITE);
    CloseHandle(thread);
    return result == WAIT_OBJECT_0 ? 0 : -1;
}

static char *utf8_arg(const wchar_t *arg) {
    return utf8_path(arg);
}

int lcovmerge_main(int argc, char **argv);

int wmain(int argc, wchar_t **wide_argv) {
    char **argv = calloc((size_t)argc, sizeof(*argv));
    if (!argv) return 3;
    int result = 3;
    for (int i = 0; i < argc; ++i) {
        argv[i] = utf8_arg(wide_argv[i]);
        if (!argv[i]) goto done;
    }
    result = lcovmerge_main(argc, argv);
done:
    for (int i = 0; i < argc; ++i) free(argv[i]);
    free(argv);
    return result;
}
