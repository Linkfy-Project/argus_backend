"""
Modelo SQLAlchemy para camadas geoespaciais de Macaé.

Armazena malhas do município, setores censitários (IBGE/geobr)
e malha viária (OSM/osmnx) como GeoJSON em EPSG:4326 com
metadados calculados na projeção EPSG:31983 (UTM 23S).
"""

from datetime import datetime
from sqlalchemy import (
    Column, DateTime, Float, Integer, String, Text, UniqueConstraint
)
from app.db.session import Base


class GeoLayer(Base):
    """Camadas geoespaciais de Macaé (município, setores censitários, malha viária)."""

    __tablename__ = "geo_layers"

    __table_args__ = (
        UniqueConstraint("layer_type", "code", name="uq_geo_layer_type_code"),
    )

    id = Column(Integer, primary_key=True, index=True)
    layer_type = Column(String(30), index=True, nullable=False)
    # 'municipality' | 'census_tract' | 'road'

    code = Column(String(50), index=True, nullable=False)
    # municipality → "3302403" (IBGE)
    # census_tract → "330240305000001" (código do setor)
    # road → "osm_12345678" (OSM id)

    name = Column(String(255), nullable=True)
    # "Macaé" | "SETOR 01" | "Rua Doutor Télio Barreto"

    geojson_geometry = Column(Text, nullable=False)
    # String GeoJSON da geometria em EPSG:4326 (WGS84)

    properties_json = Column(Text, nullable=True)
    # Propriedades extras do geobr/OSM como JSON string

    # Metadados espaciais (calculados na projeção EPSG:31983)
    centroid_lat = Column(Float, nullable=True)
    centroid_lon = Column(Float, nullable=True)
    length_m = Column(Float, nullable=True)
    area_m2 = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
