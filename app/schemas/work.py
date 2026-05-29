from datetime import date, datetime
import math
from pydantic import BaseModel, ConfigDict

class WorkBase(BaseModel):
    external_id: str | None = None
    source: str = "manual"
    municipio: str = "Macae"
    object_description: str = ""
    contractor_name: str | None = None
    contractor_document: str | None = None
    contract_type: str | None = None
    contract_number: str | None = None
    bidding_number: str | None = None
    managing_unit: str | None = None
    requesting_agency: str | None = None
    contract_value: float | None = None
    committed_value: float | None = None
    settled_value: float | None = None
    paid_value: float | None = None
    additive_value: float | None = None
    area_m2: float | None = None
    benchmark_cost_m2: float | None = None
    crea_light_count: int = 0
    crea_medium_count: int = 0
    crea_grave_count: int = 0
    territorial_overlap_ratio: float | None = None
    signed_at: date | None = None
    due_at: date | None = None
    finished_at: date | None = None
    status: str | None = None
    address: str | None = None
    neighborhood: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    idh: float | None = None

class WorkCreate(WorkBase):
    pass

class WorkUpdate(WorkBase):
    pass

class AlertRead(BaseModel):
    id: int
    code: str
    severity: str
    severity_weight: float = 0.0
    severity_multiplier: float = 1.0
    weighted_severity: float = 0.0
    message: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class WorkRead(WorkBase):
    id: int
    efficiency_score: float | None = None
    cost_score: float | None = None
    deadline_score: float | None = None
    quality_score: float | None = None
    recurrence_score: float | None = None
    social_impact_score: float | None = None
    risk_delay_probability: float | None = None
    risk_cost_probability: float | None = None
    risk_rework_probability: float | None = None
    created_at: datetime
    updated_at: datetime
    alerts: list[AlertRead] = []
    model_config = ConfigDict(from_attributes=True)

class ScoreDetails(BaseModel):
    efficiency_score: float
    components: dict
    alerts: list[dict]

class ScoringRules(BaseModel):
    weights: dict
    formulas: dict
    triggers: dict

class PredictionInput(BaseModel):
    contract_value: float | None = None
    committed_value: float | None = None
    settled_value: float | None = None
    additive_value: float | None = None
    area_m2: float | None = None
    benchmark_cost_m2: float | None = None
    crea_light_count: int = 0
    crea_medium_count: int = 0
    crea_grave_count: int = 0
    territorial_overlap_ratio: float | None = None
    delay_days: int | None = None
    contractor_recurrence: int | None = None
    idh: float | None = None

class PredictionOutput(BaseModel):
    delay_probability: float
    cost_overrun_probability: float
    rework_probability: float
    model_version: str

class PaginatedWorks(BaseModel):
    """Resposta paginada para listagem de obras."""
    items: list[WorkRead]
    total: int
    page: int
    per_page: int
    total_pages: int
