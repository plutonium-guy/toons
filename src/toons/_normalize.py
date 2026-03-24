from __future__ import annotations

import base64
import dataclasses
import enum
import importlib
import math
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from fractions import Fraction
from functools import lru_cache
from pathlib import PurePath
from typing import Any
from uuid import UUID

from .codecs import CodecRegistry

MAGIC_KEY = "\x00toons"


def stable_sort_key(value: Any) -> Any:
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", int(value))
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", repr(value))
    if isinstance(value, complex):
        return ("complex", repr(value.real), repr(value.imag))
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return ("bytes", bytes(value))
    if isinstance(value, tuple):
        return ("tuple", tuple(stable_sort_key(item) for item in value))
    if isinstance(value, frozenset):
        return ("frozenset", tuple(sorted(stable_sort_key(item) for item in value)))
    if isinstance(value, set):
        return ("set", tuple(sorted(stable_sort_key(item) for item in value)))
    return (type(value).__module__, type(value).__qualname__, repr(value))


def _qualname(tp: type[Any]) -> str:
    return f"{tp.__module__}:{tp.__qualname__}"


@lru_cache(maxsize=256)
def _load_symbol(name: str) -> Any:
    module_name, qualname = name.split(":", 1)
    module = importlib.import_module(module_name)
    symbol: Any = module
    for part in qualname.split("."):
        symbol = getattr(symbol, part)
    return symbol


def _extension(name: str, payload: Any) -> dict[str, Any]:
    return {MAGIC_KEY: ["ext", name, payload]}


def _escaped_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {MAGIC_KEY: ["dict", payload]}


def _normalize_extension(
    name: str,
    payload: Any,
    *,
    registry: CodecRegistry | None,
    deterministic: bool,
    allow_nan: bool,
) -> dict[str, Any]:
    return _extension(
        name,
        normalize_value(
            payload,
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        ),
    )


def _normalize_sequence(
    values: list[Any],
    *,
    registry: CodecRegistry | None,
    deterministic: bool,
    allow_nan: bool,
) -> list[Any]:
    return [
        normalize_value(
            item,
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )
        for item in values
    ]


def _normalize_mapping(
    value: Mapping[str, Any],
    *,
    registry: CodecRegistry | None,
    deterministic: bool,
    allow_nan: bool,
) -> dict[str, Any]:
    items = value.items()
    if deterministic:
        items = sorted(items, key=lambda item: item[0])

    normalized: dict[str, Any] = {}
    for key, item in items:
        if not isinstance(key, str):
            raise TypeError("TOONS only supports string keys in dictionaries")
        normalized[key] = normalize_value(
            item,
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if MAGIC_KEY in normalized:
        return _escaped_dict(normalized)
    return normalized


def normalize_value(
    value: Any,
    *,
    registry: CodecRegistry | None = None,
    deterministic: bool = False,
    allow_nan: bool = True,
) -> Any:
    codec = registry.encoder_for(value) if registry is not None else None
    if codec is not None:
        return _normalize_extension(
            codec.name,
            codec.encode(value),
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if value is None or isinstance(value, (bool, str)):
        return value

    if isinstance(value, (bytes, bytearray, memoryview)):
        return _normalize_extension(
            "python.bytes",
            base64.b64encode(bytes(value)).decode("ascii"),
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, enum.Enum):
        return _normalize_extension(
            f"enum:{_qualname(type(value))}",
            value.name,
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, int):
        if -(2**63) <= value <= (2**63) - 1:
            return value
        return _normalize_extension(
            "python.bigint",
            str(value),
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, float):
        if math.isfinite(value):
            return value
        if not allow_nan:
            raise ValueError("NaN and infinity are disabled for this TOONS dump")
        return _normalize_extension(
            "python.float",
            "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf"),
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, complex):
        return _normalize_extension(
            "python.complex",
            [value.real, value.imag],
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, datetime):
        return _normalize_extension(
            "python.datetime",
            value.isoformat(),
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, date):
        return _normalize_extension(
            "python.date",
            value.isoformat(),
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, time):
        return _normalize_extension(
            "python.time",
            value.isoformat(),
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, timedelta):
        return _normalize_extension(
            "python.timedelta",
            [value.days, value.seconds, value.microseconds],
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, Decimal):
        return _normalize_extension(
            "python.decimal",
            str(value),
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, UUID):
        return _normalize_extension(
            "python.uuid",
            str(value),
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, tuple) and hasattr(type(value), "_fields"):
        return _normalize_extension(
            f"namedtuple:{_qualname(type(value))}",
            value._asdict(),
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, tuple):
        return _normalize_extension(
            "python.tuple",
            list(value),
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, list):
        return _normalize_sequence(
            value,
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, set):
        ordered = sorted(value, key=stable_sort_key) if deterministic else list(value)
        return _normalize_extension(
            "python.set",
            ordered,
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, frozenset):
        ordered = sorted(value, key=stable_sort_key) if deterministic else list(value)
        return _normalize_extension(
            "python.frozenset",
            ordered,
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, Mapping):
        return _normalize_mapping(
            value,
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, Fraction):
        return _normalize_extension(
            "fractions.Fraction",
            {"numerator": value.numerator, "denominator": value.denominator},
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if isinstance(value, PurePath):
        return _normalize_extension(
            f"path:{_qualname(type(value))}",
            str(value),
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        payload = {
            field.name: getattr(value, field.name)
            for field in dataclasses.fields(value)
        }
        return _normalize_extension(
            f"dataclass:{_qualname(type(value))}",
            payload,
            registry=registry,
            deterministic=deterministic,
            allow_nan=allow_nan,
        )

    raise TypeError(f"Unsupported TOONS type: {type(value).__name__}")


def _denormalize_mapping(value: Mapping[str, Any], *, registry: CodecRegistry | None) -> dict[str, Any]:
    return {key: denormalize_value(item, registry=registry) for key, item in value.items()}


def _decode_extension(name: str, payload: Any, *, registry: CodecRegistry | None) -> Any:
    if name == "python.bytes":
        return base64.b64decode(payload.encode("ascii"))

    if name == "python.bigint":
        return int(payload)

    if name == "python.float":
        if payload == "nan":
            return float("nan")
        if payload == "inf":
            return float("inf")
        if payload == "-inf":
            return float("-inf")
        raise ValueError(f"Unknown special float payload: {payload}")

    if name == "python.complex":
        return complex(payload[0], payload[1])

    if name == "python.date":
        return date.fromisoformat(payload)

    if name == "python.time":
        return time.fromisoformat(payload)

    if name == "python.datetime":
        return datetime.fromisoformat(payload)

    if name == "python.timedelta":
        return timedelta(days=payload[0], seconds=payload[1], microseconds=payload[2])

    if name == "python.decimal":
        return Decimal(payload)

    if name == "python.uuid":
        return UUID(payload)

    if name == "python.tuple":
        return tuple(payload)

    if name == "python.set":
        return set(payload)

    if name == "python.frozenset":
        return frozenset(payload)

    if name == "fractions.Fraction":
        return Fraction(payload["numerator"], payload["denominator"])

    if name.startswith("enum:"):
        tp = _load_symbol(name.removeprefix("enum:"))
        return tp[payload]

    if name.startswith("path:"):
        tp = _load_symbol(name.removeprefix("path:"))
        return tp(payload)

    if name.startswith("dataclass:"):
        tp = _load_symbol(name.removeprefix("dataclass:"))
        return tp(**payload)

    if name.startswith("namedtuple:"):
        tp = _load_symbol(name.removeprefix("namedtuple:"))
        return tp(**payload)

    codec = registry.decoder_for(name) if registry is not None else None
    if codec is None:
        raise ValueError(f"Unknown TOONS extension payload: {name}")
    return codec.decode(payload)


def denormalize_value(value: Any, *, registry: CodecRegistry | None = None) -> Any:
    if isinstance(value, list):
        return [denormalize_value(item, registry=registry) for item in value]

    if isinstance(value, dict):
        if set(value.keys()) == {MAGIC_KEY}:
            marker = value[MAGIC_KEY]
            if not isinstance(marker, list) or not marker:
                raise ValueError("Malformed TOONS extension envelope")
            kind = marker[0]
            if kind == "dict":
                if len(marker) != 2 or not isinstance(marker[1], Mapping):
                    raise ValueError("Malformed TOONS escaped dictionary envelope")
                return _denormalize_mapping(marker[1], registry=registry)
            if kind == "ext":
                if len(marker) != 3:
                    raise ValueError("Malformed TOONS extension envelope")
                payload = denormalize_value(marker[2], registry=registry)
                return _decode_extension(marker[1], payload, registry=registry)
            raise ValueError(f"Unknown TOONS envelope kind: {kind}")
        return _denormalize_mapping(value, registry=registry)

    return value
