#include "lm_internal.h"

static int new_function_event(const Record *record) {
    return (record->type == REC_GROUP || record->type == REC_FN) &&
           (record->flags & FLAG_FN_NEW) != 0;
}

static int legacy_function_event(const Record *record) {
    return (record->type == REC_GROUP || record->type == REC_FN) &&
           (record->flags & FLAG_FN_NEW) == 0;
}

static unsigned record_order(const Record *record) {
    if (record->type == REC_TN) return 0u;
    if (record->type == REC_SECTION) return 1u;
    if (new_function_event(record)) return 3u;
    if (legacy_function_event(record)) return 4u;
    if (record->type == REC_FNDA) return 5u;
    if (record->type == REC_DA) return 6u;
    if (record->type == REC_BRDA) return 7u;
    if (record->type == REC_MCDC) return 8u;
    return 9u;
}

int record_key_compare_same_path(const Record *left, const Record *right) {
    int result = 0;
    int left_new = new_function_event(left);
    int right_new = new_function_event(right);
    if (left_new && right_new) {
        if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
        if (left->b != right->b) return (left->b > right->b) - (left->b < right->b);
        if (left->c != right->c) return (left->c > right->c) - (left->c < right->c);
        if (left->type != right->type) return left->type == REC_GROUP ? -1 : 1;
        if (left->type == REC_GROUP) return string_compare(left->extra, right->extra);
        return string_compare(left->text, right->text);
    }
    if (legacy_function_event(left) && legacy_function_event(right)) {
        if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
        if (left->b != right->b) return (left->b > right->b) - (left->b < right->b);
        if (left->c != right->c) return (left->c > right->c) - (left->c < right->c);
        result = string_compare(left->extra, right->extra);
        if (result) return result;
        if (left->type != right->type) return left->type == REC_GROUP ? -1 : 1;
        return string_compare(left->text, right->text);
    }
    unsigned left_order = record_order(left), right_order = record_order(right);
    if (left_order != right_order) return (left_order > right_order) - (left_order < right_order);
    if (left->type != right->type) return (left->type > right->type) - (left->type < right->type);
    switch (left->type) {
        case REC_TN:
        case REC_EXT:
            return string_compare(left->text, right->text);
        case REC_SECTION:
            return 0;
        case REC_GROUP:
            if ((left->flags & FLAG_FN_NEW) != (right->flags & FLAG_FN_NEW))
                return (left->flags > right->flags) - (left->flags < right->flags);
            if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
            if (left->b != right->b) return (left->b > right->b) - (left->b < right->b);
            if (left->c != right->c) return (left->c > right->c) - (left->c < right->c);
            return string_compare(left->extra, right->extra);
        case REC_FN:
            if ((left->flags & FLAG_FN_NEW) != (right->flags & FLAG_FN_NEW))
                return (left->flags > right->flags) - (left->flags < right->flags);
            if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
            if (left->b != right->b) return (left->b > right->b) - (left->b < right->b);
            if (left->c != right->c) return (left->c > right->c) - (left->c < right->c);
            result = string_compare(left->extra, right->extra);
            return result ? result : string_compare(left->text, right->text);
        case REC_FNDA:
            if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
            if (left->b != right->b) return (left->b > right->b) - (left->b < right->b);
            if (left->c != right->c) return (left->c > right->c) - (left->c < right->c);
            result = string_compare(left->extra, right->extra);
            return result ? result : string_compare(left->text, right->text);
        case REC_BRDA:
            if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
            if (left->b != right->b) return (left->b > right->b) - (left->b < right->b);
            if ((left->flags & FLAG_NUMERIC_BRANCH) != (right->flags & FLAG_NUMERIC_BRANCH))
                return (left->flags & FLAG_NUMERIC_BRANCH) ? -1 : 1;
            if (left->flags & FLAG_NUMERIC_BRANCH) {
                if (left->c != right->c) return (left->c > right->c) - (left->c < right->c);
            } else {
                result = string_compare(left->text, right->text);
                if (result) return result;
            }
            if ((left->flags & (FLAG_BR_EXCEPTION | FLAG_BR_FALLTHROUGH | FLAG_BR_UNREACHABLE)) !=
                (right->flags & (FLAG_BR_EXCEPTION | FLAG_BR_FALLTHROUGH | FLAG_BR_UNREACHABLE)))
                return (left->flags > right->flags) - (left->flags < right->flags);
            return 0;
        case REC_MCDC:
            if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
            if (left->b != right->b) return (left->b > right->b) - (left->b < right->b);
            if (left->c != right->c) return (left->c > right->c) - (left->c < right->c);
            if (left->flags != right->flags) return (left->flags > right->flags) - (left->flags < right->flags);
            return 0;
        case REC_DA:
            if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
            return 0;
        default:
            return 0;
    }
}

int record_key_compare(const Record *left, const Record *right) {
    if (left->path != right->path &&
        (left->path_id == 0 || right->path_id == 0 || left->path_id != right->path_id)) {
        int result = string_compare(left->path, right->path);
        if (result) return result;
    }
    return record_key_compare_same_path(left, right);
}

int record_total_compare(const Record *left, const Record *right) {
    int result = record_key_compare(left, right);
    if (result) return result;
    if (left->type == REC_DA) {
        result = string_compare(left->extra, right->extra);
        if (result) return result;
    }
    if (left->flags != right->flags) return (left->flags > right->flags) - (left->flags < right->flags);
    if (left->value != right->value) return (left->value > right->value) - (left->value < right->value);
    result = string_compare(left->text, right->text);
    if (result) return result;
    result = string_compare(left->extra, right->extra);
    if (result) return result;
    if (left->input_id != right->input_id) return (left->input_id > right->input_id) - (left->input_id < right->input_id);
    return (left->origin_line > right->origin_line) - (left->origin_line < right->origin_line);
}

int record_total_compare_same_path(const Record *left, const Record *right) {
    if (left->type == REC_DA && right->type == REC_DA) {
        if (left->a != right->a) return (left->a > right->a) - (left->a < right->a);
        int result = string_compare(left->extra, right->extra);
        if (result) return result;
        if (left->flags != right->flags) return (left->flags > right->flags) - (left->flags < right->flags);
        if (left->value != right->value) return (left->value > right->value) - (left->value < right->value);
        if (left->input_id != right->input_id)
            return (left->input_id > right->input_id) - (left->input_id < right->input_id);
        return (left->origin_line > right->origin_line) - (left->origin_line < right->origin_line);
    }
    int result = record_key_compare_same_path(left, right);
    if (result) return result;
    if (left->type == REC_DA) {
        result = string_compare(left->extra, right->extra);
        if (result) return result;
    }
    if (left->flags != right->flags) return (left->flags > right->flags) - (left->flags < right->flags);
    if (left->value != right->value) return (left->value > right->value) - (left->value < right->value);
    result = string_compare(left->text, right->text);
    if (result) return result;
    result = string_compare(left->extra, right->extra);
    if (result) return result;
    if (left->input_id != right->input_id) return (left->input_id > right->input_id) - (left->input_id < right->input_id);
    return (left->origin_line > right->origin_line) - (left->origin_line < right->origin_line);
}

int record_qsort_compare(const void *left, const void *right) {
    return record_total_compare((const Record *)left, (const Record *)right);
}

int same_key(const Record *left, const Record *right) {
    /* Unknown extensions have no defined merge operator; preserve every row. */
    if (left->type == REC_EXT || right->type == REC_EXT) return 0;
    /* Testcase names describe inputs, not the merged coverage identity. */
    if (left->type == REC_TN && right->type == REC_TN)
        return strcmp(left->path, right->path) == 0;
    return record_key_compare(left, right) == 0;
}
