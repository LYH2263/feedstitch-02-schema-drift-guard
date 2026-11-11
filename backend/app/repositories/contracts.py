from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.modules.schema_drift_guard import default_contract, validate_contract


def get_contract(conn: sqlite3.Connection, source_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT source_id, contract_json, updated_at FROM source_contracts WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["contract"] = json.loads(d.pop("contract_json"))
    return d


def get_contract_payload(conn: sqlite3.Connection, source_id: int) -> dict[str, Any]:
    """Return the contract mapping, falling back to the default contract."""
    row = get_contract(conn, source_id)
    if row is None:
        return default_contract()
    return row["contract"]


def list_contracts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT sc.source_id, s.name AS source_name, sc.contract_json, sc.updated_at
        FROM source_contracts sc
        JOIN sources s ON s.id = sc.source_id
        ORDER BY sc.source_id
        """
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["contract"] = json.loads(d.pop("contract_json"))
        out.append(d)
    return out


def upsert_contract(
    conn: sqlite3.Connection, source_id: int, contract: dict[str, Any]
) -> dict[str, Any]:
    normalized = validate_contract(contract)
    conn.execute(
        """
        INSERT INTO source_contracts(source_id, contract_json, updated_at)
        VALUES (?,?,datetime('now'))
        ON CONFLICT(source_id) DO UPDATE SET
            contract_json=excluded.contract_json,
            updated_at=datetime('now')
        """,
        (source_id, json.dumps(normalized, ensure_ascii=False)),
    )
    return normalized


def reset_contract(conn: sqlite3.Connection, source_id: int) -> dict[str, Any]:
    contract = default_contract()
    conn.execute(
        """
        INSERT INTO source_contracts(source_id, contract_json, updated_at)
        VALUES (?,?,datetime('now'))
        ON CONFLICT(source_id) DO UPDATE SET
            contract_json=excluded.contract_json,
            updated_at=datetime('now')
        """,
        (source_id, json.dumps(contract, ensure_ascii=False)),
    )
    return contract


def insert_drift(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    source_id: int,
    business_key: str | None,
    raw_payload: dict[str, Any],
    findings: list[dict[str, Any]],
) -> None:
    kinds = sorted({f["kind"] for f in findings})
    conn.execute(
        """
        INSERT INTO schema_drifts(
            run_id, source_id, business_key, raw_payload_json,
            findings_json, drift_kinds, created_at
        ) VALUES (?,?,?,?,?,?,datetime('now'))
        """,
        (
            run_id,
            source_id,
            business_key,
            json.dumps(raw_payload, ensure_ascii=False),
            json.dumps(findings, ensure_ascii=False),
            ",".join(kinds),
        ),
    )


def list_drifts(
    conn: sqlite3.Connection, run_id: int | None = None
) -> list[dict[str, Any]]:
    if run_id is None:
        rows = conn.execute(
            """
            SELECT id, run_id, source_id, business_key, raw_payload_json,
                   findings_json, drift_kinds, created_at
            FROM schema_drifts ORDER BY id DESC LIMIT 300
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, run_id, source_id, business_key, raw_payload_json,
                   findings_json, drift_kinds, created_at
            FROM schema_drifts WHERE run_id = ? ORDER BY id
            """,
            (run_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["raw_payload"] = json.loads(d.pop("raw_payload_json"))
        d["findings"] = json.loads(d.pop("findings_json"))
        d["drift_kinds"] = d["drift_kinds"].split(",") if d["drift_kinds"] else []
        out.append(d)
    return out


def count_drifts(conn: sqlite3.Connection, run_id: int | None = None) -> int:
    if run_id is None:
        return int(conn.execute("SELECT COUNT(*) c FROM schema_drifts").fetchone()["c"])
    return int(
        conn.execute(
            "SELECT COUNT(*) c FROM schema_drifts WHERE run_id = ?", (run_id,)
        ).fetchone()["c"]
    )
