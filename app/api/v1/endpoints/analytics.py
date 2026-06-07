"""
Endpoints de analytics do ARGUS.

Fornece resumos, rankings, tendências e comparações intermunicipais.
Inclui normalização de nomes de municípios para evitar duplicatas
(como "Macae" e "Macaé" aparecendo como municípios separados).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db.session import get_db
from app.models.work import PublicWork, Alert

import logging
import unicodedata

# Logger para debug/info ao invés de print()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _normalize_filter_term(raw: str) -> str:
    """
    Normaliza termo de busca de município removendo acentos.
    Usado em conjunto com func.unaccent() no SQL para comparação insensível a acentos.

    Args:
        raw: Termo de busca informado pelo usuário (ex: "macae").

    Returns:
        Termo sem acentos em lowercase (ex: "macae").
    """
    if not raw:
        return raw
    cleaned = unicodedata.normalize("NFD", raw)
    cleaned = "".join(c for c in cleaned if unicodedata.category(c) != "Mn")
    return cleaned.strip().lower()


def _normalize_municipio(raw: str) -> str:
    """
    Normaliza nome de município para formato canônico no resultado da API.

    Remove acentos para comparação, aplica mapeamento canônico e retorna
    o nome padronizado. Usado como medida de segurança no endpoint
    inter-municipal para evitar duplicatas residuais.

    Args:
        raw: Nome bruto do município vindo do banco de dados.

    Returns:
        Nome do município normalizado (ex: "Macaé").
    """
    if not raw:
        return raw
    # Remove acentos para comparação case-insensitive
    cleaned = unicodedata.normalize("NFD", raw)
    cleaned = "".join(c for c in cleaned if unicodedata.category(c) != "Mn")
    cleaned = cleaned.strip().lower()
    # Mapeamento canônico de variações conhecidas
    canonical_map = {"macae": "Macaé", "macaé": "Macaé"}
    return canonical_map.get(cleaned, raw.strip())

@router.get("/summary")
def summary(municipio: str | None = None, db: Session = Depends(get_db)):
    q = db.query(PublicWork)
    if municipio:
        # Usa unaccent() para remover acentos de AMBOS os lados da comparação
        # Exemplo: busca "macae" encontra "Macaé" no banco
        normalized = _normalize_filter_term(municipio)
        q = q.filter(func.unaccent(PublicWork.municipio).ilike(f"%{normalized}%"))
    total = q.count()
    avg_score = q.with_entities(func.avg(PublicWork.efficiency_score)).scalar()
    delayed = q.filter(PublicWork.deadline_score < 100).count()
    critical_alerts = db.query(Alert).filter(Alert.severity == "critical").count()
    return {
        "total_works": total,
        "average_efficiency_score": round(float(avg_score or 0), 2),
        "delayed_works": delayed,
        "critical_alerts": critical_alerts,
    }

@router.get("/rankings")
def rankings(limit: int = 10, db: Session = Depends(get_db)):
    worst = db.query(PublicWork).order_by(PublicWork.efficiency_score.asc().nullslast()).limit(limit).all()
    best = db.query(PublicWork).order_by(PublicWork.efficiency_score.desc().nullslast()).limit(limit).all()
    def pack(work: PublicWork):
        return {"id": work.id, "municipio": work.municipio, "object_description": work.object_description, "contractor_name": work.contractor_name, "efficiency_score": work.efficiency_score}
    return {"worst": [pack(w) for w in worst], "best": [pack(w) for w in best]}

@router.get("/map/geojson")
def geojson(db: Session = Depends(get_db)):
    works = db.query(PublicWork).filter(PublicWork.latitude.isnot(None), PublicWork.longitude.isnot(None)).all()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [w.longitude, w.latitude]},
                "properties": {"id": w.id, "municipio": w.municipio, "score": w.efficiency_score, "objeto": w.object_description, "contratado": w.contractor_name},
            }
            for w in works
        ],
    }

@router.get("/trends")
def trends(municipio: str | None = None, db: Session = Depends(get_db)):
    """
    Retorna evolução mensal dos scores para gráfico de tendência.
    Agrupa obras por mês de assinatura e calcula médias.
    """
    from sqlalchemy import extract
    from collections import defaultdict

    q = db.query(PublicWork)
    if municipio:
        # Usa unaccent() para remover acentos de AMBOS os lados da comparação
        normalized = _normalize_filter_term(municipio)
        q = q.filter(func.unaccent(PublicWork.municipio).ilike(f"%{normalized}%"))

    works = q.filter(PublicWork.signed_at.isnot(None)).all()

    monthly = defaultdict(lambda: {"scores": [], "count": 0, "value": 0.0})
    for w in works:
        if w.signed_at:
            key = w.signed_at.strftime("%Y-%m")
            monthly[key]["count"] += 1
            monthly[key]["value"] += float(w.contract_value or 0)
            if w.efficiency_score is not None:
                monthly[key]["scores"].append(float(w.efficiency_score))

    result = []
    for month in sorted(monthly.keys()):
        data = monthly[month]
        avg = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0
        result.append({
            "month": month,
            "avg_score": round(avg, 2),
            "count": data["count"],
            "total_value": round(data["value"], 2),
        })

    return result


@router.get("/inter-municipal")
def inter_municipal(db: Session = Depends(get_db)):
    """
    Compara indicadores entre municípios para análise intermunicipal.

    Após a query SQL, aplica normalização de nomes de municípios para
    agrupar variações residuais (ex: "Macae" + "Macaé") em um único
    registro canônico, somando totais e fazendo média ponderada dos scores.
    """
    from sqlalchemy import func
    from collections import defaultdict

    rows = (
        db.query(
            PublicWork.municipio,
            func.count(PublicWork.id).label("total"),
            func.avg(PublicWork.efficiency_score).label("avg_score"),
            func.sum(PublicWork.contract_value).label("total_value"),
            func.sum(PublicWork.risk_delay_probability).label("avg_delay_risk"),
        )
        .group_by(PublicWork.municipio)
        .all()
    )

    # ── Normalização pós-query para agrupar variações residuais ──
    # Usa dicionário para acumular totais por município canônico
    grouped: dict[str, dict] = defaultdict(lambda: {
        "total_works": 0,
        "weighted_score_sum": 0.0,
        "score_count": 0,
        "total_value": 0.0,
        "avg_delay_risk_sum": 0.0,
    })

    for r in rows:
        canonical = _normalize_municipio(r.municipio or "")
        g = grouped[canonical]
        g["total_works"] += r.total
        g["total_value"] += float(r.total_value or 0)
        g["avg_delay_risk_sum"] += float((r.avg_delay_risk or 0) / max(r.total, 1))
        # Acumula score ponderado pelo número de obras para média ponderada
        if r.avg_score is not None:
            g["weighted_score_sum"] += float(r.avg_score) * r.total
            g["score_count"] += r.total

    # ── Monta resultado final com médias ponderadas ──
    logger.debug("inter_municipal - %d linhas do SQL normalizadas para %d municípios", len(rows), len(grouped))
    result = []
    for municipio, g in grouped.items():
        avg_score = g["weighted_score_sum"] / g["score_count"] if g["score_count"] > 0 else 0.0
        avg_delay = g["avg_delay_risk_sum"] / max(g["total_works"], 1)
        result.append({
            "municipio": municipio,
            "total_works": g["total_works"],
            "avg_score": round(avg_score, 2),
            "total_value": round(g["total_value"], 2),
            "avg_delay_risk": round(avg_delay, 4),
        })

    return result
