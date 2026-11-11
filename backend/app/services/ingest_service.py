from __future__ import annotations

from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.merge_rules import decide_merge, eligible_for_incremental
from app.engines.parse_feed import CORE_FIELDS, parse_fixture
from app.modules.schema_drift_guard import check_record
from app.repositories import canonical as canon_repo
from app.repositories import contracts as contract_repo
from app.repositories import runs as runs_repo
from app.repositories import sources as sources_repo


def _contract_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the staging payload from the normalized core fields.

    Only records that passed the contract reach this point, so every core
    field is populated and normalized.
    """
    return {key: item[key] for key in CORE_FIELDS}


def preview_source(source_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        source = sources_repo.get_source(conn, source_id)
        if not source:
            raise ValueError("source not found")
        contract = contract_repo.get_contract_payload(conn, source_id)
        path = FIXTURES_DIR / source["fixture_path"]
        items = parse_fixture(path, source["format"])
        preview_items = []
        drift_count = 0
        for item in items:
            findings = check_record(item, contract)
            if findings:
                drift_count += 1
            preview_items.append(
                {
                    **{key: item.get(key) for key in CORE_FIELDS},
                    "contract_status": "drift" if findings else "ok",
                    "drift_findings": findings,
                }
            )
        return {
            "source": source,
            "contract": contract,
            "count": len(items),
            "drift_count": drift_count,
            "items": preview_items[:50],
        }
    finally:
        conn.close()


def run_ingest(source_id: int, mode: str = "incremental") -> dict[str, Any]:
    if mode not in ("incremental", "full"):
        raise ValueError("mode must be incremental or full")

    conn = connect()
    try:
        source = sources_repo.get_source(conn, source_id)
        if not source:
            raise ValueError("source not found")
        if not source["enabled"]:
            raise ValueError("source disabled")

        contract = contract_repo.get_contract_payload(conn, source_id)

        if mode == "full":
            sources_repo.set_watermark(conn, source_id, None)
            source["watermark_ts"] = None

        run_id = runs_repo.create_run(conn, source_id, mode)
        path = FIXTURES_DIR / source["fixture_path"]

        try:
            items = parse_fixture(path, source["format"])
        except Exception as exc:  # noqa: BLE001
            runs_repo.finish_run(
                conn,
                run_id,
                status="failed",
                parsed=0,
                upserted=0,
                conflicts=0,
                skipped=0,
                drift=0,
                report={"error": str(exc)},
            )
            conn.commit()
            raise

        parsed = 0
        upserted = 0
        conflicts = 0
        skipped = 0
        drift = 0
        max_event_ts: str | None = source.get("watermark_ts")
        dispositions: list[str] = []

        for item in items:
            parsed += 1

            # --- 1) structural contract gate (runs before watermark/merge) ---
            findings = check_record(item, contract)
            if findings:
                drift += 1
                dispositions.append("schema_drift")
                contract_repo.insert_drift(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    business_key=item.get("business_key"),
                    raw_payload=item.get("raw", {}),
                    findings=findings,
                )
                # Drift rows never reach staging merge or canonical storage,
                # and must not advance the watermark.
                continue

            item["payload"] = _contract_payload(item)

            # --- 2) incremental watermark gate ---
            if mode == "incremental" and not eligible_for_incremental(
                item["event_ts"],
                source.get("watermark_ts"),
                int(source.get("lookback_seconds") or 0),
            ):
                runs_repo.insert_staging(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    item=item,
                    disposition="skipped_watermark",
                )
                skipped += 1
                dispositions.append("skipped_watermark")
                continue

            # --- 3) business-key merge / conflict ---
            existing = canon_repo.get_by_key(conn, item["business_key"])
            existing_view = None
            if existing:
                existing_view = {
                    "business_key": existing["business_key"],
                    "title": existing["title"],
                    "price": existing["price"],
                    "category": existing["category"],
                    "event_ts": existing["event_ts"],
                    "source_revision": existing["source_revision"],
                    "payload": existing["payload"],
                }

            decision = decide_merge(existing_view, item)
            if decision in ("insert", "upsert"):
                canon_repo.upsert_item(conn, item, source_id)
                runs_repo.insert_staging(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    item=item,
                    disposition=decision,
                )
                upserted += 1
                dispositions.append(decision)
                if max_event_ts is None or item["event_ts"] > max_event_ts:
                    max_event_ts = item["event_ts"]
            elif decision == "conflict":
                runs_repo.insert_conflict(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    business_key=item["business_key"],
                    existing_payload=existing_view["payload"] if existing_view else {},
                    incoming_payload=item["payload"],
                    reason="non_key_field_mismatch",
                )
                runs_repo.insert_staging(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    item=item,
                    disposition="quarantine",
                )
                conflicts += 1
                dispositions.append("quarantine")
                if max_event_ts is None or item["event_ts"] > max_event_ts:
                    max_event_ts = item["event_ts"]
            else:
                runs_repo.insert_staging(
                    conn,
                    run_id=run_id,
                    source_id=source_id,
                    item=item,
                    disposition="skip_stale",
                )
                skipped += 1
                dispositions.append("skip_stale")
                if max_event_ts is None or item["event_ts"] > max_event_ts:
                    max_event_ts = item["event_ts"]

        if max_event_ts:
            sources_repo.set_watermark(conn, source_id, max_event_ts)

        # Reconciliation is computed from the database, not from in-memory
        # counters, so the report and the list pages share one source of truth.
        stats = runs_repo.disposition_stats(conn, run_id)
        db_conflicts = runs_repo.count_conflicts(conn, run_id)
        db_drifts = contract_repo.count_drifts(conn, run_id)
        canonical_count = canon_repo.count_items(conn)
        reconciliation = {
            "parsed_count": parsed,
            "upserted_count": upserted,
            "conflict_count": conflicts,
            "skipped_count": skipped,
            "drift_count": drift,
            "accounted_count": stats["total_accounted"],
            "staging_total": stats["staging_total"],
            "staging_dispositions": {
                key: value
                for key, value in stats.items()
                if key not in ("staging_total", "total_accounted", "schema_drift")
            },
            "drift_rows": db_drifts,
            "conflict_rows": db_conflicts,
            "canonical_count": canonical_count,
            "balanced": parsed == stats["total_accounted"]
            and upserted == stats.get("insert", 0) + stats.get("upsert", 0)
            and conflicts == db_conflicts == stats.get("quarantine", 0)
            and drift == db_drifts == stats.get("schema_drift", 0)
            and skipped
            == stats.get("skipped_watermark", 0) + stats.get("skip_stale", 0),
        }
        report = {
            "source_id": source_id,
            "mode": mode,
            "parsed_count": parsed,
            "upserted_count": upserted,
            "conflict_count": conflicts,
            "skipped_count": skipped,
            "drift_count": drift,
            "canonical_count": canonical_count,
            "watermark_ts": max_event_ts,
            "dispositions": dispositions,
            "reconciliation": reconciliation,
        }
        runs_repo.finish_run(
            conn,
            run_id,
            status="ok",
            parsed=parsed,
            upserted=upserted,
            conflicts=conflicts,
            skipped=skipped,
            drift=drift,
            report=report,
        )
        conn.commit()
        run = runs_repo.get_run(conn, run_id)
        return run or report
    finally:
        conn.close()


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        sources = sources_repo.list_sources(conn)
        return {
            "source_count": len(sources),
            "enabled_sources": sum(1 for s in sources if s["enabled"]),
            "canonical_count": canon_repo.count_items(conn),
            "conflict_count": runs_repo.count_conflicts(conn),
            "drift_count": contract_repo.count_drifts(conn),
            "run_count": conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"],
            "staging_count": conn.execute("SELECT COUNT(*) c FROM staging_rows").fetchone()["c"],
        }
    finally:
        conn.close()


def run_reconciliation(run_id: int) -> dict[str, Any]:
    """Recompute the report-vs-list reconciliation for a single run."""
    conn = connect()
    try:
        run = runs_repo.get_run(conn, run_id)
        if not run:
            raise ValueError("run not found")
        stats = runs_repo.disposition_stats(conn, run_id)
        db_conflicts = runs_repo.count_conflicts(conn, run_id)
        db_drifts = contract_repo.count_drifts(conn, run_id)
        canonical_total = canon_repo.count_items(conn)
        return {
            "run_id": run_id,
            "report": {
                "parsed_count": run["parsed_count"],
                "upserted_count": run["upserted_count"],
                "conflict_count": run["conflict_count"],
                "skipped_count": run["skipped_count"],
                "drift_count": run["drift_count"],
            },
            "lists": {
                "staging_total_for_run": stats["staging_total"],
                "staging_dispositions": {
                    key: value
                    for key, value in stats.items()
                    if key not in ("staging_total", "total_accounted", "schema_drift")
                },
                "drift_rows_for_run": db_drifts,
                "conflict_rows_for_run": db_conflicts,
                "canonical_total_rows": canonical_total,
            },
            "balanced": run["parsed_count"] == stats["total_accounted"]
            and run["upserted_count"]
            == stats.get("insert", 0) + stats.get("upsert", 0)
            and run["conflict_count"] == db_conflicts == stats.get("quarantine", 0)
            and run["drift_count"] == db_drifts == stats.get("schema_drift", 0)
            and run["skipped_count"]
            == stats.get("skipped_watermark", 0) + stats.get("skip_stale", 0),
        }
    finally:
        conn.close()
