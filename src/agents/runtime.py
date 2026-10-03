"""Cartão A2A servido pelo SDK e execução local da tarefa, com uma chamada autorizada."""

import os

import httpx
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from fastapi import FastAPI
from pydantic import ValidationError

from agents.evidence import (
    conclusion,
    domain_summary,
    explanation,
    parse_for_agent,
    stopped_artifact,
    technical_outcome,
)
from contracts.models import (
    AnomalyArtifact,
    AnomalyTask,
    DomainTask,
    GatewayCall,
    GatewayResult,
    SpecialistArtifact,
)
from model_client import complete
from settings import GATEWAY_CALL_TIMEOUT_SECONDS, tool_and_model_budget

SPECIALIST_MODEL = os.environ.get("SPECIALIST_MODEL", "gpt-4.1-mini")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300")
INVALID_SUMMARY = "A consulta não produziu evidência utilizável."


def build_card(name: str, description: str, url: str, skill_id: str, skill_name: str, skill_description: str) -> AgentCard:
    card = AgentCard(
        name=name,
        description=description,
        version="0.1.0",
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
    )
    card.supported_interfaces.append(
        AgentInterface(url=url, protocol_binding="HTTP+JSON", protocol_version="1.0")
    )
    card.capabilities.CopyFrom(AgentCapabilities(streaming=False))
    skill = AgentSkill(
        id=skill_id,
        name=skill_name,
        description=skill_description,
        tags=["support"],
        input_modes=["application/json"],
        output_modes=["application/json"],
    )
    card.skills.append(skill)
    return card


def mount_cards(app: FastAPI, card: AgentCard) -> None:
    routes = create_agent_card_routes(card, card_url="/.well-known/agent-card.json")
    routes += create_agent_card_routes(card, card_url="/.well-known/agent.json")
    app.router.routes.extend(routes)


class GatewayClient:
    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self._timeout = GATEWAY_CALL_TIMEOUT_SECONDS
        self._http = httpx.Client(
            base_url=base_url,
            timeout=GATEWAY_CALL_TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
        )

    def call(self, body: GatewayCall) -> tuple[int, GatewayResult]:
        try:
            response = self._http.post(
                "/v1/tools/call",
                json=body.model_dump(mode="json"),
                timeout=self._timeout,
            )
        except httpx.TimeoutException:
            return 503, GatewayResult(decision="downstream_failed", detail="a ferramenta não respondeu")
        except httpx.HTTPError:
            return 503, GatewayResult(decision="downstream_failed", detail="a ferramenta não respondeu")
        return _classify_gateway_response(response)

    def close(self) -> None:
        self._http.close()


def _classify_gateway_response(response: httpx.Response) -> tuple[int, GatewayResult]:
    if response.status_code in (401, 403):
        return _decision_or_default(response, "denied_authentication")
    if response.status_code == 429:
        return _decision_or_default(response, "rate_limited")
    if response.status_code >= 500:
        return _decision_or_default(response, "downstream_failed", "a ferramenta não respondeu")
    try:
        payload = response.json()
    except ValueError:
        return response.status_code, GatewayResult(decision="invalid_payload")
    if response.status_code == 200:
        try:
            return 200, GatewayResult.model_validate(payload)
        except ValidationError:
            return response.status_code, GatewayResult(decision="invalid_payload")
    detail = payload.get("detail", payload) if isinstance(payload, dict) else None
    if isinstance(detail, dict) and "decision" in detail:
        try:
            return response.status_code, GatewayResult.model_validate(detail)
        except ValidationError:
            return response.status_code, GatewayResult(decision="invalid_payload")
    return response.status_code, GatewayResult(decision="invalid_payload")


def _decision_or_default(
    response: httpx.Response,
    decision: str,
    detail: str | None = None,
) -> tuple[int, GatewayResult]:
    try:
        payload = response.json()
    except ValueError:
        return response.status_code, GatewayResult(decision=decision, detail=detail)
    body = payload.get("detail", payload) if isinstance(payload, dict) else None
    if isinstance(body, dict) and "decision" in body:
        try:
            return response.status_code, GatewayResult.model_validate(body)
        except ValidationError:
            return response.status_code, GatewayResult(decision="invalid_payload")
    return response.status_code, GatewayResult(decision=decision, detail=detail)


def run_domain_task(
    agent_id: str,
    tool_name: str,
    task: DomainTask,
    gateway: GatewayClient,
) -> SpecialistArtifact:
    """Uma chamada, com o customer da tarefa. 429 encerra a ferramenta sem nova tentativa."""
    call = GatewayCall(
        trace_id=task.trace_id,
        task_id=task.task_id,
        task_customer_id=task.input.customer_id,
        tool=tool_name,
        arguments={
            "customer_id": task.input.customer_id,
            "business_date": task.input.business_date.isoformat(),
        },
        model=SPECIALIST_MODEL,
    )
    gateway_timeout, model_limit = tool_and_model_budget(task.timeout_seconds, includes_gateway=True)
    if gateway_timeout <= 0:
        return stopped_artifact(
            agent_id, task, "unavailable", "A consulta não coube no prazo da investigação."
        )
    if isinstance(gateway, GatewayClient):
        gateway._timeout = gateway_timeout
    _status, result = gateway.call(call)
    if result.decision == "rate_limited":
        return stopped_artifact(
            agent_id, task, "refused", "Limite de chamadas atingido. A ferramenta não foi tentada de novo."
        )
    if result.decision == "downstream_failed":
        return stopped_artifact(
            agent_id, task, "unavailable", "A consulta ficou indisponível. A ferramenta não foi concluída."
        )
    if result.decision == "invalid_payload":
        return stopped_artifact(agent_id, task, "invalid", INVALID_SUMMARY)
    if result.decision not in {"allowed", "replayed"} or result.data is None:
        return stopped_artifact(agent_id, task, "refused", "A consulta foi recusada pelo gateway.")
    parsed = parse_for_agent(agent_id, result.data)
    if (
        parsed is None
        or parsed.customer_id != task.input.customer_id
        or parsed.business_date != task.input.business_date
    ):
        return stopped_artifact(agent_id, task, "invalid", INVALID_SUMMARY)
    template = domain_summary(agent_id, parsed)
    summary = template if model_limit <= 0 else complete(
        SPECIALIST_MODEL,
        "Resuma em uma frase, em português, somente o que está no texto. Não invente status nem customer.",
        template,
        timeout=model_limit,
    )
    return SpecialistArtifact(
        task_id=task.task_id,
        trace_id=task.trace_id,
        agent_id=agent_id,
        status="completed",
        source="replica",
        as_of=parsed.as_of,
        customer_id=task.input.customer_id,
        business_date=task.input.business_date,
        summary=summary or template,
        data=parsed.model_dump(mode="json"),
    )


def run_anomaly_task(task: AnomalyTask) -> AnomalyArtifact:
    payments_outcome = technical_outcome(task.payments, task.customer_id, task.business_date)
    reconciliation_outcome = technical_outcome(task.reconciliation, task.customer_id, task.business_date)
    codes, business_conclusion = conclusion(
        task.customer_id,
        task.business_date,
        payments_outcome,
        reconciliation_outcome,
        task.payments.data if payments_outcome == "completed" else None,
        task.reconciliation.data if reconciliation_outcome == "completed" else None,
    )
    anomaly = business_conclusion == "divergence"
    text = explanation(
        task.customer_id,
        codes,
        business_conclusion,
        payments_outcome,
        reconciliation_outcome,
    )
    _gateway_timeout, model_limit = tool_and_model_budget(task.timeout_seconds, includes_gateway=False)
    rewritten = None if model_limit <= 0 else complete(
        SPECIALIST_MODEL,
        "Explique a divergência em português, curto, sem inventar status, valor ou customer.",
        text,
        timeout=model_limit,
    )
    return AnomalyArtifact(
        task_id=task.task_id,
        trace_id=task.trace_id,
        customer_id=task.customer_id,
        business_date=task.business_date,
        payments_outcome=payments_outcome,
        reconciliation_outcome=reconciliation_outcome,
        conclusion=business_conclusion,
        anomaly=anomaly,
        codes=codes,
        explanation=rewritten or text,
    )
