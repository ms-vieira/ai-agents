"""O orquestrador escolhe o fluxo. O customer id sai da pergunta, não do texto da réplica."""

import inspect
import os
import re
import time
import uuid
from datetime import date

import httpx
from pydantic import ValidationError

from agents.evidence import (
    accept_anomaly_artifact,
    consultation_line,
    inconclusive_anomaly,
    shareable_artifact,
    stopped_artifact,
    technical_outcome,
)
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
from model_client import complete
from settings import (
    AGENT_CALL_TIMEOUT_SECONDS,
    BUSINESS_DATE,
    GATEWAY_CALL_TIMEOUT_SECONDS,
    INVESTIGATION_DEADLINE_SECONDS,
    MODEL_TIMEOUT_SECONDS,
    PARECER_RESERVE_SECONDS,
)

CUSTOMER_RE = re.compile(r"\bC-\d{4}\b")
ORCHESTRATOR_MODEL = os.environ.get("ORCHESTRATOR_MODEL", "gpt-4.1")
REPLICA_WARNING = "A leitura vem da réplica e pode estar atrás do processamento transacional."
RETURN_MARGIN_SECONDS = 0.25
MIN_OPERATION_SECONDS = 0.5


def _call_floor() -> float:
    return min(AGENT_CALL_TIMEOUT_SECONDS, GATEWAY_CALL_TIMEOUT_SECONDS + 0.5)


class Deadline:
    def __init__(self, seconds: float, reserve: float, clock) -> None:
        self._clock = clock
        self._end = clock() + seconds
        self._reserve = reserve

    def now(self) -> float:
        return self._clock()

    def remaining(self) -> float:
        return self._end - self.now() - self._reserve

    def budget(self) -> float | None:
        available = self.remaining()
        if available < _call_floor():
            return None
        return available


class _CallProblem(Exception):
    def __init__(self, status: str, summary: str) -> None:
        self.status = status
        self.summary = summary


class AgentDirectory:
    def payments(self, task: DomainTask) -> SpecialistArtifact:
        raise NotImplementedError

    def reconciliation(self, task: DomainTask) -> SpecialistArtifact:
        raise NotImplementedError

    def anomaly(self, task: AnomalyTask) -> AnomalyArtifact:
        raise NotImplementedError


class HttpAgentDirectory(AgentDirectory):
    def __init__(
        self,
        payments_url: str,
        reconciliation_url: str,
        anomaly_url: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float | None = None,
    ) -> None:
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
        self._default_timeout = AGENT_CALL_TIMEOUT_SECONDS if timeout is None else timeout
        self._http = httpx.Client(timeout=self._default_timeout, transport=transport)

    def payments(self, task: DomainTask, deadline: Deadline | None = None) -> SpecialistArtifact:
        return self._domain("payments", task, deadline)

    def reconciliation(self, task: DomainTask, deadline: Deadline | None = None) -> SpecialistArtifact:
        return self._domain("reconciliation", task, deadline)

    def anomaly(self, task: AnomalyTask, deadline: Deadline | None = None) -> AnomalyArtifact:
        try:
            payload = self._send("anomaly", task.model_dump(mode="json"), deadline)
        except _CallProblem as problem:
            return inconclusive_anomaly(task, problem.summary)
        try:
            parsed = AnomalyArtifact.model_validate(payload)
        except ValidationError:
            return inconclusive_anomaly(
                task,
                "A análise de anomalia devolveu uma resposta inválida. A conclusão fica inconclusiva.",
            )
        return accept_anomaly_artifact(parsed, task)

    def _domain(self, name: str, task: DomainTask, deadline: Deadline | None) -> SpecialistArtifact:
        try:
            payload = self._send(name, task.model_dump(mode="json"), deadline)
        except _CallProblem as problem:
            return stopped_artifact(name, task, problem.status, problem.summary)
        try:
            return SpecialistArtifact.model_validate(payload)
        except ValidationError:
            return stopped_artifact(name, task, "invalid", "A consulta não produziu evidência utilizável.")

    def _send(self, name: str, payload: dict, deadline: Deadline | None) -> dict:
        call_end = self._call_end(deadline)
        if call_end is None:
            raise _CallProblem("unavailable", "A consulta não coube no prazo da investigação.")
        timeout = self._operation_timeout(call_end, deadline)
        if timeout is None:
            raise _CallProblem("unavailable", "A consulta não coube no prazo da investigação.")
        base = self._urls[name]
        try:
            card = self._http.get(f"{base}/.well-known/agent-card.json", timeout=timeout)
        except httpx.TimeoutException as exc:
            raise _CallProblem("unavailable", "A consulta excedeu o prazo.") from exc
        except httpx.HTTPError as exc:
            raise _CallProblem("unavailable", "A consulta está indisponível.") from exc
        _raise_for_status(card, "O cartão do agente")
        try:
            body = card.json()
        except ValueError as exc:
            raise _CallProblem("invalid", "O cartão do agente não é um JSON válido.") from exc
        skills = body.get("skills", []) if isinstance(body, dict) else []
        skill_ids = [skill.get("id") for skill in skills if isinstance(skill, dict)]
        if self._skills[name] not in skill_ids:
            raise _CallProblem("invalid", "O cartão do agente não publica a skill esperada.")
        timeout = self._operation_timeout(call_end, deadline)
        if timeout is None:
            raise _CallProblem("unavailable", "A consulta não coube no prazo da investigação.")
        specialist_budget = timeout - RETURN_MARGIN_SECONDS
        if specialist_budget <= 0:
            raise _CallProblem("unavailable", "A consulta não coube no prazo da investigação.")
        request_body = dict(payload)
        request_body["timeout_seconds"] = specialist_budget
        try:
            response = self._http.post(f"{base}/v1/tasks", json=request_body, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise _CallProblem("unavailable", "A consulta excedeu o prazo.") from exc
        except httpx.HTTPError as exc:
            raise _CallProblem("unavailable", "A consulta está indisponível.") from exc
        _raise_for_status(response, "A consulta")
        try:
            parsed = response.json()
        except ValueError as exc:
            raise _CallProblem("invalid", "A consulta não produziu evidência utilizável.") from exc
        if not isinstance(parsed, dict):
            raise _CallProblem("invalid", "A consulta não produziu evidência utilizável.")
        return parsed

    def _call_end(self, deadline: Deadline | None) -> float | None:
        if deadline is None:
            return time.monotonic() + self._default_timeout
        available = deadline.remaining()
        if available < _call_floor():
            return None
        return deadline.now() + min(self._default_timeout, available)

    def _operation_timeout(self, call_end: float, deadline: Deadline | None) -> float | None:
        if deadline is None:
            remaining = call_end - time.monotonic()
        else:
            remaining = min(call_end - deadline.now(), deadline.remaining())
        if remaining < MIN_OPERATION_SECONDS:
            return None
        return remaining

    def close(self) -> None:
        self._http.close()


def answer_question(
    question: str,
    manifest: SessionManifest,
    agents: AgentDirectory | None = None,
    *,
    trace_id: str | None = None,
    clock=time.monotonic,
    deadline_seconds: float | None = None,
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
    deadline = Deadline(
        INVESTIGATION_DEADLINE_SECONDS if deadline_seconds is None else deadline_seconds,
        PARECER_RESERVE_SECONDS,
        clock,
    )
    payments_task = _domain_task(trace, "lookup_customer_processing", customer_id, day, question)
    reconciliation_task = _domain_task(
        trace, "lookup_customer_reconciliation", customer_id, day, question
    )
    payments = shareable_artifact(
        _invoke_domain(agents.payments, payments_task, deadline, "payments"),
        payments_task,
        "payments",
    )
    reconciliation = shareable_artifact(
        _invoke_domain(agents.reconciliation, reconciliation_task, deadline, "reconciliation"),
        reconciliation_task,
        "reconciliation",
    )
    anomaly_task = AnomalyTask(
        task_id=str(uuid.uuid4()),
        trace_id=trace,
        customer_id=customer_id,
        business_date=day,
        payments=payments,
        reconciliation=reconciliation,
    )
    anomaly = _invoke_anomaly(agents.anomaly, anomaly_task, deadline)
    parecer = _parecer(trace, customer_id, day, payments, reconciliation, anomaly, deadline)
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


def _invoke_domain(method, task: DomainTask, deadline: Deadline, agent_id: str) -> SpecialistArtifact:
    budget = deadline.budget()
    if budget is None:
        return stopped_artifact(agent_id, task, "unavailable", "A consulta não coube no prazo da investigação.")
    task = task.model_copy(update={"timeout_seconds": min(budget, AGENT_CALL_TIMEOUT_SECONDS)})
    try:
        return _call_agent(method, task, deadline)
    except _CallProblem as problem:
        return stopped_artifact(agent_id, task, problem.status, problem.summary)
    except httpx.TimeoutException:
        return stopped_artifact(agent_id, task, "unavailable", "A consulta excedeu o prazo.")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            return stopped_artifact(agent_id, task, "refused", "A consulta foi recusada.")
        return stopped_artifact(agent_id, task, "unavailable", "A consulta está indisponível.")
    except httpx.HTTPError:
        return stopped_artifact(agent_id, task, "unavailable", "A consulta está indisponível.")
    except ValidationError:
        return stopped_artifact(agent_id, task, "invalid", "A consulta não produziu evidência utilizável.")
    except (ValueError, TypeError):
        return stopped_artifact(agent_id, task, "invalid", "A consulta não produziu evidência utilizável.")


def _invoke_anomaly(method, task: AnomalyTask, deadline: Deadline) -> AnomalyArtifact:
    budget = deadline.budget()
    if budget is None:
        return inconclusive_anomaly(
            task,
            "A análise de anomalia não coube no prazo. A conclusão fica inconclusiva.",
        )
    task = task.model_copy(update={"timeout_seconds": min(budget, AGENT_CALL_TIMEOUT_SECONDS)})
    try:
        artifact = _call_agent(method, task, deadline)
    except _CallProblem as problem:
        return inconclusive_anomaly(task, problem.summary)
    except httpx.TimeoutException:
        return inconclusive_anomaly(task, "A análise de anomalia está indisponível. A conclusão fica inconclusiva.")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            return inconclusive_anomaly(task, "A análise de anomalia foi recusada. A conclusão fica inconclusiva.")
        return inconclusive_anomaly(task, "A análise de anomalia está indisponível. A conclusão fica inconclusiva.")
    except httpx.HTTPError:
        return inconclusive_anomaly(task, "A análise de anomalia está indisponível. A conclusão fica inconclusiva.")
    except ValidationError:
        return inconclusive_anomaly(
            task,
            "A análise de anomalia devolveu uma resposta inválida. A conclusão fica inconclusiva.",
        )
    except (ValueError, TypeError):
        return inconclusive_anomaly(
            task,
            "A análise de anomalia devolveu uma resposta inválida. A conclusão fica inconclusiva.",
        )
    return accept_anomaly_artifact(artifact, task)


def _call_agent(method, task, deadline: Deadline):
    if _accepts_deadline(method):
        return method(task, deadline)
    return method(task)


def _accepts_deadline(method) -> bool:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    if "deadline" in signature.parameters:
        return True
    return any(
        parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
        for parameter in signature.parameters.values()
    )


def _parecer(
    trace_id: str,
    customer_id: str,
    day: date,
    payments: SpecialistArtifact,
    reconciliation: SpecialistArtifact,
    anomaly: AnomalyArtifact,
    deadline: Deadline,
) -> Parecer:
    payments_outcome = technical_outcome(payments, customer_id, day)
    reconciliation_outcome = technical_outcome(reconciliation, customer_id, day)
    if payments_outcome != "completed" or reconciliation_outcome != "completed":
        conclusion = "inconclusive"
        codes: list[str] = []
        explanation = (
            f"A consulta de {customer_id} está inconclusiva: "
            "a evidência foi recusada, está indisponível ou é inválida."
        )
    else:
        conclusion = anomaly.conclusion
        codes = list(anomaly.codes)
        explanation = anomaly.explanation
    payment_status = _status_of(payments, payments_outcome, "payment")
    reconciliation_status = _status_of(reconciliation, reconciliation_outcome, "reconciliation")
    answer = _compose_answer(
        customer_id,
        payments,
        reconciliation,
        payments_outcome,
        reconciliation_outcome,
        conclusion,
        codes,
        explanation,
        payment_status,
        reconciliation_status,
        deadline,
    )
    sources: list[SourceRef] = []
    if payments_outcome == "completed":
        sources.append(SourceRef(domain="payments", source="replica", as_of=payments.as_of))
    if reconciliation_outcome == "completed":
        sources.append(SourceRef(domain="reconciliation", source="replica", as_of=reconciliation.as_of))
    return Parecer(
        trace_id=trace_id,
        customer_id=customer_id,
        business_date=day,
        answer=answer,
        payments_status=payment_status,
        reconciliation_status=reconciliation_status,
        payments_outcome=payments_outcome,
        reconciliation_outcome=reconciliation_outcome,
        conclusion=conclusion,
        anomaly=conclusion == "divergence",
        codes=codes,
        sources=sources,
    )


def _status_of(artifact: SpecialistArtifact, outcome: str, key: str) -> str | None:
    if outcome != "completed" or not isinstance(artifact.data, dict):
        return None
    if not artifact.data.get("found"):
        return None
    record = artifact.data.get(key) or {}
    status = record.get("status")
    return status if isinstance(status, str) else None


def _compose_answer(
    customer_id: str,
    payments: SpecialistArtifact,
    reconciliation: SpecialistArtifact,
    payments_outcome: str,
    reconciliation_outcome: str,
    conclusion: str,
    codes: list[str],
    explanation: str,
    payment_status: str | None,
    reconciliation_status: str | None,
    deadline: Deadline,
) -> str:
    parts = [
        f"Customer {customer_id}.",
        consultation_line("pagamentos", payments_outcome, payments),
        consultation_line("conciliação", reconciliation_outcome, reconciliation),
    ]
    if payment_status:
        parts.append(f"Status de pagamento: {payment_status}.")
    if reconciliation_status:
        parts.append(f"Status de conciliação: {reconciliation_status}.")
    if conclusion == "divergence":
        parts.append(f"Anomalia: {', '.join(codes)}. {explanation}")
    else:
        parts.append(explanation)
    parts.append(REPLICA_WARNING)
    base = " ".join(part.strip() for part in parts if part)
    prose = _model_prose(base, deadline)
    text = prose or base
    if REPLICA_WARNING not in text:
        text = f"{text} {REPLICA_WARNING}"
    return text


def _model_prose(base: str, deadline: Deadline) -> str | None:
    budget = deadline.budget()
    if budget is None:
        return None
    return complete(
        ORCHESTRATOR_MODEL,
        (
            "Reescreva o parecer de sustentação em português, curto, "
            "sem inventar status, valor ou customer. Mantenha os identificadores."
        ),
        base,
        timeout=min(MODEL_TIMEOUT_SECONDS, budget),
    )


def _raise_for_status(response: httpx.Response, label: str) -> None:
    if response.status_code in (401, 403):
        raise _CallProblem("refused", f"{label} foi recusado.")
    if response.status_code >= 400:
        raise _CallProblem("unavailable", f"{label} está indisponível.")


def default_business_date() -> date:
    return BUSINESS_DATE
