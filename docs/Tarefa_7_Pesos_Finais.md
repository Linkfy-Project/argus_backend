# Relatório Técnico: Tarefa 7 — Definição dos Pesos Finais dos Pilares do Score ARGUS

**Data:** 2026-06-05  
**Status:** ✅ Definido e documentado

---

## 1. Problema

O documento de regras original define pesos fixos para 5 pilares. O código atual usa uma versão modificada (v2) com 6 pilares, incluindo ML Risk Score. Era necessário decidir qual conjunto usar e documentar a justificativa.

## 2. Análise Comparativa

### Tabela de pesos:

| Pilar | Documento Original | Código v2 (ML) | Variação |
|---|---|---|---|
| Custo Paramétrico | 30% | 25% | -5% |
| Variáveis de Prazo | 25% | 25% | 0% |
| Qualidade Técnica | 20% | 20% | 0% |
| Recorrência Territorial | 15% | 10% | -5% |
| Impacto Socioeconômico | 10% | 5% | -5% |
| ML Risk Score | — | 15% | +15% (novo) |
| **Total** | **100%** | **100%** | — |

### Justificativa da redistribuição:

Os 15% do ML Risk Score foram redistribuídos proporcionalmente dos outros pilares:
- **Custo (-5%)**: O ML já prevê `risk_cost_probability`, complementando o pilar rule-based
- **Recorrência (-5%)**: O ML captura padrões de recorrência via `contractor_recurrence`
- **Impacto (-5%)**: O IDH já é usado como feature no ML, reduzindo a necessidade de peso direto

## 3. Decisão Final

**Mantidos os pesos v2 com ML (código atual)**

### Pesos definitivos:

| Pilar | Peso | Função no scoring.py |
|---|---|---|
| Custo Paramétrico | **25%** | `calculate_cost_score()` |
| Variáveis de Prazo | **25%** | `calculate_deadline_score()` |
| Qualidade Técnica | **20%** | `calculate_quality_score()` |
| Recorrência Territorial | **10%** | `calculate_recurrence_score()` |
| Impacto Socioeconômico | **5%** | `calculate_social_impact_score()` |
| ML Risk Score | **15%** | [`calculate_ml_risk_score()`](../app/services/scoring.py:581) |

## 4. Regras Especiais (mantidas do documento)

- **Multiplicador de Criticidade IDH < 0.600**: Alertas multiplicados por 1.5x
- **Matriz de Penalidade CREA**: Leve (5pts), Média (15pts), Grave (40pts)
- **Teto legal de aditivos**: 25% do valor original

## 5. Impacto no Sistema

Com os pesos v2:
- O pilar de ML contribui com 15% do score final
- O [`calculate_ml_risk_score()`](../app/services/scoring.py:581) converte probabilidades (0-1) em score (0-100) usando a fórmula: `100 - (média das probabilidades de risco × 100)`
- O score final é mais preditivo que o modelo puramente rule-based
- A [`calculate_score()`](../app/services/scoring.py:620) aplica os pesos no dicionário `W` (linha 672), que é idêntico ao [`WEIGHTS`](../app/services/scoring.py:13) global (linha 13)

## 6. Constantes Definidas

```python
# argus_backend/app/services/scoring.py (linhas 12-36)

# Pesos v2 — com ML Risk Score integrado (15% redistribuído dos outros pilares)
WEIGHTS = {
    "cost": 0.25,
    "deadline": 0.25,
    "quality": 0.20,
    "recurrence": 0.10,
    "social_impact": 0.05,
    "ml_risk": 0.15,
}

CREA_PENALTIES = {"light": 5, "medium": 15, "grave": 40}
CRITICAL_IDH_THRESHOLD = 0.600
CRITICAL_IDH_MULTIPLIER = 1.5
```

## 7. Verificação de Consistência no Código

| Verificação | Localização | Status |
|---|---|---|
| `WEIGHTS` global com pesos v2 | [scoring.py:13](../app/services/scoring.py:13) | ✅ Correto |
| `W` dentro de `calculate_score()` | [scoring.py:672](../app/services/scoring.py:672) | ✅ Correto |
| `CREA_PENALTIES` | [scoring.py:22](../app/services/scoring.py:22) | ✅ Correto |
| `CRITICAL_IDH_THRESHOLD` (= 0.600) | [scoring.py:35](../app/services/scoring.py:35) | ✅ Correto |
| `CRITICAL_IDH_MULTIPLIER` (= 1.5) | [scoring.py:36](../app/services/scoring.py:36) | ✅ Correto |
| Soma dos pesos = 100% | Dicionário `WEIGHTS` | ✅ 0.25+0.25+0.20+0.10+0.05+0.15 = 1.00 |
