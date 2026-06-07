"""
Schemas Pydantic para os endpoints de Análise Microterritorial de Macaé-RJ.

Define os modelos de resposta para:
- /territory/macae/overview — Visão geral territorial
- /territory/macae/neighborhoods — Lista de bairros com indicadores de risco
- /territory/macae/neighborhoods/{bairro} — Detalhe de um bairro específico
- /territory/macae/heatmap — GeoJSON para mapa de calor
- /territory/macae/data-quality — Qualidade dos dados territoriais

Todos os schemas tratam nulos com segurança e usam valores padrão
para evitar erros no frontend.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# ── 1. Overview territorial ────────────────────────────────────────────────

class TerritoryOverview(BaseModel):
    """
    Visão geral da análise microterritorial de Macaé-RJ.
    Responde: quais bairros concentram maior risco, onde estão as obras críticas,
    quais bairros têm maior valor contratado, mais atrasos, concentração de
    fornecedores e dados ruins.
    """
    municipio: str = Field("Macaé-RJ", description="Nome canônico do município")
    bairros_monitorados: int = Field(0, description="Quantidade de bairros distintos com obras")
    obras_monitoradas: int = Field(0, description="Total de obras do município")
    valor_total_contratado: float = Field(0.0, description="Soma de contract_value de todas as obras")
    score_medio: float = Field(0.0, description="Média de efficiency_score das obras com score")
    bairros_criticos: int = Field(0, description="Bairros com score médio abaixo de 40")
    obras_sem_bairro: int = Field(0, description="Obras com neighborhood nulo ou vazio")
    obras_sem_geolocalizacao: int = Field(0, description="Obras com latitude ou longitude nula")
    bairro_mais_critico: str = Field("", description="Bairro com menor score médio")
    bairro_maior_valor: str = Field("", description="Bairro com maior valor total contratado")
    bairro_mais_atrasos: str = Field("", description="Bairro com mais obras atrasadas")
    recomendacoes: list[str] = Field(default_factory=list, description="Lista de recomendações territoriais para o gestor")

    model_config = ConfigDict(from_attributes=True)


# ── 2. Item de bairro na lista ─────────────────────────────────────────────

class NeighborhoodListItem(BaseModel):
    """
    Bairro com indicadores agregados de risco territorial.
    Ordenado por maior risco (score baixo, mais obras críticas, mais alertas, maior valor).
    """
    bairro: str = Field(..., description="Nome do bairro")
    obras: int = Field(0, description="Quantidade de obras no bairro")
    valor_total: float = Field(0.0, description="Valor total contratado no bairro")
    valor_pago: float = Field(0.0, description="Valor total pago no bairro")
    score_medio: float = Field(0.0, description="Score médio das obras do bairro")
    obras_criticas: int = Field(0, description="Obras com score 0-39")
    obras_alto_risco: int = Field(0, description="Obras com score 40-59")
    obras_atrasadas: int = Field(0, description="Obras atrasadas no bairro")
    alertas_totais: int = Field(0, description="Total de alertas nas obras do bairro")
    alertas_criticos: int = Field(0, description="Alertas com severity=critical")
    fornecedores_distintos: int = Field(0, description="Fornecedores únicos no bairro")
    fornecedor_mais_recorrente: str = Field("", description="Fornecedor com mais obras no bairro")
    obras_sem_geolocalizacao: int = Field(0, description="Obras sem latitude/longitude no bairro")
    classificacao: str = Field("", description="Classificação de risco do bairro")
    recomendacao: str = Field("", description="Recomendação de ação para o bairro")

    model_config = ConfigDict(from_attributes=True)


# ── 3. Detalhe do bairro ───────────────────────────────────────────────────

class ObraResumo(BaseModel):
    """Resumo de uma obra para listas no detalhe do bairro."""
    id: int = Field(..., description="ID da obra")
    descricao: str = Field("", description="Descrição resumida da obra")
    fornecedor: str | None = Field(None, description="Nome do contratado")
    score: float | None = Field(None, description="Score ARGUS (efficiency_score)")
    classificacao: str = Field("", description="Classificação de risco")
    valor_contratado: float = Field(0.0, description="Valor do contrato")
    dias_atraso: int = Field(0, description="Dias de atraso em relação ao prazo")
    alertas: int = Field(0, description="Quantidade de alertas")

    model_config = ConfigDict(from_attributes=True)


class FornecedorResumo(BaseModel):
    """Resumo de um fornecedor no bairro."""
    nome: str = Field(..., description="Nome do fornecedor")
    cnpj: str | None = Field(None, description="CNPJ do fornecedor")
    obras: int = Field(0, description="Quantidade de obras no bairro")
    valor_total: float = Field(0.0, description="Valor total contratado")
    score_medio: float = Field(0.0, description="Score médio das obras")

    model_config = ConfigDict(from_attributes=True)


class AlertaResumo(BaseModel):
    """Resumo de um alerta no bairro."""
    obra_id: int = Field(..., description="ID da obra")
    code: str = Field(..., description="Código do alerta")
    severity: str = Field(..., description="Severidade do alerta")
    message: str = Field("", description="Mensagem do alerta")

    model_config = ConfigDict(from_attributes=True)


class BairroResumo(BaseModel):
    """Resumo numérico do bairro para o detalhe."""
    obras: int = Field(0)
    valor_total: float = Field(0.0)
    valor_pago: float = Field(0.0)
    score_medio: float = Field(0.0)
    obras_criticas: int = Field(0)
    obras_alto_risco: int = Field(0)
    obras_atrasadas: int = Field(0)
    alertas_totais: int = Field(0)
    alertas_criticos: int = Field(0)
    fornecedores_distintos: int = Field(0)
    classificacao: str = Field("")

    model_config = ConfigDict(from_attributes=True)


class NeighborhoodDetail(BaseModel):
    """
    Detalhe completo de um bairro com obras críticas, atrasadas,
    fornecedores, alertas, análise textual e ações recomendadas.
    """
    bairro: str = Field(..., description="Nome do bairro")
    resumo: BairroResumo = Field(..., description="Resumo numérico do bairro")
    obras_criticas: list[ObraResumo] = Field(default_factory=list, description="Obras críticas do bairro (score 0-39)")
    obras_atrasadas: list[ObraResumo] = Field(default_factory=list, description="Obras atrasadas do bairro")
    principais_fornecedores: list[FornecedorResumo] = Field(default_factory=list, description="Top fornecedores do bairro")
    alertas: list[AlertaResumo] = Field(default_factory=list, description="Alertas recentes do bairro")
    analise_textual: str = Field("", description="Análise textual automática do bairro")
    acoes_recomendadas: list[str] = Field(default_factory=list, description="Ações recomendadas para o gestor")

    model_config = ConfigDict(from_attributes=True)


# ── 4. Heatmap (GeoJSON) ──────────────────────────────────────────────────

class HeatmapFeatureProperties(BaseModel):
    """Propriedades de cada feature no GeoJSON do heatmap."""
    obra_id: int = Field(..., description="ID da obra")
    nome: str = Field("", description="Descrição resumida da obra")
    bairro: str = Field("Não informado", description="Bairro da obra")
    score: float | None = Field(None, description="Score ARGUS")
    classificacao: str = Field("", description="Classificação de risco")
    valor_contratado: float = Field(0.0, description="Valor do contrato")
    alertas: int = Field(0, description="Quantidade de alertas")
    dias_atraso: int = Field(0, description="Dias de atraso")
    fornecedor: str | None = Field(None, description="Nome do fornecedor")

    model_config = ConfigDict(from_attributes=True)


class HeatmapResponse(BaseModel):
    """Resposta GeoJSON FeatureCollection para o heatmap territorial."""
    type: str = Field("FeatureCollection", description="Tipo GeoJSON")
    features: list[dict] = Field(default_factory=list, description="Lista de features GeoJSON")

    model_config = ConfigDict(from_attributes=True)


# ── 5. Data Quality ────────────────────────────────────────────────────────

class ObraDataQualityIssue(BaseModel):
    """Obra com problemas de qualidade de dados para saneamento."""
    id: int = Field(..., description="ID da obra")
    descricao: str = Field("", description="Descrição resumida da obra")
    bairro: str = Field("Não informado", description="Bairro da obra")
    problemas: list[str] = Field(default_factory=list, description="Lista de problemas encontrados")

    model_config = ConfigDict(from_attributes=True)


class DataQualityReport(BaseModel):
    """
    Relatório de qualidade dos dados territoriais de Macaé-RJ.
    Identifica obras com campos obrigatórios faltantes para saneamento cadastral.
    """
    total_obras: int = Field(0, description="Total de obras do município")
    obras_sem_bairro: int = Field(0, description="Obras com neighborhood nulo ou vazio")
    obras_sem_geolocalizacao: int = Field(0, description="Obras com latitude ou longitude nula")
    obras_sem_valor: int = Field(0, description="Obras sem contract_value ou igual a zero")
    obras_sem_fornecedor: int = Field(0, description="Obras sem contractor_name")
    obras_sem_prazo: int = Field(0, description="Obras sem due_at")
    data_quality_score: float = Field(0.0, description="Score de qualidade dos dados (0-100)")
    obras_para_saneamento: list[ObraDataQualityIssue] = Field(
        default_factory=list,
        description="Lista de obras que precisam de saneamento cadastral",
    )

    model_config = ConfigDict(from_attributes=True)
