"""Cartão A2A servido pelo SDK e execução local da tarefa, com uma chamada autorizada."""

import os

import httpx
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from fastapi import FastAPI

from contracts.models import (
    AnomalyArtifact,
    AnomalyTask,
    DomainTask,
    GatewayCall,
    GatewayResult,
    SpecialistArtifact,
)
from model_client import complete

SPECIALIST_MODEL = os.environ.get("SPECIALIST_MODEL", "gpt-4.1-mini")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300")


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
        self._http = httpx.Client(
            base_url=base_url,
            timeout=15,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
        )

    def call(self, body: GatewayCall) -> tuple[int, GatewayResult]:
        response = self._http.post("/v1/tools/call", json=body.model_dump(mode="json"))
        if response.status_code == 200:
            return 200, GatewayResult.model_validate(response.json())
        detail = response.json().get("detail", response.json())
        if isinstance(detail, dict) and "decision" in detail:
            return response.status_code, GatewayResult.model_validate(detail)
        return response.status_code, GatewayResult(
            decision="denied_allowlist",
            detail=str(detail),
        )

    def close(self) -> None:
        self._http.close()


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
    _status, result = gateway.call(call)
    if result.decision == "rate_limited":
        return _refused(agent_id, task, "Limite de chamadas atingido. A ferramenta não foi tentada de novo.")
    if result.decision not in {"allowed", "replayed"} or result.data is None:
        return _refused(agent_id, task, result.detail or "A consulta foi recusada pelo gateway.")
    template = _domain_summary(agent_id, result.data)
    summary = complete(
        SPECIALIST_MODEL,
        "Resuma em uma frase, em português, somente o que está no texto. Não invente status nem customer.",
        template,
    )
    return SpecialistArtifact(
        task_id=task.task_id,
        trace_id=task.trace_id,
        agent_id=agent_id,
        status="completed",
        source="replica",
        as_of=result.data.get("as_of"),
        customer_id=task.input.customer_id,
        business_date=task.input.business_date,
        summary=summary or template,
        data=result.data,
    )


def run_anomaly_task(task: AnomalyTask) -> AnomalyArtifact:
    codes, anomaly = _codes(task.payments.data, task.reconciliation.data)
    explanation = _explanation(task.customer_id, codes, anomaly)
    rewritten = complete(
        SPECIALIST_MODEL,
        "Explique a divergência em português, curto, sem inventar status, valor ou customer.",
        explanation,
    )
    return AnomalyArtifact(
        task_id=task.task_id,
        trace_id=task.trace_id,
        customer_id=task.customer_id,
        business_date=task.business_date,
        anomaly=anomaly,
        codes=codes,
        explanation=rewritten or explanation,
    )


def _refused(agent_id: str, task: DomainTask, summary: str) -> SpecialistArtifact:
    return SpecialistArtifact(
        task_id=task.task_id,
        trace_id=task.trace_id,
        agent_id=agent_id,
        status="refused",
        customer_id=task.input.customer_id,
        business_date=task.input.business_date,
        summary=summary,
        data=None,
    )


def _domain_summary(agent_id: str, data: dict) -> str:
    as_of = data.get("as_of")
    if agent_id == "payments":
        payment = data.get("payment") or {}
        if not data.get("found"):
            return f"A réplica de pagamentos não tem processamento para este customer (as_of {as_of})."
        return (
            f"Pagamento {payment.get('payment_id')} com status {payment.get('status')} "
            f"na réplica (as_of {as_of})."
        )
    reconciliation = data.get("reconciliation") or {}
    if not data.get("found"):
        return f"A réplica de conciliação não tem registro para este customer (as_of {as_of})."
    return (
        f"Conciliação {reconciliation.get('reconciliation_id')} com status {reconciliation.get('status')} "
        f"na réplica (as_of {as_of})."
    )


def _codes(payments: dict | None, reconciliation: dict | None) -> tuple[list[str], bool]:
    payments = payments or {}
    reconciliation = reconciliation or {}
    payment = payments.get("payment") or {}
    recon = reconciliation.get("reconciliation") or {}
    codes: list[str] = []
    payment_status = payment.get("status")
    recon_status = recon.get("status")
    if payments.get("found") and reconciliation.get("found") and payment_status == "SUCCESS" and recon_status == "ERROR":
        codes.append("PAYMENT_SUCCESS_RECONCILIATION_ERROR")
    anomaly_code = recon.get("anomaly_code")
    if anomaly_code and anomaly_code not in codes:
        codes.append(anomaly_code)
    if payments.get("found") and not reconciliation.get("found"):
        codes.append("RECONCILIATION_MISSING")
    if reconciliation.get("found") and not payments.get("found"):
        codes.append("PAYMENT_MISSING")
    coherent_failure = payment_status == "FAILED" and recon_status == "PENDING"
    anomaly = bool(codes) and not coherent_failure
    if coherent_failure:
        return [], False
    return codes, anomaly


def _explanation(customer_id: str, codes: list[str], anomaly: bool) -> str:
    if not anomaly:
        return f"Os dois domínios não divergem para {customer_id}."
    return f"Há divergência para {customer_id}: {', '.join(codes)}."
