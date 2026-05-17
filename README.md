# ARGUS Backend — FastAPI

Backend em FastAPI para o sistema **ARGUS**, plataforma de inteligência para eficiência de obras públicas municipais no RJ, com foco inicial em Macaé.

## O que este backend entrega

- API REST para obras públicas, indicadores, rankings e mapa GeoJSON.
- Ingestão de dados do TCE-RJ e Portal da Transparência de Macaé.
- Importação de CSVs gerados por crawlers ou planilhas de validação.
- Cálculo do Índice de Eficiência Composta ARGUS.
- Gatilhos de auditoria para custo, prazo, aditivos e recorrência.
- Camada inicial de Machine Learning para probabilidade de atraso, estouro de custo e retrabalho.
- Exportação CSV e Excel para relatórios e integração com frontend.

## Arquitetura

```text
React/Frontend -> FastAPI -> SQLite/PostgreSQL
                   |-> ETL TCE-RJ / Portal Macaé
                   |-> Scoring Engine ARGUS
                   |-> ML Baseline Model
                   |-> Exports CSV/XLSX
```

Por padrão, o projeto roda com SQLite para facilitar a demo. Para produção/MVP, troque `DATABASE_URL` para PostgreSQL.

## Como rodar localmente

```bash
cd argus_backend
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Acesse:

- API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

## Popular dados de demonstração

```bash
python scripts/seed_demo.py
```

Depois acesse:

```bash
GET http://localhost:8000/api/v1/works
GET http://localhost:8000/api/v1/analytics/summary
GET http://localhost:8000/api/v1/analytics/map/geojson
```

## Fluxo com dados reais

### 1. Baixar TCE-RJ

```bash
curl -X POST "http://localhost:8000/api/v1/etl/tcerj/run?municipio=Macae"
```

Isso salva os CSVs em `data/raw/tcerj`.

### 2. Importar consolidado

```bash
curl -X POST "http://localhost:8000/api/v1/etl/import-csv?path=data/raw/tcerj/obras_consolidado.csv&municipio=Macae"
```

### 3. Recalcular tudo

```bash
curl -X POST "http://localhost:8000/api/v1/works/recompute-all"
```

## Índice ARGUS

O score final é calculado em escala 0 a 100 usando os pesos:

- Custo paramétrico: 30%
- Variáveis de prazo: 25%
- Qualidade técnica/aditivos: 20%
- Recorrência territorial: 15%
- Impacto socioeconômico: 10%

## Observações importantes

- O modelo de ML incluído é um baseline sintético para hackathon e prova de conceito. Ele deve ser substituído por dados rotulados reais quando houver ground truth suficiente.
- A recorrência territorial está preparada no scoring como recorrência por contratado. Para a versão geoespacial completa, implemente polígonos/Convex Hull com dados de coordenadas/OSM.
- A importação de CSV é tolerante a nomes de colunas diferentes do TCE-RJ e do portal de Macaé.
