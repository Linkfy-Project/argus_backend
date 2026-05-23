"""
Endpoint REST para consulta de camadas geoespaciais de Macaé.

Retorna FeatureCollection GeoJSON consumível por bibliotecas
de mapas como Leaflet e Mapbox GL.
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.geo import GeoLayer

router = APIRouter(prefix="/geo-layers", tags=["geo-layers"])


@router.get("/{layer_type}")
def get_geo_layer(layer_type: str, db: Session = Depends(get_db)):
    """
    Retorna um FeatureCollection GeoJSON de uma camada geoespacial.

    layer_type: 'municipality' | 'census_tract' | 'road'
    """
    valid_types = {"municipality", "census_tract", "road"}

    if layer_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Tipo inválido. Use um de: {', '.join(sorted(valid_types))}",
        )

    layers = (
        db.query(GeoLayer)
        .filter(GeoLayer.layer_type == layer_type)
        .order_by(GeoLayer.code)
        .all()
    )

    if not layers:
        raise HTTPException(
            status_code=404,
            detail=f"Nenhum dado encontrado para layer_type='{layer_type}'.",
        )

    features = []
    for layer in layers:
        geometry = layer.geojson_geometry
        # Se veio como string JSON, parsear para dict
        if isinstance(geometry, str):
            geometry = json.loads(geometry)

        properties = {
            "code": layer.code,
            "name": layer.name,
            "centroid": (
                {"lat": layer.centroid_lat, "lon": layer.centroid_lon}
                if layer.centroid_lat is not None
                else None
            ),
        }

        if layer.area_m2 is not None:
            properties["area_m2"] = layer.area_m2

        if layer.length_m is not None:
            properties["length_m"] = layer.length_m

        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": properties,
        })

    return {
        "type": "FeatureCollection",
        "layer_type": layer_type,
        "count": len(features),
        "features": features,
    }
