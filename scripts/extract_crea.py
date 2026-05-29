"""
Script para extrair menções ao CREA a partir do texto livre dos CSVs de contratos.
Objetivo: confirmar que é possível capturar dados do CREA (nº registro, nome do profissional)
e quantas obras possuem essas informações.
"""

import re
import csv
from pathlib import Path
from collections import Counter


# ── Padrões regex para extrair dados do CREA ──────────────────────────────

# Padrão 1: "CREA-RJ sob o nº XXXXXXX" ou "CREA/RJ nº XXXXXXX"
CREA_NUM_PATTERN = re.compile(
    r'CREA[\s\-\/]*(?:RJ|MG|SP|PR|BA|RS|PE|CE|GO|DF|ES|SC|PA|AM|MA|PB|RN|AL|SE|PI|MT|MS|RO|AC|AP|RR|TO)[\s\-]*(?:sob\s+o\s+)?(?:n[ºo°.]|número|n\.?)\s*(?:[nN]º?\s*)?(\d{5,})',
    re.IGNORECASE
)

# Padrão 2: "CREA-RJ" seguido de número em contexto próximo
CREA_NUM_LOOSE = re.compile(
    r'CREA[\s\-\/]*(?:RJ|MG|SP|PR|BA|RS|PE|CE|GO|DF|ES|SC|PA|AM|MA|PB|RN|AL|SE|PI|MT|MS|RO|AC|AP|RR|TO)[\s\-;,]*(?:sob\s+o\s+)?(?:n[ºo°.]?\s*)?(\d{6,})',
    re.IGNORECASE
)

# Padrão 3: "inscrito(a) no CREA" capturando o nome do profissional antes
# Ex: "Engenheiro Civil, NOME COMPLETO, inscrito no CREA-RJ sob o nº XXXXX"
PROFESSIONAL_PATTERN = re.compile(
    r'(?:Engenheiro(?:\s+\w+)?|Arquiteto|Técnico(?:\s+\w+)?),?\s+'
    r'((?:[A-ZÁÉÍÓÚÃÕÂÊÎÔÛÇ][a-záéíóúãõâêîôûç]+\s+){2,}[A-ZÁÉÍÓÚÃÕÂÊÎÔÛÇ][a-záéíóúãõâêîôûç]+)'
    r'[,.\s]+inscri[tc](?:o|a)\s+no\s+CREA',
    re.IGNORECASE
)

# Padrão 4: Capturar o nome do profissional + CREA em sequência
PROF_CREA_PATTERN = re.compile(
    r'((?:Engenheiro(?:\s+\w+)?|Arquiteto)[^,]*),?\s*'
    r'([A-ZÁÉÍÓÚÃÕÂÊÎÔÛÇ][A-Z\s\.]+?)[\s,]+inscri[tc](?:o|a)\s+no\s+CREA[\s\-\/]*(\w+)[\s\-]*(?:sob\s+o\s+)?(?:n[ºo°.]?\s*)?(\d{5,})',
    re.IGNORECASE
)

# Padrão 5: "responsável técnica(o)" + nome
RESPONSAVEL_PATTERN = re.compile(
    r'respons[aá]vel\s+t[eé]cnic[ao][\s:,]+'
    r'((?:[A-ZÁÉÍÓÚÃÕÂÊÎÔÛÇa-záéíóúãõâêîôûç]+\s+){2,}?)[\s,]*'
    r'(?:inscri[tc](?:o|a)\s+no\s+CREA|CREA)',
    re.IGNORECASE
)


def extract_crea_from_text(text: str) -> list[dict]:
    """
    Extrai todas as menções ao CREA de um texto livre.
    Retorna lista de dicts com: registration, professional_name, state, raw_match
    """
    if not text:
        return []

    results = []
    seen_registrations = set()

    # Busca por números CREA com padrão principal
    for match in CREA_NUM_PATTERN.finditer(text):
        reg_number = match.group(1)
        if reg_number not in seen_registrations:
            seen_registrations.add(reg_number)
            # Tenta extrair contexto ao redor (200 chars antes)
            start = max(0, match.start() - 200)
            context = text[start:match.end()]
            
            # Tenta extrair nome do profissional do contexto
            prof_name = None
            prof_match = PROFESSIONAL_PATTERN.search(context)
            if prof_match:
                prof_name = prof_match.group(1).strip()
            
            if not prof_name:
                resp_match = RESPONSAVEL_PATTERN.search(context)
                if resp_match:
                    prof_name = resp_match.group(1).strip()

            # Extrai estado do CREA
            state_match = re.search(r'CREA[\s\-\/]*(\w{2})', match.group(0), re.IGNORECASE)
            state = state_match.group(1).upper() if state_match else None

            results.append({
                "registration": reg_number,
                "professional_name": prof_name,
                "state": state,
                "raw_match": match.group(0),
            })

    # Busca por padrão mais flexível para capturar os que o principal não pegou
    for match in CREA_NUM_LOOSE.finditer(text):
        reg_number = match.group(1)
        if reg_number not in seen_registrations:
            seen_registrations.add(reg_number)
            state_match = re.search(r'CREA[\s\-\/]*(\w{2})', match.group(0), re.IGNORECASE)
            state = state_match.group(1).upper() if state_match else None
            results.append({
                "registration": reg_number,
                "professional_name": None,
                "state": state,
                "raw_match": match.group(0),
            })

    return results


def analyze_csv_file(csv_path: Path, label: str) -> None:
    """Analisa um CSV buscando menções ao CREA em todas as colunas de texto."""
    print(f"\n{'='*80}")
    print(f"  ANÁLISE DE CREA NO ARQUIVO: {label}")
    print(f"  Caminho: {csv_path}")
    print(f"{'='*80}")

    if not csv_path.exists():
        print(f"  ❌ Arquivo não encontrado!")
        return

    # Lê o CSV
    sample = csv_path.read_text(encoding="utf-8-sig", errors="ignore")[:5000]
    sep = ";" if sample.count(";") >= sample.count(",") else ","

    rows = []
    with open(csv_path, encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f, delimiter=sep)
        for row in reader:
            rows.append(row)

    print(f"\n  Total de linhas: {len(rows)}")
    print(f"  Colunas: {list(rows[0].keys()) if rows else 'N/A'}")

    # Busca CREA em TODAS as colunas de texto
    crea_mentions = []  # (linha_num, contrato, coluna, extracoes)
    all_registrations = []
    all_professionals = []
    column_hits = Counter()

    for i, row in enumerate(rows):
        # Identifica o contrato (primeira coluna geralmente)
        contrato_id = list(row.values())[0] if row else f"Linha {i+1}"
        
        for col_name, cell_value in row.items():
            if not cell_value or not isinstance(cell_value, str):
                continue
            
            # Busca rápida por "CREA" antes de rodar regex pesado
            if "CREA" not in cell_value.upper() and "crea" not in cell_value.lower():
                continue

            extractions = extract_crea_from_text(cell_value)
            if extractions:
                crea_mentions.append((i + 2, contrato_id, col_name, extractions))
                column_hits[col_name] += 1
                for ext in extractions:
                    all_registrations.append(ext["registration"])
                    if ext["professional_name"]:
                        all_professionals.append(ext["professional_name"])

    # ── Resultados ──────────────────────────────────────────────────────
    unique_regs = set(all_registrations)
    unique_profs = set(all_professionals)

    print(f"\n  📊 RESUMO:")
    print(f"  Linhas com menção ao CREA: {len(crea_mentions)}")
    print(f"  Registros CREA únicos encontrados: {len(unique_regs)}")
    print(f"  Profissionais únicos identificados: {len(unique_profs)}")
    print(f"\n  Colunas onde aparece CREA:")
    for col, count in column_hits.most_common():
        print(f"    - {col}: {count} ocorrências")

    print(f"\n  📋 REGISTROS CREA ÚNICOS:")
    for reg in sorted(unique_regs):
        print(f"    CREA nº {reg}")

    print(f"\n  👤 PROFISSIONAIS IDENTIFICADOS:")
    for prof in sorted(unique_profs):
        print(f"    - {prof}")

    # Mostra até 10 exemplos detalhados
    print(f"\n  📝 EXEMPLOS DETALHADOS (até 10):")
    for linha, contrato, coluna, extracoes in crea_mentions[:10]:
        print(f"\n    Linha {linha} | Contrato: {contrato[:60]}...")
        print(f"    Coluna: {coluna}")
        for ext in extracoes:
            print(f"      → CREA: {ext['registration']} | Estado: {ext['state']} | Profissional: {ext['professional_name'] or 'N/A'}")
            # Mostra trecho do raw_match truncado
            raw = ext['raw_match'][:120]
            print(f"        Texto: \"{raw}...\"")

    # ── Busca adicional: menções a "CREA" sem número (embargos, infrações) ──
    print(f"\n  🔍 BUSCA POR MENÇÕES GENÉRICAS AO CREA (sem número):")
    generic_crea_count = 0
    generic_examples = []
    for i, row in enumerate(rows):
        for col_name, cell_value in row.items():
            if not cell_value or not isinstance(cell_value, str):
                continue
            text_upper = cell_value.upper()
            if "CREA" in text_upper:
                # Verifica se já foi capturado como registro
                extractions = extract_crea_from_text(cell_value)
                if not extractions and "CREA" in cell_value:
                    generic_crea_count += 1
                    if len(generic_examples) < 5:
                        # Extrai trecho ao redor de "CREA"
                        idx = text_upper.index("CREA")
                        start = max(0, idx - 80)
                        end = min(len(cell_value), idx + 80)
                        snippet = cell_value[start:end].replace("\n", " ").strip()
                        contrato_id = list(row.values())[0][:50] if row else "?"
                        generic_examples.append((i + 2, contrato_id, col_name, snippet))

    print(f"  Menções genéricas (sem nº extraível): {generic_crea_count}")
    for linha, contrato, col, snippet in generic_examples:
        print(f"    Linha {linha} | {col}: \"...{snippet}...\"")


def main():
    """Função principal - analisa todos os CSVs relevantes."""
    print("=" * 80)
    print("  EXTRAÇÃO DE DADOS CREA A PARTIR DE TEXTO LIVRE NOS CSVs")
    print("=" * 80)

    # Arquivos a analisar
    csv_files = [
        (Path("data/raw/macae/contratos.csv"), "Contratos Macaé (Portal Transparência)"),
        (Path("data/raw/macae/licitacoes.csv"), "Licitações Macaé (Portal Transparência)"),
        (Path("data/raw/tcerj/contratos_raw.csv"), "Contratos TCERJ"),
        (Path("data/raw/tcerj/licitacoes_raw.csv"), "Licitações TCERJ"),
        (Path("data/raw/tcerj/obras_consolidado.csv"), "Obras Consolidado TCERJ"),
    ]

    for csv_path, label in csv_files:
        analyze_csv_file(csv_path, label)

    print(f"\n{'='*80}")
    print("  ANÁLISE CONCLUÍDA")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
