from __future__ import annotations

import dataclasses
import enum
import types
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, is_typeddict


def cast(value: Any, schema: Any) -> Any:
    if schema in (Any, object):
        return value

    if schema is None or schema is type(None):
        if value is not None:
            raise TypeError(f"Expected None, received {type(value).__name__}")
        return None

    origin = get_origin(schema)
    args = get_args(schema)

    if origin in (Union, types.UnionType):
        errors: list[str] = []
        for candidate in args:
            try:
                return cast(value, candidate)
            except TypeError as exc:
                errors.append(str(exc))
        raise TypeError("; ".join(errors))

    if origin is Literal:
        if value not in args:
            raise TypeError(f"Expected one of {args!r}, received {value!r}")
        return value

    if origin is list:
        item_type = args[0] if args else Any
        if not isinstance(value, list):
            raise TypeError(f"Expected list, received {type(value).__name__}")
        return [cast(item, item_type) for item in value]

    if origin is set:
        item_type = args[0] if args else Any
        if not isinstance(value, set):
            raise TypeError(f"Expected set, received {type(value).__name__}")
        return {cast(item, item_type) for item in value}

    if origin is frozenset:
        item_type = args[0] if args else Any
        if not isinstance(value, frozenset):
            raise TypeError(f"Expected frozenset, received {type(value).__name__}")
        return frozenset(cast(item, item_type) for item in value)

    if origin is dict:
        key_type = args[0] if args else Any
        value_type = args[1] if len(args) > 1 else Any
        if not isinstance(value, Mapping):
            raise TypeError(f"Expected mapping, received {type(value).__name__}")
        return {cast(key, key_type): cast(item, value_type) for key, item in value.items()}

    if origin is tuple:
        if not isinstance(value, tuple):
            raise TypeError(f"Expected tuple, received {type(value).__name__}")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(cast(item, args[0]) for item in value)
        if args and len(args) != len(value):
            raise TypeError(f"Expected tuple of length {len(args)}, received {len(value)}")
        return tuple(cast(item, item_type) for item, item_type in zip(value, args, strict=False))

    if dataclasses.is_dataclass(schema):
        if not isinstance(value, Mapping):
            raise TypeError(f"Expected mapping for {schema.__name__}, received {type(value).__name__}")
        kwargs: dict[str, Any] = {}
        for field in dataclasses.fields(schema):
            if field.name in value:
                kwargs[field.name] = cast(value[field.name], field.type)
            elif field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
                raise TypeError(f"Missing required field {field.name!r} for {schema.__name__}")
        return schema(**kwargs)

    if is_typeddict(schema):
        if not isinstance(value, Mapping):
            raise TypeError(f"Expected mapping for {schema.__name__}, received {type(value).__name__}")
        annotations = schema.__annotations__
        return {
            key: cast(value[key], annotation)
            for key, annotation in annotations.items()
            if key in value
        }

    if isinstance(schema, type) and issubclass(schema, enum.Enum):
        if isinstance(value, schema):
            return value
        if isinstance(value, str):
            try:
                return schema[value]
            except KeyError:
                pass
        return schema(value)

    if isinstance(schema, type) and issubclass(schema, tuple) and hasattr(schema, "_fields"):
        if isinstance(value, Mapping):
            annotations = getattr(schema, "__annotations__", {})
            return schema(**{key: cast(value[key], annotations.get(key, Any)) for key in schema._fields})
        if isinstance(value, Sequence):
            return schema(*value)
        raise TypeError(f"Expected mapping or sequence for {schema.__name__}, received {type(value).__name__}")

    if schema is Path and isinstance(value, Path):
        return value

    if isinstance(schema, type):
        if isinstance(value, schema):
            return value
        return schema(value)

    return value


def infer_schema(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "type": f"{type(value).__module__}.{type(value).__qualname__}",
            "fields": {
                field.name: infer_schema(getattr(value, field.name))
                for field in dataclasses.fields(value)
            },
        }
    if isinstance(value, Mapping):
        return {
            "type": "dict",
            "values": {key: infer_schema(item) for key, item in value.items()},
        }
    if isinstance(value, list):
        return {"type": "list", "items": [infer_schema(item) for item in value]}
    if isinstance(value, tuple):
        return {"type": "tuple", "items": [infer_schema(item) for item in value]}
    if isinstance(value, set):
        return {"type": "set", "items": [infer_schema(item) for item in sorted(value, key=repr)]}
    if isinstance(value, frozenset):
        return {"type": "frozenset", "items": [infer_schema(item) for item in sorted(value, key=repr)]}
    return type(value).__name__
