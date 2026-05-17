from datetime import date, datetime
from sqlalchemy import Date, DateTime, Float, Integer, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base

class PublicWork(Base):
    __tablename__ = "public_works"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    external_id: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    source: Mapped[str] = mapped_column(String(80), index=True, default="manual")
    municipio: Mapped[str] = mapped_column(String(120), index=True, default="Macae")
    object_description: Mapped[str] = mapped_column(Text, default="")
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

class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    work_id: Mapped[int] = mapped_column(ForeignKey("public_works.id"), index=True)
    code: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    work: Mapped[PublicWork] = relationship(back_populates="alerts")
