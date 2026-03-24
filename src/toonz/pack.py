from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import lzma
import zlib
from typing import Any

from .options import CompressionKind, DigestKind

PACK_MAGIC = b"TSPK"
PACK_VERSION = 1


def _compress(data: bytes, kind: CompressionKind | None) -> bytes:
    if kind is None:
        return data
    if kind == "gzip":
        return gzip.compress(data)
    if kind == "lzma":
        return lzma.compress(data)
    if kind == "zlib":
        return zlib.compress(data)
    raise ValueError(f"Unsupported compression kind: {kind}")


def _decompress(data: bytes, kind: CompressionKind | None) -> bytes:
    if kind is None:
        return data
    if kind == "gzip":
        return gzip.decompress(data)
    if kind == "lzma":
        return lzma.decompress(data)
    if kind == "zlib":
        return zlib.decompress(data)
    raise ValueError(f"Unsupported compression kind: {kind}")


def _ensure_key(secret: bytes | str) -> bytes:
    if isinstance(secret, bytes):
        return secret
    return secret.encode("utf-8")


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def pack_bytes(
    payload: bytes,
    *,
    compression: CompressionKind | None = None,
    checksum: DigestKind | None = "sha256",
    secret: bytes | str | None = None,
    hmac_digest: DigestKind = "sha256",
) -> bytes:
    body = _compress(payload, compression)
    metadata: dict[str, Any] = {
        "compression": compression,
        "checksum": checksum,
        "digest": hashlib.new(checksum, body).hexdigest() if checksum else None,
        "hmac": hmac_digest if secret is not None else None,
    }
    header_no_mac = _canonical_json(metadata)
    if secret is not None:
        metadata["mac"] = hmac.new(_ensure_key(secret), header_no_mac + body, hmac_digest).hexdigest()

    header = _canonical_json(metadata)
    return PACK_MAGIC + bytes([PACK_VERSION]) + len(header).to_bytes(4, "little") + header + body


def unpack_bytes(
    packed: bytes | bytearray | memoryview,
    *,
    secret: bytes | str | None = None,
) -> bytes:
    raw = bytes(packed)
    if len(raw) < 9 or raw[:4] != PACK_MAGIC:
        raise ValueError("Not a packed TOONS envelope")
    version = raw[4]
    if version != PACK_VERSION:
        raise ValueError(f"Unsupported TOONS pack version {version}")
    header_length = int.from_bytes(raw[5:9], "little")
    header_end = 9 + header_length
    header = json.loads(raw[9:header_end].decode("utf-8"))
    body = raw[header_end:]

    if header.get("checksum"):
        digest = hashlib.new(header["checksum"], body).hexdigest()
        if digest != header.get("digest"):
            raise ValueError("TOONS envelope checksum verification failed")

    if header.get("hmac") is not None:
        if secret is None:
            raise ValueError("A secret is required to unseal this TOONS envelope")
        metadata = dict(header)
        mac = metadata.pop("mac", None)
        expected = hmac.new(
            _ensure_key(secret),
            _canonical_json(metadata) + body,
            metadata["hmac"],
        ).hexdigest()
        if not hmac.compare_digest(mac or "", expected):
            raise ValueError("TOONS envelope HMAC verification failed")

    return _decompress(body, header.get("compression"))
