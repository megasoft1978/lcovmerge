#include "lm_internal.h"

static int ensure_source_record(OutputState *output) {
    if (output->source_written) return 0;
    output->source_written = 1;
    return writer_text(&output->writer, "SF:") == 0 &&
           writer_text(&output->writer, output->path) == 0 &&
           writer_text(&output->writer, "\n") == 0 ? 0 : -1;
}

static int start_output_file(OutputState *output, const char *path) {
    if (output->active) return 0;
    output->path = duplicate_string(path);
    if (!output->path) return -1;
    output->active = 1;
    output->source_written = 0;
    output->fnf = output->fnh = output->lf = output->lh = 0;
    output->brf = output->brh = output->mcf = output->mch = 0;
    output->have_fn_group = 0;
    output->fn_group_hit = 0;
    free(output->fn_group_name);
    output->fn_group_name = NULL;
    return 0;
}

static int same_function_group(const OutputState *output, const Record *record) {
    return output->have_fn_group && output->fn_group_a == record->a &&
           output->fn_group_b == record->b && output->fn_group_c == record->c &&
           strcmp(output->fn_group_name ? output->fn_group_name : "", record->extra) == 0;
}

static void finish_function_group(OutputState *output) {
    if (output->have_fn_group && output->fn_group_hit)
        output->fnh = saturating_add(output->fnh, UINT64_C(1));
    output->have_fn_group = 0;
    output->fn_group_hit = 0;
}

static int add_function_hit(OutputState *output, const Record *record) {
    if (!same_function_group(output, record)) {
        finish_function_group(output);
        free(output->fn_group_name);
        output->fn_group_name = NULL;
        output->fn_group_name = duplicate_string(record->extra);
        if (!output->fn_group_name) return -1;
        output->fn_group_a = record->a;
        output->fn_group_b = record->b;
        output->fn_group_c = record->c;
        output->have_fn_group = 1;
    }
    if (record->value != 0) output->fn_group_hit = 1;
    return 0;
}

int output_record(OutputState *output, const Record *record) {
    /* TN/SF markers delimit paths, so coverpoint rows need no repeated path compare. */
    int begins_source_section = record->type == REC_TN || record->type == REC_SECTION;
    if (begins_source_section && output->active && strcmp(output->path, record->path) != 0 &&
        output_finish_file(output) != 0) return -1;
    if (start_output_file(output, record->path) != 0) return -1;
    if (record->type == REC_TN) {
        if (record->value != 0 || record->text[0] == '\0') return 0;
        return writer_text(&output->writer, "TN:") == 0 &&
               writer_text(&output->writer, record->text) == 0 &&
               writer_text(&output->writer, "\n") == 0 ? 0 : -1;
    }
    if (record->type == REC_SECTION) return ensure_source_record(output);
    if (ensure_source_record(output) != 0) return -1;
    switch (record->type) {
        case REC_GROUP:
            output->fnf = saturating_add(output->fnf, UINT64_C(1));
            if (record->flags & FLAG_FN_NEW) {
                if (writer_text(&output->writer, "FNL:") != 0 ||
                    writer_u64(&output->writer, record->c) != 0 ||
                    writer_char(&output->writer, ',') != 0 ||
                    writer_u64(&output->writer, record->a) != 0) return -1;
                if (record->b != 0 && (writer_char(&output->writer, ',') != 0 ||
                    writer_u64(&output->writer, record->b) != 0)) return -1;
                return writer_char(&output->writer, '\n');
            }
            return 0;
        case REC_FN:
            if (record->flags & FLAG_FN_NEW) {
                if (add_function_hit(output, record) != 0) return -1;
                if (writer_text(&output->writer, "FNA:") != 0 ||
                    writer_u64(&output->writer, record->c) != 0 ||
                    writer_char(&output->writer, ',') != 0 ||
                    writer_u64(&output->writer, record->value) != 0 ||
                    writer_char(&output->writer, ',') != 0) return -1;
                return writer_text(&output->writer, record->text) == 0 &&
                       writer_text(&output->writer, "\n") == 0 ? 0 : -1;
            }
            if (writer_text(&output->writer, "FN:") != 0 ||
                writer_u64(&output->writer, record->a) != 0 ||
                writer_char(&output->writer, ',') != 0) return -1;
            if (record->b != 0 && (writer_u64(&output->writer, record->b) != 0 ||
                writer_char(&output->writer, ',') != 0)) return -1;
            return writer_text(&output->writer, record->text) == 0 && writer_text(&output->writer, "\n") == 0 ? 0 : -1;
        case REC_FNDA:
            if (add_function_hit(output, record) != 0) return -1;
            if (writer_text(&output->writer, "FNDA:") != 0 ||
                writer_u64(&output->writer, record->value) != 0 ||
                writer_char(&output->writer, ',') != 0) return -1;
            return writer_text(&output->writer, record->text) == 0 && writer_text(&output->writer, "\n") == 0 ? 0 : -1;
        case REC_BRDA: {
            if (writer_text(&output->writer, "BRDA:") != 0 ||
                writer_u64(&output->writer, record->a) != 0 ||
                writer_char(&output->writer, ',') != 0) return -1;
            if ((record->flags & FLAG_BR_EXCEPTION) && writer_text(&output->writer, "e") != 0) return -1;
            if ((record->flags & FLAG_BR_FALLTHROUGH) && writer_text(&output->writer, "f") != 0) return -1;
            if ((record->flags & FLAG_BR_UNREACHABLE) && writer_text(&output->writer, "U") != 0) return -1;
            if (writer_u64(&output->writer, record->b) != 0 || writer_char(&output->writer, ',') != 0) return -1;
            if (record->flags & FLAG_BRANCH_TEXT) {
                if (writer_text(&output->writer, record->text) != 0) return -1;
            } else if (writer_u64(&output->writer, record->c) != 0) return -1;
            if (record->flags & FLAG_DASH) {
                if (writer_text(&output->writer, ",-\n") != 0) return -1;
            } else if (writer_char(&output->writer, ',') != 0 ||
                       writer_u64(&output->writer, record->value) != 0 ||
                       writer_char(&output->writer, '\n') != 0) return -1;
            if (!(record->flags & FLAG_BR_UNREACHABLE)) {
                output->brf = saturating_add(output->brf, UINT64_C(1));
                if (record->value != 0 && !(record->flags & FLAG_DASH))
                    output->brh = saturating_add(output->brh, UINT64_C(1));
            }
            return 0;
        }
        case REC_MCDC:
            if (writer_text(&output->writer, "MCDC:") != 0 ||
                writer_u64(&output->writer, record->a) != 0 ||
                writer_char(&output->writer, ',') != 0 ||
                ((record->flags & FLAG_MCDC_UNREACHABLE) && writer_char(&output->writer, 'U') != 0) ||
                writer_u64(&output->writer, record->b) != 0 ||
                writer_char(&output->writer, ',') != 0 ||
                writer_char(&output->writer, (record->flags & FLAG_MCDC_TRUE) ? 't' : 'f') != 0 ||
                writer_char(&output->writer, ',') != 0 ||
                writer_u64(&output->writer, record->value) != 0 ||
                writer_char(&output->writer, ',') != 0 ||
                writer_u64(&output->writer, record->c) != 0 ||
                writer_char(&output->writer, ',') != 0 ||
                writer_text(&output->writer, record->text) != 0 || writer_text(&output->writer, "\n") != 0) return -1;
            if (!(record->flags & FLAG_MCDC_UNREACHABLE)) {
                output->mcf = saturating_add(output->mcf, UINT64_C(1));
                if (record->value != 0) output->mch = saturating_add(output->mch, UINT64_C(1));
            }
            return 0;
        case REC_DA:
            if (writer_text(&output->writer, "DA:") != 0 ||
                writer_u64(&output->writer, record->a) != 0 ||
                writer_char(&output->writer, ',') != 0 ||
                writer_u64(&output->writer, record->value) != 0) return -1;
            if (*record->extra && (writer_text(&output->writer, ",") != 0 || writer_text(&output->writer, record->extra) != 0)) return -1;
            if (writer_text(&output->writer, "\n") != 0) return -1;
            output->lf = saturating_add(output->lf, UINT64_C(1));
            if (record->value != 0) output->lh = saturating_add(output->lh, UINT64_C(1));
            return 0;
        case REC_EXT:
            return writer_text(&output->writer, record->text) == 0 && writer_text(&output->writer, "\n") == 0 ? 0 : -1;
        default:
            return -1;
    }
}

int output_finish_file(OutputState *output) {
    if (!output->active) return 0;
    if (ensure_source_record(output) != 0) return -1;
    finish_function_group(output);
    static const char *const labels[] = {"FNF:", "FNH:", "BRF:", "BRH:", "MCF:", "MCH:", "LF:", "LH:"};
    const uint64_t values[] = {output->fnf, output->fnh, output->brf, output->brh,
                               output->mcf, output->mch, output->lf, output->lh};
    for (size_t i = 0; i < sizeof(values) / sizeof(values[0]); ++i)
        if (writer_text(&output->writer, labels[i]) != 0 ||
            writer_u64(&output->writer, values[i]) != 0 ||
            writer_char(&output->writer, '\n') != 0) return -1;
    if (writer_text(&output->writer, "end_of_record\n") != 0) return -1;
    output->total_fnf = saturating_add(output->total_fnf, output->fnf);
    output->total_fnh = saturating_add(output->total_fnh, output->fnh);
    output->total_brf = saturating_add(output->total_brf, output->brf);
    output->total_brh = saturating_add(output->total_brh, output->brh);
    output->total_lf = saturating_add(output->total_lf, output->lf);
    output->total_lh = saturating_add(output->total_lh, output->lh);
    output->active = 0;
    free(output->path);
    output->path = NULL;
    free(output->fn_group_name);
    output->fn_group_name = NULL;
    output->have_fn_group = 0;
    output->fn_group_hit = 0;
    return 0;
}

static const char *input_name_for(const Options *options, uint32_t input_id) {
    return (size_t)input_id < options->inputs.count ? options->inputs.items[input_id] : "<input>";
}

int merge_aggregate(Record *aggregate, const Record *next, const Options *options,
                           FILE *diagnostics) {
    if (next->type == REC_TN) {
        if (strcmp(aggregate->text, next->text) != 0) aggregate->value = 1;
    } else if (next->type == REC_FNDA || next->type == REC_MCDC ||
        (next->type == REC_FN && (next->flags & FLAG_FN_NEW))) {
        aggregate->value = saturating_add(aggregate->value, next->value);
        if (next->type == REC_MCDC && strcmp(aggregate->text, next->text) != 0) {
            fprintf(diagnostics, "lcovmerge: %s:%" PRIu64 ": MC/DC expression mismatch for %s:%" PRIu64 " group %" PRIu64 " index %" PRIu64 "\n",
                    input_name_for(options, next->input_id), next->origin_line,
                    next->path, next->a, next->b, next->c);
            if (strcmp(next->text, aggregate->text) < 0) {
                size_t length = next->text_length;
                memcpy(aggregate->text, next->text, length + 1);
                aggregate->text_length = next->text_length;
            }
        }
    } else if (next->type == REC_BRDA) {
        if (aggregate->flags & FLAG_DASH) {
            if (!(next->flags & FLAG_DASH)) {
                aggregate->value = next->value;
                aggregate->flags &= (uint8_t)~FLAG_DASH;
            }
        } else if (!(next->flags & FLAG_DASH)) aggregate->value = saturating_add(aggregate->value, next->value);
    } else if (next->type == REC_DA) {
        aggregate->value = saturating_add(aggregate->value, next->value);
        if (!aggregate->extra[0]) {
            size_t length = next->extra_length;
            memcpy(aggregate->extra, next->extra, length + 1);
            aggregate->extra_length = next->extra_length;
        } else if (next->extra[0]) {
            int order = FIELD_COMPARE(aggregate, next, extra);
            if (order != 0) {
                fprintf(diagnostics, "lcovmerge: %s:%" PRIu64 ": checksum mismatch for %s:%" PRIu64 "\n",
                        input_name_for(options, next->input_id), next->origin_line,
                        next->path, next->a);
                if (options->strict_checksum) return 2;
                if (order > 0) {
                    size_t length = next->extra_length;
                    memcpy(aggregate->extra, next->extra, length + 1);
                    aggregate->extra_length = next->extra_length;
                }
            }
        }
    }
    return 0;
}

static int output_directory(const char *filename, char **directory_out) {
    char *copy = duplicate_string(filename);
    if (!copy) return -1;
    char *separator = NULL;
    for (char *cursor = copy; *cursor; ++cursor)
        if (*cursor == '/' || *cursor == '\\') separator = cursor;
    if (!separator) {
        free(copy);
        *directory_out = duplicate_string(".");
        return *directory_out ? 0 : -1;
    }
    if (separator == copy || (separator == copy + 2 && copy[1] == ':')) separator[1] = '\0';
    else *separator = '\0';
    *directory_out = copy;
    return 0;
}

int create_output_temp(const char *output_path, OutputFile *file) {
    char *directory = NULL;
    if (output_directory(output_path, &directory) != 0) {
        fprintf(stderr, "lcovmerge: %s:0: cannot determine output parent directory; "
                        "ensure it exists and is writable\n", output_path);
        return -1;
    }
    unsigned long system_error = 0;
    if (lm_create_temp(directory, &file->path, &file->handle, &system_error) != 0) {
        char reason[256];
        lm_format_error(system_error, reason, sizeof(reason));
        fprintf(stderr, "lcovmerge: %s:0: cannot create output temporary file: %s; "
                        "parent directory '%s' must exist and be writable\n",
                output_path, reason, directory);
        free(directory);
        return -1;
    }
    file->owns_handle = 1;
    free(directory);
    return 0;
}
