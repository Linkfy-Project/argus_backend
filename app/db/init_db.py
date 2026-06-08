"""
Script de inicialização e migração do banco de dados do ARGUS.

Responsável por criar tabelas, adicionar colunas novas em bancos existentes
e executar migrações de dados (como normalização de nomes de municípios).
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from app.db.session import Base, engine
from app.models.work import Alert, PublicWork, ModelCache
from app.models.geo import GeoLayer
from app.core.logging import get_logger

logger = get_logger(__name__)


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


def _normalize_municipios_existing() -> None:
    """
    Migração de dados: corrige variações de nomes de municípios já existentes no banco.

    Atualiza registros com variações como "macae", "macae-rj", "macae/rj" para o
    formato canônico "Macaé". Executado de forma idempotente (seguro para rodar
    múltiplas vezes sem efeitos colaterais).

    NOTA: Usa UPPER(TRIM(...)) para compatibilidade com SQLite e PostgreSQL.
    """
    inspector = inspect(engine)
    if "public_works" not in inspector.get_table_names():
        logger.info("init_db - tabela public_works não existe, pulando migração de municípios")
        return

    # Verifica se a coluna municipio existe
    existing_cols = {col["name"] for col in inspector.get_columns("public_works")}
    if "municipio" not in existing_cols:
        logger.info("init_db - coluna municipio não existe, pulando migração de municípios")
        return

    # Variações conhecidas de "Macae" que devem ser normalizadas para "Macaé"
    # Usa LOWER + REPLACE para remover acentos e comparar case-insensitive
    variations = ["macae", "macae-rj", "macae/rj", "macaé", "macae "]

    with engine.begin() as conn:
        for variation in variations:
            # Conta quantos registros serão afetados antes de atualizar
            count_result = conn.execute(
                text("SELECT COUNT(*) FROM public_works WHERE LOWER(TRIM(municipio)) = :var"),
                {"var": variation.lower().strip()},
            ).scalar()

            if count_result and count_result > 0:
                logger.info(f"init_db - normalizando {count_result} registros com municipio='{variation}' -> 'Macaé'")
                conn.execute(
                    text("UPDATE public_works SET municipio = 'Macaé' WHERE LOWER(TRIM(municipio)) = :var"),
                    {"var": variation.lower().strip()},
                )

    logger.info("init_db - migração de normalização de municípios concluída")


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_columns(PublicWork)
    _ensure_columns(Alert)
    _ensure_columns(ModelCache)
    _normalize_municipios_existing()
