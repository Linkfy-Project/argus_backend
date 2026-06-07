"""
Schemas Pydantic para os endpoints do Dashboard Executivo do ARGUS.

Define os modelos de resposta para:
- /dashboard/summary — KPIs agregados do painel executivo
- /dashboard/priority-queue — Fila priorizada de obras que precisam de atenção
- /dashboard/risk-distribution — Distribuição de obras por faixa de risco
- /dashboard/top-neighborhoods-risk — Ranking de bairros com maior risco
- /dashboard/top-suppliers-risk — Ranking de fornecedores com maior risco
"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


# ── 1. Summary (painel executivo) ──────────────────────────────────────────

class DashboardSummary(BaseModel):
    """
    Resumo executivo com todos os KPIs que o frontend precisa
    para montar o painel sem calcular nada no navegador.
    """
    municipio: str = Field(..., description="Nome canônico do município (ex: Macaé-RJ)")
    ultima_atualizacao: str = Field(..., description="Data/hora da última atualização ISO 8601")
    obras_monitoradas: int = Field(0, description="Total de obras no município")
    valor_total_contratado: float = Field(0.0, description="Soma de contract_value de todas as obras")
    valor_total_pago: float = Field(0.0, description="Soma de paid_value de todas as obras")
    valor_potencial_em_risco: float = Field(0.0, description="Soma de contract_value de obras com score < 60")
    obras_criticas: int = Field(0, description="Obras com score entre 0 e 39")
    obras_alto_risco: int = Field(0, description="Obras com score entre 40 e 59")
    obras_em_atencao: int = Field(0, description="Obras com score entre 60 e 79")
    obras_eficientes: int = Field(0, description="Obras com score entre 80 e 100")
    obras_atrasadas: int = Field(0, description="Obras sem finished_at, com due_at preenchido e anterior a hoje")
    obras_sem_geolocalizacao: int = Field(0, description="Obras com latitude ou longitude nula")
    contratos_com_aditivos_altos: int = Field(0, description="Obras onde additive_value/contract_value > 25%%")
    alertas_criticos: int = Field(0, description="Total de alertas com severity=critical")
    alertas_totais: int = Field(0, description="Total de alertas de qualquer severidade")
    fornecedores_monitorados: int = Field(0, description="Fornecedores únicos (contractor_document não nulo)")
    bairros_monitorados: int = Field(0, description="Bairros únicos (neighborhood não nulo)")
    score_medio: float = Field(0.0, description="Média de efficiency_score das obras com score")
    data_quality_score: float = Field(0.0, description="Percentual 0-100 de completude dos dados")

    model_config = ConfigDict(from_attributes=True)


# ── 2. Priority Queue ──────────────────────────────────────────────────────

class PriorityQueueItem(BaseModel):
    """
    Item da fila priorizada de obras que o gestor deve avaliar primeiro.
    """
    prioridade: int = Field(..., description="Posição na fila (1 = mais urgente)")
    obra_id: int = Field(..., description="ID da obra")
    obra: str = Field("", description="Descrição resumida da obra")
    bairro: str | None = Field(None, description="Bairro da obra")
    secretaria: str | None = Field(None, description="Unidade gestora da obra")
    fornecedor: str | None = Field(None, description="Nome do contratado")
    score_argus: float | None = Field(None, description="Score ARGUS (efficiency_score)")
    classificacao_risco: str = Field(..., description="Crítico / Alto risco / Atenção / Eficiente / Sem dados suficientes")
    valor_contratado: float = Field(0.0, description="Valor do contrato")
    valor_em_risco_estimado: float = Field(0.0, description="Estimativa de valor em risco")
    dias_atraso: int = Field(0, description="Dias de atraso em relação ao prazo")
    alertas_ativos: int = Field(0, description="Quantidade de alertas ativos na obra")
    motivo_principal: str = Field("", description="Justificativa da prioridade")
    acao_sugerida: str = Field("", description="Ação recomendada para o gestor")

    model_config = ConfigDict(from_attributes=True)


# ── 3. Risk Distribution ───────────────────────────────────────────────────

class RiskDistributionItem(BaseModel):
    """
    Faixa de risco com contagem de obras.
    """
    label: str = Field(..., description="Nome da faixa (Eficiente, Atenção, etc.)")
    min: int | None = Field(None, description="Score mínimo da faixa (null para Sem score)")
    max: int | None = Field(None, description="Score máximo da faixa (null para Sem score)")
    total: int = Field(0, description="Quantidade de obras na faixa")

    model_config = ConfigDict(from_attributes=True)


# ── 4. Top Neighborhoods Risk ──────────────────────────────────────────────

class NeighborhoodRiskItem(BaseModel):
    """
    Bairro com indicadores agregados de risco.
    """
    bairro: str = Field(..., description="Nome do bairro")
    obras: int = Field(0, description="Quantidade de obras no bairro")
    score_medio: float = Field(0.0, description="Score médio das obras do bairro")
    obras_criticas: int = Field(0, description="Obras com score 0-39")
    obras_atrasadas: int = Field(0, description="Obras atrasadas no bairro")
    valor_total: float = Field(0.0, description="Valor total contratado no bairro")
    alertas: int = Field(0, description="Total de alertas nas obras do bairro")
    classificacao: str = Field(..., description="Classificação de risco do bairro")
    recomendacao: str = Field("", description="Recomendação de ação para o bairro")

    model_config = ConfigDict(from_attributes=True)


# ── 5. Top Suppliers Risk ──────────────────────────────────────────────────

class SupplierRiskItem(BaseModel):
    """
    Fornecedor com indicadores agregados de risco.
    """
    fornecedor: str = Field(..., description="Nome do fornecedor")
    cnpj: str | None = Field(None, description="CNPJ do fornecedor")
    contratos: int = Field(0, description="Quantidade de contratos")
    valor_total: float = Field(0.0, description="Valor total contratado")
    score_medio: float = Field(0.0, description="Score médio das obras do fornecedor")
    obras_criticas: int = Field(0, description="Obras com score 0-39")
    alertas: int = Field(0, description="Total de alertas nas obras do fornecedor")
    aditivo_medio_percentual: float = Field(0.0, description="Percentual médio de aditivos contratuais")
    classificacao: str = Field(..., description="Classificação de risco do fornecedor")
    recomendacao: str = Field("", description="Recomendação de ação para o fornecedor")

    model_config = ConfigDict(from_attributes=True)
