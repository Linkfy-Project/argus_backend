"""
Módulo de cálculo de sobreposição territorial por buffer (raio).

Fluxo:
1. Busca todas as obras com coordenadas (latitude/longitude) e data de assinatura.
2. Para cada obra, cria um buffer circular de raio configurável (padrão 500m).
3. Conta quantas outras obras estão dentro do buffer E dentro da janela temporal.
4. Calcula o overlap_ratio e atualiza o campo territorial_overlap_ratio no banco.

Otimização: usa filtro por bounding box (ordenando por latitude) para evitar
comparações N². Aproximação metros→graus: 1 grau ≈ 111.000 metros.

Debug logs usam prefixo "DEBUG: [OVERLAY]".
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

from shapely.geometry import Point
from sqlalchemy.orm import Session

from app.models.work import PublicWork

# Constante de conversão: 1 grau ≈ 111.000 metros (aproximação para WGS84)
METERS_PER_DEGREE: float = 111_000.0

# Threshold de obras vizinhas esperadas para normalizar o ratio
# Se uma região tem menos que esse número de obras, o ratio será < 1.0
EXPECTED_NEIGHBORS_THRESHOLD: int = 3


def calculate_territorial_overlaps(
    db: Session,
    radius_m: float = 500.0,
    window_months: int = 24,
) -> dict:
    """
    Calcula a sobreposição territorial para todas as obras com coordenadas.

    Args:
        db: Sessão do SQLAlchemy.
        radius_m: Raio do buffer em metros (padrão 500m).
        window_months: Janela temporal em meses para considerar obras vizinhas (padrão 24).

    Returns:
        dict com estatísticas do processamento.
    """
    print(f"DEBUG: [OVERLAY] ===============================================")
    print(f"DEBUG: [OVERLAY] Iniciando cálculo de sobreposição territorial")
    print(f"DEBUG: [OVERLAY]   Raio:     {radius_m}m")
    print(f"DEBUG: [OVERLAY]   Janela:   {window_months} meses")
    print(f"DEBUG: [OVERLAY]   Threshold: {EXPECTED_NEIGHBORS_THRESHOLD} obras esperadas")
    print(f"DEBUG: [OVERLAY] ===============================================")

    # ── 1. Buscar obras com coordenadas e data de assinatura ──
    works: list[PublicWork] = (
        db.query(PublicWork)
        .filter(
            PublicWork.latitude.isnot(None),
            PublicWork.longitude.isnot(None),
            PublicWork.signed_at.isnot(None),
        )
        .all()
    )

    total_works = len(works)
    print(f"DEBUG: [OVERLAY] Obras com coordenadas e signed_at: {total_works}")

    if total_works == 0:
        print("DEBUG: [OVERLAY] Nenhuma obra encontrada. Retornando.")
        return {"total_works": 0, "works_updated": 0, "status": "no_works"}

    # ── 2. Converter metros para graus (aproximação WGS84) ──
    radius_degrees: float = radius_m / METERS_PER_DEGREE
    print(f"DEBUG: [OVERLAY] Raio em graus: {radius_degrees:.6f}")

    # ── 3. Definir janela temporal ──
    # Calcula a data limite: obras assinadas dentro dos últimos window_months meses
    # Usamos timedelta com dias aproximados (30.44 dias/mês)
    window_days: int = int(window_months * 30.44)
    today: date = date.today()
    window_start: date = today - timedelta(days=window_days)
    print(f"DEBUG: [OVERLAY] Janela temporal: {window_start} até {today}")

    # ── 4. Preparar dados para comparação eficiente ──
    # Cria uma lista de tuplas (id, lat, lon, signed_at, point) ordenada por latitude
    # Isso permite filtrar por bounding box de forma eficiente
    work_data: list[tuple[int, float, float, date, Point]] = []
    for w in works:
        lat = float(w.latitude)
        lon = float(w.longitude)
        point = Point(lon, lat)  # Shapely usa (x=lon, y=lat)
        work_data.append((w.id, lat, lon, w.signed_at, point))

    # Ordena por latitude para permitir busca binária no bounding box
    work_data.sort(key=lambda x: x[1])  # ordena por lat
    print(f"DEBUG: [OVERLAY] Dados preparados e ordenados por latitude.")

    # ── 5. Calcular sobreposição para cada obra ──
    # Para cada obra, busca candidatos no bounding box (lat ± radius_degrees)
    # e depois filtra por longitude e distância real
    latitudes: list[float] = [wd[1] for wd in work_data]
    works_updated: int = 0

    for idx, (work_id, lat, lon, signed_at, point) in enumerate(work_data):
        # Determina a janela temporal para esta obra específica
        # Considera obras assinadas dentro de window_months antes ou depois
        if signed_at is None:
            continue

        time_lower: date = signed_at - timedelta(days=window_days)
        time_upper: date = signed_at + timedelta(days=window_days)

        # Conta vizinhos (excluindo a própria obra)
        neighbor_count: int = 0

        # Busca binária: encontra o índice mais à esquerda onde lat >= lat - radius_degrees
        lat_min: float = lat - radius_degrees
        lat_max: float = lat + radius_degrees

        # Encontra o range de índices no array ordenado por latitude
        start_idx: int = _bisect_left(latitudes, lat_min)
        end_idx: int = _bisect_right(latitudes, lat_max)

        # Itera apenas sobre candidatos no bounding box de latitude
        for j in range(start_idx, end_idx):
            if j == idx:
                continue  # Pula a própria obra

            n_id, n_lat, n_lon, n_signed_at, n_point = work_data[j]

            # Filtro por longitude (bounding box)
            if abs(n_lon - lon) > radius_degrees:
                continue

            # Filtro por janela temporal
            if n_signed_at is None:
                continue
            if n_signed_at < time_lower or n_signed_at > time_upper:
                continue

            # Teste de distância real usando Shapely (buffer circular)
            if point.distance(n_point) <= radius_degrees:
                neighbor_count += 1

        # Calcula o overlap_ratio
        # ratio = obras_vizinhas / max(1, obras_vizinhas_esperadas)
        overlap_ratio: float = neighbor_count / max(1, EXPECTED_NEIGHBORS_THRESHOLD)

        # Atualiza o campo no banco
        db.query(PublicWork).filter(PublicWork.id == work_id).update(
            {"territorial_overlap_ratio": overlap_ratio}
        )
        works_updated += 1

        if idx % 100 == 0:
            print(
                f"DEBUG: [OVERLAY]   Processadas {idx + 1}/{total_works} obras..."
            )

    # ── 6. Commit das alterações ──
    if works_updated > 0:
        db.commit()
        print(f"DEBUG: [OVERLAY] Commit realizado: {works_updated} obras atualizadas.")

    stats: dict = {
        "total_works": total_works,
        "works_updated": works_updated,
        "radius_m": radius_m,
        "window_months": window_months,
        "status": "ok",
    }

    print(f"DEBUG: [OVERLAY] ===============================================")
    print(f"DEBUG: [OVERLAY] Cálculo de sobreposição territorial concluído")
    print(f"DEBUG: [OVERLAY]   Total de obras:   {total_works}")
    print(f"DEBUG: [OVERLAY]   Obras atualizadas: {works_updated}")
    print(f"DEBUG: [OVERLAY] ===============================================")

    return stats


def _bisect_left(arr: list[float], target: float) -> int:
    """
    Busca binária: encontra o índice mais à esquerda onde arr[i] >= target.

    Equivalente a bisect.bisect_left, mas implementado manualmente para
    evitar importação adicional.

    Args:
        arr: Lista ordenada de floats.
        target: Valor alvo.

    Returns:
        Índice mais à esquerda onde arr[i] >= target.
    """
    lo: int = 0
    hi: int = len(arr)
    while lo < hi:
        mid: int = (lo + hi) // 2
        if arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _bisect_right(arr: list[float], target: float) -> int:
    """
    Busca binária: encontra o índice mais à direita onde arr[i] <= target.

    Equivalente a bisect.bisect_right, mas implementado manualmente.

    Args:
        arr: Lista ordenada de floats.
        target: Valor alvo.

    Returns:
        Índice mais à direita onde arr[i] <= target (i.e., primeiro índice onde arr[i] > target).
    """
    lo: int = 0
    hi: int = len(arr)
    while lo < hi:
        mid: int = (lo + hi) // 2
        if arr[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo
