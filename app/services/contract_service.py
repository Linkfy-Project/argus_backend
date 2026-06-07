"""
Serviço de contratos do ARGUS.

Responsável por listar, filtrar e detalhar contratos.
Cada PublicWork representa um contrato na base de dados do ARGUS.
O serviço deriva campos de contrato a partir dos dados da obra,
incluindo status calculado, dias para vencimento e classificação de risco.
"""

from __future__ import annotations

import logging
import unicodedata
from datetime import date, datetime

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_

from app.models.work import Alert, PublicWork
from app.schemas.contract import ContractRead, ContractDetailRead

logger = logging.getLogger(__name__)


def _normalize_municipio(raw: str) -> str:
    """Normaliza o termo de busca de município removendo acentos."""
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


def _safe_number(value, default: float = 0.0) -> float:
    """Retorna valor numérico seguro, substituindo None por default."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _calc_status(work: PublicWork) -> str:
    """
    Calcula o status do contrato baseado nas datas da obra.
    Regras:
    - Concluída: finished_at preenchido
    - Vencida: due_at < hoje e não concluída
    - Vigente: signed_at preenchido, não concluída, não vencida
    - Planejada: sem signed_at e não concluída
    """
    today = date.today()
    if work.finished_at:
        return "Concluída"
    if work.due_at and work.due_at < today:
        return "Vencida"
    if work.signed_at:
        return "Vigente"
    return "Planejada"


def _calc_dias_vencimento(work: PublicWork) -> int | None:
    """Calcula dias restantes até o vencimento do contrato."""
    if not work.due_at:
        return None
    today = date.today()
    delta = (work.due_at - today).days
    return delta


def _calc_percentual_aditivo(work: PublicWork) -> float | None:
    """Calcula percentual de aditivo em relação ao valor original."""
    contract_val = _safe_number(work.contract_value)
    additive_val = _safe_number(work.additive_value)
    if contract_val <= 0:
        return None
    return round((additive_val / contract_val) * 100, 1)


def _classificacao_risco(score: float | None) -> str:
    """Classifica o risco baseado no score ARGUS."""
    if score is None:
        return "Não avaliado"
    if score >= 80:
        return "Baixo risco"
    if score >= 60:
        return "Atenção"
    if score >= 40:
        return "Alto risco"
    return "Crítico"


def _acao_sugerida(work: PublicWork, score: float | None) -> str:
    """Gera ação sugerida baseada no estado do contrato."""
    status = _calc_status(work)
    percentual = _calc_percentual_aditivo(work)

    if status == "Vencida":
        return "Solicitar replanejamento e justificativa para o atraso."
    if percentual and percentual >= 25:
        return "Revisar aditivos e verificar fundamentação legal."
    if score is not None and score < 40:
        return "Reforçar fiscalização e priorizar auditoria."
    if score is not None and score < 60:
        return "Acompanhar de perto e solicitar relatórios periódicos."
    return "Monitoramento de rotina."


def _work_to_contract_read(work: PublicWork, alert_count: int) -> ContractRead:
    """
    Converte um modelo PublicWork em ContractRead para a API.
    Deriva campos como status, dias_para_vencimento, classificação de risco.
    """
    score = work.efficiency_score
    return ContractRead(
        id=f"work-{work.id}",
        work_id=work.id,
        numero_contrato=work.contract_number or None,
        objeto=work.object_description or None,
        obra_nome=work.object_description or None,
        municipio=work.municipio or None,
        bairro=work.neighborhood or None,
        fornecedor=_normalize_fornecedor(work.contractor_name),
        cnpj_fornecedor=work.contractor_document or None,
        secretaria=work.managing_unit or None,
        valor_original=_safe_number(work.contract_value) or None,
        valor_atual=_safe_number(work.contract_value) + _safe_number(work.additive_value) or None,
        valor_pago=_safe_number(work.paid_value) or None,
        percentual_aditivo=_calc_percentual_aditivo(work),
        data_inicio=work.signed_at,
        data_fim=work.due_at,
        dias_para_vencimento=_calc_dias_vencimento(work),
        status=_calc_status(work),
        score_argus=round(score, 1) if score is not None else None,
        classificacao_risco=_classificacao_risco(score),
        alertas=alert_count,
        acao_sugerida=_acao_sugerida(work, score),
    )


def list_contracts(
    db: Session,
    municipio: str | None = None,
    fornecedor: str | None = None,
    secretaria: str | None = None,
    bairro: str | None = None,
    status: str | None = None,
    risco: str | None = None,
    com_aditivo: bool | None = None,
    vencendo: bool | None = None,
    vencido: bool | None = None,
    search: str | None = None,
) -> list[ContractRead]:
    """
    Lista contratos (derivados de PublicWork) com filtros opcionais.
    Retorna lista de ContractRead enriquecidos com dados calculados.
    """
    q = db.query(PublicWork).options(joinedload(PublicWork.alerts))

    today = date.today()

    # ── Filtros ──────────────────────────────────────────────
    if municipio:
        normalized = _normalize_municipio(municipio)
        logger.debug("list_contracts - filtro municipio='%s' normalizado='%s'", municipio, normalized)
        q = q.filter(func.unaccent(PublicWork.municipio).ilike(f"%{normalized}%"))

    if fornecedor:
        normalized_f = _normalize_municipio(fornecedor)
        q = q.filter(func.unaccent(PublicWork.contractor_name).ilike(f"%{normalized_f}%"))

    if secretaria:
        q = q.filter(PublicWork.managing_unit.ilike(f"%{secretaria}%"))

    if bairro:
        q = q.filter(PublicWork.neighborhood.ilike(f"%{bairro}%"))

    if status:
        if status == "Concluída":
            q = q.filter(PublicWork.finished_at.isnot(None))
        elif status == "Vencida":
            q = q.filter(
                PublicWork.finished_at.is_(None),
                PublicWork.due_at.isnot(None),
                PublicWork.due_at < today,
            )
        elif status == "Vigente":
            q = q.filter(
                PublicWork.finished_at.is_(None),
                PublicWork.signed_at.isnot(None),
                or_(PublicWork.due_at.is_(None), PublicWork.due_at >= today),
            )
        elif status == "Planejada":
            q = q.filter(PublicWork.finished_at.is_(None), PublicWork.signed_at.is_(None))

    if risco:
        if risco.lower() in ["critico", "crítico"]:
            q = q.filter(PublicWork.efficiency_score.isnot(None), PublicWork.efficiency_score < 40)
        elif risco.lower() in ["alto", "alto risco"]:
            q = q.filter(PublicWork.efficiency_score.isnot(None), PublicWork.efficiency_score >= 40, PublicWork.efficiency_score < 60)
        elif risco.lower() in ["atenção", "atencao"]:
            q = q.filter(PublicWork.efficiency_score.isnot(None), PublicWork.efficiency_score >= 60, PublicWork.efficiency_score < 80)
        elif risco.lower() in ["baixo", "baixo risco"]:
            q = q.filter(PublicWork.efficiency_score.isnot(None), PublicWork.efficiency_score >= 80)

    if com_aditivo is True:
        q = q.filter(PublicWork.additive_value.isnot(None), PublicWork.additive_value > 0)
    elif com_aditivo is False:
        q = q.filter(or_(PublicWork.additive_value.is_(None), PublicWork.additive_value == 0))

    if vencendo is True:
        # Contratos vencendo nos próximos 30 dias
        from datetime import timedelta
        limite = today + timedelta(days=30)
        q = q.filter(
            PublicWork.finished_at.is_(None),
            PublicWork.due_at.isnot(None),
            PublicWork.due_at >= today,
            PublicWork.due_at <= limite,
        )

    if vencido is True:
        q = q.filter(
            PublicWork.finished_at.is_(None),
            PublicWork.due_at.isnot(None),
            PublicWork.due_at < today,
        )

    if search:
        term = f"%{search}%"
        normalized_search = _normalize_municipio(search)
        q = q.filter(
            or_(
                PublicWork.object_description.ilike(term),
                PublicWork.contract_number.ilike(term),
                func.unaccent(PublicWork.municipio).ilike(f"%{normalized_search}%"),
                PublicWork.contractor_name.ilike(term),
                PublicWork.managing_unit.ilike(term),
            )
        )

    # Ordena por score (pior primeiro)
    q = q.order_by(PublicWork.efficiency_score.asc().nullslast(), PublicWork.id.desc())

    works = q.all()

    # Conta alertas por obra
    work_ids = [w.id for w in works]
    alert_counts: dict[int, int] = {}
    if work_ids:
        rows = (
            db.query(Alert.work_id, func.count(Alert.id))
            .filter(Alert.work_id.in_(work_ids))
            .group_by(Alert.work_id)
            .all()
        )
        alert_counts = {wid: count for wid, count in rows}

    return [_work_to_contract_read(w, alert_counts.get(w.id, 0)) for w in works]


def get_contract(db: Session, contract_id: str | int) -> ContractDetailRead | None:
    """
    Busca um contrato por ID.
    Aceita tanto ID numérico da obra quanto formato 'work-123'.
    """
    # Parse do ID
    if isinstance(contract_id, str) and contract_id.startswith("work-"):
        try:
            work_id = int(contract_id.replace("work-", ""))
        except ValueError:
            return None
    elif isinstance(contract_id, int):
        work_id = contract_id
    else:
        try:
            work_id = int(contract_id)
        except (ValueError, TypeError):
            return None

    work = (
        db.query(PublicWork)
        .options(joinedload(PublicWork.alerts))
        .filter(PublicWork.id == work_id)
        .first()
    )
    if not work:
        return None

    # Conta alertas
    alert_count = len(work.alerts) if work.alerts else 0
    base = _work_to_contract_read(work, alert_count)

    # Monta detalhes dos alertas
    alertas_detalhes = []
    for a in (work.alerts or []):
        alertas_detalhes.append({
            "id": a.id,
            "code": a.code,
            "severity": a.severity,
            "message": a.message,
            "status": a.status or "Novo",
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })

    return ContractDetailRead(
        **base.model_dump(),
        created_at=work.created_at,
        updated_at=work.updated_at,
        alertas_detalhes=alertas_detalhes,
    )
