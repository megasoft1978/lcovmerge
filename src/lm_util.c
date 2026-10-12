#include "lm_internal.h"

atomic_bool interrupted = ATOMIC_VAR_INIT(0);
_Static_assert(ATOMIC_BOOL_LOCK_FREE == 2, "signal cancellation flag must be lock-free");

static void on_signal(int signal_number) {
    (void)signal_number;
    atomic_store_explicit(&interrupted, 1, memory_order_relaxed);
}

int install_signal_handlers(void) {
#ifdef _WIN32
    if (signal(SIGINT, on_signal) == SIG_ERR || signal(SIGTERM, on_signal) == SIG_ERR) return -1;
#ifdef SIGHUP
    if (signal(SIGHUP, on_signal) == SIG_ERR) return -1;
#endif
#else
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = on_signal;
    action.sa_flags = 0;
    if (sigemptyset(&action.sa_mask) != 0 ||
        sigaction(SIGINT, &action, NULL) != 0 ||
        sigaction(SIGTERM, &action, NULL) != 0) return -1;
#ifdef SIGHUP
    if (sigaction(SIGHUP, &action, NULL) != 0) return -1;
#endif
    struct sigaction ignore_pipe;
    memset(&ignore_pipe, 0, sizeof(ignore_pipe));
    ignore_pipe.sa_handler = SIG_IGN;
    if (sigemptyset(&ignore_pipe.sa_mask) != 0 ||
        sigaction(SIGPIPE, &ignore_pipe, NULL) != 0) return -1;
#endif
    return 0;
}

char *duplicate_range(const char *text, size_t length) {
    if (length == SIZE_MAX) return NULL;
    char *copy = malloc(length + 1);
    if (!copy) return NULL;
    if (length) memcpy(copy, text, length);
    copy[length] = '\0';
    return copy;
}

char *duplicate_string(const char *text) {
    return duplicate_range(text, strlen(text));
}

static int list_add(StringList *list, char *item) {
    if (list->count == list->capacity) {
        size_t next = list->capacity ? list->capacity * 2 : 16;
        if (next < list->capacity || next > SIZE_MAX / sizeof(*list->items)) return -1;
        char **grown = realloc(list->items, next * sizeof(*list->items));
        if (!grown) return -1;
        list->items = grown;
        list->capacity = next;
    }
    list->items[list->count++] = item;
    return 0;
}

int list_add_string(StringList *list, const char *item) {
    char *copy = duplicate_string(item);
    if (!copy) return -1;
    if (list_add(list, copy) != 0) { free(copy); return -1; }
    return 0;
}

void list_free(StringList *list) {
    for (size_t i = 0; i < list->count; ++i) free(list->items[i]);
    free(list->items);
    memset(list, 0, sizeof(*list));
}

int parse_u64(const char *text, size_t length, uint64_t *value) {
    if (length == 0) return 0;
    uint64_t result = 0;
    for (size_t i = 0; i < length; ++i) {
        unsigned char ch = (unsigned char)text[i];
        if (ch < (unsigned char)'0' || ch > (unsigned char)'9') return 0;
        uint64_t digit = (uint64_t)(ch - (unsigned char)'0');
        if (result > (UINT64_MAX - digit) / UINT64_C(10)) return 0;
        result = result * UINT64_C(10) + digit;
    }
    *value = result;
    return 1;
}

int parse_size(const char *text, size_t *value) {
    size_t length = strlen(text);
    uint64_t multiplier = 1;
    if (length == 0) return 0;
    unsigned char suffix = (unsigned char)text[length - 1];
    if (suffix == (unsigned char)'k' || suffix == (unsigned char)'K') { multiplier = UINT64_C(1024); --length; }
    else if (suffix == (unsigned char)'m' || suffix == (unsigned char)'M') { multiplier = UINT64_C(1024) * UINT64_C(1024); --length; }
    else if (suffix == (unsigned char)'g' || suffix == (unsigned char)'G') { multiplier = UINT64_C(1024) * UINT64_C(1024) * UINT64_C(1024); --length; }
    uint64_t amount;
    if (!parse_u64(text, length, &amount) || amount > (uint64_t)SIZE_MAX / multiplier) return 0;
    *value = (size_t)(amount * multiplier);
    return 1;
}

int run_list_add(RunList *list, char *path) {
    if (list->count == list->capacity) {
        size_t next = list->capacity ? list->capacity * 2 : 16;
        if (next < list->capacity || next > SIZE_MAX / sizeof(*list->paths)) return -1;
        char **grown = realloc(list->paths, next * sizeof(*list->paths));
        if (!grown) return -1;
        list->paths = grown;
        list->capacity = next;
    }
    list->paths[list->count++] = path;
    return 0;
}

void run_list_cleanup(RunList *list) {
    for (size_t i = 0; i < list->count; ++i) {
        if (list->paths[i]) {
            (void)lm_remove(list->paths[i]);
            free(list->paths[i]);
        }
    }
    free(list->paths);
    memset(list, 0, sizeof(*list));
}
