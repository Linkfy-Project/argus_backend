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


def recompute_all(db: Session):
    ids = [row[0] for row in db.query(PublicWork.id).all()]
    for work_id in ids:
        recompute_work(db, work_id)
    return {"updated": len(ids)}


def explain_score(db: Session, work_id: int) -> dict | None:
    work = db.query(PublicWork).filter(PublicWork.id == work_id).first()
    if not work:
        return None
    recurrence = contractor_recurrence(db, work)
    return calculate_score(work, contractor_recurrence=recurrence).as_dict()
