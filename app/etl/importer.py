"""
Módulo de importação de CSV para o ARGUS.

Contém funções de limpeza, transformação e importação de dados de obras
públicas a partir de arquivos CSV. Inclui filtro semântico para garantir
que apenas registros que são efetivamente obras públicas de engenharia
sejam importados.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import math
import re
import unicodedata
import warnings

import pandas as pd
from sqlalchemy.orm import Session

# Suprime warnings repetitivos de parse de datas (dayfirst vs formato %Y-%m-%d)
warnings.filterwarnings("ignore", message="Parsing dates in.*dayfirst.*")

from app.models.work import PublicWork
from app.services.work_service import recompute_many
from app.utils.parsing import first_present
from app.core.logging import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────
# Filtro semântico de obras públicas de engenharia
# ──────────────────────────────────────────────

# Regex para identificar obras públicas de engenharia
# Estratégia de dois níveis:
#   Nível 1 - Palavras-chave fortes isoladas (auto-incluem)
#   Nível 2 - Palavras moderadas em contexto (combinadas com outras evidências)
#
# Regras de design para evitar falsos positivos:
# 1. "escola" isoladamente NÃO é aceito (falso positivo em material escolar)
# 2. "hospital" isoladamente NÃO é aceito (falso positivo em compras hospitalares)
# 3. "posto de saúde" ou "unidade básica" → só vale se houver evidência forte de construção
#    (como "construção", "reforma", "obra" nas proximidades)
# 4. Palavras como "construção civil", "engenharia", "pavimentação" → auto-incluem
IS_WORK_STRONG_REGEX = re.compile(r'''(?ix)
    # ── Nível 1: Evidência FORTE (auto-inclui) ──
    (?:
        # Construção civil é sempre obra
        constru[cç][aã]o\s+civil
        |
        # Engenharia diretamente
        (?:obras?\s+de\s+|servi[cç]os?\s+de\s+|empresa\s+especializada\s+em\s+)
        (?:engenharia|constru[cç][aã]o\s+civil)
        |
        # Pavimentação / asfalto / calçamento
        pavimenta[cç][aã]o|asfalto|cal[cç]amento
        |
        # Drenagem / saneamento / esgotamento
        (?:sistema\s+de\s+)?(?:drenagem|esgotamento\s+sanit[aá]rio|macrodrenagem)
        |
        # Estação de tratamento / elevatória
        esta[cç][aã]o\s+(?:de\s+)?(?:tratamento|elevat[oó]ria|recalque)
        |
        # Infraestrutura (urbana, de redes, serviços de)
        infraestrutura\s+(?:do|no|de|em|urbana|de\s+redes|de\s+comunica[cç][aã]o)
        |
        # Serviços de infraestrutura
        servi[cç]os?\s+de\s+infraestrutura
        |
        # Regularização fundiária
        regulariza[cç][aã]o\s+fundi[aá]ria
        |
        # Obra de contenção / estabilização geotécnica
        (?:muro\s+de\s+conten[cç][aã]o|estabiliza[cç][aã]o\s+geot[ée]cnica)
        |
        # Pontes, viadutos, passarelas, ciclovias
        ponte|viaduto|passarela|ciclovia
        |
        # Urbanização de bairros/loteamentos
        urbaniza[cç][aã]o\s+(?:do|de|no)
        |
        # Praça pública
        (?:constru[cç][aã]o|reforma|execu[cç][aã]o).{0,50}(?:pra[çc]a|quadra\s+poliesportiva)
        |
        # Cobertura de quadra
        cobertura\s+de\s+quadra\s+poliesportiva
        |
        # Obras de arte (pontes, etc.)
        (?:tabuleiro\s+de\s+ponte|constru[cç][aã]o\s+da\s+ponte)
        |
        # Rede de captação / galeria
        (?:rede\s+de\s+capta[cç][aã]o|galeria\s+de\s+[áa]guas\s+pluviais)
        |
        # Recuperação estrutural / reforço estrutural
        recupera[cç][aã]o\s+estrutural|refor[cç]o\s+estrutural
        |
        # Estradas vicinais / rurais
        (?:manuten[cç][aã]o\s+(?:de\s+)?)?estradas?\s+vicinais?
        |
        # Materiais/insumos para obra (brita, pedra, etc. quando no contexto de obra)
        (?:aquisi[cç][aã]o|fornecimento).{0,40}(?:brita|pedra\s+de\s+m[aã]o|insumos?\s+brutos?).{0,40}(?:drenagem|pavimenta[cç][aã]o|estradas?|obra|infraestrutura)
        |
        # Manutenção de estradas
        manuten[cç][aã]o\s+ostensiva|manuten[cç][aã]o\s+(?:de\s+)?estradas?
        |
        # Fornecimento de materiais/equipamentos para infraestrutura
        fornecimento\s+de\s+materiais.{0,30}(?:infraestrutura|obra|estrada)
    )
    |
    # ── Nível 2: Evidência MODERADA (requer contexto de construção/reforma) ──
    (?:
        (?:constru[cç][aã]o|reforma|amplia[cç][aã]o|execu[cç][aã]o\s+das?\s+obras?)
        .{0,60}
        (?:
            creche|cemit[eé]rio|delegacia|esta[cç][aã]o\s+ferrovi[aá]ria|
            castelo\s+d[`\'´\x27]?[aã]gua|academia\s+popular|
            conjunto\s+habitacional|condom[ií]nio\s+popular|habita[cç][aã]o\s+popular|
            unidade\s+b[aá]sica\s+de\s+sa[uú]de|posto\s+de\s+sa[uú]de|
            bloco\s+de\s+t[uú]mulo|hemocentro|
            escola\s+municipal|hospital\s+municipal|
            pr[eé]dio\s+do\s+(?:hemocentro|hospital|creche|escola)
        )
    )
''')

# Palavras que indicam que NÃO é obra (falsos positivos comuns)
# Usado como contra-filtro para evitar registros que mencionam "escola", "hospital", etc.
# mas na verdade são compras de material, serviços não-obra etc.
NON_WORK_WEAK_REGEX = re.compile(r'''(?ix)
    # Compras de materiais que NÃO são insumos de obra
    # Nota: materiais como brita, pedra, cimento para obra NÃO devem ser rejeitados
    aquisi[cç][aã]o\s+de\s+(?:
        medicamento|uniforme|ve[ií]culo|material\s+escolar|
        material\s+cama\s+e\s+banho|material\s+de\s+escrit[oó]rio|
        material\s+de\s+primeiros\s+socorros|caf[eé]\s+em\s+p[oó]|
        [aá]gua\s+mineral|fraldas?\s+descart[aá]veis|
        g[eé]neros?\s+aliment[ií]cios
    )|
    medicamento|fraldas?\s+descart[aá]veis|
    insemina[cç][aã]o\s+artificial|
    empr[eé]stimo\s+pessoal|
    # Treinamentos, cursos e capacitação (não são obras)
    curso\s+de\s+(?:capacita[cç][aã]o|treinamento|qualifica[cç][aã]o)|
    capacita[cç][aã]o\s+(?:de\s+)?servidores|
    a[cç][aã]o\s+de\s+capacita[cç][aã]o|
    treinamento\s+(?:de\s+)?servidores|
    inscri[cç][aã]o\s+de\s+servidores|
    qualifica[cç][aã]o\s+profissional|
    contrata[cç][aã]o\s+de\s+artista|
    apresenta[cç][aã]o\s+art[ií]stica|
    palestra|
    show\s+(?:musical|art[ií]stico)|
    evento\s+(?:cultural|art[ií]stico)|
    # Serviços não-relacionados a obras (mas relacionados a estradas DEVEM passar)
    # "infraestrutura de redes de comunicação" é borderline — mantendo como obra
    acesso.*internet|
    fornecimento\s+de\s+energia\s+el[eé]trica|
    concess[aã]o\s+de\s+empr[eé]stimo|
    servi[cç]o\s+de\s+buffet|
    seguran[aç]a\s+p[uú]blica|
    # Serviços de escritório/administrativos
    arbitragem|media[cç][aã]o\s+de\s+conflitos
''')


def is_work_related(
    object_description: str | None,
    contract_type: str | None = None,
) -> bool:
    """
    Verifica se um registro é realmente uma obra pública de engenharia.

    Estratégia de três níveis:
    - Nível 1 (rápido): Se o CSV tem coluna 'Tipo de Contrato' com valor
      'Obras e Serviços de Engenharia', já é aprovado.
    - Nível 2 (contra-filtro): Se o objeto contém palavras típicas de
      compras/serviços não-obra, rejeita.
    - Nível 3 (semântico): Aplica regex com palavras-chave fortes (auto-incluem)
      e palavras moderadas (requerem contexto de construção/reforma).

    Args:
        object_description: Descrição/objeto do contrato ou licitação.
        contract_type: Tipo de contrato (ex: "Obras e Serviços de Engenharia").

    Returns:
        True se o registro é relacionado a obras públicas de engenharia.
    """
    # Nível 1: Tipo de contrato já classifica como obra
    if contract_type is not None and isinstance(contract_type, str):
        ct = contract_type.strip().lower()
        if ct in ("obras e serviços de engenharia", "obras e servicos de engenharia"):
            return True

    # Verifica se object_description é string não-vazia
    if object_description is None or not isinstance(object_description, str):
        return False

    obj_lower = object_description.lower().strip()
    if not obj_lower:
        return False

    # Nível 2: Contra-filtro — verificar se é claramente compra/serviço não-obra
    # Se a regex de não-obra encontrar match, a linha NÃO é obra
    if NON_WORK_WEAK_REGEX.search(obj_lower):
        # Exceção: se também tem palavra forte de obra, pode ser obra mesmo assim
        # Ex: "Aquisição de material para reforma de escola" — isso é obra
        # Mas "Aquisição de material escolar" — não é obra
        # Se tem "construção" ou "reforma" forte, prevalece
        if IS_WORK_STRONG_REGEX.search(obj_lower):
            return True
        return False

    # Nível 3: Regex semântico principal
    return bool(IS_WORK_STRONG_REGEX.search(obj_lower))


# ──────────────────────────────────────────────
# Extração de endereço a partir da descrição
# ──────────────────────────────────────────────

# Regex para extrair endereço do object_description
# Estratégia: tenta padrões do mais específico ao mais genérico.

# Padrão 1: LOCALIZADA NA/NO <endereço completo>
# Captura do início do endereço até delimitador (COM FORNECIMENTO, PARA, MACAÉ, etc.)
ADDR_MAIN = re.compile(
    r'(?:LOCALIZAD[AO]|SITUAD[AO]|EXISTENTE)\s+'
    r'(?:NA|À|NO|EM|Á|A)\s+'
    r'((?:RUA|AVENIDA|AV\.|ESTRADA|PRA[CÇ]A|TRAVESSA|ALAMEDA|LARGO|RODOVIA|VIA)\s+'
    r'(?:[\wÀ-ÿ\.\,\s/\-\(\)]+?))'
    r'(?=,\s*COM\s+FORNECIMENTO|\s+COM\s+FORNECIMENTO|,\s*PARA\s+ATENDER|\.\s*$|;\s*$|\s*MAC[AE])',
    re.IGNORECASE
)

# Padrão 2: LOCALIZADA NO BAIRRO <nome>
ADDR_BAIRRO = re.compile(
    r'(?:LOCALIZAD[AO]|SITUAD[AO])?\s*'
    r'(?:NO|NA|DO|DA|EM)\s+'
    r'((?:BAIRRO|DISTRITO|LOCALIDADE|SUB[-\s]DISTRITO)\s+'
    r'[\wÀ-ÿ]+(?:\s+(?:DOS?|DAS?|DE)\s+[\wÀ-ÿ]+)*)'
    r'(?=,\s*MAC[AE]|\s*MAC[AE]|,\s*COM\s+FORNECIMENTO|$)',
    re.IGNORECASE
)

# Padrão 3: NA/NO <RUA/AV> <nome> (sem LOCALIZADA)
ADDR_VIA = re.compile(
    r'(?:NA|À|NO|EM)\s+'
    r'((?:RUA|AVENIDA|AV\.|ESTRADA|TRAVESSA|ALAMEDA|LARGO|RODOVIA|VIA)\s+'
    r'[\wÀ-ÿ\.\,\s/\-\(\)]+?)'
    r'(?=,\s*COM\s+FORNECIMENTO|\s+COM\s+FORNECIMENTO|,\s*MAC[AE]|\s*MAC[AE]|,\s*PARA|\.\s*$|$)',
    re.IGNORECASE
)

# Padrão 4: Captura genérica de BAIRRO no texto
# "BAIRRO X", "NO BAIRRO X", "DO BAIRRO X"
ADDR_GENERICO = re.compile(
    r'(?:NO|DO|DA|EM)\s+'
    r'((?:BAIRRO|DISTRITO|LOCALIDADE|ILHA|PRAIA)\s+'
    r'[\wÀ-ÿ]+(?:\s+(?:DOS?|DAS?|DE)\s+[\wÀ-ÿ]+)*)',
    re.IGNORECASE
)

# Padrão 5: NO/NA <local> sem BAIRRO (ex: "NO FRADE")
ADDR_SIMPLE = re.compile(
    r'(?:LOCALIZAD[AO]|SITUAD[AO])?\s*(?:NO|NA|EM)\s+'
    r'((?:[A-Z][a-zÀ-ÿ]+\s+){1,4}?MAC[AE])',
    re.IGNORECASE
)

# Após capturar o match, limpa sufixos desnecessários como "MACAÉ/RJ" no final
ADDRESS_CLEANUP_REGEX = re.compile(r'''(?ix)
    ,?\s*MAC[AE][ÓO]?\s*[-–/]?\s*RJ\s*$|
    ,?\s*MACA[EÉ]\s*[-–]\s*RJ\s*$|
    \s*[-–]\s*MACA[EÉ]\s*$
''')


def extract_address_from_description(description: str | None) -> str | None:
    """
    Extrai endereço de uma descrição de obra usando regex.

    Usa múltiplas estratégias em ordem de precisão:
    1. "LOCALIZADA NA RUA X, BAIRRO Y" (via + bairro)
    2. "LOCALIZADA NO BAIRRO Y" (só bairro)
    3. "NA RUA X, BAIRRO Y" (sem prefixo)
    4. "NO BAIRRO Y" (fallback bairro)
    5. "NO SANA, MACAÉ/RJ" (localidade)

    Args:
        description: Descrição/objeto do contrato.

    Returns:
        Endereço extraído ou None se não encontrado.
    """
    # Importa re se não estiver disponível no escopo
    import re as _re_module

    if not description or not isinstance(description, str):
        return None

    desc = description.strip()
    if not desc:
        return None

    # Tenta cada estratégia em ordem
    addr = None
    for regex in [ADDR_MAIN, ADDR_BAIRRO, ADDR_VIA, ADDR_GENERICO, ADDR_SIMPLE]:
        m = regex.search(desc)
        if m and m.lastindex and m.group(1):
            addr = m.group(1).strip()
            break

    if not addr:
        return None

    # Limpa sufixos como ", MACAÉ/RJ" do final
    addr = re.sub(r',?\s*MAC[AE][ÓO]?\s*[-–/]?\s*RJ\s*$', '', addr).strip()
    addr = re.sub(r',?\s*MACA[EÉ]\s*[-–]\s*RJ\s*$', '', addr).strip()
    addr = re.sub(r'\s*[-–]\s*MACA[EÉ]\s*$', '', addr).strip()

    # Remove vírgulas no início/fim e espaços duplicados
    addr = addr.strip(' ,;')
    addr = _re_module.sub(r'\s+', ' ', addr)

    if not addr or len(addr) < 5:
        return None

    return addr[:250]


def clean_value(value):
    """
    Converte valores problemáticos do Pandas para None antes de salvar no banco.
    Resolve NaN, NaT, strings vazias e infinitos.
    """

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None

    if isinstance(value, str):
        value = value.strip()

        if value == "":
            return None

        if value.lower() in {"nan", "nat", "none", "null", "na", "n/a"}:
            return None

        return value

    return value


def clean_str(value) -> str | None:
    value = clean_value(value)

    if value is None:
        return None

    text = str(value).strip()

    if text == "":
        return None

    # Evita coisas como "26.0" quando vier de campo categórico/id.
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]

    return text


# ──────────────────────────────────────────────
# Normalização de nomes de municípios
# ──────────────────────────────────────────────

# Mapeamento canônico de variações de nomes de municípios para o formato padrão
_CANONICAL_MUNICIPIOS: dict[str, str] = {
    "macae": "Macaé",
    "macaé": "Macaé",
    "macae-rj": "Macaé",
    "macae/rj": "Macaé",
}


def normalize_municipio_name(raw: str | None, default: str = "Macae") -> str:
    """
    Normaliza nome de município para formato canônico.

    Remove acentos temporariamente para comparação case-insensitive,
    aplica mapeamento canônico e retorna o nome padronizado.

    Args:
        raw: Nome bruto do município (pode ser None ou vazio).
        default: Valor padrão caso raw seja None/vazio.

    Returns:
        Nome do município normalizado no formato canônico (ex: "Macaé").
    """
    if not raw:
        logger.info(f"normalize_municipio_name - valor vazio, usando default='{default}'")
        return default

    # Remove acentos para comparação
    cleaned = unicodedata.normalize("NFD", raw)
    cleaned = "".join(c for c in cleaned if unicodedata.category(c) != "Mn")
    cleaned = cleaned.strip().lower()

    # Tenta encontrar no mapeamento canônico
    canonical = _CANONICAL_MUNICIPIOS.get(cleaned)
    if canonical:
        logger.info(f"normalize_municipio_name - '{raw}' -> '{canonical}' (via mapeamento canônico)")
        return canonical

    # Se não encontrou no mapeamento, retorna com title case
    result = raw.strip().title()
    logger.info(f"normalize_municipio_name - '{raw}' -> '{result}' (via title case)")
    return result


def clean_float(value) -> float | None:
    value = clean_value(value)

    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()

        if text == "":
            return None

        text = (
            text.replace("R$", "")
            .replace("\xa0", "")
            .replace(" ", "")
            .strip()
        )

        # Formato brasileiro: 1.234.567,89
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        # Formato brasileiro simples: 1234,89
        elif "," in text:
            text = text.replace(",", ".")

        # Mantém apenas número, sinal e ponto decimal.
        text = re.sub(r"[^0-9.\-]", "", text)

        if text in {"", ".", "-", "-."}:
            return None

        value = text

    try:
        number = float(value)

        if math.isnan(number) or math.isinf(number):
            return None

        return number
    except Exception:
        return None


def clean_date(value) -> date | None:
    value = clean_value(value)

    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
            if number > 10**11:
                parsed = pd.to_datetime(number, unit="ms", errors="coerce")
            elif number > 10**9:
                parsed = pd.to_datetime(number, unit="s", errors="coerce")
            else:
                parsed = pd.to_datetime(number, errors="coerce", dayfirst=True)
            if pd.isna(parsed):
                return None
            return parsed.date()
        except Exception:
            return None

    text = str(value).strip()

    if re.fullmatch(r"\d+(\.0)?", text):
        try:
            number = float(text)
            if number > 10**11:
                parsed = pd.to_datetime(number, unit="ms", errors="coerce")
                if not pd.isna(parsed):
                    return parsed.date()
            elif number > 10**9:
                parsed = pd.to_datetime(number, unit="s", errors="coerce")
                if not pd.isna(parsed):
                    return parsed.date()
        except Exception:
            pass

    if text == "":
        return None

    try:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)

        if pd.isna(parsed):
            return None

        return parsed.date()
    except Exception:
        return None


def read_csv_flexible(path: Path) -> pd.DataFrame:
    """
    Lê CSV tentando detectar separador.
    Usa dtype=str e keep_default_na=False para evitar NaN/NaT entrando no fluxo.
    """

    sample = path.read_text(encoding="utf-8-sig", errors="ignore")[:5000]

    sep = ";" if sample.count(";") >= sample.count(",") else ","

    try:
        df = pd.read_csv(
            path,
            sep=sep,
            encoding="utf-8-sig",
            dtype=str,
            keep_default_na=False,
            low_memory=False,
        )
    except UnicodeDecodeError:
        df = pd.read_csv(
            path,
            sep=sep,
            encoding="latin1",
            dtype=str,
            keep_default_na=False,
            low_memory=False,
        )

    df.columns = [str(col).replace("\ufeff", "").strip() for col in df.columns]

    return df


def build_external_id(
    source: str | None,
    contract_number: str | None,
    bidding_number: str | None,
    contractor_document: str | None,
    object_description: str | None,
) -> str | None:
    """
    Cria uma chave razoável para evitar duplicação.
    """

    parts = [
        clean_str(source),
        clean_str(contract_number),
        clean_str(bidding_number),
        clean_str(contractor_document),
    ]

    parts = [part for part in parts if part]

    if parts:
        return "|".join(parts)[:120]

    if object_description:
        return f"{source or 'csv'}|{object_description[:80]}"[:120]

    return None


def row_to_payload(row: dict, default_municipio: str = "Macae") -> dict:
    """
    Converte uma linha de CSV em um payload limpo para PublicWork.
    """

    obj = first_present(
        row,
        [
            "Objeto",
            "objeto",
            "dsobjeto",
            "DescricaoObjeto",
            "DescriçãoObjeto",
            "descricao",
            "Descrição",
            "Descricao",
        ],
    )

    contractor = first_present(
        row,
        [
            "Contratado",
            "contratado",
            "nmempresa",
            "Empresa",
            "empresa",
            "NomeContratado",
        ],
    )

    doc = first_present(
        row,
        [
            "CNPJCPFContratado",
            "cnpjcpfcontratado",
            "cnpj",
            "CNPJ",
            "cpf_cnpj",
            "CNPJ/CPF",
            "DocumentoContratado",
        ],
    )

    municipio = (
        first_present(
            row,
            [
                "municipio",
                "Município",
                "Municipio",
                "Ente",
                "ente",
                "nm_municipio",
                "nome_municipio",
            ],
        )
        or default_municipio
    )

    contract_number = first_present(
        row,
        [
            "Contrato",
            "nrcontrato",
            "NumeroContrato",
            "NúmeroContrato",
            "Nº Contrato",
            "Numero",
            "Número",
        ],
    )

    bidding_number = first_present(
        row,
        [
            "N° Licitação",
            "Nº Licitação",
            "nrlicitacao",
            "NumeroLicitacao",
            "NúmeroLicitação",
            "modalidaenumero",
        ],
    )

    source = first_present(row, ["fonte", "source", "Fonte"]) or "csv_import"

    contract_type = first_present(
        row,
        [
            "TipoContrato",
            "tipo_contrato",
            "idtipocontrato",
            "Tipo Contrato",
            "idtipolicitacao",
        ],
    )

    managing_unit = first_present(
        row,
        [
            "UnidadeGestora",
            "Unidade Gestora",
            "idunidadegestora",
            "idunidadegestoradireta",
            "idunidadegestoraindireta",
        ],
    )

    requesting_agency = first_present(
        row,
        [
            "Órgão Solicitante",
            "OrgaoSolicitante",
            "ÓrgãoSolicitante",
            "idorgaosolicitante",
            "idorgaosolicitanteindireta",
        ],
    )

    object_description = clean_str(obj)

    source_clean = clean_str(source) or "csv_import"
    contract_number_clean = clean_str(contract_number)
    bidding_number_clean = clean_str(bidding_number)
    contractor_document_clean = clean_str(doc)

    external_id = build_external_id(
        source=source_clean,
        contract_number=contract_number_clean,
        bidding_number=bidding_number_clean,
        contractor_document=contractor_document_clean,
        object_description=object_description,
    )

    payload = {
        "external_id": external_id,
        "source": source_clean,
        "municipio": normalize_municipio_name(municipio, default_municipio),
        "object_description": object_description or "",
        "contractor_name": clean_str(contractor),
        "contractor_document": contractor_document_clean,
        "contract_type": clean_str(contract_type),
        "contract_number": contract_number_clean,
        "bidding_number": bidding_number_clean,
        "managing_unit": clean_str(managing_unit),
        "requesting_agency": clean_str(requesting_agency),
        "contract_value": clean_float(
            first_present(
                row,
                [
                    "ValorContrato",
                    "Valor Contrato",
                    "Valor",
                    "nrvalor",
                    "ValorEstimado",
                    "Valor Estimado",
                    "ValorTotalContrato",
                    "valor_original",
                    "valor",
                ],
            )
        ),
        "committed_value": clean_float(
            first_present(row, ["ValorEmpenhado", "Valor Empenhado", "valor_empenhado"])
        ),
        "settled_value": clean_float(
            first_present(row, ["ValorLiquidado", "Valor Liquidado", "valor_liquidado"])
        ),
        "paid_value": clean_float(
            first_present(row, ["ValorPago", "Valor Pago", "valor_pago"])
        ),
        "additive_value": clean_float(
            first_present(row, ["Aditivo", "ValorAditivo", "Valor Aditivo", "valor_aditivo", "ValorFinalAditivado", "valor_final_aditivado"])
        ),
        "area_m2": clean_float(
            first_present(row, ["area_m2", "Área", "Area", "area", "metragem", "metragem_m2"])
        ),
        "benchmark_cost_m2": clean_float(
            first_present(row, ["benchmark_cost_m2", "SINAPI_m2", "sinapi_m2", "custo_referencia_m2", "CustoReferenciaM2"])
        ),
        "crea_light_count": int(clean_float(first_present(row, ["crea_light_count", "infracoes_crea_leves", "CREA_Leve"])) or 0),
        "crea_medium_count": int(clean_float(first_present(row, ["crea_medium_count", "infracoes_crea_medias", "CREA_Media"])) or 0),
        "crea_grave_count": int(clean_float(first_present(row, ["crea_grave_count", "infracoes_crea_graves", "CREA_Grave", "embargos_crea"])) or 0),
        "territorial_overlap_ratio": clean_float(
            first_present(row, ["territorial_overlap_ratio", "overlap_ratio", "recorrencia_territorial_ratio", "sobreposicao_ratio"])
        ),
        "signed_at": clean_date(
            first_present(
                row,
                [
                    "DataAssinaturaContrato",
                    "Data Assinatura Contrato",
                    "DataAssinatura",
                    "DataInicioObra",
                    "DataPublicacaoEdital",
                    "DataHomologacao",
                    "dtlicitacao",
                    "data_assinatura",
                    "Data",
                    "Início",
                    "Inicio",
                ],
            )
        ),
        "due_at": clean_date(
            first_present(
                row,
                [
                    "DataVencimentoContrato",
                    "Data Vencimento Contrato",
                    "DataVencimento",
                    "data_vencimento",
                    "Vigência",
                    "Vigencia",
                    "Fim",
                    "DataUltimaAtualizacao",
                ],
            )
        ),
        "finished_at": clean_date(
            first_present(
                row,
                [
                    "DataConclusao",
                    "DataConclusão",
                    "data_conclusao",
                    "finished_at",
                ],
            )
        ),
        "status": clean_str(
            first_present(row, ["status", "Status", "Situacao", "Situação", "StatusContrato", "TipoParalisacao"])
        ),
        "address": (
            addr := clean_str(
                first_present(row, ["Endereco", "Endereço", "address", "logradouro"])
            )
        ) or extract_address_from_description(object_description),
        "neighborhood": clean_str(
            first_present(row, ["Bairro", "bairro", "neighborhood"])
        ),
        "latitude": clean_float(first_present(row, ["latitude", "lat", "Latitude"])),
        "longitude": clean_float(first_present(row, ["longitude", "lon", "lng", "Longitude"])),
        "idh": clean_float(first_present(row, ["idh", "IDH"])),
    }

    return payload


def find_existing_work(db: Session, payload: dict) -> PublicWork | None:
    """
    Busca obra existente para evitar duplicação em importações periódicas.
    """

    external_id = payload.get("external_id")
    source = payload.get("source")

    if external_id and source:
        existing = (
            db.query(PublicWork)
            .filter(PublicWork.external_id == external_id)
            .filter(PublicWork.source == source)
            .first()
        )

        if existing:
            return existing

    contract_number = payload.get("contract_number")
    contractor_document = payload.get("contractor_document")

    if contract_number and contractor_document:
        return (
            db.query(PublicWork)
            .filter(PublicWork.contract_number == contract_number)
            .filter(PublicWork.contractor_document == contractor_document)
            .first()
        )

    return None


def upsert_work(db: Session, payload: dict) -> tuple[PublicWork, bool]:
    """
    Cria ou atualiza uma obra.
    Retorna: (obra, created=True/False)

    CORREÇÃO: Quando a obra já existe, não sobrescreve campos que possuem
    valor válido com None. Isso evita que coordenadas geocodificadas
    (latitude/longitude), scores calculados e outros campos preenchidos
    por pipelines posteriores sejam apagados quando o sync reimporta os
    mesmos dados do CSV (que não contém esses campos).
    """

    existing = find_existing_work(db, payload)

    if existing:
        for key, value in payload.items():
            # Só sobrescreve se o novo valor NÃO for None,
            # ou se o campo existente também for None.
            # Isso preserva coordenadas geocodificadas, scores, etc.
            if value is not None or getattr(existing, key, None) is None:
                setattr(existing, key, value)

        return existing, False

    work = PublicWork(**payload)
    db.add(work)

    return work, True


def import_csv(
    db: Session,
    path: str | Path,
    default_municipio: str = "Macae",
    recompute: bool = True,
) -> dict:
    """
    Importa CSV para o banco com limpeza de NaN/NaT e proteção contra duplicidade.

    Otimização de performance:
    - Fase 1: Faz apenas db.flush() por linha (sem sync em disco) + um único db.commit()
    - Fase 2: Recalcula scores em lote separado (recompute_work lida com seus próprios commits)
    Isso reduz drasticamente o tempo de importação no SQLite.
    """

    p = Path(path)

    if not p.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {p}")

    df = read_csv_flexible(p)

    created_ids: list[int] = []
    updated_ids: list[int] = []
    errors: list[dict] = []

    total_linhas = len(df)

    DEBUG: str | None = None  # flag para depuração

    # ── Fase 1: Upsert em lote (apenas flush, commit único no final) ──
    for index, record in df.iterrows():
        row_number = int(index) + 2

        try:
            payload = row_to_payload(record.to_dict(), default_municipio=default_municipio)

            # Ignora linhas sem objeto e sem número de contrato/licitação.
            if not payload.get("object_description") and not payload.get("contract_number") and not payload.get("bidding_number"):
                errors.append(
                    {
                        "row": row_number,
                        "status": "skipped",
                        "reason": "Linha sem objeto, contrato ou licitação.",
                    }
                )
                continue

            # Filtro semântico: verifica se o registro é realmente uma obra pública
            if not is_work_related(
                object_description=payload.get("object_description"),
                contract_type=payload.get("contract_type"),
            ):
                errors.append(
                    {
                        "row": row_number,
                        "status": "skipped",
                        "reason": "Registro não é uma obra pública de engenharia (filtro semântico).",
                    }
                )
                continue

            work, created = upsert_work(db, payload)

            # db.flush() popula o work.id sem forçar sync em disco
            db.flush()

            if created:
                created_ids.append(work.id)
            else:
                updated_ids.append(work.id)

        except Exception as exc:
            db.rollback()

            errors.append(
                {
                    "row": row_number,
                    "status": "error",
                    "error": str(exc),
                }
            )

    # Commit único de todas as linhas
    db.commit()

    print(
        f"DEBUG: [IMPORT] Upsert concluído: {len(created_ids)} criados, "
        f"{len(updated_ids)} atualizados, {len([e for e in errors if e.get('status') == 'error'])} erros, "
        f"{len([e for e in errors if e.get('status') == 'skipped'])} pulados em {total_linhas} linhas."
    )

    # ── Fase 2: Recalcular scores em BATCH otimizado ──
    if recompute:
        todos_ids = created_ids + updated_ids
        total_recompute = len(todos_ids)
        logger.info(f"[IMPORT] Recalculando scores de {total_recompute} obras em batch...")

        try:
            result = recompute_many(db, todos_ids)
            logger.info(f"[IMPORT] Recompute batch concluído: {result['updated']} obras processadas.")
        except Exception as exc:
            logger.info(f"[IMPORT]   Erro no recompute batch: {exc}")

    return {
        "path": str(p),
        "created": len(created_ids),
        "updated": len(updated_ids),
        "errors": len([error for error in errors if error.get("status") == "error"]),
        "skipped": len([error for error in errors if error.get("status") == "skipped"]),
        "created_ids_preview": created_ids[:20],
        "updated_ids_preview": updated_ids[:20],
        "errors_preview": errors[:20],
        "preview_limit": 20,
    }