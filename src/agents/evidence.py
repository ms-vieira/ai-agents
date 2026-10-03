"""Validação dos envelopes de leitura e regras determinísticas do parecer."""

from datetime import date

from pydantic import ValidationError

from contracts.models import (
    AnomalyArtifact,
    AnomalyTask,
    DomainTask,
    PaymentReadResponse,
    PaymentRecord,
    ReconciliationReadResponse,
    ReconciliationRecord,
    SpecialistArtifact,
    SpecialistStatus,
)

_REQUIRED = ("source", "as_of", "found", "customer_id", "business_date")


def parse_payment(data: dict | None) -> PaymentReadResponse | None:
    return _parse(PaymentReadResponse, data)


def parse_reconciliation(data: dict | None) -> ReconciliationReadResponse | None:
    return _parse(ReconciliationReadResponse, data)


def parse_for_agent(agent_id: str, data: dict | None):
    if agent_id == "payments":
        return parse_payment(data)
    if agent_id == "reconciliation":
        return parse_reconciliation(data)
    return None


def technical_outcome(artifact: SpecialistArtifact, customer_id: str, business_date: date) -> str:
    if artifact.status == "refused":
        return "refused"
    if artifact.status == "unavailable":
        return "unavailable"
    if artifact.status == "invalid":
        return "invalid"
    if artifact.status != "completed" or not isinstance(artifact.data, dict):
        return "unavailable"
    parsed = parse_for_agent(artifact.agent_id, artifact.data)
    if parsed is None:
        return "invalid"
    if parsed.customer_id != customer_id or parsed.business_date != business_date:
        return "invalid"
    return "completed"


def conclusion(
    customer_id: str,
    business_date: date,
    payments_outcome: str,
    reconciliation_outcome: str,
    payments: dict | None,
    reconciliation: dict | None,
) -> tuple[list[str], str]:
    if payments_outcome != "completed" or reconciliation_outcome != "completed":
        return [], "inconclusive"
    payment = parse_payment(payments)
    recon = parse_reconciliation(reconciliation)
    if payment is None or recon is None:
        return [], "inconclusive"
    if not _same_task(payment, customer_id, business_date) or not _same_task(recon, customer_id, business_date):
        return [], "inconclusive"
    if not payment.found and not recon.found:
        return [], "inconclusive"
    codes: list[str] = []
    if payment.found and not recon.found:
        codes.append("RECONCILIATION_MISSING")
    if recon.found and not payment.found:
        codes.append("PAYMENT_MISSING")
    if payment.found and recon.found and payment.payment is not None and recon.reconciliation is not None:
        codes.extend(record_codes(payment.payment, recon.reconciliation))
    if codes:
        return codes, "divergence"
    if (
        payment.payment is not None
        and recon.reconciliation is not None
        and aligned(payment.payment, recon.reconciliation)
    ):
        return [], "no_divergence"
    return [], "inconclusive"


def record_codes(payment: PaymentRecord, recon: ReconciliationRecord) -> list[str]:
    codes: list[str] = []
    if payment.status == "SUCCESS" and recon.status == "ERROR":
        codes.append("PAYMENT_SUCCESS_RECONCILIATION_ERROR")
    if payment.payment_id and recon.payment_id and payment.payment_id != recon.payment_id:
        codes.append("PAYMENT_ID_MISMATCH")
    if amounts_mismatch(payment, recon):
        codes.append("AMOUNT_MISMATCH")
    return codes


def amounts_mismatch(payment: PaymentRecord, recon: ReconciliationRecord) -> bool:
    if payment.status == "FAILED" and recon.status == "PENDING":
        expected = recon.expected_amount_cents
        settled = recon.settled_amount_cents
        if expected is not None and payment.amount_cents != expected:
            return True
        if settled is not None and settled > 0 and settled != payment.amount_cents:
            return True
        if settled is not None and settled > 0 and expected is not None and settled != expected:
            return True
        return False
    pairs = (
        (payment.amount_cents, recon.expected_amount_cents),
        (payment.amount_cents, recon.settled_amount_cents),
        (recon.expected_amount_cents, recon.settled_amount_cents),
    )
    return any(left is not None and right is not None and left != right for left, right in pairs)


def aligned(payment: PaymentRecord, recon: ReconciliationRecord) -> bool:
    if not payment.payment_id or payment.payment_id != recon.payment_id:
        return False
    if payment.status == "SUCCESS" and recon.status == "UPDATED":
        return (
            recon.expected_amount_cents is not None
            and recon.settled_amount_cents is not None
            and payment.amount_cents == recon.expected_amount_cents == recon.settled_amount_cents
        )
    if payment.status == "FAILED" and recon.status == "PENDING":
        if recon.expected_amount_cents is None or payment.amount_cents != recon.expected_amount_cents:
            return False
        # settled_amount_cents is money actually settled. None is unknown, not zero.
        # Only a reported zero agrees with a failed payment.
        return recon.settled_amount_cents == 0
    return False


def explanation(
    customer_id: str,
    codes: list[str],
    business_conclusion: str,
    payments_outcome: str,
    reconciliation_outcome: str,
) -> str:
    if business_conclusion == "inconclusive":
        if payments_outcome != "completed" or reconciliation_outcome != "completed":
            return (
                f"A consulta de {customer_id} está inconclusiva: "
                "a evidência foi recusada, está indisponível ou é inválida."
            )
        return (
            f"A consulta de {customer_id} está inconclusiva: "
            "os registros não bastam para concluir se há divergência."
        )
    if business_conclusion == "no_divergence":
        return f"Os dois domínios não divergem para {customer_id}."
    return f"Há divergência para {customer_id}: {', '.join(codes)}."


def domain_summary(agent_id: str, parsed: PaymentReadResponse | ReconciliationReadResponse) -> str:
    as_of = parsed.as_of.isoformat()
    if isinstance(parsed, PaymentReadResponse):
        if not parsed.found or parsed.payment is None:
            return f"A réplica de pagamentos não tem processamento para este customer (as_of {as_of})."
        payment = parsed.payment
        return (
            f"Pagamento {payment.payment_id} com status {payment.status} "
            f"na réplica (as_of {as_of})."
        )
    if not parsed.found or parsed.reconciliation is None:
        return f"A réplica de conciliação não tem registro para este customer (as_of {as_of})."
    recon = parsed.reconciliation
    return (
        f"Conciliação {recon.reconciliation_id} com status {recon.status} "
        f"na réplica (as_of {as_of})."
    )


def stopped_artifact(agent_id: str, task: DomainTask, status: SpecialistStatus, summary: str) -> SpecialistArtifact:
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


def inconclusive_anomaly(task: AnomalyTask, text: str) -> AnomalyArtifact:
    payments_outcome = technical_outcome(task.payments, task.customer_id, task.business_date)
    reconciliation_outcome = technical_outcome(task.reconciliation, task.customer_id, task.business_date)
    return AnomalyArtifact(
        task_id=task.task_id,
        trace_id=task.trace_id,
        customer_id=task.customer_id,
        business_date=task.business_date,
        payments_outcome=payments_outcome,
        reconciliation_outcome=reconciliation_outcome,
        conclusion="inconclusive",
        anomaly=False,
        codes=[],
        explanation=text,
    )


def consultation_line(label: str, outcome: str, artifact: SpecialistArtifact) -> str:
    if outcome == "completed":
        return artifact.summary
    if outcome == "refused":
        return f"A consulta de {label} foi recusada."
    if outcome == "unavailable":
        return f"A consulta de {label} está indisponível."
    return f"A consulta de {label} não produziu evidência utilizável."


def shareable_artifact(
    artifact: SpecialistArtifact,
    customer_id: str,
    business_date: date,
) -> SpecialistArtifact:
    """Drop payload, status, and summary unless the envelope matches the task."""
    outcome = technical_outcome(artifact, customer_id, business_date)
    if outcome == "completed":
        return artifact
    status: SpecialistStatus = outcome if outcome in {"refused", "unavailable", "invalid"} else "invalid"
    label = "pagamentos" if artifact.agent_id == "payments" else "conciliação"
    return artifact.model_copy(
        update={
            "status": status,
            "source": None,
            "as_of": None,
            "summary": consultation_line(label, status, artifact),
            "data": None,
        }
    )


def _parse(model, data: dict | None):
    if not isinstance(data, dict) or any(key not in data for key in _REQUIRED):
        return None
    record_key = "payment" if model is PaymentReadResponse else "reconciliation"
    if not _financial_types(data, record_key):
        return None
    try:
        return model.model_validate(data)
    except ValidationError:
        return None


def _financial_types(data: dict, record_key: str) -> bool:
    if type(data.get("found")) is not bool:
        return False
    record = data.get(record_key)
    if record is None:
        return True
    if not isinstance(record, dict):
        return False
    for key in ("amount_cents", "expected_amount_cents", "settled_amount_cents"):
        if key not in record or record[key] is None:
            continue
        if type(record[key]) is not int:
            return False
    return True


def _same_task(parsed, customer_id: str, business_date: date) -> bool:
    return parsed.customer_id == customer_id and parsed.business_date == business_date
