from __future__ import annotations
from pathlib import Path
import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from app.core.logging import get_logger

logger = get_logger(__name__)

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
    """
    Dataset sintético para PoC com distribuição mais realista.

    A versão anterior gerava ~50-80% de exemplos positivos (risco), o que fazia
    o modelo prever probabilidades altas para quase tudo. Esta versão usa uma
    distribuição mais equilibrada (~25-35% positivos) refletindo que a maioria
    das obras públicas NÃO estão em risco crítico.

    Os thresholds de risco foram calibrados para serem mais seletivos:
    - Delay: atraso > 90 dias E baixa execução (< 40%)
    - Custo: aditivos > 25% do contrato (teto legal)
    - Retrabalho: recorrência >= 5 E aditivos > 20%
    """
    rng = np.random.default_rng(42)
    n = 600  # Mais exemplos para melhor generalização
    X = []
    y_delay = []
    y_cost = []
    y_rework = []

    for _ in range(n):
        # 70% das obras são "normais" (sem risco), 30% são "problemáticas"
        is_problematic = rng.random() < 0.30

        if is_problematic:
            # Obras problemáticas: valores mais extremos
            contract = rng.uniform(500_000, 30_000_000)
            committed = contract * rng.uniform(0.8, 1.5)
            settled = committed * rng.uniform(0.05, 0.60)  # Baixa execução
            additive = contract * rng.uniform(0.10, 0.50)  # Aditivos altos
            area = rng.uniform(200, 15_000)
            delay = int(rng.integers(30, 365))  # Atraso significativo
            recurrence = int(rng.integers(2, 10))
            idh = rng.uniform(0.40, 0.75)  # IDH mais baixo
        else:
            # Obras normais: dentro do esperado
            contract = rng.uniform(100_000, 15_000_000)
            committed = contract * rng.uniform(0.85, 1.10)
            settled = committed * rng.uniform(0.50, 1.05)  # Boa execução
            additive = contract * rng.uniform(0, 0.15)  # Aditivos baixos
            area = rng.uniform(100, 20_000)
            delay = int(rng.integers(0, 60))  # Pouco ou nenhum atraso
            recurrence = int(rng.integers(1, 4))
            idh = rng.uniform(0.55, 0.90)  # IDH mais alto

        X.append([contract, committed, settled, additive, area, delay, recurrence, idh])

        # Labels mais seletivos (thresholds mais altos = menos falsos positivos)
        # Delay: atraso > 90 dias OU execução muito baixa
        y_delay.append(1 if delay > 90 or (committed > 0 and settled / committed < 0.30) else 0)
        # Custo: aditivos > 25% do contrato (teto legal)
        y_cost.append(1 if contract > 0 and additive / contract > 0.25 else 0)
        # Retrabalho: alta recorrência E aditivos moderados
        y_rework.append(1 if recurrence >= 5 or (contract > 0 and additive / contract > 0.30) else 0)

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
        logger.info("[ML] Carregando modelo pela primeira vez (cache).")
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
