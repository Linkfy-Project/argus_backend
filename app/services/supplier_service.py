"""
Serviço de fornecedores do ARGUS.

Responsável por agregar dados de obras por fornecedor (contractor_name),
gerando ranking, detalhes, classificação de risco e recomendações.
Cada fornecedor é identificado pelo contractor_name (e contractor_document/CNPJ).
"""

from __future__ import annotations

import logging
import unicodedata
from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_

from app.models.work import Alert, PublicWork
from app.utils.obra_filter import filter_obras_query
from app.schemas.supplier import SupplierRankingRead, SupplierDetailRead

logger = logging.getLogger(__name__)


def _normalize_municipio(raw: str) -> str:
    """Normaliza o termo de busca de município removendo acentos."""
    if not raw:
        return raw
    cleaned = unicodedata.normalize("NFD", raw)
    cleaned = "".join(c for c in cleaned if unicodedata.category(c) != "Mn")
    return cleaned.strip().lower()


def _normalize_fornecedor(name: str | None) -> str:
    """Normaliza nome vazio de fornecedor como 'Não informado'."""
    if not name or not name.strip():
        return "Não informado"
    return name.strip()


def _safe_number(value, default: float = 0.0) -> float:
    """Retorna valor numérico seguro."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _classificacao(score_medio: float | None, alertas_criticos: int) -> str:
    """
    Classifica o fornecedor baseado no score médio e alertas críticos.
    Regras:
    - Score >= 80: Eficiente
    - 60-79: Atenção
    - 40-59: Alto risco
    - <40: Crítico
    - Se muitos alertas críticos, ajustar para pior classificação.
    """
    if score_medio is None:
        return "Não avaliado"

    if score_medio >= 80:
        base = "Eficiente"
    elif score_medio >= 60:
        base = "Atenção"
    elif score_medio >= 40:
        base = "Alto risco"
    else:
        base = "Crítico"

    # Se há muitos alertas críticos, piora a classificação
    if alertas_criticos >= 3 and base in ["Eficiente", "Atenção"]:
        return "Alto risco"
    if alertas_criticos >= 5:
        return "Crítico"

    return base


def _recomendacao(classificacao: str, alertas_criticos: int, obras_criticas: int) -> str:
    """Gera recomendação baseada na classificação e alertas."""
    if classificacao == "Crítico":
        return "Suspender novas contratações e realizar auditoria completa dos contratos vigentes."
    if classificacao == "Alto risco":
        if alertas_criticos > 0:
            return "Revisar histórico contratual e priorizar fiscalização nas obras críticas."
        return "Reforçar fiscalização e solicitar relatórios periódicos de execução."
    if classificacao == "Atenção":
        return "Acompanhar de perto e solicitar planos de recuperação onde aplicável."
    if classificacao == "Eficiente":
        return "Manter monitoramento de rotina."
    return "Avaliar contexto das obras para definir ação."


def _aggregate_suppliers(
    db: Session,
    municipio: str | None = None,
    bairro: str | None = None,
) -> list[dict]:
    """
    Agrega dados de obras por fornecedor.
    Retorna lista de dicionários com dados agregados por fornecedor.
    """
    q = db.query(PublicWork).options(joinedload(PublicWork.alerts))

    # ── Filtro de obras (exclui registros classificados como não-obra) ──
    q = filter_obras_query(q)

    # ── Filtros ──────────────────────────────────────────────
    if municipio:
        normalized = _normalize_municipio(municipio)
        q = q.filter(func.unaccent(PublicWork.municipio).ilike(f"%{normalized}%"))

    if bairro:
        q = q.filter(PublicWork.neighborhood.ilike(f"%{bairro}%"))

    works = q.all()

    # Agrupa por fornecedor
    groups: dict[str, list[PublicWork]] = defaultdict(list)
    for w in works:
        key = _normalize_fornecedor(w.contractor_name)
        groups[key].append(w)

    # Agrega dados
    today = date.today()
    results = []

    for fornecedor_name, supplier_works in groups.items():
        # CNPJ: pega o primeiro não-nulo
        cnpj = None
        for w in supplier_works:
            if w.contractor_document:
                cnpj = w.contractor_document
                break

        # Valores agregados
        valor_total = sum(_safe_number(w.contract_value) for w in supplier_works)
        valor_pago = sum(_safe_number(w.paid_value) for w in supplier_works)

        # Scores
        scores = [w.efficiency_score for w in supplier_works if w.efficiency_score is not None]
        score_medio = round(sum(scores) / len(scores), 1) if scores else None

        # Contagem de obras críticas e atrasadas
        obras_criticas = 0
        obras_atrasadas = 0
        for w in supplier_works:
            if w.efficiency_score is not None and w.efficiency_score < 40:
                obras_criticas += 1
            if w.due_at and not w.finished_at and w.due_at < today:
                obras_atrasadas += 1

        # Alertas
        all_alerts = []
        for w in supplier_works:
            if w.alerts:
                all_alerts.extend(w.alerts)

        alertas_totais = len(all_alerts)
        alertas_criticos = sum(1 for a in all_alerts if a.severity == "critical")

        # Aditivo médio percentual
        aditivo_percs = []
        for w in supplier_works:
            cv = _safe_number(w.contract_value)
            av = _safe_number(w.additive_value)
            if cv > 0 and av > 0:
                aditivo_percs.append((av / cv) * 100)
        aditivo_medio = round(sum(aditivo_percs) / len(aditivo_percs), 1) if aditivo_percs else 0.0

        # Bairros de atuação
        bairros = sorted(set(w.neighborhood for w in supplier_works if w.neighborhood))

        # Classificação e recomendação
        classificacao = _classificacao(score_medio, alertas_criticos)
        recomendacao = _recomendacao(classificacao, alertas_criticos, obras_criticas)

        results.append({
            "fornecedor": fornecedor_name,
            "cnpj": cnpj,
            "contratos": len(supplier_works),
            "obras": len(supplier_works),
            "valor_total": valor_total,
            "valor_pago": valor_pago,
            "score_medio": score_medio,
            "obras_criticas": obras_criticas,
            "obras_atrasadas": obras_atrasadas,
            "alertas_totais": alertas_totais,
            "alertas_criticos": alertas_criticos,
            "aditivo_medio_percentual": aditivo_medio,
            "bairros_atuacao": bairros,
            "classificacao": classificacao,
            "recomendacao": recomendacao,
            # Dados extras para detalhe
            "_works": supplier_works,
            "_alerts": all_alerts,
        })

    return results


def list_suppliers_ranking(
    db: Session,
    municipio: str | None = None,
    bairro: str | None = None,
    risco: str | None = None,
    limit: int = 50,
) -> list[SupplierRankingRead]:
    """
    Retorna ranking de fornecedores ordenado por score médio (pior primeiro).
    """
    aggregated = _aggregate_suppliers(db, municipio=municipio, bairro=bairro)

    # Filtra por risco se especificado
    if risco:
        risco_lower = risco.lower()
        aggregated = [s for s in aggregated if risco_lower in s["classificacao"].lower()]

    # Ordena por score médio (pior primeiro, None por último)
    aggregated.sort(key=lambda s: (s["score_medio"] is None, s["score_medio"] or 999))

    # Aplica limit
    aggregated = aggregated[:limit]

    return [
        SupplierRankingRead(
            fornecedor=s["fornecedor"],
            cnpj=s["cnpj"],
            contratos=s["contratos"],
            obras=s["obras"],
            valor_total=s["valor_total"],
            valor_pago=s["valor_pago"],
            score_medio=s["score_medio"],
            obras_criticas=s["obras_criticas"],
            obras_atrasadas=s["obras_atrasadas"],
            alertas_totais=s["alertas_totais"],
            alertas_criticos=s["alertas_criticos"],
            aditivo_medio_percentual=s["aditivo_medio_percentual"],
            bairros_atuacao=s["bairros_atuacao"],
            classificacao=s["classificacao"],
            recomendacao=s["recomendacao"],
        )
        for s in aggregated
    ]


def get_supplier_detail(
    db: Session,
    cnpj_or_name: str,
) -> SupplierDetailRead | None:
    """
    Busca detalhe de um fornecedor por CNPJ ou nome.
    Retorna SupplierDetailRead com obras, contratos, alertas e recomendações.
    """
    # Busca por CNPJ ou nome
    aggregated = _aggregate_suppliers(db)

    # Tenta encontrar por CNPJ primeiro
    found = None
    for s in aggregated:
        if s["cnpj"] and cnpj_or_name and s["cnpj"].replace(".", "").replace("/", "").replace("-", "") == cnpj_or_name.replace(".", "").replace("/", "").replace("-", ""):
            found = s
            break

    # Se não encontrou por CNPJ, busca por nome (case-insensitive)
    if not found:
        normalized_search = cnpj_or_name.strip().lower()
        for s in aggregated:
            if normalized_search in s["fornecedor"].lower():
                found = s
                break

    if not found:
        return None

    # Monta listas detalhadas
    obras_lista = []
    contratos_lista = []
    for w in found["_works"]:
        obra_item = {
            "id": w.id,
            "descricao": w.object_description,
            "municipio": w.municipio,
            "bairro": w.neighborhood,
            "score": round(w.efficiency_score, 1) if w.efficiency_score is not None else None,
            "status": "Concluída" if w.finished_at else ("Vencida" if w.due_at and w.due_at < date.today() else "Vigente"),
        }
        obras_lista.append(obra_item)

        contrato_item = {
            "id": f"work-{w.id}",
            "work_id": w.id,
            "numero_contrato": w.contract_number,
            "valor_original": _safe_number(w.contract_value),
            "valor_pago": _safe_number(w.paid_value),
            "data_inicio": w.signed_at.isoformat() if w.signed_at else None,
            "data_fim": w.due_at.isoformat() if w.due_at else None,
        }
        contratos_lista.append(contrato_item)

    alertas_lista = []
    for a in found["_alerts"]:
        alertas_lista.append({
            "id": a.id,
            "work_id": a.work_id,
            "code": a.code,
            "severity": a.severity,
            "message": a.message,
            "status": a.status or "Novo",
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })

    return SupplierDetailRead(
        fornecedor=found["fornecedor"],
        cnpj=found["cnpj"],
        contratos=found["contratos"],
        obras=found["obras"],
        valor_total=found["valor_total"],
        valor_pago=found["valor_pago"],
        score_medio=found["score_medio"],
        obras_criticas=found["obras_criticas"],
        obras_atrasadas=found["obras_atrasadas"],
        alertas_totais=found["alertas_totais"],
        alertas_criticos=found["alertas_criticos"],
        aditivo_medio_percentual=found["aditivo_medio_percentual"],
        bairros_atuacao=found["bairros_atuacao"],
        classificacao=found["classificacao"],
        recomendacao=found["recomendacao"],
        obras_lista=obras_lista,
        contratos_lista=contratos_lista,
        alertas_lista=alertas_lista,
    )
