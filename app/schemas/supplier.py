"""
Schemas Pydantic para o endpoint de Fornecedores do ARGUS.

Define os modelos de resposta e filtros para a API de fornecedores,
agregando dados de múltiplas obras (PublicWork) por contratado.
"""

from pydantic import BaseModel, ConfigDict, Field


class SupplierRankingRead(BaseModel):
    """Schema de resposta para ranking de fornecedores."""
    fornecedor: str
    cnpj: str | None = None
    contratos: int = 0
    obras: int = 0
    valor_total: float = 0.0
    valor_pago: float = 0.0
    score_medio: float | None = None
    obras_criticas: int = 0
    obras_atrasadas: int = 0
    alertas_totais: int = 0
    alertas_criticos: int = 0
    aditivo_medio_percentual: float = 0.0
    bairros_atuacao: list[str] = Field(default_factory=list)
    classificacao: str = "Não classificado"
    recomendacao: str = ""

    model_config = ConfigDict(from_attributes=True)


class SupplierDetailRead(BaseModel):
    """Schema de resposta detalhada para um fornecedor individual."""
    fornecedor: str
    cnpj: str | None = None
    contratos: int = 0
    obras: int = 0
    valor_total: float = 0.0
    valor_pago: float = 0.0
    score_medio: float | None = None
    obras_criticas: int = 0
    obras_atrasadas: int = 0
    alertas_totais: int = 0
    alertas_criticos: int = 0
    aditivo_medio_percentual: float = 0.0
    bairros_atuacao: list[str] = Field(default_factory=list)
    classificacao: str = "Não classificado"
    recomendacao: str = ""
    # Campos extras para detalhe
    obras_lista: list[dict] = Field(default_factory=list, description="Lista de obras do fornecedor")
    contratos_lista: list[dict] = Field(default_factory=list, description="Lista de contratos do fornecedor")
    alertas_lista: list[dict] = Field(default_factory=list, description="Lista de alertas do fornecedor")

    model_config = ConfigDict(from_attributes=True)
