from fastapi import APIRouter, Query

from app.db import connect
from app.repositories import contracts as contract_repo

router = APIRouter(tags=["schema-drifts"])


@router.get("/schema-drifts")
def list_schema_drifts(run_id: int | None = Query(default=None)):
    conn = connect()
    try:
        return contract_repo.list_drifts(conn, run_id)
    finally:
        conn.close()
