"""
Módulo de geocodificação em batch de obras públicas via Google Maps Geocoding API.

Fluxo:
1. Busca obras sem latitude/longitude no banco.
2. Monta endereços prioritários a partir do model_cache (extracao_endereco)
   e, em fallback, dos campos address/neighborhood/municipio da própria obra.
3. Envia endereços individualmente para a Google Maps Geocoding API
   (com delay entre requisições para respeitar rate limits).
4. Atualiza public_works com as coordenadas reais.
5. Somente endereços que a API NÃO conseguiu geocodificar recebem
   coordenadas aleatórias dentro do polígono de Macaé (fallback).

Uso:
    from app.etl.geocode import batch_geocode_works
    stats = batch_geocode_works(db)
"""

from __future__ import annotations

import json
import random
import time
from typing import Any

import httpx
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.work import PublicWork
from app.models.geo import GeoLayer


# ──────────────────────────────────────────────────────────────
# Constantes da Google Maps Geocoding API
# ──────────────────────────────────────────────────────────────

# URL base da Google Maps Geocoding API
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# Delay entre requisições individuais (em segundos) para respeitar rate limits
# Google permite 50 req/s, mas usamos 0.5s para ser conservador
REQUEST_DELAY_SECONDS = 0.5

# Timeout da requisição HTTP (em segundos)
HTTP_TIMEOUT = 15

# Número máximo de tentativas com backoff exponencial em caso de OVER_QUERY_LIMIT
MAX_RETRIES = 3

# Filtro de componentes para restringir resultados ao Brasil
COMPONENT_FILTER = "country:BR"


# ──────────────────────────────────────────────────────────────
# Funções auxiliares: polígono de Macaé (fallback)
# ──────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────
# Montagem de endereços para geocodificação
# ──────────────────────────────────────────────────────────────


def _build_address_for_work(
    work_id: int,
    address: str | None,
    neighborhood: str | None,
    municipio: str | None,
    cache_address: str | None,
) -> str | None:
    """
    Monta o endereço mais completo possível para geocodificação.

    Prioridade:
    1. model_cache.extracao_endereco (endereco_geocoding da IA) — mais preciso
    2. public_works.address + neighborhood + municipio

    Retorna None se não for possível montar um endereço mínimo.

    Args:
        work_id: ID da obra (para debug).
        address: Campo address de public_works.
        neighborhood: Campo neighborhood de public_works.
        municipio: Campo municipio de public_works.
        cache_address: Campo extracao_endereco de model_cache.

    Returns:
        String com o endereço formatado ou None.
    """
    # Prioridade 1: endereço extraído pela IA (model_cache)
    if cache_address and cache_address.strip():
        addr = cache_address.strip()
        # Garante que tenha referência ao Brasil para melhorar precisão
        if "brasil" not in addr.lower() and "brazil" not in addr.lower():
            addr = f"{addr}, Brasil"
        return addr

    # Prioridade 2: campos da própria obra
    parts: list[str] = []

    if address and address.strip():
        parts.append(address.strip())

    if neighborhood and neighborhood.strip():
        parts.append(neighborhood.strip())

    # Sempre adiciona o município
    muni = (municipio or "Macaé").strip()
    if muni and muni not in " ".join(parts):
        parts.append(muni)

    # Adiciona estado e país para melhorar precisão
    parts.append("Rio de Janeiro")
    parts.append("Brasil")

    if not parts:
        return None

    return ", ".join(parts)


# ──────────────────────────────────────────────────────────────
# Comunicação com a Google Maps Geocoding API (individual)
# ──────────────────────────────────────────────────────────────


def _geocode_single_address(
    address: str,
    api_key: str,
    client: httpx.Client,
) -> tuple[float, float] | None:
    """
    Geocodifica um único endereço usando a Google Maps Geocoding API.

    Args:
        address: Endereço (string) para geocodificar.
        api_key: Chave de API do Google Maps.
        client: Instância de httpx.Client reutilizável.

    Returns:
        Tupla (latitude, longitude) se encontrado, ou None se não encontrado.

    Raises:
        Exception: Se a API retornar erro inesperado após todas as tentativas.
    """
    params = {
        "address": address,
        "key": api_key,
        "language": "pt-BR",
        "region": "br",
        "components": COMPONENT_FILTER,
    }

    for tentativa in range(1, MAX_RETRIES + 1):
        try:
            response = client.get(GOOGLE_GEOCODE_URL, params=params)

            if response.status_code == 200:
                data = response.json()
                status = data.get("status", "")

                if status == "OK":
                    # Sucesso — extrai lat/lon do primeiro resultado
                    location = data["results"][0]["geometry"]["location"]
                    lat = round(location["lat"], 6)
                    lon = round(location["lng"], 6)
                    return (lat, lon)

                elif status == "ZERO_RESULTS":
                    # Endereço não encontrado — não adianta tentar de novo
                    return None

                elif status == "OVER_QUERY_LIMIT":
                    # Rate limit — faz backoff exponencial
                    tempo_espera = 2 ** tentativa
                    print(
                        f"DEBUG: [GEOCODE]     Rate limit atingido. "
                        f"Aguardando {tempo_espera}s (tentativa {tentativa}/{MAX_RETRIES})..."
                    )
                    time.sleep(tempo_espera)
                    continue

                elif status == "REQUEST_DENIED":
                    # Erro de configuração — não adianta tentar de novo
                    print(f"DEBUG: [GEOCODE]     ✘ REQUEST_DENIED: {data.get('error_message', '')}")
                    return None

                else:
                    print(f"DEBUG: [GEOCODE]     ✘ Status inesperado: {status}")
                    return None

            else:
                # Erro HTTP — tenta novamente com backoff
                tempo_espera = 2 ** tentativa
                print(
                    f"DEBUG: [GEOCODE]     Erro HTTP {response.status_code}. "
                    f"Aguardando {tempo_espera}s (tentativa {tentativa}/{MAX_RETRIES})..."
                )
                time.sleep(tempo_espera)

        except httpx.TimeoutException:
            print(f"DEBUG: [GEOCODE]     Timeout na tentativa {tentativa}/{MAX_RETRIES}")
            time.sleep(2 ** tentativa)

        except Exception as exc:
            print(f"DEBUG: [GEOCODE]     Erro inesperado: {exc}")
            time.sleep(2 ** tentativa)

    # Esgotou todas as tentativas
    print(f"DEBUG: [GEOCODE]     ✘ Falha após {MAX_RETRIES} tentativas para: {address[:80]}")
    return None


def _geocode_addresses(
    addresses: list[str],
    api_key: str,
) -> dict[str, tuple[float, float]]:
    """
    Geocodifica uma lista de endereços individualmente via Google Maps API.

    Envia um endereço por vez com delay entre requisições para respeitar
    os rate limits da API (50 req/s).

    Args:
        addresses: Lista de endereços (strings) para geocodificar.
        api_key: Chave de API do Google Maps.

    Returns:
        Dicionário {texto_do_endereco: (lat, lon)} para os que obtiveram resultado.
    """
    mapping: dict[str, tuple[float, float]] = {}
    total = len(addresses)

    # Reutiliza o mesmo client HTTP para todas as requisições (connection pooling)
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        for i, addr in enumerate(addresses, start=1):
            # Log de progresso a cada 10 endereços
            if i % 10 == 0 or i == 1:
                print(f"DEBUG: [GEOCODE]     Geocodificando {i}/{total}: {addr[:60]}...")

            result = _geocode_single_address(addr, api_key, client)
            if result:
                mapping[addr] = result

            # Delay entre requisições para respeitar rate limits
            if i < total:
                time.sleep(REQUEST_DELAY_SECONDS)

    return mapping


# ──────────────────────────────────────────────────────────────
# Função principal: batch geocoding (via Google Maps individual)
# ──────────────────────────────────────────────────────────────


def batch_geocode_works(db: Session) -> dict:
    """
    Geocodifica em batch todas as obras de public_works que não possuem
    latitude/longitude, usando a Google Maps Geocoding API.

    Fluxo otimizado com cache em model_cache:
    1. Copia coordenadas já cacheadas de model_cache → public_works (evita retrabalho).
    2. Busca APENAS obras sem coordenadas no cache (model_cache.latitude IS NULL).
    3. Envia endereços individualmente para a Google Maps Geocoding API.
    4. Salva resultados no model_cache (latitude, longitude).
    5. Copia novas coordenadas de model_cache → public_works.

    Returns:
        dict com contadores: {
            "total_works": int,
            "cached_restored": int,  # restauradas do cache
            "addressed": int,        # obras com endereço montado
            "geocoded_api": int,     # geocodificadas pela API (coordenadas reais)
            "geocoded_fallback": int,  # fallback aleatório (API não encontrou)
            "skipped_no_address": int, # sem endereço possível
            "api_errors": int,       # erros na API
        }
    """
    settings = get_settings()
    stats = {
        "total_works": 0,
        "cached_restored": 0,
        "addressed": 0,
        "geocoded_api": 0,
        "geocoded_fallback": 0,
        "skipped_no_address": 0,
        "api_errors": 0,
    }

    print(f"DEBUG: [GEOCODE] ===============================================")
    print(f"DEBUG: [GEOCODE] INICIANDO GEOCODIFICAÇÃO EM BATCH (Google Maps)")
    print(f"DEBUG: [GEOCODE] ===============================================")

    # ── ETAPA 0: Copia coordenadas já cacheadas de model_cache → public_works ──
    # Isso evita retrabalho quando public_works é resetada mas model_cache persiste.
    print(f"DEBUG: [GEOCODE] ▶ Etapa 0: Restaurando coordenadas do cache...")
    cached_restore = text("""
        UPDATE public_works
        SET latitude = mc.latitude,
            longitude = mc.longitude
        FROM model_cache mc
        WHERE mc.description_hash = public_works.description_hash
          AND (public_works.latitude IS NULL OR public_works.longitude IS NULL)
          AND mc.latitude IS NOT NULL
          AND mc.longitude IS NOT NULL
          AND mc.is_obra = 1
    """)
    # SQLite usa sintaxe diferente (sem FROM no UPDATE)
    if settings.DATABASE_URL.startswith("sqlite"):
        cached_restore = text("""
            UPDATE public_works
            SET latitude = (
                SELECT mc.latitude FROM model_cache mc
                WHERE mc.description_hash = public_works.description_hash
                  AND mc.latitude IS NOT NULL
                  AND mc.is_obra = 1
            ),
            longitude = (
                SELECT mc.longitude FROM model_cache mc
                WHERE mc.description_hash = public_works.description_hash
                  AND mc.longitude IS NOT NULL
                  AND mc.is_obra = 1
            )
            WHERE (public_works.latitude IS NULL OR public_works.longitude IS NULL)
              AND EXISTS (
                SELECT 1 FROM model_cache mc
                WHERE mc.description_hash = public_works.description_hash
                  AND mc.latitude IS NOT NULL
                  AND mc.longitude IS NOT NULL
                  AND mc.is_obra = 1
              )
        """)

    result = db.execute(cached_restore)
    stats["cached_restored"] = result.rowcount
    db.commit()
    print(f"DEBUG: [GEOCODE]   ✔ Restauradas do cache: {stats['cached_restored']}")

    # Verifica se a chave de API está configurada
    api_key = settings.GOOGLE_MAPS_API_KEY
    if not api_key:
        print(f"DEBUG: [GEOCODE] ⚠ GOOGLE_MAPS_API_KEY não configurada.")
        if settings.GEOCODE_FALLBACK_RANDOM:
            print(f"DEBUG: [GEOCODE]   Usando fallback aleatório para obras restantes.")
            return _fallback_all_random(db, stats)
        else:
            print(f"DEBUG: [GEOCODE]   Fallback desligado. Apenas cache foi restaurado.")
            return stats

    # ── ETAPA 1: Busca obras que AINDA não têm coordenadas (nem no cache) ──
    # INNER JOIN com model_cache: APENAS is_obra=1 com endereço válido
    # Exclui as que já têm lat/lon no model_cache (já foram restauradas acima)
    query = text("""
        SELECT
            pw.id,
            pw.address,
            pw.neighborhood,
            pw.municipio,
            mc.extracao_endereco,
            mc.description_hash
        FROM public_works pw
        INNER JOIN model_cache mc ON mc.description_hash = pw.description_hash
        WHERE (pw.latitude IS NULL OR pw.longitude IS NULL)
          AND mc.is_obra = 1
          AND mc.extracao_endereco IS NOT NULL
          AND mc.extracao_endereco != ''
          AND (mc.latitude IS NULL OR mc.longitude IS NULL)
    """)

    rows = db.execute(query).fetchall()

    # Aplica o limite configurado (útil para testes)
    # GEOCODE_LIMIT=0 significa sem limite (processa tudo)
    if settings.GEOCODE_LIMIT > 0 and len(rows) > settings.GEOCODE_LIMIT:
        rows = rows[: settings.GEOCODE_LIMIT]
        print(
            f"DEBUG: [GEOCODE]   ⚠ LIMITE ATIVO: processando apenas "
            f"{settings.GEOCODE_LIMIT} endereços (GEOCODE_LIMIT)"
        )

    stats["total_works"] = len(rows)

    print(f"DEBUG: [GEOCODE]   Obras sem coordenadas (nem no cache): {stats['total_works']}")

    if stats["total_works"] == 0:
        print(f"DEBUG: [GEOCODE]   ✔ Todas as obras já possuem coordenadas (via cache ou API).")
        return stats

    # ── ETAPA 2: Monta endereços para cada obra ──
    # work_id -> (address_string, description_hash)
    work_addresses: dict[int, tuple[str, str]] = {}

    for row in rows:
        work_id = row[0]
        address = row[1]
        neighborhood = row[2]
        municipio = row[3]
        cache_address = row[4]
        desc_hash = row[5]

        addr = _build_address_for_work(
            work_id=work_id,
            address=address,
            neighborhood=neighborhood,
            municipio=municipio,
            cache_address=cache_address,
        )

        if addr:
            work_addresses[work_id] = (addr, desc_hash)
        else:
            stats["skipped_no_address"] += 1

    stats["addressed"] = len(work_addresses)
    print(f"DEBUG: [GEOCODE]   Endereços montados: {stats['addressed']}")
    print(f"DEBUG: [GEOCODE]   Sem endereço possível: {stats['skipped_no_address']}")

    if stats["addressed"] == 0:
        print(f"DEBUG: [GEOCODE]   Nenhum endereço para geocodificar.")
        return stats

    # ── ETAPA 3: Deduplica endereços (múltiplas obras podem ter o mesmo endereço) ──
    # address -> description_hash (para salvar no cache pelo hash)
    unique_addr_to_hash: dict[str, str] = {}
    for addr, desc_hash in work_addresses.values():
        unique_addr_to_hash[addr] = desc_hash  # último hash vence (ok, mesmo endereço)
    unique_addresses = list(unique_addr_to_hash.keys())
    print(f"DEBUG: [GEOCODE]   Endereços únicos: {len(unique_addresses)}")

    # ── ETAPA 4: Geocodifica endereços individualmente via Google Maps API ──
    # Mapeamento global: endereço -> (lat, lon)
    print(f"DEBUG: [GEOCODE]   ▶ Enviando {len(unique_addresses)} endereços para Google Maps API...")
    geocoded_map = _geocode_addresses(unique_addresses, api_key)
    found_api = len(geocoded_map)
    not_found_api = len(unique_addresses) - found_api
    print(f"DEBUG: [GEOCODE]   ✔ Geocodificação concluída: {found_api} encontrados, {not_found_api} não encontrados")

    # ── ETAPA 5: Salva resultados no model_cache E em public_works ──
    polygon = _get_municipality_polygon(db)

    # Primeiro, salva TODOS os resultados da API no model_cache (pelo description_hash)
    for addr, (lat, lon) in geocoded_map.items():
        desc_hash = unique_addr_to_hash.get(addr)
        if desc_hash:
            db.execute(
                text(
                    "UPDATE model_cache SET latitude = :lat, longitude = :lon "
                    "WHERE description_hash = :hash"
                ),
                {"lat": lat, "lon": lon, "hash": desc_hash},
            )

    # Agora atualiza public_works com as coordenadas (API ou fallback)
    for work_id, (addr, desc_hash) in work_addresses.items():
        if addr in geocoded_map:
            # Coordenadas reais da API
            lat, lon = geocoded_map[addr]
            db.execute(
                text("UPDATE public_works SET latitude = :lat, longitude = :lon WHERE id = :id"),
                {"lat": lat, "lon": lon, "id": work_id},
            )
            stats["geocoded_api"] += 1
        else:
            # Endereço não encontrado pela API
            if settings.GEOCODE_FALLBACK_RANDOM:
                # Fallback: coordenada aleatória dentro do polígono de Macaé
                if polygon:
                    lat, lon = _random_point_in_polygon(polygon)
                else:
                    lat = round(random.uniform(-22.42, -22.34), 6)
                    lon = round(random.uniform(-41.82, -41.70), 6)

                db.execute(
                    text("UPDATE public_works SET latitude = :lat, longitude = :lon WHERE id = :id"),
                    {"lat": lat, "lon": lon, "id": work_id},
                )
                # Também salva no cache para não reprocessar
                db.execute(
                    text(
                        "UPDATE model_cache SET latitude = :lat, longitude = :lon "
                        "WHERE description_hash = :hash"
                    ),
                    {"lat": lat, "lon": lon, "hash": desc_hash},
                )
                stats["geocoded_fallback"] += 1
            else:
                # Fallback desligado — ignora endereço não encontrado
                stats["skipped_no_address"] += 1

    # Obras sem endereço montado
    for row in rows:
        work_id = row[0]
        desc_hash = row[5]
        if work_id not in work_addresses:
            if settings.GEOCODE_FALLBACK_RANDOM:
                if polygon:
                    lat, lon = _random_point_in_polygon(polygon)
                else:
                    lat = round(random.uniform(-22.42, -22.34), 6)
                    lon = round(random.uniform(-41.82, -41.70), 6)

                db.execute(
                    text("UPDATE public_works SET latitude = :lat, longitude = :lon WHERE id = :id"),
                    {"lat": lat, "lon": lon, "id": work_id},
                )
                db.execute(
                    text(
                        "UPDATE model_cache SET latitude = :lat, longitude = :lon "
                        "WHERE description_hash = :hash"
                    ),
                    {"lat": lat, "lon": lon, "hash": desc_hash},
                )
                stats["geocoded_fallback"] += 1
            else:
                stats["skipped_no_address"] += 1

    db.commit()

    # ── ETAPA 6: Estatísticas finais ──
    print(f"DEBUG: [GEOCODE] ===============================================")
    print(f"DEBUG: [GEOCODE] GEOCODIFICAÇÃO CONCLUÍDA")
    print(f"DEBUG: [GEOCODE]   Restauradas do cache:     {stats['cached_restored']}")
    print(f"DEBUG: [GEOCODE]   Total de obras novas:     {stats['total_works']}")
    print(f"DEBUG: [GEOCODE]   Com endereço montado:     {stats['addressed']}")
    print(f"DEBUG: [GEOCODE]   Geocodificadas (API):     {stats['geocoded_api']}")
    print(f"DEBUG: [GEOCODE]   Fallback (aleatório):     {stats['geocoded_fallback']}")
    print(f"DEBUG: [GEOCODE]   Sem endereço possível:    {stats['skipped_no_address']}")
    print(f"DEBUG: [GEOCODE]   Erros de API:             {stats['api_errors']}")
    print(f"DEBUG: [GEOCODE] ===============================================")

    return stats


def _fallback_all_random(db: Session, stats: dict) -> dict:
    """
    Fallback completo: atribui coordenadas aleatórias dentro do polígono
    de Macaé para todas as obras sem coordenadas.
    Usado quando a GOOGLE_MAPS_API_KEY não está configurada.

    Args:
        db: Sessão do banco de dados.
        stats: Dicionário de estatísticas (será atualizado in-place).

    Returns:
        dict de estatísticas atualizado.
    """
    print(f"DEBUG: [GEOCODE] ▶ Modo fallback: coordenadas aleatórias...")

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
        stats["geocoded_fallback"] += 1

    if stats["geocoded_fallback"] > 0:
        db.commit()

    print(f"DEBUG: [GEOCODE]   ✔ Fallback concluído: {stats['geocoded_fallback']} obras")
    return stats


# ──────────────────────────────────────────────────────────────
# Compatibilidade: função legada (mantida para não quebrar imports)
# ──────────────────────────────────────────────────────────────


def assign_random_coordinates(db: Session) -> dict:
    """
    Função legada — agora redireciona para batch_geocode_works().
    Mantida para compatibilidade com data_sync_job.py.

    Args:
        db: Sessão do banco de dados.

    Returns:
        dict com contadores no formato {"geocoded": int, "skipped": int}
    """
    print(f"DEBUG: [GEOCODE] assign_random_coordinates() redirecionando para batch_geocode_works()...")
    stats = batch_geocode_works(db)

    # Converte para o formato legado
    return {
        "geocoded": stats.get("geocoded_api", 0) + stats.get("geocoded_fallback", 0),
        "skipped": stats.get("skipped_no_address", 0),
    }
