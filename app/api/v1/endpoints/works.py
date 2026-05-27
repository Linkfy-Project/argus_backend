from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.work import WorkCreate, WorkRead
from app.services.work_service import create_work, explain_score, get_work, list_works, recompute_all, recompute_work
from app.services.scoring import WEIGHTS, CREA_PENALTIES, CRITICAL_IDH_THRESHOLD, CRITICAL_IDH_MULTIPLIER

router = APIRouter(prefix="/works", tags=["works"])


@router.get("/scoring/rules")
def scoring_rules():
    return {
        "weights": WEIGHTS,
        "formulas": {
            "cost": "max(0, 100 - ((Custo Real - Custo Referencia) / Custo Referencia) * 100)",
            "deadline": "max(0, 100 - (Dias de Atraso / 90) * 100)",
            "quality": "max(0, 100 - ((Variacao de Aditivos % / 25) * 100) - Soma das Penalidades CREA)",
            "recurrence": "100 - percentual_de_area_sobreposta por Convex Hull em janela inferior a 24 meses; fallback por CNPJ quando geometria indisponivel",
            "social_impact": "(1 - IDH Local) * 100",
            "final_score": "sum(Nota da Dimensao * Peso da Dimensao)",
        },
        "crea_penalties": CREA_PENALTIES,
        "criticality_multiplier": {
            "idh_below": CRITICAL_IDH_THRESHOLD,
            "multiplier": CRITICAL_IDH_MULTIPLIER,
            "applies_to": "severity_weight dos alertas WARNING, ALERT e CRITICAL",
        },
    }

@router.get("", response_model=list[WorkRead])
def index(municipio: str | None = None, min_score: float | None = None, max_score: float | None = None, limit: int = Query(100, le=500), offset: int = 0, db: Session = Depends(get_db)):
    return list_works(db, municipio=municipio, min_score=min_score, max_score=max_score, limit=limit, offset=offset)

@router.post("", response_model=WorkRead)
def create(payload: WorkCreate, db: Session = Depends(get_db)):
    return create_work(db, payload)

@router.get("/{work_id}", response_model=WorkRead)
def show(work_id: int, db: Session = Depends(get_db)):
    work = get_work(db, work_id)
    if not work:
        raise HTTPException(status_code=404, detail="Obra não encontrada")
    return work

@router.get("/{work_id}/score-explain")
def score_explain(work_id: int, db: Session = Depends(get_db)):
    result = explain_score(db, work_id)
    if not result:
        raise HTTPException(status_code=404, detail="Obra não encontrada")
    return result

@router.post("/{work_id}/recompute", response_model=WorkRead)
def recompute(work_id: int, db: Session = Depends(get_db)):
    work = recompute_work(db, work_id)
    if not work:
        raise HTTPException(status_code=404, detail="Obra não encontrada")
    return work

@router.post("/recompute-all")
def recompute_everything(db: Session = Depends(get_db)):
    return recompute_all(db)
