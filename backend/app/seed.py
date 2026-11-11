import json

from app.db import connect
from app.modules.schema_drift_guard import default_contract


def _has_column(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _ensure_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS sources(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            format TEXT NOT NULL,
            fixture_path TEXT NOT NULL,
            watermark_ts TEXT,
            lookback_seconds INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            parsed_count INTEGER NOT NULL,
            upserted_count INTEGER NOT NULL,
            conflict_count INTEGER NOT NULL,
            skipped_count INTEGER NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS staging_rows(
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            business_key TEXT NOT NULL,
            event_ts TEXT NOT NULL,
            source_revision INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            disposition TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS canonical_items(
            id INTEGER PRIMARY KEY,
            business_key TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            price REAL NOT NULL,
            category TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            event_ts TEXT NOT NULL,
            source_revision INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conflicts(
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            business_key TEXT NOT NULL,
            existing_payload TEXT NOT NULL,
            incoming_payload TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_contracts(
            source_id INTEGER PRIMARY KEY,
            contract_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS schema_drifts(
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            business_key TEXT,
            raw_payload_json TEXT NOT NULL,
            findings_json TEXT NOT NULL,
            drift_kinds TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_schema_drifts_run ON schema_drifts(run_id);
        """
    )
    # Lightweight migration for databases created before the drift guard.
    if not _has_column(conn, "runs", "drift_count"):
        conn.execute("ALTER TABLE runs ADD COLUMN drift_count INTEGER NOT NULL DEFAULT 0")


def init_db():
    conn = connect()
    try:
        _ensure_schema(conn)

        if conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 0:
            conn.executemany(
                """
                INSERT INTO sources(name, format, fixture_path, watermark_ts, lookback_seconds, enabled, note)
                VALUES (?,?,?,?,?,?,?)
                """,
                [
                    (
                        "Alpha Catalog",
                        "json",
                        "alpha_catalog.json",
                        None,
                        0,
                        1,
                        "clean：三件独立货号，契约匹配时应全部 upsert",
                    ),
                    (
                        "Beta Offers",
                        "jsonl",
                        "beta_offers.jsonl",
                        None,
                        0,
                        1,
                        "dirty：SKU-1001 与 Alpha 同键异价→冲突；另含独立货号",
                    ),
                    (
                        "Gamma Shelf",
                        "xml",
                        "gamma_shelf.xml",
                        None,
                        0,
                        1,
                        "xml 源：两件独立货号",
                    ),
                    (
                        "Delta Drift Lab",
                        "jsonl",
                        "delta_driftlab.jsonl",
                        None,
                        0,
                        1,
                        "漂移演练：1 行合规 + 缺必填 / 类型不符 / 未声明字段各 1 行",
                    ),
                ],
            )
            conn.execute("INSERT INTO settings(key, value) VALUES ('project_label', 'Feedstitch')")
            conn.execute("INSERT INTO settings(key, value) VALUES ('default_mode', 'incremental')")
            conn.commit()

        # Every source always has an explicit, editable contract.
        for source in conn.execute("SELECT id FROM sources").fetchall():
            exists = conn.execute(
                "SELECT 1 FROM source_contracts WHERE source_id = ?",
                (source["id"],),
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO source_contracts(source_id, contract_json, updated_at)
                    VALUES (?,?,datetime('now'))
                    """,
                    (source["id"], json.dumps(default_contract(), ensure_ascii=False)),
                )
        conn.commit()
    finally:
        conn.close()
