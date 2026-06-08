from io import StringIO, BytesIO
import pandas as pd
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.work import PublicWork
from app.utils.obra_filter import filter_obras_query

router = APIRouter(prefix="/exports", tags=["exports"])

FIELDS = ["id", "municipio", "object_description", "contractor_name", "contract_value", "settled_value", "efficiency_score", "risk_delay_probability", "risk_cost_probability", "risk_rework_probability"]

@router.get("/works.csv")
def works_csv(db: Session = Depends(get_db)):
    q = db.query(PublicWork)
    q = filter_obras_query(q)
    rows = q.all()
    df = pd.DataFrame([{field: getattr(w, field) for field in FIELDS} for w in rows])
    buffer = StringIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)
    return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=argus_obras.csv"})

@router.get("/works.xlsx")
def works_xlsx(db: Session = Depends(get_db)):
    q = db.query(PublicWork)
    q = filter_obras_query(q)
    rows = q.all()
    df = pd.DataFrame([{field: getattr(w, field) for field in FIELDS} for w in rows])
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="obras")
    output.seek(0)
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=argus_obras.xlsx"})
