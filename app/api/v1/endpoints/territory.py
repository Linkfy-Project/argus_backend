"""
Endpoints de Análise Microterritorial de Macaé-RJ.

Fornece dados prontos para o frontend montar a página "Análise Macaé-RJ"
sem precisar fazer agregações complexas no browser.

Endpoints:
- GET /territory/macae/overview — Visão geral territorial
- GET /territory/macae/neighborhoods — Lista de bairros com indicadores de risco
- GET /territory/macae/neighborhoods/{bairro} — Detalhe de um bairro específico
- GET /territory/macae/heatmap — GeoJSON para mapa de calor
- GET /territory/macae/data-quality — Qualidade dos dados territoriais

Todos os endpoints tratam nulos com segurança e retornam zeros/listas vazias
quando não há dados, evitando erros 500.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.territory import (
    DataQualityReport,
    HeatmapResponse,
    NeighborhoodDetail,
    NeighborhoodListItem,
    TerritoryOverview,
)
from app.services.territory_service import (
    get_data_quality_report,
    get_heatmap_geojson,
    get_neighborhood_detail,
    get_neighborhoods_list,
    get_territory_overview,
)

router = APIRouter(prefix="/territory", tags=["territory"])


@router.get(
    "/macae/overview",
    response_model=TerritoryOverview,
    summary="Visão Geral Territorial de Macaé-RJ",
    description=(
        "Retorna a visão geral da análise microterritorial de Macaé-RJ. "
        "Inclui contagem de bairros monitorados, obras, valor total contratado, "
        "score médio, bairros críticos, obras sem bairro/geolocalização, "
        "bairro mais crítico, bairro com maior valor, bairro com mais atrasos "
        "e recomendações territoriais para o gestor público."
    ),
)
def territory_overview(
    municipio: str = Query(
        default="Macae",
        description="Nome do município para filtrar (aceita com/sem acento, ex: 'Macae' ou 'Macaé')",
    ),
    db: Session = Depends(get_db),
) -> TerritoryOverview:
    """
    Visão geral da análise microterritorial de Macaé-RJ.

    Responde às perguntas:
    - Quais bairros concentram maior risco?
    - Onde estão as obras críticas?
    - Quais bairros têm maior valor contratado?
    - Quais regiões possuem dados ruins ou obras sem geolocalização?
    - Que recomendações territoriais o gestor deve seguir?
    """
    return get_territory_overview(db, municipio=municipio)


@router.get(
    "/macae/neighborhoods",
    response_model=list[NeighborhoodListItem],
    summary="Lista de Bairros com Indicadores de Risco",
    description=(
        "Retorna lista de bairros de Macaé-RJ com indicadores agregados de risco territorial. "
        "Ordenada por maior risco: score médio menor primeiro, mais obras críticas, "
        "mais alertas críticos, maior valor contratado. "
        "Cada bairro inclui: obras, valor total/pago, score médio, obras críticas, "
        "obras atrasadas, alertas, fornecedores, classificação e recomendação."
    ),
)
def territory_neighborhoods(
    municipio: str = Query(
        default="Macae",
        description="Nome do município para filtrar (aceita com/sem acento)",
    ),
    db: Session = Depends(get_db),
) -> list[NeighborhoodListItem]:
    """
    Lista de bairros com indicadores de risco territorial.

    Ordenação por maior risco:
    - Score médio menor primeiro
    - Mais obras críticas primeiro
    - Mais alertas críticos primeiro
    - Maior valor contratado primeiro
    """
    return get_neighborhoods_list(db, municipio=municipio)


@router.get(
    "/macae/neighborhoods/{bairro}",
    response_model=NeighborhoodDetail,
    summary="Detalhe de um Bairro Específico",
    description=(
        "Retorna detalhe completo de um bairro de Macaé-RJ, incluindo: "
        "resumo numérico, obras críticas, obras atrasadas, principais fornecedores, "
        "alertas, análise textual automática e ações recomendadas para o gestor."
    ),
)
def territory_neighborhood_detail(
    bairro: str,
    municipio: str = Query(
        default="Macae",
        description="Nome do município para filtrar (aceita com/sem acento)",
    ),
    db: Session = Depends(get_db),
) -> NeighborhoodDetail:
    """
    Detalhe completo de um bairro com obras críticas, atrasadas,
    fornecedores, alertas, análise textual e ações recomendadas.

    Retorna 404 se o bairro não for encontrado.
    """
    result = get_neighborhood_detail(db, bairro=bairro, municipio=municipio)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Bairro '{bairro}' não encontrado no município.",
        )
    return result


@router.get(
    "/macae/heatmap",
    response_model=HeatmapResponse,
    summary="Heatmap Territorial GeoJSON",
    description=(
        "Retorna FeatureCollection GeoJSON com obras georreferenciadas de Macaé-RJ "
        "e propriedades úteis para o mapa de calor: obra_id, nome, bairro, score, "
        "classificação, valor contratado, alertas, dias de atraso e fornecedor. "
        "Apenas obras com latitude e longitude válidas são incluídas."
    ),
)
def territory_heatmap(
    municipio: str = Query(
        default="Macae",
        description="Nome do município para filtrar (aceita com/sem acento)",
    ),
    db: Session = Depends(get_db),
) -> HeatmapResponse:
    """
    GeoJSON FeatureCollection para o heatmap territorial.

    Cada feature inclui:
    - geometry: Point com coordenadas [longitude, latitude]
    - properties: obra_id, nome, bairro, score, classificacao,
      valor_contratado, alertas, dias_atraso, fornecedor
    """
    return get_heatmap_geojson(db, municipio=municipio)


@router.get(
    "/macae/data-quality",
    response_model=DataQualityReport,
    summary="Relatório de Qualidade dos Dados Territoriais",
    description=(
        "Retorna relatório de qualidade dos dados territoriais de Macaé-RJ. "
        "Identifica: total de obras, obras sem bairro, sem geolocalização, "
        "sem valor, sem fornecedor, sem prazo, score de qualidade de dados "
        "e lista das obras que precisam de saneamento cadastral."
    ),
)
def territory_data_quality(
    municipio: str = Query(
        default="Macae",
        description="Nome do município para filtrar (aceita com/sem acento)",
    ),
    db: Session = Depends(get_db),
) -> DataQualityReport:
    """
    Relatório de qualidade dos dados territoriais.

    Critérios avaliados por obra:
    - Bairro preenchido
    - Geolocalização (latitude/longitude) preenchida
    - Valor contratado preenchido e > 0
    - Fornecedor preenchido
    - Prazo (due_at) preenchido

    data_quality_score = percentual de campos preenchidos (0-100).
    """
    return get_data_quality_report(db, municipio=municipio)
