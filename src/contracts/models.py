from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CUSTOMER_ID_PATTERN = r"^C-\d{4}$"
BUSINESS_DATE = date(2026, 9, 30)

PaymentStatus = Literal["SUCCESS", "FAILED", "NOT_PROCESSED"]
ReconciliationStatus = Literal["UPDATED", "ERROR", "PENDING"]
SpecialistStatus = Literal["completed", "partial", "refused"]
GatewayDecision = Literal[
    "allowed",
    "denied_allowlist",
    "denied_customer_mismatch",
    "denied_customer_not_in_catalog",
    "denied_missing_customer",
    "rate_limited",
    "budget_exhausted",
    "replayed",
]
ScenarioName = Literal["aligned", "divergence", "failed"]


class PaymentRecord(BaseModel):
    payment_id: str
    status: PaymentStatus
    amount_cents: int
    currency: Literal["BRL"] = "BRL"
    error_code: str | None = None
    error_detail: str | None = None
    processed_at: datetime | None = None


class PaymentReadResponse(BaseModel):
    source: Literal["replica"] = "replica"
    as_of: datetime
    customer_id: str
    business_date: date
    found: bool
    payment: PaymentRecord | None = None


class ReconciliationRecord(BaseModel):
    reconciliation_id: str
    payment_id: str | None = None
    status: ReconciliationStatus
    expected_amount_cents: int | None = None
    settled_amount_cents: int | None = None
    anomaly_code: str | None = None
    detail: str | None = None


class ReconciliationReadResponse(BaseModel):
    source: Literal["replica"] = "replica"
    as_of: datetime
    customer_id: str
    business_date: date
    found: bool
    reconciliation: ReconciliationRecord | None = None


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(pattern=CUSTOMER_ID_PATTERN)
    business_date: date


class GatewayCall(BaseModel):
    trace_id: str
    task_id: str
    task_customer_id: str | None = None
    tool: str
    arguments: dict
    model: str | None = None


class GatewayResult(BaseModel):
    decision: GatewayDecision
    data: dict | None = None
    detail: str | None = None


class AuditLine(BaseModel):
    trace_id: str
    task_id: str
    agent_id: str
    model: str | None = None
    tool: str
    customer_id: str | None = None
    decision: GatewayDecision
    latency_ms: int
    at: datetime
    redacted_text: str | None = None


class DomainTaskInput(BaseModel):
    customer_id: str = Field(pattern=CUSTOMER_ID_PATTERN)
    business_date: date
    question: str


class DomainTask(BaseModel):
    task_id: str
    trace_id: str
    skill: str
    input: DomainTaskInput


class SpecialistArtifact(BaseModel):
    task_id: str
    trace_id: str
    agent_id: str
    status: SpecialistStatus
    source: Literal["replica"] | None = None
    as_of: datetime | None = None
    customer_id: str
    business_date: date
    summary: str
    data: dict | None = None


class AnomalyTask(BaseModel):
    task_id: str
    trace_id: str
    customer_id: str
    business_date: date
    payments: SpecialistArtifact
    reconciliation: SpecialistArtifact


class AnomalyArtifact(BaseModel):
    task_id: str
    trace_id: str
    agent_id: Literal["anomaly"] = "anomaly"
    customer_id: str
    business_date: date
    anomaly: bool
    codes: list[str]
    explanation: str


class SourceRef(BaseModel):
    domain: Literal["payments", "reconciliation"]
    source: Literal["replica"]
    as_of: datetime | None


class Parecer(BaseModel):
    trace_id: str
    customer_id: str
    business_date: date
    answer: str
    payments_status: str | None
    reconciliation_status: str | None
    anomaly: bool
    sources: list[SourceRef]


class CatalogCustomer(BaseModel):
    customer_id: str


class PublicCatalog(BaseModel):
    business_date: date
    customers: list[CatalogCustomer]


class SessionCustomer(BaseModel):
    customer_id: str
    scenario: ScenarioName


class SessionManifest(BaseModel):
    business_date: date
    replicated_at: datetime
    customers: list[SessionCustomer]

    def public(self) -> PublicCatalog:
        return PublicCatalog(
            business_date=self.business_date,
            customers=[CatalogCustomer(customer_id=item.customer_id) for item in self.customers],
        )

    def ids(self) -> set[str]:
        return {item.customer_id for item in self.customers}

    def customer_for(self, scenario: ScenarioName) -> str:
        for item in self.customers:
            if item.scenario == scenario:
                return item.customer_id
        raise KeyError(scenario)
