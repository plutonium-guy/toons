const std = @import("std");
const json = std.json;

const Allocator = std.mem.Allocator;
const render_allocator = std.heap.c_allocator;

pub fn renderJsonText(allocator: Allocator, value: json.Value, delimiter: u8) ![]u8 {
    if (delimiter != ',' and delimiter != '|' and delimiter != '\t') {
        return error.InvalidDelimiter;
    }

    var buffer = std.ArrayList(u8).empty;
    defer buffer.deinit(allocator);
    try renderRoot(&buffer, allocator, value, delimiter);
    return buffer.toOwnedSlice(allocator);
}

pub fn parseText(allocator: Allocator, input: []const u8) !json.Value {
    var lines = try collectLines(allocator, input);
    defer lines.deinit(allocator);

    var parser = TextParser{
        .allocator = allocator,
        .lines = lines.items,
        .index = 0,
    };
    return parser.parse();
}

const TextLine = struct {
    number: usize,
    indent: usize,
    content: []const u8,
};

const ArrayHeader = struct {
    key: ?[]const u8,
    length: usize,
    delimiter: u8,
    fields: ?[]const []const u8,
};

const SplitField = struct {
    lhs: []const u8,
    rhs: []const u8,
};

const ParsedField = struct {
    key: []const u8,
    value: json.Value,
};

fn collectLines(allocator: Allocator, input: []const u8) !std.ArrayList(TextLine) {
    var lines = std.ArrayList(TextLine).empty;
    var start: usize = 0;
    var line_number: usize = 1;
    while (start <= input.len) {
        const end = std.mem.indexOfScalarPos(u8, input, start, '\n') orelse input.len;
        var line = input[start..end];
        if (line.len > 0 and line[line.len - 1] == '\r') {
            line = line[0 .. line.len - 1];
        }
        if (!isBlankLine(line)) {
            const indent = countIndent(line);
            try lines.append(allocator, .{
                .number = line_number,
                .indent = indent,
                .content = line[indent..],
            });
        }
        if (end == input.len) break;
        start = end + 1;
        line_number += 1;
    }
    return lines;
}

fn isBlankLine(line: []const u8) bool {
    for (line) |char| {
        if (char != ' ' and char != '\t' and char != '\r') return false;
    }
    return true;
}

fn countIndent(line: []const u8) usize {
    var count: usize = 0;
    while (count < line.len and line[count] == ' ') : (count += 1) {}
    return count;
}

fn renderRoot(buffer: *std.ArrayList(u8), allocator: Allocator, value: json.Value, delimiter: u8) anyerror!void {
    switch (value) {
        .object => |object| try renderObjectLines(buffer, allocator, object, 0, delimiter),
        .array => |array| try renderArrayLines(buffer, allocator, null, array, 0, delimiter),
        else => try renderPrimitive(buffer, value, delimiter),
    }
}

fn renderObjectLines(
    buffer: *std.ArrayList(u8),
    allocator: Allocator,
    object: json.ObjectMap,
    indent: usize,
    delimiter: u8,
) anyerror!void {
    var it = object.iterator();
    while (it.next()) |entry| {
        try renderFieldLines(buffer, allocator, entry.key_ptr.*, entry.value_ptr.*, indent, delimiter);
    }
}

fn renderFieldLines(
    buffer: *std.ArrayList(u8),
    allocator: Allocator,
    key: []const u8,
    value: json.Value,
    indent: usize,
    delimiter: u8,
) anyerror!void {
    switch (value) {
        .array => |array| try renderArrayLines(buffer, allocator, key, array, indent, delimiter),
        .object => |object| {
            try beginLine(buffer, indent);
            try renderKey(buffer, key, delimiter);
            try buffer.append(allocator, ':');
            if (object.count() != 0) {
                var child = std.ArrayList(u8).empty;
                defer child.deinit(allocator);
                try renderObjectLines(&child, allocator, object, 0, delimiter);
                try appendIndentedBlock(buffer, allocator, child.items, indent + 2);
            }
        },
        else => {
            try beginLine(buffer, indent);
            try renderKey(buffer, key, delimiter);
            try buffer.appendSlice(allocator, ": ");
            try renderPrimitive(buffer, value, delimiter);
        },
    }
}

fn renderArrayLines(
    buffer: *std.ArrayList(u8),
    allocator: Allocator,
    key: ?[]const u8,
    array: json.Array,
    indent: usize,
    delimiter: u8,
) anyerror!void {
    if (arrayIsPrimitive(array.items)) {
        try beginLine(buffer, indent);
        if (key) |actual| try renderKey(buffer, actual, delimiter);
        try writeLengthMarker(buffer, allocator, array.items.len, delimiter);
        try buffer.append(allocator, ':');
        if (array.items.len > 0) {
            try buffer.append(allocator, ' ');
            for (array.items, 0..) |item, index| {
                if (index > 0) try buffer.append(allocator, delimiter);
                try renderPrimitive(buffer, item, delimiter);
            }
        }
        return;
    }

    if (try tabularFields(allocator, array)) |fields| {
        defer allocator.free(fields);
        try beginLine(buffer, indent);
        if (key) |actual| try renderKey(buffer, actual, delimiter);
        try writeLengthMarker(buffer, allocator, array.items.len, delimiter);
        try buffer.append(allocator, '{');
        for (fields, 0..) |field, index| {
            if (index > 0) try buffer.append(allocator, delimiter);
            try renderKey(buffer, field, delimiter);
        }
        try buffer.appendSlice(allocator, "}:");
        for (array.items) |item| {
            try beginLine(buffer, indent + 2);
            const object = item.object;
            for (fields, 0..) |field, index| {
                if (index > 0) try buffer.append(allocator, delimiter);
                try renderPrimitive(buffer, object.get(field).?, delimiter);
            }
        }
        return;
    }

    try beginLine(buffer, indent);
    if (key) |actual| try renderKey(buffer, actual, delimiter);
    try writeLengthMarker(buffer, allocator, array.items.len, delimiter);
    try buffer.append(allocator, ':');
    for (array.items) |item| {
        try renderArrayItemLines(buffer, allocator, item, indent + 2, delimiter);
    }
}

fn renderArrayItemLines(
    buffer: *std.ArrayList(u8),
    allocator: Allocator,
    value: json.Value,
    indent: usize,
    delimiter: u8,
) anyerror!void {
    switch (value) {
        .object => |object| {
            if (object.count() == 0) {
                try beginLine(buffer, indent);
                try buffer.append(allocator, '-');
                return;
            }

            var it = object.iterator();
            const first = it.next().?;
            try beginLine(buffer, indent);
            try buffer.appendSlice(allocator, "- ");
            try renderInlineField(buffer, allocator, first.key_ptr.*, first.value_ptr.*, indent, delimiter);
            while (it.next()) |entry| {
                try renderFieldLines(buffer, allocator, entry.key_ptr.*, entry.value_ptr.*, indent + 2, delimiter);
            }
        },
        .array => |array| {
            var child = std.ArrayList(u8).empty;
            defer child.deinit(allocator);
            try renderArrayLines(&child, allocator, null, array, 0, delimiter);
            try beginLine(buffer, indent);
            try buffer.appendSlice(allocator, "- ");
            try appendInlineBlock(buffer, allocator, child.items, indent + 2);
        },
        else => {
            try beginLine(buffer, indent);
            try buffer.appendSlice(allocator, "- ");
            try renderPrimitive(buffer, value, delimiter);
        },
    }
}

fn renderInlineField(
    buffer: *std.ArrayList(u8),
    allocator: Allocator,
    key: []const u8,
    value: json.Value,
    indent: usize,
    delimiter: u8,
) anyerror!void {
    switch (value) {
        .array => |array| {
            var child = std.ArrayList(u8).empty;
            defer child.deinit(allocator);
            try renderArrayLines(&child, allocator, key, array, 0, delimiter);
            try appendInlineBlock(buffer, allocator, child.items, indent + 4);
        },
        .object => |object| {
            try renderKey(buffer, key, delimiter);
            try buffer.append(allocator, ':');
            if (object.count() != 0) {
                var child = std.ArrayList(u8).empty;
                defer child.deinit(allocator);
                try renderObjectLines(&child, allocator, object, 0, delimiter);
                try appendIndentedBlock(buffer, allocator, child.items, indent + 2);
            }
        },
        else => {
            try renderKey(buffer, key, delimiter);
            try buffer.appendSlice(allocator, ": ");
            try renderPrimitive(buffer, value, delimiter);
        },
    }
}

fn appendInlineBlock(
    buffer: *std.ArrayList(u8),
    allocator: Allocator,
    block: []const u8,
    rest_indent: usize,
) anyerror!void {
    var start: usize = 0;
    var first = true;
    while (start <= block.len) {
        const end = std.mem.indexOfScalarPos(u8, block, start, '\n') orelse block.len;
        const line = block[start..end];
        if (first) {
            try buffer.appendSlice(allocator, line);
            first = false;
        } else {
            try buffer.append(allocator, '\n');
            try buffer.appendNTimes(allocator, ' ', rest_indent);
            try buffer.appendSlice(allocator, line);
        }
        if (end == block.len) break;
        start = end + 1;
    }
}

fn appendIndentedBlock(
    buffer: *std.ArrayList(u8),
    allocator: Allocator,
    block: []const u8,
    indent: usize,
) !void {
    if (block.len == 0) return;
    var start: usize = 0;
    while (start <= block.len) {
        const end = std.mem.indexOfScalarPos(u8, block, start, '\n') orelse block.len;
        const line = block[start..end];
        try buffer.append(allocator, '\n');
        try buffer.appendNTimes(allocator, ' ', indent);
        try buffer.appendSlice(allocator, line);
        if (end == block.len) break;
        start = end + 1;
    }
}

fn beginLine(buffer: *std.ArrayList(u8), indent: usize) !void {
    if (buffer.items.len > 0) try buffer.append(render_allocator, '\n');
    try buffer.appendNTimes(render_allocator, ' ', indent);
}

fn writeLengthMarker(buffer: *std.ArrayList(u8), allocator: Allocator, length: usize, delimiter: u8) !void {
    if (delimiter == ',') {
        try buffer.writer(allocator).print("[{}]", .{length});
    } else {
        try buffer.writer(allocator).print("[{}{c}]", .{ length, delimiter });
    }
}

fn renderKey(buffer: *std.ArrayList(u8), value: []const u8, delimiter: u8) !void {
    if (canLeaveUnquoted(value, delimiter, true)) {
        try buffer.appendSlice(render_allocator, value);
        return;
    }
    try quoteString(buffer, value);
}

fn renderPrimitive(buffer: *std.ArrayList(u8), value: json.Value, delimiter: u8) !void {
    switch (value) {
        .null => try buffer.appendSlice(render_allocator, "null"),
        .bool => |inner| try buffer.appendSlice(render_allocator, if (inner) "true" else "false"),
        .integer => |inner| try buffer.writer(render_allocator).print("{}", .{inner}),
        .float => |inner| try buffer.writer(render_allocator).print("{d}", .{inner}),
        .number_string => |inner| try buffer.appendSlice(render_allocator, inner),
        .string => |inner| {
            if (canLeaveUnquoted(inner, delimiter, false)) {
                try buffer.appendSlice(render_allocator, inner);
            } else {
                try quoteString(buffer, inner);
            }
        },
        else => return error.ExpectedPrimitive,
    }
}

fn quoteString(buffer: *std.ArrayList(u8), value: []const u8) !void {
    try buffer.append(render_allocator, '"');
    for (value) |char| {
        switch (char) {
            '\\' => try buffer.appendSlice(render_allocator, "\\\\"),
            '"' => try buffer.appendSlice(render_allocator, "\\\""),
            '\n' => try buffer.appendSlice(render_allocator, "\\n"),
            '\r' => try buffer.appendSlice(render_allocator, "\\r"),
            '\t' => try buffer.appendSlice(render_allocator, "\\t"),
            else => {
                if (char < 0x20) return error.UnsupportedControlCharacter;
                try buffer.append(render_allocator, char);
            },
        }
    }
    try buffer.append(render_allocator, '"');
}

fn canLeaveUnquoted(value: []const u8, delimiter: u8, for_key: bool) bool {
    if (value.len == 0) return false;
    if (std.mem.trim(u8, value, " ").len != value.len) return false;
    if (std.mem.eql(u8, value, "true") or std.mem.eql(u8, value, "false") or std.mem.eql(u8, value, "null")) return false;
    if (value[0] == '-') return false;
    if (looksNumberLike(value)) return false;
    for (value) |char| {
        if (char == ':' or char == '"' or char == '\\' or char == '[' or char == ']' or char == '{' or char == '}') return false;
        if (char == '\n' or char == '\r' or char == '\t') return false;
        if (!for_key and char == delimiter) return false;
    }
    return true;
}

fn looksNumberLike(value: []const u8) bool {
    if (value.len == 0) return false;
    var index: usize = 0;
    if (value[index] == '-') {
        index += 1;
        if (index == value.len) return false;
    }
    if (!std.ascii.isDigit(value[index])) return false;
    if (value[index] == '0') {
        index += 1;
    } else {
        while (index < value.len and std.ascii.isDigit(value[index])) : (index += 1) {}
    }
    if (index < value.len and value[index] == '.') {
        index += 1;
        if (index == value.len or !std.ascii.isDigit(value[index])) return false;
        while (index < value.len and std.ascii.isDigit(value[index])) : (index += 1) {}
    }
    if (index < value.len and (value[index] == 'e' or value[index] == 'E')) {
        index += 1;
        if (index < value.len and (value[index] == '+' or value[index] == '-')) index += 1;
        if (index == value.len or !std.ascii.isDigit(value[index])) return false;
        while (index < value.len and std.ascii.isDigit(value[index])) : (index += 1) {}
    }
    return index == value.len;
}

fn isPrimitiveValue(value: json.Value) bool {
    return switch (value) {
        .null, .bool, .integer, .float, .number_string, .string => true,
        else => false,
    };
}

fn arrayIsPrimitive(items: []const json.Value) bool {
    for (items) |item| {
        if (!isPrimitiveValue(item)) return false;
    }
    return true;
}

fn tabularFields(allocator: Allocator, array: json.Array) !?[]const []const u8 {
    if (array.items.len == 0) return null;
    const first = switch (array.items[0]) {
        .object => |inner| inner,
        else => return null,
    };
    if (first.count() == 0) return null;

    const fields = try allocator.alloc([]const u8, first.count());
    var index: usize = 0;
    var first_it = first.iterator();
    while (first_it.next()) |entry| {
        if (!isPrimitiveValue(entry.value_ptr.*)) {
            allocator.free(fields);
            return null;
        }
        fields[index] = entry.key_ptr.*;
        index += 1;
    }

    for (array.items[1..]) |item| {
        const object = switch (item) {
            .object => |inner| inner,
            else => {
                allocator.free(fields);
                return null;
            },
        };
        if (object.count() != fields.len) {
            allocator.free(fields);
            return null;
        }
        for (fields) |field| {
            const child = object.get(field) orelse {
                allocator.free(fields);
                return null;
            };
            if (!isPrimitiveValue(child)) {
                allocator.free(fields);
                return null;
            }
        }
    }
    return fields;
}

const TextParser = struct {
    allocator: Allocator,
    lines: []const TextLine,
    index: usize,

    fn parse(self: *TextParser) anyerror!json.Value {
        if (self.lines.len == 0) return json.Value{ .object = json.ObjectMap.init(self.allocator) };
        const line = self.peek();
        if (line.indent != 0) return self.fail(line.number, "Root indentation must start at column 0");
        if (looksLikeField(line.content)) {
            const header = try parseArrayHeader(self.allocator, (try splitField(line.content)).lhs);
            if (header != null and header.?.key == null) {
                self.index += 1;
                return self.parseArrayBlock(header.?, splitField(line.content) catch unreachable, 2);
            }
            return self.parseObject(0);
        }
        if (self.lines.len != 1) return self.fail(line.number, "Multi-line TOON documents must be objects or arrays");
        self.index += 1;
        return try parsePrimitiveToken(self.allocator, line.content);
    }

    fn parseObject(self: *TextParser, expected_indent: usize) anyerror!json.Value {
        var object = json.ObjectMap.init(self.allocator);
        while (self.index < self.lines.len) {
            const line = self.peek();
            if (line.indent < expected_indent) break;
            if (line.indent != expected_indent) return self.fail(line.number, "Unexpected indentation in object block");
            const parsed = try self.parseFieldLine(line.content, line.indent, line.number);
            try object.put(parsed.key, parsed.value);
        }
        return json.Value{ .object = object };
    }

    fn parseFieldLine(self: *TextParser, content: []const u8, indent: usize, line_number: usize) anyerror!ParsedField {
        const split = try splitField(content);
        if (try parseArrayHeader(self.allocator, split.lhs)) |header| {
            if (header.key != null) {
                self.index += 1;
                return .{
                    .key = header.key.?,
                    .value = try self.parseArrayBlock(header, split, indent + 2),
                };
            }
        }

        const key = try parseKeyToken(self.allocator, split.lhs);
        self.index += 1;
        if (split.rhs.len != 0) {
            return .{ .key = key, .value = try parsePrimitiveToken(self.allocator, split.rhs) };
        }
        if (self.index >= self.lines.len) {
            return .{ .key = key, .value = json.Value{ .object = json.ObjectMap.init(self.allocator) } };
        }
        const next = self.peek();
        if (next.indent <= indent) {
            return .{ .key = key, .value = json.Value{ .object = json.ObjectMap.init(self.allocator) } };
        }
        if (next.indent != indent + 2) return self.fail(line_number, "Nested object indentation must increase by 2 spaces");
        return .{ .key = key, .value = try self.parseObject(indent + 2) };
    }

    fn parseArrayBlock(self: *TextParser, header: ArrayHeader, split: SplitField, continuation_indent: usize) anyerror!json.Value {
        var array = json.Array.init(self.allocator);

        if (header.fields) |fields| {
            if (split.rhs.len != 0) return self.fail(self.currentLineNumber(), "Tabular arrays cannot be declared inline");
            while (self.index < self.lines.len) {
                const line = self.peek();
                if (line.indent < continuation_indent) break;
                if (line.indent != continuation_indent) return self.fail(line.number, "Unexpected indentation in tabular array");
                self.index += 1;
                const tokens = try splitDelimited(self.allocator, line.content, header.delimiter);
                if (tokens.len != fields.len) return self.fail(line.number, "Tabular row width does not match header");
                var object = json.ObjectMap.init(self.allocator);
                for (fields, 0..) |field, index| {
                    try object.put(field, try parsePrimitiveToken(self.allocator, tokens[index]));
                }
                try array.append(json.Value{ .object = object });
            }
            if (array.items.len != header.length) return self.fail(self.currentLineNumber(), "Tabular row count does not match declared length");
            return json.Value{ .array = array };
        }

        if (split.rhs.len != 0) {
            const tokens = try splitDelimited(self.allocator, split.rhs, header.delimiter);
            if (tokens.len != header.length) return self.fail(self.currentLineNumber(), "Inline array length does not match declared length");
            for (tokens) |token| try array.append(try parsePrimitiveToken(self.allocator, token));
            return json.Value{ .array = array };
        }

        while (self.index < self.lines.len) {
            const line = self.peek();
            if (line.indent < continuation_indent) break;
            if (line.indent != continuation_indent) return self.fail(line.number, "Unexpected indentation in array block");
            if (!std.mem.startsWith(u8, line.content, "-")) break;
            try array.append(try self.parseArrayItem(continuation_indent));
        }
        if (array.items.len != header.length) return self.fail(self.currentLineNumber(), "Array length does not match declared length");
        return json.Value{ .array = array };
    }

    fn parseArrayItem(self: *TextParser, indent: usize) anyerror!json.Value {
        const line = self.peek();
        if (line.indent != indent) return self.fail(line.number, "Unexpected indentation in array item");
        if (std.mem.eql(u8, line.content, "-")) {
            self.index += 1;
            if (self.index < self.lines.len and self.peek().indent > indent) {
                return self.parseObject(indent + 2);
            }
            return json.Value{ .object = json.ObjectMap.init(self.allocator) };
        }
        if (!std.mem.startsWith(u8, line.content, "- ")) return self.fail(line.number, "Array items must start with '- '");
        const suffix = line.content[2..];
        if (looksLikeField(suffix)) {
            const split = try splitField(suffix);
            if (try parseArrayHeader(self.allocator, split.lhs)) |header| {
                if (header.key == null) {
                    self.index += 1;
                    return self.parseArrayBlock(header, split, indent + 2);
                }
            }
            return self.parseObjectItem(suffix, indent);
        }
        self.index += 1;
        return try parsePrimitiveToken(self.allocator, suffix);
    }

    fn parseObjectItem(self: *TextParser, first_field: []const u8, indent: usize) anyerror!json.Value {
        var object = json.ObjectMap.init(self.allocator);
        const first = try self.parseInlineField(first_field, indent);
        try object.put(first.key, first.value);
        while (self.index < self.lines.len) {
            const line = self.peek();
            if (line.indent < indent + 2) break;
            if (line.indent != indent + 2) return self.fail(line.number, "Unexpected indentation in object array item");
            const parsed = try self.parseFieldLine(line.content, indent + 2, line.number);
            try object.put(parsed.key, parsed.value);
        }
        return json.Value{ .object = object };
    }

    fn parseInlineField(self: *TextParser, content: []const u8, indent: usize) anyerror!ParsedField {
        const split = try splitField(content);
        if (try parseArrayHeader(self.allocator, split.lhs)) |header| {
            if (header.key != null) {
                self.index += 1;
                return .{
                    .key = header.key.?,
                    .value = try self.parseArrayBlock(header, split, indent + 4),
                };
            }
        }

        const key = try parseKeyToken(self.allocator, split.lhs);
        self.index += 1;
        if (split.rhs.len != 0) {
            return .{ .key = key, .value = try parsePrimitiveToken(self.allocator, split.rhs) };
        }
        if (self.index < self.lines.len and self.peek().indent >= indent + 2) {
            return .{ .key = key, .value = try self.parseObject(indent + 2) };
        }
        return .{ .key = key, .value = json.Value{ .object = json.ObjectMap.init(self.allocator) } };
    }

    fn peek(self: *TextParser) TextLine {
        return self.lines[self.index];
    }

    fn currentLineNumber(self: *TextParser) usize {
        if (self.index >= self.lines.len) {
            return if (self.lines.len == 0) 1 else self.lines[self.lines.len - 1].number;
        }
        return self.lines[self.index].number;
    }

    fn fail(self: *TextParser, line_number: usize, message: []const u8) error{ParseFailed} {
        _ = self;
        std.log.err("line {}: {s}", .{ line_number, message });
        return error.ParseFailed;
    }
};

fn looksLikeField(content: []const u8) bool {
    _ = splitField(content) catch return false;
    return true;
}

fn splitField(content: []const u8) !SplitField {
    var in_string = false;
    var escaped = false;
    var bracket_depth: usize = 0;
    var brace_depth: usize = 0;
    for (content, 0..) |char, index| {
        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (char == '\\') {
                escaped = true;
            } else if (char == '"') {
                in_string = false;
            }
            continue;
        }
        switch (char) {
            '"' => in_string = true,
            '[' => bracket_depth += 1,
            ']' => bracket_depth -= 1,
            '{' => brace_depth += 1,
            '}' => brace_depth -= 1,
            ':' => if (bracket_depth == 0 and brace_depth == 0) {
                var rhs = content[index + 1 ..];
                if (rhs.len > 0 and rhs[0] == ' ') rhs = rhs[1..];
                return .{ .lhs = content[0..index], .rhs = rhs };
            },
            else => {},
        }
    }
    return error.ExpectedColon;
}

fn parseArrayHeader(allocator: Allocator, text: []const u8) !?ArrayHeader {
    var base = text;
    var fields: ?[]const []const u8 = null;

    if (base.len > 0 and base[base.len - 1] == '}') {
        const open_index = findMatchingOpenBrace(base) orelse return null;
        const fields_text = base[open_index + 1 .. base.len - 1];
        base = base[0..open_index];
        var delimiter: u8 = ',';
        if (base.len >= 2 and base[base.len - 1] == ']' and base[base.len - 2] == '|') delimiter = '|';
        if (base.len >= 2 and base[base.len - 1] == ']' and base[base.len - 2] == '\t') delimiter = '\t';
        fields = try splitDelimited(allocator, fields_text, delimiter);
    }

    if (base.len == 0 or base[base.len - 1] != ']') return null;
    const open = findMatchingOpenBracket(base) orelse return null;
    const inside = base[open + 1 .. base.len - 1];
    if (inside.len == 0) return null;

    var delimiter: u8 = ',';
    var digits = inside;
    if (inside[inside.len - 1] == '|' or inside[inside.len - 1] == '\t') {
        delimiter = inside[inside.len - 1];
        digits = inside[0 .. inside.len - 1];
    }
    if (digits.len == 0) return null;
    for (digits) |char| {
        if (!std.ascii.isDigit(char)) return null;
    }
    const length = try std.fmt.parseInt(usize, digits, 10);
    const key = if (open == 0) null else try parseKeyToken(allocator, base[0..open]);
    return .{ .key = key, .length = length, .delimiter = delimiter, .fields = fields };
}

fn findMatchingOpenBrace(text: []const u8) ?usize {
    var in_string = false;
    var escaped = false;
    var index = text.len;
    while (index > 0) {
        index -= 1;
        const char = text[index];
        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (char == '\\') {
                escaped = true;
            } else if (char == '"') {
                in_string = false;
            }
            continue;
        }
        if (char == '"') {
            in_string = true;
            continue;
        }
        if (char == '{') return index;
    }
    return null;
}

fn findMatchingOpenBracket(text: []const u8) ?usize {
    var in_string = false;
    var escaped = false;
    var index = text.len;
    while (index > 0) {
        index -= 1;
        const char = text[index];
        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (char == '\\') {
                escaped = true;
            } else if (char == '"') {
                in_string = false;
            }
            continue;
        }
        if (char == '"') {
            in_string = true;
            continue;
        }
        if (char == '[') return index;
    }
    return null;
}

fn parseKeyToken(allocator: Allocator, token: []const u8) ![]const u8 {
    const trimmed = std.mem.trim(u8, token, " ");
    if (trimmed.len == 0) return error.EmptyKey;
    if (trimmed[0] == '"') return parseQuotedString(allocator, trimmed);
    return trimmed;
}

fn splitDelimited(allocator: Allocator, text: []const u8, delimiter: u8) ![]const []const u8 {
    if (text.len == 0) return allocator.alloc([]const u8, 0);

    var parts = std.ArrayList([]const u8).empty;
    var in_string = false;
    var escaped = false;
    var start: usize = 0;
    for (text, 0..) |char, index| {
        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (char == '\\') {
                escaped = true;
            } else if (char == '"') {
                in_string = false;
            }
            continue;
        }
        if (char == '"') {
            in_string = true;
            continue;
        }
        if (char == delimiter) {
            try parts.append(allocator, std.mem.trim(u8, text[start..index], " "));
            start = index + 1;
        }
    }
    if (in_string) return error.UnterminatedString;
    try parts.append(allocator, std.mem.trim(u8, text[start..], " "));
    return parts.toOwnedSlice(allocator);
}

fn parsePrimitiveToken(allocator: Allocator, token: []const u8) !json.Value {
    const trimmed = std.mem.trim(u8, token, " ");
    if (std.mem.eql(u8, trimmed, "null")) return .null;
    if (std.mem.eql(u8, trimmed, "true")) return json.Value{ .bool = true };
    if (std.mem.eql(u8, trimmed, "false")) return json.Value{ .bool = false };
    if (trimmed.len > 0 and trimmed[0] == '"') return json.Value{ .string = try parseQuotedString(allocator, trimmed) };
    if (isIntegerToken(trimmed)) return json.Value{ .integer = try std.fmt.parseInt(i64, trimmed, 10) };
    if (std.mem.startsWith(u8, trimmed, "-0") and !std.mem.eql(u8, trimmed, "-0") and !std.mem.eql(u8, trimmed, "-0.0")) {
        return json.Value{ .string = trimmed };
    }
    if (std.mem.startsWith(u8, trimmed, "0") and trimmed.len > 1 and !std.mem.startsWith(u8, trimmed, "0.")) {
        return json.Value{ .string = trimmed };
    }
    if (isFloatToken(trimmed)) return json.Value.parseFromNumberSlice(trimmed);
    return json.Value{ .string = trimmed };
}

fn isIntegerToken(token: []const u8) bool {
    if (token.len == 0) return false;
    var index: usize = 0;
    if (token[index] == '-') {
        index += 1;
        if (index == token.len) return false;
    }
    if (token[index] == '0') return index + 1 == token.len;
    if (!std.ascii.isDigit(token[index])) return false;
    while (index < token.len) : (index += 1) {
        if (!std.ascii.isDigit(token[index])) return false;
    }
    return true;
}

fn isFloatToken(token: []const u8) bool {
    if (token.len == 0) return false;
    var index: usize = 0;
    if (token[index] == '-') {
        index += 1;
        if (index == token.len) return false;
    }
    var saw_digit = false;
    while (index < token.len and std.ascii.isDigit(token[index])) : (index += 1) {
        saw_digit = true;
    }
    if (index < token.len and token[index] == '.') {
        index += 1;
        while (index < token.len and std.ascii.isDigit(token[index])) : (index += 1) {
            saw_digit = true;
        }
    }
    if (!saw_digit) return false;
    if (index < token.len and (token[index] == 'e' or token[index] == 'E')) {
        index += 1;
        if (index < token.len and (token[index] == '+' or token[index] == '-')) index += 1;
        if (index == token.len) return false;
        while (index < token.len and std.ascii.isDigit(token[index])) : (index += 1) {}
    }
    return index == token.len and
        (std.mem.indexOfScalar(u8, token, '.') != null or std.mem.indexOfAny(u8, token, "eE") != null);
}

fn parseQuotedString(allocator: Allocator, token: []const u8) ![]const u8 {
    if (token.len < 2 or token[0] != '"' or token[token.len - 1] != '"') return error.InvalidQuotedString;
    var buffer = std.ArrayList(u8).empty;
    defer buffer.deinit(allocator);
    var index: usize = 1;
    while (index < token.len - 1) : (index += 1) {
        const char = token[index];
        if (char == '\\') {
            index += 1;
            if (index >= token.len - 1) return error.DanglingEscape;
            switch (token[index]) {
                '\\' => try buffer.append(allocator, '\\'),
                '"' => try buffer.append(allocator, '"'),
                'n' => try buffer.append(allocator, '\n'),
                'r' => try buffer.append(allocator, '\r'),
                't' => try buffer.append(allocator, '\t'),
                else => return error.UnsupportedEscape,
            }
        } else {
            try buffer.append(allocator, char);
        }
    }
    return buffer.toOwnedSlice(allocator);
}
