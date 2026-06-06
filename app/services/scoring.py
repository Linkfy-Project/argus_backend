from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.work import PublicWork
from app.core.config import get_settings

# Pesos v2 — com ML Risk Score integrado (15% redistribuído dos outros pilares)
WEIGHTS = {
    "cost": 0.25,
    "deadline": 0.25,
    "quality": 0.20,
    "recurrence": 0.10,
    "social_impact": 0.05,
    "ml_risk": 0.15,
}

CREA_PENALTIES = {
    "light": 5,
    "medium": 15,
    "grave": 40,
}

SEVERITY_WEIGHTS = {
    "info": 0.0,
    "warning": 1.0,
    "alert": 2.0,
    "critical": 3.0,
}

CRITICAL_IDH_THRESHOLD = 0.600
CRITICAL_IDH_MULTIPLIER = 1.5


@dataclass
class ScoreResult:
    cost_score: float
    deadline_score: float
    quality_score: float
    recurrence_score: float
    social_impact_score: float
    efficiency_score: float
    alerts: list[dict]
    components: dict

    def as_dict(self) -> dict:
        return asdict(self)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def safe_float(value, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def get_work_value(work: PublicWork, attr: str, default=None):
    return getattr(work, attr, default)


def calculate_contractor_crea_totals(db: Session, work: PublicWork) -> tuple[int, int]:
    """
    Calcula o total de infrações CREA e a quantidade de obras
    associadas ao mesmo contratado (via contractor_document/CNPJ).

    Retorna (contractor_work_count, contractor_crea_total).
    Se não houver contractor_document, retorna (1, 0).
    """
    if not work.contractor_document:
        return 1, 0

    row = (
        db.query(
            func.count(PublicWork.id).label("work_count"),
            (
                func.coalesce(func.sum(PublicWork.crea_light_count), 0)
                + func.coalesce(func.sum(PublicWork.crea_medium_count), 0)
                + func.coalesce(func.sum(PublicWork.crea_grave_count), 0)
            ).label("crea_total"),
        )
        .filter(PublicWork.contractor_document == work.contractor_document)
        .one()
    )

    return row.work_count or 1, int(row.crea_total or 0)


def delay_days(work: PublicWork, today: date | None = None) -> int:
    """
    Regra do dicionário de dados: uma obra é considerada atrasada quando a data
    atual é superior à DataVencimentoContrato e o ValorLiquidado é inferior ao
    ValorEmpenhado. Se a obra já foi concluída, não há atraso ativo.
    """
    today = today or date.today()

    if not work.due_at or work.finished_at:
        return 0
    if today <= work.due_at:
        return 0
    if work.committed_value is not None and work.settled_value is not None:
        if work.settled_value >= work.committed_value:
            return 0

    return (today - work.due_at).days


def _add_alert(alerts: list[dict], code: str, severity: str, message: str, details: dict | None = None) -> None:
    severity_weight = SEVERITY_WEIGHTS.get(severity, 0.0)
    alerts.append(
        {
            "code": code,
            "severity": severity,
            "severity_weight": severity_weight,
            "severity_multiplier": 1.0,
            "weighted_severity": severity_weight,
            "message": message,
            "details": details or {},
        }
    )


def calculate_cost_score(work: PublicWork, benchmark_cost_m2: float | None = None) -> tuple[float, dict, list[dict]]:
    """
    Calcula o score de custo paramétrico.

    Hierarquia de estratégias:
    1. Benchmark SINAPI × área (ideal) — compara custo real vs referência.
    2. Heurística de execução financeira — usa committed/settled/additive como proxy.
    3. Fallback conservador — penaliza ausência de dados como risco de transparência.

    Quando não há benchmark SINAPI, NÃO devolvemos 100 (neutro), pois isso
    torna impossível atingir score crítico. Em vez disso, aplicamos heurísticas
    baseadas nos dados financeiros disponíveis.
    """
    alerts: list[dict] = []

    real_cost = safe_float(work.settled_value)
    if real_cost is None:
        real_cost = safe_float(work.contract_value)

    benchmark = benchmark_cost_m2 or get_work_value(work, "benchmark_cost_m2", None)
    benchmark = safe_float(benchmark)
    area_m2 = safe_float(work.area_m2)

    reference_cost = None
    if benchmark and benchmark > 0 and area_m2 and area_m2 > 0:
        reference_cost = benchmark * area_m2

    # ── Aplicação de correção inflacionária (IPCA) ────────────────────
    # Se a obra tem signed_at de um ano anterior e a correção está habilitada,
    # corrige o real_cost para a data atual antes de comparar com o benchmark.
    # A correção é aplicada apenas para COMPARAÇÃO — o valor original não é alterado.
    original_real_cost = real_cost  # Preserva o valor original para logging
    inflation_applied = False
    inflation_factor = 1.0

    settings = get_settings()
    if settings.INFLATION_ENABLED and real_cost is not None and work.signed_at is not None:
        try:
            from app.etl.inflation import correct_value_cached
            corrected = correct_value_cached(
                value=real_cost,
                source_date=work.signed_at,
                target_date=date.today(),
            )
            if corrected != real_cost:
                inflation_factor = corrected / real_cost if real_cost else 1.0
                real_cost = corrected
                inflation_applied = True
                print(f"DEBUG: [INFLATION] Obra ID={work.id}: R$ {original_real_cost:,.2f} "
                      f"({work.signed_at}) -> R$ {real_cost:,.2f} (fator: {inflation_factor:.6f})")
        except Exception as e:
            print(f"DEBUG: [INFLATION] ERRO ao corrigir obra ID={work.id}: {e} — usando valor original")

    # ── Estratégia 1: Benchmark SINAPI disponível ──────────────────────
    if real_cost is not None and reference_cost is not None and reference_cost > 0:
        deviation = (real_cost - reference_cost) / reference_cost
        score = clamp(100 - max(0.0, deviation) * 100)

        if deviation >= 0.50:
            _add_alert(alerts, "CRITICAL_AUDITORIA", "critical", "Desvio de custo maior ou igual a 50% do benchmark SINAPI.", {"deviation_ratio": deviation})
        elif deviation >= 0.16:
            _add_alert(alerts, "ALERT_SOBREPRECO", "alert", "Desvio de custo entre 16% e 49% do benchmark SINAPI.", {"deviation_ratio": deviation})
        elif deviation > 0:
            _add_alert(alerts, "WARNING_CUSTO", "warning", "Desvio de custo acima do benchmark SINAPI.", {"deviation_ratio": deviation})

        result_details = {
            "real_cost": real_cost,
            "original_real_cost": original_real_cost,
            "reference_cost": reference_cost,
            "benchmark_cost_m2": benchmark,
            "area_m2": area_m2,
            "deviation_ratio": deviation,
            "strategy": "benchmark_sinapi",
            "formula": "max(0, 100 - ((Custo Real - Custo Referencia) / Custo Referencia) * 100)",
            "inflation_applied": inflation_applied,
            "inflation_factor": inflation_factor,
            "signed_at": str(work.signed_at) if work.signed_at else None,
        }
        return score, result_details, alerts

    # ── Estratégia 2: Heurística sem benchmark (proxy de risco financeiro) ──
    # Usa dados disponíveis: committed vs settled, additive ratio, contract_value.
    contract_value = safe_float(work.contract_value)
    committed_value = safe_float(work.committed_value)
    settled_value = safe_float(work.settled_value)
    additive_value = safe_float(work.additive_value, 0.0) or 0.0

    score = 70.0  # Base conservadora quando há dados financeiros mas sem benchmark
    details: dict = {
        "strategy": "heuristic_no_benchmark",
        "benchmark_cost_m2": benchmark,
        "area_m2": area_m2,
        "formula": "70 base + ajustes por heurísticas financeiras",
    }

    has_any_financial_data = False

    # Heurística A: Aditivos como % do contrato (quando há valor contratual)
    if contract_value and contract_value > 0:
        has_any_financial_data = True
        additive_ratio = additive_value / contract_value
        # Aditivos altos = possível superfaturamento ou mudança de escopo
        if additive_ratio >= 0.25:
            score -= 30
            _add_alert(alerts, "CRITICAL_TETO_LEGAL_ADITIVOS", "critical",
                       "Aditivos atingem ou superam 25% do valor contratado (sem benchmark SINAPI).",
                       {"additive_ratio": additive_ratio})
        elif additive_ratio >= 0.16:
            score -= 15
            _add_alert(alerts, "ALERT_ADITIVO_CUSTO", "alert",
                       "Aditivos entre 16% e 24% do valor contratado (sem benchmark SINAPI).",
                       {"additive_ratio": additive_ratio})
        elif additive_ratio > 0:
            score -= 5
        details["additive_ratio"] = additive_ratio

    # Heurística B: Comprometido vs Liquidado (baixa execução = possível paralisação)
    if committed_value and committed_value > 0 and settled_value is not None:
        has_any_financial_data = True
        execution_ratio = settled_value / committed_value
        if execution_ratio < 0.30:
            score -= 15
            _add_alert(alerts, "ALERT_BAIXA_EXECUCAO_CUSTO", "alert",
                       "Execução financeira abaixo de 30% do empenhado.",
                       {"execution_ratio": execution_ratio})
        elif execution_ratio < 0.50:
            score -= 8
        details["execution_ratio"] = execution_ratio

    # Heurística C: Valor muito alto sem detalhamento = risco de transparência
    if contract_value and contract_value > 5_000_000 and not benchmark:
        score -= 10
        _add_alert(alerts, "WARNING_TRANSPARENCIA_CUSTO", "warning",
                   "Contrato acima de R$5 milhões sem referência de custo paramétrico.",
                   {"contract_value": contract_value})
        details["high_value_no_benchmark"] = True

    # Se NÃO há NENHUM dado financeiro, penaliza mais (opacidade total)
    if not has_any_financial_data and not real_cost:
        score = 50.0  # Sem dados = risco alto de transparência
        _add_alert(alerts, "ALERT_DADOS_FINANCEIROS_AUSENTES", "alert",
                   "Obra sem dados financeiros disponíveis (valor contratado, empenhado ou liquidado).",
                   {})
        details["note"] = "Sem dados financeiros; penalidade de opacidade aplicada."

    score = clamp(score)
    details["final_score"] = score
    return score, details, alerts


def detect_crea_suspicious_patterns(
    work: PublicWork,
    contractor_work_count: int = 1,
    contractor_crea_total: int = 0,
) -> list[dict]:
    """
    Detecta padrões suspeitos nas anotações CREA de uma obra.

    Padrões detectados:
    1. Alta concentração de infrações leves (possível tentativa de mascarar problemas graves)
    2. Múltiplas infrações graves (risco crítico)
    3. Infrações em proporção desproporcional ao porte da obra
    4. Mesmo contratado com muitas infrações em obras diferentes
    5. Combinação suspeita — infrações médias + graves sem leves (subnotificação)
    """
    alerts: list[dict] = []

    light = int(safe_float(get_work_value(work, "crea_light_count", 0), 0) or 0)
    medium = int(safe_float(get_work_value(work, "crea_medium_count", 0), 0) or 0)
    grave = int(safe_float(get_work_value(work, "crea_grave_count", 0), 0) or 0)
    total_infractions = light + medium + grave

    if total_infractions == 0:
        return alerts

    # Padrão 1: Muitas infrações leves acumuladas (>= 5) — possível tentativa de mascarar
    if light >= 5 and grave == 0:
        _add_alert(
            alerts, "ALERT_CREA_ACUMULO_LEVES", "alert",
            f"Acúmulo de {light} infrações CREA leves sem infrações graves. Possível mascaramento de problemas técnicos.",
            {"light_count": light, "medium_count": medium, "grave_count": grave},
        )

    # Padrão 2: Múltiplas infrações graves
    if grave >= 2:
        _add_alert(
            alerts, "CRITICAL_CREA_MULTIPLAS_GRAVES", "critical",
            f"Múltiplas infrações CREA graves ({grave}). Risco crítico de irregularidade técnica.",
            {"grave_count": grave},
        )

    # Padrão 3: Proporção desproporcional — muitas infrações para obra pequena
    contract_value = safe_float(work.contract_value)
    area_m2 = safe_float(work.area_m2)
    if contract_value and contract_value < 500_000 and total_infractions >= 3:
        _add_alert(
            alerts, "ALERT_CREA_DESPROPORCIONAL", "alert",
            f"Obra de pequeno porte (R$ {contract_value:,.0f}) com {total_infractions} infrações CREA. Proporção desproporcional.",
            {"total_infractions": total_infractions, "contract_value": contract_value},
        )
    elif area_m2 and area_m2 < 500 and total_infractions >= 3:
        _add_alert(
            alerts, "ALERT_CREA_DESPROPORCIONAL", "alert",
            f"Obra de pequena área ({area_m2:.0f} m²) com {total_infractions} infrações CREA. Proporção desproporcional.",
            {"total_infractions": total_infractions, "area_m2": area_m2},
        )

    # Padrão 4: Contratado com muitas infrações em múltiplas obras
    if contractor_work_count >= 3 and contractor_crea_total >= 5:
        _add_alert(
            alerts, "ALERT_CREA_RECORRENCIA_CONTRATADO", "alert",
            f"Contratado associado a {contractor_crea_total} infrações CREA em {contractor_work_count} obras. Padrão recorrente.",
            {"contractor_work_count": contractor_work_count, "contractor_crea_total": contractor_crea_total},
        )

    # Padrão 5: Combinação suspeita — infrações médias + graves sem leves
    if medium >= 2 and grave >= 1 and light == 0:
        _add_alert(
            alerts, "WARNING_CREA_PADRAO_SUSPEITO", "warning",
            "Infrações CREA médias e graves sem nenhuma infração leve registrada. Possível subnotificação.",
            {"light_count": light, "medium_count": medium, "grave_count": grave},
        )

    return alerts


def calculate_deadline_score(work: PublicWork, today: date | None = None) -> tuple[float, dict, list[dict]]:
    """
    Calcula o score de prazo/cronograma.

    Hierarquia:
    1. Obra com prazo definido — calcula atraso normal.
    2. Obra sem prazo — penaliza como risco de planejamento (opacidade de cronograma).
    3. Obra concluída no prazo — score 100.
    """
    alerts: list[dict] = []
    today = today or date.today()

    # Obra concluída = sem atraso ativo
    if work.finished_at:
        return 100.0, {
            "delay_days": 0,
            "due_at": work.due_at.isoformat() if work.due_at else None,
            "finished_at": work.finished_at.isoformat(),
            "formula": "max(0, 100 - (Dias de Atraso / 90) * 100)",
            "note": "Obra concluída; sem atraso ativo.",
        }, alerts

    days = delay_days(work, today=today)

    if days > 0:
        # Obra atrasada
        score = clamp(100 - (days / 90) * 100)
        if days > 90:
            _add_alert(alerts, "CRITICAL_PARALISACAO", "critical", f"Atraso superior a 90 dias ({days} dias).", {"delay_days": days})
        elif days >= 31:
            _add_alert(alerts, "ALERT_RISCO_ALTO", "alert", f"Atraso entre 31 e 90 dias ({days} dias).", {"delay_days": days})
        elif days >= 1:
            _add_alert(alerts, "WARNING_CRONOGRAMA", "warning", f"Atraso entre 1 e 30 dias ({days} dias).", {"delay_days": days})

        return score, {
            "delay_days": days,
            "due_at": work.due_at.isoformat() if work.due_at else None,
            "formula": "max(0, 100 - (Dias de Atraso / 90) * 100)",
        }, alerts

    # Sem atraso ativo — verificar se tem prazo definido
    if work.due_at:
        # Tem prazo e não está atrasada
        return 100.0, {
            "delay_days": 0,
            "due_at": work.due_at.isoformat(),
            "formula": "max(0, 100 - (Dias de Atraso / 90) * 100)",
        }, alerts

    # Obra SEM prazo definido E não concluída — risco de planejamento
    # Penalidade baseada no tempo desde a assinatura (se disponível)
    days_since_signed = 0
    if work.signed_at:
        days_since_signed = (today - work.signed_at).days

    # Obras sem prazo são penalizadas: 70 base, com agravante se assinada há muito tempo
    score = 70.0
    if days_since_signed > 365:
        score = 40.0
        _add_alert(alerts, "ALERT_SEM_PRAZO_LONGA", "alert",
                   f"Obra sem prazo definido, assinada há {days_since_signed} dias.",
                   {"days_since_signed": days_since_signed})
    elif days_since_signed > 180:
        score = 55.0
        _add_alert(alerts, "WARNING_SEM_PRAZO", "warning",
                   f"Obra sem prazo definido, assinada há {days_since_signed} dias.",
                   {"days_since_signed": days_since_signed})
    else:
        _add_alert(alerts, "WARNING_SEM_PRAZO", "warning",
                   "Obra sem prazo de vencimento definido.",
                   {})

    return clamp(score), {
        "delay_days": 0,
        "due_at": None,
        "days_since_signed": days_since_signed,
        "formula": "Penalidade por ausência de prazo: base 70, agravante temporal.",
        "note": "Obra sem prazo definido; penalidade de planejamento aplicada.",
    }, alerts


def calculate_quality_score(work: PublicWork) -> tuple[float, dict, list[dict]]:
    """
    Calcula o score de qualidade técnica e aditivos.

    Além de penalizar aditivos e CREA, aplica penalidade de transparência
    quando dados essenciais de qualidade estão ausentes (sem valor contratual,
    sem área, sem benchmark). A ausência de dados é um risco em si.
    """
    alerts: list[dict] = []
    contract_value = safe_float(work.contract_value)
    additive_value = safe_float(work.additive_value, 0.0) or 0.0
    benchmark = safe_float(get_work_value(work, "benchmark_cost_m2", None))
    area_m2 = safe_float(work.area_m2)

    additive_ratio = 0.0
    if contract_value and contract_value > 0:
        additive_ratio = max(0.0, additive_value / contract_value)

    light = int(safe_float(get_work_value(work, "crea_light_count", 0), 0) or 0)
    medium = int(safe_float(get_work_value(work, "crea_medium_count", 0), 0) or 0)
    grave = int(safe_float(get_work_value(work, "crea_grave_count", 0), 0) or 0)
    crea_penalty = light * CREA_PENALTIES["light"] + medium * CREA_PENALTIES["medium"] + grave * CREA_PENALTIES["grave"]

    additive_penalty = (additive_ratio / 0.25) * 100 if additive_ratio > 0 else 0.0

    # Penalidade de transparência: dados essenciais faltando
    transparency_penalty = 0.0
    missing_data_flags = []

    if not contract_value:
        transparency_penalty += 15.0
        missing_data_flags.append("contract_value")
    if not area_m2:
        transparency_penalty += 10.0
        missing_data_flags.append("area_m2")
    if not benchmark:
        transparency_penalty += 10.0
        missing_data_flags.append("benchmark_cost_m2")

    # Limita penalidade de transparência a 25 pontos
    transparency_penalty = min(transparency_penalty, 25.0)

    score = clamp(100 - additive_penalty - crea_penalty - transparency_penalty)

    # Alertas de aditivos
    if additive_ratio >= 0.25:
        _add_alert(alerts, "CRITICAL_TETO_LEGAL_ADITIVOS", "critical", "Aditivos atingem ou superam o teto de 25% do valor original.", {"additive_ratio": additive_ratio})
    elif additive_ratio > 0:
        _add_alert(alerts, "WARNING_ESCOPO", "warning", "Aditivo contratual detectado; escopo ou valor foi alterado.", {"additive_ratio": additive_ratio})

    # Alertas de CREA
    if grave:
        _add_alert(alerts, "CRITICAL_CREA", "critical", "Registro CREA grave/embargo associado à obra ou construtora.", {"grave_count": grave})
    elif medium:
        _add_alert(alerts, "ALERT_CREA", "alert", "Registro CREA médio associado à obra ou construtora.", {"medium_count": medium})
    elif light:
        _add_alert(alerts, "WARNING_CREA", "warning", "Registro CREA leve associado à obra ou construtora.", {"light_count": light})

    # Alerta de transparência
    if missing_data_flags:
        _add_alert(alerts, "WARNING_TRANSPARENCIA_QUALIDADE", "warning",
                   f"Dados de qualidade técnica ausentes: {', '.join(missing_data_flags)}.",
                   {"missing_fields": missing_data_flags})

    return score, {
        "contract_value": contract_value,
        "additive_value": additive_value,
        "additive_ratio": additive_ratio,
        "additive_penalty": additive_penalty,
        "crea_penalty": crea_penalty,
        "transparency_penalty": transparency_penalty,
        "missing_data_flags": missing_data_flags,
        "crea_counts": {"light": light, "medium": medium, "grave": grave},
        "formula": "max(0, 100 - aditivos_penalidade - crea_penalidade - transparencia_penalidade)",
    }, alerts


def calculate_recurrence_score(work: PublicWork, contractor_recurrence: int = 1, overlap_ratio: float | None = None) -> tuple[float, dict, list[dict]]:
    """
    Calcula o score de recorrência territorial/documental.

    Mantém a lógica original (geometric_overlap ou contractor_document_fallback).
    Sem alterações estruturais.
    """
    alerts: list[dict] = []
    ratio = safe_float(overlap_ratio)
    if ratio is None:
        ratio = safe_float(get_work_value(work, "territorial_overlap_ratio", None))

    if ratio is not None:
        ratio = clamp(ratio, 0.0, 1.0)
        score = clamp(100 - ratio * 100)
        if ratio >= 0.50:
            _add_alert(alerts, "CRITICAL_RECORRENCIA_TERRITORIAL", "critical", "Sobreposição territorial superior ou igual a 50% em janela inferior a 24 meses.", {"overlap_ratio": ratio})
        elif ratio > 0:
            _add_alert(alerts, "ALERT_RECORRENCIA_TERRITORIAL", "alert", "Sobreposição territorial detectada em janela inferior a 24 meses.", {"overlap_ratio": ratio})
        mode = "geometric_overlap"
    else:
        # Fallback documentado no dicionário: recorrência pela quantidade de contratos do mesmo CNPJ.
        recurrence = max(int(contractor_recurrence or 1), 1)
        score = clamp(100 - max(0, recurrence - 1) * 15)
        if recurrence >= 4:
            _add_alert(alerts, "ALERT_RECORRENCIA_CNPJ", "alert", "Alta recorrência de contratos vinculados ao mesmo CNPJ contratado.", {"contractor_recurrence": recurrence})
        mode = "contractor_document_fallback"

    return score, {
        "mode": mode,
        "contractor_recurrence": int(contractor_recurrence or 1),
        "territorial_overlap_ratio": ratio,
        "formula": "100 - percentual_de_area_sobreposta; fallback: -15 pontos por contrato recorrente do mesmo CNPJ.",
    }, alerts


def calculate_social_impact_score(work: PublicWork) -> tuple[float, dict, list[dict]]:
    """
    Calcula o score de impacto socioeconômico.

    Quando o IDH não está disponível, aplica um default mais conservador (45)
    em vez de 60. A lógica: municípios sem dados de IDH tendem a ser menos
    desenvolvidos (menor transparência = menor IDH provável).
    """
    idh = safe_float(work.idh)
    if idh is None:
        # Default conservador: 45 em vez de 60
        # (1 - 0.55) * 100 = 45, assumindo IDH ~0.55 para municípios sem dado
        return 45.0, {"idh": None, "formula": "(1 - IDH Local) * 100", "note": "Sem IDH; default conservador 45 (IDH estimado ~0.55)."}, []
    return clamp((1 - idh) * 100), {"idh": idh, "formula": "(1 - IDH Local) * 100"}, []


def apply_social_criticality_multiplier(work: PublicWork, alerts: list[dict]) -> None:
    """Multiplica a criticidade dos alertas quando IDH < 0.600."""
    idh = safe_float(work.idh)
    if idh is None or idh >= CRITICAL_IDH_THRESHOLD:
        return

    for alert in alerts:
        base = float(alert.get("severity_weight", SEVERITY_WEIGHTS.get(alert.get("severity"), 0.0)))
        alert["severity_multiplier"] = CRITICAL_IDH_MULTIPLIER
        alert["weighted_severity"] = round(base * CRITICAL_IDH_MULTIPLIER, 2)
        alert["message"] += " Criticidade multiplicada por 1.5x por IDH local inferior a 0.600."


def calculate_ml_risk_score(
    risk_delay_probability: float | None = None,
    risk_cost_probability: float | None = None,
    risk_rework_probability: float | None = None,
) -> tuple[float, dict]:
    """
    Calcula um score de risco ML integrado às probabilidades previstas pelo modelo.

    Fórmula: score = 100 - (média das probabilidades de risco × 100)
    Isso converte as probabilidades (0-1) do ML em um score (0-100) onde:
    - Probabilidade alta de risco = score baixo
    - Probabilidade baixa de risco = score alto

    Peso no score final: 15% (redistribuído dos outros 5 pilares).
    """
    probs = []
    details: dict = {}

    if risk_delay_probability is not None:
        probs.append(float(risk_delay_probability))
        details["risk_delay_probability"] = risk_delay_probability
    if risk_cost_probability is not None:
        probs.append(float(risk_cost_probability))
        details["risk_cost_probability"] = risk_cost_probability
    if risk_rework_probability is not None:
        probs.append(float(risk_rework_probability))
        details["risk_rework_probability"] = risk_rework_probability

    if not probs:
        return 70.0, {"note": "Sem predições ML disponíveis; default conservador 70."}

    avg_risk = sum(probs) / len(probs)
    score = clamp(100 - avg_risk * 100)
    details["average_risk_probability"] = round(avg_risk, 4)
    details["formula"] = "100 - (média das probabilidades de risco × 100)"

    return score, details


def calculate_score(
    work: PublicWork,
    contractor_recurrence: int = 1,
    benchmark_cost_m2: float | None = None,
    overlap_ratio: float | None = None,
    today: date | None = None,
    risk_delay_probability: float | None = None,
    risk_cost_probability: float | None = None,
    risk_rework_probability: float | None = None,
    contractor_work_count: int = 1,
    contractor_crea_total: int = 0,
) -> ScoreResult:
    """
    Calcula o Índice Composto de Eficiência ARGUS (score 0-100).

    Pesos (v2 — com ML integrado):
    - Custo Paramétrico:     25%  (era 30%)
    - Prazo / Cronograma:    25%  (mantido)
    - Qualidade Técnica:     20%  (mantido)
    - Recorrência Territorial: 10% (era 15%)
    - Impacto Socioeconômico:  5% (era 10%)
    - ML Risk Score:          15%  (NOVO)

    Os pesos foram redistribuídos para incorporar o ML sem alterar drasticamente
    a escala existente.
    """
    alerts: list[dict] = []

    cost_score, cost_details, cost_alerts = calculate_cost_score(work, benchmark_cost_m2=benchmark_cost_m2)
    deadline_score, deadline_details, deadline_alerts = calculate_deadline_score(work, today=today)
    quality_score, quality_details, quality_alerts = calculate_quality_score(work)
    recurrence_score, recurrence_details, recurrence_alerts = calculate_recurrence_score(work, contractor_recurrence=contractor_recurrence, overlap_ratio=overlap_ratio)
    social_impact_score, social_details, social_alerts = calculate_social_impact_score(work)
    ml_risk_score, ml_details = calculate_ml_risk_score(
        risk_delay_probability=risk_delay_probability,
        risk_cost_probability=risk_cost_probability,
        risk_rework_probability=risk_rework_probability,
    )

    # CREA suspicious pattern detection (Pilar 3 enhancement)
    crea_suspicious_alerts = detect_crea_suspicious_patterns(
        work,
        contractor_work_count=contractor_work_count,
        contractor_crea_total=contractor_crea_total,
    )

    for group in (cost_alerts, deadline_alerts, quality_alerts, recurrence_alerts, social_alerts, crea_suspicious_alerts):
        alerts.extend(group)

    apply_social_criticality_multiplier(work, alerts)

    # Pesos v2 com ML integrado
    W = {
        "cost": 0.25,
        "deadline": 0.25,
        "quality": 0.20,
        "recurrence": 0.10,
        "social_impact": 0.05,
        "ml_risk": 0.15,
    }

    efficiency = (
        cost_score * W["cost"]
        + deadline_score * W["deadline"]
        + quality_score * W["quality"]
        + recurrence_score * W["recurrence"]
        + social_impact_score * W["social_impact"]
        + ml_risk_score * W["ml_risk"]
    )

    return ScoreResult(
        cost_score=round(cost_score, 2),
        deadline_score=round(deadline_score, 2),
        quality_score=round(quality_score, 2),
        recurrence_score=round(recurrence_score, 2),
        social_impact_score=round(social_impact_score, 2),
        efficiency_score=round(efficiency, 2),
        alerts=alerts,
        components={
            "weights": W,
            "cost": cost_details,
            "deadline": deadline_details,
            "quality": quality_details,
            "recurrence": recurrence_details,
            "social_impact": social_details,
            "ml_risk": ml_details,
            "criticality_rule": {
                "idh_threshold": CRITICAL_IDH_THRESHOLD,
                "alert_multiplier": CRITICAL_IDH_MULTIPLIER,
            },
        },
    )
