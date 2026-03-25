from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

from .options import DecodeLimits

HEADER = b"TOON"
TOKEN_NAMES = {
    0x00: "null",
    0x01: "bool",
    0x02: "int",
    0x03: "float",
    0x04: "string",
    0x05: "bytes",
    0x06: "list",
    0x07: "dict",
    0x08: "tuple",
    0x09: "set",
    0x0A: "frozenset",
    0x0B: "date",
    0x0C: "time",
    0x0D: "datetime",
    0x0E: "timedelta",
    0x0F: "decimal",
    0x10: "uuid",
    0x11: "path",
    0x12: "complex",
}


@dataclass
class InspectionNode:
    kind: str
    path: str
    offset: int
    end_offset: int
    detail: str | None = None
    children: list[InspectionNode] = field(default_factory=list)


class InspectionError(ValueError):
    def __init__(self, message: str, *, offset: int, path: str) -> None:
        self.offset = offset
        self.path = path
        super().__init__(f"{message} at offset {offset} ({path})")


@dataclass
class _Stats:
    nodes: int = 0


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.index = 0

    def read(self, size: int, *, path: str) -> bytes:
        if self.index + size > len(self.data):
            raise InspectionError("Unexpected end of payload", offset=self.index, path=path)
        chunk = self.data[self.index : self.index + size]
        self.index += size
        return chunk

    def read_u8(self, *, path: str) -> int:
        return self.read(1, path=path)[0]

    def read_u32(self, *, path: str) -> int:
        return int.from_bytes(self.read(4, path=path), "little")

    def read_i64(self, *, path: str) -> int:
        return int.from_bytes(self.read(8, path=path), "little", signed=True)

    def read_f64(self, *, path: str) -> float:
        return struct.unpack("<d", self.read(8, path=path))[0]


def _child_path(parent: str, suffix: str) -> str:
    if suffix.startswith("["):
        return f"{parent}{suffix}"
    return f"{parent}.{suffix}"


def _preview_text(raw: bytes, *, offset: int, path: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InspectionError("Invalid UTF-8 string payload", offset=offset + exc.start, path=path) from exc


def _check_limits(
    *,
    limits: DecodeLimits | None,
    stats: _Stats,
    depth: int,
    container_length: int | None = None,
    string_length: int | None = None,
    bytes_length: int | None = None,
    path: str,
    offset: int,
) -> None:
    if limits is None:
        return
    if limits.max_total_nodes is not None and stats.nodes > limits.max_total_nodes:
        raise InspectionError("Node count limit exceeded", offset=offset, path=path)
    if limits.max_depth is not None and depth > limits.max_depth:
        raise InspectionError("Maximum nesting depth exceeded", offset=offset, path=path)
    if container_length is not None and limits.max_container_length is not None and container_length > limits.max_container_length:
        raise InspectionError("Container length limit exceeded", offset=offset, path=path)
    if string_length is not None and limits.max_string_length is not None and string_length > limits.max_string_length:
        raise InspectionError("String length limit exceeded", offset=offset, path=path)
    if bytes_length is not None and limits.max_bytes_length is not None and bytes_length > limits.max_bytes_length:
        raise InspectionError("Bytes length limit exceeded", offset=offset, path=path)


def _parse_value(reader: _Reader, *, path: str, depth: int, limits: DecodeLimits | None, stats: _Stats) -> InspectionNode:
    start = reader.index
    token = reader.read_u8(path=path)
    kind = TOKEN_NAMES.get(token, f"unknown:{token:#x}")
    stats.nodes += 1
    _check_limits(limits=limits, stats=stats, depth=depth, path=path, offset=start)

    if token == 0x00:
        return InspectionNode(kind, path, start, reader.index, "null")
    if token == 0x01:
        raw = reader.read_u8(path=path)
        if raw not in (0, 1):
            raise InspectionError("Invalid boolean payload", offset=reader.index - 1, path=path)
        return InspectionNode(kind, path, start, reader.index, "true" if raw else "false")
    if token == 0x02:
        value = reader.read_i64(path=path)
        return InspectionNode(kind, path, start, reader.index, str(value))
    if token == 0x03:
        value = reader.read_f64(path=path)
        return InspectionNode(kind, path, start, reader.index, repr(value))
    if token in {0x04, 0x0B, 0x0C, 0x0D, 0x0F, 0x10, 0x11}:
        length_offset = reader.index
        length = reader.read_u32(path=path)
        _check_limits(
            limits=limits,
            stats=stats,
            depth=depth,
            string_length=length,
            path=path,
            offset=length_offset,
        )
        raw = reader.read(length, path=path)
        return InspectionNode(kind, path, start, reader.index, _preview_text(raw, offset=reader.index - length, path=path))
    if token == 0x05:
        length_offset = reader.index
        length = reader.read_u32(path=path)
        _check_limits(
            limits=limits,
            stats=stats,
            depth=depth,
            bytes_length=length,
            path=path,
            offset=length_offset,
        )
        reader.read(length, path=path)
        return InspectionNode(kind, path, start, reader.index, f"{length} bytes")
    if token in {0x06, 0x08, 0x09, 0x0A, 0x0E, 0x12}:
        count_offset = reader.index
        count = reader.read_u32(path=path)
        _check_limits(
            limits=limits,
            stats=stats,
            depth=depth,
            container_length=count,
            path=path,
            offset=count_offset,
        )
        children: list[InspectionNode] = []
        labels = None
        if token == 0x0E:
            labels = ["days", "seconds", "microseconds"]
        elif token == 0x12:
            labels = ["real", "imag"]
        for index in range(count):
            suffix = f"[{index}]"
            if labels is not None and index < len(labels):
                suffix = f".{labels[index]}"
            children.append(
                _parse_value(
                    reader,
                    path=_child_path(path, suffix),
                    depth=depth + 1,
                    limits=limits,
                    stats=stats,
                )
            )
        return InspectionNode(kind, path, start, reader.index, f"{count} items", children)
    if token == 0x07:
        count_offset = reader.index
        count = reader.read_u32(path=path)
        _check_limits(
            limits=limits,
            stats=stats,
            depth=depth,
            container_length=count,
            path=path,
            offset=count_offset,
        )
        children: list[InspectionNode] = []
        for _ in range(count):
            key_length_offset = reader.index
            key_length = reader.read_u32(path=path)
            _check_limits(
                limits=limits,
                stats=stats,
                depth=depth,
                string_length=key_length,
                path=path,
                offset=key_length_offset,
            )
            key_bytes = reader.read(key_length, path=path)
            key = _preview_text(key_bytes, offset=reader.index - key_length, path=path)
            key_path = _child_path(path, f"[{key!r}]")
            children.append(
                _parse_value(
                    reader,
                    path=key_path,
                    depth=depth + 1,
                    limits=limits,
                    stats=stats,
                )
            )
        return InspectionNode(kind, path, start, reader.index, f"{count} pairs", children)

    raise InspectionError("Unknown token tag", offset=start, path=path)


def inspect_payload(data: bytes | bytearray | memoryview, *, limits: DecodeLimits | None = None) -> InspectionNode:
    raw = bytes(data)
    reader = _Reader(raw)
    if len(raw) < 5:
        raise InspectionError("Payload is too short to be TOONS", offset=0, path="root")
    if reader.read(4, path="root") != HEADER:
        raise InspectionError("Missing TOONS header", offset=0, path="root")
    version = reader.read_u8(path="root")
    if version not in (1, 2):
        raise InspectionError("Unsupported TOONS version", offset=4, path="root")

    stats = _Stats()
    node = _parse_value(reader, path="root", depth=0, limits=limits, stats=stats)
    if reader.index != len(raw):
        raise InspectionError("Trailing bytes detected", offset=reader.index, path="root")
    return node


def format_inspection(node: InspectionNode, *, indent: str = "  ") -> str:
    lines: list[str] = []

    def visit(current: InspectionNode, depth: int) -> None:
        prefix = indent * depth
        detail = f" {current.detail}" if current.detail else ""
        lines.append(
            f"{prefix}{current.path}: {current.kind}{detail} [{current.offset},{current.end_offset})"
        )
        for child in current.children:
            visit(child, depth + 1)

    visit(node, 0)
    return "\n".join(lines)
