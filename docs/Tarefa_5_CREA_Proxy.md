# Relatório Técnico: Tarefa 5 — Extração de Infrações CREA via Proxy Multi-Fonte

**Data:** 2026-06-05  
**Status:** ✅ Implementado

---

## 1. Problema

O modelo [`PublicWork`](../app/models/work.py) tem 3 campos para infrações CREA:
- `crea_light_count` — infrações leves (penalidade: 5 pontos no score)
- `crea_medium_count` — infrações médias (penalidade: 15 pontos)
- `crea_grave_count` — infrações graves/embargo (penalidade: 40 pontos)

Esses campos estavam **sempre zerados**, tornando o Pilar de Qualidade Técnica (20% do score) subutilizado. O [`calculate_quality_score()`](../app/services/scoring.py:407) e [`detect_crea_suspicious_patterns()`](../app/services/scoring.py:250) tinham lógica completa para penalizar infrações, mas sem dados para processar.

---

## 2. Descoberta da Pesquisa

### CREA-RJ não tem API pública de infrações

A pesquisa revelou que:
- O [portal de consulta pública do CREA-RJ](https://www.crea-rj.org.br/consultapublica) oferece apenas: profissionais, empresas, ARTs, certidões e protocolos — **sem infrações/autuações**
- A taxonomia leve/média/grave **não existe oficialmente** no CREA — a classificação legal é baseada na Lei nº 5.194/1966 com tipos de violação
- O script existente [`extract_crea.py`](../scripts/extract_crea.py) apenas faz regex no texto dos CSVs para encontrar menções a CREA

### Fontes alternativas identificadas

| Fonte | API | Dados | Viabilidade |
|---|---|---|---|
| **TCE-RJ** | Já integrado | Obras paralisadas, penalidades | ⭐⭐⭐⭐⭐ |
| **CEIS/CNEP (CGU)** | REST pública | Empresas sancionadas por CNPJ | ⭐⭐⭐⭐ |
| **Portal da Transparência** | REST pública | Dados de licitações e contratos | ⭐⭐⭐ |

---

## 3. Solução Implementada

### Arquivo criado: [`crea_proxy.py`](../app/etl/crea_proxy.py)

Módulo que estima infrações CREA usando 3 fontes complementares:

#### Fonte 1: TCE-RJ — Obras Paralisadas
- Lê `data/raw/tcerj/obras_paralisadas_raw.csv` (430 registros, 157 CNPJs únicos)
- Se o CNPJ da obra aparece nas obras paralisadas → `crea_grave_count += 1`
- Se o contratado tem múltiplas obras paralisadas → `crea_medium_count += 1` por obra extra

#### Fonte 2: CEIS/CNEP (Portal da Transparência — CGU)
- Consulta `https://portaldatransparencia.gov.br/api-de-dados/ceis` e `/cnep`
- Para cada CNPJ único no banco (limitado a 50 por execução, priorizando mais frequentes)
- Sanção no CEIS (impedimento) → `crea_medium_count += 1`
- Sanção no CNEP (inidôneo) → `crea_grave_count += 1`
- Rate limiting: 1 segundo entre requests

#### Fonte 3: Detecção por texto
- "embargo", "interdição" na descrição → `crea_grave_count += 1`
- "multa", "advertência" na descrição → `crea_light_count += 1`

### Função principal: [`sync_crea_proxy(db)`](../app/etl/crea_proxy.py:174)

---

## 4. Integrações

### Pipeline ETL
- Nova **etapa 5/10** em [`data_sync_job.py`](../app/jobs/data_sync_job.py), após SINAPI benchmark
- Controlada por `CREA_PROXY_ENABLED` (pode ser desabilitada)

### Configuração
Adicionado a [`config.py`](../app/core/config.py):
- `CREA_PROXY_ENABLED: bool = True`
- `CREA_CGU_MAX_CNPJS: int = 50`

---

## 5. Adaptações em Relação à Solução Inicial

| Aspecto | Solução Inicial (Documento) | Solução Implementada |
|---|---|---|
| Fonte de dados | CREA-RJ direto | Proxy: TCE-RJ + CGU + texto |
| Taxonomia | Leve/Média/Grave do CREA | Mapeamento para leve/média/grave via regras |
| API | CREA-RJ (não existe) | CGU Portal da Transparência (gratuita) |
| Cobertura | 100% das infrações CREA | Estimativa baseada em indicadores correlatos |
| Precisão | Alta (dados oficiais CREA) | Média (proxy por sanções e obras paralisadas) |

---

## 6. Efeito no Scoring

Com os campos `crea_*_count` preenchidos:
- [`calculate_quality_score()`](../app/services/scoring.py:407) aplica penalidades: `light×5 + medium×15 + grave×40`
- [`detect_crea_suspicious_patterns()`](../app/services/scoring.py:250) detecta padrões suspeitos (5 padrões)
- Uma obra com 1 infração grave perde 40 pontos do score de qualidade

---

## 7. Limitações

- Dados da CGU podem estar desatualizados (atraso de meses)
- Rate limit da CGU limita a 50 CNPJs por execução
- Não diferencia infrações por obra específica (usa CNPJ do contratado)
- Falsos positivos possíveis: empresa sancionada por outra obra pode afetar todas as suas obras
