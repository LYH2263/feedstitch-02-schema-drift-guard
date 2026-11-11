"""Schema drift guard — per-source field-level contracts.

Each source owns a contract describing the fields its fixture records must
expose: whether the field is required and its expected scalar type. Every
parsed record is validated against the contract before it is allowed into the
staging/merge pipeline:

* a required field absent (or empty)            -> ``missing_required``
* a present value not matching the expected type -> ``type_mismatch``
* a raw field never declared in the contract     -> ``undeclared_field``

Any violation turns the record into a *structural drift* row that never
reaches canonical storage. Parsing is format-level; this module owns the
business-level field contract.
"""

from __future__ import annotations

from typing import Any

from app.engines.parse_feed import CORE_FIELDS, coerce_value

SUPPORTED_TYPES = ("string", "number", "integer", "boolean")

DRIFT_MISSING_REQUIRED = "missing_required"
DRIFT_TYPE_MISMATCH = "type_mismatch"
DRIFT_UNDECLARED_FIELD = "undeclared_field"
DRIFT_KINDS = (DRIFT_MISSING_REQUIRED, DRIFT_TYPE_MISMATCH, DRIFT_UNDECLARED_FIELD)

# Core fields every feed record is expected to carry, with the canonical type.
DEFAULT_CONTRACT_FIELDS: dict[str, dict[str, Any]] = {
    "business_key": {"required": True, "type": "string"},
    "title": {"required": True, "type": "string"},
    "category": {"required": True, "type": "string"},
    "event_ts": {"required": True, "type": "string"},
    "price": {"required": True, "type": "number"},
    "source_revision": {"required": True, "type": "integer"},
}

# These six back the NOT NULL / typed staging and canonical columns, so a
# contract may neither drop them nor relax their required flag or type.
# Additional custom fields remain fully editable.
CORE_FIELD_TYPES: dict[str, str] = {
    name: spec["type"] for name, spec in DEFAULT_CONTRACT_FIELDS.items()
}


def default_contract() -> dict[str, Any]:
    """Contract matching the normalized core staging fields (all required)."""
    return {
        "version": 1,
        "allow_undeclared": False,
        "fields": {
            name: dict(spec) for name, spec in DEFAULT_CONTRACT_FIELDS.items()
        },
    }


def validate_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a contract payload coming from the API/UI."""
    if not isinstance(contract, dict):
        raise ValueError("contract must be an object")
    fields = contract.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("contract.fields must be a non-empty object")

    normalized_fields: dict[str, dict[str, Any]] = {}
    for name, spec in fields.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("field name must be a non-empty string")
        if not isinstance(spec, dict):
            raise ValueError(f"field {name}: spec must be an object")
        expected_type = spec.get("type")
        if expected_type not in SUPPORTED_TYPES:
            raise ValueError(
                f"field {name}: type must be one of {', '.join(SUPPORTED_TYPES)}"
            )
        field_name = name.strip()
        normalized_fields[field_name] = {
            "required": bool(spec.get("required", False)),
            "type": expected_type,
        }

    # Core fields back the typed, NOT NULL staging/canonical columns, so they
    # cannot be removed, made optional, or retyped. Custom fields are free.
    missing_core = [name for name in CORE_FIELD_TYPES if name not in normalized_fields]
    if missing_core:
        raise ValueError(
            f"core fields cannot be removed from a contract: {', '.join(missing_core)}"
        )
    for name, expected_type in CORE_FIELD_TYPES.items():
        spec = normalized_fields[name]
        if not spec["required"]:
            raise ValueError(f"core field {name} must stay required")
        if spec["type"] != expected_type:
            raise ValueError(
                f"core field {name} must keep type {expected_type}, got {spec['type']}"
            )

    if not any(spec["required"] for spec in normalized_fields.values()):
        raise ValueError("contract must declare at least one required field")

    allow_undeclared = bool(contract.get("allow_undeclared", False))
    return {
        "version": 1,
        "allow_undeclared": allow_undeclared,
        "fields": normalized_fields,
    }


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def check_record(
    record: dict[str, Any],
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return drift findings for one parsed record.

    An empty list means the record honors the contract. The parsed record
    keeps both normalized core fields and an untouched ``raw`` mapping.
    """
    raw = record.get("raw")
    if not isinstance(raw, dict):
        raw = {key: record.get(key) for key in CORE_FIELDS}

    findings: list[dict[str, Any]] = []
    fields = contract.get("fields", {})

    for name, spec in fields.items():
        present = name in raw
        value = raw.get(name)
        if not present or _is_empty(value):
            if spec.get("required"):
                findings.append(
                    {
                        "kind": DRIFT_MISSING_REQUIRED,
                        "field": name,
                        "expected_type": spec["type"],
                        "actual_type": None,
                        "expected": f"required {spec['type']}",
                        "actual": None,
                        "detail": "required field is missing or empty",
                    }
                )
            continue

        ok, _coerced = coerce_value(value, spec["type"])
        if not ok:
            findings.append(
                {
                    "kind": DRIFT_TYPE_MISMATCH,
                    "field": name,
                    "expected_type": spec["type"],
                    "actual_type": type(value).__name__,
                    "expected": spec["type"],
                    "actual": value,
                    "detail": f"value cannot be interpreted as {spec['type']}",
                }
            )

    if not contract.get("allow_undeclared", False):
        declared = set(fields)
        for name in sorted(set(raw) - declared):
            findings.append(
                {
                    "kind": DRIFT_UNDECLARED_FIELD,
                    "field": name,
                    "expected_type": None,
                    "actual_type": type(raw[name]).__name__,
                    "expected": "field declared in contract",
                    "actual": raw[name],
                    "detail": "field is not declared in the source contract",
                }
            )

    return findings
