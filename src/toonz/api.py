from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, BinaryIO

from . import _ffi
from ._normalize import denormalize_value, normalize_value
from .codecs import CodecRegistry
from .inspection import InspectionError, format_inspection, inspect_payload
from .options import CompressionKind, DecodeLimits, DigestKind
from .pack import pack_bytes, unpack_bytes
from .schema import cast
from .stream import dump_chunks, iterdecode as _iterdecode_frames, iterencode as _iterencode_chunks
from .stream import read_framed, write_framed
from .text import decode_text as _decode_text
from .text import encode_text as _encode_text
from .text import encode_llm_text as _encode_llm_text


class ToonsError(RuntimeError):
    """Raised when the TOONS native layer reports an error."""


def dumps(
    obj: Any,
    *,
    deterministic: bool = False,
    allow_nan: bool = True,
    registry: CodecRegistry | None = None,
) -> bytes:
    try:
        return _ffi.serialize(
            normalize_value(
                obj,
                registry=registry,
                deterministic=deterministic,
                allow_nan=allow_nan,
            ),
            deterministic=deterministic,
        )
    except RuntimeError as exc:
        raise ToonsError(str(exc)) from exc


def canonical_dumps(
    obj: Any,
    *,
    allow_nan: bool = True,
    registry: CodecRegistry | None = None,
) -> bytes:
    return dumps(obj, deterministic=True, allow_nan=allow_nan, registry=registry)


def dump(
    obj: Any,
    fp: BinaryIO,
    *,
    deterministic: bool = False,
    allow_nan: bool = True,
    registry: CodecRegistry | None = None,
) -> None:
    fp.write(dumps(obj, deterministic=deterministic, allow_nan=allow_nan, registry=registry))


def loads(
    data: bytes | bytearray | memoryview,
    *,
    registry: CodecRegistry | None = None,
    limits: DecodeLimits | None = None,
    schema: Any = None,
) -> Any:
    raw = bytes(data)
    if limits is not None:
        try:
            inspect_payload(raw, limits=limits)
        except InspectionError as exc:
            raise ToonsError(str(exc)) from exc
    try:
        value = _ffi.deserialize(raw)
    except RuntimeError as exc:
        try:
            inspect_payload(raw)
        except InspectionError as detail:
            raise ToonsError(str(detail)) from exc
        raise ToonsError(str(exc)) from exc

    try:
        restored = denormalize_value(value, registry=registry)
        return cast(restored, schema) if schema is not None else restored
    except (TypeError, ValueError) as exc:
        raise ToonsError(str(exc)) from exc


def loads_as(
    data: bytes | bytearray | memoryview,
    schema: Any,
    *,
    registry: CodecRegistry | None = None,
    limits: DecodeLimits | None = None,
) -> Any:
    return loads(data, registry=registry, limits=limits, schema=schema)


def load(
    fp: BinaryIO,
    *,
    registry: CodecRegistry | None = None,
    limits: DecodeLimits | None = None,
    schema: Any = None,
) -> Any:
    return loads(fp.read(), registry=registry, limits=limits, schema=schema)


def load_as(
    fp: BinaryIO,
    schema: Any,
    *,
    registry: CodecRegistry | None = None,
    limits: DecodeLimits | None = None,
) -> Any:
    return loads_as(fp.read(), schema, registry=registry, limits=limits)


def inspect_tree(
    data: bytes | bytearray | memoryview,
    *,
    limits: DecodeLimits | None = None,
):
    return inspect_payload(data, limits=limits)


def inspect_text(
    data: bytes | bytearray | memoryview,
    *,
    limits: DecodeLimits | None = None,
) -> str:
    return format_inspection(inspect_tree(data, limits=limits))


def encode_text(
    obj: Any,
    *,
    delimiter: str = ",",
    deterministic: bool = False,
    allow_nan: bool = True,
    registry: CodecRegistry | None = None,
    key_folding: str = "off",
    flatten_depth: int | None = None,
    optimize_for_llm: bool = False,
) -> str:
    try:
        return _encode_text(
            obj,
            delimiter=delimiter,
            deterministic=deterministic,
            allow_nan=allow_nan,
            registry=registry,
            key_folding=key_folding,
            flatten_depth=flatten_depth,
            optimize_for_llm=optimize_for_llm,
        )
    except ValueError as exc:
        raise ToonsError(str(exc)) from exc


def encode_llm_text(
    obj: Any,
    *,
    deterministic: bool = True,
    allow_nan: bool = True,
    registry: CodecRegistry | None = None,
    flatten_depth: int | None = None,
) -> str:
    try:
        return _encode_llm_text(
            obj,
            deterministic=deterministic,
            allow_nan=allow_nan,
            registry=registry,
            flatten_depth=flatten_depth,
        )
    except ValueError as exc:
        raise ToonsError(str(exc)) from exc


def decode_text(
    text: str,
    *,
    registry: CodecRegistry | None = None,
    schema: Any = None,
    expand_paths: str = "off",
) -> Any:
    try:
        return _decode_text(
            text,
            registry=registry,
            schema=schema,
            expand_paths=expand_paths,
        )
    except (TypeError, ValueError) as exc:
        raise ToonsError(str(exc)) from exc


def pack(
    obj: Any,
    *,
    deterministic: bool = False,
    allow_nan: bool = True,
    registry: CodecRegistry | None = None,
    compression: CompressionKind | None = None,
    checksum: DigestKind | None = "sha256",
    secret: bytes | str | None = None,
    hmac_digest: DigestKind = "sha256",
) -> bytes:
    payload = dumps(obj, deterministic=deterministic, allow_nan=allow_nan, registry=registry)
    try:
        return pack_bytes(
            payload,
            compression=compression,
            checksum=checksum,
            secret=secret,
            hmac_digest=hmac_digest,
        )
    except ValueError as exc:
        raise ToonsError(str(exc)) from exc


def unpack(
    data: bytes | bytearray | memoryview,
    *,
    registry: CodecRegistry | None = None,
    limits: DecodeLimits | None = None,
    schema: Any = None,
    secret: bytes | str | None = None,
) -> Any:
    try:
        payload = unpack_bytes(data, secret=secret)
    except ValueError as exc:
        raise ToonsError(str(exc)) from exc
    return loads(payload, registry=registry, limits=limits, schema=schema)


def seal(
    obj: Any,
    secret: bytes | str,
    *,
    deterministic: bool = False,
    allow_nan: bool = True,
    registry: CodecRegistry | None = None,
    compression: CompressionKind | None = None,
    checksum: DigestKind | None = "sha256",
    hmac_digest: DigestKind = "sha256",
) -> bytes:
    return pack(
        obj,
        deterministic=deterministic,
        allow_nan=allow_nan,
        registry=registry,
        compression=compression,
        checksum=checksum,
        secret=secret,
        hmac_digest=hmac_digest,
    )


def unseal(
    data: bytes | bytearray | memoryview,
    secret: bytes | str,
    *,
    registry: CodecRegistry | None = None,
    limits: DecodeLimits | None = None,
    schema: Any = None,
) -> Any:
    return unpack(data, registry=registry, limits=limits, schema=schema, secret=secret)


def iterencode(
    obj: Any,
    *,
    chunk_size: int = 8192,
    deterministic: bool = False,
    allow_nan: bool = True,
    registry: CodecRegistry | None = None,
) -> Iterator[bytes]:
    return _iterencode_chunks(
        dumps(obj, deterministic=deterministic, allow_nan=allow_nan, registry=registry),
        chunk_size=chunk_size,
    )


def iterdump(
    obj: Any,
    fp: BinaryIO,
    *,
    chunk_size: int = 8192,
    deterministic: bool = False,
    allow_nan: bool = True,
    registry: CodecRegistry | None = None,
) -> None:
    dump_chunks(
        dumps(obj, deterministic=deterministic, allow_nan=allow_nan, registry=registry),
        fp,
        chunk_size=chunk_size,
    )


def stream_dump(
    objects: Iterable[Any],
    fp: BinaryIO,
    *,
    deterministic: bool = False,
    allow_nan: bool = True,
    registry: CodecRegistry | None = None,
) -> None:
    write_framed(
        objects,
        fp,
        encoder=lambda obj: dumps(
            obj,
            deterministic=deterministic,
            allow_nan=allow_nan,
            registry=registry,
        ),
    )


def stream_load(
    fp: BinaryIO,
    *,
    registry: CodecRegistry | None = None,
    limits: DecodeLimits | None = None,
    schema: Any = None,
) -> Iterator[Any]:
    return read_framed(
        fp,
        decoder=lambda payload: loads(payload, registry=registry, limits=limits, schema=schema),
    )


def iterloads(
    chunks: Iterable[bytes],
    *,
    registry: CodecRegistry | None = None,
    limits: DecodeLimits | None = None,
    schema: Any = None,
) -> Iterator[Any]:
    return _iterdecode_frames(
        chunks,
        decoder=lambda payload: loads(payload, registry=registry, limits=limits, schema=schema),
    )
