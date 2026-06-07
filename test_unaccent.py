"""Testa se a função unaccent está funcionando no SQLite via ORM SQLAlchemy."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sqlalchemy import func, text
from app.db.session import SessionLocal
from app.models.work import PublicWork

db = SessionLocal()
try:
    # Testa a função unaccent via SQL raw
    result = db.execute(text("SELECT unaccent('Macaé')")).scalar()
    print(f"DEBUG: unaccent('Macaé') = '{result}'")
    assert result == "Macae", f"Esperado 'Macae', recebeu '{result}'"

    # Testa filtro via ORM (como o backend faz)
    count = db.query(PublicWork).filter(
        func.unaccent(PublicWork.municipio).ilike("%macae%")
    ).count()
    print(f"DEBUG: ORM filter func.unaccent(municipio).ilike('%macae%') = {count} obras")
    assert count == 779, f"Esperado 779, recebeu {count}"

    # Testa filtro original (que falha)
    count_old = db.query(PublicWork).filter(
        PublicWork.municipio.ilike("%macae%")
    ).count()
    print(f"DEBUG: Filtro antigo municipio.ilike('%macae%') = {count_old} obras (esperado 0)")

    print("DEBUG: Teste de unaccent via ORM PASSOU!")
finally:
    db.close()
