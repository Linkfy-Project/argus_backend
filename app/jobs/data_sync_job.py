"""
Job principal de sincronização automática dos dados públicos do ARGUS.

Fluxo:
0. (Opcional) Reset completo do banco se FORCE_RESET=true.
   NOTA: model_cache NUNCA é excluída, mesmo com FORCE_RESET=true.
1. Extrai dados do TCE-RJ.
2. Extrai dados do Portal de Macaé.
3. Importa CSVs disponíveis.
4. Pipeline de IA (OpenRouter) — classifica descrições e extrai endereços.
5. Sincroniza camadas geoespaciais (município, setores, malha viária).
6. Geocodifica obras sem coordenadas.
7. Sincroniza IDH por setor censitário.
8. Calcula sobreposição territorial (buffer por raio).

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

    print(f"DEBUG: [ARGUS JOB] ===============================================")
    print(f"DEBUG: [ARGUS JOB] INICIANDO SINCRONIZAÇÃO")
    print(f"DEBUG: [ARGUS JOB]   Município: {municipio}")
    print(f"DEBUG: [ARGUS JOB]   Ano:       {ano or 'Todos'}")
    print(f"DEBUG: [ARGUS JOB]   Início:    {started_at.isoformat()}")
    print(f"DEBUG: [ARGUS JOB] ===============================================")

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
    if settings.FORCE_RESET:
        print(f"DEBUG: [ARGUS JOB] ▶ Etapa 0: FORCE_RESET=true — limpando TODAS as tabelas (exceto model_cache)...")
        db_reset = SessionLocal()
        try:
            # Ordem respeita foreign keys: alerts depende de public_works
            # model_cache NUNCA é limpa — o cache de IA é preservado
            db_reset.execute(text("DELETE FROM alerts"))
            db_reset.execute(text("DELETE FROM public_works"))
            db_reset.execute(text("DELETE FROM geo_layers"))
            db_reset.commit()
            print(f"DEBUG: [ARGUS JOB]   ✔ Tabelas limpas: alerts, public_works, geo_layers")
            print(f"DEBUG: [ARGUS JOB]   ℹ model_cache PRESERVADA (nunca é resetada)")
            result["steps"].append(
                {
                    "step": "force_reset",
                    "status": "ok",
                    "detail": "Tabelas truncadas (model_cache preservada).",
                }
            )
        except Exception as exc:
            db_reset.rollback()
            print(f"DEBUG: [ARGUS JOB]   ✘ Erro ao limpar tabelas: {exc}")
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
        print(f"DEBUG: [ARGUS JOB] FORCE_RESET=false — modo acumulativo (sem limpeza).")

    # ── Step 1: Extração TCE-RJ ──
    print(f"DEBUG: [ARGUS JOB] ▶ Etapa 1/8: Extraindo dados do TCE-RJ...")
    try:
        tcerj_result = extract_tcerj(municipio=municipio, ano=ano)
        result["steps"].append(
            {
                "step": "extract_tcerj",
                "status": "ok",
                "result": tcerj_result,
            }
        )
        print(f"DEBUG: [ARGUS JOB]   ✔ TCE-RJ concluído: {tcerj_result}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "extract_tcerj",
                "status": "error",
                "error": str(exc),
            }
        )
        print(f"DEBUG: [ARGUS JOB]   ✘ TCE-RJ falhou: {exc}")

    # ── Step 2: Extração Portal de Macaé ──
    print(f"DEBUG: [ARGUS JOB] ▶ Etapa 2/8: Extraindo dados do Portal de Macaé...")
    try:
        macae_result = update_macae_portal()
        result["steps"].append(
            {
                "step": "update_macae_portal",
                "status": "ok",
                "result": macae_result,
            }
        )
        print(f"DEBUG: [ARGUS JOB]   ✔ Portal Macaé concluído: {macae_result}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "update_macae_portal",
                "status": "error",
                "error": str(exc),
            }
        )
        print(f"DEBUG: [ARGUS JOB]   ✘ Portal Macaé falhou: {exc}")

    # ── Step 3: Importação de CSVs ──
    print(f"DEBUG: [ARGUS JOB] ▶ Etapa 3/8: Importando CSVs disponíveis...")

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
            print(f"DEBUG: [ARGUS JOB]     - CSV não encontrado, pulando: {csv_path}")
            result["steps"].append(
                {
                    "step": "import_csv",
                    "status": "skipped",
                    "path": str(csv_path),
                    "reason": "Arquivo não encontrado",
                }
            )
            continue

        print(f"DEBUG: [ARGUS JOB]     - Importando: {csv_path}...")
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
            print(f"DEBUG: [ARGUS JOB]       ✘ Falha ao importar {csv_path}: {exc}")

        finally:
            db.close()

    # ── Step 4: Backfill de hashes + Pipeline de IA (OpenRouter) ──
    print(f"DEBUG: [ARGUS JOB] ▶ Etapa 4/8: Backfill de hashes + Pipeline de IA...")
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
        print(f"DEBUG: [ARGUS JOB]   ✔ Pipeline de IA concluída: {ai_result}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "ai_pipeline",
                "status": "error",
                "error": str(exc),
            }
        )
        print(f"DEBUG: [ARGUS JOB]   ✘ Pipeline de IA falhou: {exc}")
    finally:
        db_ai.close()

    # ── Step 5: Camadas geoespaciais ──
    print(f"DEBUG: [ARGUS JOB] ▶ Etapa 5/8: Sincronizando camadas geoespaciais...")
    try:
        geo_result = sync_geo_layers()
        result["steps"].append(
            {
                "step": "sync_geo_layers",
                "status": "ok",
                "result": geo_result,
            }
        )
        print(f"DEBUG: [ARGUS JOB]   ✔ Camadas geoespaciais concluídas: {geo_result}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "sync_geo_layers",
                "status": "error",
                "error": str(exc),
            }
        )
        print(f"DEBUG: [ARGUS JOB]   ✘ Camadas geoespaciais falharam: {exc}")

    # ── Step 6: Geocodificação em batch (Google Maps) ──
    print(f"DEBUG: [ARGUS JOB] ▶ Etapa 6/8: Geocodificação em batch...")
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
        print(f"DEBUG: [ARGUS JOB]   ✔ Geocodificação concluída: {geo_stats}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "geocode_works",
                "status": "error",
                "error": str(exc),
            }
        )
        print(f"DEBUG: [ARGUS JOB]   ✘ Geocodificação falhou: {exc}")
    finally:
        db.close()

    # ── Step 7: Sincronização de IDH por setor censitário ──
    print(f"DEBUG: [ARGUS JOB] ▶ Etapa 7/8: Sincronizando IDH por setor censitário...")
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
        print(f"DEBUG: [ARGUS JOB]   ✔ IDH sincronizado: {idh_stats}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "sync_idh",
                "status": "error",
                "error": str(exc),
            }
        )
        print(f"DEBUG: [ARGUS JOB]   ✘ IDH falhou: {exc}")
    finally:
        db.close()

    # ── Step 8: Sobreposição territorial (buffer por raio) ──
    print(f"DEBUG: [ARGUS JOB] ▶ Etapa 8/8: Calculando sobreposição territorial...")
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
        print(f"DEBUG: [ARGUS JOB]   ✔ Sobreposição territorial concluída: {overlap_stats}")
    except Exception as exc:
        result["steps"].append(
            {
                "step": "territorial_overlap",
                "status": "error",
                "error": str(exc),
            }
        )
        print(f"DEBUG: [ARGUS JOB]   ✘ Sobreposição territorial falhou: {exc}")
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

    print(f"DEBUG: [ARGUS JOB] ===============================================")
    print(f"DEBUG: [ARGUS JOB] SINCRONIZAÇÃO CONCLUÍDA")
    print(f"DEBUG: [ARGUS JOB]   Status final:  {result['status']}")
    print(f"DEBUG: [ARGUS JOB]   Duração:       {duration:.2f} segundos")
    print(f"DEBUG: [ARGUS JOB]   Criados:       {total_created}")
    print(f"DEBUG: [ARGUS JOB]   Atualizados:   {total_updated}")
    print(f"DEBUG: [ARGUS JOB]   Erros:         {total_errors}")
    print(f"DEBUG: [ARGUS JOB]   Pulados:       {total_skipped}")
    print(f"DEBUG: [ARGUS JOB]   Finalizado:    {finished_at.isoformat()}")
    print(f"DEBUG: [ARGUS JOB] ===============================================")

    return result
