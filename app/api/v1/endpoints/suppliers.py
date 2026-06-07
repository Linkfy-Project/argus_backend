"""
Endpoints de fornecedores do ARGUS.

Expõe endpoints para ranking e detalhe de fornecedores.
Cada fornecedor é agregado a partir de múltiplas obras (PublicWork)
com o mesmo contractor_name.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.supplier import SupplierRankingRead, SupplierDetailRead
from app.services.supplier_service import list_suppliers_ranking, get_supplier_detail

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get(
    "/ranking",
    response_model=list[SupplierRankingRead],
    summary="Ranking de fornecedores",
    description=(
        "Retorna ranking de fornecedores ordenado por score médio (pior primeiro). "
        "Agrega dados de todas as obras de cada fornecedor, incluindo valores, "
        "alertas, classificação de risco e recomendações. "
        "Suporta filtros por município, bairro, risco e limite de resultados."
    ),
)
def ranking(
    municipio: str | None = Query(None, description="Filtrar por município"),
    bairro: str | None = Query(None, description="Filtrar por bairro"),
    risco: str | None = Query(None, description="Filtrar por classificação de risco (Eficiente, Atenção, Alto risco, Crítico)"),
    limit: int = Query(50, ge=1, le=500, description="Limite de resultados"),
    db: Session = Depends(get_db),
):
    """Retorna ranking de fornecedores."""
    return list_suppliers_ranking(db, municipio=municipio, bairro=bairro, risco=risco, limit=limit)


@router.get(
    "/{cnpj_or_name}",
    response_model=SupplierDetailRead,
    summary="Detalhe de um fornecedor",
    description=(
        "Retorna detalhes completos de um fornecedor, incluindo resumo, "
        "lista de obras, contratos, bairros de atuação, alertas e recomendações. "
        "Aceita CNPJ ou nome do fornecedor."
    ),
)
def show(cnpj_or_name: str, db: Session = Depends(get_db)):
    """Busca detalhe de um fornecedor por CNPJ ou nome."""
    result = get_supplier_detail(db, cnpj_or_name)
    if not result:
        raise HTTPException(status_code=404, detail="Fornecedor não encontrado")
    return result
