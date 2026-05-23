from app.db.session import Base, engine
from app.models.work import PublicWork, Alert
from app.models.geo import GeoLayer


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
