from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, BinaryIO, Callable

FRAME_MAGIC = b"TSF1"


def iterencode(payload: bytes, *, chunk_size: int = 8192) -> Iterator[bytes]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    for start in range(0, len(payload), chunk_size):
        yield payload[start : start + chunk_size]


def dump_chunks(payload: bytes, fp: BinaryIO, *, chunk_size: int = 8192) -> None:
    for chunk in iterencode(payload, chunk_size=chunk_size):
        fp.write(chunk)


def frame_bytes(payload: bytes) -> bytes:
    return FRAME_MAGIC + len(payload).to_bytes(8, "little") + payload


def write_framed(
    objects: Iterable[Any],
    fp: BinaryIO,
    *,
    encoder: Callable[[Any], bytes],
) -> None:
    for obj in objects:
        fp.write(frame_bytes(encoder(obj)))


def read_framed(
    fp: BinaryIO,
    *,
    decoder: Callable[[bytes], Any],
) -> Iterator[Any]:
    while True:
        header = fp.read(4)
        if not header:
            return
        if header != FRAME_MAGIC:
            raise ValueError("Invalid TOONS stream frame header")
        size_raw = fp.read(8)
        if len(size_raw) != 8:
            raise ValueError("Unexpected end of TOONS frame stream")
        size = int.from_bytes(size_raw, "little")
        payload = fp.read(size)
        if len(payload) != size:
            raise ValueError("Unexpected end of TOONS frame payload")
        yield decoder(payload)


def iterdecode(
    chunks: Iterable[bytes],
    *,
    decoder: Callable[[bytes], Any],
) -> Iterator[Any]:
    buffer = bytearray()
    for chunk in chunks:
        buffer.extend(chunk)
        while True:
            if len(buffer) < 12:
                break
            if bytes(buffer[:4]) != FRAME_MAGIC:
                raise ValueError("Invalid TOONS stream frame header")
            size = int.from_bytes(buffer[4:12], "little")
            frame_end = 12 + size
            if len(buffer) < frame_end:
                break
            payload = bytes(buffer[12:frame_end])
            del buffer[:frame_end]
            yield decoder(payload)
    if buffer:
        raise ValueError("Trailing partial TOONS frame detected")
