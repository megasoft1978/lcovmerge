#include "lm_internal.h"

int wildcard_match(const char *pattern, const char *text) {
    const char *star = NULL;
    const char *retry = NULL;
    while (*text) {
        if (*pattern == '?' || *pattern == *text) { ++pattern; ++text; continue; }
        if (*pattern == '[') {
            const char *end = strchr(pattern + 1, ']');
            if (end) {
                const char *item = pattern + 1;
                int invert = 0, matched = 0;
                if (item < end && (*item == '!' || *item == '^')) { invert = 1; ++item; }
                while (item < end) {
                    if (item + 2 < end && item[1] == '-') {
                        if (*text >= item[0] && *text <= item[2]) matched = 1;
                        item += 3;
                    } else {
                        if (*text == *item) matched = 1;
                        ++item;
                    }
                }
                if ((matched != 0) != (invert != 0)) { pattern = end + 1; ++text; continue; }
            }
        }
        if (*pattern == '*') { star = pattern++; retry = text; continue; }
        if (star) { pattern = star + 1; text = ++retry; continue; }
        return 0;
    }
    while (*pattern == '*') ++pattern;
    return *pattern == '\0';
}

static int prefix_match(const char *path, const char *prefix, size_t *matched) {
    size_t length = strlen(prefix);
    if (length == 0) { *matched = 0; return 1; }
    if (strncmp(path, prefix, length) != 0) return 0;
    if (path[length] && path[length] != '/' && path[length] != '\\' &&
        prefix[length - 1] != '/' && prefix[length - 1] != '\\') return 0;
    *matched = length;
    while (path[length] == '/' || path[length] == '\\') ++length;
    *matched = length;
    return 1;
}

char *rewrite_path(const Options *options, const char *path) {
    char *current = duplicate_string(path);
    if (!current) return NULL;
    for (size_t i = 0; i < options->rebase_count; ++i) {
        size_t matched = 0;
        if (prefix_match(current, options->rebases[i].old_prefix, &matched)) {
            size_t new_length = strlen(options->rebases[i].new_prefix);
            size_t rest = strlen(current + matched);
            size_t old_length = strlen(options->rebases[i].old_prefix);
            char separator = current[old_length] == '\\' ? '\\' : '/';
            const char *new_prefix = options->rebases[i].new_prefix;
            if (strchr(new_prefix, '\\') && !strchr(new_prefix, '/')) separator = '\\';
            else if (strchr(new_prefix, '/') && !strchr(new_prefix, '\\')) separator = '/';
            int add_separator = rest != 0 && new_length != 0 &&
                options->rebases[i].new_prefix[new_length - 1] != '/' &&
                options->rebases[i].new_prefix[new_length - 1] != '\\';
            if (new_length > SIZE_MAX - rest - (size_t)add_separator - 1) { free(current); return NULL; }
            char *next = malloc(new_length + (size_t)add_separator + rest + 1);
            if (!next) { free(current); return NULL; }
            memcpy(next, options->rebases[i].new_prefix, new_length);
            size_t cursor = new_length;
            if (add_separator) next[cursor++] = separator;
            for (size_t character = 0; character < rest; ++character) {
                char value = current[matched + character];
                next[cursor + character] = value == '/' || value == '\\' ? separator : value;
            }
            next[cursor + rest] = '\0';
            free(current);
            current = next;
        }
    }
    if (options->prefix_strip) {
        size_t matched = 0;
        if (prefix_match(current, options->prefix_strip, &matched)) {
            char *next = duplicate_string(current + matched);
            if (!next) { free(current); return NULL; }
            free(current);
            current = next;
        }
    }
    if (strlen(current) > MAX_LINE) { free(current); return NULL; }
    return current;
}

int path_selected(const Options *options, const char *path) {
    int included = options->includes.count == 0;
    for (size_t i = 0; i < options->includes.count; ++i)
        if (wildcard_match(options->includes.items[i], path)) included = 1;
    for (size_t i = 0; i < options->excludes.count; ++i)
        if (wildcard_match(options->excludes.items[i], path)) return 0;
    return included;
}
