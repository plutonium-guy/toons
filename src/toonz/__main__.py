from __future__ import annotations

import argparse
import ast
import base64
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import UUID

from .api import decode_text, encode_llm_text, encode_text, inspect_text, pack, unpack
from .api import dumps, loads
from .schema import infer_schema


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": list(value)}
    if isinstance(value, complex):
        return {"__complex__": [value.real, value.imag]}
    if isinstance(value, Fraction):
        return {"__fraction__": [value.numerator, value.denominator]}
    if isinstance(value, (date, datetime, time, Decimal, UUID, Path)):
        return str(value)
    if isinstance(value, timedelta):
        return {"__timedelta__": [value.days, value.seconds, value.microseconds]}
    if is_dataclass(value):
        return {"__dataclass__": asdict(value)}
    if isinstance(value, tuple):
        return {"__tuple__": [_to_jsonable(item) for item in value]}
    if isinstance(value, set):
        return {"__set__": [_to_jsonable(item) for item in sorted(value, key=repr)]}
    if isinstance(value, frozenset):
        return {"__frozenset__": [_to_jsonable(item) for item in sorted(value, key=repr)]}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Encode and decode TOONZ payloads.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    encode_parser = subparsers.add_parser("encode", help="Encode a Python literal to TOONZ bytes.")
    encode_parser.add_argument("literal", help="Python literal, parsed with ast.literal_eval")
    encode_parser.add_argument("--canonical", action="store_true", help="Sort mappings for stable output")

    decode_parser = subparsers.add_parser("decode", help="Decode TOONZ bytes from hex.")
    decode_parser.add_argument("hex_payload", help="Hex string generated from a TOONZ payload")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a TOONZ payload from hex.")
    inspect_parser.add_argument("hex_payload", help="Hex string generated from a TOONZ payload")

    encode_text_parser = subparsers.add_parser("encode-text", help="Encode a Python literal to TOON text.")
    encode_text_parser.add_argument("literal", help="Python literal, parsed with ast.literal_eval")
    encode_text_parser.add_argument("--canonical", action="store_true", help="Sort mappings for stable output")
    encode_text_parser.add_argument("--delimiter", choices=[",", "|", "\\t", "auto"], default=",")
    encode_text_parser.add_argument("--key-folding", choices=["off", "safe"], default="off")
    encode_text_parser.add_argument("--llm", action="store_true", help="Optimize text output for LLM generation")

    llm_text_parser = subparsers.add_parser("encode-llm-text", help="Encode a Python literal to LLM-friendly TOON text.")
    llm_text_parser.add_argument("literal", help="Python literal, parsed with ast.literal_eval")

    decode_text_parser = subparsers.add_parser("decode-text", help="Decode TOON text to JSON output.")
    decode_text_parser.add_argument("text_payload", help="TOON text payload")
    decode_text_parser.add_argument("--expand-paths", choices=["off", "safe"], default="off")

    schema_parser = subparsers.add_parser("schema", help="Infer a lightweight schema from a Python literal.")
    schema_parser.add_argument("literal", help="Python literal, parsed with ast.literal_eval")

    pack_parser = subparsers.add_parser("pack", help="Pack a TOONZ payload with optional compression/HMAC.")
    pack_parser.add_argument("literal", help="Python literal, parsed with ast.literal_eval")
    pack_parser.add_argument("--compression", choices=["gzip", "lzma", "zlib"])
    pack_parser.add_argument("--secret", help="Shared secret for HMAC sealing")

    unpack_parser = subparsers.add_parser("unpack", help="Unpack a packed TOONZ payload from base64.")
    unpack_parser.add_argument("base64_payload", help="Packed TOONZ envelope in base64")
    unpack_parser.add_argument("--secret", help="Shared secret for HMAC sealed payloads")

    args = parser.parse_args()

    if args.command == "encode":
        value = ast.literal_eval(args.literal)
        print(dumps(value, deterministic=args.canonical).hex())
        return

    if args.command == "decode":
        payload = bytes.fromhex(args.hex_payload)
        print(json.dumps(_to_jsonable(loads(payload)), indent=2, sort_keys=True))
        return

    if args.command == "inspect":
        payload = bytes.fromhex(args.hex_payload)
        print(inspect_text(payload))
        return

    if args.command == "encode-text":
        value = ast.literal_eval(args.literal)
        delimiter = "\t" if args.delimiter == "\\t" else args.delimiter
        print(
            encode_text(
                value,
                delimiter=delimiter,
                deterministic=args.canonical,
                key_folding=args.key_folding,
                optimize_for_llm=args.llm,
            )
        )
        return

    if args.command == "encode-llm-text":
        value = ast.literal_eval(args.literal)
        print(encode_llm_text(value))
        return

    if args.command == "decode-text":
        print(
            json.dumps(
                _to_jsonable(decode_text(args.text_payload, expand_paths=args.expand_paths)),
                indent=2,
                sort_keys=True,
            )
        )
        return

    if args.command == "schema":
        value = ast.literal_eval(args.literal)
        print(json.dumps(infer_schema(value), indent=2, sort_keys=True))
        return

    if args.command == "pack":
        value = ast.literal_eval(args.literal)
        packed = pack(value, compression=args.compression, secret=args.secret)
        print(base64.b64encode(packed).decode("ascii"))
        return

    payload = base64.b64decode(args.base64_payload)
    print(json.dumps(_to_jsonable(unpack(payload, secret=args.secret)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
