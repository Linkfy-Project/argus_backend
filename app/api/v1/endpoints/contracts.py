"""
Endpoints de contratos do ARGUS.

Expõe endpoints para listar e detalhar contratos.
Cada contrato é derivado de uma PublicWork, com campos calculados
como status, dias para vencimento, classificação de risco, etc.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.contract import ContractRead, ContractDetailRead
from app.services.contract_service import list_contracts, get_contract

router = APIRouter(prefix="/contracts", tags=["contracts"])


@router.get(
    "",
    response_model=list[ContractRead],
    summary="Lista contratos com filtros",
    description=(
        "Retorna lista de contratos derivados de obras públicas. "
        "Cada contrato inclui status calculado, dias para vencimento, "
        "classificação de risco e contagem de alertas. "
        "Suporta filtros por município, fornecedor, secretaria, bairro, "
        "status, risco, aditivos, vencimento e busca textual."
    ),
)
def index(
    municipio: str | None = Query(None, description="Filtrar por município (normalizado, sem acento)"),
    fornecedor: str | None = Query(None, description="Filtrar por fornecedor/contratado"),
    secretaria: str | None = Query(None, description="Filtrar por secretaria/unidade gestora"),
    bairro: str | None = Query(None, description="Filtrar por bairro"),
    status: str | None = Query(None, description="Filtrar por status (Concluída, Vencida, Vigente, Planejada)"),
    risco: str | None = Query(None, description="Filtrar por classificação de risco (Crítico, Alto risco, Atenção, Baixo risco)"),
    com_aditivo: bool | None = Query(None, description="Filtrar contratos com aditivo (true) ou sem (false)"),
    vencendo: bool | None = Query(None, description="Filtrar contratos vencendo nos próximos 30 dias"),
    vencido: bool | None = Query(None, description="Filtrar contratos já vencidos"),
    search: str | None = Query(None, description="Busca textual em descrição, número, município, fornecedor e secretaria"),
    db: Session = Depends(get_db),
):
    """Lista contratos com filtros opcionais."""
    return list_contracts(
        db,
        municipio=municipio,
        fornecedor=fornecedor,
        secretaria=secretaria,
        bairro=bairro,
        status=status,
        risco=risco,
        com_aditivo=com_aditivo,
        vencendo=vencendo,
        vencido=vencido,
        search=search,
    )


@router.get(
    "/{contract_id}",
    response_model=ContractDetailRead,
    summary="Detalhe de um contrato",
    description=(
        "Retorna detalhes de um contrato específico. "
        "Aceita ID numérico da obra ou formato 'work-123'."
    ),
)
def show(contract_id: str, db: Session = Depends(get_db)):
    """Busca detalhe de um contrato por ID."""
    result = get_contract(db, contract_id)
    if not result:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    return result
