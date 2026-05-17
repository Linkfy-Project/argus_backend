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
