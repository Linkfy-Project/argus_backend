"""
Schemas Pydantic para o endpoint de Alertas do ARGUS.

Define os modelos de resposta e filtros para a API de alertas,
derivando informações da tabela Alert + PublicWork associada.
"""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


# ── Status permitidos para alertas ──────────────────────────────────────
ALLOWED_ALERT_STATUSES = ["Novo", "Em análise", "Encaminhado", "Resolvido", "Descartado"]


# ── Mapa de código → tipo legível ────────────────────────────────────────
ALERT_CODE_TO_TIPO: dict[str, str] = {
    "CRITICAL_DELAY": "Atraso crítico",
    "COST_OVERRUN": "Estouro de custo",
    "HIGH_ADDITIVE": "Aditivo elevado",
    "NO_CONTRACT_NUMBER": "Sem número de contrato",
    "LOW_IDH": "IDH baixo na região",
    "CREA_GRAVE": "Infração CREA grave",
    "CREA_MEDIUM": "Infração CREA moderada",
    "CREA_MULTIPLE": "Múltiplas infrações CREA",
    "TERRITORIAL_OVERLAP": "Sobreposição territorial",
    "LONG_OVERDUE": "Atraso prolongado",
    "UNFINISHED_NO_DUE": "Sem previsão de conclusão",
    "HIGH_RISK_ML": "Alto risco preditivo",
    "ML_DELAY_RISK": "Risco de atraso (ML)",
    "ML_COST_RISK": "Risco de estouro (ML)",
    "ML_REWORK_RISK": "Risco de retrabalho (ML)",
    # Códigos reais do scoring.py
    "CRITICAL_AUDITORIA": "Desvio crítico de custo",
    "ALERT_SOBREPRECO": "Sobrepreço detectado",
    "WARNING_CUSTO": "Desvio de custo",
    "CRITICAL_TETO_LEGAL_ADITIVOS": "Aditivos acima do teto legal",
    "ALERT_ADITIVO_CUSTO": "Aditivo elevado de custo",
    "ALERT_BAIXA_EXECUCAO_CUSTO": "Baixa execução financeira",
    "WARNING_TRANSPARENCIA_CUSTO": "Risco de transparência",
    "ALERT_DADOS_FINANCEIROS_AUSENTES": "Dados financeiros ausentes",
    "CRITICAL_DELAY_DAYS": "Atraso crítico (dias)",
    "ALERT_DELAY_DAYS": "Atraso significativo",
    "WARNING_DELAY_DAYS": "Atraso moderado",
    "ALERT_NO_DUE_DATE": "Sem data de vencimento",
    "CRITICAL_CREA_GRAVE": "Infração CREA grave",
    "ALERT_CREA_MEDIUM": "Infração CREA moderada",
    "CREA_PATTERNS_SUSPECT": "Padrão suspeito CREA",
    "WARNING_CREA": "Infração CREA leve",
    "ML_HIGH_DELAY_RISK": "Alto risco de atraso (ML)",
    "ML_HIGH_COST_RISK": "Alto risco de custo (ML)",
    "ML_HIGH_REWORK_RISK": "Alto risco de retrabalho (ML)",
    "CRITICAL_OVERLAP": "Sobreposição territorial crítica",
    "ALERT_OVERLAP": "Sobreposição territorial detectada",
}

# ── Mapa de severidade → nível legível ──────────────────────────────────
SEVERITY_TO_NIVEL: dict[str, str] = {
    "info": "Informativo",
    "warning": "Atenção",
    "alert": "Alerta",
    "critical": "Crítico",
}

# ── Motivos sugeridos por código de alerta ──────────────────────────────
ALERT_CODE_TO_MOTIVO: dict[str, str] = {
    "CRITICAL_DELAY": "Prazo vencido e obra sem conclusão registrada.",
    "COST_OVERRUN": "Valor executado excede significativamente o valor contratado.",
    "HIGH_ADDITIVE": "Percentual de aditivos contratuais acima do recomendado.",
    "NO_CONTRACT_NUMBER": "Obra registrada sem número de contrato formal.",
    "LOW_IDH": "Obra localizada em região com IDH muito baixo.",
    "CREA_GRAVE": "Infração grave registrada no CREA para o contratado.",
    "CREA_MEDIUM": "Infração moderada registrada no CREA para o contratado.",
    "CREA_MULTIPLE": "Contratado acumula múltiplas infrações no CREA.",
    "TERRITORIAL_OVERLAP": "Sobreposição territorial detectada entre obras do mesmo contratado.",
    "LONG_OVERDUE": "Obra com atraso superior a 90 dias do prazo contratual.",
    "UNFINISHED_NO_DUE": "Obra sem data de conclusão e sem previsão definida.",
    "HIGH_RISK_ML": "Modelo preditivo indica alto risco de problemas.",
    "ML_DELAY_RISK": "Modelo de machine learning indica risco elevado de atraso.",
    "ML_COST_RISK": "Modelo de machine learning indica risco elevado de estouro de custo.",
    "ML_REWORK_RISK": "Modelo de machine learning indica risco elevado de retrabalho.",
}

# ── Ações sugeridas por código de alerta ────────────────────────────────
ALERT_CODE_TO_ACAO: dict[str, str] = {
    "CRITICAL_DELAY": "Solicitar replanejamento físico-financeiro e priorizar vistoria.",
    "COST_OVERRUN": "Auditar aditivos e solicitar justificativa documental ao contratado.",
    "HIGH_ADDITIVE": "Revisar fundamentação legal dos aditivos e comparar com mercado.",
    "NO_CONTRACT_NUMBER": "Regularizar documentação contratual junto à secretaria responsável.",
    "LOW_IDH": "Priorizar conclusão da obra devido ao impacto social elevado.",
    "CREA_GRAVE": "Exigir apresentação de ART atualizada e vistoriar obra.",
    "CREA_MEDIUM": "Notificar contratado para regularização junto ao CREA.",
    "CREA_MULTIPLE": "Avaliar desqualificação do contratado e reforçar fiscalização.",
    "TERRITORIAL_OVERLAP": "Verificar se há duplicidade de objetos contratuais.",
    "LONG_OVERDUE": "Convocar reunião de replanejamento com secretaria e contratado.",
    "UNFINISHED_NO_DUE": "Definir novo cronograma e registrar previsão de conclusão.",
    "HIGH_RISK_ML": "Reforçar fiscalização e solicitar relatório de avanço físico.",
    "ML_DELAY_RISK": "Antecipar vistoria e solicitar plano de recuperação de prazo.",
    "ML_COST_RISK": "Revisar medições e comparar com curva S de referência.",
    "ML_REWORK_RISK": "Verificar qualidade dos serviços executados com amostragem em campo.",
}


class AlertStatusUpdate(BaseModel):
    """Payload para atualização de status de um alerta."""
    status: str = Field(..., description="Novo status do alerta", examples=["Em análise"])


class AlertRead(BaseModel):
    """Schema de resposta para alertas expostos pela API."""
    id: int
    work_id: int
    tipo: str
    code: str
    severity: str
    nivel: str
    status: str
    obra_nome: str | None = None
    municipio: str | None = None
    bairro: str | None = None
    fornecedor: str | None = None
    descricao: str | None = None
    motivo: str | None = None
    acao_sugerida: str | None = None
    data_deteccao: datetime | None = None
    score_argus: float | None = None
    valor_contratado: float | None = None

    model_config = ConfigDict(from_attributes=True)
