"""
Serviço de Análise Microterritorial de Macaé-RJ para o ARGUS.

Centraliza toda a lógica de negócio para os endpoints territoriais focados em Macaé-RJ.
Cada função retorna dados prontos para o frontend, sem necessidade de cálculo no navegador.

Responsabilidades:
- Calcular visão geral territorial (overview)
- Gerar ranking de bairros com indicadores de risco
- Detalhar um bairro específico com obras críticas, atrasadas e fornecedores
- Produzir GeoJSON para heatmap territorial
- Relatório de qualidade dos dados territoriais
- Gerar recomendações textuais automáticas baseadas nas métricas

Regras:
- Normalizar bairro nulo/vazio como "Não informado".
- Não quebrar com campos nulos.
- Usar "Macae"/"Macaé" de forma intercambiável.
- Não depender de dados externos nesta etapa.
"""

from __future__ import annotations

import logging
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models.work import Alert, PublicWork
from app.utils.obra_filter import filter_obras_query
from app.schemas.territory import (
    AlertaResumo,
    BairroResumo,
    DataQualityReport,
    FornecedorResumo,
    HeatmapResponse,
    NeighborhoodDetail,
    NeighborhoodListItem,
    ObraDataQualityIssue,
    ObraResumo,
    TerritoryOverview,
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

# Thresholds para classificação de risco do bairro
BAIRRO_CRITICO_THRESHOLD = 40.0  # score médio abaixo disso = crítico
BAIRRO_ALTO_RISCO_THRESHOLD = 60.0  # score médio abaixo disso = alto risco


# ── Funções auxiliares ─────────────────────────────────────────────────────

def _normalize_municipio(raw: str) -> str:
    """
    Normaliza o nome do município para busca case-insensitive e sem acentos.

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


def _apply_municipio_filter(q, municipio: str | None = None):
    """
    Aplica filtro de município na query usando func.unaccent().
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
        Rótulo da classificação.
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


def _normalize_neighborhood(neighborhood: str | None) -> str:
    """
    Normaliza o nome do bairro, convertendo nulo/vazio para "Não informado".

    Args:
        neighborhood: Nome do bairro vindo do banco.

    Returns:
        Nome do bairro ou "Não informado" se nulo/vazio.
    """
    if not neighborhood or not neighborhood.strip():
        return "Não informado"
    return neighborhood.strip()


def _strip_accents(text: str) -> str:
    """
    Remove acentos de um texto para comparação insensível a acentos.

    Args:
        text: Texto com possíveis acentos.

    Returns:
        Texto sem acentos em lowercase.
    """
    cleaned = unicodedata.normalize("NFD", text)
    cleaned = "".join(c for c in cleaned if unicodedata.category(c) != "Mn")
    return cleaned.strip().lower()


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


def _classify_neighborhood(score_medio: float, obras_criticas: int, alertas_criticos: int) -> str:
    """
    Classifica o bairro com base no score médio, obras críticas e alertas críticos.

    Args:
        score_medio: Score médio das obras do bairro.
        obras_criticas: Quantidade de obras críticas (score 0-39).
        alertas_criticos: Quantidade de alertas críticos.

    Returns:
        Classificação do bairro (Crítico, Alto risco, Atenção, Eficiente).
    """
    if score_medio < BAIRRO_CRITICO_THRESHOLD or obras_criticas >= 3:
        return "Crítico"
    if score_medio < BAIRRO_ALTO_RISCO_THRESHOLD or alertas_criticos >= 3:
        return "Alto risco"
    if score_medio < 80:
        return "Atenção"
    return "Eficiente"


def _generate_bairro_recommendation(
    classificacao: str,
    score_medio: float,
    obras_criticas: int,
    obras_atrasadas: int,
    alertas_criticos: int,
    fornecedor_mais_recorrente: str,
    obras_sem_geo: int,
    total_obras: int,
) -> str:
    """
    Gera recomendação textual para um bairro baseada nas métricas.

    Args:
        classificacao: Classificação de risco do bairro.
        score_medio: Score médio das obras.
        obras_criticas: Quantidade de obras críticas.
        obras_atrasadas: Quantidade de obras atrasadas.
        alertas_criticos: Quantidade de alertas críticos.
        fornecedor_mais_recorrente: Nome do fornecedor mais recorrente.
        obras_sem_geo: Obras sem geolocalização.
        total_obras: Total de obras no bairro.

    Returns:
        Texto de recomendação.
    """
    parts = []

    if classificacao == "Crítico":
        parts.append("Priorizar vistoria e revisar contratos de maior valor.")
    elif classificacao == "Alto risco":
        parts.append("Incluir bairro na pauta de controle interno.")
    elif classificacao == "Atenção":
        parts.append("Monitorar indicadores e solicitar relatórios de progresso.")

    if obras_criticas >= 3:
        parts.append(f"Bairro com {obras_criticas} obras críticas: priorizar fiscalização territorial.")

    if obras_atrasadas >= 3:
        parts.append(f"{obras_atrasadas} obras atrasadas: solicitar atualização de cronograma.")

    if alertas_criticos >= 3:
        parts.append(f"{alertas_criticos} alertas críticos: investigar causas imediatamente.")

    if fornecedor_mais_recorrente and obras_criticas >= 2:
        parts.append(f"Fornecedor '{fornecedor_mais_recorrente}' recorrente em obras críticas: revisar histórico contratual.")

    geo_pct = (obras_sem_geo / total_obras * 100) if total_obras > 0 else 0
    if geo_pct > 30:
        parts.append(f"Mais de {geo_pct:.0f}% das obras sem geolocalização: iniciar saneamento cadastral.")

    if score_medio < 60:
        parts.append("Score médio abaixo de 60: incluir bairro na pauta de controle interno.")

    if not parts:
        parts.append("Manter monitoramento regular dos indicadores.")

    return " ".join(parts)


def _generate_overview_recommendations(
    bairros_criticos: int,
    obras_sem_geo: int,
    total_obras: int,
    bairro_mais_critico: str,
    fornecedores_criticos: list[str],
) -> list[str]:
    """
    Gera lista de recomendações territoriais para a visão geral.

    Args:
        bairros_criticos: Quantidade de bairros críticos.
        obras_sem_geo: Quantidade de obras sem geolocalização.
        total_obras: Total de obras.
        bairro_mais_critico: Nome do bairro mais crítico.
        fornecedores_criticos: Lista de fornecedores recorrentes em obras críticas.

    Returns:
        Lista de recomendações textuais.
    """
    recomendacoes = []

    if bairros_criticos > 0:
        recomendacoes.append(
            f"Priorizar fiscalização nos bairros com score médio abaixo de 40 ({bairros_criticos} bairro(s) crítico(s))."
        )

    if bairro_mais_critico and bairro_mais_critico != "Não informado":
        recomendacoes.append(
            f"Realizar vistoria prioritária no bairro '{bairro_mais_critico}', o mais crítico do município."
        )

    geo_pct = (obras_sem_geo / total_obras * 100) if total_obras > 0 else 0
    if geo_pct > 30:
        recomendacoes.append(
            f"Mais de {geo_pct:.0f}% das obras ({obras_sem_geo}) sem geolocalização: iniciar saneamento cadastral."
        )
    elif obras_sem_geo > 0:
        recomendacoes.append(
            f"Sanear cadastro das {obras_sem_geo} obras sem geolocalização."
        )

    if fornecedores_criticos:
        nomes = ", ".join(fornecedores_criticos[:3])
        recomendacoes.append(
            f"Fornecedor(es) recorrente(s) em obras críticas: {nomes}. Revisar histórico contratual."
        )

    if not recomendacoes:
        recomendacoes.append("Nenhuma ação urgente identificada. Manter monitoramento regular.")

    return recomendacoes


# ── 1. Overview territorial ────────────────────────────────────────────────

def get_territory_overview(db: Session, municipio: str | None = None) -> TerritoryOverview:
    """
    Calcula a visão geral da análise microterritorial de Macaé-RJ.

    Responde:
    - Quais bairros concentram maior risco?
    - Onde estão as obras críticas?
    - Quais bairros têm maior valor contratado?
    - Quais bairros têm mais obras atrasadas?
    - Onde há concentração de fornecedores?
    - Quais regiões possuem dados ruins?
    - Que recomendações territoriais o gestor deve seguir?

    Args:
        db: Sessão do banco de dados.
        municipio: Nome do município para filtrar (padrão "Macae").

    Returns:
        TerritoryOverview com todos os indicadores preenchidos.
    """
    logger.debug("DEBUG: get_territory_overview - municipio='%s'", municipio)

    # Query base com filtro de município e carregamento de alertas
    q = _apply_municipio_filter(db.query(PublicWork), municipio)
    works = q.options(joinedload(PublicWork.alerts)).all()
    total_works = len(works)

    logger.debug("DEBUG: get_territory_overview - total_works=%d", total_works)

    if total_works == 0:
        return TerritoryOverview(
            municipio=_canonical_municipio(municipio or "Macae"),
            recomendacoes=["Nenhum dado disponível para análise territorial."],
        )

    hoje = date.today()

    # ── Acumuladores por bairro ──
    bairro_data: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "scores": [],
        "valor_total": 0.0,
        "obras_criticas": 0,
        "obras_atrasadas": 0,
        "alertas_criticos": 0,
    })

    # ── Contadores globais ──
    obras_sem_bairro = 0
    obras_sem_geolocalizacao = 0
    valor_total_contratado = 0.0
    bairros_set: set[str] = set()
    fornecedores_criticos_counter: Counter[str] = Counter()

    for work in works:
        bairro = _normalize_neighborhood(work.neighborhood)
        score = work.efficiency_score

        # Contagem de bairros
        if bairro != "Não informado":
            bairros_set.add(bairro)
        else:
            obras_sem_bairro += 1

        # Geolocalização
        if work.latitude is None or work.longitude is None:
            obras_sem_geolocalizacao += 1

        # Valor total
        valor_total_contratado += float(work.contract_value or 0)

        # Dados por bairro
        bd = bairro_data[bairro]
        if score is not None:
            bd["scores"].append(float(score))
        bd["valor_total"] += float(work.contract_value or 0)

        # Obras críticas (score 0-39)
        if score is not None and score < 40:
            bd["obras_criticas"] += 1
            # Conta fornecedores em obras críticas
            if work.contractor_name:
                fornecedores_criticos_counter[work.contractor_name] += 1

        # Obras atrasadas
        if _is_overdue(work, hoje):
            bd["obras_atrasadas"] += 1

        # Alertas críticos
        if work.alerts:
            bd["alertas_criticos"] += sum(1 for a in work.alerts if a.severity == "critical")

    # ── Cálculos globais ──
    all_scores = []
    for bd in bairro_data.values():
        all_scores.extend(bd["scores"])

    score_medio = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0.0
    bairros_monitorados = len(bairros_set)

    # ── Identificar bairros críticos (score médio < 40) ──
    bairros_criticos_count = 0
    bairro_mais_critico = ""
    bairro_maior_valor = ""
    bairro_mais_atrasos = ""

    pior_score = 101.0
    maior_valor = 0.0
    mais_atrasos = 0

    for bairro, bd in bairro_data.items():
        if bairro == "Não informado":
            continue
        media = sum(bd["scores"]) / len(bd["scores"]) if bd["scores"] else 100.0

        if media < BAIRRO_CRITICO_THRESHOLD:
            bairros_criticos_count += 1

        if media < pior_score:
            pior_score = media
            bairro_mais_critico = bairro

        if bd["valor_total"] > maior_valor:
            maior_valor = bd["valor_total"]
            bairro_maior_valor = bairro

        if bd["obras_atrasadas"] > mais_atrasos:
            mais_atrasos = bd["obras_atrasadas"]
            bairro_mais_atrasos = bairro

    # ── Fornecedores críticos ──
    fornecedores_criticos = [nome for nome, _ in fornecedores_criticos_counter.most_common(5)]

    # ── Recomendações ──
    recomendacoes = _generate_overview_recommendations(
        bairros_criticos=bairros_criticos_count,
        obras_sem_geo=obras_sem_geolocalizacao,
        total_obras=total_works,
        bairro_mais_critico=bairro_mais_critico,
        fornecedores_criticos=fornecedores_criticos,
    )

    logger.debug(
        "DEBUG: get_territory_overview - bairros=%d, criticos=%d, sem_geo=%d",
        bairros_monitorados, bairros_criticos_count, obras_sem_geolocalizacao,
    )

    return TerritoryOverview(
        municipio=_canonical_municipio(municipio or "Macae"),
        bairros_monitorados=bairros_monitorados,
        obras_monitoradas=total_works,
        valor_total_contratado=round(valor_total_contratado, 2),
        score_medio=score_medio,
        bairros_criticos=bairros_criticos_count,
        obras_sem_bairro=obras_sem_bairro,
        obras_sem_geolocalizacao=obras_sem_geolocalizacao,
        bairro_mais_critico=bairro_mais_critico or "Não informado",
        bairro_maior_valor=bairro_maior_valor or "Não informado",
        bairro_mais_atrasos=bairro_mais_atrasos or "Não informado",
        recomendacoes=recomendacoes,
    )


# ── 2. Lista de bairros ────────────────────────────────────────────────────

def get_neighborhoods_list(
    db: Session,
    municipio: str | None = None,
) -> list[NeighborhoodListItem]:
    """
    Retorna lista de bairros com indicadores agregados de risco territorial.
    Ordenada por maior risco: score médio menor, mais obras críticas,
    mais alertas críticos, maior valor contratado.

    Args:
        db: Sessão do banco de dados.
        municipio: Nome do município para filtrar.

    Returns:
        Lista de NeighborhoodListItem ordenada por risco.
    """
    logger.debug("DEBUG: get_neighborhoods_list - municipio='%s'", municipio)

    q = _apply_municipio_filter(db.query(PublicWork), municipio)
    works = q.options(joinedload(PublicWork.alerts)).all()

    logger.debug("DEBUG: get_neighborhoods_list - obras encontradas=%d", len(works))

    if not works:
        return []

    hoje = date.today()

    # ── Acumuladores por bairro ──
    bairro_data: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "obras": 0,
        "valor_total": 0.0,
        "valor_pago": 0.0,
        "scores": [],
        "obras_criticas": 0,
        "obras_alto_risco": 0,
        "obras_atrasadas": 0,
        "alertas_totais": 0,
        "alertas_criticos": 0,
        "fornecedores_counter": Counter(),
        "obras_sem_geo": 0,
    })

    for work in works:
        bairro = _normalize_neighborhood(work.neighborhood)
        score = work.efficiency_score
        bd = bairro_data[bairro]

        bd["obras"] += 1
        bd["valor_total"] += float(work.contract_value or 0)
        bd["valor_pago"] += float(work.paid_value or 0)

        if score is not None:
            bd["scores"].append(float(score))
            if score < 40:
                bd["obras_criticas"] += 1
            elif score < 60:
                bd["obras_alto_risco"] += 1

        if _is_overdue(work, hoje):
            bd["obras_atrasadas"] += 1

        if work.alerts:
            bd["alertas_totais"] += len(work.alerts)
            bd["alertas_criticos"] += sum(1 for a in work.alerts if a.severity == "critical")

        if work.contractor_name:
            bd["fornecedores_counter"][work.contractor_name] += 1

        if work.latitude is None or work.longitude is None:
            bd["obras_sem_geo"] += 1

    # ── Montar lista de resultados ──
    result: list[NeighborhoodListItem] = []

    for bairro, bd in bairro_data.items():
        score_medio = round(sum(bd["scores"]) / len(bd["scores"]), 2) if bd["scores"] else 0.0
        classificacao = _classify_neighborhood(score_medio, bd["obras_criticas"], bd["alertas_criticos"])

        # Fornecedor mais recorrente
        fornecedor_mais = ""
        if bd["fornecedores_counter"]:
            fornecedor_mais = bd["fornecedores_counter"].most_common(1)[0][0]

        recomendacao = _generate_bairro_recommendation(
            classificacao=classificacao,
            score_medio=score_medio,
            obras_criticas=bd["obras_criticas"],
            obras_atrasadas=bd["obras_atrasadas"],
            alertas_criticos=bd["alertas_criticos"],
            fornecedor_mais_recorrente=fornecedor_mais,
            obras_sem_geo=bd["obras_sem_geo"],
            total_obras=bd["obras"],
        )

        result.append(NeighborhoodListItem(
            bairro=bairro,
            obras=bd["obras"],
            valor_total=round(bd["valor_total"], 2),
            valor_pago=round(bd["valor_pago"], 2),
            score_medio=score_medio,
            obras_criticas=bd["obras_criticas"],
            obras_alto_risco=bd["obras_alto_risco"],
            obras_atrasadas=bd["obras_atrasadas"],
            alertas_totais=bd["alertas_totais"],
            alertas_criticos=bd["alertas_criticos"],
            fornecedores_distintos=len(bd["fornecedores_counter"]),
            fornecedor_mais_recorrente=fornecedor_mais,
            obras_sem_geolocalizacao=bd["obras_sem_geo"],
            classificacao=classificacao,
            recomendacao=recomendacao,
        ))

    # ── Ordenação por maior risco ──
    # Critério: score médio menor, mais obras críticas, mais alertas críticos, maior valor
    result.sort(key=lambda x: (
        x.score_medio,            # Menor score primeiro
        -x.obras_criticas,        # Mais obras críticas primeiro
        -x.alertas_criticos,      # Mais alertas críticos primeiro
        -x.valor_total,           # Maior valor primeiro
    ))

    logger.debug("DEBUG: get_neighborhoods_list - bairros retornados=%d", len(result))
    return result


# ── 3. Detalhe do bairro ───────────────────────────────────────────────────

def get_neighborhood_detail(
    db: Session,
    bairro: str,
    municipio: str | None = None,
) -> NeighborhoodDetail | None:
    """
    Retorna detalhe completo de um bairro com obras críticas, atrasadas,
    fornecedores, alertas, análise textual e ações recomendadas.

    Args:
        db: Sessão do banco de dados.
        bairro: Nome do bairro para detalhar.
        municipio: Nome do município para filtrar.

    Returns:
        NeighborhoodDetail ou None se o bairro não for encontrado.
    """
    logger.debug("DEBUG: get_neighborhood_detail - bairro='%s', municipio='%s'", bairro, municipio)

    # Busca obras do município com alertas carregados
    q = _apply_municipio_filter(db.query(PublicWork), municipio)
    works = q.options(joinedload(PublicWork.alerts)).all()

    # Normaliza o bairro de busca para comparação (sem acentos, lowercase)
    bairro_normalized = _strip_accents(bairro)

    # Filtra obras do bairro (comparação case-insensitive e sem acentos)
    bairro_works = [
        w for w in works
        if _strip_accents(_normalize_neighborhood(w.neighborhood)) == bairro_normalized
    ]

    if not bairro_works:
        logger.debug("DEBUG: get_neighborhood_detail - bairro '%s' não encontrado", bairro)
        return None

    logger.debug("DEBUG: get_neighborhood_detail - obras encontradas=%d", len(bairro_works))

    hoje = date.today()

    # ── Acumuladores ──
    scores = []
    valor_total = 0.0
    valor_pago = 0.0
    obras_criticas = 0
    obras_alto_risco = 0
    obras_atrasadas = 0
    alertas_totais = 0
    alertas_criticos = 0
    fornecedores_counter: Counter[str] = Counter()
    fornecedores_cnpj: dict[str, str | None] = {}

    obras_criticas_list: list[ObraResumo] = []
    obras_atrasadas_list: list[ObraResumo] = []
    alertas_list: list[AlertaResumo] = []

    for work in bairro_works:
        score = work.efficiency_score
        valor_total += float(work.contract_value or 0)
        valor_pago += float(work.paid_value or 0)

        if score is not None:
            scores.append(float(score))

        # Classificação
        is_critica = score is not None and score < 40
        is_alto_risco = score is not None and 40 <= score < 60

        if is_critica:
            obras_criticas += 1
        if is_alto_risco:
            obras_alto_risco += 1

        # Atraso
        delay_days = _compute_delay_days(work, hoje)
        is_delayed = delay_days > 0
        if is_delayed:
            obras_atrasadas += 1

        # Alertas
        alerts_list_work = work.alerts or []
        alerts_count = len(alerts_list_work)
        critical_count = sum(1 for a in alerts_list_work if a.severity == "critical")
        alertas_totais += alerts_count
        alertas_criticos += critical_count

        # Fornecedores
        if work.contractor_name:
            fornecedores_counter[work.contractor_name] += 1
            if work.contractor_name not in fornecedores_cnpj:
                fornecedores_cnpj[work.contractor_name] = work.contractor_document

        # Monta ObraResumo para obras críticas
        if is_critica:
            obras_criticas_list.append(ObraResumo(
                id=work.id,
                descricao=(work.object_description or "")[:120],
                fornecedor=work.contractor_name,
                score=score,
                classificacao=_classify_score(score),
                valor_contratado=float(work.contract_value or 0),
                dias_atraso=delay_days,
                alertas=alerts_count,
            ))

        # Monta ObraResumo para obras atrasadas
        if is_delayed:
            obras_atrasadas_list.append(ObraResumo(
                id=work.id,
                descricao=(work.object_description or "")[:120],
                fornecedor=work.contractor_name,
                score=score,
                classificacao=_classify_score(score),
                valor_contratado=float(work.contract_value or 0),
                dias_atraso=delay_days,
                alertas=alerts_count,
            ))

        # Monta AlertaResumo
        for alert in alerts_list_work:
            alertas_list.append(AlertaResumo(
                obra_id=work.id,
                code=alert.code,
                severity=alert.severity,
                message=alert.message[:200] if alert.message else "",
            ))

    # ── Cálculos finais ──
    score_medio = round(sum(scores) / len(scores), 2) if scores else 0.0
    classificacao = _classify_neighborhood(score_medio, obras_criticas, alertas_criticos)
    fornecedores_distintos = len(fornecedores_counter)

    # ── Top fornecedores ──
    principais_fornecedores: list[FornecedorResumo] = []
    for nome, count in fornecedores_counter.most_common(5):
        principais_fornecedores.append(FornecedorResumo(
            nome=nome,
            cnpj=fornecedores_cnpj.get(nome),
            obras=count,
            valor_total=0.0,  # Poderia ser calculado se necessário
            score_medio=0.0,  # Poderia ser calculado se necessário
        ))

    # Fornecedor mais recorrente para recomendação
    fornecedor_mais = ""
    if fornecedores_counter:
        fornecedor_mais = fornecedores_counter.most_common(1)[0][0]

    # Obras sem geolocalização no bairro
    obras_sem_geo = sum(
        1 for w in bairro_works
        if w.latitude is None or w.longitude is None
    )

    # ── Recomendação ──
    recomendacao = _generate_bairro_recommendation(
        classificacao=classificacao,
        score_medio=score_medio,
        obras_criticas=obras_criticas,
        obras_atrasadas=obras_atrasadas,
        alertas_criticos=alertas_criticos,
        fornecedor_mais_recorrente=fornecedor_mais,
        obras_sem_geo=obras_sem_geo,
        total_obras=len(bairro_works),
    )

    # ── Análise textual ──
    analise_textual = _generate_analise_textual(
        bairro=_normalize_neighborhood(bairro_works[0].neighborhood if bairro_works else bairro),
        total_obras=len(bairro_works),
        score_medio=score_medio,
        obras_criticas=obras_criticas,
        obras_atrasadas=obras_atrasadas,
        alertas_criticos=alertas_criticos,
        valor_total=valor_total,
        fornecedores_distintos=fornecedores_distintos,
        fornecedor_mais=fornecedor_mais,
        classificacao=classificacao,
    )

    # ── Ações recomendadas ──
    acoes = _generate_acoes_recomendadas(
        classificacao=classificacao,
        obras_criticas=obras_criticas,
        obras_atrasadas=obras_atrasadas,
        alertas_criticos=alertas_criticos,
        fornecedor_mais=fornecedor_mais,
        obras_sem_geo=obras_sem_geo,
        total_obras=len(bairro_works),
    )

    # Ordena listas por criticidade
    obras_criticas_list.sort(key=lambda x: x.score if x.score is not None else 0)
    obras_atrasadas_list.sort(key=lambda x: x.dias_atraso, reverse=True)
    alertas_list.sort(key=lambda x: 0 if x.severity == "critical" else 1)

    logger.debug("DEBUG: get_neighborhood_detail - concluído para '%s'", bairro)

    return NeighborhoodDetail(
        bairro=_normalize_neighborhood(bairro_works[0].neighborhood if bairro_works else bairro),
        resumo=BairroResumo(
            obras=len(bairro_works),
            valor_total=round(valor_total, 2),
            valor_pago=round(valor_pago, 2),
            score_medio=score_medio,
            obras_criticas=obras_criticas,
            obras_alto_risco=obras_alto_risco,
            obras_atrasadas=obras_atrasadas,
            alertas_totais=alertas_totais,
            alertas_criticos=alertas_criticos,
            fornecedores_distintos=fornecedores_distintos,
            classificacao=classificacao,
        ),
        obras_criticas=obras_criticas_list[:20],  # Limita a 20 para não sobrecarregar
        obras_atrasadas=obras_atrasadas_list[:20],
        principais_fornecedores=principais_fornecedores,
        alertas=alertas_list[:50],  # Limita a 50 alertas
        analise_textual=analise_textual,
        acoes_recomendadas=acoes,
    )


def _generate_analise_textual(
    bairro: str,
    total_obras: int,
    score_medio: float,
    obras_criticas: int,
    obras_atrasadas: int,
    alertas_criticos: int,
    valor_total: float,
    fornecedores_distintos: int,
    fornecedor_mais: str,
    classificacao: str,
) -> str:
    """
    Gera análise textual automática do bairro.

    Returns:
        Texto descritivo da situação do bairro.
    """
    parts = []
    parts.append(f"O bairro '{bairro}' possui {total_obras} obras monitoradas, ")
    parts.append(f"com valor total contratado de R$ {valor_total:,.2f}. ")

    if score_medio > 0:
        parts.append(f"O score médio de eficiência é {score_medio:.1f}, classificado como '{classificacao}'. ")
    else:
        parts.append("Não há scores calculados para as obras deste bairro. ")

    if obras_criticas > 0:
        parts.append(f"Há {obras_criticas} obra(s) com score crítico (abaixo de 40), indicando alto risco de ineficiência. ")

    if obras_atrasadas > 0:
        parts.append(f"{obras_atrasadas} obra(s) encontram-se atrasadas em relação ao prazo contratual. ")

    if alertas_criticos > 0:
        parts.append(f"Foram identificados {alertas_criticos} alerta(s) crítico(s) que demandam ação imediata. ")

    if fornecedores_distintos > 0:
        parts.append(f"Há {fornecedores_distintos} fornecedor(es) distinto(s) atuando no bairro")
        if fornecedor_mais:
            parts.append(f", sendo '{fornecedor_mais}' o mais recorrente.")
        else:
            parts.append(".")

    return "".join(parts)


def _generate_acoes_recomendadas(
    classificacao: str,
    obras_criticas: int,
    obras_atrasadas: int,
    alertas_criticos: int,
    fornecedor_mais: str,
    obras_sem_geo: int,
    total_obras: int,
) -> list[str]:
    """
    Gera lista de ações recomendadas para o bairro.

    Returns:
        Lista de ações recomendadas.
    """
    acoes = []

    if obras_criticas > 0:
        acoes.append(f"Realizar vistoria nas {obras_criticas} obra(s) críticas.")

    if obras_atrasadas > 0:
        acoes.append(f"Solicitar atualização de cronograma das {obras_atrasadas} obra(s) atrasadas.")

    if alertas_criticos > 0:
        acoes.append(f"Investigar os {alertas_criticos} alerta(s) críticos e tomar medidas corretivas.")

    if classificacao == "Crítico":
        acoes.append("Incluir bairro na pauta de controle interno prioritário.")
        acoes.append("Revisar contratos de maior valor no bairro.")

    if fornecedor_mais and obras_criticas >= 2:
        acoes.append(f"Revisar histórico contratual do fornecedor '{fornecedor_mais}'.")

    geo_pct = (obras_sem_geo / total_obras * 100) if total_obras > 0 else 0
    if geo_pct > 30:
        acoes.append("Iniciar saneamento cadastral para obras sem geolocalização.")

    if not acoes:
        acoes.append("Manter monitoramento regular dos indicadores do bairro.")

    return acoes


# ── 4. Heatmap (GeoJSON) ──────────────────────────────────────────────────

def get_heatmap_geojson(db: Session, municipio: str | None = None) -> HeatmapResponse:
    """
    Produz FeatureCollection GeoJSON com obras georreferenciadas e propriedades úteis
    para o mapa de calor territorial.

    Args:
        db: Sessão do banco de dados.
        municipio: Nome do município para filtrar.

    Returns:
        HeatmapResponse com FeatureCollection GeoJSON.
    """
    logger.debug("DEBUG: get_heatmap_geojson - municipio='%s'", municipio)

    q = _apply_municipio_filter(db.query(PublicWork), municipio)
    # Filtra apenas obras com coordenadas válidas
    q = q.filter(
        PublicWork.latitude.isnot(None),
        PublicWork.longitude.isnot(None),
    )
    works = q.options(joinedload(PublicWork.alerts)).all()

    logger.debug("DEBUG: get_heatmap_geojson - obras georreferenciadas=%d", len(works))

    hoje = date.today()
    features = []

    for work in works:
        score = work.efficiency_score
        delay_days = _compute_delay_days(work, hoje)
        alerts_list = work.alerts or []
        alerts_count = len(alerts_list)

        # Propriedades do feature
        properties = {
            "obra_id": work.id,
            "nome": (work.object_description or "")[:120],
            "bairro": _normalize_neighborhood(work.neighborhood),
            "score": score,
            "classificacao": _classify_score(score),
            "valor_contratado": float(work.contract_value or 0),
            "alertas": alerts_count,
            "dias_atraso": delay_days,
            "fornecedor": work.contractor_name,
        }

        # Feature GeoJSON
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(work.longitude), float(work.latitude)],
            },
            "properties": properties,
        }
        features.append(feature)

    logger.debug("DEBUG: get_heatmap_geojson - features retornados=%d", len(features))

    return HeatmapResponse(
        type="FeatureCollection",
        features=features,
    )


# ── 5. Data Quality ────────────────────────────────────────────────────────

def get_data_quality_report(db: Session, municipio: str | None = None) -> DataQualityReport:
    """
    Gera relatório de qualidade dos dados territoriais de Macaé-RJ.
    Identifica obras com campos obrigatórios faltantes para saneamento cadastral.

    Critérios de qualidade:
    - Bairro preenchido
    - Geolocalização (latitude/longitude) preenchida
    - Valor contratado preenchido e > 0
    - Fornecedor preenchido
    - Prazo (due_at) preenchido

    Args:
        db: Sessão do banco de dados.
        municipio: Nome do município para filtrar.

    Returns:
        DataQualityReport com métricas e lista de obras para saneamento.
    """
    logger.debug("DEBUG: get_data_quality_report - municipio='%s'", municipio)

    q = _apply_municipio_filter(db.query(PublicWork), municipio)
    works = q.options(joinedload(PublicWork.alerts)).all()
    total_works = len(works)

    logger.debug("DEBUG: get_data_quality_report - total_works=%d", total_works)

    if total_works == 0:
        return DataQualityReport()

    # ── Contadores de problemas ──
    obras_sem_bairro = 0
    obras_sem_geolocalizacao = 0
    obras_sem_valor = 0
    obras_sem_fornecedor = 0
    obras_sem_prazo = 0

    # ── Campos preenchidos para score de qualidade ──
    # 5 campos por obra: bairro, geo, valor, fornecedor, prazo
    quality_fields_filled = 0
    quality_fields_total = total_works * 5

    # ── Lista de obras com problemas ──
    obras_para_saneamento: list[ObraDataQualityIssue] = []

    for work in works:
        problemas: list[str] = []

        # Bairro
        if not work.neighborhood or not work.neighborhood.strip():
            obras_sem_bairro += 1
            problemas.append("Bairro não informado")
        else:
            quality_fields_filled += 1

        # Geolocalização
        if work.latitude is None or work.longitude is None:
            obras_sem_geolocalizacao += 1
            problemas.append("Sem geolocalização (latitude/longitude)")
        else:
            quality_fields_filled += 1

        # Valor contratado
        if work.contract_value is None or work.contract_value <= 0:
            obras_sem_valor += 1
            problemas.append("Valor contratado não informado ou zero")
        else:
            quality_fields_filled += 1

        # Fornecedor
        if not work.contractor_name or not work.contractor_name.strip():
            obras_sem_fornecedor += 1
            problemas.append("Fornecedor não informado")
        else:
            quality_fields_filled += 1

        # Prazo
        if work.due_at is None:
            obras_sem_prazo += 1
            problemas.append("Prazo (due_at) não informado")
        else:
            quality_fields_filled += 1

        # Se tem algum problema, adiciona à lista de saneamento
        if problemas:
            obras_para_saneamento.append(ObraDataQualityIssue(
                id=work.id,
                descricao=(work.object_description or "")[:120],
                bairro=_normalize_neighborhood(work.neighborhood),
                problemas=problemas,
            ))

    # Score de qualidade: percentual de campos preenchidos
    data_quality_score = round((quality_fields_filled / quality_fields_total) * 100, 1) if quality_fields_total > 0 else 0.0

    # Ordena obras para saneamento: mais problemas primeiro
    obras_para_saneamento.sort(key=lambda x: len(x.problemas), reverse=True)

    logger.debug(
        "DEBUG: get_data_quality_report - quality_score=%.1f, obras_para_saneamento=%d",
        data_quality_score, len(obras_para_saneamento),
    )

    return DataQualityReport(
        total_obras=total_works,
        obras_sem_bairro=obras_sem_bairro,
        obras_sem_geolocalizacao=obras_sem_geolocalizacao,
        obras_sem_valor=obras_sem_valor,
        obras_sem_fornecedor=obras_sem_fornecedor,
        obras_sem_prazo=obras_sem_prazo,
        data_quality_score=data_quality_score,
        obras_para_saneamento=obras_para_saneamento[:100],  # Limita a 100 para não sobrecarregar
    )
