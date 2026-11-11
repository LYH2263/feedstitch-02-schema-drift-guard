from fastapi import APIRouter, HTTPException

from app.db import connect
from app.repositories import contracts as contract_repo
from app.repositories import sources as sources_repo
from app.schemas.entities import ContractUpdate

router = APIRouter(tags=["contracts"])


@router.get("/contracts")
def list_contracts():
    conn = connect()
    try:
        return contract_repo.list_contracts(conn)
    finally:
        conn.close()


@router.get("/sources/{source_id}/contract")
def get_contract(source_id: int):
    conn = connect()
    try:
        if not sources_repo.get_source(conn, source_id):
            raise HTTPException(404, "source not found")
        row = contract_repo.get_contract(conn, source_id)
        if row is None:
            raise HTTPException(404, "contract not found")
        return row
    finally:
        conn.close()


@router.put("/sources/{source_id}/contract")
def put_contract(source_id: int, body: ContractUpdate):
    conn = connect()
    try:
        if not sources_repo.get_source(conn, source_id):
            raise HTTPException(404, "source not found")
        try:
            normalized = contract_repo.upsert_contract(
                conn,
                source_id,
                body.model_dump(),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        conn.commit()
        return contract_repo.get_contract(conn, source_id)
    finally:
        conn.close()


@router.post("/sources/{source_id}/contract/reset")
def reset_contract(source_id: int):
    conn = connect()
    try:
        if not sources_repo.get_source(conn, source_id):
            raise HTTPException(404, "source not found")
        contract_repo.reset_contract(conn, source_id)
        conn.commit()
        return contract_repo.get_contract(conn, source_id)
    finally:
        conn.close()
