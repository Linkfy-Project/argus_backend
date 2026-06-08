"""
Job principal de sincronização automática dos dados públicos do ARGUS.

Fluxo:
0. (Opcional) Reset completo do banco se FORCE_RESET=true.
   NOTA: model_cache NUNCA é excluída, mesmo com FORCE_RESET=true.
1. Extrai dados do TCE-RJ.
2. Extrai dados do Portal de Macaé.
3. Importa CSVs disponíveis.
4. Aplica benchmarks SINAPI (preenche benchmark_cost_m2 por tipo de obra).
5. Estimativa de infrações CREA via proxy (TCE-RJ + CGU CEIS/CNEP).
6. Pipeline de IA (OpenRouter) — classifica descrições e extrai endereços.
7. Sincroniza camadas geoespaciais (município, setores, malha viária).
8. Geocodifica obras sem coordenadas.
9. Sincroniza IDH por setor censitário.
10. Calcula sobreposição territorial (buffer por raio).

Cada etapa possui logs de depuração (prefixo DEBUG:) para facilitar
o monitoramento em tempo real no terminal.
"""

from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.etl.tcerj_client import extract_tcerj
from app.etl.macae_portal import update_macae_portal
from app.etl.importer import import_csv
from app.etl.ai_pipeline import run_ai_pipeline, backfill_description_hashes
from app.etl.geo_sync import sync_geo_layers
from app.etl.geocode import batch_geocode_works
from app.etl.idh_sync import sync_idh
from app.etl.overlap import calculate_territorial_overlaps
from app.etl.sinapi_benchmark import apply_sinapi_benchmarks
from app.etl.crea_proxy import sync_crea_proxy
from app.etl.neighborhood_sync import sync_neighborhood_polygons, backfill_neighborhoods
from app.core.logging import get_logger

logger = get_logger(__name__)


def sync_public_data_job(municipio: str = "Macae", ano: int | None = None) -> dict:
    """
    Job principal de sincronização automática dos dados públicos do ARGUS.

    Args:
        municipio: Município alvo da sincronização (padrão: "Macae").
        ano: Ano de referência para filtro (None = sem filtro).

    Returns:
        Dicionário com o resultado completo de cada etapa.
    """

    started_at = datetime.now()

    logger.info(f"[ARGUS JOB] ===============================================")
    logger.info(f"[ARGUS JOB] INICIANDO SINCRONIZAÇÃO")
    logger.info(f"[ARGUS JOB]   Município: {municipio}")
    logger.info(f"[ARGUS JOB]   Ano:       {ano or 'Todos'}")
    logger.info(f"[ARGUS JOB]   Início:    {started_at.isoformat()}")
    logger.info(f"[ARGUS JOB] ===============================================")

    result = {
        "status": "started",
        "municipio": municipio,
        "ano": ano,
        "started_at": started_at.isoformat(),
        "steps": [],
    }

    # ── Step 0 (opcional): Reset completo do banco ──
    # NOTA: model_cache NUNCA é excluída, mesmo com FORCE_RESET=true,
    # pois o processamento da IA sempre deve ser reaproveitado.
    settings = get_settings()
    if settings.FORCE_RESETT:
        logger.info(f"[ARGUS JOB] ▶ Etapa 0: FORCE_RESET=true — limpando TODAS as tabelas (exceto model_cache)...")
        db_reset = SessionLocal()
        try:
            # Ordem respeita foreign keys: alerts depende de public_works
            # model_cache NUNCA é limpa — o cache de IA é preservado
            db_reset.execute(text("DELETE FROM alerts"))
            db_reset.execute(text("DELETE FROM public_works"))
            db_reset.execute(text("DELETE FROM geo_layers"))
            db_reset.commit()
            logger.info(f"[ARGUS JOB]   ✔ Tabelas limpas: alerts, public_works, geo_layers")
            logger.info(f"[ARGUS JOB]   ℹ model_cache PRESERVADA (nunca é resetada)")
            result["steps"].append(
                {
                    "step": "force_reset",
                    "status": "ok",
                    "detail": "Tabelas truncadas (model_cache preservada).",
                }
            )
        except Exception as exc:
            db_reset.rollback()
            logger.info(f"[ARGUS JOB]   ✘ Erro ao limpar tabelas: {exc}")
            result["steps"].append(
                {
                    "step": "force_reset",
                    "status": "error",
                    "error": str(exc),
                }
            )
        finally:
            db_reset.close()
    else:
        logger.info(f"[ARGUS JOB] FORCE_RESET=false — modo acumulativo (sem limpeza).")

    # ── Step 1: Extração TCE-RJ ──
    logger.info(f"[ARGUS JOB] ▶ Etapa 1/10: Extraindo dados do TCE-RJ...")
    try:
        tcerj_result = extract_tcerj(municipio=municipio, ano=ano)
        result["steps"].append(
            {
                "step": "extract_tcerj",
                "status": "ok",
                "result": tcerj_result,
            }
        )
        logger.info(f"[ARGUS JOB]   ✔ TCE-RJ concluído: {tcerj_result}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "extract_tcerj",
                "status": "error",
                "error": str(exc),
            }
        )
        logger.info(f"[ARGUS JOB]   ✘ TCE-RJ falhou: {exc}")

    # ── Step 2: Extração Portal de Macaé ──
    logger.info(f"[ARGUS JOB] ▶ Etapa 2/10: Extraindo dados do Portal de Macaé...")
    try:
        macae_result = update_macae_portal()
        result["steps"].append(
            {
                "step": "update_macae_portal",
                "status": "ok",
                "result": macae_result,
            }
        )
        logger.info(f"[ARGUS JOB]   ✔ Portal Macaé concluído: {macae_result}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "update_macae_portal",
                "status": "error",
                "error": str(exc),
            }
        )
        logger.info(f"[ARGUS JOB]   ✘ Portal Macaé falhou: {exc}")

    # ── Step 3: Importação de CSVs ──
    logger.info(f"[ARGUS JOB] ▶ Etapa 3/10: Importando CSVs disponíveis...")

    candidate_paths = [
        "data/raw/tcerj/obras_consolidado.csv",
        "data/raw/tcerj/licitacoes_obras.csv",
        "data/raw/tcerj/contratos_obras.csv",
        "data/raw/macae/contratos.csv",
        "data/raw/macae/licitacoes.csv",
        "dados_tcerj/obras_consolidado.csv",
        "dados_tcerj/licitacoes_obras.csv",
        "dados_tcerj/contratos_obras.csv",
        "dados_macae/contratos.csv",
        "dados_macae/licitacoes.csv",
    ]

    total_created = 0
    total_updated = 0
    total_errors = 0
    total_skipped = 0

    for path in candidate_paths:
        csv_path = Path(path)

        if not csv_path.exists():
            logger.info(f"[ARGUS JOB]     - CSV não encontrado, pulando: {csv_path}")
            result["steps"].append(
                {
                    "step": "import_csv",
                    "status": "skipped",
                    "path": str(csv_path),
                    "reason": "Arquivo não encontrado",
                }
            )
            continue

        logger.info(f"[ARGUS JOB]     - Importando: {csv_path}...")
        db = SessionLocal()

        try:
            import_result = import_csv(
                db,
                path=str(csv_path),
                default_municipio=municipio,
                recompute=True,
            )

            created = int(import_result.get("created", 0) or 0)
            updated = int(import_result.get("updated", 0) or 0)
            errors = int(import_result.get("errors", 0) or 0)
            skipped = int(import_result.get("skipped", 0) or 0)

            total_created += created
            total_updated += updated
            total_errors += errors
            total_skipped += skipped

            result["steps"].append(
                {
                    "step": "import_csv",
                    "status": "ok",
                    "path": str(csv_path),
                    "result": import_result,
                }
            )

            print(
                f"DEBUG: [ARGUS JOB]       ✔ Criados: {created} | "
                f"Atualizados: {updated} | Erros: {errors} | Pulados: {skipped}"
            )

        except Exception as exc:
            db.rollback()
            result["steps"].append(
                {
                    "step": "import_csv",
                    "status": "error",
                    "path": str(csv_path),
                    "error": str(exc),
                }
            )
            logger.info(f"[ARGUS JOB]       ✘ Falha ao importar {csv_path}: {exc}")

        finally:
            db.close()

    # ── Step 4: Aplicar benchmarks SINAPI ──
    logger.info(f"[ARGUS JOB] ▶ Etapa 4/10: Aplicando benchmarks SINAPI (custo/m² de referência)...")
    db_sinapi = SessionLocal()
    try:
        sinapi_result = apply_sinapi_benchmarks(db_sinapi)
        result["steps"].append(
            {
                "step": "sinapi_benchmarks",
                "status": "ok",
                "result": sinapi_result,
            }
        )
        logger.info(f"[ARGUS JOB]   ✔ Benchmarks SINAPI concluídos: {sinapi_result}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "sinapi_benchmarks",
                "status": "error",
                "error": str(exc),
            }
        )
        logger.info(f"[ARGUS JOB]   ✘ Benchmarks SINAPI falharam: {exc}")
    finally:
        db_sinapi.close()

    # ── Step 5: Estimativa de infrações CREA via proxy (TCE-RJ + CGU) ──
    settings_crea = get_settings()
    if settings_crea.CREA_PROXY_ENABLED:
        logger.info(f"[ARGUS JOB] ▶ Etapa 5/10: Estimativa de infrações CREA via proxy...")
        db_crea = SessionLocal()
        try:
            crea_result = sync_crea_proxy(db_crea)
            result["steps"].append(
                {
                    "step": "crea_proxy",
                    "status": "ok",
                    "result": crea_result,
                }
            )
            logger.info(f"[ARGUS JOB]   ✔ CREA proxy concluído: {crea_result}")
        except Exception as exc:
            result["steps"].append(
                {
                    "step": "crea_proxy",
                    "status": "error",
                    "error": str(exc),
                }
            )
            logger.info(f"[ARGUS JOB]   ✘ CREA proxy falhou: {exc}")
        finally:
            db_crea.close()
    else:
        logger.info(f"[ARGUS JOB] ▶ Etapa 5/10: CREA proxy DESABILITADO (CREA_PROXY_ENABLED=false), pulando...")
        result["steps"].append(
            {
                "step": "crea_proxy",
                "status": "skipped",
                "reason": "CREA_PROXY_ENABLED=false",
            }
        )

    # ── Step 6: Backfill de hashes + Pipeline de IA (OpenRouter) ──
    logger.info(f"[ARGUS JOB] ▶ Etapa 6/10: Backfill de hashes + Pipeline de IA...")
    db_ai = SessionLocal()
    try:
        # Primeiro, garante que todos os public_works tenham description_hash
        backfill_result = backfill_description_hashes(db_ai)
        result["steps"].append(
            {
                "step": "backfill_hashes",
                "status": backfill_result.get("status", "unknown"),
                "result": backfill_result,
            }
        )

        # Depois, executa a pipeline de IA (se API key configurada)
        ai_result = run_ai_pipeline(db_ai)
        result["steps"].append(
            {
                "step": "ai_pipeline",
                "status": ai_result.get("status", "unknown"),
                "result": ai_result,
            }
        )
        logger.info(f"[ARGUS JOB]   ✔ Pipeline de IA concluída: {ai_result}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "ai_pipeline",
                "status": "error",
                "error": str(exc),
            }
        )
        logger.info(f"[ARGUS JOB]   ✘ Pipeline de IA falhou: {exc}")
    finally:
        db_ai.close()

    # ── Step 7: Camadas geoespaciais ──
    logger.info(f"[ARGUS JOB] ▶ Etapa 7/12: Sincronizando camadas geoespaciais...")
    try:
        geo_result = sync_geo_layers()
        result["steps"].append(
            {
                "step": "sync_geo_layers",
                "status": "ok",
                "result": geo_result,
            }
        )
        logger.info(f"[ARGUS JOB]   ✔ Camadas geoespaciais concluídas: {geo_result}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "sync_geo_layers",
                "status": "error",
                "error": str(exc),
            }
        )
        logger.info(f"[ARGUS JOB]   ✘ Camadas geoespaciais falharam: {exc}")

    # ── Step 8: Polígonos de bairros (OpenStreetMap) ──
    logger.info(f"[ARGUS JOB] ▶ Etapa 8/12: Sincronizando polígonos de bairros...")
    try:
        neighborhood_poly_result = sync_neighborhood_polygons()
        result["steps"].append(
            {
                "step": "sync_neighborhood_polygons",
                "status": "ok",
                "result": neighborhood_poly_result,
            }
        )
        logger.info(f"[ARGUS JOB]   ✔ Polígonos de bairros: {neighborhood_poly_result}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "sync_neighborhood_polygons",
                "status": "error",
                "error": str(exc),
            }
        )
        logger.info(f"[ARGUS JOB]   ✘ Polígonos de bairros falharam: {exc}")

    # ── Step 9: Geocodificação em batch (Google Maps) ──
    logger.info(f"[ARGUS JOB] ▶ Etapa 9/12: Geocodificação em batch...")
    db = SessionLocal()
    try:
        geo_stats = batch_geocode_works(db)
        result["steps"].append(
            {
                "step": "geocode_works",
                "status": "ok",
                "result": geo_stats,
            }
        )
        logger.info(f"[ARGUS JOB]   ✔ Geocodificação concluída: {geo_stats}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "geocode_works",
                "status": "error",
                "error": str(exc),
            }
        )
        logger.info(f"[ARGUS JOB]   ✘ Geocodificação falhou: {exc}")
    finally:
        db.close()

    # ── Step 10: Backfill de neighborhoods (point-in-polygon + regex) ──
    logger.info(f"[ARGUS JOB] ▶ Etapa 10/12: Preenchendo bairros das obras...")
    db = SessionLocal()
    try:
        nb_stats = backfill_neighborhoods(db)
        result["steps"].append(
            {
                "step": "backfill_neighborhoods",
                "status": "ok",
                "result": nb_stats,
            }
        )
        logger.info(f"[ARGUS JOB]   ✔ Backfill de bairros concluído: {nb_stats}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "backfill_neighborhoods",
                "status": "error",
                "error": str(exc),
            }
        )
        logger.info(f"[ARGUS JOB]   ✘ Backfill de bairros falhou: {exc}")
    finally:
        db.close()

    # ── Step 11: Sincronização de IDH por setor censitário ──
    logger.info(f"[ARGUS JOB] ▶ Etapa 11/12: Sincronizando IDH por setor censitário...")
    db = SessionLocal()
    try:
        idh_stats = sync_idh(db)
        result["steps"].append(
            {
                "step": "sync_idh",
                "status": "ok",
                "result": idh_stats,
            }
        )
        logger.info(f"[ARGUS JOB]   ✔ IDH sincronizado: {idh_stats}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "sync_idh",
                "status": "error",
                "error": str(exc),
            }
        )
        logger.info(f"[ARGUS JOB]   ✘ IDH falhou: {exc}")
    finally:
        db.close()

    # ── Step 12: Sobreposição territorial (buffer por raio) ──
    logger.info(f"[ARGUS JOB] ▶ Etapa 12/12: Calculando sobreposição territorial...")
    db = SessionLocal()
    try:
        overlap_stats = calculate_territorial_overlaps(db)
        result["steps"].append(
            {
                "step": "territorial_overlap",
                "status": "ok",
                "result": overlap_stats,
            }
        )
        logger.info(f"[ARGUS JOB]   ✔ Sobreposição territorial concluída: {overlap_stats}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "territorial_overlap",
                "status": "error",
                "error": str(exc),
            }
        )
        logger.info(f"[ARGUS JOB]   ✘ Sobreposição territorial falhou: {exc}")
    finally:
        db.close()

    # ── Finalização ──
    finished_at = datetime.now()
    duration = (finished_at - started_at).total_seconds()

    result["status"] = "finished"
    result["total_created"] = total_created
    result["total_updated"] = total_updated
    result["total_errors"] = total_errors
    result["total_skipped"] = total_skipped
    result["finished_at"] = finished_at.isoformat()

    logger.info(f"[ARGUS JOB] ===============================================")
    logger.info(f"[ARGUS JOB] SINCRONIZAÇÃO CONCLUÍDA")
    logger.info(f"[ARGUS JOB]   Status final:  {result['status']}")
    logger.info(f"[ARGUS JOB]   Duração:       {duration:.2f} segundos")
    logger.info(f"[ARGUS JOB]   Criados:       {total_created}")
    logger.info(f"[ARGUS JOB]   Atualizados:   {total_updated}")
    logger.info(f"[ARGUS JOB]   Erros:         {total_errors}")
    logger.info(f"[ARGUS JOB]   Pulados:       {total_skipped}")
    logger.info(f"[ARGUS JOB]   Finalizado:    {finished_at.isoformat()}")
    logger.info(f"[ARGUS JOB] ===============================================")

    return result
