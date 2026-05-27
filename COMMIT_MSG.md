# Commit: Otimização massiva de performance e logs de depuração no scheduler

## Arquivos modificados:

### 1. `app/jobs/data_sync_job.py`
**O que foi feito:** Adicionados logs `DEBUG:` em todas as etapas do job de sincronização.
- Print do início, cada etapa (1/4 a 4/4), conclusão com duração total
- Logs de sucesso/erro de cada etapa com detalhes
- Resumo final: criados, atualizados, erros, pulados, duração em segundos

### 2. `app/etl/importer.py`
**O que foi feito:**
- Adicionado `warnings.filterwarnings` para suprimir warnings repetitivos de `pd.to_datetime(dayfirst=True)`
- **Otimização principal:** Substituído `db.commit()` + `db.refresh()` + `recompute_work()` por linha por:
  - Fase 1: Upsert em lote com `db.flush()` + 1 único `db.commit()`
  - Fase 2: Recompute batch usando `recompute_many()` em vez de loop individual
- Adicionados logs `DEBUG:` de progresso do upsert e recompute

### 3. `app/services/ml_service.py`
**O que foi feito:**
- Adicionado `get_cached_model()` — cache global do modelo ML carregado (evita `joblib.load()` do disco a cada predict)
- Adicionado `invalidate_model_cache()` — para forçar recarga após re-treino
- **Otimização:** Criado `predict_risks_batch()` — faz `predict_proba(X)` na matriz completa (N_obras x 8 features) em vez de loop linha a linha

### 4. `app/services/work_service.py`
**O que foi feito:**
- **Otimização principal:** Criado `recompute_many(work_ids)` — função batch que recalcula N obras com:
  - 1 única query `SELECT ... WHERE id IN (...)` para buscar todas as obras
  - 1 query `GROUP BY contractor_document` para pré-computar recorrências (vs 1 COUNT por obra)
  - Modelo ML carregado 1x via cache
  - `predict_risks_batch()` com matriz numpy completa
  - 1 `DELETE WHERE work_id IN (...)` em massa para alerts antigos
  - `bulk_insert_mappings()` para inserir todos os novos alerts
  - **1 único `db.commit()`** no final
- `recompute_all()` agora chama `recompute_many()` em vez de loop

### 5. `scripts/validate_data.py` (novo)
**O que foi feito:** Script de validação dos dados no SQLite que verifica:
- Contagem total de obras e scores
- Estatísticas (média, min, max)
- Distribuição por faixa de score
- Alertas por severidade
- Top 5 maiores riscos / piores scores
- Camadas geoespaciais

### 6. `.env` (novo)
**O que foi feito:** Criado a partir do `.env.example` com configuração SQLite local.

---

## Ganho de performance obtido:

| Antes | Depois | Ganho |
|-------|--------|-------|
| 1 commit + 1 recompute por obra (12.171 commits) | 3 commits no total (1 por CSV) + 3 batches | **~4060x menos commits** |
| `joblib.load()` 1x por obra (12.171 loads) | Cache global → 1 load | **12.170x menos I/O de disco** |
| `predict_proba()` 1 obra por vez (12.171 chamadas) | `predict_proba()` matriz completa (3 chamadas) | **~4000x menos overhead numpy** |
| Sincronização completa: ~4 min | Sincronização completa: **46s** | **~5x mais rápido** |
