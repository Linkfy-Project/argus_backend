"""
Módulo de sincronização do IDH (IDHM) por setor censitário.

Fluxo:
1. Busca o IDHM municipal de Macaé via API SIDRA/IBGE.
2. Armazena o IDHM no properties_json de cada GeoLayer de setor censitário.
3. Faz join espacial: para cada obra com coordenadas, encontra o setor
   censitário que contém o ponto e atribui o IDHM à obra.

Silencioso por design — não emite logs intermediários.
"""

from __future__ import annotations

import json
from typing import Optional

import requests
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.geo import GeoLayer
from app.models.work import PublicWork

# IDHM municipal de Macaé (Atlas Brasil / PNUD, Censo 2010) — fallback
MACAE_IDHM_FALLBACK = 0.789

# API SIDRA: Tabela 137 (IDHM), Variável 468 (IDHM), Nível 7 (Município)
SIDRA_MUNICIPAL_URL = "https://apisidra.ibge.gov.br/values/t/137/n7/3302403/v/468/p/2010"

SIDRA_TIMEOUT_S = 10


def _fetch_municipal_idhm() -> Optional[float]:
    """Busca o IDHM municipal de Macaé na API SIDRA."""
    try:
        resp = requests.get(SIDRA_MUNICIPAL_URL, timeout=SIDRA_TIMEOUT_S)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if isinstance(data, list) and len(data) >= 2:
            value_str = data[1].get("V", "")
            if value_str and value_str != "...":
                return float(value_str)
    except Exception:
        pass
    return None


def _point_in_polygon(lat: float, lon: float, polygon: list[list[float]]) -> bool:
    """Teste ray-casting para ponto dentro de polígono."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _get_polygon_from_geojson(geom: dict) -> Optional[list[list[float]]]:
    """Extrai anel exterior do GeoJSON (Polygon ou MultiPolygon)."""
    geom_type = geom.get("type", "")
    coords = geom.get("coordinates", [])
    if not coords:
        return None
    if geom_type == "Polygon":
        return coords[0]
    elif geom_type == "MultiPolygon":
        biggest = max(coords, key=lambda p: len(p[0]))
        return biggest[0]
    return None


def sync_idh(db: Session) -> dict:
    """
    Sincroniza IDHM por setor censitário e associa às obras.

    Returns:
        dict com contadores.
    """
    stats = {"tracts_updated": 0, "works_updated": 0, "idhm_source": "unknown"}

    # ── 1. Buscar IDHM municipal ──
    idhm = _fetch_municipal_idhm()
    if idhm is None:
        idhm = MACAE_IDHM_FALLBACK
        stats["idhm_source"] = "municipal_fallback"
    else:
        stats["idhm_source"] = "sidra"

    # ── 2. Atualizar setores censitários ──
    tracts = (
        db.query(GeoLayer)
        .filter(GeoLayer.layer_type == "census_tract")
        .all()
    )

    if not tracts:
        return stats

    for tract in tracts:
        props = {}
        if tract.properties_json:
            try:
                props = json.loads(tract.properties_json)
            except (json.JSONDecodeError, TypeError):
                pass

        props["idhm"] = idhm
        props["idhm_source"] = stats["idhm_source"]
        tract.properties_json = json.dumps(props, ensure_ascii=False)
        stats["tracts_updated"] += 1

    db.flush()

    # ── 3. Join espacial: atribuir IDHM às obras ──
    works = (
        db.query(PublicWork)
        .filter(
            PublicWork.latitude.isnot(None),
            PublicWork.longitude.isnot(None),
            or_(PublicWork.idh.is_(None), PublicWork.idh == 0),
        )
        .all()
    )

    # Pré-carregar polígonos dos setores censitários
    tract_polygons: list[tuple[str, list[list[float]]]] = []
    for tract in tracts:
        geom = tract.geojson_geometry
        if isinstance(geom, str):
            geom = json.loads(geom)
        polygon = _get_polygon_from_geojson(geom)
        if polygon:
            tract_polygons.append((tract.code, polygon))

    for work in works:
        lat = work.latitude
        lon = work.longitude
        if lat is None or lon is None:
            continue

        # Procurar o setor censitário que contém o ponto
        for code, polygon in tract_polygons:
            if _point_in_polygon(lat, lon, polygon):
                work.idh = idhm
                stats["works_updated"] += 1
                break

    if stats["works_updated"] > 0 or stats["tracts_updated"] > 0:
        db.commit()

    return stats
