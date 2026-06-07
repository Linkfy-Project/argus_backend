import hashlib
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text, ForeignKey, event
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base

class PublicWork(Base):
    __tablename__ = "public_works"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    external_id: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    source: Mapped[str] = mapped_column(String(80), index=True, default="manual")
    municipio: Mapped[str] = mapped_column(String(120), index=True, default="Macae")
    object_description: Mapped[str] = mapped_column(Text, default="")
    description_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    contractor_name: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    contractor_document: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    contract_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    contract_number: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    bidding_number: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    managing_unit: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requesting_agency: Mapped[str | None] = mapped_column(String(255), nullable=True)

    contract_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    committed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    settled_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    paid_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    additive_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_cost_m2: Mapped[float | None] = mapped_column(Float, nullable=True)

    crea_light_count: Mapped[int] = mapped_column(Integer, default=0)
    crea_medium_count: Mapped[int] = mapped_column(Integer, default=0)
    crea_grave_count: Mapped[int] = mapped_column(Integer, default=0)
    territorial_overlap_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)

    signed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    finished_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str | None] = mapped_column(String(80), nullable=True)

    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    neighborhood: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    idh: Mapped[float | None] = mapped_column(Float, nullable=True)

    efficiency_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    deadline_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    recurrence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    social_impact_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_delay_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_cost_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_rework_probability: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    alerts: Mapped[list["Alert"]] = relationship(back_populates="work", cascade="all, delete-orphan")

    def compute_description_hash(self) -> None:
        """Calcula e atribui o hash SHA-256 do object_description."""
        if self.object_description:
            self.description_hash = hashlib.sha256(
                self.object_description.encode("utf-8")
            ).hexdigest()


@event.listens_for(PublicWork, "before_insert")
def _public_work_before_insert(mapper, connection, target: PublicWork) -> None:
    """Evento que calcula o description_hash automaticamente antes de inserir."""
    target.compute_description_hash()


@event.listens_for(PublicWork, "before_update")
def _public_work_before_update(mapper, connection, target: PublicWork) -> None:
    """Evento que recalcula o description_hash se a descrição mudou."""
    # Verifica se o object_description foi modificado
    from sqlalchemy import inspect as sa_inspect
    state = sa_inspect(target)
    hist = state.attrs.object_description.history
    if hist.has_changes():
        target.compute_description_hash()

class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    work_id: Mapped[int] = mapped_column(ForeignKey("public_works.id"), index=True)
    code: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    severity_weight: Mapped[float] = mapped_column(Float, default=0.0)
    severity_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    weighted_severity: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text)
    # Status do alerta: Novo, Em análise, Encaminhado, Resolvido, Descartado
    status: Mapped[str] = mapped_column(String(40), default="Novo", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    work: Mapped[PublicWork] = relationship(back_populates="alerts")


class ModelCache(Base):
    """
    Cache de resultados da pipeline de IA (OpenRouter).
    Armazena a classificação e extração de endereço feita pelo modelo
    para cada descrição única (identificada pelo hash SHA-256).
    Esta tabela NUNCA deve ser excluída ou resetada, mesmo com FORCE_RESET.
    """
    __tablename__ = "model_cache"

    # Chave primária: hash SHA-256 da descrição do objeto
    description_hash: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    # Descrição original (para auditoria/debug)
    object_description: Mapped[str] = mapped_column(Text, default="")
    # Se a descrição é uma obra (true) ou não (false)
    is_obra: Mapped[int] = mapped_column(Integer, default=0)  # 0=False, 1=True
    # Local/logradouro extraído da descrição
    local: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Cidade extraída da descrição
    cidade: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Estado (UF) extraído da descrição
    estado: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # Endereço formatado para geocodificação via OpenStreetMap/Nominatim
    extracao_endereco: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ID do modelo usado no OpenRouter
    model_id: Mapped[str] = mapped_column(String(255), default="")
    # Resposta bruta completa do modelo (JSON serializado)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Status do processamento: "ok", "error", "pending"
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # Mensagem de erro (se houver)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Coordenadas geográficas (cache de geocodificação para evitar retrabalho)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
