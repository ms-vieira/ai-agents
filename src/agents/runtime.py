"""Cartão A2A servido pelo SDK e execução local da tarefa, com uma chamada autorizada."""

import os
from datetime import date

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
        return _stopped(agent_id, task, "refused", "Limite de chamadas atingido. A ferramenta não foi tentada de novo.")
    if result.decision == "downstream_failed":
        return _stopped(agent_id, task, "unavailable", "A consulta ficou indisponível. A ferramenta não foi concluída.")
    if result.decision not in {"allowed", "replayed"} or result.data is None:
        return _stopped(agent_id, task, "refused", result.detail or "A consulta foi recusada pelo gateway.")
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
    payments_outcome = _technical_outcome(task.payments, task.customer_id, task.business_date)
    reconciliation_outcome = _technical_outcome(task.reconciliation, task.customer_id, task.business_date)
    codes, conclusion = _conclusion(
        task.customer_id,
        task.business_date,
        payments_outcome,
        reconciliation_outcome,
        task.payments.data,
        task.reconciliation.data,
    )
    anomaly = conclusion == "divergence"
    explanation = _explanation(task.customer_id, codes, conclusion)
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
        payments_outcome=payments_outcome,
        reconciliation_outcome=reconciliation_outcome,
        conclusion=conclusion,
        anomaly=anomaly,
        codes=codes,
        explanation=rewritten or explanation,
    )


def _stopped(agent_id: str, task: DomainTask, status: str, summary: str) -> SpecialistArtifact:
    return SpecialistArtifact(
        task_id=task.task_id,
        trace_id=task.trace_id,
        agent_id=agent_id,
        status=status,
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


def _technical_outcome(artifact: SpecialistArtifact, customer_id: str, business_date: date) -> str:
    if artifact.status == "refused":
        return "refused"
    if artifact.status != "completed" or artifact.data is None:
        return "unavailable"
    if _identity_mismatch(artifact.data, customer_id, business_date):
        return "invalid"
    return "completed"


def _identity_mismatch(data: dict, customer_id: str, business_date: date) -> bool:
    if data.get("customer_id") != customer_id:
        return True
    return str(data.get("business_date")) != business_date.isoformat()


def _conclusion(
    customer_id: str,
    business_date: date,
    payments_outcome: str,
    reconciliation_outcome: str,
    payments: dict | None,
    reconciliation: dict | None,
) -> tuple[list[str], str]:
    if payments_outcome != "completed" or reconciliation_outcome != "completed":
        return [], "inconclusive"
    payments = payments or {}
    reconciliation = reconciliation or {}
    if _identity_mismatch(payments, customer_id, business_date) or _identity_mismatch(
        reconciliation, customer_id, business_date
    ):
        return [], "inconclusive"
    payment = payments.get("payment") or {}
    recon = reconciliation.get("reconciliation") or {}
    payment_found = bool(payments.get("found"))
    recon_found = bool(reconciliation.get("found"))
    if not payment_found and not recon_found:
        return [], "inconclusive"
    codes: list[str] = []
    if payment_found and not recon_found:
        codes.append("RECONCILIATION_MISSING")
    if recon_found and not payment_found:
        codes.append("PAYMENT_MISSING")
    if payment_found and recon_found:
        codes.extend(_record_codes(payment, recon))
    if codes:
        return codes, "divergence"
    if _aligned(payment, recon):
        return [], "no_divergence"
    return [], "inconclusive"


def _record_codes(payment: dict, recon: dict) -> list[str]:
    codes: list[str] = []
    payment_status = payment.get("status")
    recon_status = recon.get("status")
    payment_id = payment.get("payment_id")
    recon_payment_id = recon.get("payment_id")
    if payment_status == "SUCCESS" and recon_status == "ERROR":
        codes.append("PAYMENT_SUCCESS_RECONCILIATION_ERROR")
    if payment_id and recon_payment_id and payment_id != recon_payment_id:
        codes.append("PAYMENT_ID_MISMATCH")
    if _amounts_mismatch(payment, recon):
        codes.append("AMOUNT_MISMATCH")
    return codes


def _amounts_mismatch(payment: dict, recon: dict) -> bool:
    payment_amount = payment.get("amount_cents")
    expected = recon.get("expected_amount_cents")
    settled = recon.get("settled_amount_cents")
    pairs = (
        (payment_amount, expected),
        (payment_amount, settled),
        (expected, settled),
    )
    return any(left is not None and right is not None and left != right for left, right in pairs)


def _aligned(payment: dict, recon: dict) -> bool:
    payment_status = payment.get("status")
    recon_status = recon.get("status")
    payment_id = payment.get("payment_id")
    recon_payment_id = recon.get("payment_id")
    if not payment_id or payment_id != recon_payment_id:
        return False
    payment_amount = payment.get("amount_cents")
    expected = recon.get("expected_amount_cents")
    settled = recon.get("settled_amount_cents")
    if payment_status == "SUCCESS" and recon_status == "UPDATED":
        return (
            payment_amount is not None
            and expected is not None
            and settled is not None
            and payment_amount == expected == settled
        )
    if payment_status == "FAILED" and recon_status == "PENDING":
        if payment_amount is None or expected is None or payment_amount != expected:
            return False
        return settled is None or settled == expected
    return False


def _explanation(customer_id: str, codes: list[str], conclusion: str) -> str:
    if conclusion == "inconclusive":
        return (
            f"A consulta de {customer_id} está inconclusiva: "
            "a evidência foi recusada, está indisponível ou é inválida."
        )
    if conclusion == "no_divergence":
        return f"Os dois domínios não divergem para {customer_id}."
    return f"Há divergência para {customer_id}: {', '.join(codes)}."
