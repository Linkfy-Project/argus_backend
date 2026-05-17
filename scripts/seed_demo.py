from datetime import date, timedelta
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.schemas.work import WorkCreate
from app.services.work_service import create_work

SAMPLES = [
    WorkCreate(
        source="demo", municipio="Macae", object_description="Revitalização da Praia de Cavaleiros", contractor_name="Engetécnica Serviços e Construções Ltda", contractor_document="27974948000102",
        contract_value=12_000_000, committed_value=12_000_000, settled_value=8_000_000, additive_value=0, signed_at=date.today()-timedelta(days=400), due_at=date.today()-timedelta(days=150), address="Av. Atlântica, Cavaleiros, Macaé - RJ", neighborhood="Cavaleiros", latitude=-22.4062, longitude=-41.7976, idh=0.82, area_m2=6000
    ),
    WorkCreate(
        source="demo", municipio="Macae", object_description="Construção de condomínio popular no Imburo", contractor_name="João Fortes Engenharia", contractor_document="33035536000100",
        contract_value=140_000_000, committed_value=140_000_000, settled_value=65_000_000, additive_value=0, signed_at=date.today()-timedelta(days=800), due_at=date.today()-timedelta(days=300), address="Estrada do Imburo, Macaé - RJ", neighborhood="Imburo", latitude=-22.2901, longitude=-41.8812, idh=0.55, area_m2=50000
    ),
    WorkCreate(
        source="demo", municipio="Macae", object_description="Pavimentação e drenagem em via municipal", contractor_name="Construtora Exemplo", contractor_document="11222333000144",
        contract_value=3_500_000, committed_value=3_500_000, settled_value=3_200_000, additive_value=800_000, signed_at=date.today()-timedelta(days=220), due_at=date.today()+timedelta(days=30), address="Centro, Macaé - RJ", neighborhood="Centro", latitude=-22.3717, longitude=-41.7857, idh=0.72, area_m2=3000
    ),
]

if __name__ == "__main__":
    init_db()
    db = SessionLocal()
    try:
        for item in SAMPLES:
            create_work(db, item)
        print(f"Seed concluído: {len(SAMPLES)} obras demo criadas.")
    finally:
        db.close()
