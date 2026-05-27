"""
Job principal de sincronização automática dos dados públicos do ARGUS.

Fluxo:
1. Extrai dados do TCE-RJ.
2. Extrai dados do Portal de Macaé.
3. Importa CSVs disponíveis.
4. Sincroniza camadas geoespaciais (município, setores, malha viária).

Cada etapa possui logs de depuração (prefixo DEBUG:) para facilitar
o monitoramento em tempo real no terminal.
"""

from datetime import datetime
from pathlib import Path

from app.db.session import SessionLocal
from app.etl.tcerj_client import extract_tcerj
from app.etl.macae_portal import update_macae_portal
from app.etl.importer import import_csv
from app.etl.geo_sync import sync_geo_layers


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

    # ── Step 1: Extração TCE-RJ ──
    print(f"DEBUG: [ARGUS JOB] ▶ Etapa 1/4: Extraindo dados do TCE-RJ...")
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
    print(f"DEBUG: [ARGUS JOB] ▶ Etapa 2/4: Extraindo dados do Portal de Macaé...")
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
    print(f"DEBUG: [ARGUS JOB] ▶ Etapa 3/4: Importando CSVs disponíveis...")

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

    # ── Step 4: Camadas geoespaciais ──
    print(f"DEBUG: [ARGUS JOB] ▶ Etapa 4/4: Sincronizando camadas geoespaciais...")
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
