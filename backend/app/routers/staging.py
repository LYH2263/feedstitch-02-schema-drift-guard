from fastapi import APIRouter, Query

from app.db import connect
from app.repositories import runs as runs_repo

router = APIRouter(tags=["staging"])


@router.get("/staging")
def list_staging(run_id: int | None = Query(default=None)):
    conn = connect()
    try:
        return runs_repo.list_staging(conn, run_id)
    finally:
        conn.close()


@router.get("/staging/stats")
def staging_stats(run_id: int | None = Query(default=None)):
    """Per-disposition counts; schema_drift rows are merged from the drift
    table so every parsed row is accounted for in one place."""
    conn = connect()
    try:
        return runs_repo.disposition_stats(conn, run_id)
    finally:
        conn.close()
