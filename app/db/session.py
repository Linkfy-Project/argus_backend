import unicodedata
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings


def _sqlite_unaccent(text: str | None) -> str | None:
    """
    Função SQL customizada para SQLite que remove acentos de strings.
    Registra como função escalar no SQLite para permitir buscas
    insensíveis a acentos (ex: 'macae' encontra 'Macaé').
    """
    if text is None:
        return None
    # Decompõe caracteres unicode (NFD) e remove diacríticos (combining marks)
    normalized = unicodedata.normalize("NFD", text)
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


settings = get_settings()

engine_kwargs = {
    "pool_pre_ping": True,
}

if settings.DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(
    settings.DATABASE_URL,
    **engine_kwargs,
)

# ── Registra função unaccent no SQLite para buscas sem acentos ──
# Exemplo: unaccent('Macaé') → 'Macae', permitindo ILIKE com 'macae'
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_unaccent(dbapi_conn, connection_record):
        dbapi_conn.create_function("unaccent", 1, _sqlite_unaccent)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()