"""
Módulo de sincronização de bairros de Macaé via polígonos do OpenStreetMap.

Pipeline:
1. Baixa polígonos dos bairros de Macaé via osmnx (admin_level=9 + place=suburb)
2. Armazena na tabela geo_layers com layer_type='neighborhood'
3. Para cada obra com coordenadas, faz point-in-polygon para determinar o bairro
4. Atualiza public_works.neighborhood com o bairro encontrado
5. Para obras SEM coordenadas, usa regex como fallback (extrai de model_cache.local)

Uso no pipeline (data_sync_job.py):
    from app.etl.neighborhood_sync import sync_neighborhood_polygons, backfill_neighborhoods
    sync_neighborhood_polygons()   # baixa e armazena polígonos
    backfill_neighborhoods(db)     # preenche neighborhood nas obras

Uso standalone:
    python -m app.etl.neighborhood_sync
"""

from __future__ import annotations

import json
import re
from datetime import datetime

import geopandas as gpd
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.geo import GeoLayer
from app.models.work import PublicWork, ModelCache
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Constantes ──────────────────────────────────────────────────────────────


def _normalize_neighborhood_name(name: str) -> str:
    """
    Normaliza o nome de um bairro para formato consistente.

    Regras:
    - Converte para Title Case (primeira maiúscula)
    - Remove sufixos como "- MACAÉ - RJ", "? Macaé/ RJ", "EM MACAÉ"
    - Remove espaços extras
    - Corrige encoding/acentos quando possível

    Args:
        name: Nome bruto do bairro.

    Returns:
        Nome normalizado.
    """
    import unicodedata

    if not name:
        return ""

    name = name.strip()

    # Remove sufixos comuns de localização
    suffixes_to_remove = [
        r'\s*[-–—]\s*MACA[ÉE]\s*[-–—/]\s*RJ\s*$',
        r'\s*\?\s*MACA[ÉE]\s*/?\s*RJ?\s*$',
        r'\s+EM\s+MACA[ÉE]\s*[-–/]?\s*RJ?\s*$',
        r'\s+DE\s+MACA[ÉE]\s*[-–/]?\s*RJ?\s*$',
        r'\s+NO\s+MUNIC[IÍ]PIO\s+DE\s+MACA[ÉE].*$',
        r'\s*[-–—]\s*MACA[ÉE]\s*$',
        r'\s*\(\s*MACA[ÉE]\s*\)\s*$',
    ]
    for pattern in suffixes_to_remove:
        name = re.sub(pattern, "", name, flags=re.IGNORECASE).strip()

    # Remove caracteres especiais no início/fim
    name = re.sub(r'^[?\-–—\s]+|[?\-–—\s]+$', '', name).strip()

    # Converte para Title Case (preservando preposições minúsculas)
    prepositions = {"de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas", "e"}
    words = name.split()
    title_words = []
    for i, word in enumerate(words):
        if i > 0 and word.lower() in prepositions:
            title_words.append(word.lower())
        else:
            title_words.append(word.capitalize() if word.isupper() or word.islower() else word)
    name = " ".join(title_words)

    return name

# Código IBGE de Macaé
MUNICIPIO_CODE = 3302403

# Nome para queries no OSM
OSM_PLACE = "Macaé, Rio de Janeiro, Brazil"

# EPSG para cálculo de área em metros
CRS_METRIC = "EPSG:31983"


# ── 1. Download dos polígonos de bairros via osmnx ─────────────────────────


def _fetch_neighborhood_geometries() -> gpd.GeoDataFrame:
    """
    Baixa as geometrias dos bairros de Macaé usando osmnx.

    Combina duas fontes do OpenStreetMap:
    1. admin_level=9: limites administrativos oficiais (distritos/bairros)
    2. place=suburb: bairros mapeados como places (pode ter polígonos)

    Filtra apenas features que intersectam o município de Macaé
    (para excluir cidades vizinhas como Carapebus, Conceição de Macabu, etc.)

    Returns:
        GeoDataFrame com colunas: name, geometry (polígonos).

    Raises:
        RuntimeError: Se nenhuma feature for encontrada.
    """
    import osmnx as ox
    import warnings
    warnings.filterwarnings('ignore')

    logger.info("[NEIGHBORHOOD]   Baixando boundaries admin_level=9 do OSM...")
    gdfs = []

    # Fonte 1: admin_level=9 (limites administrativos)
    try:
        gdf_admin = ox.features_from_place(OSM_PLACE, tags={"boundary": "administrative", "admin_level": "9"})
        if not gdf_admin.empty:
            # Filtra apenas polígonos/multipolígonos (exclui nodes e points)
            gdf_admin = gdf_admin[gdf_admin.geometry.type.isin(["Polygon", "MultiPolygon"])]
            if "name" in gdf_admin.columns:
                gdf_admin = gdf_admin[["name", "geometry"]].copy()
                gdf_admin["source"] = "admin_level_9"
                gdfs.append(gdf_admin)
                logger.info("[NEIGHBORHOOD]   ✔ admin_level=9: %d polígonos", len(gdf_admin))
    except Exception as exc:
        logger.info("[NEIGHBORHOOD]   ⚠ Erro ao buscar admin_level=9: %s", exc)

    # Fonte 2: place=suburb (bairros mapeados como places)
    try:
        gdf_suburb = ox.features_from_place(OSM_PLACE, tags={"place": "suburb"})
        if not gdf_suburb.empty:
            # Filtra apenas polígonos (nodes não servem para point-in-polygon)
            gdf_suburb = gdf_suburb[gdf_suburb.geometry.type.isin(["Polygon", "MultiPolygon"])]
            if "name" in gdf_suburb.columns and not gdf_suburb.empty:
                gdf_suburb = gdf_suburb[["name", "geometry"]].copy()
                gdf_suburb["source"] = "place_suburb"
                gdfs.append(gdf_suburb)
                logger.info("[NEIGHBORHOOD]   ✔ place=suburb: %d polígonos", len(gdf_suburb))
    except Exception as exc:
        logger.info("[NEIGHBORHOOD]   ⚠ Erro ao buscar place=suburb: %s", exc)

    if not gdfs:
        raise RuntimeError("Nenhuma geometria de bairro encontrada no OSM para Macaé")

    # Combina todas as fontes
    gdf_combined = gpd.GeoDataFrame(
        gpd.pd.concat(gdfs, ignore_index=True), crs="EPSG:4326"
    )

    # Remove duplicatas pelo nome (prioriza admin_level_9 por ser mais preciso)
    gdf_combined = gdf_combined.drop_duplicates(subset=["name"], keep="first")

    # Remove features que NÃO são bairros (regiões geográficas, cidades vizinhas, etc.)
    # Palavras-chave que indicam que NÃO é um bairro de Macaé
    non_bairro_keywords = [
        "região", "regiao", "intermediária", "intermediaria",
        "imediata", "geográfica", "geografica",
        "carapebus", "conceição", "conceicao", "macabu",
        "rio das ostras", "casimiro", "abreu",
        "nova friburgo", "trajano", "moraes",
        "lumiar", "sodrelândia", "sodrelandia",
        "vila da grama", "mirante da lagoa",
    ]
    mask_valid = ~gdf_combined["name"].str.lower().str.contains(
        "|".join(non_bairro_keywords), na=False
    )
    removed = len(gdf_combined) - mask_valid.sum()
    gdf_combined = gdf_combined[mask_valid].copy()
    if removed > 0:
        logger.info("[NEIGHBORHOOD]   Removidos %d features que não são bairros", removed)

    # Remove entradas cujo nome é exatamente "Macaé" (é o município, não um bairro)
    mask_not_macae = gdf_combined["name"].str.strip().str.lower() != "macaé"
    removed_macae = len(gdf_combined) - mask_not_macae.sum()
    if removed_macae > 0:
        logger.info("[NEIGHBORHOOD]   Removidas %d entradas 'Macaé' (município, não bairro)", removed_macae)
    gdf_combined = gdf_combined[mask_not_macae].copy()

    # Filtra: mantém apenas bairros cujo centróide está dentro do município de Macaé
    logger.info("[NEIGHBORHOOD]   Filtrando bairros dentro do município de Macaé...")
    try:
        gdf_municipio = ox.features_from_place(
            OSM_PLACE, tags={"boundary": "administrative", "admin_level": "8"}
        )
        if not gdf_municipio.empty:
            municipio_geom = gdf_municipio.geometry.unary_union
            # Mantém apenas bairros que intersectam o município
            mask = gdf_combined.geometry.intersects(municipio_geom)
            gdf_filtered = gdf_combined[mask].copy()
            logger.info(
                "[NEIGHBORHOOD]   %d bairros após filtro geográfico (de %d)",
                len(gdf_filtered), len(gdf_combined),
            )
        else:
            gdf_filtered = gdf_combined
    except Exception as exc:
        logger.info("[NEIGHBORHOOD]   ⚠ Erro ao filtrar por município: %s. Usando todos.", exc)
        gdf_filtered = gdf_combined

    if gdf_filtered.empty:
        raise RuntimeError("Nenhum bairro de Macaé encontrado após filtro geográfico")

    return gdf_filtered


def _gdf_to_neighborhood_records(gdf: gpd.GeoDataFrame) -> list[dict]:
    """
    Converte um GeoDataFrame de bairros em registros para a tabela geo_layers.

    Args:
        gdf: GeoDataFrame com colunas: name, geometry.

    Returns:
        Lista de dicionários prontos para inserção no banco.
    """
    import shapely

    records = []
    for idx, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        name = _normalize_neighborhood_name(str(row.get("name", "")).strip())
        if not name:
            continue

        # Converte geometria para GeoJSON string
        geojson_geom = json.dumps(shapely.geometry.mapping(geom), ensure_ascii=False)

        # Calcula centróide
        centroid = geom.centroid

        # Calcula área em metros quadrados (projetando para UTM 23S)
        try:
            geom_metric = gpd.GeoSeries([geom], crs="EPSG:4326").to_crs(CRS_METRIC).iloc[0]
            area_m2 = round(geom_metric.area, 2)
        except Exception:
            area_m2 = 0.0

        # Código único baseado no OSM ID ou índice
        osm_id = ""
        if hasattr(idx, "__iter__") and len(idx) >= 2:
            osm_id = str(idx[1])
        else:
            osm_id = str(idx)

        records.append({
            "name": name,
            "code": f"osm_{osm_id}",
            "geojson_geometry": geojson_geom,
            "centroid_lat": round(centroid.y, 6),
            "centroid_lon": round(centroid.x, 6),
            "area_m2": area_m2,
        })

    logger.info("[NEIGHBORHOOD]   %d registros preparados para o banco", len(records))
    return records


# ── 2. Armazenamento no banco (geo_layers) ─────────────────────────────────


def _store_neighborhoods_in_db(neighborhoods: list[dict]) -> dict:
    """
    Armazena os polígonos dos bairros na tabela geo_layers via UPSERT.

    Args:
        neighborhoods: Lista de dicionários com dados dos bairros.

    Returns:
        Dicionário com contadores: created, updated, total.
    """
    db = SessionLocal()
    try:
        created = 0
        updated = 0
        now = datetime.utcnow()

        for nb in neighborhoods:
            existing = (
                db.query(GeoLayer)
                .filter(
                    GeoLayer.layer_type == "neighborhood",
                    GeoLayer.code == nb["code"],
                )
                .first()
            )

            if existing:
                existing.name = nb["name"]
                existing.geojson_geometry = nb["geojson_geometry"]
                existing.centroid_lat = nb["centroid_lat"]
                existing.centroid_lon = nb["centroid_lon"]
                existing.area_m2 = nb["area_m2"]
                existing.updated_at = now
                updated += 1
            else:
                new_layer = GeoLayer(
                    layer_type="neighborhood",
                    code=nb["code"],
                    name=nb["name"],
                    geojson_geometry=nb["geojson_geometry"],
                    centroid_lat=nb["centroid_lat"],
                    centroid_lon=nb["centroid_lon"],
                    area_m2=nb["area_m2"],
                    created_at=now,
                    updated_at=now,
                )
                db.add(new_layer)
                created += 1

        db.commit()
        result = {"created": created, "updated": updated, "total": len(neighborhoods)}
        logger.info(
            "[NEIGHBORHOOD]   ✔ UPSERT geo_layers: %d criados, %d atualizados, %d total",
            created, updated, len(neighborhoods),
        )
        return result

    except Exception as exc:
        db.rollback()
        logger.info("[NEIGHBORHOOD]   ✘ Erro ao salvar no banco: %s", exc)
        raise
    finally:
        db.close()


# ── 3. Função principal: sync_neighborhood_polygons ────────────────────────


def sync_neighborhood_polygons() -> dict:
    """
    Sincroniza os polígonos dos bairros de Macaé no banco de dados.

    Fluxo:
    1. Verifica se já existem bairros no geo_layers (evita retrabalho)
    2. Baixa polígonos do OpenStreetMap via osmnx
    3. Extrai e normaliza os bairros
    4. Armazena na tabela geo_layers

    Returns:
        Dicionário com status e contadores.
    """
    logger.info("[NEIGHBORHOOD] ===============================================")
    logger.info("[NEIGHBORHOOD] SINCRONIZAÇÃO DE POLÍGONOS DE BAIRROS")
    logger.info("[NEIGHBORHOOD] ===============================================")

    # Guarda: verifica se já existem bairros no banco
    db = SessionLocal()
    try:
        count = db.query(GeoLayer).filter(GeoLayer.layer_type == "neighborhood").count()
        if count > 0:
            logger.info(
                "[NEIGHBORHOOD]   Já existem %d bairros no geo_layers. Pulando download.",
                count,
            )
            return {
                "status": "skipped",
                "reason": f"Já existem {count} bairros no banco.",
                "count": count,
            }
    finally:
        db.close()

    # Baixa do OSM via osmnx
    logger.info("[NEIGHBORHOOD] ▶ Baixando polígonos de bairros do OpenStreetMap...")
    try:
        gdf = _fetch_neighborhood_geometries()
    except Exception as exc:
        logger.info("[NEIGHBORHOOD]   ✘ Falha ao baixar do OSM: %s", exc)
        return {"status": "error", "error": str(exc)}

    # Converte para registros do banco
    logger.info("[NEIGHBORHOOD] ▶ Convertendo geometrias para registros do banco...")
    neighborhoods = _gdf_to_neighborhood_records(gdf)

    if not neighborhoods:
        logger.info("[NEIGHBORHOOD]   ⚠ Nenhum bairro válido extraído.")
        return {"status": "empty", "error": "Nenhum bairro válido extraído do OSM."}

    # Salva no banco
    logger.info("[NEIGHBORHOOD] ▶ Salvando %d bairros no banco...", len(neighborhoods))
    result = _store_neighborhoods_in_db(neighborhoods)
    result["status"] = "ok"

    logger.info("[NEIGHBORHOOD] ===============================================")
    logger.info("[NEIGHBORHOOD] SINCRONIZAÇÃO CONCLUÍDA: %d bairros", result["total"])
    logger.info("[NEIGHBORHOOD] ===============================================")

    return result


# ── 4. Point-in-Polygon: determinar bairro de cada obra ────────────────────


def _load_neighborhood_polygons_from_db() -> list[dict]:
    """
    Carrega todos os polígonos de bairros do banco de dados.

    Returns:
        Lista de dicionários com: name, shapely_geometry.
    """
    db = SessionLocal()
    try:
        layers = (
            db.query(GeoLayer)
            .filter(GeoLayer.layer_type == "neighborhood")
            .all()
        )

        neighborhoods = []
        for layer in layers:
            try:
                from shapely.geometry import shape as shapely_shape
                geom = shapely_shape(json.loads(layer.geojson_geometry))
                neighborhoods.append({
                    "name": layer.name,
                    "geometry": geom,
                })
            except Exception as exc:
                logger.info(
                    "[NEIGHBORHOOD]   ⚠ Erro ao carregar geometria de '%s': %s",
                    layer.name, exc,
                )

        return neighborhoods

    finally:
        db.close()


def _find_neighborhood_for_point(
    lat: float,
    lon: float,
    neighborhoods: list[dict],
) -> str | None:
    """
    Determina o bairro de um ponto usando point-in-polygon.

    Args:
        lat: Latitude do ponto.
        lon: Longitude do ponto.
        neighborhoods: Lista de bairros com geometrias shapely.

    Returns:
        Nome do bairro se o ponto estiver dentro de algum polígono, ou None.
    """
    try:
        from shapely.geometry import Point
        point = Point(lon, lat)  # shapely usa (x, y) = (lon, lat)

        for nb in neighborhoods:
            if nb["geometry"].contains(point):
                return nb["name"]

        # Se não encontrou exato, tenta com buffer pequeno (~50m)
        # Útil para pontos na borda do polígono
        point_buffered = point.buffer(0.0005)  # ~50m em graus

        for nb in neighborhoods:
            if nb["geometry"].intersects(point_buffered):
                return nb["name"]

    except Exception as exc:
        logger.info(
            "[NEIGHBORHOOD]   ⚠ Erro no point-in-polygon (%.6f, %.6f): %s",
            lat, lon, exc,
        )

    return None


def _extract_bairro_from_text(text: str) -> str | None:
    """
    Extrai o nome do bairro de um texto usando regex.

    Procura padrões como:
    - "Bairro XXX"
    - "BAIRRO XXX"
    - "Bairros XXX"

    Args:
        text: Texto para buscar (local, address ou description).

    Returns:
        Nome do bairro extraído ou None.
    """
    if not text:
        return None

    # Padrão: "Bairro(s) <nome>" seguido de vírgula, parêntese ou fim da string
    # Captura o nome do bairro após a palavra "Bairro" ou "Bairros"
    patterns = [
        r'[Bb]airros?\s+(.+?)(?:\s*[,;()\n]|$)',
        r'NO\s+BAIRRO\s+(.+?)(?:\s*[,;()\n]|$)',
        r'no\s+[Bb]airro\s+(.+?)(?:\s*[,;()\n]|$)',
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            bairro = match.group(1).strip()
            # Remove caracteres indesejados no final
            bairro = re.sub(r'[)\].]+$', '', bairro).strip()
            # Remove artigos e preposições no início
            bairro = re.sub(r'^(de|do|da|dos|das)\s+', '', bairro, flags=re.IGNORECASE).strip()
            if len(bairro) >= 3:  # Evita matches muito curtos
                return _normalize_neighborhood_name(bairro)

    return None


def backfill_neighborhoods(db: Session) -> dict:
    """
    Preenche o campo neighborhood em public_works para todas as obras.

    Estratégia em 2 camadas:
    1. Point-in-polygon: para obras COM coordenadas, usa os polígonos de bairros
    2. Regex fallback: para obras SEM coordenadas, extrai bairro de model_cache.local
       e public_works.address

    Args:
        db: Sessão do banco de dados.

    Returns:
        Dicionário com contadores de obras atualizadas.
    """
    logger.info("[NEIGHBORHOOD] ===============================================")
    logger.info("[NEIGHBORHOOD] BACKFILL DE NEIGHBORHOODS")
    logger.info("[NEIGHBORHOOD] ===============================================")

    stats = {
        "total_works": 0,
        "already_has_neighborhood": 0,
        "updated_polygon": 0,
        "updated_regex": 0,
        "no_match": 0,
    }

    # ── Carrega polígonos de bairros do banco ──
    neighborhoods = _load_neighborhood_polygons_from_db()
    logger.info(
        "[NEIGHBORHOOD]   %d polígonos de bairros carregados",
        len(neighborhoods),
    )

    # ── Busca todas as obras ──
    works = db.query(PublicWork).all()
    stats["total_works"] = len(works)
    logger.info("[NEIGHBORHOOD]   %d obras para processar", len(works))

    # ── Carrega model_cache para fallback regex ──
    cache_map: dict[str, str] = {}  # description_hash -> local
    cache_entries = db.query(
        ModelCache.description_hash,
        ModelCache.local,
    ).filter(
        ModelCache.local.isnot(None),
        ModelCache.local != "",
    ).all()
    for entry in cache_entries:
        cache_map[entry.description_hash] = entry.local
    logger.info(
        "[NEIGHBORHOOD]   %d entradas de model_cache com local",
        len(cache_map),
    )

    # ── Processa cada obra ──
    for work in works:
        # Se já tem neighborhood válido, pula
        if work.neighborhood and work.neighborhood.strip():
            stats["already_has_neighborhood"] += 1
            continue

        bairro = None

        # Camada 1: Point-in-polygon (obras com coordenadas)
        if work.latitude is not None and work.longitude is not None and neighborhoods:
            bairro = _find_neighborhood_for_point(
                work.latitude, work.longitude, neighborhoods
            )
            if bairro:
                stats["updated_polygon"] += 1

        # Camada 2: Regex fallback (model_cache.local, address, description)
        if not bairro:
            # Tenta model_cache.local primeiro
            cache_local = cache_map.get(work.description_hash)
            if cache_local:
                bairro = _extract_bairro_from_text(cache_local)

            # Tenta public_works.address
            if not bairro and work.address:
                bairro = _extract_bairro_from_text(work.address)

            # Tenta object_description
            if not bairro and work.object_description:
                bairro = _extract_bairro_from_text(work.object_description)

            if bairro:
                stats["updated_regex"] += 1

        # Atualiza o registro
        if bairro:
            work.neighborhood = bairro
        else:
            stats["no_match"] += 1

    # Commit em lote
    db.commit()

    logger.info("[NEIGHBORHOOD] ===============================================")
    logger.info("[NEIGHBORHOOD] BACKFILL CONCLUÍDO")
    logger.info("[NEIGHBORHOOD]   Total de obras:              %d", stats["total_works"])
    logger.info("[NEIGHBORHOOD]   Já tinham neighborhood:      %d", stats["already_has_neighborhood"])
    logger.info("[NEIGHBORHOOD]   Atualizados por polígono:    %d", stats["updated_polygon"])
    logger.info("[NEIGHBORHOOD]   Atualizados por regex:       %d", stats["updated_regex"])
    logger.info("[NEIGHBORHOOD]   Sem correspondência:         %d", stats["no_match"])
    logger.info("[NEIGHBORHOOD] ===============================================")

    return stats


# ── 5. Execução standalone ─────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Sincronização de Bairros de Macaé ===\n")

    # Etapa 1: Baixar e salvar polígonos
    print("1. Baixando polígonos de bairros do OSM...")
    sync_result = sync_neighborhood_polygons()
    print(f"   Resultado: {sync_result}\n")

    # Etapa 2: Backfill neighborhoods
    if sync_result.get("status") in ("ok", "skipped"):
        print("2. Preenchendo neighborhood nas obras...")
        db = SessionLocal()
        try:
            backfill_result = backfill_neighborhoods(db)
            print(f"   Resultado: {backfill_result}\n")
        finally:
            db.close()

    print("=== Concluído ===")
