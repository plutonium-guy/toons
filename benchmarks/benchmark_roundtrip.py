from __future__ import annotations

import json
import pickle
import timeit
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from uuid import UUID

import toonz

PAYLOAD = {
    "name": "TOONS",
    "count": 42,
    "when": datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
    "born": date(2026, 3, 24),
    "delta": timedelta(days=2, seconds=4),
    "price": Decimal("19.9900"),
    "ratio": Fraction(2, 7),
    "path": Path("benchmarks/demo"),
    "uuid": UUID("12345678-1234-5678-1234-567812345678"),
    "items": list(range(50)),
}


def bench(label: str, stmt: str, setup: str) -> None:
    duration = timeit.timeit(stmt, setup=setup, number=2_000)
    print(f"{label:16} {duration:.4f}s")


def main() -> None:
    setup = "from __main__ import PAYLOAD, toonz, json, pickle"
    json_payload = json.dumps({k: str(v) for k, v in PAYLOAD.items()}, sort_keys=True)
    text_payload = toonz.encode_llm_text(PAYLOAD)

    print("Round-trip micro-benchmark")
    bench("toonz.dumps", "toonz.dumps(PAYLOAD, deterministic=True)", setup)
    bench("toonz.loads", "toonz.loads(toonz.dumps(PAYLOAD, deterministic=True))", setup)
    bench("toonz.text", "toonz.encode_text(PAYLOAD, deterministic=True)", setup)
    bench("toonz.llm", "toonz.encode_llm_text(PAYLOAD)", setup)
    bench("toonz.parse", f"toonz.decode_text({text_payload!r})", setup)
    bench("pickle.dumps", "pickle.dumps(PAYLOAD)", setup)
    bench("pickle.loads", "pickle.loads(pickle.dumps(PAYLOAD))", setup)
    bench("json.dumps", "json.dumps({k: str(v) for k, v in PAYLOAD.items()}, sort_keys=True)", setup)
    bench("json.loads", f"json.loads({json_payload!r})", setup)


if __name__ == "__main__":
    main()
