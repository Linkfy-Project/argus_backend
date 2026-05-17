from fastapi import APIRouter
from app.schemas.work import PredictionInput, PredictionOutput
from app.services.ml_service import predict_risks, train_baseline_model

router = APIRouter(prefix="/ml", tags=["machine-learning"])

@router.post("/predict", response_model=PredictionOutput)
def predict(payload: PredictionInput):
    return predict_risks(payload.model_dump())

@router.post("/train-baseline")
def train_baseline():
    train_baseline_model()
    return {"status": "trained", "model_version": "baseline-synthetic-v1"}
