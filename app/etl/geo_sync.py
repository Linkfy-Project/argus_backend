"""
Módulo de sincronização de camadas geoespaciais de Macaé.

Pipeline:
1. Baixa dados do geobr (município + setores censitários) e osmnx (malha viária)
2. Reprojeta para EPSG:31983 (UTM 23S) para cálculos métricos
3. Calcula área, comprimento e centróide como colunas do DataFrame
4. Simplifica geometrias com Douglas-Peucker (5m de tolerância)
5. Converte de volta para EPSG:4326 e gera GeoJSON
6. Faz UPSERT no banco SQLite

⚠️ Nunca use .iloc[idx] dentro de .iterrows() — idx é label, não posição.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.geo import GeoLayer

logger = logging.getLogger(__name__)

# Código IBGE de Macaé (7 dígitos) - usar inteiro, pois geobr 0.2.2 compara como int
MUNICIPIO_CODE = 3302403

# Nome para queries no OSM
OSM_PLACE = "Macaé, Rio de Janeiro, Brazil"

# EPSG:31983 = SIRGAS 2000 / UTM zone 23S (abrange o RJ, unidade: metros)
CRS_METRIC = "EPSG:31983"

# EPSG:4326 = WGS84 (padrão para GeoJSON na web)
CRS_GEOGRAPHIC = "EPSG:4326"

# Tolerância de simplificação em metros (Douglas-Peucker)
SIMPLIFY_TOLERANCE_M = 5.0


# ──────────────────────────────────────────────
# Pipeline de processamento de GeoDataFrame
# ──────────────────────────────────────────────


def _process_gdf(gdf: Any, layer_type: str, tolerance: float = SIMPLIFY_TOLERANCE_M) -> list[dict]:
    """
    Aplica o pipeline completo de transformação em um GeoDataFrame.

    Etapas:
    1. Reprojetar para CRS métrico (EPSG:31983)
    2. Calcular área/comprimento e centróide como colunas do DataFrame
    3. Simplificar geometria (Douglas-Peucker) na projeção métrica
    4. Reprojetar de volta para EPSG:4326
    5. Converter geometry → GeoJSON string
    6. Retornar lista de dicionários prontos para o banco
    """
    import geopandas as gpd
    import shapely

    if gdf is None or gdf.empty:
        logger.warning("[GEO] GeoDataFrame vazio para layer_type=%s", layer_type)
        return []

    gdf = gdf.copy()

    if gdf.crs is None:
        gdf.set_crs(CRS_GEOGRAPHIC, inplace=True)

    # 1. Reprojetar para CRS métrico para cálculos exatos
    gdf_metric = gdf.to_crs(CRS_METRIC)

    # 2. Calcular métricas diretamente como colunas do DataFrame (evita problemas com iloc)
    gdf_metric["calc_centroid"] = gdf_metric.geometry.centroid

    if layer_type == "road":
        gdf_metric["calc_length"] = gdf_metric.geometry.length
        gdf_metric["calc_area"] = None
    else:
        gdf_metric["calc_area"] = gdf_metric.geometry.area
        gdf_metric["calc_length"] = None

    # 3. Simplificar a geometria principal (Douglas-Peucker) na projeção métrica
    gdf_metric.geometry = gdf_metric.geometry.simplify(
        tolerance=tolerance, preserve_topology=True
    )

    # 4. Reprojetar a geometria principal de volta para EPSG:4326 (Web)
    gdf_final = gdf_metric.to_crs(CRS_GEOGRAPHIC)

    # 5. Reprojetar a coluna de centroides separadamente para EPSG:4326
    gdf_final["calc_centroid"] = gpd.GeoSeries(
        gdf_metric["calc_centroid"], crs=CRS_METRIC
    ).to_crs(CRS_GEOGRAPHIC)

    # 6. Iteração segura usando os dados da própria linha (NUNCA .iloc[idx]!)
    records = []
    for idx, row in gdf_final.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        geom_geojson = json.dumps(shapely.geometry.mapping(geom))
        centroid_reprojetado = row["calc_centroid"]

        # Coleta propriedades extras dinamicamente
        props = {}
        for col in gdf_final.columns:
            if col not in [
                "geometry",
                "calc_centroid",
                "calc_area",
                "calc_length",
                "code",
                "name",
            ]:
                val = row[col]
                if isinstance(val, (datetime,)):
                    val = val.isoformat()
                elif hasattr(val, "item"):
                    val = val.item()
                props[col] = val

        # Trata o identificador único de forma segura para strings
        # Usa o identificador único da coluna "code" já preparada
        # (pela função _load_roads() ou _load_municipality()/_load_census_tracts())
        # NÃO usa idx nem osmid para evitar duplicatas com MultiIndex do OSM
        codigo_final = str(row.get("code", str(idx)))

        record = {
            "layer_type": layer_type,
            "code": codigo_final,
            "name": str(row.get("name", row.get("nome", ""))) or None,
            "geojson_geometry": geom_geojson,
            "properties_json": json.dumps(props, ensure_ascii=False, default=str) if props else None,
            "centroid_lat": round(centroid_reprojetado.y, 6) if centroid_reprojetado else None,
            "centroid_lon": round(centroid_reprojetado.x, 6) if centroid_reprojetado else None,
            "length_m": round(float(row["calc_length"]), 2) if row["calc_length"] is not None else None,
            "area_m2": round(float(row["calc_area"]), 2) if row["calc_area"] is not None else None,
        }
        records.append(record)

    return records


# ──────────────────────────────────────────────
# Funções de carga por camada
# ──────────────────────────────────────────────


def _load_municipality() -> list[dict]:
    """Baixa o polígono do município de Macaé via geobr."""
    from geobr import read_municipality

    logger.info("[GEO] Baixando malha do município (%s)...", MUNICIPIO_CODE)
    gdf = read_municipality(code_muni=MUNICIPIO_CODE, year=2020)
    gdf = gdf.to_crs(CRS_GEOGRAPHIC)

    # Normalizar colunas para nomes padronizados
    if "code_muni" in gdf.columns:
        gdf["code"] = gdf["code_muni"].astype(str)
    else:
        gdf["code"] = MUNICIPIO_CODE

    if "name_muni" in gdf.columns:
        gdf["name"] = gdf["name_muni"]
    else:
        gdf["name"] = "Macaé"

    return _process_gdf(gdf, layer_type="municipality")


def _load_census_tracts() -> list[dict]:
    """Baixa os setores censitários de Macaé via geobr."""
    from geobr import read_census_tract

    logger.info("[GEO] Baixando setores censitários de Macaé...")
    gdf = read_census_tract(code_tract=MUNICIPIO_CODE, year=2010)
    gdf = gdf.to_crs(CRS_GEOGRAPHIC)

    if gdf.empty:
        logger.warning("[GEO] Nenhum setor censitário encontrado para Macaé.")
        return []

    # Normalizar colunas
    if "code_tract" in gdf.columns:
        gdf["code"] = gdf["code_tract"].astype(str)
    else:
        gdf["code"] = gdf.index.astype(str)

    return _process_gdf(gdf, layer_type="census_tract")


def _load_roads() -> list[dict]:
    """Baixa a malha viária de Macaé via osmnx (network_type='drive')."""
    import osmnx as ox

    logger.info("[GEO] Baixando malha viária de Macaé via OSM...")
    graph = ox.graph_from_place(
        OSM_PLACE,
        network_type="drive",
        simplify=True,
    )
    gdf_edges = ox.graph_to_gdfs(graph, nodes=False, edges=True)

    if gdf_edges.empty:
        logger.warning("[GEO] Nenhuma via encontrada para Macaé no OSM.")
        return []

    # Gerar código único baseado no índice MultiIndex (u, v, key)
    # O índice do osmnx é (u, v, key) e é garantidamente único,
    # evitando duplicatas que ocorrem com o osmid (que pode ser lista)
    gdf_edges["code"] = gdf_edges.index.to_series().apply(
        lambda idx: f"osm_{idx[0]}_{idx[1]}_{idx[2]}"
    )

    if "name" not in gdf_edges.columns:
        gdf_edges["name"] = None

    return _process_gdf(gdf_edges, layer_type="road")


# ──────────────────────────────────────────────
# UPSERT no banco
# ──────────────────────────────────────────────


def _upsert_records(db: Session, records: list[dict], layer_type: str) -> dict:
    """Insere ou atualiza registros no banco via UPSERT.

    Identificador único: (layer_type, code).
    """
    created = 0
    updated = 0
    now = datetime.utcnow()

    for rec in records:
        existing = (
            db.query(GeoLayer)
            .filter(
                GeoLayer.layer_type == layer_type,
                GeoLayer.code == rec["code"],
            )
            .first()
        )

        if existing:
            # Atualizar registro existente
            existing.geojson_geometry = rec["geojson_geometry"]
            existing.properties_json = rec.get("properties_json")
            existing.centroid_lat = rec.get("centroid_lat")
            existing.centroid_lon = rec.get("centroid_lon")
            existing.length_m = rec.get("length_m")
            existing.area_m2 = rec.get("area_m2")
            existing.name = rec.get("name")
            existing.updated_at = now
            updated += 1
        else:
            # Criar novo registro
            new_layer = GeoLayer(
                layer_type=layer_type,
                code=rec["code"],
                name=rec.get("name"),
                geojson_geometry=rec["geojson_geometry"],
                properties_json=rec.get("properties_json"),
                centroid_lat=rec.get("centroid_lat"),
                centroid_lon=rec.get("centroid_lon"),
                length_m=rec.get("length_m"),
                area_m2=rec.get("area_m2"),
                created_at=now,
                updated_at=now,
            )
            db.add(new_layer)
            created += 1

    db.commit()

    result = {"created": created, "updated": updated, "total": len(records)}
    logger.info(
        "[GEO] UPSERT %s: %d created, %d updated, %d total",
        layer_type, created, updated, len(records),
    )
    return result


# ──────────────────────────────────────────────
# Função principal (chamada pelo job)
# ──────────────────────────────────────────────


def sync_geo_layers() -> dict:
    """
    Sincroniza as camadas geoespaciais de Macaé no banco.

    Guarda condicional:
    - Se a tabela geo_layers já tiver dados → apenas loga e retorna
    - Se estiver vazia → executa pipeline completo

    Retorna:
        dict com status de cada camada
    """
    started_at = datetime.now()
    logger.info("[GEO] Iniciando sincronização de camadas geoespaciais...")

    db = SessionLocal()
    try:
        # ── GUARDA: verificar se já existem dados ──
        count = db.query(GeoLayer).count()

        if count > 0:
            logger.info(
                "[GEO] Tabela geo_layers já possui %d registros. Pulando etapa.",
                count,
            )
            return {
                "status": "skipped",
                "reason": f"Tabela geo_layers já povoada com {count} registros.",
                "count": count,
            }

        # ── Tabela vazia → executar pipeline completo ──
        result = {
            "status": "running",
            "started_at": started_at.isoformat(),
            "layers": {},
        }

        # Camada 1: Município
        try:
            records = _load_municipality()
            r = _upsert_records(db, records, "municipality")
            result["layers"]["municipality"] = r
        except Exception as exc:
            db.rollback()
            logger.error("[GEO] Erro ao carregar municipality: %s", exc)
            result["layers"]["municipality"] = {"status": "error", "error": str(exc)}

        # Camada 2: Setores censitários
        try:
            records = _load_census_tracts()
            r = _upsert_records(db, records, "census_tract")
            result["layers"]["census_tract"] = r
        except Exception as exc:
            db.rollback()
            logger.error("[GEO] Erro ao carregar census_tract: %s", exc)
            result["layers"]["census_tract"] = {"status": "error", "error": str(exc)}

        # Camada 3: Malha viária
        try:
            records = _load_roads()
            r = _upsert_records(db, records, "road")
            result["layers"]["road"] = r
        except Exception as exc:
            db.rollback()
            logger.error("[GEO] Erro ao carregar road: %s", exc)
            result["layers"]["road"] = {"status": "error", "error": str(exc)}

        result["status"] = "finished"
        result["finished_at"] = datetime.now().isoformat()

        total = sum(
            v.get("total", 0) or 0
            for v in result["layers"].values()
            if isinstance(v, dict) and "total" in v
        )
        logger.info(
            "[GEO] Sincronização concluída: %d registros inseridos/atualizados.", total
        )

        return result

    finally:
        db.close()
