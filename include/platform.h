#ifndef LCOVMERGE_PLATFORM_H
#define LCOVMERGE_PLATFORM_H

#include <stddef.h>
#include <stdint.h>
#include <stdatomic.h>

#ifdef _WIN32
typedef void *lm_thread;
#else
#include <pthread.h>
typedef pthread_t lm_thread;
#endif

typedef intptr_t lm_handle;
typedef void *(*lm_thread_fn)(void *);

#define LM_INVALID_HANDLE ((lm_handle)-1)

int lm_open_read(const char *path, lm_handle *out);
int lm_is_regular_file(const char *path);
int lm_create_temp(const char *directory, char **path_out, lm_handle *out,
                   unsigned long *error_out);
void lm_format_error(unsigned long error, char *buffer, size_t capacity);
int lm_read(lm_handle handle, void *buffer, size_t capacity, size_t *read_out,
            const atomic_bool *cancelled);
int lm_write(lm_handle handle, const void *buffer, size_t length, size_t *written_out,
             const atomic_bool *cancelled);
int lm_close(lm_handle handle);
int lm_remove(const char *path);
int lm_rename(const char *from, const char *to);
char *lm_default_tempdir(void);
lm_handle lm_stdin_handle(void);
lm_handle lm_stdout_handle(void);
unsigned lm_cpu_count(void);
int lm_thread_start(lm_thread *thread, lm_thread_fn function, void *argument);
int lm_thread_join(lm_thread thread);
#ifdef _WIN32
int lm_install_console_ctrl_handler(void);
#endif

#endif
