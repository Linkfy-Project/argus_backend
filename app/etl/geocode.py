"""
Módulo de geocodificação em batch de obras públicas via Geoapify API.

Fluxo:
1. Busca obras sem latitude/longitude no banco.
2. Monta endereços prioritários a partir do model_cache (extracao_endereco)
   e, em fallback, dos campos address/neighborhood/municipio da própria obra.
3. Envia endereços em lotes de até 1000 para a Geoapify Batch Geocoding API.
4. Faz polling até o job terminar e baixa os resultados (lat/lon).
5. Atualiza public_works com as coordenadas reais.
6. Somente endereços que a API NÃO conseguiu geocodificar recebem
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
# Constantes da Geoapify Batch Geocoding API
# ──────────────────────────────────────────────────────────────

# URL base para criar um job de batch geocoding
GEOAPIFY_BATCH_URL = "https://api.geoapify.com/v1/batch/geocode/search"

# Máximo de endereços por lote (limite da API)
BATCH_SIZE = 1000

# Intervalo entre polls de status (em segundos)
POLL_INTERVAL_SECONDS = 5

# Máximo de tentativas de polling por lote
MAX_POLL_ATTEMPTS = 120  # 120 * 5s = 10 minutos máximo por lote

# Timeout da requisição HTTP (em segundos)
HTTP_TIMEOUT = 30

# Filtro de país para restringir resultados ao Brasil
COUNTRY_FILTER = "countrycode:br"


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
# Comunicação com a Geoapify Batch API
# ──────────────────────────────────────────────────────────────


def _create_batch_job(
    addresses: list[str],
    api_key: str,
) -> dict[str, Any]:
    """
    Cria um job de batch geocoding na Geoapify.

    Args:
        addresses: Lista de endereços (strings) para geocodificar.
        api_key: Chave de API da Geoapify.

    Returns:
        Dicionário com 'id', 'status' e 'url' do job.

    Raises:
        Exception: Se a criação do job falhar.
    """
    url = f"{GEOAPIFY_BATCH_URL}?apiKey={api_key}&filter={COUNTRY_FILTER}&lang=pt"

    print(f"DEBUG: [GEOCODE]   Enviando {len(addresses)} endereços para Geoapify batch...")

    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        response = client.post(
            url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            content=json.dumps(addresses),
        )

    if response.status_code != 202:
        raise Exception(
            f"Falha ao criar batch job (HTTP {response.status_code}): {response.text[:300]}"
        )

    job_data = response.json()
    print(f"DEBUG: [GEOCODE]   ✔ Job criado: id={job_data.get('id')}")
    return job_data


def _poll_batch_job(
    job_url: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """
    Faz polling do job de batch geocoding até completar ou timeout.

    Args:
        job_url: URL retornada pelo job para consultar resultados.
        api_key: Chave de API da Geoapify.

    Returns:
        Lista de resultados geocodificados (cada item tem 'query', 'lat', 'lon', etc.).

    Raises:
        Exception: Se o job falhar ou exceder o timeout.
    """
    # A URL retornada pelo job JÁ contém o apiKey, não precisamos adicionar novamente.
    # Se por algum motivo não tiver, adicionamos.
    if "apiKey=" in job_url:
        poll_url = job_url
    else:
        poll_url = f"{job_url}&apiKey={api_key}"

    print(f"DEBUG: [GEOCODE]   Aguardando resultados do job...")

    for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            response = client.get(poll_url)

        if response.status_code == 200:
            # Job completo — retorna os resultados
            results = response.json()
            print(f"DEBUG: [GEOCODE]   ✔ Job concluído! {len(results)} resultados recebidos.")
            return results

        elif response.status_code == 202:
            # Ainda processando
            if attempt % 6 == 0:  # Log a cada 30 segundos
                print(f"DEBUG: [GEOCODE]     ... ainda processando (tentativa {attempt}/{MAX_POLL_ATTEMPTS})")
            time.sleep(POLL_INTERVAL_SECONDS)

        else:
            raise Exception(
                f"Erro ao consultar job (HTTP {response.status_code}): {response.text[:300]}"
            )

    raise Exception(
        f"Timeout ao aguardar resultados do job ({MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECONDS}s)"
    )


def _extract_lat_lon_from_results(
    results: list[dict[str, Any]],
) -> dict[str, tuple[float, float]]:
    """
    Extrai lat/lon dos resultados da Geoapify, indexando pelo texto do query.

    Args:
        results: Lista de resultados da API batch.

    Returns:
        Dicionário {texto_do_endereco: (lat, lon)} para os que obtiveram resultado.
    """
    mapping: dict[str, tuple[float, float]] = {}

    for item in results:
        # O campo 'query.text' contém o endereço que foi enviado
        query_text = item.get("query", {}).get("text", "")
        lat = item.get("lat")
        lon = item.get("lon")

        if query_text and lat is not None and lon is not None:
            mapping[query_text] = (round(lat, 6), round(lon, 6))

    return mapping


# ──────────────────────────────────────────────────────────────
# Função principal: batch geocoding
# ──────────────────────────────────────────────────────────────


def batch_geocode_works(db: Session) -> dict:
    """
    Geocodifica em batch todas as obras de public_works que não possuem
    latitude/longitude, usando a Geoapify Batch Geocoding API.

    Fluxo otimizado com cache em model_cache:
    1. Copia coordenadas já cacheadas de model_cache → public_works (evita retrabalho).
    2. Busca APENAS obras sem coordenadas no cache (model_cache.latitude IS NULL).
    3. Envia endereços novos para a Geoapify batch API.
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
    print(f"DEBUG: [GEOCODE] INICIANDO GEOCODIFICAÇÃO EM BATCH (Geoapify)")
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
    api_key = settings.GEOAPIFY_API_KEY
    if not api_key:
        print(f"DEBUG: [GEOCODE] ⚠ GEOAPIFY_API_KEY não configurada.")
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

    # ── ETAPA 4: Envia em lotes de BATCH_SIZE ──
    # Mapeamento global: endereço -> (lat, lon)
    geocoded_map: dict[str, tuple[float, float]] = {}

    for batch_start in range(0, len(unique_addresses), BATCH_SIZE):
        batch = unique_addresses[batch_start:batch_start + BATCH_SIZE]
        batch_num = (batch_start // BATCH_SIZE) + 1
        total_batches = (len(unique_addresses) + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"DEBUG: [GEOCODE]   ── Lote {batch_num}/{total_batches} ({len(batch)} endereços)")

        try:
            # Cria o job
            job_data = _create_batch_job(batch, api_key)
            job_url = job_data.get("url", "")

            if not job_url:
                print(f"DEBUG: [GEOCODE]   ✘ Job não retornou URL. Pulando lote.")
                stats["api_errors"] += len(batch)
                continue

            # Faz polling até completar
            results = _poll_batch_job(job_url, api_key)

            # Extrai lat/lon dos resultados
            batch_map = _extract_lat_lon_from_results(results)
            geocoded_map.update(batch_map)

            found_in_batch = len(batch_map)
            not_found = len(batch) - found_in_batch
            print(
                f"DEBUG: [GEOCODE]   ✔ Lote {batch_num}: "
                f"{found_in_batch} encontrados, {not_found} não encontrados"
            )

        except Exception as exc:
            print(f"DEBUG: [GEOCODE]   ✘ Erro no lote {batch_num}: {exc}")
            stats["api_errors"] += len(batch)

    print(f"DEBUG: [GEOCODE]   Total geocodificado pela API: {len(geocoded_map)}")

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
    Usado quando a GEOAPIFY_API_KEY não está configurada.

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
