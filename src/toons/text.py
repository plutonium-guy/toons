from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Literal

from . import _ffi
from ._normalize import MAGIC_KEY, denormalize_value, normalize_value
from .codecs import CodecRegistry
from .schema import cast

KeyFoldingMode = Literal["off", "safe"]
ExpandPathsMode = Literal["off", "safe"]
TextDelimiter = Literal[",", "|", "\t", "auto"]

_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NUMBER_LIKE_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$")
_INT_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_FLOAT_RE = re.compile(r"^-?(?:0|[1-9][0-9]*|\.[0-9]+|[0-9]+\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")

_ESCAPE_ENCODE = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}
_ESCAPE_DECODE = {
    "\\": "\\",
    '"': '"',
    "n": "\n",
    "r": "\r",
    "t": "\t",
}

_TEXT_SENTINEL_KEY = "$toons"
_TEXT_SENTINEL_KIND = "kind"
_TEXT_SENTINEL_PAYLOAD = "payload"
_TEXT_SENTINEL_NAME = "name"
_TEXT_SENTINEL_ESCAPED_DICT = "escaped-dict"


class ToonTextError(ValueError):
    """Raised when TOON text cannot be parsed or encoded."""


@dataclass(frozen=True)
class _ArrayHeader:
    key: str | None
    length: int
    delimiter: str
    fields: list[str] | None


def encode_text(
    obj: Any,
    *,
    delimiter: TextDelimiter = ",",
    deterministic: bool = False,
    allow_nan: bool = True,
    registry: CodecRegistry | None = None,
    key_folding: KeyFoldingMode = "off",
    flatten_depth: int | None = None,
    optimize_for_llm: bool = False,
) -> str:
    normalized = normalize_value(
        obj,
        registry=registry,
        deterministic=deterministic,
        allow_nan=allow_nan,
    )
    normalized = _textify_envelopes(normalized)
    resolved_key_folding = "safe" if optimize_for_llm and key_folding == "off" else key_folding
    if resolved_key_folding == "safe":
        normalized = _fold_paths(normalized, flatten_depth)
    elif resolved_key_folding != "off":
        raise ToonTextError(f"Unsupported key folding mode: {key_folding!r}")
    resolved_delimiter = _resolve_delimiter(normalized, delimiter=delimiter, optimize_for_llm=optimize_for_llm)
    return _ffi.render_text(normalized, delimiter=resolved_delimiter)


def encode_llm_text(
    obj: Any,
    *,
    deterministic: bool = True,
    allow_nan: bool = True,
    registry: CodecRegistry | None = None,
    flatten_depth: int | None = None,
) -> str:
    return encode_text(
        obj,
        delimiter="auto",
        deterministic=deterministic,
        allow_nan=allow_nan,
        registry=registry,
        key_folding="safe",
        flatten_depth=flatten_depth,
        optimize_for_llm=True,
    )


def decode_text(
    text: str,
    *,
    registry: CodecRegistry | None = None,
    schema: Any = None,
    expand_paths: ExpandPathsMode = "off",
) -> Any:
    value = _ffi.parse_text(text)
    if expand_paths == "safe":
        value = _expand_paths(value)
    elif expand_paths != "off":
        raise ToonTextError(f"Unsupported path expansion mode: {expand_paths!r}")
    value = _restore_envelopes(value)
    restored = denormalize_value(value, registry=registry)
    return cast(restored, schema) if schema is not None else restored


def _validate_delimiter(delimiter: TextDelimiter) -> None:
    if delimiter not in {",", "|", "\t", "auto"}:
        raise ToonTextError("Delimiter must be one of ',', '|', '\\t', or 'auto'")


def _resolve_delimiter(value: Any, *, delimiter: TextDelimiter, optimize_for_llm: bool) -> str:
    _validate_delimiter(delimiter)
    if delimiter != "auto" and not optimize_for_llm:
        return delimiter

    requested = ("\t", "|", ",") if delimiter == "auto" or optimize_for_llm else (delimiter,)
    best = min(
        requested,
        key=lambda candidate: (_delimiter_penalty(value, candidate), _delimiter_preference(candidate)),
    )
    return best


def _render_root(value: Any, *, delimiter: str) -> str:
    if isinstance(value, dict):
        return "\n".join(_render_object_lines(value, indent=0, delimiter=delimiter))
    if isinstance(value, list):
        return "\n".join(_render_array_lines(None, value, indent=0, delimiter=delimiter))
    return _render_primitive(value, delimiter=delimiter)


def _render_object_lines(value: dict[str, Any], *, indent: int, delimiter: str) -> list[str]:
    lines: list[str] = []
    for key, item in value.items():
        lines.extend(_render_field_lines(key, item, indent=indent, delimiter=delimiter))
    return lines


def _render_field_lines(key: str, value: Any, *, indent: int, delimiter: str) -> list[str]:
    prefix = " " * indent
    rendered_key = _render_key(key, delimiter=delimiter)
    if _is_primitive(value):
        return [f"{prefix}{rendered_key}: {_render_primitive(value, delimiter=delimiter)}"]
    if isinstance(value, dict):
        if not value:
            return [f"{prefix}{rendered_key}:"]
        return [f"{prefix}{rendered_key}:"] + _render_object_lines(value, indent=indent + 2, delimiter=delimiter)
    if isinstance(value, list):
        return _render_array_lines(key, value, indent=indent, delimiter=delimiter)
    raise ToonTextError(f"Unsupported TOON text value: {type(value).__name__}")


def _render_array_lines(key: str | None, value: list[Any], *, indent: int, delimiter: str) -> list[str]:
    prefix = " " * indent
    header_prefix = "" if key is None else _render_key(key, delimiter=delimiter)
    length_marker = _format_length_marker(len(value), delimiter)
    tabular_fields = _tabular_fields(value)
    if _is_primitive_array(value):
        rendered = delimiter.join(_render_primitive(item, delimiter=delimiter) for item in value)
        if rendered:
            return [f"{prefix}{header_prefix}{length_marker}: {rendered}"]
        return [f"{prefix}{header_prefix}{length_marker}:"]
    if tabular_fields is not None:
        header = _format_tabular_header(header_prefix, len(value), delimiter, tabular_fields)
        lines = [f"{prefix}{header}:"]
        row_prefix = " " * (indent + 2)
        for item in value:
            row = delimiter.join(
                _render_primitive(item.get(field), delimiter=delimiter)
                for field in tabular_fields
            )
            lines.append(f"{row_prefix}{row}")
        return lines
    lines = [f"{prefix}{header_prefix}{length_marker}:"]
    for item in value:
        lines.extend(_render_array_item_lines(item, indent=indent + 2, delimiter=delimiter))
    return lines


def _render_array_item_lines(value: Any, *, indent: int, delimiter: str) -> list[str]:
    prefix = " " * indent
    if _is_primitive(value):
        return [f"{prefix}- {_render_primitive(value, delimiter=delimiter)}"]
    if isinstance(value, list):
        rendered = _render_array_lines(None, value, indent=0, delimiter=delimiter)
        first = f"{prefix}- {rendered[0]}"
        rest = [(" " * (indent + 2)) + line for line in rendered[1:]]
        return [first] + rest
    if isinstance(value, dict):
        if not value:
            return [f"{prefix}-"]
        items = list(value.items())
        first_key, first_value = items[0]
        first_line, continuation = _render_inline_field(first_key, first_value, indent=indent, delimiter=delimiter)
        lines = [f"{prefix}- {first_line}"]
        lines.extend(continuation)
        for key, item in items[1:]:
            lines.extend(_render_field_lines(key, item, indent=indent + 2, delimiter=delimiter))
        return lines
    raise ToonTextError(f"Unsupported array item type: {type(value).__name__}")


def _render_inline_field(key: str, value: Any, *, indent: int, delimiter: str) -> tuple[str, list[str]]:
    rendered_key = _render_key(key, delimiter=delimiter)
    if _is_primitive(value):
        return f"{rendered_key}: {_render_primitive(value, delimiter=delimiter)}", []
    if isinstance(value, dict):
        if not value:
            return f"{rendered_key}:", []
        return (
            f"{rendered_key}:",
            _render_object_lines(value, indent=indent + 2, delimiter=delimiter),
        )
    if isinstance(value, list):
        lines = _render_array_lines(key, value, indent=0, delimiter=delimiter)
        first = lines[0]
        rest = [(" " * (indent + 4)) + line for line in lines[1:]]
        return first, rest
    raise ToonTextError(f"Unsupported inline field type: {type(value).__name__}")


def _is_primitive(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _is_primitive_array(value: list[Any]) -> bool:
    return all(_is_primitive(item) for item in value)


def _tabular_fields(value: list[Any]) -> list[str] | None:
    if not value or not all(isinstance(item, dict) for item in value):
        return None
    first = value[0]
    if not first:
        return None
    fields = list(first.keys())
    if any(not _is_primitive(first[field]) for field in fields):
        return None
    field_set = set(fields)
    for item in value[1:]:
        if set(item.keys()) != field_set:
            return None
        if any(not _is_primitive(item[field]) for field in fields):
            return None
    return fields


def _format_length_marker(length: int, delimiter: str) -> str:
    if delimiter == ",":
        return f"[{length}]"
    return f"[{length}{delimiter}]"


def _format_tabular_header(key: str, length: int, delimiter: str, fields: list[str]) -> str:
    fields_text = delimiter.join(_render_key(field, delimiter=delimiter) for field in fields)
    return f"{key}{_format_length_marker(length, delimiter)}{{{fields_text}}}"


def _render_key(key: str, *, delimiter: str) -> str:
    return _render_string_token(key, delimiter=delimiter, for_key=True)


def _render_primitive(value: Any, *, delimiter: str) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _canonical_float(value)
    if isinstance(value, str):
        return _render_string_token(value, delimiter=delimiter, for_key=False)
    raise ToonTextError(f"Unsupported primitive value: {type(value).__name__}")


@lru_cache(maxsize=1024)
def _canonical_float(value: float) -> str:
    if value == 0:
        return "0"
    try:
        decimal = Decimal(str(value))
    except InvalidOperation as exc:
        raise ToonTextError(f"Cannot render float {value!r}") from exc
    text = format(decimal, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"-0", ""}:
        return "0"
    return text


def _quote_string(value: str) -> str:
    for char in value:
        if ord(char) < 0x20 and char not in {"\n", "\r", "\t"}:
            raise ToonTextError("TOON text cannot encode control characters outside \\n, \\r, and \\t")
    escaped = "".join(_ESCAPE_ENCODE.get(char, char) for char in value)
    return f'"{escaped}"'


def _render_string_token(value: str, *, delimiter: str, for_key: bool) -> str:
    return _render_string_token_cached(value, delimiter, for_key)


@lru_cache(maxsize=8192)
def _render_string_token_cached(value: str, delimiter: str, for_key: bool) -> str:
    if _can_leave_unquoted(value, delimiter=delimiter, for_key=for_key):
        return value
    return _quote_string(value)


def _can_leave_unquoted(value: str, *, delimiter: str, for_key: bool) -> bool:
    return _can_leave_unquoted_cached(value, delimiter, for_key)


@lru_cache(maxsize=8192)
def _can_leave_unquoted_cached(value: str, delimiter: str, for_key: bool) -> bool:
    if value == "" or value != value.strip():
        return False
    if value in {"true", "false", "null"}:
        return False
    if value == "-" or value.startswith("-"):
        return False
    if _NUMBER_LIKE_RE.fullmatch(value):
        return False
    forbidden = {":", '"', "\\", "[", "]", "{", "}"}
    if not for_key:
        forbidden.add(delimiter)
    return all(char not in forbidden and char not in {"\n", "\r", "\t"} for char in value)


def _delimiter_penalty(value: Any, delimiter: str) -> tuple[int, int]:
    stats = _DelimiterStats()
    _collect_delimiter_stats(value, delimiter, stats)
    return (stats.quoted_tokens, stats.delimiter_conflicts)


def _delimiter_preference(delimiter: str) -> int:
    return {"\t": 0, "|": 1, ",": 2}[delimiter]


@dataclass
class _DelimiterStats:
    quoted_tokens: int = 0
    delimiter_conflicts: int = 0


def _collect_delimiter_stats(value: Any, delimiter: str, stats: _DelimiterStats) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not _can_leave_unquoted(key, delimiter=delimiter, for_key=True):
                stats.quoted_tokens += 1
            _collect_delimiter_stats(item, delimiter, stats)
        return
    if isinstance(value, list):
        for item in value:
            _collect_delimiter_stats(item, delimiter, stats)
        return
    if isinstance(value, str):
        if not _can_leave_unquoted(value, delimiter=delimiter, for_key=False):
            stats.quoted_tokens += 1
        if delimiter in value:
            stats.delimiter_conflicts += 1


def _fold_paths(value: Any, flatten_depth: int | None) -> Any:
    depth_limit = flatten_depth if flatten_depth is not None else 2**31
    if isinstance(value, list):
        return [_fold_paths(item, flatten_depth) for item in value]
    if not isinstance(value, dict):
        return value

    result: dict[str, Any] = {}
    original_keys = set(value)
    for key, item in value.items():
        candidate = _fold_single_chain(key, item, depth_limit, flatten_depth)
        if candidate is None:
            result[key] = _fold_paths(item, flatten_depth)
            continue
        folded_key, folded_value = candidate
        if folded_key != key and folded_key not in original_keys:
            result[folded_key] = folded_value
        else:
            result[key] = _fold_paths(item, flatten_depth)
    return result


def _fold_single_chain(
    key: str,
    value: Any,
    depth_limit: int,
    flatten_depth: int | None,
) -> tuple[str, Any] | None:
    if depth_limit < 2 or not _SAFE_SEGMENT_RE.fullmatch(key):
        return None
    segments = [key]
    current = value
    remaining = depth_limit - 1
    while remaining > 0 and isinstance(current, dict) and len(current) == 1:
        next_key, next_value = next(iter(current.items()))
        if not _SAFE_SEGMENT_RE.fullmatch(next_key):
            return None
        segments.append(next_key)
        current = next_value
        remaining -= 1
    if len(segments) < 2:
        return None
    if isinstance(current, dict) and current:
        return None
    return ".".join(segments), _fold_paths(current, flatten_depth)


def _expand_paths(value: Any) -> Any:
    if isinstance(value, list):
        return [_expand_paths(item) for item in value]
    if not isinstance(value, dict):
        return value

    expanded: dict[str, Any] = {}
    for key, item in value.items():
        item = _expand_paths(item)
        parts = key.split(".")
        if len(parts) < 2 or not all(_SAFE_SEGMENT_RE.fullmatch(part) for part in parts):
            expanded[key] = item
            continue

        target = expanded
        blocked = False
        for part in parts[:-1]:
            existing = target.get(part)
            if existing is None:
                next_target: dict[str, Any] = {}
                target[part] = next_target
                target = next_target
                continue
            if not isinstance(existing, dict):
                blocked = True
                break
            target = existing
        if blocked or parts[-1] in target:
            expanded[key] = item
        else:
            target[parts[-1]] = item
    return expanded


def _textify_envelopes(value: Any) -> Any:
    if isinstance(value, list):
        return [_textify_envelopes(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {MAGIC_KEY} and isinstance(value[MAGIC_KEY], list):
        envelope = value[MAGIC_KEY]
        if len(envelope) == 3 and envelope[0] == "ext":
            return {
                _TEXT_SENTINEL_KEY: {
                    _TEXT_SENTINEL_KIND: "ext",
                    _TEXT_SENTINEL_NAME: envelope[1],
                    _TEXT_SENTINEL_PAYLOAD: _textify_envelopes(envelope[2]),
                }
            }
        if len(envelope) == 2 and envelope[0] == "dict":
            return {
                _TEXT_SENTINEL_KEY: {
                    _TEXT_SENTINEL_KIND: "dict",
                    _TEXT_SENTINEL_PAYLOAD: _textify_envelopes(envelope[1]),
                }
            }
    transformed = {key: _textify_envelopes(item) for key, item in value.items()}
    if set(transformed) == {_TEXT_SENTINEL_KEY} and isinstance(transformed[_TEXT_SENTINEL_KEY], dict):
        return {
            _TEXT_SENTINEL_KEY: {
                _TEXT_SENTINEL_KIND: _TEXT_SENTINEL_ESCAPED_DICT,
                _TEXT_SENTINEL_PAYLOAD: transformed,
            }
        }
    return transformed


def _restore_envelopes(value: Any) -> Any:
    if isinstance(value, list):
        return [_restore_envelopes(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {_TEXT_SENTINEL_KEY} and isinstance(value[_TEXT_SENTINEL_KEY], dict):
        wrapper = value[_TEXT_SENTINEL_KEY]
        kind = wrapper.get(_TEXT_SENTINEL_KIND)
        if kind == "ext" and _TEXT_SENTINEL_NAME in wrapper:
            return {
                MAGIC_KEY: [
                    "ext",
                    wrapper[_TEXT_SENTINEL_NAME],
                    _restore_envelopes(wrapper.get(_TEXT_SENTINEL_PAYLOAD)),
                ]
            }
        if kind == "dict":
            return {
                MAGIC_KEY: [
                    "dict",
                    _restore_envelopes(wrapper.get(_TEXT_SENTINEL_PAYLOAD, {})),
                ]
            }
        if kind == _TEXT_SENTINEL_ESCAPED_DICT:
            payload = wrapper.get(_TEXT_SENTINEL_PAYLOAD, {})
            if not isinstance(payload, dict):
                raise ToonTextError("Malformed escaped TOON text dictionary envelope")
            return {key: _restore_envelopes(item) for key, item in payload.items()}
    return {key: _restore_envelopes(item) for key, item in value.items()}


class _Parser:
    def __init__(self, text: str) -> None:
        self.lines = [
            (index + 1, line.rstrip("\r"))
            for index, line in enumerate(text.splitlines())
            if line.strip() != ""
        ]
        self.index = 0

    def parse(self) -> Any:
        if not self.lines:
            return {}
        indent, content = self._peek()
        if indent != 0:
            raise self._error("Root indentation must start at column 0")
        header = _parse_array_header(_field_lhs(content)) if _looks_like_field(content) else None
        if header is not None and header.key is None:
            self.index += 1
            return self._parse_array_block(header, indent=0, right_part=_field_rhs(content), continuation_indent=2)
        if _looks_like_field(content):
            return self._parse_object(expected_indent=0)
        if len(self.lines) != 1:
            raise self._error("Multi-line TOON documents must be objects or arrays")
        self.index += 1
        return _parse_primitive_token(content)

    def _parse_object(self, *, expected_indent: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while self.index < len(self.lines):
            indent, content = self._peek()
            if indent < expected_indent:
                break
            if indent != expected_indent:
                raise self._error("Unexpected indentation in object block")
            key, value = self._parse_field_line(content, indent=indent)
            result[key] = value
        return result

    def _parse_field_line(self, content: str, *, indent: int) -> tuple[str, Any]:
        header = _parse_array_header(_field_lhs(content))
        if header is not None and header.key is not None:
            self.index += 1
            value = self._parse_array_block(
                header,
                indent=indent,
                right_part=_field_rhs(content),
                continuation_indent=indent + 2,
            )
            return header.key, value

        key_text, rhs = _split_field(content)
        key = _parse_key_token(key_text)
        self.index += 1
        if rhs != "":
            return key, _parse_primitive_token(rhs)
        if self.index >= len(self.lines):
            return key, {}
        next_indent, next_content = self._peek()
        if next_indent <= indent:
            return key, {}
        if next_indent != indent + 2:
            raise self._error("Nested object indentation must increase by 2 spaces")
        nested_header = _parse_array_header(next_content)
        if nested_header is not None and nested_header.key is None:
            raise self._error("Root-style arrays are not valid directly under object keys")
        return key, self._parse_object(expected_indent=indent + 2)

    def _parse_array_block(
        self,
        header: _ArrayHeader,
        *,
        indent: int,
        right_part: str,
        continuation_indent: int,
    ) -> list[Any]:
        if header.fields is not None:
            if right_part:
                raise self._error("Tabular arrays cannot be declared inline")
            rows: list[Any] = []
            while self.index < len(self.lines):
                next_indent, content = self._peek()
                if next_indent < continuation_indent:
                    break
                if next_indent != continuation_indent:
                    raise self._error("Unexpected indentation in tabular array")
                self.index += 1
                values = _split_delimited(content, header.delimiter)
                if len(values) != len(header.fields):
                    raise self._error("Tabular row width does not match header")
                rows.append(
                    {
                        field: _parse_primitive_token(token)
                        for field, token in zip(header.fields, values, strict=True)
                    }
                )
            if len(rows) != header.length:
                raise self._error("Tabular row count does not match declared length")
            return rows

        if right_part != "":
            values = [] if header.length == 0 and right_part == "" else [
                _parse_primitive_token(token)
                for token in _split_delimited(right_part, header.delimiter)
            ]
            if len(values) != header.length:
                raise self._error("Inline array length does not match declared length")
            return values

        items: list[Any] = []
        while self.index < len(self.lines):
            next_indent, content = self._peek()
            if next_indent < continuation_indent:
                break
            if next_indent != continuation_indent:
                raise self._error("Unexpected indentation in array block")
            if not content.startswith("-"):
                break
            items.append(self._parse_array_item(indent=continuation_indent))
        if len(items) != header.length:
            raise self._error("Array length does not match declared length")
        return items

    def _parse_array_item(self, *, indent: int) -> Any:
        line_indent, content = self._peek()
        if line_indent != indent:
            raise self._error("Unexpected indentation in array item")
        if content == "-":
            self.index += 1
            if self.index < len(self.lines):
                next_indent, _ = self._peek()
                if next_indent > indent:
                    return self._parse_object(expected_indent=indent + 2)
            return {}
        if not content.startswith("- "):
            raise self._error("Array items must start with '- '")
        suffix = content[2:]
        root_header = _parse_array_header(_field_lhs(suffix)) if _looks_like_field(suffix) else None
        if root_header is not None and root_header.key is None:
            self.index += 1
            return self._parse_array_block(
                root_header,
                indent=indent,
                right_part=_field_rhs(suffix),
                continuation_indent=indent + 2,
            )
        if _looks_like_field(suffix):
            return self._parse_object_item(first_field=suffix, indent=indent)
        self.index += 1
        return _parse_primitive_token(suffix)

    def _parse_object_item(self, *, first_field: str, indent: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        key, value = self._parse_inline_field(first_field, indent=indent)
        result[key] = value
        while self.index < len(self.lines):
            next_indent, content = self._peek()
            if next_indent < indent + 2:
                break
            if next_indent != indent + 2:
                raise self._error("Unexpected indentation in object array item")
            key, value = self._parse_field_line(content, indent=indent + 2)
            result[key] = value
        return result

    def _parse_inline_field(self, content: str, *, indent: int) -> tuple[str, Any]:
        header = _parse_array_header(_field_lhs(content))
        if header is not None and header.key is not None:
            self.index += 1
            value = self._parse_array_block(
                header,
                indent=indent,
                right_part=_field_rhs(content),
                continuation_indent=indent + 4,
            )
            return header.key, value

        key_text, rhs = _split_field(content)
        key = _parse_key_token(key_text)
        self.index += 1
        if rhs != "":
            return key, _parse_primitive_token(rhs)
        if self.index < len(self.lines):
            next_indent, _ = self._peek()
            if next_indent >= indent + 2:
                return key, self._parse_object(expected_indent=indent + 2)
        return key, {}

    def _peek(self) -> tuple[int, str]:
        line_no, raw = self.lines[self.index]
        indent = len(raw) - len(raw.lstrip(" "))
        if raw[:indent] != " " * indent:
            raise self._error("Tabs are not allowed for indentation", line_no=line_no)
        return indent, raw[indent:]

    def _error(self, message: str, *, line_no: int | None = None) -> ToonTextError:
        number = line_no if line_no is not None else self.lines[self.index][0]
        return ToonTextError(f"Line {number}: {message}")


def _looks_like_field(content: str) -> bool:
    try:
        _split_field(content)
    except ToonTextError:
        return False
    return True


def _field_lhs(content: str) -> str:
    return _split_field(content)[0]


def _field_rhs(content: str) -> str:
    return _split_field(content)[1]


def _split_field(content: str) -> tuple[str, str]:
    in_string = False
    escaped = False
    bracket_depth = 0
    brace_depth = 0
    for index, char in enumerate(content):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth -= 1
        elif char == ":" and bracket_depth == 0 and brace_depth == 0:
            rhs = content[index + 1 :]
            if rhs.startswith(" "):
                rhs = rhs[1:]
            return content[:index], rhs
    raise ToonTextError("Expected ':' in field declaration")


def _parse_array_header(text: str) -> _ArrayHeader | None:
    match = re.fullmatch(r"(.*)?\[(\d+)([\|\t])?\](?:\{(.*)\})?", text)
    if match is None:
        return None
    key_text, length_text, delimiter_text, fields_text = match.groups()
    delimiter = delimiter_text or ","
    key = None
    if key_text not in (None, ""):
        key = _parse_key_token(key_text)
    fields = None
    if fields_text is not None:
        fields = [_parse_key_token(token) for token in _split_delimited(fields_text, delimiter)]
    return _ArrayHeader(
        key=key,
        length=int(length_text),
        delimiter=delimiter,
        fields=fields,
    )


def _parse_key_token(token: str) -> str:
    token = token.strip()
    if token.startswith('"'):
        value = _parse_quoted_string(token)
        if not isinstance(value, str):
            raise ToonTextError("Object keys must be strings")
        return value
    if token == "":
        raise ToonTextError("Object keys cannot be empty")
    return token


def _split_delimited(text: str, delimiter: str) -> list[str]:
    if text == "":
        return []
    tokens: list[str] = []
    current: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            current.append(char)
            continue
        if char == delimiter:
            tokens.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if in_string:
        raise ToonTextError("Unterminated string literal")
    tokens.append("".join(current).strip())
    return tokens


def _parse_primitive_token(token: str) -> Any:
    return _parse_primitive_token_cached(token.strip())


@lru_cache(maxsize=8192)
def _parse_primitive_token_cached(token: str) -> Any:
    if token == "null":
        return None
    if token == "true":
        return True
    if token == "false":
        return False
    if token.startswith('"'):
        return _parse_quoted_string(token)
    if _INT_RE.fullmatch(token):
        return int(token)
    if token.startswith("-0") and token not in {"-0", "-0.0"}:
        return token
    if token.startswith("0") and len(token) > 1 and not token.startswith("0."):
        return token
    if _FLOAT_RE.fullmatch(token):
        return float(token)
    return token


def _parse_quoted_string(token: str) -> str:
    return _parse_quoted_string_cached(token)


@lru_cache(maxsize=8192)
def _parse_quoted_string_cached(token: str) -> str:
    if len(token) < 2 or token[0] != '"' or token[-1] != '"':
        raise ToonTextError("Invalid quoted string")
    chars: list[str] = []
    index = 1
    end = len(token) - 1
    while index < end:
        char = token[index]
        if char == "\\":
            index += 1
            if index >= end:
                raise ToonTextError("Dangling escape in string literal")
            escaped = token[index]
            if escaped not in _ESCAPE_DECODE:
                raise ToonTextError(f"Unsupported escape sequence: \\{escaped}")
            chars.append(_ESCAPE_DECODE[escaped])
        else:
            chars.append(char)
        index += 1
    return "".join(chars)
