"""
Endpoints de Relatórios Executivos do ARGUS.

Fornece dados agregados para geração de relatórios executivos,
incluindo resumo geral, obras críticas, análise por bairro,
fornecedores e qualidade dos dados.

Endpoints:
- GET /reports/executive     — Relatório executivo geral com KPIs e recomendações
- GET /reports/critical-works — Obras críticas que precisam de atenção imediata
- GET /reports/neighborhoods  — Análise consolidada por bairro
- GET /reports/suppliers      — Análise consolidada de fornecedores
- GET /reports/data-quality   — Relatório de qualidade dos dados
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, case
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.models.work import PublicWork, Alert
from app.utils.obra_filter import filter_obras_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])


def _safe_float(v: Any, default: float = 0.0) -> float:
    """Converte valor para float com fallback seguro."""
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _classify_score(score: float | None) -> str:
    """Classifica o score ARGUS em faixa de risco."""
    if score is None:
        return "Sem dados"
    if score >= 80:
        return "Eficiente"
    if score >= 60:
        return "Atenção"
    if score >= 40:
        return "Alto risco"
    return "Crítico"


def _normalize_municipio(raw: str) -> str:
    """Normaliza nome do município para busca sem acentos."""
    import unicodedata
    if not raw:
        return raw
    cleaned = unicodedata.normalize("NFD", raw)
    cleaned = "".join(c for c in cleaned if unicodedata.category(c) != "Mn")
    return cleaned.strip().lower()


# ─────────────────────────────────────────────────────────────────────────────
# GET /reports/executive
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/executive", summary="Relatório Executivo Geral")
def report_executive(
    municipio: str = Query(default="Macae", description="Nome do município"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Relatório executivo consolidado com KPIs, prioridades,
    bairros críticos, fornecedores em revisão, contratos com aditivos altos,
    alertas críticos e recomendações.
    """
    mun = _normalize_municipio(municipio)
    works = (
        filter_obras_query(
            db.query(PublicWork)
            .options(joinedload(PublicWork.alerts))
            .filter(func.lower(func.unaccent(PublicWork.municipio)).contains(mun))
        )
        .all()
    )

    if not works:
        return {
            "municipio": municipio,
            "gerado_em": datetime.utcnow().isoformat(),
            "kpis": _empty_kpis(),
            "prioridades_hoje": [],
            "bairros_criticos": [],
            "fornecedores_revisao": [],
            "contratos_aditivos_altos": [],
            "alertas_criticos": [],
            "recomendacoes": ["Nenhum dado disponível para o município informado."],
        }

    today = date.today()
    scores = [w.efficiency_score for w in works if w.efficiency_score is not None]
    total_value = sum(_safe_float(w.contract_value) for w in works)
    paid_value = sum(_safe_float(w.paid_value) for w in works)
    additive_value = sum(_safe_float(w.additive_value) for w in works)
    avg_score = sum(scores) / len(scores) if scores else 0

    # Contagens por classificação
    critico_count = sum(1 for s in scores if s < 40)
    alto_risco_count = sum(1 for s in scores if 40 <= s < 60)
    atencao_count = sum(1 for s in scores if 60 <= s < 80)
    eficiente_count = sum(1 for s in scores if s >= 80)

    # Obras atrasadas
    atrasadas = []
    for w in works:
        if w.finished_at:
            continue
        if w.due_at and w.due_at < today:
            atrasadas.append(w)

    # Obras sem geolocalização
    sem_geo = [w for w in works if w.latitude is None or w.longitude is None]

    # Contratos com aditivos altos (>25%)
    aditivos_altos = []
    for w in works:
        cv = _safe_float(w.contract_value)
        av = _safe_float(w.additive_value)
        if cv > 0 and av > 0 and (av / cv) > 0.25:
            aditivos_altos.append({
                "id": w.id,
                "objeto": w.object_description or f"Obra #{w.id}",
                "fornecedor": w.contractor_name,
                "valor_contratado": cv,
                "valor_aditivo": av,
                "percentual_aditivo": round((av / cv) * 100, 1),
            })

    # KPIs
    kpis = {
        "obras_monitoradas": len(works),
        "valor_total_contratado": round(total_value, 2),
        "valor_total_pago": round(paid_value, 2),
        "valor_total_aditivos": round(additive_value, 2),
        "score_medio": round(avg_score, 1),
        "obras_eficientes": eficiente_count,
        "obras_em_atencao": atencao_count,
        "obras_alto_risco": alto_risco_count,
        "obras_criticas": critico_count,
        "obras_atrasadas": len(atrasadas),
        "obras_sem_geolocalizacao": len(sem_geo),
        "contratos_aditivos_altos": len(aditivos_altos),
        "percentual_executado": round((paid_value / total_value * 100), 1) if total_value > 0 else 0,
    }

    # Prioridades de hoje: obras críticas + atrasadas, ordenadas por score
    priority_candidates = [w for w in works if (w.efficiency_score is not None and w.efficiency_score < 60) or (w.due_at and w.due_at < today and not w.finished_at)]
    priority_candidates.sort(key=lambda w: (w.efficiency_score or 0))
    prioridades = []
    for w in priority_candidates[:10]:
        prioridades.append({
            "id": w.id,
            "objeto": w.object_description or f"Obra #{w.id}",
            "bairro": w.neighborhood,
            "fornecedor": w.contractor_name,
            "score": w.efficiency_score,
            "classificacao": _classify_score(w.efficiency_score),
            "valor_contratado": _safe_float(w.contract_value),
            "motivo": _build_priority_reason(w, today),
        })

    # Bairros críticos
    bairros_map: dict[str, list[PublicWork]] = {}
    for w in works:
        b = w.neighborhood or "Não informado"
        bairros_map.setdefault(b, []).append(w)

    bairros_criticos = []
    for bairro, bworks in bairros_map.items():
        bscores = [w.efficiency_score for w in bworks if w.efficiency_score is not None]
        if not bscores:
            continue
        bavg = sum(bscores) / len(bscores)
        if bavg < 60:
            bairros_criticos.append({
                "bairro": bairro,
                "obras": len(bworks),
                "score_medio": round(bavg, 1),
                "obras_criticas": sum(1 for s in bscores if s < 40),
                "valor_total": round(sum(_safe_float(w.contract_value) for w in bworks), 2),
            })
    bairros_criticos.sort(key=lambda x: x["score_medio"])

    # Fornecedores que merecem revisão
    forn_map: dict[str, list[PublicWork]] = {}
    for w in works:
        f = w.contractor_name
        if f:
            forn_map.setdefault(f, []).append(w)

    fornecedores_revisao = []
    for fornecedor, fworks in forn_map.items():
        fscores = [w.efficiency_score for w in fworks if w.efficiency_score is not None]
        if not fscores:
            continue
        favg = sum(fscores) / len(fscores)
        alertas_count = sum(len(w.alerts) for w in fworks)
        if favg < 60 or alertas_count > 3:
            fornecedores_revisao.append({
                "fornecedor": fornecedor,
                "obras": len(fworks),
                "score_medio": round(favg, 1),
                "alertas": alertas_count,
                "valor_total": round(sum(_safe_float(w.contract_value) for w in fworks), 2),
                "classificacao": _classify_score(favg),
            })
    fornecedores_revisao.sort(key=lambda x: x["score_medio"])

    # Alertas críticos
    alertas_criticos = []
    for w in works:
        for a in (w.alerts or []):
            if a.severity and a.severity.lower() in ("critical", "danger", "crítico"):
                alertas_criticos.append({
                    "id": a.id,
                    "obra_id": w.id,
                    "obra": w.object_description or f"Obra #{w.id}",
                    "codigo": a.code,
                    "severidade": a.severity,
                    "mensagem": a.message,
                    "criado_em": a.created_at.isoformat() if a.created_at else None,
                })

    # Recomendações executivas
    recomendacoes = _build_recommendations(
        kpis, bairros_criticos, fornecedores_revisao, aditivos_altos, alertas_criticos
    )

    return {
        "municipio": municipio,
        "gerado_em": datetime.utcnow().isoformat(),
        "kpis": kpis,
        "prioridades_hoje": prioridades,
        "bairros_criticos": bairros_criticos[:10],
        "fornecedores_revisao": fornecedores_revisao[:10],
        "contratos_aditivos_altos": aditivos_altos[:10],
        "alertas_criticos": alertas_criticos[:20],
        "recomendacoes": recomendacoes,
    }


def _empty_kpis() -> dict[str, Any]:
    return {
        "obras_monitoradas": 0, "valor_total_contratado": 0, "valor_total_pago": 0,
        "valor_total_aditivos": 0, "score_medio": 0, "obras_eficientes": 0,
        "obras_em_atencao": 0, "obras_alto_risco": 0, "obras_criticas": 0,
        "obras_atrasadas": 0, "obras_sem_geolocalizacao": 0,
        "contratos_aditivos_altos": 0, "percentual_executado": 0,
    }


def _build_priority_reason(w: PublicWork, today: date) -> str:
    reasons = []
    if w.efficiency_score is not None and w.efficiency_score < 40:
        reasons.append("Score crítico")
    elif w.efficiency_score is not None and w.efficiency_score < 60:
        reasons.append("Alto risco")
    if w.due_at and w.due_at < today and not w.finished_at:
        days = (today - w.due_at).days
        reasons.append(f"Atraso de {days} dias")
    if len(w.alerts or []) > 0:
        reasons.append(f"{len(w.alerts)} alerta(s)")
    return "; ".join(reasons) if reasons else "Priorizado pelo score"


def _build_recommendations(kpis, bairros, fornecedores, aditivos, alertas) -> list[str]:
    recs = []
    if kpis["obras_criticas"] > 0:
        recs.append(f"Priorizar auditoria nas {kpis['obras_criticas']} obras classificadas como Críticas (score < 40).")
    if kpis["obras_atrasadas"] > 0:
        recs.append(f"Investigar as {kpis['obras_atrasadas']} obras com atraso contratual e solicitar replanejamento.")
    if aditivos:
        recs.append(f"Auditar os {len(aditivos)} contratos com aditivos acumulados acima de 25% do valor original.")
    if fornecedores:
        recs.append(f"Revisar os {len(fornecedores)} fornecedores com score médio abaixo de 60 ou excesso de alertas.")
    if bairros:
        recs.append(f"Reforçar fiscalização nos {len(bairros)} bairros com score médio abaixo de 60.")
    if kpis["obras_sem_geolocalizacao"] > 0:
        recs.append(f"Saneamento cadastral: {kpis['obras_sem_geolocalizacao']} obras sem geolocalização comprometem a análise territorial.")
    if alertas:
        recs.append(f"Encaminhar os {len(alertas)} alertas críticos para análise da equipe de controle interno.")
    if not recs:
        recs.append("Nenhuma ação crítica identificada no momento. Manter monitoramento contínuo.")
    return recs


# ─────────────────────────────────────────────────────────────────────────────
# GET /reports/critical-works
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/critical-works", summary="Obras Críticas")
def report_critical_works(
    municipio: str = Query(default="Macae"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Retorna obras com score crítico (<40) ou alto risco (40-59)."""
    mun = _normalize_municipio(municipio)
    works = (
        filter_obras_query(
            db.query(PublicWork)
            .options(joinedload(PublicWork.alerts))
            .filter(func.lower(func.unaccent(PublicWork.municipio)).contains(mun))
            .filter(PublicWork.efficiency_score.isnot(None))
            .filter(PublicWork.efficiency_score < 60)
        )
        .order_by(PublicWork.efficiency_score.asc())
        .limit(limit)
        .all()
    )
    today = date.today()
    return [
        {
            "id": w.id,
            "objeto": w.object_description or f"Obra #{w.id}",
            "bairro": w.neighborhood,
            "municipio": w.municipio,
            "fornecedor": w.contractor_name,
            "score": w.efficiency_score,
            "classificacao": _classify_score(w.efficiency_score),
            "valor_contratado": _safe_float(w.contract_value),
            "valor_pago": _safe_float(w.paid_value),
            "percentual_aditivo": round((_safe_float(w.additive_value) / _safe_float(w.contract_value)) * 100, 1) if _safe_float(w.contract_value) > 0 else 0,
            "dias_atraso": (today - w.due_at).days if w.due_at and w.due_at < today and not w.finished_at else 0,
            "alertas": len(w.alerts or []),
            "score_custo": w.cost_score,
            "score_prazo": w.deadline_score,
            "score_qualidade": w.quality_score,
            "score_recorrencia": w.recurrence_score,
            "score_social": w.social_impact_score,
            "previsao_entrega": w.due_at.isoformat() if w.due_at else None,
            "status": w.status or ("Concluída" if w.finished_at else "Em andamento"),
        }
        for w in works
    ]


# ─────────────────────────────────────────────────────────────────────────────
# GET /reports/neighborhoods
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/neighborhoods", summary="Análise por Bairro")
def report_neighborhoods(
    municipio: str = Query(default="Macae"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Análise consolidada de obras agrupadas por bairro."""
    mun = _normalize_municipio(municipio)
    works = (
        filter_obras_query(
            db.query(PublicWork)
            .options(joinedload(PublicWork.alerts))
            .filter(func.lower(func.unaccent(PublicWork.municipio)).contains(mun))
        )
        .all()
    )

    bairros_map: dict[str, list[PublicWork]] = {}
    for w in works:
        b = w.neighborhood or "Não informado"
        bairros_map.setdefault(b, []).append(w)

    result = []
    for bairro, bworks in bairros_map.items():
        scores = [w.efficiency_score for w in bworks if w.efficiency_score is not None]
        avg = sum(scores) / len(scores) if scores else None
        result.append({
            "bairro": bairro,
            "obras": len(bworks),
            "score_medio": round(avg, 1) if avg is not None else None,
            "classificacao": _classify_score(avg),
            "obras_criticas": sum(1 for s in scores if s < 40),
            "obras_alto_risco": sum(1 for s in scores if 40 <= s < 60),
            "obras_atrasadas": sum(1 for w in bworks if w.due_at and w.due_at < date.today() and not w.finished_at),
            "valor_total": round(sum(_safe_float(w.contract_value) for w in bworks), 2),
            "valor_pago": round(sum(_safe_float(w.paid_value) for w in bworks), 2),
            "alertas_totais": sum(len(w.alerts or []) for w in bworks),
            "fornecedores_distintos": len(set(w.contractor_name for w in bworks if w.contractor_name)),
        })

    result.sort(key=lambda x: x["score_medio"] if x["score_medio"] is not None else 999)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# GET /reports/suppliers
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/suppliers", summary="Análise de Fornecedores")
def report_suppliers(
    municipio: str = Query(default="Macae"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Análise consolidada de fornecedores com indicadores de risco."""
    mun = _normalize_municipio(municipio)
    works = (
        filter_obras_query(
            db.query(PublicWork)
            .options(joinedload(PublicWork.alerts))
            .filter(func.lower(func.unaccent(PublicWork.municipio)).contains(mun))
            .filter(PublicWork.contractor_name.isnot(None))
        )
        .all()
    )

    forn_map: dict[str, list[PublicWork]] = {}
    for w in works:
        f = w.contractor_name.strip() if w.contractor_name else None
        if f:
            forn_map.setdefault(f, []).append(w)

    result = []
    for fornecedor, fworks in forn_map.items():
        scores = [w.efficiency_score for w in fworks if w.efficiency_score is not None]
        avg = sum(scores) / len(scores) if scores else None
        total_alerts = sum(len(w.alerts or []) for w in fworks)
        bairros = list(set(w.neighborhood for w in fworks if w.neighborhood))

        result.append({
            "fornecedor": fornecedor,
            "contratos": len(fworks),
            "score_medio": round(avg, 1) if avg is not None else None,
            "classificacao": _classify_score(avg),
            "obras_criticas": sum(1 for s in scores if s < 40),
            "obras_atrasadas": sum(1 for w in fworks if w.due_at and w.due_at < date.today() and not w.finished_at),
            "valor_total": round(sum(_safe_float(w.contract_value) for w in fworks), 2),
            "valor_pago": round(sum(_safe_float(w.paid_value) for w in fworks), 2),
            "alertas_totais": total_alerts,
            "bairros_atuacao": bairros[:5],
            "aditivo_medio_percentual": _avg_additive_pct(fworks),
        })

    result.sort(key=lambda x: x["score_medio"] if x["score_medio"] is not None else 999)
    return result


def _avg_additive_pct(works: list[PublicWork]) -> float:
    """Calcula percentual médio de aditivos de uma lista de obras."""
    pcts = []
    for w in works:
        cv = _safe_float(w.contract_value)
        av = _safe_float(w.additive_value)
        if cv > 0 and av > 0:
            pcts.append((av / cv) * 100)
    return round(sum(pcts) / len(pcts), 1) if pcts else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# GET /reports/data-quality
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/data-quality", summary="Qualidade dos Dados")
def report_data_quality(
    municipio: str = Query(default="Macae"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Relatório de qualidade dos dados: completude dos campos obrigatórios."""
    mun = _normalize_municipio(municipio)
    works = (
        filter_obras_query(
            db.query(PublicWork)
            .options(joinedload(PublicWork.alerts))
            .filter(func.lower(func.unaccent(PublicWork.municipio)).contains(mun))
        )
        .all()
    )

    total = len(works)
    if total == 0:
        return {
            "municipio": municipio,
            "total_obras": 0,
            "obras_sem_bairro": 0,
            "obras_sem_geolocalizacao": 0,
            "obras_sem_valor": 0,
            "obras_sem_fornecedor": 0,
            "obras_sem_prazo": 0,
            "obras_sem_score": 0,
            "data_quality_score": 0,
            "obras_para_saneamento": [],
        }

    sem_bairro = [w for w in works if not w.neighborhood]
    sem_geo = [w for w in works if w.latitude is None or w.longitude is None]
    sem_valor = [w for w in works if w.contract_value is None or w.contract_value <= 0]
    sem_fornecedor = [w for w in works if not w.contractor_name]
    sem_prazo = [w for w in works if w.due_at is None]
    sem_score = [w for w in works if w.efficiency_score is None]

    # Score de qualidade: percentual de completude dos 6 campos obrigatórios
    total_fields = total * 6
    filled_fields = total_fields - (
        len(sem_bairro) + len(sem_geo) + len(sem_valor) +
        len(sem_fornecedor) + len(sem_prazo) + len(sem_score)
    )
    quality_score = round((filled_fields / total_fields) * 100, 1) if total_fields > 0 else 0

    # Obras para saneamento (com mais problemas)
    saneamento = []
    for w in works:
        problemas = []
        if not w.neighborhood:
            problemas.append("Sem bairro")
        if w.latitude is None or w.longitude is None:
            problemas.append("Sem geolocalização")
        if w.contract_value is None or w.contract_value <= 0:
            problemas.append("Sem valor contratado")
        if not w.contractor_name:
            problemas.append("Sem fornecedor")
        if w.due_at is None:
            problemas.append("Sem prazo")
        if w.efficiency_score is None:
            problemas.append("Sem score ARGUS")
        if len(problemas) >= 2:
            saneamento.append({
                "id": w.id,
                "descricao": w.object_description or f"Obra #{w.id}",
                "bairro": w.neighborhood,
                "problemas": problemas,
            })

    saneamento.sort(key=lambda x: len(x["problemas"]), reverse=True)

    return {
        "municipio": municipio,
        "total_obras": total,
        "obras_sem_bairro": len(sem_bairro),
        "obras_sem_geolocalizacao": len(sem_geo),
        "obras_sem_valor": len(sem_valor),
        "obras_sem_fornecedor": len(sem_fornecedor),
        "obras_sem_prazo": len(sem_prazo),
        "obras_sem_score": len(sem_score),
        "data_quality_score": quality_score,
        "obras_para_saneamento": saneamento[:20],
    }
