from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db.session import get_db
from app.models.work import PublicWork, Alert

router = APIRouter(prefix="/analytics", tags=["analytics"])

@router.get("/summary")
def summary(municipio: str | None = None, db: Session = Depends(get_db)):
    q = db.query(PublicWork)
    if municipio:
        q = q.filter(PublicWork.municipio.ilike(f"%{municipio}%"))
    total = q.count()
    avg_score = q.with_entities(func.avg(PublicWork.efficiency_score)).scalar()
    delayed = q.filter(PublicWork.deadline_score < 100).count()
    critical_alerts = db.query(Alert).filter(Alert.severity == "critical").count()
    return {
        "total_works": total,
        "average_efficiency_score": round(float(avg_score or 0), 2),
        "delayed_works": delayed,
        "critical_alerts": critical_alerts,
    }

@router.get("/rankings")
def rankings(limit: int = 10, db: Session = Depends(get_db)):
    worst = db.query(PublicWork).order_by(PublicWork.efficiency_score.asc().nullslast()).limit(limit).all()
    best = db.query(PublicWork).order_by(PublicWork.efficiency_score.desc().nullslast()).limit(limit).all()
    def pack(work: PublicWork):
        return {"id": work.id, "municipio": work.municipio, "object_description": work.object_description, "contractor_name": work.contractor_name, "efficiency_score": work.efficiency_score}
    return {"worst": [pack(w) for w in worst], "best": [pack(w) for w in best]}

@router.get("/map/geojson")
def geojson(db: Session = Depends(get_db)):
    works = db.query(PublicWork).filter(PublicWork.latitude.isnot(None), PublicWork.longitude.isnot(None)).all()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [w.longitude, w.latitude]},
                "properties": {"id": w.id, "municipio": w.municipio, "score": w.efficiency_score, "objeto": w.object_description, "contratado": w.contractor_name},
            }
            for w in works
        ],
    }

@router.get("/trends")
def trends(municipio: str | None = None, db: Session = Depends(get_db)):
    """
    Retorna evolução mensal dos scores para gráfico de tendência.
    Agrupa obras por mês de assinatura e calcula médias.
    """
    from sqlalchemy import extract
    from collections import defaultdict

    q = db.query(PublicWork)
    if municipio:
        q = q.filter(PublicWork.municipio.ilike(f"%{municipio}%"))

    works = q.filter(PublicWork.signed_at.isnot(None)).all()

    monthly = defaultdict(lambda: {"scores": [], "count": 0, "value": 0.0})
    for w in works:
        if w.signed_at:
            key = w.signed_at.strftime("%Y-%m")
            monthly[key]["count"] += 1
            monthly[key]["value"] += float(w.contract_value or 0)
            if w.efficiency_score is not None:
                monthly[key]["scores"].append(float(w.efficiency_score))

    result = []
    for month in sorted(monthly.keys()):
        data = monthly[month]
        avg = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0
        result.append({
            "month": month,
            "avg_score": round(avg, 2),
            "count": data["count"],
            "total_value": round(data["value"], 2),
        })

    return result


@router.get("/inter-municipal")
def inter_municipal(db: Session = Depends(get_db)):
    """Compara indicadores entre municípios para análise intermunicipal."""
    from sqlalchemy import func

    rows = (
        db.query(
            PublicWork.municipio,
            func.count(PublicWork.id).label("total"),
            func.avg(PublicWork.efficiency_score).label("avg_score"),
            func.sum(PublicWork.contract_value).label("total_value"),
            func.sum(PublicWork.risk_delay_probability).label("avg_delay_risk"),
        )
        .group_by(PublicWork.municipio)
        .all()
    )

    return [
        {
            "municipio": r.municipio,
            "total_works": r.total,
            "avg_score": round(float(r.avg_score or 0), 2),
            "total_value": round(float(r.total_value or 0), 2),
            "avg_delay_risk": round(float((r.avg_delay_risk or 0) / max(r.total, 1)), 4),
        }
        for r in rows
    ]
