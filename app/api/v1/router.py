from fastapi import APIRouter
from app.api.v1.endpoints import works, etl, analytics, ml, exports, geo, dashboard, territory, alerts, contracts, suppliers, reports

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(works.router)
api_router.include_router(etl.router)
api_router.include_router(analytics.router)
api_router.include_router(ml.router)
api_router.include_router(exports.router)
api_router.include_router(geo.router)
api_router.include_router(dashboard.router)
api_router.include_router(territory.router)
api_router.include_router(alerts.router)
api_router.include_router(contracts.router)
api_router.include_router(suppliers.router)
api_router.include_router(reports.router)
