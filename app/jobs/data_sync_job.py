from datetime import datetime
from pathlib import Path

from app.db.session import SessionLocal
from app.etl.tcerj_client import extract_tcerj
from app.etl.macae_portal import update_macae_portal
from app.etl.importer import import_csv


def sync_public_data_job(municipio: str = "Macae", ano: int | None = None) -> dict:
    """
    Job principal de sincronização automática dos dados públicos do ARGUS.

    Fluxo:
    1. Extrai dados do TCE-RJ.
    2. Extrai dados do Portal de Macaé.
    3. Importa CSVs disponíveis.
    4. Popula/atualiza o banco para consumo pelo dashboard.
    """

    started_at = datetime.now()

    result = {
        "status": "started",
        "municipio": municipio,
        "ano": ano,
        "started_at": started_at.isoformat(),
        "steps": [],
    }

    try:
        tcerj_result = extract_tcerj(municipio=municipio, ano=ano)

        result["steps"].append(
            {
                "step": "extract_tcerj",
                "status": "ok",
                "result": tcerj_result,
            }
        )
    except Exception as exc:
        result["steps"].append(
            {
                "step": "extract_tcerj",
                "status": "error",
                "error": str(exc),
            }
        )

    try:
        macae_result = update_macae_portal()

        result["steps"].append(
            {
                "step": "update_macae_portal",
                "status": "ok",
                "result": macae_result,
            }
        )
    except Exception as exc:
        result["steps"].append(
            {
                "step": "update_macae_portal",
                "status": "error",
                "error": str(exc),
            }
        )

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
            result["steps"].append(
                {
                    "step": "import_csv",
                    "status": "skipped",
                    "path": str(csv_path),
                    "reason": "Arquivo não encontrado",
                }
            )
            continue

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

        finally:
            db.close()

    result["status"] = "finished"
    result["total_created"] = total_created
    result["total_updated"] = total_updated
    result["total_errors"] = total_errors
    result["total_skipped"] = total_skipped
    result["finished_at"] = datetime.now().isoformat()

    print("[ARGUS JOB] Sincronização concluída:", result)

    return result