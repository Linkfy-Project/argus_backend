"""Testa se a função unaccent está funcionando no SQLite."""
from sqlalchemy import text
from app.db.session import SessionLocal

db = SessionLocal()
try:
    # Testa a função unaccent
    result = db.execute(text("SELECT unaccent('Macaé')")).scalar()
    print(f"DEBUG: unaccent('Macaé') = '{result}'")
    assert result == "Macae", f"Esperado 'Macae', recebeu '{result}'"

    # Testa filtro com ILIKE usando unaccent
    result2 = db.execute(text("SELECT COUNT(*) FROM public_works WHERE unaccent(municipio) ILIKE '%macae%'")).scalar()
    print(f"DEBUG: Obras com unaccent(municipio) ILIKE '%macae%': {result2}")

    print("DEBUG: Teste de unaccent PASSOU!")
finally:
    db.close()
