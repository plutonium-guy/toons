from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

EncodeHook = Callable[[Any], Any]
DecodeHook = Callable[[Any], Any]


@dataclass(frozen=True)
class Codec:
    python_type: type[Any]
    name: str
    encode: EncodeHook
    decode: DecodeHook


@dataclass
class CodecRegistry:
    _entries: list[Codec] = field(default_factory=list)
    _decoder_index: dict[str, Codec] = field(default_factory=dict)

    def register(
        self,
        python_type: type[Any],
        name: str,
        encode: EncodeHook,
        decode: DecodeHook,
    ) -> None:
        codec = Codec(
            python_type=python_type,
            name=name,
            encode=encode,
            decode=decode,
        )
        self._entries.append(codec)
        self._decoder_index[name] = codec

    def copy(self) -> CodecRegistry:
        clone = CodecRegistry(list(self._entries))
        clone._decoder_index = dict(self._decoder_index)
        return clone

    def encoder_for(self, value: Any) -> Codec | None:
        for entry in reversed(self._entries):
            if isinstance(value, entry.python_type):
                return entry
        return None

    def decoder_for(self, name: str) -> Codec | None:
        return self._decoder_index.get(name)
