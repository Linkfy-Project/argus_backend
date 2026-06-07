"""
Endpoints de alertas do ARGUS.

Expõe endpoints para listar, filtrar e atualizar status dos alertas.
Cada alerta é enriquecido com dados da obra associada (municipio, fornecedor, etc).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.alert import AlertRead, AlertStatusUpdate, ALLOWED_ALERT_STATUSES
from app.services.alert_service import list_alerts, update_alert_status

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get(
    "",
    response_model=list[AlertRead],
    summary="Lista alertas com filtros",
    description=(
        "Retorna lista de alertas enriquecidos com dados da obra associada. "
        "Suporta filtros por município, severidade, status, tipo, bairro, "
        "fornecedor, obra_id e busca textual."
    ),
)
def index(
    municipio: str | None = Query(None, description="Filtrar por município (normalizado, sem acento)"),
    severity: str | None = Query(None, description="Filtrar por severidade (info, warning, alert, critical)"),
    status: str | None = Query(None, description="Filtrar por status do alerta (Novo, Em análise, Encaminhado, Resolvido, Descartado)"),
    tipo: str | None = Query(None, description="Filtrar por tipo de alerta (ex: 'Atraso crítico', 'Estouro de custo')"),
    bairro: str | None = Query(None, description="Filtrar por bairro da obra"),
    fornecedor: str | None = Query(None, description="Filtrar por fornecedor/contratado"),
    obra_id: int | None = Query(None, description="Filtrar por ID da obra"),
    search: str | None = Query(None, description="Busca textual em mensagem, código, descrição, município e fornecedor"),
    db: Session = Depends(get_db),
):
    """Lista alertas com filtros opcionais."""
    return list_alerts(
        db,
        municipio=municipio,
        severity=severity,
        status=status,
        tipo=tipo,
        bairro=bairro,
        fornecedor=fornecedor,
        obra_id=obra_id,
        search=search,
    )


@router.patch(
    "/{alert_id}/status",
    response_model=AlertRead,
    summary="Atualiza status de um alerta",
    description=(
        "Atualiza o status de um alerta específico. "
        f"Status permitidos: {', '.join(ALLOWED_ALERT_STATUSES)}"
    ),
)
def update_status(alert_id: int, payload: AlertStatusUpdate, db: Session = Depends(get_db)):
    """Atualiza o status de um alerta."""
    try:
        result = update_alert_status(db, alert_id, payload.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not result:
        raise HTTPException(status_code=404, detail="Alerta não encontrado")

    return result
