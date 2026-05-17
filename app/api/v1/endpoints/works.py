from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.work import WorkCreate, WorkRead
from app.services.work_service import create_work, get_work, list_works, recompute_all, recompute_work

router = APIRouter(prefix="/works", tags=["works"])

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

@router.post("/{work_id}/recompute", response_model=WorkRead)
def recompute(work_id: int, db: Session = Depends(get_db)):
    work = recompute_work(db, work_id)
    if not work:
        raise HTTPException(status_code=404, detail="Obra não encontrada")
    return work

@router.post("/recompute-all")
def recompute_everything(db: Session = Depends(get_db)):
    return recompute_all(db)
