"""
Schemas Pydantic para o endpoint de Contratos do ARGUS.

Define os modelos de resposta e filtros para a API de contratos,
derivando informações da tabela PublicWork (cada obra = 1 contrato).
"""

from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field


class ContractRead(BaseModel):
    """Schema de resposta para contratos expostos pela API."""
    id: str = Field(..., description="ID composto no formato 'work-{id}'", examples=["work-123"])
    work_id: int
    numero_contrato: str | None = None
    objeto: str | None = None
    obra_nome: str | None = None
    municipio: str | None = None
    bairro: str | None = None
    fornecedor: str | None = None
    cnpj_fornecedor: str | None = None
    secretaria: str | None = None
    valor_original: float | None = None
    valor_atual: float | None = None
    valor_pago: float | None = None
    percentual_aditivo: float | None = None
    data_inicio: date | None = None
    data_fim: date | None = None
    dias_para_vencimento: int | None = None
    status: str | None = None
    score_argus: float | None = None
    classificacao_risco: str | None = None
    alertas: int = 0
    acao_sugerida: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ContractDetailRead(ContractRead):
    """Schema de resposta detalhada para um contrato individual."""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    alertas_detalhes: list[dict] = Field(default_factory=list, description="Lista de alertas associados")
