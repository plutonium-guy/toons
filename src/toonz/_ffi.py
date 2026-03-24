from __future__ import annotations

import ctypes
import json
from pathlib import Path
from typing import Any

from ._build_zig import build_native, library_filename, should_rebuild

U8 = ctypes.c_ubyte
U8Ptr = ctypes.POINTER(U8)


class ToonsSlice(ctypes.Structure):
    _fields_ = [("ptr", U8Ptr), ("len", ctypes.c_size_t)]


_LIB: ctypes.CDLL | None = None


def _source_native_dir() -> Path:
    return Path(__file__).resolve().parent / "_native"


def _load_library() -> ctypes.CDLL:
    global _LIB
    if _LIB is not None:
        return _LIB

    native_dir = _source_native_dir()
    library_path = native_dir / library_filename()
    if should_rebuild(native_dir):
        build_native(native_dir)

    try:
        lib = ctypes.CDLL(str(library_path))
    except OSError:
        build_native(native_dir)
        lib = ctypes.CDLL(str(library_path))

    lib.toons_serialize_json.argtypes = [
        U8Ptr,
        ctypes.c_size_t,
        ctypes.c_bool,
        ctypes.POINTER(U8Ptr),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    lib.toons_serialize_json.restype = ctypes.c_bool

    lib.toons_deserialize_json.argtypes = [
        U8Ptr,
        ctypes.c_size_t,
        ctypes.POINTER(U8Ptr),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    lib.toons_deserialize_json.restype = ctypes.c_bool

    lib.toons_render_json_text.argtypes = [
        U8Ptr,
        ctypes.c_size_t,
        U8,
        ctypes.POINTER(U8Ptr),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    lib.toons_render_json_text.restype = ctypes.c_bool

    lib.toons_parse_text_json.argtypes = [
        U8Ptr,
        ctypes.c_size_t,
        ctypes.POINTER(U8Ptr),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    lib.toons_parse_text_json.restype = ctypes.c_bool

    lib.toons_free_buffer.argtypes = [U8Ptr, ctypes.c_size_t]
    lib.toons_free_buffer.restype = None

    lib.toons_last_error_message.argtypes = []
    lib.toons_last_error_message.restype = ToonsSlice

    _LIB = lib
    return lib


def _to_native_buffer(data: bytes) -> tuple[Any, U8Ptr]:
    buffer = (U8 * len(data)).from_buffer_copy(data)
    return buffer, ctypes.cast(buffer, U8Ptr)


def last_error_message() -> str:
    lib = _load_library()
    slice_value = lib.toons_last_error_message()
    if not slice_value.ptr or slice_value.len == 0:
        return "Unknown TOONS native error"
    return ctypes.string_at(slice_value.ptr, slice_value.len).decode("utf-8", errors="replace")


def serialize(obj: Any, *, deterministic: bool = False) -> bytes:
    lib = _load_library()
    json_payload = json.dumps(
        obj,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    input_buffer, input_ptr = _to_native_buffer(json_payload)
    _ = input_buffer

    output_ptr = U8Ptr()
    output_len = ctypes.c_size_t(0)
    if not lib.toons_serialize_json(
        input_ptr,
        len(json_payload),
        deterministic,
        ctypes.byref(output_ptr),
        ctypes.byref(output_len),
    ):
        raise RuntimeError(last_error_message())

    try:
        return ctypes.string_at(output_ptr, output_len.value)
    finally:
        lib.toons_free_buffer(output_ptr, output_len.value)


def deserialize(data: bytes | bytearray | memoryview) -> Any:
    lib = _load_library()
    raw = bytes(data)
    if not raw:
        raise ValueError("TOONS payload cannot be empty")
    input_buffer, input_ptr = _to_native_buffer(raw)
    _ = input_buffer

    output_ptr = U8Ptr()
    output_len = ctypes.c_size_t(0)
    if not lib.toons_deserialize_json(
        input_ptr,
        len(raw),
        ctypes.byref(output_ptr),
        ctypes.byref(output_len),
    ):
        raise RuntimeError(last_error_message())

    try:
        text = ctypes.string_at(output_ptr, output_len.value).decode("utf-8")
    finally:
        lib.toons_free_buffer(output_ptr, output_len.value)

    return json.loads(text)


def render_text(obj: Any, *, delimiter: str) -> str:
    lib = _load_library()
    json_payload = json.dumps(
        obj,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    input_buffer, input_ptr = _to_native_buffer(json_payload)
    _ = input_buffer

    output_ptr = U8Ptr()
    output_len = ctypes.c_size_t(0)
    delimiter_byte = delimiter.encode("utf-8")
    if len(delimiter_byte) != 1:
        raise ValueError("Delimiter must be a single byte")
    if not lib.toons_render_json_text(
        input_ptr,
        len(json_payload),
        delimiter_byte[0],
        ctypes.byref(output_ptr),
        ctypes.byref(output_len),
    ):
        raise RuntimeError(last_error_message())

    try:
        return ctypes.string_at(output_ptr, output_len.value).decode("utf-8")
    finally:
        lib.toons_free_buffer(output_ptr, output_len.value)


def parse_text(text: str) -> Any:
    lib = _load_library()
    raw = text.encode("utf-8")
    input_buffer, input_ptr = _to_native_buffer(raw)
    _ = input_buffer

    output_ptr = U8Ptr()
    output_len = ctypes.c_size_t(0)
    if not lib.toons_parse_text_json(
        input_ptr,
        len(raw),
        ctypes.byref(output_ptr),
        ctypes.byref(output_len),
    ):
        raise RuntimeError(last_error_message())

    try:
        return json.loads(ctypes.string_at(output_ptr, output_len.value).decode("utf-8"))
    finally:
        lib.toons_free_buffer(output_ptr, output_len.value)
