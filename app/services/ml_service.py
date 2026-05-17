from __future__ import annotations
from pathlib import Path
import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier

ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "ml" / "artifacts"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
MODEL_PATH = ARTIFACT_DIR / "argus_baseline_model.joblib"
FEATURES = [
    "contract_value", "committed_value", "settled_value", "additive_value",
    "area_m2", "delay_days", "contractor_recurrence", "idh",
]


def _baseline_dataset():
    # Dataset sintético inicial para PoC. Substituir por dados rotulados reais na Fase 2.
    rng = np.random.default_rng(42)
    X = []
    y_delay = []
    y_cost = []
    y_rework = []
    for _ in range(400):
        contract = rng.uniform(100_000, 30_000_000)
        committed = contract * rng.uniform(0.7, 1.3)
        settled = committed * rng.uniform(0.1, 1.1)
        additive = contract * rng.uniform(0, 0.35)
        area = rng.uniform(100, 20_000)
        delay = int(rng.integers(0, 240))
        recurrence = int(rng.integers(1, 8))
        idh = rng.uniform(0.45, 0.9)
        X.append([contract, committed, settled, additive, area, delay, recurrence, idh])
        y_delay.append(1 if delay > 45 or settled < committed * 0.45 else 0)
        y_cost.append(1 if additive / contract > 0.16 else 0)
        y_rework.append(1 if recurrence >= 4 or additive / contract > 0.25 else 0)
    return np.array(X), np.array(y_delay), np.array(y_cost), np.array(y_rework)


def train_baseline_model():
    X, yd, yc, yr = _baseline_dataset()
    models = {
        "delay": RandomForestClassifier(n_estimators=80, random_state=42).fit(X, yd),
        "cost": RandomForestClassifier(n_estimators=80, random_state=43).fit(X, yc),
        "rework": RandomForestClassifier(n_estimators=80, random_state=44).fit(X, yr),
        "features": FEATURES,
        "version": "baseline-synthetic-v1",
    }
    joblib.dump(models, MODEL_PATH)
    return models


def load_model():
    if MODEL_PATH.exists():
        return joblib.load(MODEL_PATH)
    return train_baseline_model()


def predict_risks(features: dict) -> dict:
    models = load_model()
    row = []
    for name in FEATURES:
        value = features.get(name)
        row.append(0.0 if value is None else float(value))
    X = np.array([row])

    def proba(model_name: str) -> float:
        model = models[model_name]
        if hasattr(model, "predict_proba"):
            return float(model.predict_proba(X)[0][1])
        return float(model.predict(X)[0])

    return {
        "delay_probability": round(proba("delay"), 4),
        "cost_overrun_probability": round(proba("cost"), 4),
        "rework_probability": round(proba("rework"), 4),
        "model_version": models.get("version", "unknown"),
    }
