from __future__ import annotations
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from app.models.work import PublicWork, Alert
from app.schemas.work import WorkCreate
from app.services.scoring import calculate_score, delay_days
from app.services.ml_service import predict_risks


def list_works(db: Session, municipio: str | None = None, min_score: float | None = None, max_score: float | None = None, limit: int = 100, offset: int = 0):
    q = db.query(PublicWork).options(joinedload(PublicWork.alerts))
    if municipio:
        q = q.filter(PublicWork.municipio.ilike(f"%{municipio}%"))
    if min_score is not None:
        q = q.filter(PublicWork.efficiency_score >= min_score)
    if max_score is not None:
        q = q.filter(PublicWork.efficiency_score <= max_score)
    return q.order_by(PublicWork.efficiency_score.asc().nullslast(), PublicWork.id.desc()).offset(offset).limit(limit).all()


def get_work(db: Session, work_id: int):
    return db.query(PublicWork).options(joinedload(PublicWork.alerts)).filter(PublicWork.id == work_id).first()


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
    work = db.query(PublicWork).filter(PublicWork.id == work_id).first()
    if not work:
        return None
    recurrence = contractor_recurrence(db, work)
    score = calculate_score(work, contractor_recurrence=recurrence)
    work.cost_score = score.cost_score
    work.deadline_score = score.deadline_score
    work.quality_score = score.quality_score
    work.recurrence_score = score.recurrence_score
    work.social_impact_score = score.social_impact_score
    work.efficiency_score = score.efficiency_score

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
    print(f"DEBUG: [RECOMPUTE_BATCH] Carregando modelo ML (cache)...")
    ml_models = get_cached_model()

    # 2. Buscar todas as obras de uma vez
    print(f"DEBUG: [RECOMPUTE_BATCH] Buscando {total} obras no banco...")
    works = (
        db.query(PublicWork)
        .filter(PublicWork.id.in_(work_ids))
        .all()
    )
    works_map = {w.id: w for w in works}
    print(f"DEBUG: [RECOMPUTE_BATCH] Encontradas {len(works)} obras.")

    # 3. Pré-computar recurrências (1 query com GROUP BY)
    print(f"DEBUG: [RECOMPUTE_BATCH] Pré-computando recurrências...")
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

    # 4. Montar features para batch predict + calcular scores
    print(f"DEBUG: [RECOMPUTE_BATCH] Calculando scores e montando features...")
    features_list = []
    score_results = []
    works_to_update = []

    for work in works:
        recurrence = _get_recurrence(work)
        score = calculate_score(work, contractor_recurrence=recurrence)
        score_results.append(score)
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

    # 5. Predizer riscos em batch (1 chamada numpy)
    print(f"DEBUG: [RECOMPUTE_BATCH] Predizendo riscos em batch ({len(features_list)} obras)...")
    risk_results = predict_risks_batch(features_list, models=ml_models)

    # 6. Atualizar campos das obras em memória
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
    print(f"DEBUG: [RECOMPUTE_BATCH] Removendo alerts antigos em batch...")
    db.query(Alert).filter(Alert.work_id.in_(work_ids)).delete(synchronize_session="fetch")

    # 8. Batch INSERT de novos alerts
    print(f"DEBUG: [RECOMPUTE_BATCH] Inserindo novos alerts em batch...")
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

    print(f"DEBUG: [RECOMPUTE_BATCH] Batch concluído: {total} obras processadas em 1 commit.")
    return {"updated": total}


def recompute_all(db: Session):
    ids = [row[0] for row in db.query(PublicWork.id).all()]
    return recompute_many(db, ids)


def explain_score(db: Session, work_id: int) -> dict | None:
    work = db.query(PublicWork).filter(PublicWork.id == work_id).first()
    if not work:
        return None
    recurrence = contractor_recurrence(db, work)
    return calculate_score(work, contractor_recurrence=recurrence).as_dict()
