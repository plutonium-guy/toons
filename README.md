# TOONS

TOONS stands for **Token Oriented Object Notation Serializer**.

This package provides a Python API backed by a Zig shared library loaded through
FFI. It supports round-tripping:

- `None`
- `bool`
- `int` values that fit in signed 64-bit
- `float`
- `complex`
- `str`
- `bytes`
- `list`
- `tuple`
- `set`
- `frozenset`
- `dict` with string keys
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
- custom user-defined types via codec registration

## Quick start

```bash
uv sync --extra dev
uv run pytest
uv run python benchmarks/benchmark_roundtrip.py
```

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

## Higher-level features

- Canonical encoding with `toons.canonical_dumps(...)`
- TOON text encode/decode with `toons.encode_text(...)` and `toons.decode_text(...)`
- LLM-oriented compact text output with `toons.encode_llm_text(...)`
- Typed decode with `toons.loads_as(..., MyDataclass)`
- Custom codecs through `toons.CodecRegistry`
- Framed stream helpers with `toons.stream_dump(...)` and `toons.stream_load(...)`
- Envelope packing with compression, checksums, and HMAC sealing via `toons.pack(...)` and `toons.seal(...)`
- Payload inspection and limit enforcement with `toons.inspect_text(...)` and `toons.DecodeLimits(...)`

### Custom codec example

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

### Typed decode example

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

### Inspect and protect payloads

```python
import toons

payload = toons.dumps({"items": [1, {"deep": ["x"]}]})
print(toons.inspect_text(payload))

sealed = toons.seal({"secret": "value"}, "shared-key", compression="gzip")
decoded = toons.unseal(sealed, "shared-key")
```

### TOON text syntax

```python
import toons

payload = {
    "items": [
        {"sku": "A1", "qty": 2, "price": 9.99},
        {"sku": "B2", "qty": 1, "price": 14.5},
    ]
}

text = toons.encode_text(payload)
assert text == "items[2]{sku,qty,price}:\n  A1,2,9.99\n  B2,1,14.5"
assert toons.decode_text(text) == payload

llm_text = toons.encode_llm_text({"data": {"metadata": {"items": ["alpha,beta", "gamma,delta"]}}})
# Automatically chooses a delimiter that avoids extra quotes and folds safe key paths.
```

## Format overview

TOONS is a compact binary token stream:

- `TOON` header + version byte
- one-byte token tags per value
- little-endian fixed-width numeric payloads
- length-prefixed strings, bytes, lists, tuples, and dicts
- extension tags for temporal and standard-library scalar types

The public API is intentionally small:

- `toons.dumps(obj) -> bytes`
- `toons.loads(data) -> object`
- `toons.dump(obj, fp)`
- `toons.load(fp)`
- `toons.encode_text(obj) -> str`
- `toons.encode_llm_text(obj) -> str`
- `toons.decode_text(text) -> object`

When the native library is not already present, the package will build it with
`zig build-lib` on first use.
