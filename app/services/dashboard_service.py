"""
Serviço de Dashboard Executivo do ARGUS.

Centraliza toda a lógica de negócio para os endpoints de dashboard executivo.
Cada função retorna dados prontos para o frontend, sem necessidade de cálculo no navegador.

Responsabilidades:
- Calcular KPIs agregados (summary)
- Montar fila priorizada de obras (priority-queue)
- Distribuição de obras por faixa de risco (risk-distribution)
- Ranking de bairros com maior risco (top-neighborhoods-risk)
- Ranking de fornecedores com maior risco (top-suppliers-risk)

Todas as funções tratam nulos com segurança e retornam zeros/listas vazias
quando não há dados, evitando erros 500.
"""

from __future__ import annotations

import logging
import unicodedata
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, case, literal_column
from sqlalchemy.orm import Session, joinedload

from app.models.work import PublicWork, Alert
from app.utils.obra_filter import filter_obras_query
from app.schemas.dashboard import (
    DashboardSummary,
    PriorityQueueItem,
    RiskDistributionItem,
    NeighborhoodRiskItem,
    SupplierRiskItem,
)

logger = logging.getLogger(__name__)


# ── Constantes de classificação de risco ───────────────────────────────────
# Faixas do score ARGUS:
#   80-100 = Eficiente
#   60-79  = Atenção
#   40-59  = Alto risco
#   0-39   = Crítico
#   null   = Sem dados suficientes

RISK_LABELS = {
    "eficiente": "Eficiente",
    "atencao": "Atenção",
    "alto_risco": "Alto risco",
    "critico": "Crítico",
    "sem_dados": "Sem dados suficientes",
}


def _normalize_municipio(raw: str) -> str:
    """
    Normaliza o nome do município para busca case-insensitive e sem acentos.
    Remove acentos e converte para lowercase para comparação com func.unaccent().

    Args:
        raw: Nome do município informado pelo usuário (ex: "Macae").

    Returns:
        Nome normalizado sem acentos em lowercase (ex: "macae").
    """
    if not raw:
        return raw
    cleaned = unicodedata.normalize("NFD", raw)
    cleaned = "".join(c for c in cleaned if unicodedata.category(c) != "Mn")
    return cleaned.strip().lower()


def _canonical_municipio(raw: str) -> str:
    """
    Retorna o nome canônico do município para exibição no resultado.
    Mapeia variações conhecidas para o formato oficial.

    Args:
        raw: Nome bruto do município vindo do banco ou do parâmetro.

    Returns:
        Nome canônico formatado (ex: "Macaé-RJ").
    """
    if not raw:
        return "Macaé-RJ"
    cleaned = unicodedata.normalize("NFD", raw)
    cleaned = "".join(c for c in cleaned if unicodedata.category(c) != "Mn")
    cleaned = cleaned.strip().lower()
    canonical_map = {"macae": "Macaé-RJ", "macaé": "Macaé-RJ"}
    return canonical_map.get(cleaned, raw.strip())


def _apply_municipio_filter(q, municipio: str | None):
    """
    Aplica filtro de município na query usando func.unaccent() para
    comparação insensível a acentos.
    Também aplica filtro de obras (exclui registros classificados como não-obra).

    Args:
        q: Query SQLAlchemy.
        municipio: Nome do município para filtrar (opcional).

    Returns:
        Query com filtros aplicados.
    """
    # Aplica filtro para excluir registros classificados como não-obra (is_obra=0)
    q = filter_obras_query(q)
    if municipio:
        normalized = _normalize_municipio(municipio)
        logger.debug("DEBUG: _apply_municipio_filter - filtro='%s' normalizado='%s'", municipio, normalized)
        q = q.filter(func.unaccent(PublicWork.municipio).ilike(f"%{normalized}%"))
    return q


def _classify_score(score: float | None) -> str:
    """
    Classifica o score em uma faixa de risco.

    Args:
        score: Valor do efficiency_score (0-100) ou None.

    Returns:
        Rótulo da classificação (Eficiente, Atenção, Alto risco, Crítico, Sem dados suficientes).
    """
    if score is None:
        return RISK_LABELS["sem_dados"]
    if score >= 80:
        return RISK_LABELS["eficiente"]
    if score >= 60:
        return RISK_LABELS["atencao"]
    if score >= 40:
        return RISK_LABELS["alto_risco"]
    return RISK_LABELS["critico"]


def _is_overdue(work: PublicWork, today: date | None = None) -> bool:
    """
    Verifica se uma obra está atrasada.
    Regra: finished_at nulo, due_at preenchido e due_at menor que hoje.

    Args:
        work: Instância de PublicWork.
        today: Data de referência (padrão: hoje).

    Returns:
        True se a obra está atrasada.
    """
    today = today or date.today()
    return (
        work.finished_at is None
        and work.due_at is not None
        and work.due_at < today
    )


def _compute_delay_days(work: PublicWork, today: date | None = None) -> int:
    """
    Calcula quantos dias de atraso a obra acumula.
    Retorna 0 se não está atrasada.

    Args:
        work: Instância de PublicWork.
        today: Data de referência (padrão: hoje).

    Returns:
        Número de dias de atraso (0 se não atrasada).
    """
    today = today or date.today()
    if not _is_overdue(work, today):
        return 0
    return (today - work.due_at).days


def _has_high_additive(work: PublicWork) -> bool:
    """
    Verifica se a obra tem aditivo contratual acima de 25%.
    Regra: additive_value / contract_value > 0.25 quando ambos existirem.

    Args:
        work: Instância de PublicWork.

    Returns:
        True se o aditivo excede 25% do valor contratado.
    """
    if work.contract_value and work.contract_value > 0 and work.additive_value is not None:
        return (work.additive_value / work.contract_value) > 0.25
    return False


def _additive_percent(work: PublicWork) -> float:
    """
    Calcula o percentual de aditivo contratual.
    Retorna 0.0 se não for possível calcular.

    Args:
        work: Instância de PublicWork.

    Returns:
        Percentual do aditivo em relação ao valor contratado (0.0 a N).
    """
    if work.contract_value and work.contract_value > 0 and work.additive_value is not None:
        return round((work.additive_value / work.contract_value) * 100, 1)
    return 0.0


# ── 1. Summary ─────────────────────────────────────────────────────────────

def get_dashboard_summary(db: Session, municipio: str | None = None) -> DashboardSummary:
    """
    Calcula o resumo executivo com todos os KPIs para o painel do gestor.

    Regras:
    - Score alto é bom.
    - 80-100 = eficiente, 60-79 = atenção, 40-59 = alto risco, 0-39 = crítico.
    - Obra atrasada: finished_at nulo, due_at preenchido e due_at < hoje.
    - Obra sem geolocalização: latitude ou longitude nula.
    - Aditivo alto: additive_value / contract_value > 0.25.
    - Valor potencial em risco: soma de contract_value das obras com score < 60.
    - data_quality_score: percentual de obras com valor, prazo, fornecedor,
      bairro e geolocalização preenchidos.

    Args:
        db: Sessão do banco de dados.
        municipio: Nome do município para filtrar (opcional, default "Macae").

    Returns:
        DashboardSummary com todos os KPIs preenchidos.
    """
    today = date.today()
    logger.debug("DEBUG: get_dashboard_summary - municipio='%s'", municipio)

    # Query base com filtro de município
    q = _apply_municipio_filter(db.query(PublicWork), municipio)
    # Materializa todas as obras do município para cálculos em memória
    # (necessário porque muitos cálculos dependem de lógica Python, não SQL puro)
    works = q.options(joinedload(PublicWork.alerts)).all()
    total_works = len(works)

    logger.debug("DEBUG: get_dashboard_summary - total_works=%d", total_works)

    # Se não há obras, retorna tudo zerado
    if total_works == 0:
        return DashboardSummary(
            municipio=_canonical_municipio(municipio or "Macae"),
            ultima_atualizacao=datetime.now(tz=None).isoformat(),
            obras_monitoradas=0,
        )

    # ── Contadores de classificação de risco ──
    obras_criticas = 0
    obras_alto_risco = 0
    obras_em_atencao = 0
    obras_eficientes = 0
    obras_atrasadas = 0
    obras_sem_geolocalizacao = 0
    contratos_com_aditivos_altos = 0

    # ── Acumuladores financeiros ──
    valor_total_contratado = 0.0
    valor_total_pago = 0.0
    valor_potencial_em_risco = 0.0

    # ── Acumuladores de alertas ──
    alertas_criticos = 0
    alertas_totais = 0

    # ── Conjuntos para contagem única ──
    fornecedores_set: set[str] = set()
    bairros_set: set[str] = set()

    # ── Acumuladores para data_quality_score ──
    # Critérios: valor, prazo, fornecedor, bairro e geolocalização preenchidos
    quality_fields_filled = 0
    quality_fields_total = total_works * 5  # 5 campos por obra

    # ── Scores para média ──
    scores_list: list[float] = []

    for work in works:
        score = work.efficiency_score

        # Classificação de risco
        if score is not None:
            scores_list.append(float(score))
            if score >= 80:
                obras_eficientes += 1
            elif score >= 60:
                obras_em_atencao += 1
            elif score >= 40:
                obras_alto_risco += 1
            else:
                obras_criticas += 1

        # Atraso
        if _is_overdue(work, today):
            obras_atrasadas += 1

        # Geolocalização
        if work.latitude is None or work.longitude is None:
            obras_sem_geolocalizacao += 1

        # Aditivo alto
        if _has_high_additive(work):
            contratos_com_aditivos_altos += 1

        # Valores financeiros
        valor_total_contratado += float(work.contract_value or 0)
        valor_total_pago += float(work.paid_value or 0)

        # Valor potencial em risco: obras com score < 60
        if score is not None and score < 60:
            valor_potencial_em_risco += float(work.contract_value or 0)

        # Alertas
        if work.alerts:
            alertas_totais += len(work.alerts)
            alertas_criticos += sum(1 for a in work.alerts if a.severity == "critical")

        # Fornecedores e bairros únicos
        if work.contractor_document:
            fornecedores_set.add(work.contractor_document)
        if work.neighborhood:
            bairros_set.add(work.neighborhood)

        # Data quality: verifica se cada campo está preenchido
        if work.contract_value is not None and work.contract_value > 0:
            quality_fields_filled += 1
        if work.due_at is not None:
            quality_fields_filled += 1
        if work.contractor_name:
            quality_fields_filled += 1
        if work.neighborhood:
            quality_fields_filled += 1
        if work.latitude is not None and work.longitude is not None:
            quality_fields_filled += 1

    # Cálculos finais
    score_medio = round(sum(scores_list) / len(scores_list), 2) if scores_list else 0.0
    data_quality_score = round((quality_fields_filled / quality_fields_total) * 100, 1) if quality_fields_total > 0 else 0.0

    logger.debug(
        "DEBUG: get_dashboard_summary - criticas=%d, alto_risco=%d, atencao=%d, eficientes=%d",
        obras_criticas, obras_alto_risco, obras_em_atencao, obras_eficientes,
    )

    return DashboardSummary(
        municipio=_canonical_municipio(municipio or "Macae"),
        ultima_atualizacao=datetime.now(tz=None).isoformat(),
        obras_monitoradas=total_works,
        valor_total_contratado=round(valor_total_contratado, 2),
        valor_total_pago=round(valor_total_pago, 2),
        valor_potencial_em_risco=round(valor_potencial_em_risco, 2),
        obras_criticas=obras_criticas,
        obras_alto_risco=obras_alto_risco,
        obras_em_atencao=obras_em_atencao,
        obras_eficientes=obras_eficientes,
        obras_atrasadas=obras_atrasadas,
        obras_sem_geolocalizacao=obras_sem_geolocalizacao,
        contratos_com_aditivos_altos=contratos_com_aditivos_altos,
        alertas_criticos=alertas_criticos,
        alertas_totais=alertas_totais,
        fornecedores_monitorados=len(fornecedores_set),
        bairros_monitorados=len(bairros_set),
        score_medio=score_medio,
        data_quality_score=data_quality_score,
    )


# ── 2. Priority Queue ─────────────────────────────────────────────────────

def _compute_priority_score(
    work: PublicWork,
    alerts_count: int,
    critical_alerts_count: int,
    today: date,
) -> float:
    """
    Calcula um score de prioridade para ordenar obras na fila.
    Quanto MAIOR o score de prioridade, mais urgente a obra.

    Fatores que aumentam prioridade:
    - Score baixo (inverso: 100 - score)
    - Alertas críticos (peso alto)
    - Atraso em dias
    - Aditivo acima de 25%
    - Valor contratado alto (normalizado)
    - Falta de geolocalização (penalidade menor)

    Args:
        work: Instância de PublicWork.
        alerts_count: Total de alertas da obra.
        critical_alerts_count: Total de alertas críticos da obra.
        today: Data de referência.

    Returns:
        Score de prioridade (quanto maior, mais urgente).
    """
    priority = 0.0

    # Score baixo aumenta prioridade (inverso)
    score = work.efficiency_score
    if score is not None:
        priority += (100 - score) * 2.0  # Peso alto para score baixo
    else:
        priority += 150.0  # Sem score = prioridade alta para saneamento

    # Alertas críticos
    priority += critical_alerts_count * 30.0
    priority += alerts_count * 3.0

    # Atraso
    delay = _compute_delay_days(work, today)
    priority += min(delay, 365) * 0.5  # Cap em 365 dias para não distorcer

    # Aditivo alto
    if _has_high_additive(work):
        priority += 40.0

    # Valor contratado alto (normalizado: cada R$ 100.000 = +5 pontos)
    value = float(work.contract_value or 0)
    priority += min(value / 100_000, 500) * 5.0  # Cap para não distorcer

    # Falta de geolocalização (penalidade menor, não deve superar obra crítica)
    if work.latitude is None or work.longitude is None:
        priority += 10.0

    return priority


def _build_action_and_reason(
    work: PublicWork,
    alerts_count: int,
    critical_alerts_count: int,
    delay_days: int,
    has_high_additive: bool,
    has_score: bool,
) -> tuple[str, str]:
    """
    Gera o motivo principal e a ação sugerida para uma obra na fila de prioridade.

    Args:
        work: Instância de PublicWork.
        alerts_count: Total de alertas.
        critical_alerts_count: Total de alertas críticos.
        delay_days: Dias de atraso.
        has_high_additive: Se tem aditivo alto.
        has_score: Se tem score calculado.

    Returns:
        Tupla (motivo_principal, acao_sugerida).
    """
    reasons = []
    actions = []

    if not has_score:
        reasons.append("Dados insuficientes para cálculo do score")
        actions.append("Saneamento cadastral: preencher campos obrigatórios (valor, prazo, fornecedor, geolocalização).")
        return " | ".join(reasons), " ".join(actions)

    if critical_alerts_count > 0:
        reasons.append(f"{critical_alerts_count} alerta(s) crítico(s)")

    if delay_days > 0:
        reasons.append(f"Atraso de {delay_days} dias")

    score = work.efficiency_score
    if score is not None and score < 40:
        reasons.append("Score crítico")
    elif score is not None and score < 60:
        reasons.append("Score baixo (alto risco)")

    if has_high_additive:
        reasons.append("Aditivo contratual acima de 25%")

    if not reasons:
        reasons.append("Indicadores de risco moderados")

    # Ações sugeridas baseadas nos problemas encontrados
    if delay_days > 0 and score is not None and score < 60:
        actions.append("Priorizar vistoria técnica e solicitar replanejamento físico-financeiro.")
    elif delay_days > 0:
        actions.append("Verificar causas do atraso e solicitar cronograma atualizado.")
    elif score is not None and score < 40:
        actions.append("Realizar auditoria técnica detalhada e revisar contrato.")
    elif score is not None and score < 60:
        actions.append("Acompanhar de perto e solicitar relatório de progresso.")
    elif critical_alerts_count > 0:
        actions.append("Investigar alertas críticos e tomar medidas corretivas.")
    else:
        actions.append("Monitorar indicadores periodicamente.")

    return " | ".join(reasons), " ".join(actions)


def get_priority_queue(
    db: Session,
    municipio: str | None = None,
    limit: int = 10,
) -> list[PriorityQueueItem]:
    """
    Monta a fila priorizada de obras que o gestor deve avaliar primeiro.

    Critério de prioridade (combinado em _compute_priority_score):
    - Score baixo aumenta prioridade.
    - Alertas críticos aumentam prioridade.
    - Atraso aumenta prioridade.
    - Aditivo acima de 25% aumenta prioridade.
    - Valor contratado alto aumenta prioridade.
    - Falta de geolocalização aumenta prioridade (penalidade menor).
    - Obras sem score entram como "Sem dados suficientes".

    Args:
        db: Sessão do banco de dados.
        municipio: Nome do município para filtrar (opcional).
        limit: Número máximo de obras na fila (default 10).

    Returns:
        Lista de PriorityQueueItem ordenada por prioridade (mais urgente primeiro).
    """
    today = date.today()
    logger.debug("DEBUG: get_priority_queue - municipio='%s', limit=%d", municipio, limit)

    # Busca obras com alertas carregados
    q = _apply_municipio_filter(db.query(PublicWork), municipio)
    works = q.options(joinedload(PublicWork.alerts)).all()

    logger.debug("DEBUG: get_priority_queue - obras encontradas=%d", len(works))

    if not works:
        return []

    # Calcula prioridade para cada obra
    scored_works: list[tuple[float, PublicWork, int, int]] = []
    for work in works:
        alerts_list = work.alerts or []
        alerts_count = len(alerts_list)
        critical_count = sum(1 for a in alerts_list if a.severity == "critical")

        priority = _compute_priority_score(work, alerts_count, critical_count, today)
        scored_works.append((priority, work, alerts_count, critical_count))

    # Ordena por prioridade (maior primeiro)
    scored_works.sort(key=lambda x: x[0], reverse=True)

    # Limita ao número solicitado
    top_works = scored_works[:limit]

    # Monta a resposta
    result: list[PriorityQueueItem] = []
    for rank, (priority, work, alerts_count, critical_count) in enumerate(top_works, start=1):
        has_score = work.efficiency_score is not None
        delay = _compute_delay_days(work, today)
        high_additive = _has_high_additive(work)

        # Valor em risco estimado:
        # - Se score < 60: usa contract_value (valor total potencialmente em risco)
        # - Caso contrário: estima 20% do contract_value como risco conservador
        contract_val = float(work.contract_value or 0)
        if work.efficiency_score is not None and work.efficiency_score < 60:
            valor_risco = contract_val
        elif work.efficiency_score is not None:
            valor_risco = round(contract_val * 0.2, 2)
        else:
            valor_risco = round(contract_val * 0.5, 2)  # Sem score = risco desconhecido

        motivo, acao = _build_action_and_reason(
            work, alerts_count, critical_count, delay, high_additive, has_score,
        )

        result.append(PriorityQueueItem(
            prioridade=rank,
            obra_id=work.id,
            obra=(work.object_description or "")[:120],
            bairro=work.neighborhood,
            secretaria=work.managing_unit,
            fornecedor=work.contractor_name,
            score_argus=round(work.efficiency_score, 1) if work.efficiency_score is not None else None,
            classificacao_risco=_classify_score(work.efficiency_score),
            valor_contratado=contract_val,
            valor_em_risco_estimado=valor_risco,
            dias_atraso=delay,
            alertas_ativos=alerts_count,
            motivo_principal=motivo,
            acao_sugerida=acao,
        ))

    logger.debug("DEBUG: get_priority_queue - retornando %d itens", len(result))
    return result


# ── 3. Risk Distribution ──────────────────────────────────────────────────

def get_risk_distribution(
    db: Session,
    municipio: str | None = None,
) -> list[RiskDistributionItem]:
    """
    Retorna a distribuição de obras por faixa de risco.

    Faixas:
    - Eficiente: 80-100
    - Atenção: 60-79
    - Alto risco: 40-59
    - Crítico: 0-39
    - Sem score: null

    Args:
        db: Sessão do banco de dados.
        municipio: Nome do município para filtrar (opcional).

    Returns:
        Lista de RiskDistributionItem com contagem por faixa.
    """
    logger.debug("DEBUG: get_risk_distribution - municipio='%s'", municipio)

    q = _apply_municipio_filter(db.query(PublicWork), municipio)
    works = q.all()

    # Inicializa contadores
    counts = {
        "eficiente": 0,
        "atencao": 0,
        "alto_risco": 0,
        "critico": 0,
        "sem_dados": 0,
    }

    for work in works:
        score = work.efficiency_score
        if score is None:
            counts["sem_dados"] += 1
        elif score >= 80:
            counts["eficiente"] += 1
        elif score >= 60:
            counts["atencao"] += 1
        elif score >= 40:
            counts["alto_risco"] += 1
        else:
            counts["critico"] += 1

    result = [
        RiskDistributionItem(label="Eficiente", min=80, max=100, total=counts["eficiente"]),
        RiskDistributionItem(label="Atenção", min=60, max=79, total=counts["atencao"]),
        RiskDistributionItem(label="Alto risco", min=40, max=59, total=counts["alto_risco"]),
        RiskDistributionItem(label="Crítico", min=0, max=39, total=counts["critico"]),
        RiskDistributionItem(label="Sem score", min=None, max=None, total=counts["sem_dados"]),
    ]

    logger.debug("DEBUG: get_risk_distribution - eficientes=%d, atencao=%d, alto_risco=%d, criticas=%d, sem_dados=%d",
                 counts["eficiente"], counts["atencao"], counts["alto_risco"], counts["critico"], counts["sem_dados"])

    return result


# ── 4. Top Neighborhoods Risk ─────────────────────────────────────────────

def get_top_neighborhoods_risk(
    db: Session,
    municipio: str | None = None,
    limit: int = 10,
) -> list[NeighborhoodRiskItem]:
    """
    Retorna ranking de bairros com maior risco, ordenado por score médio crescente.

    Args:
        db: Sessão do banco de dados.
        municipio: Nome do município para filtrar (opcional).
        limit: Número máximo de bairros no ranking (default 10).

    Returns:
        Lista de NeighborhoodRiskItem ordenada por risco (pior primeiro).
    """
    today = date.today()
    logger.debug("DEBUG: get_top_neighborhoods_risk - municipio='%s', limit=%d", municipio, limit)

    q = _apply_municipio_filter(db.query(PublicWork), municipio)
    works = q.options(joinedload(PublicWork.alerts)).all()

    logger.debug("DEBUG: get_top_neighborhoods_risk - obras encontradas=%d", len(works))

    if not works:
        return []

    # Agrupa obras por bairro
    neighborhoods: dict[str, list[PublicWork]] = {}
    for work in works:
        bairro = work.neighborhood or "Sem bairro"
        if bairro not in neighborhoods:
            neighborhoods[bairro] = []
        neighborhoods[bairro].append(work)

    # Calcula indicadores por bairro
    bairro_stats: list[dict[str, Any]] = []
    for bairro, bairro_works in neighborhoods.items():
        scores = [float(w.efficiency_score) for w in bairro_works if w.efficiency_score is not None]
        score_medio = round(sum(scores) / len(scores), 1) if scores else 0.0

        obras_criticas = sum(1 for w in bairro_works if w.efficiency_score is not None and w.efficiency_score < 40)
        obras_atrasadas = sum(1 for w in bairro_works if _is_overdue(w, today))
        valor_total = sum(float(w.contract_value or 0) for w in bairro_works)
        alertas_total = sum(len(w.alerts or []) for w in bairro_works)

        # Classificação baseada no score médio
        if not scores:
            classificacao = RISK_LABELS["sem_dados"]
        elif score_medio >= 80:
            classificacao = RISK_LABELS["eficiente"]
        elif score_medio >= 60:
            classificacao = RISK_LABELS["atencao"]
        elif score_medio >= 40:
            classificacao = RISK_LABELS["alto_risco"]
        else:
            classificacao = RISK_LABELS["critico"]

        # Recomendação baseada na classificação
        if classificacao == RISK_LABELS["critico"]:
            recomendacao = "Intervenção imediata: realizar auditoria completa e suspender pagamentos até regularização."
        elif classificacao == RISK_LABELS["alto_risco"]:
            recomendacao = "Priorizar fiscalização territorial neste bairro."
        elif classificacao == RISK_LABELS["atencao"]:
            recomendacao = "Acompanhar de perto e solicitar relatórios de progresso."
        else:
            recomendacao = "Manter monitoramento regular."

        bairro_stats.append({
            "bairro": bairro,
            "obras": len(bairro_works),
            "score_medio": score_medio,
            "obras_criticas": obras_criticas,
            "obras_atrasadas": obras_atrasadas,
            "valor_total": round(valor_total, 2),
            "alertas": alertas_total,
            "classificacao": classificacao,
            "recomendacao": recomendacao,
        })

    # Ordena por score médio crescente (pior primeiro), desempatando por obras críticas
    bairro_stats.sort(key=lambda x: (x["score_medio"], -x["obras_criticas"]))

    # Limita ao número solicitado
    result = [NeighborhoodRiskItem(**stats) for stats in bairro_stats[:limit]]

    logger.debug("DEBUG: get_top_neighborhoods_risk - retornando %d bairros", len(result))
    return result


# ── 5. Top Suppliers Risk ─────────────────────────────────────────────────

def get_top_suppliers_risk(
    db: Session,
    municipio: str | None = None,
    limit: int = 10,
) -> list[SupplierRiskItem]:
    """
    Retorna ranking de fornecedores com maior risco, ordenado por score médio crescente.

    Args:
        db: Sessão do banco de dados.
        municipio: Nome do município para filtrar (opcional).
        limit: Número máximo de fornecedores no ranking (default 10).

    Returns:
        Lista de SupplierRiskItem ordenada por risco (pior primeiro).
    """
    logger.debug("DEBUG: get_top_suppliers_risk - municipio='%s', limit=%d", municipio, limit)

    q = _apply_municipio_filter(db.query(PublicWork), municipio)
    works = q.options(joinedload(PublicWork.alerts)).all()

    logger.debug("DEBUG: get_top_suppliers_risk - obras encontradas=%d", len(works))

    if not works:
        return []

    # Agrupa obras por fornecedor (CNPJ ou nome)
    suppliers: dict[str, list[PublicWork]] = {}
    supplier_names: dict[str, str] = {}
    supplier_cnpjs: dict[str, str | None] = {}

    for work in works:
        # Usa CNPJ como chave primária, fallback para nome
        key = work.contractor_document or work.contractor_name or "Desconhecido"
        if key not in suppliers:
            suppliers[key] = []
            supplier_names[key] = work.contractor_name or "Desconhecido"
            supplier_cnpjs[key] = work.contractor_document
        suppliers[key].append(work)

    # Calcula indicadores por fornecedor
    supplier_stats: list[dict[str, Any]] = []
    for key, supplier_works in suppliers.items():
        scores = [float(w.efficiency_score) for w in supplier_works if w.efficiency_score is not None]
        score_medio = round(sum(scores) / len(scores), 1) if scores else 0.0

        obras_criticas = sum(1 for w in supplier_works if w.efficiency_score is not None and w.efficiency_score < 40)
        valor_total = sum(float(w.contract_value or 0) for w in supplier_works)
        alertas_total = sum(len(w.alerts or []) for w in supplier_works)

        # Percentual médio de aditivos
        additive_percents = [_additive_percent(w) for w in supplier_works]
        aditivo_medio = round(sum(additive_percents) / len(additive_percents), 1) if additive_percents else 0.0

        # Classificação baseada no score médio
        if not scores:
            classificacao = RISK_LABELS["sem_dados"]
        elif score_medio >= 80:
            classificacao = RISK_LABELS["eficiente"]
        elif score_medio >= 60:
            classificacao = RISK_LABELS["atencao"]
        elif score_medio >= 40:
            classificacao = RISK_LABELS["alto_risco"]
        else:
            classificacao = RISK_LABELS["critico"]

        # Recomendação baseada na classificação e características
        if classificacao == RISK_LABELS["critico"]:
            recomendacao = "Suspender novas contratações e realizar auditoria completa do histórico."
        elif classificacao == RISK_LABELS["alto_risco"]:
            recomendacao = "Revisar histórico de execução e aditivos."
        elif classificacao == RISK_LABELS["atencao"]:
            recomendacao = "Acompanhar de perto os contratos ativos e solicitar garantias adicionais."
        else:
            recomendacao = "Manter monitoramento regular."

        supplier_stats.append({
            "fornecedor": supplier_names[key],
            "cnpj": supplier_cnpjs[key],
            "contratos": len(supplier_works),
            "valor_total": round(valor_total, 2),
            "score_medio": score_medio,
            "obras_criticas": obras_criticas,
            "alertas": alertas_total,
            "aditivo_medio_percentual": aditivo_medio,
            "classificacao": classificacao,
            "recomendacao": recomendacao,
        })

    # Ordena por score médio crescente (pior primeiro), desempatando por obras críticas
    supplier_stats.sort(key=lambda x: (x["score_medio"], -x["obras_criticas"]))

    # Limita ao número solicitado
    result = [SupplierRiskItem(**stats) for stats in supplier_stats[:limit]]

    logger.debug("DEBUG: get_top_suppliers_risk - retornando %d fornecedores", len(result))
    return result
