"""
Módulo proxy de estimativa de infrações CREA para o ARGUS.

O CREA-RJ não possui API pública de infrações, portanto este módulo utiliza
duas fontes alternativas para estimar a quantidade de infrações leves, médias
e graves associadas a cada obra pública:

Fonte 1: TCE-RJ — Obras Paralisadas e Penalidades (CSV local)
  - Se a obra consta na lista de obras paralisadas → infração grave.
  - Se o contratado (CNPJ) tem múltiplas obras paralisadas → infração média por obra extra.

Fonte 2: CEIS/CNEP (Portal da Transparência — CGU — API pública)
  - CNPJ com sanção ativa no CEIS (impedimento) → infração média.
  - CNPJ com sanção ativa no CNEP (inidôneo) → infração grave.

Regras adicionais por texto do objeto:
  - Menção a "embargo", "interdição" → infração grave.
  - Menção a "multa", "advertência" → infração leve.

Todas as impressões de depuração começam com "DEBUG: [CREA]".
"""

from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.work import PublicWork

# ── Constantes ────────────────────────────────────────────────────────────────

# URL base da API do Portal da Transparência (CGU)
# NOTA: O endpoint correto usa o subdomínio "api." — sem ele retorna 405
CGU_BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"

# Headers padrão para requisições HTTP (serão complementados com a chave de API)
HEADERS = {"User-Agent": "Mozilla/5.0 (ARGUS Bot)", "Accept": "application/json"}

# Padrões regex para detecção de palavras-chave no texto do objeto da obra
# Palavras que indicam infração grave (embargo, interdição)
PADROES_GRAVE = re.compile(
    r"\b(embargo|interdi[cç][aã]o|interditad[oa])\b",
    re.IGNORECASE,
)

# Palavras que indicam infração leve (multa, advertência)
PADROES_LEVE = re.compile(
    r"\b(multa|advert[eê]ncia|notifica[cç][aã]o)\b",
    re.IGNORECASE,
)

# Caminhos possíveis para os CSVs do TCE-RJ (ordem de prioridade)
TCERJ_CSV_PATHS = [
    Path("data/raw/tcerj/obras_paralisadas_raw.csv"),
    Path("data/raw/tcerj/obras_consolidado.csv"),
]


# ── Funções auxiliares ────────────────────────────────────────────────────────

def _normalizar_cnpj(cnpj: str | None) -> str:
    """
    Remove caracteres não numéricos do CNPJ para padronização.

    Args:
        cnpj: CNPJ com ou sem formatação (ex: "12.345.678/0001-90").

    Returns:
        Apenas os dígitos do CNPJ, ou string vazia se None/vazio.
    """
    if not cnpj:
        return ""
    return re.sub(r"\D", "", str(cnpj))


def _carregar_obras_paralisadas_tcerj() -> pd.DataFrame:
    """
    Carrega o CSV de obras paralisadas do TCE-RJ.

    Tenta os caminhos em ordem de prioridade. Retorna DataFrame vazio
    se nenhum arquivo for encontrado.

    Returns:
        DataFrame com as obras paralisadas (pode estar vazio).
    """
    for csv_path in TCERJ_CSV_PATHS:
        if csv_path.exists():
            print(f"DEBUG: [CREA] Carregando obras paralisadas de: {csv_path}")
            try:
                df = pd.read_csv(csv_path, encoding="utf-8-sig", low_memory=False)
                # Normaliza nomes de colunas (strip espaços)
                df.columns = [str(c).strip() for c in df.columns]
                print(f"DEBUG: [CREA]   ✔ {len(df)} registros carregados")
                return df
            except Exception as exc:
                print(f"DEBUG: [CREA]   ✘ Erro ao ler {csv_path}: {exc}")
                continue

    print(f"DEBUG: [CREA]   ⚠ Nenhum CSV de obras paralisadas encontrado")
    return pd.DataFrame()


def _extrair_cnpjs_paralisados(df: pd.DataFrame) -> dict[str, int]:
    """
    Extrai CNPJs das obras paralisadas e conta quantas obras cada CNPJ tem.

    Args:
        df: DataFrame de obras paralisadas do TCE-RJ.

    Returns:
        Dicionário {cnpj_normalizado: quantidade_de_obras_paralisadas}.
    """
    if df.empty:
        return {}

    # Procura a coluna de CNPJ (pode ter nomes variados)
    cnpj_col = None
    for candidate in ["CNPJContratada", "CNPJCPFContratado", "cnpj_contratada", "cnpj"]:
        if candidate in df.columns:
            cnpj_col = candidate
            break

    if cnpj_col is None:
        print(f"DEBUG: [CREA]   ⚠ Coluna de CNPJ não encontrada nas obras paralisadas. Colunas: {list(df.columns)}")
        return {}

    # Conta obras por CNPJ
    contador: Counter[str] = Counter()
    for cnpj_raw in df[cnpj_col].dropna():
        cnpj = _normalizar_cnpj(cnpj_raw)
        if cnpj and len(cnpj) == 14:  # CNPJ válido tem 14 dígitos
            contador[cnpj] += 1

    print(f"DEBUG: [CREA]   CNPJs únicos nas obras paralisadas: {len(contador)}")
    return dict(contador)


def _consultar_ceis(cnpj: str, api_headers: dict) -> bool:
    """
    Consulta se um CNPJ possui sanção ativa no CEIS (Cadastro de Empresas
    Inidôneas e Suspensas — impedimento de licitar).

    NOTA: O parâmetro correto da API é "codigoSancionado" (confirmado via
    OpenAPI spec em /v3/api-docs). Usar "cnpjSancionado" causa a API a
    IGNORAR o filtro e retornar a primeira página de TODOS os registros,
    gerando falso positivo para todos os CNPJs.

    Args:
        cnpj: CNPJ normalizado (apenas dígitos).
        api_headers: Headers HTTP com a chave de API (chave-api-dados).

    Returns:
        True se o CNPJ tem sanção ativa, False caso contrário.
    """
    try:
        url = f"{CGU_BASE_URL}/ceis"
        # IMPORTANTE: usar "codigoSancionado" e NÃO "cnpjSancionado"
        # "cnpjSancionado" é um parâmetro desconhecido que a API ignora,
        # retornando resultados aleatórios (falso positivo para todos).
        params = {"codigoSancionado": cnpj, "pagina": 1}
        response = requests.get(url, params=params, headers=api_headers, timeout=30)

        if response.status_code == 200:
            data = response.json()
            # Se retornou lista não vazia, há sanções
            return isinstance(data, list) and len(data) > 0

        print(f"DEBUG: [CREA]     CEIS retornou status {response.status_code} para CNPJ {cnpj}")
        return False

    except requests.exceptions.Timeout:
        print(f"DEBUG: [CREA]     ⚠ Timeout ao consultar CEIS para CNPJ {cnpj}")
        return False
    except Exception as exc:
        print(f"DEBUG: [CREA]     ✘ Erro ao consultar CEIS para CNPJ {cnpj}: {exc}")
        return False


def _consultar_cnep(cnpj: str, api_headers: dict) -> bool:
    """
    Consulta se um CNPJ possui sanção ativa no CNEP (Cadastro Nacional de
    Empresas Punidas — inidôneo para licitar).

    NOTA: O parâmetro correto da API é "codigoSancionado" (confirmado via
    OpenAPI spec em /v3/api-docs).

    Args:
        cnpj: CNPJ normalizado (apenas dígitos).
        api_headers: Headers HTTP com a chave de API (chave-api-dados).

    Returns:
        True se o CNPJ tem sanção ativa, False caso contrário.
    """
    try:
        url = f"{CGU_BASE_URL}/cnep"
        # IMPORTANTE: usar "codigoSancionado" e NÃO "cnpjSancionado"
        params = {"codigoSancionado": cnpj, "pagina": 1}
        response = requests.get(url, params=params, headers=api_headers, timeout=30)

        if response.status_code == 200:
            data = response.json()
            return isinstance(data, list) and len(data) > 0

        print(f"DEBUG: [CREA]     CNEP retornou status {response.status_code} para CNPJ {cnpj}")
        return False

    except requests.exceptions.Timeout:
        print(f"DEBUG: [CREA]     ⚠ Timeout ao consultar CNEP para CNPJ {cnpj}")
        return False
    except Exception as exc:
        print(f"DEBUG: [CREA]     ✘ Erro ao consultar CNEP para CNPJ {cnpj}: {exc}")
        return False


def _detectar_palavras_chave(texto: str) -> tuple[int, int, int]:
    """
    Analisa o texto do objeto da obra em busca de palavras-chave que
    indiquem infrações.

    Args:
        texto: Descrição/texto do objeto da obra.

    Returns:
        Tupla (leve, media, grave) com a contagem de cada tipo.
    """
    if not texto:
        return 0, 0, 0

    leve = len(PADROES_LEVE.findall(texto))
    grave = len(PADROES_GRAVE.findall(texto))

    return leve, 0, grave


# ── Função principal ──────────────────────────────────────────────────────────

def sync_crea_proxy(db: Session) -> dict:
    """
    Sincroniza estimativas de infrações CREA usando fontes proxy (TCE-RJ + CGU).

    Fluxo:
    1. Carregar dados de obras paralisadas do TCE-RJ (CSV local).
    2. Detectar palavras-chave no texto do objeto de cada obra.
    3. Consultar CEIS/CNEP para CNPJs únicos no banco (com rate limiting).
    4. Atualizar crea_light_count, crea_medium_count, crea_grave_count.

    Args:
        db: Sessão do SQLAlchemy.

    Returns:
        Dicionário com estatísticas da sincronização.
    """
    settings = get_settings()
    started_at = time.time()

    print(f"DEBUG: [CREA] ===============================================")
    print(f"DEBUG: [CREA] INICIANDO SINCRONIZAÇÃO CREA PROXY")
    print(f"DEBUG: [CREA] ===============================================")

    # Estatísticas de retorno
    stats = {
        "works_processed": 0,
        "works_updated": 0,
        "tcerj_paralyzed_matches": 0,
        "cgu_ceis_matches": 0,
        "cgu_cnep_matches": 0,
        "keyword_grave": 0,
        "keyword_leve": 0,
        "cnpjs_consulted_cgu": 0,
        "duration_seconds": 0.0,
    }

    # ── 1. Carregar obras paralisadas do TCE-RJ ────────────────────────────
    print(f"DEBUG: [CREA] ▶ Etapa 1: Carregando dados de obras paralisadas do TCE-RJ...")
    df_paralisadas = _carregar_obras_paralisadas_tcerj()
    cnpjs_paralisados = _extrair_cnpjs_paralisados(df_paralisadas)

    # ── 2. Carregar todas as obras do banco ────────────────────────────────
    print(f"DEBUG: [CREA] ▶ Etapa 2: Carregando obras do banco de dados...")
    obras = db.query(PublicWork).all()
    print(f"DEBUG: [CREA]   Total de obras no banco: {len(obras)}")

    if not obras:
        print(f"DEBUG: [CREA]   ⚠ Nenhuma obra no banco. Finalizando.")
        stats["duration_seconds"] = round(time.time() - started_at, 2)
        return stats

    # ── 3. Coletar CNPJs únicos e ordenar por frequência ───────────────────
    # Prioriza CNPJs que aparecem em mais obras (mais impacto por consulta)
    cnpj_obras: dict[str, list[PublicWork]] = {}
    for obra in obras:
        cnpj = _normalizar_cnpj(obra.contractor_document)
        if cnpj and len(cnpj) == 14:
            cnpj_obras.setdefault(cnpj, []).append(obra)

    # Ordena por quantidade de obras (decrescente) para priorizar os mais frequentes
    cnpjs_ordenados = sorted(cnpj_obras.keys(), key=lambda c: len(cnpj_obras[c]), reverse=True)

    # Limita ao máximo configurado
    max_cnpjs = settings.CREA_CGU_MAX_CNPJS
    cnpjs_para_consultar = cnpjs_ordenados[:max_cnpjs]
    cnpjs_excluidos = len(cnpjs_ordenados) - len(cnpjs_para_consultar)

    print(f"DEBUG: [CREA]   CNPJs únicos encontrados: {len(cnpj_obras)}")
    print(f"DEBUG: [CREA]   CNPJs a consultar na CGU: {len(cnpjs_para_consultar)} (limite: {max_cnpjs})")
    if cnpjs_excluidos > 0:
        print(f"DEBUG: [CREA]   CNPJs excluídos pelo limite: {cnpjs_excluidos}")

    # ── 4. Consultar CEIS/CNEP na CGU ─────────────────────────────────────
    # Verifica se a chave de API da CGU está configurada.
    # Se não estiver, as consultas CGU são puladas e apenas o TCE-RJ é usado.
    cgu_api_key = settings.CGU_API_KEY
    cnpjs_com_ceis: set[str] = set()
    cnpjs_com_cnep: set[str] = set()

    if not cgu_api_key:
        print(f"DEBUG: [CREA] ▶ Etapa 3: CGU_API_KEY não configurada — pulando consultas CEIS/CNEP.")
        print(f"DEBUG: [CREA]   Cadastre-se em https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email")
        print(f"DEBUG: [CREA]   e adicione CGU_API_KEY=sua-chave no .env")
    else:
        # Monta os headers com a chave de API da CGU
        # A API requer o header "chave-api-dados" com o token de acesso
        api_headers = {**HEADERS, "chave-api-dados": cgu_api_key}

        print(f"DEBUG: [CREA] ▶ Etapa 3: Consultando CEIS/CNEP na CGU...")
        print(f"DEBUG: [CREA]   URL base: {CGU_BASE_URL}")

        for i, cnpj in enumerate(cnpjs_para_consultar):
            if (i + 1) % 10 == 0 or i == 0:
                print(f"DEBUG: [CREA]   Consultando CNPJ {i + 1}/{len(cnpjs_para_consultar)}: {cnpj}")

            # Consulta CEIS (impedimento)
            if _consultar_ceis(cnpj, api_headers):
                cnpjs_com_ceis.add(cnpj)
                print(f"DEBUG: [CREA]     ✔ CEIS: CNPJ {cnpj} TEM sanção ativa (impedimento)")

            # Rate limiting: 1 segundo entre requests
            time.sleep(1)

            # Consulta CNEP (inidôneo)
            if _consultar_cnep(cnpj, api_headers):
                cnpjs_com_cnep.add(cnpj)
                print(f"DEBUG: [CREA]     ✔ CNEP: CNPJ {cnpj} TEM sanção ativa (inidôneo)")

            # Rate limiting: 1 segundo entre requests
            time.sleep(1)

            stats["cnpjs_consulted_cgu"] += 1

        print(f"DEBUG: [CREA]   CEIS (impedidos): {len(cnpjs_com_ceis)} CNPJs")
        print(f"DEBUG: [CREA]   CNEP (inidôneos): {len(cnpjs_com_cnep)} CNPJs")

    # ── 5. Aplicar regras de classificação e atualizar obras ───────────────
    print(f"DEBUG: [CREA] ▶ Etapa 4: Aplicando regras de classificação...")

    for obra in obras:
        cnpj = _normalizar_cnpj(obra.contractor_document)

        # Zera contadores antes de recalcular (idempotente)
        light = 0
        medium = 0
        grave = 0

        # ── Regra TCE-RJ: Obra paralisada ──
        if cnpj and cnpj in cnpjs_paralisados:
            # Primeira obra paralisada = infração grave
            grave += 1
            stats["tcerj_paralyzed_matches"] += 1

            # Se o contratado tem múltiplas obras paralisadas, cada obra extra = infração média
            obras_paralisadas_do_cnpj = cnpjs_paralisados[cnpj]
            if obras_paralisadas_do_cnpj > 1:
                medium += (obras_paralisadas_do_cnpj - 1)

        # ── Regra CEIS (CGU): Sanção ativa (impedimento) ──
        if cnpj and cnpj in cnpjs_com_ceis:
            medium += 1
            stats["cgu_ceis_matches"] += 1

        # ── Regra CNEP (CGU): Sanção ativa (inidôneo) ──
        if cnpj and cnpj in cnpjs_com_cnep:
            grave += 1
            stats["cgu_cnep_matches"] += 1

        # ── Regra por texto do objeto ──
        texto = obra.object_description or ""
        kw_leve, _, kw_grave = _detectar_palavras_chave(texto)
        light += kw_leve
        grave += kw_grave
        if kw_leve > 0:
            stats["keyword_leve"] += kw_leve
        if kw_grave > 0:
            stats["keyword_grave"] += kw_grave

        # ── Atualizar obra se houve mudanças ──
        if (
            light != obra.crea_light_count
            or medium != obra.crea_medium_count
            or grave != obra.crea_grave_count
        ):
            obra.crea_light_count = light
            obra.crea_medium_count = medium
            obra.crea_grave_count = grave
            stats["works_updated"] += 1

        stats["works_processed"] += 1

    # ── 6. Commit no banco ─────────────────────────────────────────────────
    print(f"DEBUG: [CREA] ▶ Etapa 5: Salvando alterações no banco...")
    try:
        db.commit()
        print(f"DEBUG: [CREA]   ✔ Commit realizado com sucesso")
    except Exception as exc:
        db.rollback()
        print(f"DEBUG: [CREA]   ✘ Erro no commit: {exc}")
        stats["error"] = str(exc)

    # ── Finalização ────────────────────────────────────────────────────────
    stats["duration_seconds"] = round(time.time() - started_at, 2)

    print(f"DEBUG: [CREA] ===============================================")
    print(f"DEBUG: [CREA] SINCRONIZAÇÃO CREA PROXY CONCLUÍDA")
    print(f"DEBUG: [CREA]   Obras processadas:     {stats['works_processed']}")
    print(f"DEBUG: [CREA]   Obras atualizadas:     {stats['works_updated']}")
    print(f"DEBUG: [CREA]   TCE-RJ paralisadas:    {stats['tcerj_paralyzed_matches']}")
    print(f"DEBUG: [CREA]   CGU CEIS (impedidos):  {stats['cgu_ceis_matches']}")
    print(f"DEBUG: [CREA]   CGU CNEP (inidôneos):  {stats['cgu_cnep_matches']}")
    print(f"DEBUG: [CREA]   Keywords grave:        {stats['keyword_grave']}")
    print(f"DEBUG: [CREA]   Keywords leve:         {stats['keyword_leve']}")
    print(f"DEBUG: [CREA]   CNPJs consultados CGU: {stats['cnpjs_consulted_cgu']}")
    print(f"DEBUG: [CREA]   Duração:               {stats['duration_seconds']}s")
    print(f"DEBUG: [CREA] ===============================================")

    return stats
