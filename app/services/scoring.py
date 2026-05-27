from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from app.models.work import PublicWork

WEIGHTS = {
    "cost": 0.30,
    "deadline": 0.25,
    "quality": 0.20,
    "recurrence": 0.15,
    "social_impact": 0.10,
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

    # Quando não há SINAPI/benchmark disponível no dado público, não penalizamos por ausência.
    # Mantém compatibilidade com o gabarito, onde os casos sem desvio reportado recebem 100.
    if real_cost is None or reference_cost is None or reference_cost <= 0:
        return 100.0, {
            "real_cost": real_cost,
            "reference_cost": reference_cost,
            "benchmark_cost_m2": benchmark,
            "area_m2": area_m2,
            "deviation_ratio": None,
            "formula": "max(0, 100 - ((Custo Real - Custo Referencia) / Custo Referencia) * 100)",
            "note": "Sem benchmark SINAPI/m2 suficiente; nota neutra 100.",
        }, alerts

    deviation = (real_cost - reference_cost) / reference_cost
    score = clamp(100 - max(0.0, deviation) * 100)

    if deviation >= 0.50:
        _add_alert(alerts, "CRITICAL_AUDITORIA", "critical", "Desvio de custo maior ou igual a 50% do benchmark SINAPI.", {"deviation_ratio": deviation})
    elif deviation >= 0.16:
        _add_alert(alerts, "ALERT_SOBREPRECO", "alert", "Desvio de custo entre 16% e 49% do benchmark SINAPI.", {"deviation_ratio": deviation})
    elif deviation > 0:
        _add_alert(alerts, "WARNING_CUSTO", "warning", "Desvio de custo acima do benchmark SINAPI.", {"deviation_ratio": deviation})

    return score, {
        "real_cost": real_cost,
        "reference_cost": reference_cost,
        "benchmark_cost_m2": benchmark,
        "area_m2": area_m2,
        "deviation_ratio": deviation,
        "formula": "max(0, 100 - ((Custo Real - Custo Referencia) / Custo Referencia) * 100)",
    }, alerts


def calculate_deadline_score(work: PublicWork, today: date | None = None) -> tuple[float, dict, list[dict]]:
    alerts: list[dict] = []
    days = delay_days(work, today=today)
    score = clamp(100 - (days / 90) * 100) if days else 100.0

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


def calculate_quality_score(work: PublicWork) -> tuple[float, dict, list[dict]]:
    alerts: list[dict] = []
    contract_value = safe_float(work.contract_value)
    additive_value = safe_float(work.additive_value, 0.0) or 0.0

    additive_ratio = 0.0
    if contract_value and contract_value > 0:
        additive_ratio = max(0.0, additive_value / contract_value)

    light = int(safe_float(get_work_value(work, "crea_light_count", 0), 0) or 0)
    medium = int(safe_float(get_work_value(work, "crea_medium_count", 0), 0) or 0)
    grave = int(safe_float(get_work_value(work, "crea_grave_count", 0), 0) or 0)
    crea_penalty = light * CREA_PENALTIES["light"] + medium * CREA_PENALTIES["medium"] + grave * CREA_PENALTIES["grave"]

    additive_penalty = (additive_ratio / 0.25) * 100 if additive_ratio > 0 else 0.0
    score = clamp(100 - additive_penalty - crea_penalty)

    if additive_ratio >= 0.25:
        _add_alert(alerts, "CRITICAL_TETO_LEGAL_ADITIVOS", "critical", "Aditivos atingem ou superam o teto de 25% do valor original.", {"additive_ratio": additive_ratio})
    elif additive_ratio > 0:
        _add_alert(alerts, "WARNING_ESCOPO", "warning", "Aditivo contratual detectado; escopo ou valor foi alterado.", {"additive_ratio": additive_ratio})

    if grave:
        _add_alert(alerts, "CRITICAL_CREA", "critical", "Registro CREA grave/embargo associado à obra ou construtora.", {"grave_count": grave})
    elif medium:
        _add_alert(alerts, "ALERT_CREA", "alert", "Registro CREA médio associado à obra ou construtora.", {"medium_count": medium})
    elif light:
        _add_alert(alerts, "WARNING_CREA", "warning", "Registro CREA leve associado à obra ou construtora.", {"light_count": light})

    return score, {
        "contract_value": contract_value,
        "additive_value": additive_value,
        "additive_ratio": additive_ratio,
        "additive_penalty": additive_penalty,
        "crea_penalty": crea_penalty,
        "crea_counts": {"light": light, "medium": medium, "grave": grave},
        "formula": "max(0, 100 - ((Variacao de Aditivos % / 25) * 100) - Soma das Penalidades CREA)",
    }, alerts


def calculate_recurrence_score(work: PublicWork, contractor_recurrence: int = 1, overlap_ratio: float | None = None) -> tuple[float, dict, list[dict]]:
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
    idh = safe_float(work.idh)
    if idh is None:
        return 60.0, {"idh": None, "formula": "(1 - IDH Local) * 100", "note": "Sem IDH; nota conservadora 60."}, []
    return clamp((1 - idh) * 100), {"idh": idh, "formula": "(1 - IDH Local) * 100"}, []


def apply_social_criticality_multiplier(work: PublicWork, alerts: list[dict]) -> None:
    idh = safe_float(work.idh)
    if idh is None or idh >= CRITICAL_IDH_THRESHOLD:
        return

    for alert in alerts:
        base = float(alert.get("severity_weight", SEVERITY_WEIGHTS.get(alert.get("severity"), 0.0)))
        alert["severity_multiplier"] = CRITICAL_IDH_MULTIPLIER
        alert["weighted_severity"] = round(base * CRITICAL_IDH_MULTIPLIER, 2)
        alert["message"] += " Criticidade multiplicada por 1.5x por IDH local inferior a 0.600."


def calculate_score(
    work: PublicWork,
    contractor_recurrence: int = 1,
    benchmark_cost_m2: float | None = None,
    overlap_ratio: float | None = None,
    today: date | None = None,
) -> ScoreResult:
    alerts: list[dict] = []

    cost_score, cost_details, cost_alerts = calculate_cost_score(work, benchmark_cost_m2=benchmark_cost_m2)
    deadline_score, deadline_details, deadline_alerts = calculate_deadline_score(work, today=today)
    quality_score, quality_details, quality_alerts = calculate_quality_score(work)
    recurrence_score, recurrence_details, recurrence_alerts = calculate_recurrence_score(work, contractor_recurrence=contractor_recurrence, overlap_ratio=overlap_ratio)
    social_impact_score, social_details, social_alerts = calculate_social_impact_score(work)

    for group in (cost_alerts, deadline_alerts, quality_alerts, recurrence_alerts, social_alerts):
        alerts.extend(group)

    apply_social_criticality_multiplier(work, alerts)

    efficiency = (
        cost_score * WEIGHTS["cost"]
        + deadline_score * WEIGHTS["deadline"]
        + quality_score * WEIGHTS["quality"]
        + recurrence_score * WEIGHTS["recurrence"]
        + social_impact_score * WEIGHTS["social_impact"]
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
            "weights": WEIGHTS,
            "cost": cost_details,
            "deadline": deadline_details,
            "quality": quality_details,
            "recurrence": recurrence_details,
            "social_impact": social_details,
            "criticality_rule": {
                "idh_threshold": CRITICAL_IDH_THRESHOLD,
                "alert_multiplier": CRITICAL_IDH_MULTIPLIER,
            },
        },
    )
