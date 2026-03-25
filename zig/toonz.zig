const std = @import("std");
const text_format = @import("text_format.zig");

const allocator = std.heap.c_allocator;
const json = std.json;

const header = "TOON";
const json_magic_key = "\x00toons";
const current_format_version: u8 = 2;
const minimum_format_version: u8 = 1;

const token_null: u8 = 0x00;
const token_bool: u8 = 0x01;
const token_int: u8 = 0x02;
const token_float: u8 = 0x03;
const token_string: u8 = 0x04;
const token_bytes: u8 = 0x05;
const token_list: u8 = 0x06;
const token_dict: u8 = 0x07;
const token_tuple: u8 = 0x08;
const token_set: u8 = 0x09;
const token_frozenset: u8 = 0x0A;
const token_date: u8 = 0x0B;
const token_time: u8 = 0x0C;
const token_datetime: u8 = 0x0D;
const token_timedelta: u8 = 0x0E;
const token_decimal: u8 = 0x0F;
const token_uuid: u8 = 0x10;
const token_path: u8 = 0x11;
const token_complex: u8 = 0x12;

pub const tag_null: c_int = 0;
pub const tag_bool: c_int = 1;
pub const tag_int: c_int = 2;
pub const tag_float: c_int = 3;
pub const tag_string: c_int = 4;
pub const tag_bytes: c_int = 5;
pub const tag_list: c_int = 6;
pub const tag_dict: c_int = 7;
pub const tag_tuple: c_int = 8;
pub const tag_set: c_int = 9;
pub const tag_frozenset: c_int = 10;
pub const tag_date: c_int = 11;
pub const tag_time: c_int = 12;
pub const tag_datetime: c_int = 13;
pub const tag_timedelta: c_int = 14;
pub const tag_decimal: c_int = 15;
pub const tag_uuid: c_int = 16;
pub const tag_path: c_int = 17;
pub const tag_complex: c_int = 18;

pub const ToonsSlice = extern struct {
    ptr: ?[*]const u8,
    len: usize,
};

pub const ToonsValue = extern struct {
    tag: c_int,
    bool_value: bool,
    int_value: i64,
    float_value: f64,
    string_value: ToonsSlice,
    bytes_value: ToonsSlice,
    children_ptr: ?[*]*ToonsValue,
    children_len: usize,
    pairs_ptr: ?[*]ToonsPair,
    pairs_len: usize,
};

pub const ToonsPair = extern struct {
    key: ToonsSlice,
    value: ?*ToonsValue,
};

const Parser = struct {
    data: []const u8,
    index: usize = 0,

    fn readByte(self: *Parser) !u8 {
        if (self.index >= self.data.len) {
            return error.UnexpectedEof;
        }

        const byte = self.data[self.index];
        self.index += 1;
        return byte;
    }

    fn readInt(self: *Parser, comptime T: type) !T {
        const size = @sizeOf(T);
        if (self.index + size > self.data.len) {
            return error.UnexpectedEof;
        }

        const bytes: *const [@sizeOf(T)]u8 = @ptrCast(self.data[self.index .. self.index + size].ptr);
        const value = std.mem.readInt(T, bytes, .little);
        self.index += size;
        return value;
    }

    fn readBytes(self: *Parser, len: usize) ![]const u8 {
        if (self.index + len > self.data.len) {
            return error.UnexpectedEof;
        }

        const bytes = self.data[self.index .. self.index + len];
        self.index += len;
        return bytes;
    }
};

var last_error_buffer: [512]u8 = undefined;
var last_error_len: usize = 0;

fn clearLastError() void {
    last_error_len = 0;
}

fn setLastError(comptime fmt: []const u8, args: anytype) void {
    const rendered = std.fmt.bufPrint(&last_error_buffer, fmt, args) catch {
        const fallback = "TOONS native error";
        @memcpy(last_error_buffer[0..fallback.len], fallback);
        last_error_len = fallback.len;
        return;
    };
    last_error_len = rendered.len;
}

fn emptySlice() ToonsSlice {
    return .{ .ptr = null, .len = 0 };
}

fn ffiSliceToBytes(value: ToonsSlice) ![]const u8 {
    if (value.len == 0) {
        return &.{};
    }

    const ptr = value.ptr orelse return error.NullPointer;
    return ptr[0..value.len];
}

fn allocValue(tag: c_int) !*ToonsValue {
    const value = try allocator.create(ToonsValue);
    value.* = .{
        .tag = tag,
        .bool_value = false,
        .int_value = 0,
        .float_value = 0,
        .string_value = emptySlice(),
        .bytes_value = emptySlice(),
        .children_ptr = null,
        .children_len = 0,
        .pairs_ptr = null,
        .pairs_len = 0,
    };
    return value;
}

fn duplicateToSlice(bytes: []const u8) !ToonsSlice {
    if (bytes.len == 0) {
        return emptySlice();
    }

    const buffer = try allocator.alloc(u8, bytes.len);
    @memcpy(buffer, bytes);
    return .{ .ptr = buffer.ptr, .len = buffer.len };
}

fn freeSlice(slice_value: ToonsSlice) void {
    if (slice_value.len == 0) {
        return;
    }

    if (slice_value.ptr) |ptr| {
        allocator.free(ptr[0..slice_value.len]);
    }
}

fn appendInt(buffer: *std.ArrayList(u8), comptime T: type, value: T) anyerror!void {
    var raw: [@sizeOf(T)]u8 = undefined;
    std.mem.writeInt(T, &raw, value, .little);
    try buffer.appendSlice(allocator, &raw);
}

fn writeBytes(buffer: *std.ArrayList(u8), bytes: []const u8) anyerror!void {
    const len_u32 = std.math.cast(u32, bytes.len) orelse return error.LengthOverflow;
    try appendInt(buffer, u32, len_u32);
    try buffer.appendSlice(allocator, bytes);
}

fn writeTokenAndBytes(buffer: *std.ArrayList(u8), token: u8, bytes: []const u8) anyerror!void {
    try buffer.append(allocator, token);
    try writeBytes(buffer, bytes);
}

fn writeChildren(buffer: *std.ArrayList(u8), value: *const ToonsValue, token: u8) anyerror!void {
    const len_u32 = std.math.cast(u32, value.children_len) orelse return error.LengthOverflow;
    try buffer.append(allocator, token);
    try appendInt(buffer, u32, len_u32);

    if (value.children_len == 0) {
        return;
    }

    const children_ptr = value.children_ptr orelse return error.NullPointer;
    const children = children_ptr[0..value.children_len];
    for (children) |child| {
        try serializeValue(buffer, child);
    }
}

// Map token to tag for string-like types
fn tokenToStringTag(token: u8) ?c_int {
    return switch (token) {
        token_string => tag_string,
        token_date => tag_date,
        token_time => tag_time,
        token_datetime => tag_datetime,
        token_decimal => tag_decimal,
        token_uuid => tag_uuid,
        token_path => tag_path,
        else => null,
    };
}

// Map token to tag for children-based types
fn tokenToChildrenTag(token: u8) ?c_int {
    return switch (token) {
        token_list => tag_list,
        token_tuple => tag_tuple,
        token_set => tag_set,
        token_frozenset => tag_frozenset,
        token_timedelta => tag_timedelta,
        token_complex => tag_complex,
        else => null,
    };
}

fn serializeValue(buffer: *std.ArrayList(u8), value: *const ToonsValue) anyerror!void {
    switch (value.tag) {
        tag_null => try buffer.append(allocator, token_null),
        tag_bool => {
            try buffer.append(allocator, token_bool);
            try buffer.append(allocator, if (value.bool_value) 1 else 0);
        },
        tag_int => {
            try buffer.append(allocator, token_int);
            try appendInt(buffer, i64, value.int_value);
        },
        tag_float => {
            try buffer.append(allocator, token_float);
            const bits: u64 = @bitCast(value.float_value);
            try appendInt(buffer, u64, bits);
        },
        tag_string => try writeTokenAndBytes(buffer, token_string, try ffiSliceToBytes(value.string_value)),
        tag_bytes => try writeTokenAndBytes(buffer, token_bytes, try ffiSliceToBytes(value.bytes_value)),
        tag_list => try writeChildren(buffer, value, token_list),
        tag_tuple => try writeChildren(buffer, value, token_tuple),
        tag_set => try writeChildren(buffer, value, token_set),
        tag_frozenset => try writeChildren(buffer, value, token_frozenset),
        tag_timedelta => try writeChildren(buffer, value, token_timedelta),
        tag_complex => try writeChildren(buffer, value, token_complex),
        tag_date => try writeTokenAndBytes(buffer, token_date, try ffiSliceToBytes(value.string_value)),
        tag_time => try writeTokenAndBytes(buffer, token_time, try ffiSliceToBytes(value.string_value)),
        tag_datetime => try writeTokenAndBytes(buffer, token_datetime, try ffiSliceToBytes(value.string_value)),
        tag_decimal => try writeTokenAndBytes(buffer, token_decimal, try ffiSliceToBytes(value.string_value)),
        tag_uuid => try writeTokenAndBytes(buffer, token_uuid, try ffiSliceToBytes(value.string_value)),
        tag_path => try writeTokenAndBytes(buffer, token_path, try ffiSliceToBytes(value.string_value)),
        tag_dict => {
            try buffer.append(allocator, token_dict);
            const len_u32 = std.math.cast(u32, value.pairs_len) orelse return error.LengthOverflow;
            try appendInt(buffer, u32, len_u32);
            if (value.pairs_len == 0) {
                return;
            }

            const pairs_ptr = value.pairs_ptr orelse return error.NullPointer;
            const pairs = pairs_ptr[0..value.pairs_len];
            for (pairs) |pair| {
                try writeBytes(buffer, try ffiSliceToBytes(pair.key));
                const child = pair.value orelse return error.NullPointer;
                try serializeValue(buffer, child);
            }
        },
        else => return error.InvalidTag,
    }
}

fn parseBytes(parser: *Parser) anyerror![]const u8 {
    const len = try parser.readInt(u32);
    return parser.readBytes(len);
}

fn parseChildrenValue(parser: *Parser, tag: c_int) anyerror!*ToonsValue {
    const count = try parser.readInt(u32);
    const value = try allocValue(tag);
    if (count == 0) {
        return value;
    }

    const children = try allocator.alloc(*ToonsValue, count);
    errdefer allocator.free(children);
    for (children, 0..) |*child, index| {
        errdefer {
            for (children[0..index]) |existing| {
                freeValue(existing);
            }
        }
        child.* = try parseValue(parser);
    }
    value.children_ptr = children.ptr;
    value.children_len = children.len;
    return value;
}

fn parseStringValue(parser: *Parser, tag: c_int) anyerror!*ToonsValue {
    const value = try allocValue(tag);
    value.string_value = try duplicateToSlice(try parseBytes(parser));
    return value;
}

fn parseValue(parser: *Parser) anyerror!*ToonsValue {
    const token = try parser.readByte();

    // String-like types: read length-prefixed bytes into string_value
    if (tokenToStringTag(token)) |tag| {
        return parseStringValue(parser, tag);
    }

    // Children-based types: read count + recursive children
    if (tokenToChildrenTag(token)) |tag| {
        return parseChildrenValue(parser, tag);
    }

    switch (token) {
        token_null => return allocValue(tag_null),
        token_bool => {
            const value = try allocValue(tag_bool);
            const raw = try parser.readByte();
            if (raw > 1) {
                return error.InvalidBoolean;
            }
            value.bool_value = raw == 1;
            return value;
        },
        token_int => {
            const value = try allocValue(tag_int);
            value.int_value = try parser.readInt(i64);
            return value;
        },
        token_float => {
            const value = try allocValue(tag_float);
            const bits = try parser.readInt(u64);
            value.float_value = @bitCast(bits);
            return value;
        },
        token_bytes => {
            const value = try allocValue(tag_bytes);
            value.bytes_value = try duplicateToSlice(try parseBytes(parser));
            return value;
        },
        token_dict => {
            const count = try parser.readInt(u32);
            const value = try allocValue(tag_dict);
            if (count == 0) {
                return value;
            }

            const pairs = try allocator.alloc(ToonsPair, count);
            errdefer allocator.free(pairs);
            for (pairs, 0..) |*pair, index| {
                errdefer {
                    for (pairs[0..index]) |existing| {
                        freeSlice(existing.key);
                        if (existing.value) |child| {
                            freeValue(child);
                        }
                    }
                }
                pair.* = .{
                    .key = try duplicateToSlice(try parseBytes(parser)),
                    .value = try parseValue(parser),
                };
            }
            value.pairs_ptr = pairs.ptr;
            value.pairs_len = pairs.len;
            return value;
        },
        else => return error.UnknownToken,
    }
}

fn freeValue(value: *ToonsValue) void {
    switch (value.tag) {
        tag_string, tag_date, tag_time, tag_datetime, tag_decimal, tag_uuid, tag_path => freeSlice(value.string_value),
        tag_bytes => freeSlice(value.bytes_value),
        tag_list, tag_tuple, tag_set, tag_frozenset, tag_timedelta, tag_complex => {
            if (value.children_len > 0 and value.children_ptr != null) {
                const children = value.children_ptr.?[0..value.children_len];
                for (children) |child| {
                    freeValue(child);
                }
                allocator.free(children);
            }
        },
        tag_dict => {
            if (value.pairs_len > 0 and value.pairs_ptr != null) {
                const pairs = value.pairs_ptr.?[0..value.pairs_len];
                for (pairs) |pair| {
                    freeSlice(pair.key);
                    if (pair.value) |child| {
                        freeValue(child);
                    }
                }
                allocator.free(pairs);
            }
        },
        else => {},
    }
    allocator.destroy(value);
}

fn jsonString(value: json.Value) ![]const u8 {
    return switch (value) {
        .string => |inner| inner,
        else => error.ExpectedString,
    };
}

fn jsonArray(value: json.Value) ![]const json.Value {
    return switch (value) {
        .array => |inner| inner.items,
        else => error.ExpectedArray,
    };
}

const JsonEnvelope = struct {
    kind: []const u8,
    name: ?[]const u8 = null,
    payload: json.Value,
};

fn getJsonEnvelope(value: json.Value) ?JsonEnvelope {
    const object = switch (value) {
        .object => |inner| inner,
        else => return null,
    };
    if (object.count() != 1) {
        return null;
    }
    const raw = object.get(json_magic_key) orelse return null;
    const items = switch (raw) {
        .array => |inner| inner.items,
        else => return null,
    };
    if (items.len < 2) {
        return null;
    }
    const kind = switch (items[0]) {
        .string => |inner| inner,
        else => return null,
    };
    if (std.mem.eql(u8, kind, "dict")) {
        if (items.len != 2) return null;
        return .{ .kind = kind, .payload = items[1] };
    }
    if (std.mem.eql(u8, kind, "ext")) {
        if (items.len != 3) return null;
        const name = switch (items[1]) {
            .string => |inner| inner,
            else => return null,
        };
        return .{ .kind = kind, .name = name, .payload = items[2] };
    }
    return null;
}

fn writeJsonExtPrelude(jws: *json.Stringify, name: []const u8) anyerror!void {
    try jws.beginObject();
    try jws.objectField(json_magic_key);
    try jws.beginArray();
    try jws.write("ext");
    try jws.write(name);
}

fn writeJsonExtClose(jws: *json.Stringify) anyerror!void {
    try jws.endArray();
    try jws.endObject();
}

fn writeJsonStringExt(jws: *json.Stringify, name: []const u8, data: []const u8) anyerror!void {
    try writeJsonExtPrelude(jws, name);
    try jws.write(data);
    try writeJsonExtClose(jws);
}

fn writeJsonEscapedDict(jws: *json.Stringify, value: *const ToonsValue) anyerror!void {
    try jws.beginObject();
    try jws.objectField(json_magic_key);
    try jws.beginArray();
    try jws.write("dict");
    try jws.beginObject();
    const pairs = if (value.pairs_len == 0 or value.pairs_ptr == null) &[_]ToonsPair{} else value.pairs_ptr.?[0..value.pairs_len];
    for (pairs) |pair| {
        try jws.objectField(try ffiSliceToBytes(pair.key));
        const child = pair.value orelse return error.NullPointer;
        try writeJsonValue(jws, child);
    }
    try jws.endObject();
    try jws.endArray();
    try jws.endObject();
}

fn writeJsonBytesExt(jws: *json.Stringify, data: []const u8) anyerror!void {
    const encoded_len = std.base64.standard.Encoder.calcSize(data.len);
    const buffer = try allocator.alloc(u8, encoded_len);
    defer allocator.free(buffer);
    const encoded = std.base64.standard.Encoder.encode(buffer, data);
    try writeJsonExtPrelude(jws, "python.bytes");
    try jws.write(encoded);
    try writeJsonExtClose(jws);
}

fn writeJsonArray(jws: *json.Stringify, children_ptr: ?[*]*ToonsValue, children_len: usize) anyerror!void {
    try jws.beginArray();
    if (children_len > 0) {
        const children = children_ptr orelse return error.NullPointer;
        for (children[0..children_len]) |child| {
            try writeJsonValue(jws, child);
        }
    }
    try jws.endArray();
}

fn writeJsonSequenceExt(jws: *json.Stringify, name: []const u8, children_ptr: ?[*]*ToonsValue, children_len: usize) anyerror!void {
    try writeJsonExtPrelude(jws, name);
    try writeJsonArray(jws, children_ptr, children_len);
    try writeJsonExtClose(jws);
}

fn dictIsReservedEnvelope(value: *const ToonsValue) bool {
    if (value.tag != tag_dict or value.pairs_len != 1 or value.pairs_ptr == null) {
        return false;
    }
    const pair = value.pairs_ptr.?[0];
    const key = ffiSliceToBytes(pair.key) catch return false;
    if (!std.mem.eql(u8, key, json_magic_key)) {
        return false;
    }
    const child = pair.value orelse return false;
    if (child.tag != tag_list or child.children_len < 2 or child.children_ptr == null) {
        return false;
    }
    const items = child.children_ptr.?[0..child.children_len];
    if (items[0].*.tag != tag_string) {
        return false;
    }
    const kind = ffiSliceToBytes(items[0].*.string_value) catch return false;
    if (std.mem.eql(u8, kind, "dict")) {
        return child.children_len == 2;
    }
    if (std.mem.eql(u8, kind, "ext")) {
        return child.children_len == 3 and items[1].*.tag == tag_string;
    }
    return false;
}

// Extension name mapping for tag -> JSON extension name
fn tagToExtName(tag: c_int) ?[]const u8 {
    return switch (tag) {
        tag_date => "python.date",
        tag_time => "python.time",
        tag_datetime => "python.datetime",
        tag_decimal => "python.decimal",
        tag_uuid => "python.uuid",
        tag_path => "path:pathlib:Path",
        tag_tuple => "python.tuple",
        tag_set => "python.set",
        tag_frozenset => "python.frozenset",
        tag_timedelta => "python.timedelta",
        tag_complex => "python.complex",
        else => null,
    };
}

fn writeJsonValue(jws: *json.Stringify, value: *const ToonsValue) anyerror!void {
    switch (value.tag) {
        tag_null => try jws.write(null),
        tag_bool => try jws.write(value.bool_value),
        tag_int => try jws.write(value.int_value),
        tag_float => {
            if (std.math.isFinite(value.float_value)) {
                try jws.write(value.float_value);
            } else {
                try writeJsonExtPrelude(jws, "python.float");
                if (std.math.isNan(value.float_value)) {
                    try jws.write("nan");
                } else if (value.float_value > 0) {
                    try jws.write("inf");
                } else {
                    try jws.write("-inf");
                }
                try writeJsonExtClose(jws);
            }
        },
        tag_string => try jws.write(try ffiSliceToBytes(value.string_value)),
        tag_bytes => try writeJsonBytesExt(jws, try ffiSliceToBytes(value.bytes_value)),
        tag_list => try writeJsonArray(jws, value.children_ptr, value.children_len),
        // String-ext types: date, time, datetime, decimal, uuid, path
        tag_date, tag_time, tag_datetime, tag_decimal, tag_uuid, tag_path => {
            try writeJsonStringExt(jws, tagToExtName(value.tag).?, try ffiSliceToBytes(value.string_value));
        },
        // Sequence-ext types: tuple, set, frozenset, timedelta, complex
        tag_tuple, tag_set, tag_frozenset, tag_timedelta, tag_complex => {
            try writeJsonSequenceExt(jws, tagToExtName(value.tag).?, value.children_ptr, value.children_len);
        },
        tag_dict => {
            const pairs = if (value.pairs_len == 0 or value.pairs_ptr == null) &[_]ToonsPair{} else value.pairs_ptr.?[0..value.pairs_len];
            if (!dictIsReservedEnvelope(value)) {
                for (pairs) |pair| {
                    if (std.mem.eql(u8, try ffiSliceToBytes(pair.key), json_magic_key)) {
                        try writeJsonEscapedDict(jws, value);
                        return;
                    }
                }
            }
            try jws.beginObject();
            for (pairs) |pair| {
                try jws.objectField(try ffiSliceToBytes(pair.key));
                const child = pair.value orelse return error.NullPointer;
                try writeJsonValue(jws, child);
            }
            try jws.endObject();
        },
        else => return error.InvalidTag,
    }
}

fn serializeJsonObject(buffer: *std.ArrayList(u8), object: json.ObjectMap, canonical: bool) anyerror!void {
    try buffer.append(allocator, token_dict);
    const len_u32 = std.math.cast(u32, object.count()) orelse return error.LengthOverflow;
    try appendInt(buffer, u32, len_u32);

    if (object.count() == 0) {
        return;
    }

    if (canonical) {
        var keys = std.ArrayList([]const u8).empty;
        defer keys.deinit(allocator);
        var it = object.iterator();
        while (it.next()) |entry| {
            try keys.append(allocator, entry.key_ptr.*);
        }
        std.mem.sortUnstable([]const u8, keys.items, {}, struct {
            fn lessThan(_: void, a: []const u8, b: []const u8) bool {
                return std.mem.lessThan(u8, a, b);
            }
        }.lessThan);
        for (keys.items) |key| {
            try writeBytes(buffer, key);
            try serializeJsonValue(buffer, object.get(key).?, canonical);
        }
        return;
    }

    var it = object.iterator();
    while (it.next()) |entry| {
        try writeBytes(buffer, entry.key_ptr.*);
        try serializeJsonValue(buffer, entry.value_ptr.*, canonical);
    }
}

fn serializeJsonSequenceExtension(
    buffer: *std.ArrayList(u8),
    token: u8,
    payload: json.Value,
    canonical: bool,
) anyerror!void {
    const array = try jsonArray(payload);
    try buffer.append(allocator, token);
    try appendInt(buffer, u32, std.math.cast(u32, array.len) orelse return error.LengthOverflow);
    for (array) |item| try serializeJsonValue(buffer, item, canonical);
}

fn serializeJsonStringExtension(buffer: *std.ArrayList(u8), token: u8, payload: json.Value) anyerror!void {
    try buffer.append(allocator, token);
    try writeBytes(buffer, try jsonString(payload));
}

fn serializeJsonKnownExtension(
    buffer: *std.ArrayList(u8),
    name: []const u8,
    payload: json.Value,
    canonical: bool,
) anyerror!bool {
    if (std.mem.eql(u8, name, "python.bytes")) {
        const text = try jsonString(payload);
        const decoded_len = try std.base64.standard.Decoder.calcSizeForSlice(text);
        const decoded = try allocator.alloc(u8, decoded_len);
        defer allocator.free(decoded);
        try std.base64.standard.Decoder.decode(decoded, text);
        try buffer.append(allocator, token_bytes);
        try writeBytes(buffer, decoded);
        return true;
    }
    if (std.mem.eql(u8, name, "python.float")) {
        const text = try jsonString(payload);
        const float_value: f64 = if (std.mem.eql(u8, text, "nan"))
            std.math.nan(f64)
        else if (std.mem.eql(u8, text, "inf"))
            std.math.inf(f64)
        else if (std.mem.eql(u8, text, "-inf"))
            -std.math.inf(f64)
        else
            return error.InvalidFloatToken;
        try buffer.append(allocator, token_float);
        try appendInt(buffer, u64, @bitCast(float_value));
        return true;
    }
    // Sequence-based extensions
    if (std.mem.eql(u8, name, "python.tuple")) {
        try serializeJsonSequenceExtension(buffer, token_tuple, payload, canonical);
        return true;
    }
    if (std.mem.eql(u8, name, "python.set")) {
        try serializeJsonSequenceExtension(buffer, token_set, payload, canonical);
        return true;
    }
    if (std.mem.eql(u8, name, "python.frozenset")) {
        try serializeJsonSequenceExtension(buffer, token_frozenset, payload, canonical);
        return true;
    }
    if (std.mem.eql(u8, name, "python.complex")) {
        try serializeJsonSequenceExtension(buffer, token_complex, payload, canonical);
        return true;
    }
    if (std.mem.eql(u8, name, "python.timedelta")) {
        try serializeJsonSequenceExtension(buffer, token_timedelta, payload, canonical);
        return true;
    }
    // String-based extensions
    if (std.mem.eql(u8, name, "python.date")) {
        try serializeJsonStringExtension(buffer, token_date, payload);
        return true;
    }
    if (std.mem.eql(u8, name, "python.time")) {
        try serializeJsonStringExtension(buffer, token_time, payload);
        return true;
    }
    if (std.mem.eql(u8, name, "python.datetime")) {
        try serializeJsonStringExtension(buffer, token_datetime, payload);
        return true;
    }
    if (std.mem.eql(u8, name, "python.decimal")) {
        try serializeJsonStringExtension(buffer, token_decimal, payload);
        return true;
    }
    if (std.mem.eql(u8, name, "python.uuid")) {
        try serializeJsonStringExtension(buffer, token_uuid, payload);
        return true;
    }
    return false;
}

fn serializeJsonValue(buffer: *std.ArrayList(u8), value: json.Value, canonical: bool) anyerror!void {
    if (getJsonEnvelope(value)) |envelope| {
        if (std.mem.eql(u8, envelope.kind, "dict")) {
            const payload_object = switch (envelope.payload) {
                .object => |inner| inner,
                else => return error.ExpectedObject,
            };
            try serializeJsonObject(buffer, payload_object, canonical);
            return;
        }
        if (std.mem.eql(u8, envelope.kind, "ext") and envelope.name != null) {
            if (try serializeJsonKnownExtension(buffer, envelope.name.?, envelope.payload, canonical)) {
                return;
            }
        }
    }

    switch (value) {
        .null => try buffer.append(allocator, token_null),
        .bool => |inner| {
            try buffer.append(allocator, token_bool);
            try buffer.append(allocator, if (inner) 1 else 0);
        },
        .integer => |inner| {
            try buffer.append(allocator, token_int);
            try appendInt(buffer, i64, inner);
        },
        .float => |inner| {
            try buffer.append(allocator, token_float);
            try appendInt(buffer, u64, @bitCast(inner));
        },
        .number_string => |inner| {
            const parsed = json.Value.parseFromNumberSlice(inner);
            switch (parsed) {
                .integer, .float => try serializeJsonValue(buffer, parsed, canonical),
                .number_string => return error.NumberOutOfRange,
                else => unreachable,
            }
        },
        .string => |inner| {
            try buffer.append(allocator, token_string);
            try writeBytes(buffer, inner);
        },
        .array => |inner| {
            try buffer.append(allocator, token_list);
            try appendInt(buffer, u32, std.math.cast(u32, inner.items.len) orelse return error.LengthOverflow);
            for (inner.items) |item| try serializeJsonValue(buffer, item, canonical);
        },
        .object => |inner| try serializeJsonObject(buffer, inner, canonical),
    }
}

export fn toons_serialize_json(
    json_ptr: ?[*]const u8,
    json_len: usize,
    canonical: bool,
    out_ptr: *?[*]u8,
    out_len: *usize,
) bool {
    clearLastError();
    out_ptr.* = null;
    out_len.* = 0;

    const ptr = json_ptr orelse {
        setLastError("JSON pointer was null", .{});
        return false;
    };
    const payload = ptr[0..json_len];
    const parsed = json.parseFromSlice(json.Value, allocator, payload, .{ .parse_numbers = true }) catch |err| {
        setLastError("JSON parse failed: {s}", .{@errorName(err)});
        return false;
    };
    defer parsed.deinit();

    var buffer = std.ArrayList(u8).empty;
    buffer.appendSlice(allocator, header) catch |err| {
        setLastError("Unable to start payload: {s}", .{@errorName(err)});
        return false;
    };
    buffer.append(allocator, current_format_version) catch |err| {
        setLastError("Unable to write version byte: {s}", .{@errorName(err)});
        return false;
    };
    serializeJsonValue(&buffer, parsed.value, canonical) catch |err| {
        setLastError("Serialize from JSON failed: {s}", .{@errorName(err)});
        buffer.deinit(allocator);
        return false;
    };

    const owned = buffer.toOwnedSlice(allocator) catch |err| {
        setLastError("Unable to allocate serialized buffer: {s}", .{@errorName(err)});
        buffer.deinit(allocator);
        return false;
    };
    out_ptr.* = owned.ptr;
    out_len.* = owned.len;
    return true;
}

export fn toons_deserialize_json(
    data_ptr: ?[*]const u8,
    data_len: usize,
    out_ptr: *?[*]u8,
    out_len: *usize,
) bool {
    clearLastError();
    out_ptr.* = null;
    out_len.* = 0;

    const ptr = data_ptr orelse {
        setLastError("Payload pointer was null", .{});
        return false;
    };
    const data = ptr[0..data_len];

    if (data.len < header.len + 1) {
        setLastError("Payload is too short to be valid TOONS", .{});
        return false;
    }
    if (!std.mem.eql(u8, data[0..header.len], header)) {
        setLastError("Missing TOONS header", .{});
        return false;
    }
    const version = data[header.len];
    if (version < minimum_format_version or version > current_format_version) {
        setLastError("Unsupported TOONS version {d}", .{version});
        return false;
    }

    var parser = Parser{
        .data = data[header.len + 1 ..],
    };

    const root = parseValue(&parser) catch |err| {
        setLastError("Deserialize failed: {s}", .{@errorName(err)});
        return false;
    };
    defer freeValue(root);

    if (parser.index != parser.data.len) {
        setLastError("Trailing bytes detected after root value", .{});
        return false;
    }

    var aw: std.Io.Writer.Allocating = .init(allocator);
    defer aw.deinit();
    var jws: json.Stringify = .{ .writer = &aw.writer, .options = .{} };
    writeJsonValue(&jws, root) catch |err| {
        setLastError("Serialize to JSON failed: {s}", .{@errorName(err)});
        return false;
    };
    const owned = aw.toOwnedSlice() catch |err| {
        setLastError("Unable to allocate JSON buffer: {s}", .{@errorName(err)});
        return false;
    };
    out_ptr.* = owned.ptr;
    out_len.* = owned.len;
    return true;
}

export fn toons_render_json_text(
    json_ptr: ?[*]const u8,
    json_len: usize,
    delimiter: u8,
    out_ptr: *?[*]u8,
    out_len: *usize,
) bool {
    clearLastError();
    out_ptr.* = null;
    out_len.* = 0;

    const ptr = json_ptr orelse {
        setLastError("JSON pointer was null", .{});
        return false;
    };
    const payload = ptr[0..json_len];
    const parsed = json.parseFromSlice(json.Value, allocator, payload, .{ .parse_numbers = true }) catch |err| {
        setLastError("JSON parse failed: {s}", .{@errorName(err)});
        return false;
    };
    defer parsed.deinit();

    const rendered = text_format.renderJsonText(allocator, parsed.value, delimiter) catch |err| {
        setLastError("TOON text render failed: {s}", .{@errorName(err)});
        return false;
    };
    out_ptr.* = rendered.ptr;
    out_len.* = rendered.len;
    return true;
}

export fn toons_parse_text_json(
    text_ptr: ?[*]const u8,
    text_len: usize,
    out_ptr: *?[*]u8,
    out_len: *usize,
) bool {
    clearLastError();
    out_ptr.* = null;
    out_len.* = 0;

    const ptr = text_ptr orelse {
        setLastError("Text pointer was null", .{});
        return false;
    };
    const text = ptr[0..text_len];

    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const arena_allocator = arena.allocator();

    const value = text_format.parseText(arena_allocator, text) catch |err| {
        setLastError("TOON text parse failed: {s}", .{@errorName(err)});
        return false;
    };

    var aw: std.Io.Writer.Allocating = .init(allocator);
    defer aw.deinit();
    var jws: json.Stringify = .{ .writer = &aw.writer, .options = .{} };
    jws.write(value) catch |err| {
        setLastError("Serialize TOON text JSON failed: {s}", .{@errorName(err)});
        return false;
    };
    const owned = aw.toOwnedSlice() catch |err| {
        setLastError("Unable to allocate TOON text JSON buffer: {s}", .{@errorName(err)});
        return false;
    };
    out_ptr.* = owned.ptr;
    out_len.* = owned.len;
    return true;
}

export fn toons_serialize(root: ?*const ToonsValue, out_ptr: *?[*]u8, out_len: *usize) bool {
    clearLastError();
    out_ptr.* = null;
    out_len.* = 0;

    const value = root orelse {
        setLastError("Root value pointer was null", .{});
        return false;
    };

    var buffer = std.ArrayList(u8).empty;
    buffer.appendSlice(allocator, header) catch |err| {
        setLastError("Unable to start payload: {s}", .{@errorName(err)});
        return false;
    };
    buffer.append(allocator, current_format_version) catch |err| {
        setLastError("Unable to write version byte: {s}", .{@errorName(err)});
        return false;
    };
    serializeValue(&buffer, value) catch |err| {
        setLastError("Serialize failed: {s}", .{@errorName(err)});
        buffer.deinit(allocator);
        return false;
    };

    const owned = buffer.toOwnedSlice(allocator) catch |err| {
        setLastError("Unable to allocate serialized buffer: {s}", .{@errorName(err)});
        buffer.deinit(allocator);
        return false;
    };
    out_ptr.* = owned.ptr;
    out_len.* = owned.len;
    return true;
}

export fn toons_deserialize(data_ptr: ?[*]const u8, data_len: usize, out_root: *?*ToonsValue) bool {
    clearLastError();
    out_root.* = null;

    if (data_len == 0) {
        setLastError("Payload cannot be empty", .{});
        return false;
    }

    const ptr = data_ptr orelse {
        setLastError("Payload pointer was null", .{});
        return false;
    };
    const data = ptr[0..data_len];

    if (data.len < header.len + 1) {
        setLastError("Payload is too short to be valid TOONS", .{});
        return false;
    }
    if (!std.mem.eql(u8, data[0..header.len], header)) {
        setLastError("Missing TOONS header", .{});
        return false;
    }
    const version = data[header.len];
    if (version < minimum_format_version or version > current_format_version) {
        setLastError("Unsupported TOONS version {d}", .{version});
        return false;
    }

    var parser = Parser{
        .data = data[header.len + 1 ..],
    };

    const root = parseValue(&parser) catch |err| {
        setLastError("Deserialize failed: {s}", .{@errorName(err)});
        return false;
    };
    errdefer freeValue(root);

    if (parser.index != parser.data.len) {
        freeValue(root);
        setLastError("Trailing bytes detected after root value", .{});
        return false;
    }

    out_root.* = root;
    return true;
}

export fn toons_free_buffer(ptr: ?[*]u8, len: usize) void {
    if (ptr) |buffer| {
        allocator.free(buffer[0..len]);
    }
}

export fn toons_free_value(root: ?*ToonsValue) void {
    if (root) |value| {
        freeValue(value);
    }
}

export fn toons_last_error_message() ToonsSlice {
    if (last_error_len == 0) {
        return emptySlice();
    }
    return .{ .ptr = last_error_buffer[0..last_error_len].ptr, .len = last_error_len };
}
