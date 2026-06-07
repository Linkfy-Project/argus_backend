"""
Endpoints do Dashboard Executivo do ARGUS.

Fornece dados prontos para o frontend executivo, eliminando a necessidade
de cálculos no navegador. O gestor público recebe respostas prontas sobre
o estado das obras do município.

Endpoints:
- GET /dashboard/summary — KPIs agregados do painel executivo
- GET /dashboard/priority-queue — Fila priorizada de obras que precisam de atenção
- GET /dashboard/risk-distribution — Distribuição de obras por faixa de risco
- GET /dashboard/top-neighborhoods-risk — Ranking de bairros com maior risco
- GET /dashboard/top-suppliers-risk — Ranking de fornecedores com maior risco

Todos os endpoints tratam nulos com segurança e retornam zeros/listas vazias
quando não há dados, evitando erros 500.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.dashboard import (
    DashboardSummary,
    PriorityQueueItem,
    RiskDistributionItem,
    NeighborhoodRiskItem,
    SupplierRiskItem,
)
from app.services.dashboard_service import (
    get_dashboard_summary,
    get_priority_queue,
    get_risk_distribution,
    get_top_neighborhoods_risk,
    get_top_suppliers_risk,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get(
    "/summary",
    response_model=DashboardSummary,
    summary="Resumo Executivo do Dashboard",
    description=(
        "Retorna todos os KPIs que o frontend precisa para montar o painel executivo "
        "sem calcular nada no navegador. Inclui contagem de obras por faixa de risco, "
        "valores financeiros, alertas, indicadores de qualidade dos dados e score médio. "
        "Normaliza nomes de município com e sem acento (ex: 'Macae' encontra 'Macaé'). "
        "Retorna zeros quando não há dados para o município informado."
    ),
)
def dashboard_summary(
    municipio: str = Query(
        default="Macae",
        description="Nome do município para filtrar (aceita com/sem acento, ex: 'Macae' ou 'Macaé')",
    ),
    db: Session = Depends(get_db),
) -> DashboardSummary:
    """
    Resumo executivo com todos os KPIs do painel.

    - Score alto é bom (80-100 = eficiente, 0-39 = crítico).
    - Valor potencial em risco = soma de contract_value das obras com score < 60.
    - data_quality_score = percentual de completude dos campos obrigatórios.
    """
    return get_dashboard_summary(db, municipio=municipio)


@router.get(
    "/priority-queue",
    response_model=list[PriorityQueueItem],
    summary="Fila Priorizada de Obras",
    description=(
        "Retorna as obras que o gestor deve avaliar primeiro, ordenadas por urgência. "
        "O critério de prioridade combina: score baixo, alertas críticos, atraso, "
        "aditivos acima de 25%, valor contratado alto e falta de geolocalização. "
        "Obras sem score entram como 'Sem dados suficientes' com ação sugerida de "
        "saneamento cadastral. Falta de geolocalização aumenta prioridade, mas não "
        "supera uma obra crítica de alto valor."
    ),
)
def dashboard_priority_queue(
    municipio: str = Query(
        default="Macae",
        description="Nome do município para filtrar (aceita com/sem acento)",
    ),
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Número máximo de obras na fila (1-100, default 10)",
    ),
    db: Session = Depends(get_db),
) -> list[PriorityQueueItem]:
    """
    Fila priorizada de obras que precisam de atenção imediata.

    Cada item inclui motivo principal, ação sugerida e valor em risco estimado.
    """
    return get_priority_queue(db, municipio=municipio, limit=limit)


@router.get(
    "/risk-distribution",
    response_model=list[RiskDistributionItem],
    summary="Distribuição de Obras por Faixa de Risco",
    description=(
        "Retorna a contagem de obras em cada faixa de risco do score ARGUS: "
        "Eficiente (80-100), Atenção (60-79), Alto risco (40-59), "
        "Crítico (0-39) e Sem score (null). Útil para gráficos de pizza "
        "ou barras no frontend executivo."
    ),
)
def dashboard_risk_distribution(
    municipio: str = Query(
        default="Macae",
        description="Nome do município para filtrar (aceita com/sem acento)",
    ),
    db: Session = Depends(get_db),
) -> list[RiskDistributionItem]:
    """
    Distribuição de obras por faixa de risco para gráficos do dashboard.
    """
    return get_risk_distribution(db, municipio=municipio)


@router.get(
    "/top-neighborhoods-risk",
    response_model=list[NeighborhoodRiskItem],
    summary="Ranking de Bairros com Maior Risco",
    description=(
        "Retorna ranking de bairros ordenado por score médio crescente (pior primeiro). "
        "Cada item inclui contagem de obras, obras críticas, obras atrasadas, "
        "valor total, alertas, classificação de risco e recomendação de ação. "
        "Útil para identificar áreas geográficas que precisam de fiscalização prioritária."
    ),
)
def dashboard_top_neighborhoods_risk(
    municipio: str = Query(
        default="Macae",
        description="Nome do município para filtrar (aceita com/sem acento)",
    ),
    limit: int = Query(
        default=10,
        ge=1,
        le=50,
        description="Número máximo de bairros no ranking (1-50, default 10)",
    ),
    db: Session = Depends(get_db),
) -> list[NeighborhoodRiskItem]:
    """
    Ranking de bairros com maior risco para o gestor territorial.
    """
    return get_top_neighborhoods_risk(db, municipio=municipio, limit=limit)


@router.get(
    "/top-suppliers-risk",
    response_model=list[SupplierRiskItem],
    summary="Ranking de Fornecedores com Maior Risco",
    description=(
        "Retorna ranking de fornecedores ordenado por score médio crescente (pior primeiro). "
        "Cada item inclui CNPJ, contagem de contratos, valor total, obras críticas, "
        "alertas, percentual médio de aditivos, classificação de risco e recomendação. "
        "Útil para identificar fornecedores problemáticos que precisam de auditoria."
    ),
)
def dashboard_top_suppliers_risk(
    municipio: str = Query(
        default="Macae",
        description="Nome do município para filtrar (aceita com/sem acento)",
    ),
    limit: int = Query(
        default=10,
        ge=1,
        le=50,
        description="Número máximo de fornecedores no ranking (1-50, default 10)",
    ),
    db: Session = Depends(get_db),
) -> list[SupplierRiskItem]:
    """
    Ranking de fornecedores com maior risco para auditoria de contratos.
    """
    return get_top_suppliers_risk(db, municipio=municipio, limit=limit)
