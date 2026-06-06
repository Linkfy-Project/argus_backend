"""
Módulo de correção inflacionária (IPCA) para normalização de custos de obras públicas.

Objetivo: permitir comparar custos de obras de diferentes anos corrigindo-os para
o valor atual usando o IPCA (Índice Nacional de Preços ao Consumidor Amplo),
obtido diretamente da API do Banco Central do Brasil (BCB - SGS série 433).

Exemplo: uma obra de R$ 1 milhão em 2018 equivale a ~R$ 1,45 milhão em 2025.

Funções:
- fetch_ipca_series: consulta a API do BCB e retorna variações mensais do IPCA.
- build_ipca_index: converte variações mensais em números-índice acumulados.
- correct_value: corrige um valor monetário de uma data para outra usando o IPCA.
"""

import math
from datetime import date, datetime
from typing import Optional

import requests

# URL base da API do BCB para a série 433 (IPCA mensal)
BCB_IPCA_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados"
# Formato de data esperado pelo BCB: dd/MM/yyyy
BCB_DATE_FORMAT = "%d/%m/%Y"
# Formato de data retornado pelo BCB na resposta: dd/MM/yyyy
BCB_RESPONSE_DATE_FORMAT = "%d/%m/%Y"
# Timeout da requisição HTTP em segundos
REQUEST_TIMEOUT = 30


def fetch_ipca_series(start_date: str = "2018-01-01") -> dict[str, float]:
    """
    Consulta a API do BCB e retorna as variações mensais do IPCA.

    Args:
        start_date: Data inicial no formato 'YYYY-MM-DD'. Padrão: '2018-01-01'.

    Returns:
        Dicionário {data_string (dd/MM/yyyy): valor_variacao_mensal (float)}.
        Exemplo: {'01/2018': 0.29, '02/2018': 0.32, ...}

    Raises:
        requests.RequestException: Se a requisição à API falhar.
    """
    print(f"DEBUG: [INFLATION] Buscando série IPCA a partir de {start_date}...")

    # Converte a data inicial para o formato dd/MM/yyyy esperado pelo BCB
    try:
        dt = datetime.strptime(start_date, "%Y-%m-%d")
        data_inicial = dt.strftime(BCB_DATE_FORMAT)
    except ValueError:
        print(f"DEBUG: [INFLATION] Formato de data inválido: {start_date}, usando 01/01/2018")
        data_inicial = "01/01/2018"

    params = {
        "formato": "json",
        "dataInicial": data_inicial,
    }

    print(f"DEBUG: [INFLATION] Requisitando BCB: {BCB_IPCA_URL} com params={params}")
    response = requests.get(BCB_IPCA_URL, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    dados = response.json()
    print(f"DEBUG: [INFLATION] Recebidos {len(dados)} registros do BCB")

    # Monta o dicionário {data: variação_mensal}
    series: dict[str, float] = {}
    for item in dados:
        data_str = item.get("data", "")
        valor_str = item.get("valor", "0")
        try:
            valor = float(valor_str.replace(",", "."))
            series[data_str] = valor
        except (ValueError, TypeError):
            print(f"DEBUG: [INFLATION] Valor inválido para data {data_str}: {valor_str}")
            continue

    print(f"DEBUG: [INFLATION] Série IPCA processada: {len(series)} meses")
    return series


def build_ipca_index(series: dict[str, float]) -> dict[str, float]:
    """
    Converte variações mensais do IPCA em números-índice acumulados.

    Fórmula: índice[i] = índice[i-1] * (1 + variação[i] / 100)
    Base = 100 no primeiro mês da série.

    Args:
        series: Dicionário {data_string: variação_mensal} retornado por fetch_ipca_series.

    Returns:
        Dicionário {data_string: índice_acumulado}.
        Exemplo: {'01/2018': 100.0, '02/2018': 100.29, ...}

    Note:
        As datas na resposta do BCB vêm no formato 'dd/MM/yyyy' (ex: '01/01/2018').
        Para ordenar corretamente, convertemos para datetime antes de ordenar.
    """
    print("DEBUG: [INFLATION] Construindo índice acumulado do IPCA...")

    if not series:
        print("DEBUG: [INFLATION] Série vazia — retornando índice vazio")
        return {}

    # Converte as chaves de data para datetime para ordenar cronologicamente
    parsed_dates: list[tuple[datetime, str, float]] = []
    for data_str, valor in series.items():
        try:
            # Tenta formato dd/MM/yyyy (resposta do BCB)
            dt = datetime.strptime(data_str, "%d/%m/%Y")
        except ValueError:
            try:
                # Tenta formato MM/yyyy (alternativo)
                dt = datetime.strptime(data_str, "%m/%Y")
            except ValueError:
                try:
                    # Tenta formato yyyy-MM (ISO)
                    dt = datetime.strptime(data_str, "%Y-%m")
                except ValueError:
                    print(f"DEBUG: [INFLATION] Data não reconhecida: {data_str}")
                    continue
        parsed_dates.append((dt, data_str, valor))

    # Ordena por data
    parsed_dates.sort(key=lambda x: x[0])

    if not parsed_dates:
        print("DEBUG: [INFLATION] Nenhuma data válida encontrada na série")
        return {}

    # Constrói o índice acumulado
    index: dict[str, float] = {}
    current_index = 100.0

    for i, (dt, data_str, variacao) in enumerate(parsed_dates):
        if i == 0:
            # Primeiro mês: base = 100
            current_index = 100.0
        else:
            # Acumula: índice[i] = índice[i-1] * (1 + variação/100)
            current_index = current_index * (1.0 + variacao / 100.0)
        index[data_str] = current_index

    print(f"DEBUG: [INFLATION] Índice acumulado: {len(index)} meses")
    if index:
        first_key = parsed_dates[0][1]
        last_key = parsed_dates[-1][1]
        print(f"DEBUG: [INFLATION]   Primeiro mês: {first_key} = {index[first_key]:.4f}")
        print(f"DEBUG: [INFLATION]   Último mês:   {last_key} = {index[last_key]:.4f}")

    return index


def _find_closest_index(index: dict[str, float], target_date: date) -> Optional[float]:
    """
    Encontra o índice IPCA mais próximo de uma data alvo.

    Args:
        index: Dicionário {data_string: índice_acumulado} retornado por build_ipca_index.
        target_date: Data alvo para buscar o índice.

    Returns:
        O valor do índice mais próximo, ou None se o índice estiver vazio.
    """
    if not index:
        return None

    best_index: Optional[float] = None
    best_diff = math.inf

    for data_str, idx_value in index.items():
        try:
            # Tenta diferentes formatos de data
            try:
                dt = datetime.strptime(data_str, "%d/%m/%Y")
            except ValueError:
                try:
                    dt = datetime.strptime(data_str, "%m/%Y")
                except ValueError:
                    dt = datetime.strptime(data_str, "%Y-%m")

            diff = abs((dt.date() - target_date).days)
            if diff < best_diff:
                best_diff = diff
                best_index = idx_value
        except (ValueError, TypeError):
            continue

    return best_index


def correct_value(
    value: float,
    source_date: date,
    target_date: Optional[date] = None,
) -> float:
    """
    Corrige um valor monetário usando o IPCA acumulado entre duas datas.

    Fórmula: valor_corrigido = valor * (índice_target / índice_source)

    Args:
        value: Valor original a ser corrigido.
        source_date: Data de origem do valor (ex: data de assinatura da obra).
        target_date: Data de destino da correção. Se None, usa a data atual.

    Returns:
        Valor corrigido. Se a API falhar ou não houver dados suficientes,
        retorna o valor original sem correção (fallback seguro).
    """
    if target_date is None:
        target_date = date.today()

    print(f"DEBUG: [INFLATION] Corrigindo R$ {value:,.2f} de {source_date} para {target_date}")

    # Se as datas são iguais, não há correção a fazer
    if source_date == target_date:
        print("DEBUG: [INFLATION] Datas iguais — sem correção necessária")
        return value

    try:
        # Busca a série IPCA (com margem de segurança para incluir meses anteriores)
        start_year = min(source_date.year, target_date.year) - 1
        series = fetch_ipca_series(start_date=f"{start_year}-01-01")
        index = build_ipca_index(series)

        if not index:
            print("DEBUG: [INFLATION] Índice vazio — retornando valor original")
            return value

        # Busca os índices mais próximos das datas de origem e destino
        source_index = _find_closest_index(index, source_date)
        target_index = _find_closest_index(index, target_date)

        if source_index is None or target_index is None:
            print("DEBUG: [INFLATION] Índices não encontrados — retornando valor original")
            return value

        if source_index <= 0:
            print("DEBUG: [INFLATION] Índice de origem <= 0 — retornando valor original")
            return value

        # Calcula o valor corrigido
        correction_factor = target_index / source_index
        corrected_value = value * correction_factor

        print(f"DEBUG: [INFLATION] Índice origem: {source_index:.4f}")
        print(f"DEBUG: [INFLATION] Índice destino: {target_index:.4f}")
        print(f"DEBUG: [INFLATION] Fator de correção: {correction_factor:.6f}")
        print(f"DEBUG: [INFLATION] Valor corrigido: R$ {corrected_value:,.2f}")

        return corrected_value

    except Exception as e:
        print(f"DEBUG: [INFLATION] ERRO na correção: {e} — retornando valor original")
        return value


# ── Cache de série para evitar múltiplas requisições ──
# O cache é preenchido na primeira chamada e reutilizado nas subsequentes.
_series_cache: Optional[dict[str, float]] = None
_index_cache: Optional[dict[str, float]] = None


def _ensure_index_loaded() -> None:
    """Garante que a série e o índice IPCA estejam carregados em memória."""
    global _series_cache, _index_cache
    if _series_cache is not None and _index_cache is not None:
        return

    try:
        _series_cache = fetch_ipca_series()
        _index_cache = build_ipca_index(_series_cache)
        print(f"DEBUG: [INFLATION] Cache carregado: {len(_index_cache)} índices")
    except Exception as e:
        print(f"DEBUG: [INFLATION] ERRO ao carregar cache: {e}")
        _series_cache = {}
        _index_cache = {}


def correct_value_cached(
    value: float,
    source_date: date,
    target_date: Optional[date] = None,
) -> float:
    """
    Versão com cache de correct_value(). Usa o cache global para evitar
    múltiplas requisições à API do BCB durante uma mesma execução.

    Args:
        value: Valor original a ser corrigido.
        source_date: Data de origem do valor.
        target_date: Data de destino (padrão: data atual).

    Returns:
        Valor corrigido, ou valor original em caso de falha.
    """
    global _series_cache, _index_cache

    if target_date is None:
        target_date = date.today()

    # Se as datas são iguais, não há correção
    if source_date == target_date:
        return value

    # Garante que o cache está carregado
    _ensure_index_loaded()

    if not _index_cache:
        print("DEBUG: [INFLATION] Cache vazio — retornando valor original")
        return value

    # Busca os índices mais próximos
    source_index = _find_closest_index(_index_cache, source_date)
    target_index = _find_closest_index(_index_cache, target_date)

    if source_index is None or target_index is None:
        print("DEBUG: [INFLATION] Índices não encontrados no cache — retornando valor original")
        return value

    if source_index <= 0:
        return value

    # Calcula o valor corrigido
    correction_factor = target_index / source_index
    corrected_value = value * correction_factor

    print(f"DEBUG: [INFLATION] Cache hit — fator: {correction_factor:.6f}, "
          f"corrigido: R$ {corrected_value:,.2f}")

    return corrected_value


def invalidate_cache() -> None:
    """Limpa o cache de séries IPCA. Útil para forçar recarga da API."""
    global _series_cache, _index_cache
    _series_cache = None
    _index_cache = None
    print("DEBUG: [INFLATION] Cache invalidado")
