from __future__ import annotations

import io
import random
from collections import namedtuple
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from pathlib import Path
from typing import TypedDict
from uuid import UUID

import pytest

import toons


class Status(Enum):
    ACTIVE = "active"


Point = namedtuple("Point", ["x", "y"])


@dataclass(frozen=True)
class Artist:
    name: str
    born: date
    active: bool


@dataclass(frozen=True)
class Token:
    value: str


class ArtistPayload(TypedDict):
    name: str
    born: date
    active: bool


def test_round_trip_nested_payload() -> None:
    payload = {
        "name": "TOONS",
        "version": 1,
        "flags": [True, False, None],
        "blob": b"\x00zig\x01",
        "coords": (1, 2, 3.5),
        "meta": {"lang": "python", "ffi": "zig"},
    }

    encoded = toons.dumps(payload)
    decoded = toons.loads(encoded)

    assert decoded == payload


def test_file_helpers_round_trip() -> None:
    payload = {"answer": 42, "items": ["a", "b", "c"]}
    buffer = io.BytesIO()

    toons.dump(payload, buffer)
    buffer.seek(0)

    assert toons.load(buffer) == payload


def test_round_trip_extended_python_types() -> None:
    payload = {
        "born": date(2026, 3, 24),
        "scheduled_for": datetime(2026, 3, 24, 12, 45, 30, 123456, tzinfo=timezone.utc),
        "alarm": time(8, 15, 45, 654321),
        "elapsed": timedelta(days=3, seconds=12, microseconds=345),
        "price": Decimal("19.9900"),
        "identifier": UUID("12345678-1234-5678-1234-567812345678"),
        "location": Path("assets/toons"),
        "labels": {"zig", "python", "ffi"},
        "frozen": frozenset({("x", 1), ("y", 2)}),
        "wave": complex(3.5, -2.25),
        "ratio": Fraction(2, 7),
        "status": Status.ACTIVE,
        "point": Point(3, 4),
        "big": 2**90 + 7,
        "artist": Artist("Amiya", date(1990, 1, 1), True),
    }

    assert toons.loads(toons.dumps(payload, deterministic=True)) == payload


def test_version_1_payloads_remain_readable() -> None:
    payload = {"name": "legacy", "count": 2, "items": [1, 2, 3]}
    encoded = bytearray(toons.dumps(payload))
    encoded[4] = 1

    assert toons.loads(bytes(encoded)) == payload


def test_non_string_dict_key_is_rejected() -> None:
    with pytest.raises(TypeError):
        toons.dumps({1: "bad"})


def test_big_integers_are_supported() -> None:
    value = 2**100 + 5
    assert toons.loads(toons.dumps(value)) == value


def test_bad_payload_raises_toons_error() -> None:
    with pytest.raises(toons.ToonsError):
        toons.loads(b"not-a-toons-payload")


def test_custom_codec_round_trip() -> None:
    registry = toons.CodecRegistry()
    registry.register(Token, "custom.token", lambda token: {"value": token.value}, lambda payload: Token(payload["value"]))

    payload = {"token": Token("abc123")}

    assert toons.loads(toons.dumps(payload, registry=registry), registry=registry) == payload


def test_schema_aware_loading() -> None:
    payload: ArtistPayload = {"name": "Amiya", "born": date(1990, 1, 1), "active": True}
    encoded = toons.dumps(payload)

    assert toons.loads_as(encoded, ArtistPayload) == payload
    assert toons.loads_as(encoded, Artist) == Artist(**payload)


def test_canonical_encoding_is_stable() -> None:
    first = {"b": 2, "a": 1}
    second = {"a": 1, "b": 2}

    assert toons.canonical_dumps(first) == toons.canonical_dumps(second)
    assert toons.dumps(first) != toons.dumps(second)


def test_pack_and_unseal_with_compression_and_hmac() -> None:
    payload = {"name": "TOONS", "numbers": list(range(100))}
    sealed = toons.seal(payload, "shared-secret", compression="gzip")

    assert toons.unseal(sealed, "shared-secret") == payload
    with pytest.raises(toons.ToonsError):
        toons.unseal(sealed, "wrong-secret")


def test_stream_framing_round_trip() -> None:
    items = [
        {"name": "one"},
        Artist("Amiya", date(1990, 1, 1), True),
        {"point": Point(3, 4), "status": Status.ACTIVE},
    ]
    buffer = io.BytesIO()

    toons.stream_dump(items, buffer, deterministic=True)
    buffer.seek(0)

    assert list(toons.stream_load(buffer)) == items


def test_iterloads_round_trip() -> None:
    items = [{"n": 1}, {"n": 2}]
    buffer = io.BytesIO()
    toons.stream_dump(items, buffer)
    raw = buffer.getvalue()
    chunks = [raw[:7], raw[7:]]

    assert list(toons.iterloads(chunks)) == items


def test_inspection_and_limits() -> None:
    payload = toons.dumps({"items": [1, {"deep": ["x"]}]})
    inspection = toons.inspect_text(payload)

    assert "root['items'][1]['deep'][0]" in inspection
    with pytest.raises(toons.ToonsError, match="Maximum nesting depth exceeded"):
        toons.loads(payload, limits=toons.DecodeLimits(max_depth=2))


def test_text_round_trip_simple_object() -> None:
    payload = {"id": 123, "name": "Ada", "active": True}
    text = toons.encode_text(payload)

    assert text == "id: 123\nname: Ada\nactive: true"
    assert toons.decode_text(text) == payload


def test_text_round_trip_tabular_array() -> None:
    payload = {
        "items": [
            {"sku": "A1", "qty": 2, "price": 9.99},
            {"sku": "B2", "qty": 1, "price": 14.5},
        ]
    }
    text = toons.encode_text(payload)

    assert text == "items[2]{sku,qty,price}:\n  A1,2,9.99\n  B2,1,14.5"
    assert toons.decode_text(text) == payload


def test_text_round_trip_mixed_array_examples() -> None:
    text = (
        "items[3]:\n"
        "  - 1\n"
        "  - a: 1\n"
        "  - text\n"
    )

    assert toons.decode_text(text) == {"items": [1, {"a": 1}, "text"]}
    assert toons.decode_text(toons.encode_text({"items": [1, {"a": 1}, "text"]})) == {
        "items": [1, {"a": 1}, "text"]
    }


def test_text_root_array_and_root_primitive() -> None:
    assert toons.encode_text([1, 2, 3]) == "[3]: 1,2,3"
    assert toons.decode_text("[3]: 1,2,3") == [1, 2, 3]
    assert toons.decode_text("[2]:\n  - 1\n  - 2\n") == [1, 2]
    assert toons.encode_text("Hello 世界") == "Hello 世界"
    assert toons.decode_text("42") == 42


def test_text_key_folding_and_path_expansion() -> None:
    payload = {"data": {"metadata": {"items": ["a", "b"]}}}
    text = toons.encode_text(payload, key_folding="safe")

    assert text == "data.metadata.items[2]: a,b"
    assert toons.decode_text(text, expand_paths="safe") == payload
    assert toons.decode_text(text) == {"data.metadata.items": ["a", "b"]}


def test_text_auto_delimiter_avoids_extra_quotes() -> None:
    payload = {
        "items": [
            {"sku": "A1", "name": "Widget, Large"},
            {"sku": "B2", "name": "Gadget, Small"},
        ]
    }

    text = toons.encode_text(payload, delimiter="auto")

    assert "[2\t]{sku\tname}:" in text
    assert '"Widget, Large"' not in text
    assert toons.decode_text(text) == payload


def test_encode_llm_text_prefers_folded_compact_output() -> None:
    payload = {"data": {"metadata": {"items": ["alpha,beta", "gamma,delta"]}}}

    text = toons.encode_llm_text(payload)

    assert text.startswith("data.metadata.items[2\t]: ")
    assert '"alpha,beta"' not in text
    assert toons.decode_text(text, expand_paths="safe") == payload


def test_text_round_trip_extended_python_values() -> None:
    payload = {
        "born": date(2026, 3, 24),
        "price": Decimal("19.9900"),
        "blob": b"\x00zig\x01",
        "ratio": Fraction(2, 7),
    }

    assert toons.decode_text(toons.encode_text(payload)) == payload


def test_text_reserved_key_round_trip() -> None:
    payload = {"$toons": {"kind": "dict", "payload": {"a": 1}}}

    assert toons.decode_text(toons.encode_text(payload)) == payload


def test_fuzz_style_round_trip() -> None:
    rng = random.Random(42)

    def generate(depth: int = 0):
        primitives = [
            None,
            True,
            False,
            rng.randint(-(2**70), 2**70),
            rng.random() * 1000,
            f"text-{rng.randint(0, 999)}",
            bytes([rng.randint(0, 255) for _ in range(rng.randint(0, 4))]),
            Fraction(rng.randint(1, 9), rng.randint(1, 9)),
            Decimal(f"{rng.randint(0, 99)}.{rng.randint(0, 9999):04d}"),
            date(2026, rng.randint(1, 12), rng.randint(1, 28)),
            UUID("12345678-1234-5678-1234-567812345678"),
            Path(f"asset-{rng.randint(1, 9)}"),
        ]
        if depth >= 2:
            return rng.choice(primitives)
        choice = rng.randint(0, 4)
        if choice == 0:
            return [generate(depth + 1) for _ in range(rng.randint(0, 3))]
        if choice == 1:
            return {f"k{index}": generate(depth + 1) for index in range(rng.randint(0, 3))}
        if choice == 2:
            return tuple(generate(depth + 1) for _ in range(rng.randint(0, 3)))
        if choice == 3:
            return {rng.randint(0, 5), rng.randint(6, 10)}
        return rng.choice(primitives)

    for _ in range(25):
        value = generate()
        assert toons.loads(toons.dumps(value, deterministic=True)) == value
