# Relatório Técnico: Tarefa 6 — Correção Inflacionária (IPCA)

**Data:** 2026-06-05  
**Status:** ✅ Implementado

---

## 1. Problema

O ARGUS monitora obras públicas de diferentes anos (2018-2026). O Pilar de Custo Paramétrico compara o custo real de cada obra com o benchmark SINAPI (valores de jan/2026). Sem correção inflacional:

- Uma obra de R$ 1 milhão em 2018 seria comparada com o benchmark de 2026, gerando **falso positivo** de baixo custo
- Obras mais antigas pareceriam "mais baratas" do que realmente eram na época
- A comparação temporal seria injusta

---

## 2. Solução Implementada

### Arquivo criado: [`inflation.py`](../app/etl/inflation.py)

| Função | Descrição |
|---|---|
| [`fetch_ipca_series()`](../app/etl/inflation.py:37) | Consulta API do BCB (SGS série 433) para variações mensais do IPCA |
| [`build_ipca_index()`](../app/etl/inflation.py:90) | Converte variações em números-índice acumulados (base 100) |
| [`correct_value()`](../app/etl/inflation.py:143) | Corrige valor entre duas datas: `valor × (índice_destino / índice_origem)` |
| [`correct_value_cached()`](../app/etl/inflation.py:190) | Versão com cache em memória para evitar múltiplas requisições |

### API do BCB
- Endpoint: `https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados?formato=json`
- Gratuito, sem autenticação
- Série histórica desde 1979
- Dados mensais de variação percentual do IPCA

### Integração no Scoring
- [`calculate_cost_score()`](../app/services/scoring.py:145) agora corrige o `real_cost` via IPCA antes de comparar com o benchmark SINAPI
- A correção é **apenas para comparação** — o valor original no banco não é alterado
- Fallback seguro: se a API do BCB falhar, o valor original é usado sem correção

### Configuração
- `INFLATION_ENABLED: bool = True` — habilita/desabilita
- `INFLATION_BASE_YEAR: int = 2018` — ano base da série

### Endpoints
- `GET /etl/inflation/ipca` — retorna números-índice acumulados
- `GET /etl/inflation/test-correction` — teste de correção de valores

---

## 3. Validação

- R$ 1.000.000 em jan/2018 → R$ 1.501.456,54 em dez/2025 (fator 1.501)
- Cache funcionando (segunda chamada sem nova requisição HTTP)
- API do BCB respondendo corretamente (100+ meses de dados)

---

## 4. Adaptações

| Aspecto | Solução Inicial | Implementado |
|---|---|---|
| Biblioteca | `python-bcb` | `requests` direto (sem dependência extra) |
| Armazenamento | Campo no banco | Correção on-the-fly no scoring |
| Cache | Nenhum | Cache em memória com `invalidate_cache()` |
| Fallback | Nenhum | Valor original se API falhar |
