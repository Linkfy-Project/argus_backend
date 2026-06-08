"""
Serviço de obras públicas do ARGUS.

Responsável por CRUD de obras, filtros, paginação e recálculo de scores.
Inclui normalização de nomes de municípios nos filtros para garantir
que buscas como "macae" encontrem tanto "Macae" quanto "Macaé".
"""

from __future__ import annotations
from datetime import date, datetime
import logging
import unicodedata

# Logger para debug/info ao invés de print()
logger = logging.getLogger(__name__)
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_
from app.models.work import PublicWork, Alert
from app.schemas.work import WorkCreate
from app.services.scoring import calculate_score, delay_days, calculate_contractor_crea_totals
from app.services.ml_service import predict_risks
from app.utils.obra_filter import filter_obras_query


def _normalize_municipio_for_filter(raw: str) -> str:
    """
    Normaliza o termo de busca de município para comparação case-insensitive.

    Remove acentos do termo de busca para que "macae" encontre "Macaé" e vice-versa.
    Usado nos filtros ilike do banco de dados.

    Args:
        raw: Termo de busca informado pelo usuário.

    Returns:
        Termo normalizado sem acentos e em lowercase.
    """
    if not raw:
        return raw
    # Remove acentos para comparação
    cleaned = unicodedata.normalize("NFD", raw)
    cleaned = "".join(c for c in cleaned if unicodedata.category(c) != "Mn")
    return cleaned.strip().lower()


def list_works(
    db: Session,
    municipio: str | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
    status: str | None = None,
    search: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    has_score: bool | None = None,
    signed_from: str | None = None,
    signed_to: str | None = None,
    page: int = 1,
    per_page: int = 25,
):
    """Retorna (items, total) com paginação e filtros no banco de dados."""
    q = db.query(PublicWork).options(joinedload(PublicWork.alerts))

    # ── Filtro de obras (exclui registros classificados como não-obra) ──
    q = filter_obras_query(q)

    # ── Filtros ──────────────────────────────────────────────
    if municipio:
        # Normaliza o termo de busca para incluir variações com/sem acento.
        # Usa a função SQL unaccent() (registrada no SQLite) para remover
        # acentos de AMBOS os lados da comparação.
        # Exemplo: busca "macae" encontra "Macaé", "Macae", "macae", etc.
        normalized = _normalize_municipio_for_filter(municipio)
        logger.debug("list_works - filtro municipio='%s' normalizado='%s'", municipio, normalized)
        q = q.filter(func.unaccent(PublicWork.municipio).ilike(f"%{normalized}%"))

    if min_score is not None:
        q = q.filter(PublicWork.efficiency_score >= min_score)
    if max_score is not None:
        q = q.filter(PublicWork.efficiency_score <= max_score)

    if has_score is False:
        q = q.filter(PublicWork.efficiency_score.is_(None))
    elif has_score is True:
        q = q.filter(PublicWork.efficiency_score.isnot(None))

    if search:
        term = f"%{search}%"
        normalized_search = _normalize_municipio_for_filter(search)
        q = q.filter(
            or_(
                PublicWork.object_description.ilike(term),
                func.unaccent(PublicWork.municipio).ilike(f"%{normalized_search}%"),
                PublicWork.contractor_name.ilike(term),
            )
        )

    if min_value is not None:
        q = q.filter(PublicWork.contract_value >= min_value)
    if max_value is not None:
        q = q.filter(PublicWork.contract_value <= max_value)

    # ── Filtros temporais ─────────────────────────────────────
    if signed_from:
        try:
            from_date = datetime.strptime(signed_from, "%Y-%m-%d").date()
            q = q.filter(PublicWork.signed_at >= from_date)
        except ValueError:
            pass
    if signed_to:
        try:
            to_date = datetime.strptime(signed_to, "%Y-%m-%d").date()
            q = q.filter(PublicWork.signed_at <= to_date)
        except ValueError:
            pass

    if status:
        today = date.today()
        if status == "Concluída":
            q = q.filter(PublicWork.finished_at.isnot(None))
        elif status == "Atrasada":
            q = q.filter(PublicWork.finished_at.is_(None), PublicWork.due_at.isnot(None), PublicWork.due_at < today)
        elif status == "Em andamento":
            q = q.filter(
                PublicWork.finished_at.is_(None),
                PublicWork.signed_at.isnot(None),
                or_(PublicWork.due_at.is_(None), PublicWork.due_at >= today),
            )
        elif status == "Planejada":
            q = q.filter(PublicWork.finished_at.is_(None), PublicWork.signed_at.is_(None))
        elif status == "Paralisada":
            q = q.filter(PublicWork.status.ilike("%paralis%"))

    # ── Contagem total (antes do offset/limit) ───────────────
    total = q.count()

    # ── Paginação ────────────────────────────────────────────
    offset = (page - 1) * per_page
    items = (
        q.order_by(PublicWork.efficiency_score.asc().nullslast(), PublicWork.id.desc())
        .offset(offset)
        .limit(per_page)
        .all()
    )
    return items, total


def get_work(db: Session, work_id: int):
    """Retorna uma obra específica, excluindo registros classificados como não-obra."""
    q = db.query(PublicWork).options(joinedload(PublicWork.alerts)).filter(PublicWork.id == work_id)
    q = filter_obras_query(q)
    return q.first()


def create_work(db: Session, payload: WorkCreate):
    work = PublicWork(**payload.model_dump())
    db.add(work)
    db.commit()
    db.refresh(work)
    recompute_work(db, work.id)
    return get_work(db, work.id)


def contractor_recurrence(db: Session, work: PublicWork) -> int:
    if not work.contractor_document:
        return 1
    return db.query(func.count(PublicWork.id)).filter(PublicWork.contractor_document == work.contractor_document).scalar() or 1


def recompute_work(db: Session, work_id: int):
    """
    Recalcula scores e alertas de uma única obra.

    Fluxo: 1) prediz riscos ML → 2) calcula score rule-based com ML integrado → 3) salva.
    """
    work = db.query(PublicWork).filter(PublicWork.id == work_id).first()
    if not work:
        return None
    recurrence = contractor_recurrence(db, work)

    # 0. Pré-computar totais CREA do contratado para detecção de padrões suspeitos
    contractor_work_count, contractor_crea_total = calculate_contractor_crea_totals(db, work)

    # 1. Predizer riscos ML ANTES do score (pois o score agora usa as probabilidades)
    risks = predict_risks({
        "contract_value": work.contract_value,
        "committed_value": work.committed_value,
        "settled_value": work.settled_value,
        "additive_value": work.additive_value,
        "area_m2": work.area_m2,
        "delay_days": delay_days(work),
        "contractor_recurrence": recurrence,
        "idh": work.idh,
    })
    work.risk_delay_probability = risks["delay_probability"]
    work.risk_cost_probability = risks["cost_overrun_probability"]
    work.risk_rework_probability = risks["rework_probability"]

    # 2. Calcular score rule-based com probabilidades ML integradas + CREA patterns
    score = calculate_score(
        work,
        contractor_recurrence=recurrence,
        benchmark_cost_m2=work.benchmark_cost_m2,
        risk_delay_probability=risks["delay_probability"],
        risk_cost_probability=risks["cost_overrun_probability"],
        risk_rework_probability=risks["rework_probability"],
        contractor_work_count=contractor_work_count,
        contractor_crea_total=contractor_crea_total,
    )
    work.cost_score = score.cost_score
    work.deadline_score = score.deadline_score
    work.quality_score = score.quality_score
    work.recurrence_score = score.recurrence_score
    work.social_impact_score = score.social_impact_score
    work.efficiency_score = score.efficiency_score

    # 3. Atualizar alertas
    db.query(Alert).filter(Alert.work_id == work.id).delete()
    for alert in score.alerts:
        db.add(
            Alert(
                work_id=work.id,
                code=alert["code"],
                severity=alert["severity"],
                severity_weight=float(alert.get("severity_weight", 0.0)),
                severity_multiplier=float(alert.get("severity_multiplier", 1.0)),
                weighted_severity=float(alert.get("weighted_severity", 0.0)),
                message=alert["message"],
            )
        )
    db.commit()
    db.refresh(work)
    return work


def recompute_many(
    db: Session,
    work_ids: list[int],
) -> dict:
    """
    Recalcula scores de MULTIPLAS obras em batch otimizado.

    Otimizações:
    1. Modelo ML carregado 1x (cache global) em vez de 1x por obra.
    2. Recurrências pré-computadas com GROUP BY em vez de COUNT individual.
    3. DELETE de alerts em massa com IN (...).
    4. INSERT de alerts em massa com bulk_insert_mappings.
    5. Commit único no final.

    Args:
        db: Sessão do banco.
        work_ids: Lista de IDs das obras para recalcular.

    Retorna:
        dict com total de obras processadas.
    """
    from app.services.ml_service import get_cached_model, predict_risks_batch

    total = len(work_ids)
    if total == 0:
        return {"updated": 0}

    # 1. Carregar modelo ML 1x (cache global)
    logger.debug("[RECOMPUTE_BATCH] Carregando modelo ML (cache)...")
    ml_models = get_cached_model()

    # 2. Buscar todas as obras de uma vez
    logger.debug("[RECOMPUTE_BATCH] Buscando %d obras no banco...", total)
    works = (
        db.query(PublicWork)
        .filter(PublicWork.id.in_(work_ids))
        .all()
    )
    works_map = {w.id: w for w in works}
    logger.debug("[RECOMPUTE_BATCH] Encontradas %d obras.", len(works))

    # 3. Pré-computar recurrências (1 query com GROUP BY)
    logger.debug("[RECOMPUTE_BATCH] Pré-computando recurrências...")
    docs = [w.contractor_document for w in works if w.contractor_document]
    recurrence_map: dict[str, int] = {}
    if docs:
        rows = (
            db.query(PublicWork.contractor_document, func.count(PublicWork.id))
            .filter(PublicWork.contractor_document.in_(docs))
            .group_by(PublicWork.contractor_document)
            .all()
        )
        recurrence_map = {doc: count for doc, count in rows}

    def _get_recurrence(work: PublicWork) -> int:
        if not work.contractor_document:
            return 1
        return recurrence_map.get(work.contractor_document, 1)

    # 4. Montar features para batch predict
    logger.debug("[RECOMPUTE_BATCH] Montando features para ML...")
    features_list = []
    works_to_update = []

    for work in works:
        recurrence = _get_recurrence(work)
        works_to_update.append(work)

        features_list.append({
            "contract_value": work.contract_value,
            "committed_value": work.committed_value,
            "settled_value": work.settled_value,
            "additive_value": work.additive_value,
            "area_m2": work.area_m2,
            "delay_days": delay_days(work),
            "contractor_recurrence": recurrence,
            "idh": work.idh,
        })

    # 5. Predizer riscos em batch PRIMEIRO (1 chamada numpy)
    logger.debug("[RECOMPUTE_BATCH] Predizendo riscos em batch (%d obras)...", len(features_list))
    risk_results = predict_risks_batch(features_list, models=ml_models)

    # 6. Calcular scores rule-based COM probabilidades ML integradas
    logger.debug("[RECOMPUTE_BATCH] Calculando scores com ML integrado...")
    score_results = []
    for work, risks in zip(works_to_update, risk_results):
        recurrence = _get_recurrence(work)
        score = calculate_score(
            work,
            contractor_recurrence=recurrence,
            benchmark_cost_m2=work.benchmark_cost_m2,
            risk_delay_probability=risks["delay_probability"],
            risk_cost_probability=risks["cost_overrun_probability"],
            risk_rework_probability=risks["rework_probability"],
        )
        score_results.append(score)

    # 7. Atualizar campos das obras em memória
    for work, score, risks in zip(works_to_update, score_results, risk_results):
        work.cost_score = score.cost_score
        work.deadline_score = score.deadline_score
        work.quality_score = score.quality_score
        work.recurrence_score = score.recurrence_score
        work.social_impact_score = score.social_impact_score
        work.efficiency_score = score.efficiency_score
        work.risk_delay_probability = risks["delay_probability"]
        work.risk_cost_probability = risks["cost_overrun_probability"]
        work.risk_rework_probability = risks["rework_probability"]

    # 7. Batch DELETE de alerts existentes
    logger.debug("[RECOMPUTE_BATCH] Removendo alerts antigos em batch...")
    db.query(Alert).filter(Alert.work_id.in_(work_ids)).delete(synchronize_session="fetch")

    # 8. Batch INSERT de novos alerts
    logger.debug("[RECOMPUTE_BATCH] Inserindo novos alerts em batch...")
    new_alerts = []
    for work, score in zip(works_to_update, score_results):
        for alert in score.alerts:
            new_alerts.append({
                "work_id": work.id,
                "code": alert["code"],
                "severity": alert["severity"],
                "severity_weight": float(alert.get("severity_weight", 0.0)),
                "severity_multiplier": float(alert.get("severity_multiplier", 1.0)),
                "weighted_severity": float(alert.get("weighted_severity", 0.0)),
                "message": alert["message"],
            })

    if new_alerts:
        db.bulk_insert_mappings(Alert, new_alerts)

    # 9. Commit ÚNICO
    db.commit()

    logger.debug("[RECOMPUTE_BATCH] Batch concluído: %d obras processadas em 1 commit.", total)
    return {"updated": total}


def recompute_all(db: Session):
    ids = [row[0] for row in db.query(PublicWork.id).all()]
    return recompute_many(db, ids)


def explain_score(db: Session, work_id: int) -> dict | None:
    """Retorna o detalhamento completo do score de uma obra, incluindo ML."""
    work = db.query(PublicWork).filter(PublicWork.id == work_id).first()
    if not work:
        return None
    recurrence = contractor_recurrence(db, work)
    # Usa as probabilidades ML já salvas no banco (se existirem)
    return calculate_score(
        work,
        contractor_recurrence=recurrence,
        risk_delay_probability=work.risk_delay_probability,
        risk_cost_probability=work.risk_cost_probability,
        risk_rework_probability=work.risk_rework_probability,
    ).as_dict()
