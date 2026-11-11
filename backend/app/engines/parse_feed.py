"""Parse local fixture feeds into normalized staging records.

Parsing is deliberately lenient: structural contract enforcement (required
fields, expected types, undeclared fields) belongs to the per-source
``schema_drift_guard``. A record missing a core field is therefore kept with
``None`` placeholders plus its untouched ``raw`` payload, so the guard can
record a structured drift instead of failing the whole feed run.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


CORE_FIELDS = ("business_key", "title", "price", "category", "event_ts", "source_revision")

_STRING_FIELDS = ("business_key", "title", "category", "event_ts")


def coerce_value(value: Any, expected_type: str) -> tuple[bool, Any]:
    """Best-effort scalar coercion.

    Returns ``(ok, coerced_value)``. Strings that parse into the expected type
    are accepted, because text formats (xml/jsonl) carry numbers as strings.
    Booleans never count as numbers, mirroring JSON semantics.
    """
    if value is None:
        return False, None
    if expected_type == "string":
        if isinstance(value, bool):
            return True, str(value).lower()
        if isinstance(value, (str, int, float)):
            return True, str(value).strip()
        return False, value
    if expected_type == "number":
        if isinstance(value, bool):
            return False, value
        if isinstance(value, (int, float)):
            return True, float(value)
        if isinstance(value, str):
            try:
                return True, float(value.strip())
            except ValueError:
                return False, value
        return False, value
    if expected_type == "integer":
        if isinstance(value, bool):
            return False, value
        if isinstance(value, int):
            return True, int(value)
        if isinstance(value, float):
            return (True, int(value)) if value.is_integer() else (False, value)
        if isinstance(value, str):
            text = value.strip()
            try:
                return True, int(text)
            except ValueError:
                return False, text
        return False, value
    if expected_type == "boolean":
        if isinstance(value, bool):
            return True, value
        if isinstance(value, int) and value in (0, 1):
            return True, bool(value)
        if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0"):
            return True, value.strip().lower() in ("true", "1")
        return False, value
    return False, value


def _normalize_item(raw: dict[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {key: raw.get(key) for key in CORE_FIELDS}
    item["raw"] = dict(raw)

    for key in _STRING_FIELDS:
        value = item[key]
        if value is not None and not isinstance(value, (dict, list)):
            item[key] = str(value).strip()

    if item["price"] is not None:
        ok, coerced = coerce_value(item["price"], "number")
        if ok:
            item["price"] = coerced
    if item["source_revision"] is not None:
        ok, coerced = coerce_value(item["source_revision"], "integer")
        if ok:
            item["source_revision"] = coerced
    return item


def parse_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "items" in data:
        items = data["items"]
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError("JSON feed must be a list or an object with items[]")
    return [_normalize_item(x) for x in items]


def parse_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"jsonl line {line_no}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"jsonl line {line_no}: row must be an object")
        rows.append(_normalize_item(parsed))
    return rows


def parse_xml(path: Path) -> list[dict[str, Any]]:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for node in root.findall(".//item"):
        raw = {child.tag: child.text for child in list(node)}
        rows.append(_normalize_item(raw))
    return rows


PARSERS = {
    "json": parse_json,
    "jsonl": parse_jsonl,
    "xml": parse_xml,
}


def parse_fixture(path: Path, fmt: str) -> list[dict[str, Any]]:
    parser = PARSERS.get(fmt)
    if parser is None:
        raise ValueError(f"unsupported format: {fmt}")
    if not path.exists():
        raise FileNotFoundError(str(path))
    return parser(path)
