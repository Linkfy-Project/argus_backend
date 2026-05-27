# ARGUS Backend - FastAPI

Backend da plataforma ARGUS para análise de eficiência, risco e integridade de obras públicas municipais, com foco inicial em Macaé-RJ.

URL do backend(render): https://argus-backend-5bio.onrender.com

Documentação do Render: https://argus-backend-5bio.onrender.com/docs#/

## O que foi ajustado nesta versão

- Regras do **Índice de Eficiência Composta ARGUS (IEC)** consolidadas em `app/services/scoring.py`.
- Pesos oficiais implementados:
  - Custo Paramétrico: 30%
  - Prazo: 25%
  - Qualidade Técnica e Aditivos: 20%
  - Recorrência Territorial: 15%
  - Impacto Socioeconômico: 10%
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
curl "http://localhost:8000/api/v1/works?municipio=Macae&limit=20&min_score=50&max_score=100"
```

Parâmetros opcionais:

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `municipio` | string | — | Filtrar por município |
| `min_score` | number | — | Score mínimo (eficiência) |
| `max_score` | number | — | Score máximo (eficiência) |
| `limit` | integer | `100` | Limite de resultados (máx. 500) |
| `offset` | integer | `0` | Offset para paginação |

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
