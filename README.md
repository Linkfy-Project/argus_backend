# ARGUS Backend - FastAPI

Backend da plataforma ARGUS para análise de eficiência, risco e integridade de obras públicas municipais, com foco inicial em Macaé-RJ.

URL do backend(render): https://argus-backend-5bio.onrender.com

Documentação do Render: https://argus-backend-5bio.onrender.com/docs#/

## O que foi ajustado nesta versão

- **APIs de Alertas, Contratos e Fornecedores** (NOVO):
  - Endpoints próprios para alertas, contratos e fornecedores, eliminando a necessidade de derivar dados no frontend.
  - `GET /api/v1/alerts` — lista alertas com filtros por município, severidade, status, tipo, bairro, fornecedor, obra_id e busca textual.
  - `PATCH /api/v1/alerts/{id}/status` — atualiza status do alerta (Novo, Em análise, Encaminhado, Resolvido, Descartado).
  - `GET /api/v1/contracts` — lista contratos derivados de obras com filtros por município, fornecedor, secretaria, bairro, status, risco, aditivos e vencimento.
  - `GET /api/v1/contracts/{id}` — detalhe de contrato com alertas associados (aceita `work-123` ou ID numérico).
  - `GET /api/v1/suppliers/ranking` — ranking de fornecedores ordenado por score médio com classificação de risco e recomendações.
  - `GET /api/v1/suppliers/{cnpj_or_name}` — detalhe de fornecedor com obras, contratos, bairros e alertas.
  - Campo `status` adicionado ao modelo `Alert` com migração leve automática.
  - Schemas: `app/schemas/alert.py`, `app/schemas/contract.py`, `app/schemas/supplier.py`
  - Services: `app/services/alert_service.py`, `app/services/contract_service.py`, `app/services/supplier_service.py`
  - Endpoints: `app/api/v1/endpoints/alerts.py`, `app/api/v1/endpoints/contracts.py`, `app/api/v1/endpoints/suppliers.py`
- **Análise Microterritorial de Macaé-RJ** (NOVO):
  - Endpoints territoriais focados em Macaé-RJ com valor real para gestor público.
  - `GET /api/v1/territory/macae/overview` — visão geral territorial com bairros críticos, recomendações e KPIs.
  - `GET /api/v1/territory/macae/neighborhoods` — ranking de bairros ordenado por maior risco.
  - `GET /api/v1/territory/macae/neighborhoods/{bairro}` — detalhe completo do bairro com obras críticas, atrasadas, fornecedores e ações recomendadas.
  - `GET /api/v1/territory/macae/heatmap` — GeoJSON para mapa de calor com propriedades de risco.
  - `GET /api/v1/territory/macae/data-quality` — relatório de qualidade dos dados para saneamento cadastral.
  - Serviço: `app/services/territory_service.py`
  - Schemas: `app/schemas/territory.py`
  - Endpoints: `app/api/v1/endpoints/territory.py`
- Regras do **Índice de Eficiência Composta ARGUS (IEC)** consolidadas em `app/services/scoring.py`.
- Pesos oficiais implementados (v2 com ML Risk Score):
  - Custo Paramétrico: 25%
  - Prazo / Cronograma: 25%
  - Qualidade Técnica e Aditivos: 20%
  - Recorrência Territorial: 10%
  - Impacto Socioeconômico: 5%
  - ML Risk Score: 15%
- Fórmulas revisadas conforme o mapeamento de regras:
  - Custo: `max(0, 100 - ((Custo Real - Custo Referência) / Custo Referência) * 100)`
  - Prazo: `max(0, 100 - (Dias de Atraso / 90) * 100)`
  - Qualidade: `max(0, 100 - ((Variação de Aditivos % / 25) * 100) - Penalidades CREA)`
  - Impacto socioeconômico: `(1 - IDH Local) * 100`
- Matriz CREA incluída no cálculo:
  - Infração leve: -5 pontos
  - Infração média: -15 pontos
  - Infração grave/embargo: -40 pontos
- Regra de criticidade social implementada: alertas de obras em regiões com `IDH < 0.600` recebem multiplicador `1.5x` no peso de criticidade.
- Recorrência territorial preparada para receber `territorial_overlap_ratio`; quando não houver geometria, usa fallback documentado por recorrência do mesmo CNPJ contratado.
- ETL consolidado com scheduler quinzenal a cada 15 dias.
- Importador CSV ajustado para lidar melhor com:
  - colunas com BOM (`\ufeff`);
  - datas em milissegundos Unix vindas do TCE-RJ;
  - campos do Portal de Macaé;
  - campos de contratos, licitações e obras paralisadas do TCE-RJ;
  - campos opcionais de SINAPI, CREA e sobreposição territorial.
- Migração leve automática adicionada em `app/db/init_db.py` para incluir novas colunas em bancos já existentes sem depender de Alembic.
- Novas rotas de auditoria do score:
  - `GET /api/v1/works/scoring/rules`
  - `GET /api/v1/works/{work_id}/score-explain`
- Módulo de Machine Learning com modelo baseline para predição de riscos:
  - `POST /api/v1/ml/predict` — predição de probabilidade de atraso, estouro de custo e retrabalho
  - `POST /api/v1/ml/train-baseline` — re-treino do modelo baseline
- Exportação de dados:
  - `GET /api/v1/exports/works.csv` — exportar obras em CSV
  - `GET /api/v1/exports/works.xlsx` — exportar obras em Excel
- Camadas geoespaciais:
  - `GET /api/v1/geo-layers/{layer_type}` — FeatureCollection GeoJSON por tipo de camada (`municipality`, `census_tract`, `road`)

## Como rodar localmente

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# No Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

A API ficará disponível em:

```text
http://localhost:8000
```

Documentação Swagger:

```text
http://localhost:8000/docs
```

## Como rodar com Docker

```bash
docker compose up --build
```

## Como rodar o ETL

Pelo Swagger ou por requisição HTTP:

### Sincronização completa (TCE-RJ + Portal Macaé + Importação + Recálculo)

```bash
curl -X POST "http://localhost:8000/api/v1/etl/sync-public-data?municipio=Macae"
```

Parâmetros opcionais:

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `municipio` | string | `Macae` | Município usado na sincronização automática |
| `ano` | integer | (vazio) | Ano de referência. Se vazio, busca sem filtro de ano. |

### Extração apenas do TCE-RJ

```bash
curl -X POST "http://localhost:8000/api/v1/etl/tcerj/run?municipio=Macae&ano=2025"
```

Parâmetros opcionais:

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `municipio` | string | `Macae` | Município usado na extração do TCE-RJ |
| `ano` | integer | (vazio) | Ano de referência. Se vazio, busca sem filtro de ano. |

### Extração apenas do Portal da Transparência de Macaé

```bash
curl -X POST "http://localhost:8000/api/v1/etl/macae-portal/run"
```

### Importar manualmente um CSV local

```bash
curl -X POST "http://localhost:8000/api/v1/etl/import-csv?path=data/raw/tcerj/obras_consolidado.csv&municipio=Macae"
```

Parâmetros:

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `path` | string | (obrigatório) | Caminho local do CSV no backend |
| `municipio` | string | `Macae` | Município padrão caso o CSV não tenha município |

### Consultar status da atualização automática

```bash
curl "http://localhost:8000/api/v1/etl/sync-status"
```

O scheduler interno roda uma sincronização ao iniciar a API e depois repete o processo a cada 15 dias.

## Rotas principais para teste

### Health check

```bash
curl "http://localhost:8000/health"
```

### Obras (Works)

Listar obras:

```bash
curl "http://localhost:8000/api/v1/works?municipio=Macae&page=1&per_page=20&min_score=50&max_score=100"
```

Parâmetros opcionais:

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `municipio` | string | — | Filtrar por município |
| `min_score` | number | — | Score mínimo (eficiência) |
| `max_score` | number | — | Score máximo (eficiência) |
| `page` | integer | `1` | Página atual (começa em 1) |
| `per_page` | integer | `25` | Itens por página (máx. 10000) |

Criar obra:

```bash
curl -X POST "http://localhost:8000/api/v1/works" \
  -H "Content-Type: application/json" \
  -d '{"source": "manual", "municipio": "Macae", "object_description": "Pavimentação Rua X", "contract_value": 150000.00}'
```

Obter obra por ID:

```bash
curl "http://localhost:8000/api/v1/works/1"
```

Recalcular score de uma obra específica:

```bash
curl -X POST "http://localhost:8000/api/v1/works/1/recompute"
```

Recalcular todos os scores:

```bash
curl -X POST "http://localhost:8000/api/v1/works/recompute-all"
```

Ver regras do score:

```bash
curl "http://localhost:8000/api/v1/works/scoring/rules"
```

Auditar a memória de cálculo de uma obra:

```bash
curl "http://localhost:8000/api/v1/works/1/score-explain"
```

### Dashboard Executivo

Resumo executivo com todos os KPIs do painel:

```bash
curl "http://localhost:8000/api/v1/dashboard/summary?municipio=Macae"
```

Fila priorizada de obras (top 10 mais urgentes):

```bash
curl "http://localhost:8000/api/v1/dashboard/priority-queue?municipio=Macae&limit=10"
```

Distribuição de obras por faixa de risco:

```bash
curl "http://localhost:8000/api/v1/dashboard/risk-distribution?municipio=Macae"
```

Ranking de bairros com maior risco:

```bash
curl "http://localhost:8000/api/v1/dashboard/top-neighborhoods-risk?municipio=Macae&limit=10"
```

Ranking de fornecedores com maior risco:

```bash
curl "http://localhost:8000/api/v1/dashboard/top-suppliers-risk?municipio=Macae&limit=10"
```

Parâmetros opcionais do Dashboard:

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `municipio` | string | `Macae` | Nome do município (aceita com/sem acento) |
| `limit` | integer | `10` | Máximo de itens no ranking |

### Análise Microterritorial de Macaé-RJ

Visão geral territorial:

```bash
curl "http://localhost:8000/api/v1/territory/macae/overview"
```

Lista de bairros com indicadores de risco (ordenado por maior risco):

```bash
curl "http://localhost:8000/api/v1/territory/macae/neighborhoods"
```

Detalhe de um bairro específico:

```bash
curl "http://localhost:8000/api/v1/territory/macae/neighborhoods/Lagomar"
```

Heatmap GeoJSON para mapa de calor:

```bash
curl "http://localhost:8000/api/v1/territory/macae/heatmap"
```

Relatório de qualidade dos dados territoriais:

```bash
curl "http://localhost:8000/api/v1/territory/macae/data-quality"
```

### Analytics

Resumo analítico:

```bash
curl "http://localhost:8000/api/v1/analytics/summary?municipio=Macae"
```

Rankings de obras:

```bash
curl "http://localhost:8000/api/v1/analytics/rankings?limit=10"
```

GeoJSON para mapa:

```bash
curl "http://localhost:8000/api/v1/analytics/map/geojson"
```

### Machine Learning

Predição de riscos (atraso, estouro de custo, retrabalho):

```bash
curl -X POST "http://localhost:8000/api/v1/ml/predict" \
  -H "Content-Type: application/json" \
  -d '{"contract_value": 500000.0, "committed_value": 480000.0, "settled_value": 200000.0, "area_m2": 1200.0, "crea_light_count": 1, "crea_medium_count": 0, "crea_grave_count": 0, "delay_days": 30}'
```

Treinar/re-treinar modelo baseline:

```bash
curl -X POST "http://localhost:8000/api/v1/ml/train-baseline"
```

### Exportações

Exportar obras em CSV:

```bash
curl "http://localhost:8000/api/v1/exports/works.csv"
```

Exportar obras em Excel:

```bash
curl "http://localhost:8000/api/v1/exports/works.xlsx"
```

### Camadas Geoespaciais

Obter camada GeoJSON por tipo:

```bash
curl "http://localhost:8000/api/v1/geo-layers/{layer_type}"
```

Tipos disponíveis (`layer_type`):
- `municipality` — malha municipal
- `census_tract` — setores censitários
- `road` — malha viária

## Bases usadas ou consideradas

- TCE-RJ: contratos, licitações e obras paralisadas.
- Portal da Transparência de Macaé: contratos e licitações.
- IBGE/geobr: malhas territoriais, setores censitários e base para integração de IDH/território.
- OpenStreetMap/osmnx: malha viária e apoio a análises microterritoriais.
- SINAPI: benchmark de custo por m², quando disponível no dado importado ou enriquecido.
- CREA: penalidades técnicas leves, médias e graves, quando disponíveis ou integradas posteriormente.

## Observações de integração

O ZIP `scripts` foi usado apenas como referência de formato de CSV e comportamento das extrações. Ele não foi alterado.

Para melhorar a precisão do pilar de custo, recomenda-se enriquecer os CSVs com `benchmark_cost_m2` baseado no SINAPI. Para melhorar a recorrência territorial, recomenda-se alimentar `territorial_overlap_ratio` após processamento geoespacial com polígonos/convex hull das intervenções.
