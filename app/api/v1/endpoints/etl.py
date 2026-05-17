from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.etl.tcerj_client import extract_tcerj
from app.etl.macae_portal import update_macae_portal
from app.etl.importer import import_csv
from app.jobs.scheduler import run_sync_now, get_next_sync_info

router = APIRouter(prefix="/etl", tags=["etl"])


@router.post("/tcerj/run")
def run_tcerj(
    municipio: str = Query("Macae", description="Município usado na extração do TCE-RJ"),
    ano: int | None = Query(None, description="Ano de referência. Se vazio, busca sem filtro de ano."),
):
    """
    Executa a extração dos dados do TCE-RJ.

    Essa rota baixa/gera os arquivos brutos do TCE-RJ.
    """
    return extract_tcerj(municipio=municipio, ano=ano)


@router.post("/macae-portal/run")
def run_macae_portal():
    """
    Executa a extração dos dados do Portal da Transparência de Macaé.
    """
    return update_macae_portal()


@router.post("/import-csv")
def import_from_csv(
    path: str = Query(..., description="Caminho local do CSV no backend"),
    municipio: str = Query("Macae", description="Município padrão usado caso o CSV não tenha município"),
    db: Session = Depends(get_db),
):
    """
    Importa um CSV local para o banco.

    Exemplo:
    path=data/raw/tcerj/obras_consolidado.csv
    municipio=Macae
    """
    try:
        return import_csv(db, path=path, default_municipio=municipio, recompute=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/sync-public-data")
def sync_public_data(
    municipio: str = Query("Macae", description="Município usado na sincronização automática"),
    ano: int | None = Query(None, description="Ano de referência. Se vazio, busca sem filtro de ano."),
):
    """
    Executa manualmente o fluxo completo de sincronização do ARGUS.

    Fluxo:
    1. Extrai dados do TCE-RJ.
    2. Extrai dados do Portal de Macaé.
    3. Importa os CSVs encontrados para o banco.
    4. Recalcula os indicadores das obras.
    """
    try:
        return run_sync_now(municipio=municipio, ano=ano)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/sync-status")
def sync_status():
    """
    Retorna quanto tempo falta para a próxima sincronização automática.
    """

    return get_next_sync_info()