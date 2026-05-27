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

# Cache global do modelo carregado (evita joblib.load() em cada predict)
_model_cache = None


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
    """Carrega o modelo do disco SEM cache (útil quando o modelo é re-treinado)."""
    if MODEL_PATH.exists():
        return joblib.load(MODEL_PATH)
    return train_baseline_model()


def get_cached_model():
    """Retorna o modelo em cache. Carrega do disco apenas na primeira chamada."""
    global _model_cache
    if _model_cache is None:
        print("DEBUG: [ML] Carregando modelo pela primeira vez (cache).")
        _model_cache = load_model()
    return _model_cache


def invalidate_model_cache():
    """Invalida o cache para forçar recarga na próxima chamada (após re-treino)."""
    global _model_cache
    _model_cache = None


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


def predict_risks_batch(features_list: list[dict], models: dict | None = None) -> list[dict]:
    """
    Prediz riscos para MULTIPLAS obras DE UMA VEZ usando o modelo em cache.

    Args:
        features_list: Lista de dicionários com as features de cada obra.
        models: Modelo pré-carregado (se None, usa o cache global).

    Retorna:
        Lista de dicts com delay_probability, cost_overrun_probability,
        rework_probability, model_version.
    """
    if models is None:
        models = get_cached_model()

    if not features_list:
        return []

    # Monta matriz X 2D com TODAS as linhas de uma vez
    # Shape: (N_obras, N_features)
    X = np.array(
        [
            [0.0 if feats.get(name) is None else float(feats[name]) for name in FEATURES]
            for feats in features_list
        ],
        dtype=np.float64,
    )

    model_version = models.get("version", "unknown")
    n_obras = X.shape[0]

    # predict_proba em lote: retorna array shape (N_obras, 2)
    # A coluna [:, 1] é a probabilidade da classe positiva (risco)
    delay_probas = models["delay"].predict_proba(X)[:, 1]
    cost_probas = models["cost"].predict_proba(X)[:, 1]
    rework_probas = models["rework"].predict_proba(X)[:, 1]

    # Monta resultado como lista de dicts
    results = []
    for i in range(n_obras):
        results.append({
            "delay_probability": round(float(delay_probas[i]), 4),
            "cost_overrun_probability": round(float(cost_probas[i]), 4),
            "rework_probability": round(float(rework_probas[i]), 4),
            "model_version": model_version,
        })

    return results
