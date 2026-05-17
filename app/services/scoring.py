from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from app.models.work import PublicWork

@dataclass
class ScoreResult:
    cost_score: float
    deadline_score: float
    quality_score: float
    recurrence_score: float
    social_impact_score: float
    efficiency_score: float
    alerts: list[dict]


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def delay_days(work: PublicWork, today: date | None = None) -> int:
    today = today or date.today()
    if not work.due_at or work.finished_at:
        return 0
    if today <= work.due_at:
        return 0
    if work.committed_value and work.settled_value is not None and work.settled_value >= work.committed_value:
        return 0
    return (today - work.due_at).days


def calculate_score(work: PublicWork, contractor_recurrence: int = 1, benchmark_cost_m2: float | None = None) -> ScoreResult:
    alerts: list[dict] = []

    cost_per_m2 = None
    if work.settled_value and work.area_m2 and work.area_m2 > 0:
        cost_per_m2 = work.settled_value / work.area_m2
    elif work.contract_value and work.area_m2 and work.area_m2 > 0:
        cost_per_m2 = work.contract_value / work.area_m2

    if cost_per_m2 and benchmark_cost_m2 and benchmark_cost_m2 > 0:
        deviation = (cost_per_m2 - benchmark_cost_m2) / benchmark_cost_m2
        cost_score = clamp(100 - max(0, deviation) * 100)
        if deviation >= 0.50:
            alerts.append({"code": "CRITICAL_AUDITORIA", "severity": "critical", "message": "Desvio de custo maior ou igual a 50% do benchmark."})
        elif deviation >= 0.16:
            alerts.append({"code": "ALERT_SOBREPRECO", "severity": "alert", "message": "Desvio de custo entre 16% e 49% do benchmark."})
        elif deviation > 0:
            alerts.append({"code": "WARNING_CUSTO", "severity": "warning", "message": "Desvio de custo até 15% do benchmark."})
    else:
        cost_score = 80.0 if work.contract_value or work.settled_value else 60.0

    days = delay_days(work)
    deadline_score = clamp(100 - (days / 90) * 100) if days else 100.0
    if days > 90:
        alerts.append({"code": "CRITICAL_PARALISACAO", "severity": "critical", "message": f"Atraso superior a 90 dias ({days} dias)."})
    elif days >= 31:
        alerts.append({"code": "ALERT_RISCO_ALTO", "severity": "alert", "message": f"Atraso entre 31 e 90 dias ({days} dias)."})
    elif days >= 1:
        alerts.append({"code": "WARNING_CRONOGRAMA", "severity": "warning", "message": f"Atraso entre 1 e 30 dias ({days} dias)."})

    additive_ratio = 0.0
    if work.additive_value and work.contract_value and work.contract_value > 0:
        additive_ratio = work.additive_value / work.contract_value
    quality_score = clamp(100 - additive_ratio * 100)
    if additive_ratio >= 0.25:
        alerts.append({"code": "CRITICAL_TETO_LEGAL", "severity": "critical", "message": "Aditivos atingem ou superam 25% do valor original."})
    elif additive_ratio > 0.10:
        alerts.append({"code": "WARNING_ESCOPO", "severity": "warning", "message": "Aditivos relevantes detectados no contrato."})

    recurrence_penalty = max(0, contractor_recurrence - 1) * 10
    recurrence_score = clamp(100 - recurrence_penalty)
    if contractor_recurrence >= 4:
        alerts.append({"code": "ALERT_RECORRENCIA_ESTRUTURAL", "severity": "alert", "message": "Alta recorrência associada ao mesmo contratado/localidade."})

    if work.idh is None:
        social_impact_score = 60.0
    else:
        social_impact_score = clamp((1 - work.idh) * 100)

    efficiency = (
        cost_score * 0.30 + deadline_score * 0.25 + quality_score * 0.20 +
        recurrence_score * 0.15 + social_impact_score * 0.10
    )

    if work.idh is not None and work.idh < 0.600:
        for alert in alerts:
            if alert["severity"] == "warning":
                alert["severity"] = "alert"
            elif alert["severity"] == "alert":
                alert["severity"] = "critical"
            alert["message"] += " Criticidade ampliada por vulnerabilidade territorial."

    return ScoreResult(
        cost_score=round(cost_score, 2), deadline_score=round(deadline_score, 2),
        quality_score=round(quality_score, 2), recurrence_score=round(recurrence_score, 2),
        social_impact_score=round(social_impact_score, 2), efficiency_score=round(efficiency, 2),
        alerts=alerts,
    )
