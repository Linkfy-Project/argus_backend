"""
Módulo de geocodificação de obras públicas.

Atribui coordenadas aleatórias DENTRO do polígono do município de Macaé
para obras que ainda não possuem latitude/longitude.

Silencioso por design — não emite logs intermediários.
"""

from __future__ import annotations

import json
import random

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.work import PublicWork
from app.models.geo import GeoLayer


def _point_in_polygon(lat: float, lon: float, polygon: list[list[float]]) -> bool:
    """Teste ray-casting para ponto dentro de polígono.

    Args:
        lat, lon: coordenadas do ponto
        polygon: lista de [lon, lat] do anel exterior do polígono
    """
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


def _get_municipality_polygon(db: Session) -> list[list[float]] | None:
    """Extrai o anel exterior do polígono do município a partir do banco."""
    layer = (
        db.query(GeoLayer)
        .filter(GeoLayer.layer_type == "municipality")
        .first()
    )
    if not layer:
        return None

    geom = layer.geojson_geometry
    if isinstance(geom, str):
        geom = json.loads(geom)

    # Suporta Polygon e MultiPolygon
    geom_type = geom.get("type", "")
    coords = geom.get("coordinates", [])
    if not coords:
        return None

    if geom_type == "Polygon":
        return coords[0]  # anel exterior
    elif geom_type == "MultiPolygon":
        # Usa o maior polígono
        biggest = max(coords, key=lambda p: len(p[0]))
        return biggest[0]

    return None


def _random_point_in_polygon(polygon: list[list[float]]) -> tuple[float, float]:
    """Gera um ponto aleatório (lat, lon) dentro do bounding box do polígono,
    rejeitando pontos fora do polígono real."""
    lons = [p[0] for p in polygon]
    lats = [p[1] for p in polygon]
    lon_min, lon_max = min(lons), max(lons)
    lat_min, lat_max = min(lats), max(lats)

    for _ in range(200):  # max tentativas
        lat = random.uniform(lat_min, lat_max)
        lon = random.uniform(lon_min, lon_max)
        if _point_in_polygon(lat, lon, polygon):
            return (round(lat, 6), round(lon, 6))

    # Fallback: centroide do bounding box
    return (round((lat_min + lat_max) / 2, 6), round((lon_min + lon_max) / 2, 6))


def assign_random_coordinates(db: Session) -> dict:
    """
    Atribui coordenadas aleatórias dentro do polígono do município
    para obras que não possuem latitude/longitude.

    Returns:
        dict com contadores: {"geocoded": int, "skipped": int}
    """
    stats = {"geocoded": 0, "skipped": 0}

    polygon = _get_municipality_polygon(db)

    works = (
        db.query(PublicWork)
        .filter(
            or_(PublicWork.latitude.is_(None), PublicWork.longitude.is_(None))
        )
        .all()
    )

    for work in works:
        if polygon:
            lat, lon = _random_point_in_polygon(polygon)
        else:
            # Fallback: bounding box de Macaé
            lat = round(random.uniform(-22.42, -22.34), 6)
            lon = round(random.uniform(-41.82, -41.70), 6)
        work.latitude = lat
        work.longitude = lon
        stats["geocoded"] += 1

    if stats["geocoded"] > 0:
        db.commit()

    return stats
