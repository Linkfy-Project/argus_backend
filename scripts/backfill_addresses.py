"""
Script de migração para preencher endereços (address) das obras existentes
no banco de dados usando extração por regex da descrição (object_description).

Uso:
    python scripts/backfill_addresses.py

O script atualiza apenas registros onde address está vazio/NULL.
Faz um batch commit no final para performance.
"""

from pathlib import Path
import sys

# Garante que o diretório raiz está no path para importar o projeto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.etl.importer import extract_address_from_description


def backfill_addresses():
    """
    Percorre todas as obras sem address preenchido e tenta extrair
    um endereço da descrição (object_description).
    """
    init_db()
    db = SessionLocal()

    try:
        from app.models.work import PublicWork

        # Busca obras sem address
        works = (
            db.query(PublicWork)
            .filter(
                (PublicWork.address.is_(None))
                | (PublicWork.address == "")
            )
            .all()
        )

        print(f"DEBUG: [BACKFILL] Encontradas {len(works)} obras sem endereço.")

        atualizados = 0
        pulados = 0

        for work in works:
            if not work.object_description:
                pulados += 1
                continue

            # Extrai endereço da descrição
            endereco = extract_address_from_description(work.object_description)

            if endereco:
                work.address = endereco
                atualizados += 1
                print(f"DEBUG: [BACKFILL] ID {work.id}: \"{endereco[:80]}\"")
            else:
                pulados += 1

        db.commit()

        print(
            f"\nDEBUG: [BACKFILL] Concluído: {atualizados} atualizados, "
            f"{pulados} pulados (sem endereço extraível)."
        )

    except Exception as exc:
        db.rollback()
        print(f"DEBUG: [BACKFILL] Erro: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    backfill_addresses()
