from __future__ import annotations

from sqlalchemy import inspect, text

from app.db.session import Base, engine
from app.models.work import Alert, PublicWork, ModelCache
from app.models.geo import GeoLayer


def _sql_type(column) -> str:
    name = column.type.__class__.__name__.lower()
    if "integer" in name:
        return "INTEGER"
    if "float" in name or "numeric" in name:
        return "FLOAT"
    if "date" in name and "time" in name:
        return "DATETIME"
    if name == "date":
        return "DATE"
    return "TEXT"


def _ensure_columns(table_model) -> None:
    """Adiciona colunas novas em bancos já existentes sem Alembic."""
    table_name = table_model.__tablename__
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns(table_name)}
    missing = [col for col in table_model.__table__.columns if col.name not in existing]
    if not missing:
        return

    with engine.begin() as conn:
        for col in missing:
            default_sql = ""
            if col.default is not None and getattr(col.default, "arg", None) is not None:
                arg = col.default.arg
                if isinstance(arg, (int, float)):
                    default_sql = f" DEFAULT {arg}"
                elif isinstance(arg, str):
                    default_sql = f" DEFAULT '{arg}'"
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col.name} {_sql_type(col)}{default_sql}"))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_columns(PublicWork)
    _ensure_columns(Alert)
    _ensure_columns(ModelCache)
