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

    def register(
        self,
        python_type: type[Any],
        name: str,
        encode: EncodeHook,
        decode: DecodeHook,
    ) -> None:
        self._entries.append(
            Codec(
                python_type=python_type,
                name=name,
                encode=encode,
                decode=decode,
            )
        )

    def copy(self) -> CodecRegistry:
        return CodecRegistry(list(self._entries))

    def encoder_for(self, value: Any) -> Codec | None:
        for entry in reversed(self._entries):
            if isinstance(value, entry.python_type):
                return entry
        return None

    def decoder_for(self, name: str) -> Codec | None:
        for entry in reversed(self._entries):
            if entry.name == name:
                return entry
        return None
