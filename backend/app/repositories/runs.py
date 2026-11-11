from __future__ import annotations

import json
import sqlite3
from typing import Any


def create_run(
    conn: sqlite3.Connection,
    source_id: int,
    mode: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO runs(source_id, mode, status, parsed_count, upserted_count,
                         conflict_count, skipped_count, drift_count, report_json, created_at)
        VALUES (?,?, 'running', 0, 0, 0, 0, 0, '{}', datetime('now'))
        """,
        (source_id, mode),
    )
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    parsed: int,
    upserted: int,
    conflicts: int,
    skipped: int,
    drift: int,
    report: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE runs SET status=?, parsed_count=?, upserted_count=?, conflict_count=?,
            skipped_count=?, drift_count=?, report_json=? WHERE id=?
        """,
        (
            status,
            parsed,
            upserted,
            conflicts,
            skipped,
            drift,
            json.dumps(report, ensure_ascii=False),
            run_id,
        ),
    )


_RUN_SELECT = """
    SELECT r.id, r.source_id, s.name AS source_name, r.mode, r.status,
           r.parsed_count, r.upserted_count, r.conflict_count, r.skipped_count,
           r.drift_count, r.report_json, r.created_at
    FROM runs r JOIN sources s ON s.id = r.source_id
"""


def _hydrate(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["report"] = json.loads(d.pop("report_json") or "{}")
    return d


def list_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(_RUN_SELECT + " ORDER BY r.id DESC").fetchall()
    return [_hydrate(r) for r in rows]


def get_run(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    row = conn.execute(_RUN_SELECT + " WHERE r.id = ?", (run_id,)).fetchone()
    return _hydrate(row) if row else None


def insert_staging(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    source_id: int,
    item: dict[str, Any],
    disposition: str,
) -> None:
    conn.execute(
        """
        INSERT INTO staging_rows(
            run_id, source_id, business_key, event_ts, source_revision,
            payload_json, disposition
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            run_id,
            source_id,
            item["business_key"],
            item["event_ts"],
            item["source_revision"],
            json.dumps(item["payload"], ensure_ascii=False),
            disposition,
        ),
    )


def list_staging(conn: sqlite3.Connection, run_id: int | None = None) -> list[dict[str, Any]]:
    if run_id is None:
        rows = conn.execute(
            """
            SELECT id, run_id, source_id, business_key, event_ts, source_revision,
                   payload_json, disposition
            FROM staging_rows ORDER BY id DESC LIMIT 200
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, run_id, source_id, business_key, event_ts, source_revision,
                   payload_json, disposition
            FROM staging_rows WHERE run_id = ? ORDER BY id
            """,
            (run_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json"))
        out.append(d)
    return out


def disposition_stats(
    conn: sqlite3.Connection, run_id: int | None = None
) -> dict[str, int]:
    """Live per-disposition counts.

    Structural drift rows live in ``schema_drifts`` (not ``staging_rows``);
    they are merged here as the virtual ``schema_drift`` disposition so the
    staging statistics and the drift list stay mutually explainable.
    """
    clause = "" if run_id is None else "WHERE run_id = ?"
    params: tuple[Any, ...] = (run_id,) if run_id is not None else ()

    rows = conn.execute(
        f"SELECT disposition, COUNT(*) AS c FROM staging_rows {clause} GROUP BY disposition",
        params,
    ).fetchall()
    counts = {r["disposition"]: int(r["c"]) for r in rows}
    counts["schema_drift"] = int(
        conn.execute(
            f"SELECT COUNT(*) AS c FROM schema_drifts {clause}", params
        ).fetchone()["c"]
    )
    counts["staging_total"] = sum(
        int(r["c"])
        for r in conn.execute(
            f"SELECT COUNT(*) AS c FROM staging_rows {clause}", params
        ).fetchall()
    )
    counts["total_accounted"] = counts["staging_total"] + counts["schema_drift"]
    return counts


def insert_conflict(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    source_id: int,
    business_key: str,
    existing_payload: dict[str, Any],
    incoming_payload: dict[str, Any],
    reason: str,
) -> None:
    conn.execute(
        """
        INSERT INTO conflicts(
            run_id, source_id, business_key, existing_payload, incoming_payload,
            reason, created_at
        ) VALUES (?,?,?,?,?,?,datetime('now'))
        """,
        (
            run_id,
            source_id,
            business_key,
            json.dumps(existing_payload, ensure_ascii=False),
            json.dumps(incoming_payload, ensure_ascii=False),
            reason,
        ),
    )


def list_conflicts(
    conn: sqlite3.Connection, run_id: int | None = None
) -> list[dict[str, Any]]:
    if run_id is None:
        rows = conn.execute(
            """
            SELECT id, run_id, source_id, business_key, existing_payload, incoming_payload,
                   reason, created_at
            FROM conflicts ORDER BY id DESC
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, run_id, source_id, business_key, existing_payload, incoming_payload,
                   reason, created_at
            FROM conflicts WHERE run_id = ? ORDER BY id
            """,
            (run_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["existing"] = json.loads(d.pop("existing_payload"))
        d["incoming"] = json.loads(d.pop("incoming_payload"))
        out.append(d)
    return out


def count_conflicts(
    conn: sqlite3.Connection, run_id: int | None = None
) -> int:
    if run_id is None:
        return int(conn.execute("SELECT COUNT(*) c FROM conflicts").fetchone()["c"])
    return int(
        conn.execute(
            "SELECT COUNT(*) c FROM conflicts WHERE run_id = ?", (run_id,)
        ).fetchone()["c"]
    )
