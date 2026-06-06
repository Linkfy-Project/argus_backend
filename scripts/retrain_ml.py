#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script para retreinar o modelo ML usando dados reais do banco (knowledge distillation).

Em vez de usar dataset sintético (600 exemplos aleatórios), este script carrega
todas as obras que já possuem scores rule-based calculados e usa esses scores
como labels para treinar o modelo de classificação.

Labels derivados dos scores:
- y_delay = 1 se deadline_score < 50 (obra muito atrasada)
- y_cost  = 1 se cost_score < 50 (custo muito acima do esperado)
- y_rework = 1 se quality_score < 50 OU recurrence_score < 50 (problemas de qualidade/recorrência)

Uso:
    cd argus_backend && python scripts/retrain_ml.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Adiciona o diretório raiz do projeto ao sys.path para imports absolutos
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import cross_val_score
import joblib

from app.db.session import SessionLocal
from app.models.work import PublicWork
from app.services.ml_service import FEATURES, MODEL_PATH, invalidate_model_cache
from app.services.scoring import delay_days, calculate_contractor_crea_totals


# Mínimo de amostras para treinar o modelo (evita overfitting com poucos dados)
MIN_SAMPLES = 50


def build_features_and_labels(works: list[PublicWork], db) -> tuple[list, list, list, list]:
    """
    Constrói as matrizes de features e labels a partir das obras do banco.

    Args:
        works: Lista de objetos PublicWork com scores calculados.
        db: Sessão do banco de dados (para queries de contractor recurrence).

    Returns:
        Tupla (features, y_delay, y_cost, y_rework) como listas.
    """
    features: list[list[float]] = []
    y_delay: list[int] = []
    y_cost: list[int] = []
    y_rework: list[int] = []

    print(f"DEBUG: Processando {len(works)} obras com scores...")

    for i, w in enumerate(works):
        # Feature 1-5: Valores financeiros e área
        contract_value = float(w.contract_value or 0)
        committed_value = float(w.committed_value or 0)
        settled_value = float(w.settled_value or 0)
        additive_value = float(w.additive_value or 0)
        area_m2 = float(w.area_m2 or 0)

        # Feature 6: Dias de atraso (função do scoring.py)
        d_days = float(delay_days(w))

        # Feature 7: Contagem de obras do mesmo contratado (recorrência)
        contractor_work_count, _ = calculate_contractor_crea_totals(db, w)
        contractor_recurrence = float(contractor_work_count)

        # Feature 8: IDH
        idh = float(w.idh or 0)

        features.append([
            contract_value,
            committed_value,
            settled_value,
            additive_value,
            area_m2,
            d_days,
            contractor_recurrence,
            idh,
        ])

        # Labels derivados dos scores rule-based
        y_delay.append(1 if w.deadline_score is not None and w.deadline_score < 50 else 0)
        y_cost.append(1 if w.cost_score is not None and w.cost_score < 50 else 0)
        y_rework.append(
            1
            if (w.quality_score is not None and w.quality_score < 50)
            or (w.recurrence_score is not None and w.recurrence_score < 50)
            else 0
        )

        # Log de progresso a cada 100 obras
        if (i + 1) % 100 == 0:
            print(f"DEBUG: Processadas {i + 1}/{len(works)} obras...")

    return features, y_delay, y_cost, y_rework


def train_and_evaluate(X: np.ndarray, y_delay: np.ndarray, y_cost: np.ndarray, y_rework: np.ndarray) -> dict:
    """
    Treina os modelos RandomForest para cada label e avalia com cross-validation.

    Args:
        X: Matriz de features shape (N, 8).
        y_delay: Labels de atraso shape (N,).
        y_cost: Labels de custo shape (N,).
        y_rework: Labels de retrabalho shape (N,).

    Returns:
        Dicionário com os modelos treinados e metadados.
    """
    n_samples = X.shape[0]
    print(f"\nDEBUG: Treinando modelos com {n_samples} amostras e {X.shape[1]} features...")

    # Treina os 3 classificadores
    models = {
        "delay": RandomForestClassifier(n_estimators=100, random_state=42).fit(X, y_delay),
        "cost": RandomForestClassifier(n_estimators=100, random_state=43).fit(X, y_cost),
        "rework": RandomForestClassifier(n_estimators=100, random_state=44).fit(X, y_rework),
        "features": FEATURES,
        "version": f"real-data-v1-{n_samples}samples",
    }

    # Cross-validation para reportar qualidade do modelo
    # Usa min(5, n_samples) folds para não ter fold vazio
    cv_folds = min(5, n_samples)
    print(f"\nDEBUG: Cross-validation com {cv_folds} folds (F1-score):")

    for name, y in [("delay", y_delay), ("cost", y_cost), ("rework", y_rework)]:
        # Verifica se há pelo menos 2 classes para cross-validation
        unique_classes = len(np.unique(y))
        if unique_classes < 2:
            print(f"  {name}: SKIPPED (apenas 1 classe presente no dataset)")
            continue
        try:
            scores = cross_val_score(models[name], X, y, cv=cv_folds, scoring="f1")
            print(f"  {name}: F1 = {scores.mean():.3f} (+/- {scores.std():.3f})")
        except Exception as e:
            print(f"  {name}: ERRO na cross-validation: {e}")

    return models


def retrain_ml() -> dict:
    """
    Função principal: carrega obras do banco, gera features/labels e treina o modelo.

    Returns:
        Dicionário com status do treinamento.
    """
    print("DEBUG: [ML RETRAIN] Iniciando retreino com dados reais do banco...")

    # Abre sessão com o banco de dados
    db = SessionLocal()
    try:
        # Carrega todas as obras que já possuem efficiency_score calculado
        works = (
            db.query(PublicWork)
            .filter(PublicWork.efficiency_score.isnot(None))
            .all()
        )
        print(f"DEBUG: [ML RETRAIN] Encontradas {len(works)} obras com efficiency_score.")

        # Verifica se há dados suficientes
        if len(works) < MIN_SAMPLES:
            print(f"DEBUG: [ML RETRAIN] Dados insuficientes ({len(works)} obras). Mínimo: {MIN_SAMPLES}.")
            print("DEBUG: [ML RETRAIN] Usando dataset sintético como fallback.")
            from app.services.ml_service import train_baseline_model
            train_baseline_model()
            return {
                "status": "fallback_synthetic",
                "samples": len(works),
                "minimum": MIN_SAMPLES,
            }

        # Constrói features e labels
        features, y_delay, y_cost, y_rework = build_features_and_labels(works, db)

        # Converte para arrays numpy
        X = np.array(features, dtype=np.float64)
        y_delay_arr = np.array(y_delay, dtype=np.int32)
        y_cost_arr = np.array(y_cost, dtype=np.int32)
        y_rework_arr = np.array(y_rework, dtype=np.int32)

        # Log de distribuição das classes
        print(f"\nDEBUG: Distribuição das classes:")
        print(f"  delay:  {np.sum(y_delay_arr)} positivos / {len(y_delay_arr) - np.sum(y_delay_arr)} negativos")
        print(f"  cost:   {np.sum(y_cost_arr)} positivos / {len(y_cost_arr) - np.sum(y_cost_arr)} negativos")
        print(f"  rework: {np.sum(y_rework_arr)} positivos / {len(y_rework_arr) - np.sum(y_rework_arr)} negativos")

        # Treina e avalia
        models = train_and_evaluate(X, y_delay_arr, y_cost_arr, y_rework_arr)

        # Salva o modelo no disco
        joblib.dump(models, MODEL_PATH)
        print(f"\nDEBUG: [ML RETRAIN] Modelo salvo em {MODEL_PATH}")

        # Invalida o cache do modelo em memória
        invalidate_model_cache()
        print("DEBUG: [ML RETRAIN] Cache do modelo invalidado.")

        version = models.get("version", "unknown")
        print(f"\nDEBUG: [ML RETRAIN] Retreino concluído! Versão: {version}")

        return {
            "status": "trained",
            "samples": len(works),
            "version": version,
            "positive_rates": {
                "delay": float(np.mean(y_delay_arr)),
                "cost": float(np.mean(y_cost_arr)),
                "rework": float(np.mean(y_rework_arr)),
            },
        }

    finally:
        db.close()
        print("DEBUG: [ML RETRAIN] Sessão do banco fechada.")


if __name__ == "__main__":
    result = retrain_ml()
    print(f"\nDEBUG: Resultado final: {result}")
