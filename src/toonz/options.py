from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CompressionKind = Literal["gzip", "lzma", "zlib"]
DigestKind = Literal["blake2b", "sha256", "sha512"]


@dataclass(frozen=True)
class DumpOptions:
    deterministic: bool = False
    allow_nan: bool = True


@dataclass(frozen=True)
class DecodeLimits:
    max_depth: int | None = None
    max_container_length: int | None = None
    max_string_length: int | None = None
    max_bytes_length: int | None = None
    max_total_nodes: int | None = None
