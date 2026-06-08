import math
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.work import WorkCreate, WorkRead, PaginatedWorks
from app.services.work_service import create_work, explain_score, get_work, list_works, recompute_all, recompute_work
from app.services.scoring import WEIGHTS, CREA_PENALTIES, CRITICAL_IDH_THRESHOLD, CRITICAL_IDH_MULTIPLIER

router = APIRouter(prefix="/works", tags=["works"])


@router.get("/scoring/rules")
def scoring_rules():
    """Retorna as regras de scoring atualizadas com os 5 pilares + agravante social."""
    return {
        "weights": WEIGHTS,
        "formulas": {
            "cost": "max(0, 100 - ((Custo Real - Custo Referencia) / Custo Referencia) * 100)",
            "deadline": "max(0, 100 - (Dias de Atraso / 90) * 100)",
            "quality": "max(0, 100 - ((Variacao de Aditivos % / 25) * 100) - Soma das Penalidades CREA)",
            "recurrence": "100 - percentual_de_area_sobreposta por Convex Hull em janela inferior a 24 meses; fallback por CNPJ quando geometria indisponivel",
            "ml_risk": "100 - (media das probabilidades de risco * 100)",
            "final_score": "sum(Nota da Dimensao * Peso da Dimensao) — 5 pilares, IDH nao entra no somatorio",
        },
        "agravante_social": {
            "description": "O IDH (Impacto Socioeconomico) atua como multiplicador de criticidade, NAO como pilar do score base.",
            "idh_formula": "(1 - IDH Local) * 100",
            "threshold": CRITICAL_IDH_THRESHOLD,
            "multiplier": CRITICAL_IDH_MULTIPLIER,
            "applies_to": "severity_weight dos alertas WARNING, ALERT e CRITICAL quando IDH < 0.600",
        },
        "crea_penalties": CREA_PENALTIES,
        "criticality_multiplier": {
            "idh_below": CRITICAL_IDH_THRESHOLD,
            "multiplier": CRITICAL_IDH_MULTIPLIER,
            "applies_to": "severity_weight dos alertas WARNING, ALERT e CRITICAL",
        },
    }

@router.get("", response_model=PaginatedWorks)
def index(
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
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=10000),
    db: Session = Depends(get_db),
):
    items, total = list_works(
        db,
        municipio=municipio,
        min_score=min_score,
        max_score=max_score,
        status=status,
        search=search,
        min_value=min_value,
        max_value=max_value,
        has_score=has_score,
        signed_from=signed_from,
        signed_to=signed_to,
        page=page,
        per_page=per_page,
    )
    total_pages = max(1, math.ceil(total / per_page))
    return PaginatedWorks(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )

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
