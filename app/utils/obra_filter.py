"""
Utilitário de filtro de obras públicas.

Fornece função auxiliar para filtrar registros em public_works
que foram classificados como NÃO-OBRA (is_obra=0) pelo pipeline de IA.

Regra de negócio:
- is_obra = 1 no model_cache → É obra pública → MANTER
- is_obra = 0 no model_cache → NÃO é obra pública → DESCARTAR
- Sem correspondência no model_cache → MANTER (tratar como obra)
- description_hash nulo → MANTER (tratar como obra)
"""

from __future__ import annotations

from sqlalchemy import and_, exists

from app.models.work import ModelCache, PublicWork


def filter_obras_query(q):
    """
    Aplica filtro para excluir registros que foram classificados como
    NÃO-OBRA (is_obra=0) pelo pipeline de IA no model_cache.

    A lógica é: excluir apenas registros que TEM correspondência no
    model_cache COM is_obra=0. Registros sem correspondência ou com
    is_obra=1 são mantidos.

    Args:
        q: Query SQLAlchemy já iniciada sobre PublicWork.

    Returns:
        Query com filtro aplicado.
    """
    # Subquery correlacionada: existe model_cache com mesmo description_hash e is_obra=0?
    # Se existir, o registro é excluído (~exists).
    # Se não existir (sem cache ou com is_obra=1), o registro é mantido.
    has_non_obra_cache = exists().where(
        and_(
            ModelCache.description_hash == PublicWork.description_hash,
            ModelCache.is_obra == 0,
        )
    )
    return q.filter(~has_non_obra_cache)
