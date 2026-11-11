import shutil
import tempfile
from pathlib import Path

import pytest

from app import db, seed
from app.config import FIXTURES_DIR
from app.engines.parse_feed import parse_fixture
from app.modules.schema_drift_guard import (
    DRIFT_MISSING_REQUIRED,
    DRIFT_TYPE_MISMATCH,
    DRIFT_UNDECLARED_FIELD,
    check_record,
    default_contract,
    validate_contract,
)
from app.repositories import canonical as canon_repo
from app.repositories import contracts as contract_repo
from app.repositories import runs as runs_repo
from app.services.ingest_service import run_ingest, run_reconciliation

# The sandbox mount makes creating a fresh on-disk sqlite file expensive, so
# the schema is materialized once per module. Each test then runs inside a
# transaction that is rolled back, giving per-test isolation without rebuilding
# the schema (test-only; production connections are untouched).
_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_tmp"


@pytest.fixture(scope="module")
def _db_path():
    _TMP_ROOT.mkdir(exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=_TMP_ROOT))
    path = tmp_dir / "test.db"
    original_path = db.DB_PATH
    db.DB_PATH = path
    seed.init_db()
    try:
        yield path
    finally:
        db.DB_PATH = original_path
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def fresh_db(_db_path):
    conn = db.connect()
    conn.execute("PRAGMA synchronous=OFF")
    # Snapshot every user table; restored in teardown so tests stay independent.
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    snapshot = {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    conn.close()
    try:
        yield _db_path
    finally:
        conn = db.connect()
        conn.execute("PRAGMA synchronous=OFF")
        for table in tables:
            conn.execute(f"DELETE FROM {table}")
            if snapshot[table]:
                columns = snapshot[table][0].keys()
                placeholders = ",".join("?" for _ in columns)
                conn.executemany(
                    f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                    [tuple(row) for row in snapshot[table]],
                )
        conn.commit()
        conn.close()


def _source_id_by_name(conn, name):
    row = conn.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()
    return row["id"]


# ---------------------------------------------------------------- guard unit


def test_default_contract_matches_seed_fixtures(fresh_db):
    conn = db.connect()
    try:
        contract = default_contract()
        for fixture, fmt in (
            ("alpha_catalog.json", "json"),
            ("beta_offers.jsonl", "jsonl"),
            ("gamma_shelf.xml", "xml"),
        ):
            for record in parse_fixture(FIXTURES_DIR / fixture, fmt):
                assert check_record(record, contract) == []
    finally:
        conn.close()


def test_guard_detects_three_drift_kinds():
    contract = default_contract()

    missing = {"business_key": "k1", "title": "t", "price": 1.0,
               "event_ts": "ts", "source_revision": 1}  # category missing
    findings = check_record({"raw": missing, **missing}, contract)
    assert [f["kind"] for f in findings] == [DRIFT_MISSING_REQUIRED]
    assert findings[0]["field"] == "category"

    bad_type = {**missing, "category": "c", "price": "oops"}
    findings = check_record({"raw": bad_type, **bad_type}, contract)
    assert [f["kind"] for f in findings] == [DRIFT_TYPE_MISMATCH]
    assert findings[0]["field"] == "price"

    undeclared = {**bad_type, "price": 1.0, "loyalty_tier": "gold"}
    findings = check_record({"raw": undeclared, **undeclared}, contract)
    assert [f["kind"] for f in findings] == [DRIFT_UNDECLARED_FIELD]
    assert findings[0]["field"] == "loyalty_tier"


def test_allow_undeclared_suppresses_undeclared_findings():
    contract = default_contract()
    contract["allow_undeclared"] = True
    record = {
        "business_key": "k", "title": "t", "price": 1.0, "category": "c",
        "event_ts": "ts", "source_revision": 1, "extra": "x",
    }
    assert check_record({"raw": record, **record}, contract) == []


def test_validate_contract_rejects_unknown_type_and_empty_fields():
    with pytest.raises(ValueError):
        validate_contract({"fields": {"f": {"required": True, "type": "struct"}}})
    with pytest.raises(ValueError):
        validate_contract({"fields": {"f": {"required": False, "type": "string"}}})


def test_validate_contract_protects_core_fields():
    base = default_contract()

    # A custom field on top of the locked core set is fine.
    extended = validate_contract(
        {"fields": {**base["fields"], "loyalty_tier": {"required": False, "type": "string"}}}
    )
    assert "loyalty_tier" in extended["fields"]

    # Dropping a core field is rejected.
    without_price = {k: v for k, v in base["fields"].items() if k != "price"}
    with pytest.raises(ValueError, match="core fields cannot be removed"):
        validate_contract({"fields": without_price})

    # Making a core field optional is rejected.
    relaxed = {
        **base["fields"],
        "price": {"required": False, "type": "number"},
    }
    with pytest.raises(ValueError, match="must stay required"):
        validate_contract({"fields": relaxed})

    # Retyping a core field is rejected.
    retyped = {
        **base["fields"],
        "price": {"required": True, "type": "string"},
    }
    with pytest.raises(ValueError, match="must keep type number"):
        validate_contract({"fields": retyped})


# ------------------------------------------------------------- ingest e2e


def test_driftlab_run_blocks_three_and_writes_one(fresh_db):
    conn = db.connect()
    try:
        source_id = _source_id_by_name(conn, "Delta Drift Lab")
    finally:
        conn.close()

    run = run_ingest(source_id, "full")

    assert run["parsed_count"] == 4
    assert run["upserted_count"] == 1
    assert run["drift_count"] == 3
    assert run["conflict_count"] == 0
    assert run["skipped_count"] == 0

    conn = db.connect()
    try:
        # Drift rows never reach canonical storage.
        assert canon_repo.count_items(conn) == 1
        canonical_keys = [r["business_key"] for r in canon_repo.list_items(conn)]
        assert canonical_keys == ["SKU-4001"]

        drifts = contract_repo.list_drifts(conn, run["id"])
        assert len(drifts) == 3
        kinds = {d["business_key"]: d["drift_kinds"] for d in drifts}
        assert kinds["SKU-4002"] == [DRIFT_MISSING_REQUIRED]
        assert kinds["SKU-4003"] == [DRIFT_TYPE_MISMATCH]
        assert kinds["SKU-4004"] == [DRIFT_UNDECLARED_FIELD]
        # Enough field-level comparison info is retained.
        bad_price = next(d for d in drifts if d["business_key"] == "SKU-4003")
        assert bad_price["raw_payload"]["price"] == "oops"
        assert bad_price["findings"][0]["expected_type"] == "number"

        stats = runs_repo.disposition_stats(conn, run["id"])
        assert stats["total_accounted"] == 4
        assert stats["schema_drift"] == 3
        assert stats.get("insert", 0) + stats.get("upsert", 0) == 1

        reconciliation = run_reconciliation(run["id"])
        assert reconciliation["balanced"] is True
        assert reconciliation["lists"]["drift_rows_for_run"] == 3
        assert reconciliation["lists"]["canonical_total_rows"] == 1
    finally:
        conn.close()


def test_clean_source_contract_match_writes_all(fresh_db):
    conn = db.connect()
    try:
        alpha_id = _source_id_by_name(conn, "Alpha Catalog")
    finally:
        conn.close()

    run = run_ingest(alpha_id, "full")
    assert run["parsed_count"] == 3
    assert run["upserted_count"] == 3
    assert run["drift_count"] == 0

    conn = db.connect()
    try:
        # Report canonical_count and the canonical list must be the same number.
        assert run["report"]["canonical_count"] == canon_repo.count_items(conn) == 3
        assert len(canon_repo.list_items(conn)) == 3
        assert run_reconciliation(run["id"])["balanced"] is True
    finally:
        conn.close()


def test_tightening_contract_blocks_seed_source_then_reset_unblocks(fresh_db):
    conn = db.connect()
    try:
        alpha_id = _source_id_by_name(conn, "Alpha Catalog")
        tight = default_contract()
        tight["fields"]["sku_region"] = {"required": True, "type": "string"}
        contract_repo.upsert_contract(conn, alpha_id, tight)
        conn.commit()
    finally:
        conn.close()

    blocked = run_ingest(alpha_id, "full")
    assert blocked["drift_count"] == 3
    assert blocked["upserted_count"] == 0

    conn = db.connect()
    try:
        assert canon_repo.count_items(conn) == 0
        assert contract_repo.count_drifts(conn, blocked["id"]) == 3
        contract_repo.reset_contract(conn, alpha_id)
        conn.commit()
    finally:
        conn.close()

    unblocked = run_ingest(alpha_id, "full")
    assert unblocked["drift_count"] == 0
    assert unblocked["upserted_count"] == 3


def test_conflict_path_still_works_alongside_guard(fresh_db):
    conn = db.connect()
    try:
        alpha_id = _source_id_by_name(conn, "Alpha Catalog")
        beta_id = _source_id_by_name(conn, "Beta Offers")
    finally:
        conn.close()

    run_ingest(alpha_id, "full")
    beta_run = run_ingest(beta_id, "full")

    assert beta_run["parsed_count"] == 3
    assert beta_run["conflict_count"] == 1
    assert beta_run["drift_count"] == 0
    conn = db.connect()
    try:
        assert runs_repo.count_conflicts(conn, beta_run["id"]) == 1
        assert run_reconciliation(beta_run["id"])["balanced"] is True
    finally:
        conn.close()


def test_data_persists_across_connections(fresh_db):
    conn = db.connect()
    try:
        alpha_id = _source_id_by_name(conn, "Alpha Catalog")
    finally:
        conn.close()

    run = run_ingest(alpha_id, "full")
    run_id = run["id"]

    # A brand new connection (same file, simulating a page refresh) sees it all.
    conn = db.connect()
    try:
        assert runs_repo.get_run(conn, run_id)["upserted_count"] == 3
        assert canon_repo.count_items(conn) == 3
        assert contract_repo.get_contract(conn, alpha_id) is not None
    finally:
        conn.close()
