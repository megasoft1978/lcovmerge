#include "lm_internal.h"

int writer_flush(Writer *writer) {
    size_t pos = 0;
    while (pos < writer->used) {
        size_t amount = 0;
        if (lm_write(writer->handle, writer->buffer + pos, writer->used - pos,
                     &amount, &interrupted) != 0 || amount == 0) {
            writer->failed = 1;
            return -1;
        }
        pos += amount;
    }
    writer->used = 0;
    return 0;
}

int writer_init(Writer *writer, lm_handle handle) {
    memset(writer, 0, sizeof(*writer));
    writer->capacity = WRITE_BLOCK;
    writer->buffer = malloc(writer->capacity);
    if (!writer->buffer) return -1;
    writer->handle = handle;
    return 0;
}

int writer_bytes(Writer *writer, const void *data, size_t length) {
    const unsigned char *bytes = data;
    while (length) {
        size_t available = writer->capacity - writer->used;
        if (available == 0 && writer_flush(writer) != 0) return -1;
        available = writer->capacity - writer->used;
        size_t amount = length < available ? length : available;
        memcpy(writer->buffer + writer->used, bytes, amount);
        writer->used += amount;
        bytes += amount;
        length -= amount;
    }
    return 0;
}

int writer_text(Writer *writer, const char *text) {
    return writer_bytes(writer, text, strlen(text));
}

int writer_char(Writer *writer, char value) {
    return writer_bytes(writer, &value, 1);
}

int writer_u64(Writer *writer, uint64_t value) {
    static const char digit_pairs[] =
        "00010203040506070809"
        "10111213141516171819"
        "20212223242526272829"
        "30313233343536373839"
        "40414243444546474849"
        "50515253545556575859"
        "60616263646566676869"
        "70717273747576777879"
        "80818283848586878889"
        "90919293949596979899";
    char digits[20];
    char *cursor = digits + sizeof(digits);
    while (value >= UINT64_C(100)) {
        uint64_t quotient = value / UINT64_C(100);
        unsigned remainder = (unsigned)(value - quotient * UINT64_C(100));
        cursor -= 2;
        memcpy(cursor, digit_pairs + (size_t)remainder * 2u, 2u);
        value = quotient;
    }
    unsigned remainder = (unsigned)value;
    if (remainder < 10u) {
        *--cursor = (char)('0' + remainder);
    } else {
        cursor -= 2;
        memcpy(cursor, digit_pairs + (size_t)remainder * 2u, 2u);
    }
    return writer_bytes(writer, cursor, (size_t)(digits + sizeof(digits) - cursor));
}

int writer_format(Writer *writer, const char *format, ...) {
    char buffer[256];
    va_list arguments;
    va_start(arguments, format);
    int count = vsnprintf(buffer, sizeof(buffer), format, arguments);
    va_end(arguments);
    if (count < 0 || (size_t)count >= sizeof(buffer)) return -1;
    return writer_bytes(writer, buffer, (size_t)count);
}

void writer_destroy(Writer *writer) {
    free(writer->buffer);
    free(writer->run_path);
    memset(writer, 0, sizeof(*writer));
}

int utf8_valid(const unsigned char *bytes, size_t length) {
    size_t i = 0;
    const uint64_t high_bits = UINT64_C(0x8080808080808080);
    const uint64_t byte_ones = UINT64_C(0x0101010101010101);
    while (length - i >= sizeof(uint64_t)) {
        uint64_t word;
        memcpy(&word, bytes + i, sizeof(word));
        if (word & high_bits) break;
        if (((word - byte_ones) & ~word & high_bits) != 0) return 0;
        i += sizeof(word);
    }
    while (i < length) {
        unsigned char c = bytes[i++];
        if (c == 0) return 0;
        if (c < 0x80) continue;
        unsigned need;
        uint32_t code;
        if (c >= 0xc2 && c <= 0xdf) { need = 1; code = (uint32_t)(c & 0x1f); }
        else if (c >= 0xe0 && c <= 0xef) { need = 2; code = (uint32_t)(c & 0x0f); }
        else if (c >= 0xf0 && c <= 0xf4) { need = 3; code = (uint32_t)(c & 0x07); }
        else return 0;
        if ((size_t)need > length - i) return 0;
        for (unsigned j = 0; j < need; ++j) {
            unsigned char tail = bytes[i++];
            if ((tail & 0xc0u) != 0x80u) return 0;
            code = (code << 6) | (uint32_t)(tail & 0x3fu);
        }
        if ((need == 1 && code < 0x80u) || (need == 2 && code < 0x800u) ||
            (need == 3 && code < 0x10000u) || code > 0x10ffffu ||
            (code >= 0xd800u && code <= 0xdfffu)) return 0;
    }
    return 1;
}

int line_reader_init(LineReader *reader, lm_handle handle) {
    memset(reader, 0, sizeof(*reader));
    reader->line = malloc(LINE_INITIAL_CAPACITY);
    if (!reader->line) return -1;
    reader->line_capacity = LINE_INITIAL_CAPACITY;
    reader->handle = handle;
    return 0;
}

void line_reader_destroy(LineReader *reader) {
    free(reader->line);
    memset(reader, 0, sizeof(*reader));
}

/* Returns 1 for a line, 0 at EOF, -1 for I/O, and -2 for an invalid line. */
int line_reader_next(LineReader *reader) {
    reader->line_len = 0;
    for (;;) {
        if (reader->pos == reader->len) {
            size_t amount = 0;
            if (lm_read(reader->handle, reader->block, sizeof(reader->block), &amount,
                        &interrupted) != 0) return -1;
            reader->pos = 0;
            reader->len = amount;
            if (amount == 0) {
                if (reader->line_len == 0) return 0;
                ++reader->line_no;
                return utf8_valid((const unsigned char *)reader->line, reader->line_len) ? 1 : -2;
            }
            reader->bytes_read = saturating_add(reader->bytes_read, (uint64_t)amount);
        }
        unsigned char *newline = memchr(reader->block + reader->pos, '\n', reader->len - reader->pos);
        size_t segment = newline ? (size_t)(newline - (reader->block + reader->pos)) : reader->len - reader->pos;
        if (segment > MAX_LINE - reader->line_len) return -3;
        size_t required = reader->line_len + segment + 1u;
        if (required > reader->line_capacity) {
            size_t capacity = reader->line_capacity;
            while (capacity < required) {
                if (capacity > (MAX_LINE + 1u) / 2u) { capacity = MAX_LINE + 1u; break; }
                capacity *= 2u;
            }
            char *grown = realloc(reader->line, capacity);
            if (!grown) return -1;
            reader->line = grown;
            reader->line_capacity = capacity;
        }
        if (segment) memcpy(reader->line + reader->line_len, reader->block + reader->pos, segment);
        reader->line_len += segment;
        reader->pos += segment + (newline ? 1u : 0u);
        if (newline) {
            ++reader->line_no;
            if (!utf8_valid((const unsigned char *)reader->line, reader->line_len)) return -2;
            reader->line[reader->line_len] = '\0';
            return 1;
        }
    }
}
