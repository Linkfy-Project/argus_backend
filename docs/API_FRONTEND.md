# Integração do Frontend com o Backend ARGUS

Base URL local: `http://localhost:8000`

## Endpoints principais

- `GET /health` — verifica se a API está no ar.
- `GET /api/v1/works` — lista obras paginadas, aceita `municipio`, `min_score`, `max_score`, `status`, `search`, `min_value`, `max_value`, `page`, `per_page`.
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

## Dashboard Executivo (NOVO)

Endpoints que retornam dados prontos para o painel executivo, sem necessidade de cálculo no navegador.

### Resumo Executivo

```
GET /api/v1/dashboard/summary?municipio=Macae
```

Retorna todos os KPIs do painel: obras monitoradas, valores financeiros, contagem por faixa de risco, alertas, indicadores de qualidade dos dados e score médio.

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `municipio` | string | `Macae` | Nome do município (aceita com/sem acento) |

**Faixas de risco:**
- `Eficiente`: score 80-100
- `Atenção`: score 60-79
- `Alto risco`: score 40-59
- `Crítico`: score 0-39

### Fila Priorizada de Obras

```
GET /api/v1/dashboard/priority-queue?municipio=Macae&limit=10
```

Retorna as obras que o gestor deve avaliar primeiro, com motivo principal, ação sugerida e valor em risco estimado.

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `municipio` | string | `Macae` | Nome do município |
| `limit` | integer | `10` | Máximo de obras na fila (1-100) |

### Distribuição de Risco

```
GET /api/v1/dashboard/risk-distribution?municipio=Macae
```

Retorna contagem de obras por faixa de risco para gráficos de pizza/barras.

### Ranking de Bairros

```
GET /api/v1/dashboard/top-neighborhoods-risk?municipio=Macae&limit=10
```

Retorna bairros ordenados por score médio crescente (pior primeiro), com recomendações de ação.

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `municipio` | string | `Macae` | Nome do município |
| `limit` | integer | `10` | Máximo de bairros (1-50) |

### Ranking de Fornecedores

```
GET /api/v1/dashboard/top-suppliers-risk?municipio=Macae&limit=10
```

Retorna fornecedores ordenados por score médio crescente (pior primeiro), com percentual médio de aditivos e recomendações.

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `municipio` | string | `Macae` | Nome do município |
| `limit` | integer | `10` | Máximo de fornecedores (1-50) |

## Análise Microterritorial de Macaé-RJ (NOVO)

Endpoints que retornam dados prontos para a página "Análise Macaé-RJ" do frontend,
sem necessidade de agregações complexas no browser.

### Visão Geral Territorial

```
GET /api/v1/territory/macae/overview
```

Retorna a visão geral da análise microterritorial: bairros monitorados, obras, valor total contratado, score médio, bairros críticos, obras sem bairro/geolocalização, bairro mais crítico, bairro com maior valor, bairro com mais atrasos e recomendações territoriais.

**Resposta:**

| Campo | Tipo | Descrição |
|---|---|---|
| `municipio` | string | Nome canônico do município |
| `bairros_monitorados` | int | Quantidade de bairros distintos |
| `obras_monitoradas` | int | Total de obras |
| `valor_total_contratado` | float | Soma de contract_value |
| `score_medio` | float | Média de efficiency_score |
| `bairros_criticos` | int | Bairros com score médio < 40 |
| `obras_sem_bairro` | int | Obras com neighborhood nulo/vazio |
| `obras_sem_geolocalizacao` | int | Obras sem latitude/longitude |
| `bairro_mais_critico` | string | Bairro com menor score médio |
| `bairro_maior_valor` | string | Bairro com maior valor contratado |
| `bairro_mais_atrasos` | string | Bairro com mais obras atrasadas |
| `recomendacoes` | string[] | Lista de recomendações territoriais |

### Lista de Bairros

```
GET /api/v1/territory/macae/neighborhoods
```

Retorna lista de bairros com indicadores agregados de risco, ordenada por maior risco (score menor, mais obras críticas, mais alertas críticos, maior valor).

**Resposta (cada item):**

| Campo | Tipo | Descrição |
|---|---|---|
| `bairro` | string | Nome do bairro |
| `obras` | int | Quantidade de obras |
| `valor_total` | float | Valor total contratado |
| `valor_pago` | float | Valor total pago |
| `score_medio` | float | Score médio das obras |
| `obras_criticas` | int | Obras com score 0-39 |
| `obras_alto_risco` | int | Obras com score 40-59 |
| `obras_atrasadas` | int | Obras atrasadas |
| `alertas_totais` | int | Total de alertas |
| `alertas_criticos` | int | Alertas severity=critical |
| `fornecedores_distintos` | int | Fornecedores únicos |
| `fornecedor_mais_recorrente` | string | Fornecedor com mais obras |
| `obras_sem_geolocalizacao` | int | Obras sem coordenadas |
| `classificacao` | string | Crítico / Alto risco / Atenção / Eficiente |
| `recomendacao` | string | Recomendação de ação |

### Detalhe do Bairro

```
GET /api/v1/territory/macae/neighborhoods/{bairro}
```

Retorna detalhe completo de um bairro: resumo numérico, obras críticas, obras atrasadas, principais fornecedores, alertas, análise textual automática e ações recomendadas.

**Parâmetro de rota:** `bairro` — nome do bairro (ex: `Lagomar`)

**Resposta:**

| Campo | Tipo | Descrição |
|---|---|---|
| `bairro` | string | Nome do bairro |
| `resumo` | object | Resumo numérico (obras, valores, scores, classificação) |
| `obras_criticas` | array | Lista de obras com score 0-39 |
| `obras_atrasadas` | array | Lista de obras atrasadas |
| `principais_fornecedores` | array | Top 5 fornecedores do bairro |
| `alertas` | array | Alertas recentes do bairro |
| `analise_textual` | string | Análise textual automática |
| `acoes_recomendadas` | string[] | Ações recomendadas para o gestor |

### Heatmap Territorial

```
GET /api/v1/territory/macae/heatmap
```

Retorna FeatureCollection GeoJSON com obras georreferenciadas. Cada feature inclui propriedades de risco.

**Resposta (cada feature):**

| Propriedade | Tipo | Descrição |
|---|---|---|
| `obra_id` | int | ID da obra |
| `nome` | string | Descrição resumida |
| `bairro` | string | Bairro da obra |
| `score` | float/null | Score ARGUS |
| `classificacao` | string | Classificação de risco |
| `valor_contratado` | float | Valor do contrato |
| `alertas` | int | Quantidade de alertas |
| `dias_atraso` | int | Dias de atraso |
| `fornecedor` | string | Nome do fornecedor |

### Qualidade dos Dados Territoriais

```
GET /api/v1/territory/macae/data-quality
```

Retorna relatório de qualidade dos dados: total de obras, obras sem bairro, sem geolocalização, sem valor, sem fornecedor, sem prazo, score de qualidade (0-100) e lista de obras que precisam saneamento cadastral.

**Resposta:**

| Campo | Tipo | Descrição |
|---|---|---|
| `total_obras` | int | Total de obras |
| `obras_sem_bairro` | int | Obras sem neighborhood |
| `obras_sem_geolocalizacao` | int | Obras sem latitude/longitude |
| `obras_sem_valor` | int | Obras sem contract_value |
| `obras_sem_fornecedor` | int | Obras sem contractor_name |
| `obras_sem_prazo` | int | Obras sem due_at |
| `data_quality_score` | float | Score de qualidade (0-100) |
| `obras_para_saneamento` | array | Obras com problemas listados |

## Alertas (NOVO)

Endpoints próprios para alertas, com filtros avançados e atualização de status.
O frontend pode parar de depender exclusivamente de `worksService.listAll` para montar alertas.

### Listar Alertas

```
GET /api/v1/alerts
```

Retorna lista de alertas enriquecidos com dados da obra associada.

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `municipio` | string | — | Filtrar por município (normalizado, sem acento) |
| `severity` | string | — | Filtrar por severidade (info, warning, alert, critical) |
| `status` | string | — | Filtrar por status (Novo, Em análise, Encaminhado, Resolvido, Descartado) |
| `tipo` | string | — | Filtrar por tipo de alerta (ex: "Atraso crítico") |
| `bairro` | string | — | Filtrar por bairro da obra |
| `fornecedor` | string | — | Filtrar por fornecedor/contratado |
| `obra_id` | integer | — | Filtrar por ID da obra |
| `search` | string | — | Busca textual em mensagem, código, descrição, município e fornecedor |

**Resposta:** `AlertRead[]`

### Atualizar Status do Alerta

```
PATCH /api/v1/alerts/{id}/status
```

Payload: `{ "status": "Em análise" }`

Status permitidos: `Novo`, `Em análise`, `Encaminhado`, `Resolvido`, `Descartado`.

## Contratos (NOVO)

Endpoints próprios para contratos, derivados de obras públicas com campos calculados.

### Listar Contratos

```
GET /api/v1/contracts
```

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `municipio` | string | — | Filtrar por município |
| `fornecedor` | string | — | Filtrar por fornecedor |
| `secretaria` | string | — | Filtrar por secretaria/unidade gestora |
| `bairro` | string | — | Filtrar por bairro |
| `status` | string | — | Concluída, Vencida, Vigente, Planejada |
| `risco` | string | — | Crítico, Alto risco, Atenção, Baixo risco |
| `com_aditivo` | boolean | — | true = com aditivo, false = sem |
| `vencendo` | boolean | — | Vencendo nos próximos 30 dias |
| `vencido` | boolean | — | Já vencidos |
| `search` | string | — | Busca textual |

**Resposta:** `ContractRead[]`

### Detalhe do Contrato

```
GET /api/v1/contracts/{id}
```

Aceita ID numérico da obra ou formato `work-123`. Retorna detalhes incluindo alertas associados.

## Fornecedores (NOVO)

Endpoints próprios para ranking e detalhe de fornecedores.

### Ranking de Fornecedores

```
GET /api/v1/suppliers/ranking
```

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `municipio` | string | — | Filtrar por município |
| `bairro` | string | — | Filtrar por bairro |
| `risco` | string | — | Eficiente, Atenção, Alto risco, Crítico |
| `limit` | integer | 50 | Limite de resultados (1-500) |

**Resposta:** `SupplierRankingRead[]` ordenado por score médio (pior primeiro).

### Detalhe do Fornecedor

```
GET /api/v1/suppliers/{cnpj_or_name}
```

Aceita CNPJ ou nome do fornecedor. Retorna resumo, obras, contratos, bairros, alertas e recomendações.

## Fluxo recomendado para demo

1. Rodar API.
2. Popular dados demo com `python scripts/seed_demo.py`.
3. Consumir `/api/v1/dashboard/summary` para o painel executivo.
4. Consumir `/api/v1/dashboard/priority-queue` para a fila de prioridades.
5. Consumir `/api/v1/dashboard/risk-distribution` para gráficos de risco.
6. Consumir `/api/v1/territory/macae/overview` para a visão territorial.
7. Consumir `/api/v1/territory/macae/neighborhoods` para ranking de bairros.
8. Consumir `/api/v1/territory/macae/heatmap` para o mapa de calor.
9. Consumir `/api/v1/works` para listagem detalhada.
10. Consumir `/api/v1/analytics/map/geojson` para o mapa.

## Campos úteis para cards e dashboard

- `efficiency_score`: índice composto final ARGUS.
- `cost_score`, `deadline_score`, `quality_score`, `recurrence_score`, `social_impact_score`: pilares do índice.
- `risk_delay_probability`, `risk_cost_probability`, `risk_rework_probability`: probabilidades do modelo preditivo.
- `alerts`: lista de gatilhos como `CRITICAL_PARALISACAO`, `ALERT_SOBREPRECO` e `CRITICAL_TETO_LEGAL`.
