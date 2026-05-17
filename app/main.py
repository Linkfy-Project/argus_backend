from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db.init_db import init_db
from app.jobs.scheduler import start_scheduler, stop_scheduler

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    description="Backend FastAPI da plataforma ARGUS para eficiência de obras públicas municipais.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    """
    Inicializa o banco e inicia o scheduler automático de sincronização.
    """
    init_db()
    start_scheduler()


@app.on_event("shutdown")
def shutdown():
    """
    Encerra o scheduler com segurança quando a API for desligada.
    """
    stop_scheduler()


@app.get("/health", tags=["health"])
def health():
    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "environment": settings.ENVIRONMENT,
    }


app.include_router(api_router)