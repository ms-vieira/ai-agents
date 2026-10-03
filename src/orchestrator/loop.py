"""O orquestrador escolhe o fluxo. O customer id sai da pergunta, não do texto da réplica."""

import os
import re
import uuid
from datetime import date

import httpx

from model_client import complete
from contracts.models import (
    AnomalyArtifact,
    AnomalyTask,
    DomainTask,
    DomainTaskInput,
    Parecer,
    PublicCatalog,
    SessionManifest,
    SourceRef,
    SpecialistArtifact,
)
from settings import BUSINESS_DATE

CUSTOMER_RE = re.compile(r"\bC-\d{4}\b")
ORCHESTRATOR_MODEL = os.environ.get("ORCHESTRATOR_MODEL", "gpt-4.1")
REPLICA_WARNING = "A leitura vem da réplica e pode estar atrás do processamento transacional."


class AgentDirectory:
    def payments(self, task: DomainTask) -> SpecialistArtifact:
        raise NotImplementedError

    def reconciliation(self, task: DomainTask) -> SpecialistArtifact:
        raise NotImplementedError

    def anomaly(self, task: AnomalyTask) -> AnomalyArtifact:
        raise NotImplementedError


class HttpAgentDirectory(AgentDirectory):
    def __init__(self, payments_url: str, reconciliation_url: str, anomaly_url: str) -> None:
        self._urls = {
            "payments": payments_url.rstrip("/"),
            "reconciliation": reconciliation_url.rstrip("/"),
            "anomaly": anomaly_url.rstrip("/"),
        }
        self._skills = {
            "payments": "lookup_customer_processing",
            "reconciliation": "lookup_customer_reconciliation",
            "anomaly": "detect_anomaly",
        }
        self._http = httpx.Client(timeout=10)

    def payments(self, task: DomainTask) -> SpecialistArtifact:
        return SpecialistArtifact.model_validate(self._send("payments", task.model_dump(mode="json")))

    def reconciliation(self, task: DomainTask) -> SpecialistArtifact:
        return SpecialistArtifact.model_validate(
            self._send("reconciliation", task.model_dump(mode="json"))
        )

    def anomaly(self, task: AnomalyTask) -> AnomalyArtifact:
        return AnomalyArtifact.model_validate(self._send("anomaly", task.model_dump(mode="json")))

    def _send(self, name: str, payload: dict) -> dict:
        base = self._urls[name]
        card = self._http.get(f"{base}/.well-known/agent-card.json")
        card.raise_for_status()
        skill_ids = [skill.get("id") for skill in card.json().get("skills", [])]
        if self._skills[name] not in skill_ids:
            raise RuntimeError(f"o cartão de {name} não publica a skill esperada")
        response = self._http.post(f"{base}/v1/tasks", json=payload)
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._http.close()


def answer_question(
    question: str,
    manifest: SessionManifest,
    agents: AgentDirectory | None = None,
    *,
    trace_id: str | None = None,
) -> dict:
    catalog = manifest.public()
    customer_id = _customer_id(question)
    if customer_id is None:
        return {
            "kind": "catalog",
            "catalog": catalog.model_dump(mode="json"),
            "answer": _catalog_answer(catalog),
        }
    if customer_id not in manifest.ids():
        return {
            "kind": "recusa",
            "answer": (
                f"O customer {customer_id} não está na carga desta sessão. "
                f"Os customers disponíveis são {', '.join(catalog_ids(catalog))}."
            ),
            "catalog": catalog.model_dump(mode="json"),
        }
    if agents is None:
        raise RuntimeError("a consulta de um customer precisa dos agentes")
    trace = trace_id or str(uuid.uuid4())
    day = manifest.business_date
    payments_task = _domain_task(trace, "lookup_customer_processing", customer_id, day, question)
    reconciliation_task = _domain_task(
        trace, "lookup_customer_reconciliation", customer_id, day, question
    )
    payments = agents.payments(payments_task)
    reconciliation = agents.reconciliation(reconciliation_task)
    anomaly_task = AnomalyTask(
        task_id=str(uuid.uuid4()),
        trace_id=trace,
        customer_id=customer_id,
        business_date=day,
        payments=payments,
        reconciliation=reconciliation,
    )
    anomaly = agents.anomaly(anomaly_task)
    parecer = _parecer(trace, customer_id, day, payments, reconciliation, anomaly)
    return {"kind": "parecer", "parecer": parecer.model_dump(mode="json")}


def catalog_ids(catalog: PublicCatalog) -> list[str]:
    return [item.customer_id for item in catalog.customers]


def _customer_id(question: str) -> str | None:
    found = CUSTOMER_RE.findall(question)
    if not found:
        return None
    return found[0]


def _catalog_answer(catalog: PublicCatalog) -> str:
    ids = ", ".join(catalog_ids(catalog))
    return (
        f"Nesta carga, na data {catalog.business_date.isoformat()}, "
        f"você pode consultar os customers {ids}."
    )


def _domain_task(trace_id: str, skill: str, customer_id: str, day: date, question: str) -> DomainTask:
    return DomainTask(
        task_id=str(uuid.uuid4()),
        trace_id=trace_id,
        skill=skill,
        input=DomainTaskInput(customer_id=customer_id, business_date=day, question=question),
    )


def _parecer(
    trace_id: str,
    customer_id: str,
    day: date,
    payments: SpecialistArtifact,
    reconciliation: SpecialistArtifact,
    anomaly: AnomalyArtifact,
) -> Parecer:
    payment_status = _status_of(payments.data, "payment")
    reconciliation_status = _status_of(reconciliation.data, "reconciliation")
    answer = _compose_answer(customer_id, payments, reconciliation, anomaly, payment_status, reconciliation_status)
    return Parecer(
        trace_id=trace_id,
        customer_id=customer_id,
        business_date=day,
        answer=answer,
        payments_status=payment_status,
        reconciliation_status=reconciliation_status,
        payments_outcome=anomaly.payments_outcome,
        reconciliation_outcome=anomaly.reconciliation_outcome,
        conclusion=anomaly.conclusion,
        anomaly=anomaly.conclusion == "divergence",
        codes=list(anomaly.codes),
        sources=[
            SourceRef(domain="payments", source="replica", as_of=payments.as_of),
            SourceRef(domain="reconciliation", source="replica", as_of=reconciliation.as_of),
        ],
    )


def _status_of(data: dict | None, key: str) -> str | None:
    if not data or not data.get("found"):
        return None
    record = data.get(key) or {}
    return record.get("status")


def _compose_answer(
    customer_id: str,
    payments: SpecialistArtifact,
    reconciliation: SpecialistArtifact,
    anomaly: AnomalyArtifact,
    payment_status: str | None,
    reconciliation_status: str | None,
) -> str:
    parts = [
        f"Customer {customer_id}.",
        payments.summary,
        reconciliation.summary,
    ]
    if payment_status:
        parts.append(f"Status de pagamento: {payment_status}.")
    if reconciliation_status:
        parts.append(f"Status de conciliação: {reconciliation_status}.")
    if anomaly.conclusion == "divergence":
        parts.append(f"Anomalia: {', '.join(anomaly.codes)}. {anomaly.explanation}")
    else:
        parts.append(anomaly.explanation)
    parts.append(REPLICA_WARNING)
    base = " ".join(part.strip() for part in parts if part)
    prose = _model_prose(base)
    text = prose or base
    if REPLICA_WARNING not in text:
        text = f"{text} {REPLICA_WARNING}"
    return text


def _model_prose(base: str) -> str | None:
    return complete(
        ORCHESTRATOR_MODEL,
        (
            "Reescreva o parecer de sustentação em português, curto, "
            "sem inventar status, valor ou customer. Mantenha os identificadores."
        ),
        base,
    )


def default_business_date() -> date:
    return BUSINESS_DATE
