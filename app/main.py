from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.init_db import init_db
from app.db.session import get_db
from app.jobs.scheduler import start_scheduler, stop_scheduler

setup_logging()
logger = get_logger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle manager que substitui os decorators deprecados
    @app.on_event("startup") / @app.on_event("shutdown").
    """
    logger.info("ARGUS API iniciando — inicializando banco de dados...")
    init_db()
    logger.info("Banco inicializado — iniciando scheduler de sincronização...")
    start_scheduler()
    logger.info("ARGUS API pronta para receber requisições.")
    yield
    logger.info("ARGUS API encerrando — parando scheduler...")
    stop_scheduler()
    logger.info("ARGUS API encerrada com segurança.")


app = FastAPI(
    title=settings.APP_NAME,
    description="Backend FastAPI da plataforma ARGUS para eficiência de obras públicas municipais.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
def health():
    """
    Endpoint de health check.
    Inclui status do banco de dados, versão e timestamp para monitoramento
    por load balancers e ferramentas de observabilidade.
    """
    from datetime import datetime, timezone

    db_ok = False
    try:
        # Usa next(get_db()) direto para não depender de Depends()
        # (load balancers chamam este endpoint sem contexto de dependência)
        db = next(get_db())
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "version": app.version,
        "environment": settings.ENVIRONMENT,
        "db_status": "ok" if db_ok else "unavailable",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


app.include_router(api_router)
