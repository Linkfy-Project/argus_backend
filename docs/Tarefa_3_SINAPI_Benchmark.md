# Relatório Técnico: Tarefa 3 — Integração SINAPI de Benchmark de Custo/m²

**Data:** 2026-06-05  
**Status:** ✅ Implementado

---

## 1. Problema

O [`calculate_cost_score()`](../app/services/scoring.py:131) implementa 3 estratégias para calcular o score de custo paramétrico:

1. **Benchmark SINAPI × área** (ideal) — `max(0, 100 - ((Custo Real - Custo Referência) / Custo Referência) * 100)`
2. Heurística sem benchmark (proxy de risco financeiro)
3. Fallback conservador (50 pontos se sem dados)

O campo `benchmark_cost_m2` existia no modelo [`PublicWork`](../app/models/work.py:31), mas **nunca era preenchido**. Consequências:

- A fórmula principal do documento de regras (Estratégia 1) **nunca era usada**
- O scoring SEMPRE caía na heurística (Estratégia 2), que é menos precisa
- O endpoint `/works/scoring/rules` documentava a fórmula SINAPI, mas ela era inoperante

---

## 2. Solução Inicial Proposta

A proposta original era integrar dados SINAPI via APIs externas (AutoSINAPI ou Orçamentador). A pesquisa revelou:

- **Não existe API oficial do SINAPI** — a Caixa Econômica Federal disponibiliza apenas downloads de XLSX/PDF
- **AutoSINAPI** — API paga (R$ 13/semana mínimo)
- **Orçamentador** — API gratuita com limite de 100 req/hora

Ambas as opções tinham problemas: custo, rate limits, dependência de terceiros, latência de rede.

---

## 3. Solução Implementada

### Abordagem: Constantes SINAPI + Classificação por Regex

Em vez de depender de APIs externas, a solução usa **valores de referência SINAPI como constantes** hardcoded no sistema, atualizados trimestralmente.

### Arquivo criado: [`sinapi_benchmark.py`](../app/etl/sinapi_benchmark.py)

| Componente | Descrição |
|---|---|
| [`SINAPI_BENCHMARKS`](../app/etl/sinapi_benchmark.py:21) | Dicionário com 11 tipos de obra e custos R$/m² (RJ/Sudeste, jan/2026) |
| [`classify_work_type()`](../app/etl/sinapi_benchmark.py:58) | Classifica descrição da obra via regex em tipo SINAPI |
| [`apply_sinapi_benchmarks()`](../app/etl/sinapi_benchmark.py:82) | Percorre obras, classifica tipo, preenche `benchmark_cost_m2` |

### Tipos de obra e custos de referência:

| Tipo | R$/m² | Padrões regex |
|---|---|---|
| edificacao | 1.970 | (default) |
| edificacao_publica | 2.100 | escola, creche, UBS, posto de saúde |
| pavimentacao | 85 | pavimentação, asfalto, calçamento |
| drenagem | 120 | drenagem, galeria |
| saneamento | 150 | esgoto, saneamento |
| urbanizacao | 350 | urbanização |
| reforma | 1.200 | reforma |
| manutencao | 400 | manutenção |
| ponte | 5.000 | ponte, viaduto, passarela |
| contencao | 1.800 | contenção, muro |

### Integrações:

1. **Pipeline ETL** — Nova **etapa 4/9** em [`data_sync_job.py`](../app/jobs/data_sync_job.py), entre importação de CSVs e pipeline de IA
2. **Scoring** — [`recompute_work()`](../app/services/work_service.py:122) e [`recompute_many()`](../app/services/work_service.py:187) agora passam `benchmark_cost_m2=work.benchmark_cost_m2` para [`calculate_score()`](../app/services/scoring.py:588)
3. **API** — Endpoint `GET /etl/sinapi/benchmarks` retorna a tabela de referência

---

## 4. Efeito no Scoring

**Antes:** `calculate_cost_score()` SEMPRE usava Estratégia 2 (heurística, base 70 pontos).

**Depois:** Com `benchmark_cost_m2` preenchido E `area_m2` disponível, o scoring usa a **Estratégia 1** (fórmula SINAPI do documento de regras):
```
score = max(0, 100 - ((Custo Real - Custo Referência) / Custo Referência) * 100)
```

Onde:
- `Custo Real` = `settled_value` (ou `contract_value` como fallback)
- `Custo Referência` = `benchmark_cost_m2 × area_m2`

---

## 5. Adaptações em Relação à Solução Inicial

| Aspecto | Solução Inicial | Solução Implementada |
|---|---|---|
| Fonte de dados | API externa (AutoSINAPI/Orçamentador) | Constantes hardcoded |
| Custo | R$ 0-13/semana | R$ 0 |
| Dependência externa | Sim (API de terceiros) | Não |
| Atualização | Automática via API | Manual (trimestral) |
| Latência | Rede a cada sync | Zero (in-memory) |
| Granularidade | Por insumo/composição | Por tipo de obra |
| Precisão | Alta (preços exatos) | Média (referência por categoria) |

---

## 6. Limitações e Melhorias Futuras

- Valores são referência jan/2026 — precisam de atualização trimestral
- Classificação por regex pode errar em descrições ambíguas
- Não diferencia custo por região dentro do estado (capital vs interior)
- Futuro: integrar com a classificação da IA (pipeline de IA já extrai tipo de obra)
