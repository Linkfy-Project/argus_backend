# ARGUS Backend - FastAPI

Backend da plataforma ARGUS para análise de eficiência, risco e integridade de obras públicas municipais, com foco inicial em Macaé-RJ.

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

```bash
curl -X POST "http://localhost:8000/api/v1/etl/sync-public-data?municipio=Macae"
```

Importar manualmente um CSV local:

```bash
curl -X POST "http://localhost:8000/api/v1/etl/import-csv?path=data/raw/tcerj/obras_consolidado.csv&municipio=Macae"
```

Consultar status da atualização automática:

```bash
curl "http://localhost:8000/api/v1/etl/sync-status"
```

O scheduler interno roda uma sincronização ao iniciar a API e depois repete o processo a cada 15 dias.

## Rotas principais para teste

Health check:

```bash
curl "http://localhost:8000/health"
```

Listar obras:

```bash
curl "http://localhost:8000/api/v1/works?municipio=Macae&limit=20"
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

Resumo analítico:

```bash
curl "http://localhost:8000/api/v1/analytics/summary?municipio=Macae"
```

GeoJSON para mapa:

```bash
curl "http://localhost:8000/api/v1/analytics/map/geojson"
```

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
