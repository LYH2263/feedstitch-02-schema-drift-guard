from fastapi import APIRouter

from app.routers import (
    canonical,
    conflicts,
    contracts,
    dashboard,
    drifts,
    runs,
    settings,
    sources,
    staging,
)

api = APIRouter(prefix="/api")
api.include_router(dashboard.router)
api.include_router(sources.router)
api.include_router(contracts.router)
api.include_router(runs.router)
api.include_router(staging.router)
api.include_router(conflicts.router)
api.include_router(drifts.router)
api.include_router(canonical.router)
api.include_router(settings.router)
