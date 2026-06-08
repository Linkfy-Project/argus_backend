from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.schemas.work import PredictionInput, PredictionOutput
from app.services.ml_service import predict_risks, train_baseline_model, FEATURES, MODEL_PATH, invalidate_model_cache
from app.db.session import SessionLocal

router = APIRouter(prefix="/ml", tags=["machine-learning"])


def get_db():
    """Dependency que fornece uma sessão do banco de dados."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/predict", response_model=PredictionOutput)
def predict(payload: PredictionInput):
    return predict_risks(payload.model_dump())


@router.post("/train-baseline")
def train_baseline():
    train_baseline_model()
    return {"status": "trained", "model_version": "baseline-synthetic-v1"}


@router.post("/retrain-real")
def retrain_with_real_data(db: Session = Depends(get_db)):
    """
    Retreina o modelo ML usando scores rule-based como labels (knowledge distillation).

    Para cada obra com efficiency_score calculado, gera labels binários:
    - y_delay = 1 se deadline_score < 50
    - y_cost = 1 se cost_score < 50
    - y_rework = 1 se quality_score < 50 OU recurrence_score < 50

    Requer mínimo de 50 obras com scores. Se insuficiente, retorna status de erro.
    """
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import cross_val_score
    import joblib
    from app.models.work import PublicWork
    from app.services.scoring import delay_days, calculate_contractor_crea_totals
    from app.utils.obra_filter import filter_obras_query

    MIN_SAMPLES = 50

    # Carrega obras com score calculado
    works = (
        filter_obras_query(
            db.query(PublicWork)
            .filter(PublicWork.efficiency_score.isnot(None))
        )
        .all()
    )

    if len(works) < MIN_SAMPLES:
        return {
            "status": "insufficient_data",
            "samples": len(works),
            "minimum": MIN_SAMPLES,
        }

    # Constrói features e labels
    features = []
    y_delay = []
    y_cost = []
    y_rework = []

    for w in works:
        features.append([
            float(w.contract_value or 0),
            float(w.committed_value or 0),
            float(w.settled_value or 0),
            float(w.additive_value or 0),
            float(w.area_m2 or 0),
            float(delay_days(w)),
            float(calculate_contractor_crea_totals(db, w)[0]),
            float(w.idh or 0),
        ])
        y_delay.append(1 if w.deadline_score is not None and w.deadline_score < 50 else 0)
        y_cost.append(1 if w.cost_score is not None and w.cost_score < 50 else 0)
        y_rework.append(
            1
            if (w.quality_score is not None and w.quality_score < 50)
            or (w.recurrence_score is not None and w.recurrence_score < 50)
            else 0
        )

    X = np.array(features, dtype=np.float64)
    y_delay_arr = np.array(y_delay, dtype=np.int32)
    y_cost_arr = np.array(y_cost, dtype=np.int32)
    y_rework_arr = np.array(y_rework, dtype=np.int32)

    # Treina os modelos
    n_samples = X.shape[0]
    cv_folds = min(5, n_samples)

    models = {
        "delay": RandomForestClassifier(n_estimators=100, random_state=42).fit(X, y_delay_arr),
        "cost": RandomForestClassifier(n_estimators=100, random_state=43).fit(X, y_cost_arr),
        "rework": RandomForestClassifier(n_estimators=100, random_state=44).fit(X, y_rework_arr),
        "features": FEATURES,
        "version": f"real-data-v1-{n_samples}samples",
    }

    # Cross-validation para reportar qualidade
    cv_results = {}
    for name, y in [("delay", y_delay_arr), ("cost", y_cost_arr), ("rework", y_rework_arr)]:
        unique_classes = len(np.unique(y))
        if unique_classes < 2:
            cv_results[name] = {"f1_mean": None, "f1_std": None, "note": "apenas 1 classe"}
            continue
        try:
            scores = cross_val_score(models[name], X, y, cv=cv_folds, scoring="f1")
            cv_results[name] = {
                "f1_mean": round(float(scores.mean()), 3),
                "f1_std": round(float(scores.std()), 3),
            }
        except Exception as e:
            cv_results[name] = {"f1_mean": None, "f1_std": None, "error": str(e)}

    # Salva e invalida cache
    joblib.dump(models, MODEL_PATH)
    invalidate_model_cache()

    version = models.get("version", "unknown")
    return {
        "status": "trained",
        "samples": n_samples,
        "version": version,
        "cross_validation": cv_results,
        "positive_rates": {
            "delay": round(float(np.mean(y_delay_arr)), 3),
            "cost": round(float(np.mean(y_cost_arr)), 3),
            "rework": round(float(np.mean(y_rework_arr)), 3),
        },
    }
