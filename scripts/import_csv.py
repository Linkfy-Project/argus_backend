import argparse
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.etl.importer import import_csv

parser = argparse.ArgumentParser(description="Importa CSV de obras para o banco ARGUS")
parser.add_argument("path", help="Caminho do CSV")
parser.add_argument("--municipio", default="Macae")
args = parser.parse_args()

init_db()
db = SessionLocal()
try:
    print(import_csv(db, args.path, default_municipio=args.municipio))
finally:
    db.close()
