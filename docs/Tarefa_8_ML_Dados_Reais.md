# Relatório Técnico: Tarefa 8 — Modelo ML com Dados Reais (Knowledge Distillation)

**Data:** 2026-06-05
**Status:** ✅ Implementado

## 1. Problema

O modelo ML era treinado com dataset sintético (600 exemplos gerados aleatoriamente pelo método `_baseline_dataset()` em [`ml_service.py`](../app/services/ml_service.py)), sem relação com dados reais de obras públicas. Isso funciona para PoC, mas o ideal é que o modelo aprenda padrões reais do sistema.

## 2. Solução: Knowledge Distillation

Em vez de aguardar labels reais (que exigem auditoria manual de campo), usamos os **scores rule-based** já calculados pelo sistema como labels para treinar o modelo ML. Essa abordagem é conhecida como *knowledge distillation*: o "professor" (sistema rule-based) ensina o "aluno" (modelo ML).

### Labels Derivados dos Scores

| Label | Regra | Lógica |
|---|---|---|
| `y_delay` | `deadline_score < 50` | Obra com atraso significativo segundo scoring rule-based |
| `y_cost` | `cost_score < 50` | Custo muito acima do benchmark ou heurísticas financeiras |
| `y_rework` | `quality_score < 50` OU `recurrence_score < 50` | Problemas de qualidade (CREA) ou alta recorrência de contratado |

Os scores são contínuos (0-100), onde valores baixos indicam risco alto. O threshold de 50 foi escolhido como ponto de corte para classificação binária.

## 3. Features do Modelo

As 8 features de entrada são extraídas diretamente dos campos da tabela `public_works`:

| # | Feature | Fonte | Descrição |
|---|---------|-------|-----------|
| 1 | `contract_value` | `PublicWork.contract_value` | Valor contratual da obra |
| 2 | `committed_value` | `PublicWork.committed_value` | Valor empenhado |
| 3 | `settled_value` | `PublicWork.settled_value` | Valor liquidado |
| 4 | `additive_value` | `PublicWork.additive_value` | Valor de aditivos contratuais |
| 5 | `area_m2` | `PublicWork.area_m2` | Área da obra em metros quadrados |
| 6 | `delay_days` | `scoring.delay_days()` | Dias de atraso calculados pela regra de negócio |
| 7 | `contractor_recurrence` | `scoring.calculate_contractor_crea_totals()` | Quantidade de obras do mesmo CNPJ |
| 8 | `idh` | `PublicWork.idh` | Índice de Desenvolvimento Humano do local |

## 4. Implementação

### 4.1. Script CLI — `scripts/retrain_ml.py`

Script standalone para retreino via terminal:

```bash
cd argus_backend && python scripts/retrain_ml.py
```

Fluxo:
1. Conecta ao banco via `SessionLocal()`
2. Carrega obras com `efficiency_score IS NOT NULL`
3. Gera features e labels binários
4. Treina 3 `RandomForestClassifier` (100 árvores cada)
5. Avalia com cross-validation (F1-score)
6. Salva modelo em `app/ml/artifacts/argus_baseline_model.joblib`
7. Invalida cache do modelo em memória

### 4.2. Endpoint API — `POST /ml/retrain-real`

Endpoint FastAPI em [`ml.py`](../app/api/v1/endpoints/ml.py) para retreino via API:

```
POST /api/v1/ml/retrain-real
```

**Response (sucesso):**
```json
{
  "status": "trained",
  "samples": 150,
  "version": "real-data-v1-150samples",
  "cross_validation": {
    "delay": {"f1_mean": 0.82, "f1_std": 0.05},
    "cost": {"f1_mean": 0.75, "f1_std": 0.08},
    "rework": {"f1_mean": 0.70, "f1_std": 0.10}
  },
  "positive_rates": {
    "delay": 0.25,
    "cost": 0.18,
    "rework": 0.30
  }
}
```

**Response (dados insuficientes):**
```json
{
  "status": "insufficient_data",
  "samples": 20,
  "minimum": 50
}
```

### 4.3. Fallback

Se houver menos de 50 obras com scores, o script mantém o modelo sintético como fallback para garantir que o sistema continue funcionando.

## 5. Comparação: Antes vs Depois

| Aspecto | Antes (Sintético) | Depois (Real) |
|---|---|---|
| Dados de treino | 600 exemplos aleatórios | Obras reais do banco |
| Labels | Thresholds arbitrários sobre dados sintéticos | Scores rule-based do sistema |
| Validação | Nenhuma (baseline sem validação) | Cross-validation F1-score |
| Versão do modelo | `baseline-synthetic-v1` | `real-data-v1-Nsamples` |
| Distribuição das classes | ~30% positivos (hardcoded) | Variável conforme dados reais |
| Atualização | Manual (recriar dataset) | Automática (basta rodar retreino) |

## 6. Arquivos Envolvidos

| Arquivo | Alteração |
|---|---|
| [`scripts/retrain_ml.py`](../scripts/retrain_ml.py) | **NOVO** — Script de retreino via CLI |
| [`app/api/v1/endpoints/ml.py`](../app/api/v1/endpoints/ml.py) | **MODIFICADO** — Adicionado endpoint `POST /ml/retrain-real` |
| [`app/services/ml_service.py`](../app/services/ml_service.py) | Referência — `FEATURES`, `MODEL_PATH`, `invalidate_model_cache()` |
| [`app/services/scoring.py`](../app/services/scoring.py) | Referência — `delay_days()`, `calculate_contractor_crea_totals()` |
| [`app/models/work.py`](../app/models/work.py) | Referência — Campos de score no modelo `PublicWork` |

## 7. Limitações e Próximos Passos

- **Threshold arbitrário:** O corte de 50 para binarização é uma simplificação. Pode ser ajustado ou substituído por regressão.
- **Classes desbalanceadas:** Obras problemáticas podem ser minoria. Considerar SMOTE ou class_weight.
- **Conceito de data drift:** O modelo deve ser retreinado periodicamente conforme novos dados entram no sistema.
- **Labels reais:** O ideal futuro é ter labels de auditoria manual para validar a abordagem de knowledge distillation.
