# TOONS

**TOONS** stands for **Token Oriented Object Notation Serializer**.

It is a Python package with a Zig native core that gives you:

- a compact binary serializer for rich Python data
- TOON text encode/decode for structured, human-readable payloads
- an LLM-friendly text mode that prefers low-friction output
- custom codecs, typed loading, framing, inspection, packing, and sealing

TOONS is built for cases where plain JSON is too limited, `pickle` is too Python-specific, and you still want good ergonomics from Python.

## Why TOONS

- **Native core**: CPU-heavy serialization and parsing lives in Zig.
- **Python-first API**: the package feels natural to use from Python.
- **Richer than JSON**: supports many standard-library and Python-specific types.
- **LLM-aware text mode**: can emit compact TOON text that is easier for models to continue correctly.
- **Practical tooling**: includes stream framing, payload inspection, checksums, compression, and HMAC sealing.

## Installation

TOONS is designed to work well with `uv`.

```bash
uv sync --extra dev
```

The native Zig library is bundled in builds, and when needed the package can rebuild it automatically on first use.

## Quick Start

### Binary round-trip

```python
from datetime import date
from decimal import Decimal

import toons

payload = {
    "name": "TOONS",
    "flags": [True, False, None],
    "blob": b"\x00zig\x01",
    "coords": (1, 2, 3),
    "born": date(2026, 3, 24),
    "price": Decimal("19.9900"),
}

encoded = toons.dumps(payload)
decoded = toons.loads(encoded)

assert decoded == payload
```

### TOON text round-trip

```python
import toons

payload = {
    "items": [
        {"sku": "A1", "qty": 2, "price": 9.99},
        {"sku": "B2", "qty": 1, "price": 14.5},
    ]
}

text = toons.encode_text(payload)
print(text)

# items[2]{sku,qty,price}:
#   A1,2,9.99
#   B2,1,14.5

assert toons.decode_text(text) == payload
```

### LLM-friendly text mode

```python
import toons

payload = {
    "data": {
        "metadata": {
            "items": ["alpha,beta", "gamma,delta"]
        }
    }
}

text = toons.encode_llm_text(payload)
print(text)

# Uses safe key folding and can choose a delimiter that reduces quoting.

restored = toons.decode_text(text, expand_paths="safe")
assert restored == payload
```

## Supported Data Types

TOONS round-trips:

- `None`
- `bool`
- `int`
- `float`
- `complex`
- `str`
- `bytes`
- `list`
- `tuple`
- `set`
- `frozenset`
- `dict[str, ...]`
- `datetime.date`
- `datetime.time`
- `datetime.datetime`
- `datetime.timedelta`
- `decimal.Decimal`
- `uuid.UUID`
- `pathlib.Path`
- `fractions.Fraction`
- `enum.Enum`
- dataclass instances
- namedtuple instances
- arbitrary-size integers
- custom user-defined types through codec registration

## Feature Tour

### Stable binary encoding

```python
import toons

first = {"b": 2, "a": 1}
second = {"a": 1, "b": 2}

assert toons.canonical_dumps(first) == toons.canonical_dumps(second)
```

### Custom codecs

```python
from dataclasses import dataclass

import toons


@dataclass(frozen=True)
class Token:
    value: str


registry = toons.CodecRegistry()
registry.register(
    Token,
    "example.token",
    lambda token: {"value": token.value},
    lambda payload: Token(payload["value"]),
)

encoded = toons.dumps({"token": Token("abc")}, registry=registry)
decoded = toons.loads(encoded, registry=registry)

assert decoded["token"] == Token("abc")
```

### Typed loading

```python
from dataclasses import dataclass
from datetime import date

import toons


@dataclass(frozen=True)
class Artist:
    name: str
    born: date
    active: bool


encoded = toons.dumps({"name": "Amiya", "born": date(1990, 1, 1), "active": True})
artist = toons.loads_as(encoded, Artist)
```

### Inspect payloads and enforce limits

```python
import toons

payload = toons.dumps({"items": [1, {"deep": ["x"]}]})

print(toons.inspect_text(payload))

safe = toons.loads(
    payload,
    limits=toons.DecodeLimits(max_depth=8, max_total_nodes=10_000),
)
```

### Stream framed records

```python
import io
import toons

buffer = io.BytesIO()
toons.stream_dump([{"n": 1}, {"n": 2}, {"n": 3}], buffer)

buffer.seek(0)
items = list(toons.stream_load(buffer))
assert items == [{"n": 1}, {"n": 2}, {"n": 3}]
```

### Pack, compress, checksum, and seal

```python
import toons

sealed = toons.seal(
    {"secret": "value"},
    "shared-key",
    compression="gzip",
)

decoded = toons.unseal(sealed, "shared-key")
assert decoded == {"secret": "value"}
```

## Public API

Core binary API:

- `toons.dumps(obj) -> bytes`
- `toons.loads(data) -> object`
- `toons.dump(obj, fp)`
- `toons.load(fp)`

Text API:

- `toons.encode_text(obj) -> str`
- `toons.encode_llm_text(obj) -> str`
- `toons.decode_text(text) -> object`

Extra helpers:

- `toons.canonical_dumps(...)`
- `toons.loads_as(...)`
- `toons.inspect_text(...)`
- `toons.inspect_tree(...)`
- `toons.stream_dump(...)`
- `toons.stream_load(...)`
- `toons.pack(...)`
- `toons.unpack(...)`
- `toons.seal(...)`
- `toons.unseal(...)`

## Format Notes

TOONS has two user-facing representations in this package:

### 1. Native binary TOONS

The binary format uses:

- a `TOON` header and version byte
- one-byte token tags
- fixed-width numeric payloads
- length-prefixed strings, bytes, lists, tuples, and dicts
- extension tags for richer Python values

This is the fast transport/storage format.

### 2. TOON text

The package also supports TOON text features such as:

- root objects, arrays, and primitives
- inline primitive arrays
- tabular arrays
- mixed arrays with list markers
- quoting and escaping rules
- safe key folding and safe path expansion

This is the readable / prompt-friendly format.

## Development

Run the local checks with:

```bash
uv run pytest
uv run python benchmarks/benchmark_roundtrip.py
uv build
```

## Project Layout

- [`src/toons`](/Volumes/external_storage/toonz/src/toons): Python package
- [`zig/toons.zig`](/Volumes/external_storage/toonz/zig/toons.zig): binary serializer/deserializer
- [`zig/text_format.zig`](/Volumes/external_storage/toonz/zig/text_format.zig): native TOON text renderer/parser
- [`tests/test_toons.py`](/Volumes/external_storage/toonz/tests/test_toons.py): test coverage
- [`benchmarks/benchmark_roundtrip.py`](/Volumes/external_storage/toonz/benchmarks/benchmark_roundtrip.py): local micro-benchmark

## License

Add your preferred license file and project metadata before publishing widely.
