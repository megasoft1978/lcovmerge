#define WIN32_LEAN_AND_MEAN
#include "platform.h"

#include <windows.h>
#include <errno.h>
#include <process.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

typedef struct {
    lm_thread_fn function;
    void *argument;
} ThreadStart;

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

static wchar_t *long_path(wchar_t *path) {
    size_t path_length = wcslen(path);
    if (path_length < MAX_PATH ||
        (path[0] == L'\\' && path[1] == L'\\' &&
         (path[2] == L'?' || path[2] == L'.') && path[3] == L'\\'))
        return path;

    for (size_t i = 0; i < path_length; ++i)
        if (path[i] == L'/') path[i] = L'\\';

    wchar_t *absolute = path;
    if (!((path_length >= 3u && path[1] == L':' && path[2] == L'\\') ||
          (path_length >= 2u && path[0] == L'\\' && path[1] == L'\\'))) {
        DWORD capacity = GetFullPathNameW(path, 0, NULL, NULL);
        if (capacity == 0 || (size_t)capacity > SIZE_MAX / sizeof(*path)) {
            free(path);
            return NULL;
        }
        absolute = malloc((size_t)capacity * sizeof(*absolute));
        if (!absolute) { free(path); return NULL; }
        DWORD length = GetFullPathNameW(path, capacity, absolute, NULL);
        free(path);
        if (length == 0 || length >= capacity) { free(absolute); return NULL; }
    }

    size_t absolute_length = wcslen(absolute);
    if (absolute_length < MAX_PATH) return absolute;
    if (absolute[0] == L'\\' && absolute[1] == L'\\') {
        static const wchar_t prefix[] = L"\\\\?\\UNC\\";
        size_t prefix_length = sizeof(prefix) / sizeof(prefix[0]) - 1u;
        size_t character_count = prefix_length + absolute_length - 1u;
        if (character_count > SIZE_MAX / sizeof(*absolute)) {
            free(absolute);
            return NULL;
        }
        wchar_t *extended = malloc(character_count * sizeof(*extended));
        if (!extended) { free(absolute); return NULL; }
        memcpy(extended, prefix, prefix_length * sizeof(*extended));
        memcpy(extended + prefix_length, absolute + 2,
               (absolute_length - 1u) * sizeof(*extended));
        free(absolute);
        return extended;
    }
    if (absolute_length >= 3u && absolute[1] == L':' && absolute[2] == L'\\') {
        static const wchar_t prefix[] = L"\\\\?\\";
        size_t prefix_length = sizeof(prefix) / sizeof(prefix[0]) - 1u;
        if (absolute_length + prefix_length + 1u > SIZE_MAX / sizeof(*absolute)) {
            free(absolute);
            return NULL;
        }
        wchar_t *extended = malloc((absolute_length + prefix_length + 1u) * sizeof(*extended));
        if (!extended) { free(absolute); return NULL; }
        memcpy(extended, prefix, prefix_length * sizeof(*extended));
        memcpy(extended + prefix_length, absolute,
               (absolute_length + 1u) * sizeof(*extended));
        free(absolute);
        return extended;
    }
    free(absolute);
    return NULL;
}

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
    return long_path(wide);
}

static BOOL WINAPI console_ctrl_handler(DWORD event) {
    switch (event) {
        case CTRL_C_EVENT:
        case CTRL_BREAK_EVENT:
        case CTRL_CLOSE_EVENT:
            (void)raise(SIGINT);
            return TRUE;
        default:
            return FALSE;
    }
}

int lm_install_console_ctrl_handler(void) {
    return SetConsoleCtrlHandler(console_ctrl_handler, TRUE) ? 0 : -1;
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
    static volatile LONG sequence = 0;
    static const wchar_t name_format[] = L"lcovmerge-%08lX-%08lX-%08lX.tmp";
    wchar_t name[64];
    size_t directory_length = wcslen(wide_dir);
    int add_separator = directory_length != 0 && wide_dir[directory_length - 1] != L'/' &&
                        wide_dir[directory_length - 1] != L'\\';
    for (unsigned attempt = 0; attempt < 256u; ++attempt) {
        int name_length = swprintf(name, sizeof(name) / sizeof(name[0]), name_format,
                                   (unsigned long)GetCurrentProcessId(),
                                   (unsigned long)GetTickCount(),
                                   (unsigned long)InterlockedIncrement(&sequence));
        if (name_length <= 0 || (size_t)name_length >= sizeof(name) / sizeof(name[0])) {
            free(wide_dir);
            return -1;
        }
        size_t separator_length = add_separator ? 1u : 0u;
        size_t component_length = (size_t)name_length;
        if (directory_length > SIZE_MAX - separator_length - component_length - 1u) {
            free(wide_dir);
            return -1;
        }
        size_t path_length = directory_length + separator_length + component_length + 1u;
        if (path_length > SIZE_MAX / sizeof(*wide_dir)) { free(wide_dir); return -1; }
        wchar_t *path = malloc(path_length * sizeof(*path));
        if (!path) { free(wide_dir); return -1; }
        memcpy(path, wide_dir, directory_length * sizeof(*path));
        if (add_separator) path[directory_length] = L'\\';
        memcpy(path + directory_length + separator_length, name,
               (component_length + 1u) * sizeof(*path));
        path = long_path(path);
        if (!path) { free(wide_dir); return -1; }
        HANDLE handle = CreateFileW(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
                                    NULL, CREATE_NEW, FILE_ATTRIBUTE_TEMPORARY, NULL);
        if (handle != INVALID_HANDLE_VALUE) {
            free(wide_dir);
            char *utf8 = utf8_path(path);
            if (!utf8) { CloseHandle(handle); DeleteFileW(path); free(path); return -1; }
            free(path);
            *path_out = utf8;
            *out = (lm_handle)(intptr_t)handle;
            return 0;
        }
        DWORD error = GetLastError();
        free(path);
        if (error != ERROR_FILE_EXISTS && error != ERROR_ALREADY_EXISTS) {
            free(wide_dir);
            return -1;
        }
    }
    free(wide_dir);
    return -1;
}

int lm_read(lm_handle raw, void *buffer, size_t capacity, size_t *read_out,
            const atomic_bool *cancelled) {
    (void)cancelled;
    HANDLE handle = (HANDLE)(intptr_t)raw;
    DWORD request = capacity > UINT32_MAX ? UINT32_MAX : (DWORD)capacity;
    DWORD result = 0;
    if (!ReadFile(handle, buffer, request, &result, NULL)) return -1;
    *read_out = (size_t)result;
    return 0;
}

int lm_write(lm_handle raw, const void *buffer, size_t length, size_t *written_out,
             const atomic_bool *cancelled) {
    (void)cancelled;
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

static unsigned __stdcall thread_entry(void *raw) {
    ThreadStart *start = raw;
    lm_thread_fn function = start->function;
    void *argument = start->argument;
    free(start);
    (void)function(argument);
    return 0u;
}

int lm_thread_start(lm_thread *thread, lm_thread_fn function, void *argument) {
    ThreadStart *start = malloc(sizeof(*start));
    if (!start) return -1;
    start->function = function;
    start->argument = argument;
    uintptr_t raw_handle = _beginthreadex(NULL, 0, thread_entry, start, 0, NULL);
    if (raw_handle == 0) { free(start); return -1; }
    *thread = (HANDLE)raw_handle;
    return 0;
}

int lm_thread_join(lm_thread thread) {
    DWORD result = WaitForSingleObject(thread, INFINITE);
    BOOL closed = CloseHandle(thread);
    return result == WAIT_OBJECT_0 && closed ? 0 : -1;
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
    if (lm_install_console_ctrl_handler() != 0) {
        fputs("lcovmerge: cannot install console control handler\n", stderr);
        goto done;
    }
    result = lcovmerge_main(argc, argv);
done:
    for (int i = 0; i < argc; ++i) free(argv[i]);
    free(argv);
    return result;
}
