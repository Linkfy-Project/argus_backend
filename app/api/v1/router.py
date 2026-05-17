from fastapi import APIRouter
from app.api.v1.endpoints import works, etl, analytics, ml, exports

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(works.router)
api_router.include_router(etl.router)
api_router.include_router(analytics.router)
api_router.include_router(ml.router)
api_router.include_router(exports.router)
