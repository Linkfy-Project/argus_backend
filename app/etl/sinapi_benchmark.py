"""
Módulo de benchmark SINAPI para classificação e preenchimento de custo/m² de referência.

Objetivo: preencher o campo benchmark_cost_m2 do modelo PublicWork com base em
tabelas de referência SINAPI/CEF/IBGE para o estado do RJ (jan/2026).

Isso permite que o calculate_cost_score() em scoring.py utilize a Estratégia 1
(Benchmark SINAPI × área) em vez de cair sempre na heurística sem benchmark.

Funções:
- classify_work_type: classifica a descrição da obra em tipo SINAPI via regex.
- apply_sinapi_benchmarks: percorre todas as obras e preenche benchmark_cost_m2.
"""

import re
from sqlalchemy.orm import Session
from app.models.work import PublicWork
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Custos SINAPI de referência por tipo de obra (R$/m²) — RJ/Sudeste 2026 ──
# Fonte: SINAPI/CEF/IBGE (valores de referência para obras públicas)
SINAPI_BENCHMARKS: dict[str, float] = {
    # Tipos de obra genéricos
    "edificacao": 1970.0,           # Edificação padrão (residencial/comercial)
    "edificacao_publica": 2100.0,   # Edificação pública (escola, UBS, creche)
    "pavimentacao": 85.0,           # Pavimentação asfáltica (R$/m² de via)
    "drenagem": 120.0,              # Drenagem pluvial (R$/m linear)
    "saneamento": 150.0,            # Saneamento/esgoto (R$/m linear)
    "urbanizacao": 350.0,           # Urbanização de áreas (R$/m²)
    "reforma": 1200.0,              # Reforma (R$/m²)
    "manutencao": 400.0,            # Manutenção predial (R$/m²)
    "ponte": 5000.0,                # Ponte/viaduto (R$/m²)
    "contencao": 1800.0,            # Contenção/muro (R$/m²)
    "default": 1970.0,              # Default: edificação padrão
}

# ── Mapeamento de regex para tipos SINAPI ──
# Cada tupla: (padrão regex, tipo SINAPI)
# Ordem importa: primeiros matches têm prioridade.
_TYPE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Pavimentação
    (re.compile(r"pavimenta[cç][aã]o|asfalto|asf[aá]ltic[ao]|cal[cç]amento|recapeamento|tapaburaco", re.IGNORECASE), "pavimentacao"),
    # Ponte / Viaduto
    (re.compile(r"ponte|viaduto|passarela|elevad[ao]", re.IGNORECASE), "ponte"),
    # Contenção / Muro
    (re.compile(r"conten[cç][aã]o|muro de arrimo|muro de conten[cç][aã]o|gabi[aã]o", re.IGNORECASE), "contencao"),
    # Edificação pública (escola, UBS, creche, etc.)
    (re.compile(r"escola|creche|ubs|posto de sa[uú]de|hospital|up[aá]|escola municipal|escola estadual|quadra coberta|gin[aá]sio|centro comunit[aá]rio|biblioteca|prefeitura|cras|caps", re.IGNORECASE), "edificacao_publica"),
    # Drenagem
    (re.compile(r"drenagem|galeria|bueiro|boca de lobo|rede pluvial|drenagem pluvial|canaliza[cç][aã]o", re.IGNORECASE), "drenagem"),
    # Saneamento / Esgoto
    (re.compile(r"saneamento|esgoto|rede de esgoto|coleta de esgoto|tratamento de [eá]gua|abastecimento de [aá]gua|ETA|ETE|liga[cç][aã]o domiciliar", re.IGNORECASE), "saneamento"),
    # Urbanização
    (re.compile(r"urbaniza[cç][aã]o|urbanismo|pra[cç]a|parque|paisagismo|logradouro|infraestrutura urbana", re.IGNORECASE), "urbanizacao"),
    # Reforma
    (re.compile(r"reforma|reforma geral|amplia[cç][aã]o|adapta[cç][aã]o|moderniza[cç][aã]o|recupera[cç][aã]o", re.IGNORECASE), "reforma"),
    # Manutenção
    (re.compile(r"manuten[cç][aã]o|conserva[cç][aã]o|manuten[cç][aã]o predial|manuten[cç][aã]o preventiva|manuten[cç][aã]o corretiva", re.IGNORECASE), "manutencao"),
    # Edificação genérica (construção, obra, edificação)
    (re.compile(r"constru[cç][aã]o|edifica[cç][aã]o|obra civil|obra nova|constru[cç][aã]o de|edif[ií]cio", re.IGNORECASE), "edificacao"),
]


def classify_work_type(object_description: str) -> str:
    """
    Classifica a descrição da obra em um tipo SINAPI usando regex.

    Args:
        object_description: Descrição/texto da obra (campo object_description do PublicWork).

    Returns:
        Chave do dicionário SINAPI_BENCHMARKS correspondente ao tipo classificado.
        Se nenhum padrão for reconhecido, retorna "default".
    """
    if not object_description:
        logger.info("[SINAPI] Descrição vazia — usando tipo 'default'")
        return "default"

    # Itera sobre os padrões regex na ordem definida
    for pattern, work_type in _TYPE_PATTERNS:
        if pattern.search(object_description):
            logger.info(f"[SINAPI] Descrição '{object_description[:80]}...' classificada como '{work_type}'")
            return work_type

    # Nenhum padrão reconhecido — usa default
    logger.info(f"[SINAPI] Descrição '{object_description[:80]}...' não reconhecida — usando 'default'")
    return "default"


def apply_sinapi_benchmarks(db: Session) -> dict:
    """
    Aplica os benchmarks SINAPI a todas as obras do banco.

    Para cada obra:
    1. Classifica o tipo pela object_description.
    2. Define benchmark_cost_m2 = SINAPI_BENCHMARKS[tipo].
    3. Atualiza o campo no banco.

    Args:
        db: Sessão do banco de dados SQLAlchemy.

    Returns:
        Dicionário com estatísticas do processamento:
        - total: número total de obras processadas
        - updated: obras que tiveram o benchmark atualizado
        - skipped: obras já com benchmark preenchido (não sobrescreve)
        - classification: contagem por tipo classificado
    """
    logger.info("[SINAPI] ===============================================")
    logger.info("[SINAPI] Iniciando aplicação de benchmarks SINAPI...")

    # Busca todas as obras
    works: list[PublicWork] = db.query(PublicWork).all()
    total = len(works)
    logger.info(f"[SINAPI] Total de obras encontradas: {total}")

    updated = 0
    skipped = 0
    classification: dict[str, int] = {}

    for work in works:
        # Classifica o tipo da obra
        work_type = classify_work_type(work.object_description or "")

        # Conta a classificação
        classification[work_type] = classification.get(work_type, 0) + 1

        # Obtém o benchmark de referência
        benchmark_value = SINAPI_BENCHMARKS.get(work_type, SINAPI_BENCHMARKS["default"])

        # Se já tem benchmark preenchido, pula (não sobrescreve)
        if work.benchmark_cost_m2 is not None and work.benchmark_cost_m2 > 0:
            skipped += 1
            continue

        # Atualiza o campo
        work.benchmark_cost_m2 = benchmark_value
        updated += 1

    # Commit das alterações
    if updated > 0:
        db.commit()
        logger.info(f"[SINAPI] Commit realizado: {updated} obras atualizadas")
    else:
        logger.info("[SINAPI] Nenhuma obra precisou ser atualizada")

    # Log das estatísticas
    logger.info(f"[SINAPI] ── Estatísticas ──")
    logger.info(f"[SINAPI]   Total:      {total}")
    logger.info(f"[SINAPI]   Atualizadas: {updated}")
    logger.info(f"[SINAPI]   Puladas:     {skipped}")
    logger.info(f"[SINAPI]   Classificação por tipo:")
    for tipo, count in sorted(classification.items(), key=lambda x: -x[1]):
        logger.info(f"[SINAPI]     {tipo}: {count}")
    logger.info("[SINAPI] ===============================================")

    return {
        "total": total,
        "updated": updated,
        "skipped": skipped,
        "classification": classification,
    }
