"""
Serviço de alertas do ARGUS.

Responsável por listar, filtrar e atualizar status dos alertas.
Deriva dados da tabela Alert + PublicWork associada para enriquecer
a resposta com informações de obra, município, fornecedor, etc.
"""

from __future__ import annotations

import logging
import unicodedata

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_

from app.models.work import Alert, PublicWork
from app.utils.obra_filter import filter_obras_query
from app.schemas.alert import (
    ALERT_CODE_TO_TIPO,
    ALERT_CODE_TO_MOTIVO,
    ALERT_CODE_TO_ACAO,
    SEVERITY_TO_NIVEL,
    ALLOWED_ALERT_STATUSES,
    AlertRead,
)

logger = logging.getLogger(__name__)


def _normalize_municipio(raw: str) -> str:
    """
    Normaliza o termo de busca de município removendo acentos.
    Exemplo: 'macae' encontra 'Macaé' e vice-versa.
    """
    if not raw:
        return raw
    cleaned = unicodedata.normalize("NFD", raw)
    cleaned = "".join(c for c in cleaned if unicodedata.category(c) != "Mn")
    return cleaned.strip().lower()


def _normalize_fornecedor(name: str | None) -> str:
    """Normaliza nome vazio de fornecedor como 'Não informado'."""
    if not name or not name.strip():
        return "Não informado"
    return name.strip()


def _resolve_tipo(code: str) -> str:
    """Resolve o tipo legível a partir do código do alerta."""
    return ALERT_CODE_TO_TIPO.get(code, code.replace("_", " ").title())


def _resolve_nivel(severity: str) -> str:
    """Resolve o nível legível a partir da severidade."""
    return SEVERITY_TO_NIVEL.get(severity, severity.title())


def _resolve_motivo(code: str) -> str:
    """Resolve o motivo sugerido a partir do código do alerta."""
    return ALERT_CODE_TO_MOTIVO.get(code, "Verificar detalhes do alerta.")


def _resolve_acao(code: str) -> str:
    """Resolve a ação sugerida a partir do código do alerta."""
    return ALERT_CODE_TO_ACAO.get(code, "Analisar contexto da obra e tomar ação corretiva.")


def _alert_to_read(alert: Alert, work: PublicWork | None = None) -> AlertRead:
    """
    Converte um modelo Alert + PublicWork em AlertRead para a API.
    Enriquece com dados derivados: tipo, nível, motivo, ação sugerida, etc.
    """
    w = work or alert.work
    return AlertRead(
        id=alert.id,
        work_id=alert.work_id,
        tipo=_resolve_tipo(alert.code),
        code=alert.code,
        severity=alert.severity,
        nivel=_resolve_nivel(alert.severity),
        status=alert.status or "Novo",
        obra_nome=w.object_description if w else None,
        municipio=w.municipio if w else None,
        bairro=w.neighborhood if w else None,
        fornecedor=_normalize_fornecedor(w.contractor_name if w else None),
        descricao=alert.message,
        motivo=_resolve_motivo(alert.code),
        acao_sugerida=_resolve_acao(alert.code),
        data_deteccao=alert.created_at,
        score_argus=w.efficiency_score if w else None,
        valor_contratado=w.contract_value if w else None,
    )


def list_alerts(
    db: Session,
    municipio: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    tipo: str | None = None,
    bairro: str | None = None,
    fornecedor: str | None = None,
    obra_id: int | None = None,
    search: str | None = None,
) -> list[AlertRead]:
    """
    Lista alertas com filtros opcionais.
    Retorna lista de AlertRead enriquecidos com dados da obra associada.
    """
    # Busca alertas com join na obra, excluindo registros classificados como não-obra
    q = (
        db.query(Alert)
        .options(joinedload(Alert.work))
        .join(PublicWork, Alert.work_id == PublicWork.id)
    )
    # Aplica filtro para excluir alertas de obras classificadas como não-obra
    q = filter_obras_query(q)

    # ── Filtros ──────────────────────────────────────────────
    if municipio:
        normalized = _normalize_municipio(municipio)
        logger.debug("list_alerts - filtro municipio='%s' normalizado='%s'", municipio, normalized)
        q = q.filter(func.unaccent(PublicWork.municipio).ilike(f"%{normalized}%"))

    if severity:
        q = q.filter(Alert.severity.ilike(f"%{severity}%"))

    if status:
        q = q.filter(Alert.status.ilike(f"%{status}%"))

    if tipo:
        # Busca por código que mapeia para o tipo
        matching_codes = [code for code, t in ALERT_CODE_TO_TIPO.items() if tipo.lower() in t.lower()]
        if matching_codes:
            q = q.filter(Alert.code.in_(matching_codes))
        else:
            # Fallback: busca no próprio código
            q = q.filter(Alert.code.ilike(f"%{tipo}%"))

    if bairro:
        q = q.filter(PublicWork.neighborhood.ilike(f"%{bairro}%"))

    if fornecedor:
        normalized_f = _normalize_municipio(fornecedor)  # mesma normalização
        q = q.filter(func.unaccent(PublicWork.contractor_name).ilike(f"%{normalized_f}%"))

    if obra_id is not None:
        q = q.filter(Alert.work_id == obra_id)

    if search:
        term = f"%{search}%"
        normalized_search = _normalize_municipio(search)
        q = q.filter(
            or_(
                Alert.message.ilike(term),
                Alert.code.ilike(term),
                PublicWork.object_description.ilike(term),
                func.unaccent(PublicWork.municipio).ilike(f"%{normalized_search}%"),
                PublicWork.contractor_name.ilike(term),
            )
        )

    # Ordena por severidade (critical primeiro) e data de criação
    q = q.order_by(
        Alert.severity.desc(),  # critical > alert > warning > info
        Alert.created_at.desc(),
    )

    alerts = q.all()
    return [_alert_to_read(a) for a in alerts]


def update_alert_status(db: Session, alert_id: int, new_status: str) -> AlertRead | None:
    """
    Atualiza o status de um alerta.
    Retorna o AlertRead atualizado ou None se o alerta não existir.
    """
    if new_status not in ALLOWED_ALERT_STATUSES:
        raise ValueError(f"Status inválido: {new_status}. Permitidos: {ALLOWED_ALERT_STATUSES}")

    alert = db.query(Alert).options(joinedload(Alert.work)).filter(Alert.id == alert_id).first()
    if not alert:
        return None

    alert.status = new_status
    db.commit()
    db.refresh(alert)
    return _alert_to_read(alert)
