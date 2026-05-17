# Integração do Frontend com o Backend ARGUS

Base URL local: `http://localhost:8000`

## Endpoints principais

- `GET /health` — verifica se a API está no ar.
- `GET /api/v1/works` — lista obras, aceita `municipio`, `min_score`, `max_score`, `limit`, `offset`.
- `GET /api/v1/works/{id}` — detalhes da obra, incluindo scores e alertas.
- `POST /api/v1/works` — cria obra manualmente.
- `POST /api/v1/works/{id}/recompute` — recalcula índice, alertas e risco preditivo.
- `POST /api/v1/etl/tcerj/run?municipio=Macae` — baixa bases do TCE-RJ para `data/raw/tcerj`.
- `POST /api/v1/etl/macae-portal/run` — baixa CSVs do portal de Macaé.
- `POST /api/v1/etl/import-csv?path=data/raw/tcerj/obras_consolidado.csv` — importa CSV local para o banco.
- `GET /api/v1/analytics/summary` — KPIs gerais.
- `GET /api/v1/analytics/rankings` — melhores e piores obras por score.
- `GET /api/v1/analytics/map/geojson` — obras georreferenciadas para mapa.
- `POST /api/v1/ml/predict` — previsão isolada de riscos.
- `GET /api/v1/exports/works.csv` — exportação CSV.
- `GET /api/v1/exports/works.xlsx` — exportação Excel.

## Fluxo recomendado para demo

1. Rodar API.
2. Popular dados demo com `python scripts/seed_demo.py`.
3. Consumir `/api/v1/analytics/summary`, `/api/v1/works` e `/api/v1/analytics/map/geojson` no React.

## Campos úteis para cards e dashboard

- `efficiency_score`: índice composto final ARGUS.
- `cost_score`, `deadline_score`, `quality_score`, `recurrence_score`, `social_impact_score`: pilares do índice.
- `risk_delay_probability`, `risk_cost_probability`, `risk_rework_probability`: probabilidades do modelo preditivo.
- `alerts`: lista de gatilhos como `CRITICAL_PARALISACAO`, `ALERT_SOBREPRECO` e `CRITICAL_TETO_LEGAL`.
