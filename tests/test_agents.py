from datetime import date

import httpx

from contracts.models import AnomalyTask, DomainTask, DomainTaskInput, GatewayCall, GatewayResult, SpecialistArtifact
from agents.runtime import run_anomaly_task, run_domain_task


class CountingGateway:
    def __init__(self, decision: str) -> None:
        self.calls = 0
        self.decision = decision

    def call(self, body: GatewayCall):
        self.calls += 1
        if self.decision == "rate_limited":
            return 429, GatewayResult(decision="rate_limited", detail="limite")
        return 200, GatewayResult(
            decision="allowed",
            data={
                "source": "replica",
                "as_of": "2026-09-30T21:00:00Z",
                "found": True,
                "customer_id": body.task_customer_id,
                "payment": {"payment_id": "pay_1", "status": "SUCCESS"},
            },
        )


def _task() -> DomainTask:
    return DomainTask(
        task_id="task-1",
        trace_id="trace-1",
        skill="lookup_customer_processing",
        input=DomainTaskInput(
            customer_id="C-4821",
            business_date=date(2026, 9, 30),
            question="foi processado?",
        ),
    )


def test_429_nao_gera_segunda_tentativa() -> None:
    gateway = CountingGateway("rate_limited")
    artifact = run_domain_task("payments", "get_processing", _task(), gateway)
    assert gateway.calls == 1
    assert artifact.status == "refused"


def test_chamada_usa_o_customer_da_tarefa() -> None:
    gateway = CountingGateway("allowed")
    artifact = run_domain_task("payments", "get_processing", _task(), gateway)
    assert artifact.status == "completed"
    assert artifact.customer_id == "C-4821"
    assert artifact.data["customer_id"] == "C-4821"
    assert artifact.source == "replica"


def test_anomalia_sem_ferramenta_aponta_divergencia() -> None:
    task = AnomalyTask(
        task_id="a",
        trace_id="t",
        customer_id="C-4821",
        business_date=date(2026, 9, 30),
        payments=_artifact(
            "payments",
            _payment_data(status="SUCCESS", amount_cents=150_000),
        ),
        reconciliation=_artifact(
            "reconciliation",
            _reconciliation_data(
                status="ERROR",
                expected_amount_cents=150_000,
                settled_amount_cents=149_999,
                anomaly_code="STORED_CODE_IGNORED",
            ),
        ),
    )
    result = run_anomaly_task(task)
    assert result.conclusion == "divergence"
    assert result.anomaly is True
    assert result.codes == ["PAYMENT_SUCCESS_RECONCILIATION_ERROR", "AMOUNT_MISMATCH"]
    assert "STORED_CODE_IGNORED" not in result.codes


def test_consulta_recusada_nao_conclui_sem_divergencia() -> None:
    refused = _artifact("payments", None, status="refused")
    task = AnomalyTask(
        task_id="a",
        trace_id="t",
        customer_id="C-4821",
        business_date=date(2026, 9, 30),
        payments=refused,
        reconciliation=_artifact("reconciliation", _reconciliation_data(status="UPDATED")),
    )
    result = run_anomaly_task(task)
    assert result.payments_outcome == "refused"
    assert result.conclusion == "inconclusive"
    assert result.anomaly is False
    assert result.codes == []
    assert "não divergem" not in result.explanation


def test_evidencia_de_outro_cliente_e_invalida() -> None:
    data = _payment_data(status="SUCCESS", amount_cents=100_000)
    data["customer_id"] = "C-9999"
    task = AnomalyTask(
        task_id="a",
        trace_id="t",
        customer_id="C-4821",
        business_date=date(2026, 9, 30),
        payments=_artifact("payments", data),
        reconciliation=_artifact("reconciliation", _reconciliation_data(status="UPDATED")),
    )
    result = run_anomaly_task(task)
    assert result.payments_outcome == "invalid"
    assert result.conclusion == "inconclusive"
    assert "não divergem" not in result.explanation


def test_ids_e_valores_divergentes_sem_anomaly_code() -> None:
    task = AnomalyTask(
        task_id="a",
        trace_id="t",
        customer_id="C-4821",
        business_date=date(2026, 9, 30),
        payments=_artifact("payments", _payment_data(status="SUCCESS", amount_cents=150_000, payment_id="pay_1")),
        reconciliation=_artifact(
            "reconciliation",
            _reconciliation_data(
                status="UPDATED",
                payment_id="pay_2",
                expected_amount_cents=150_000,
                settled_amount_cents=150_000,
            ),
        ),
    )
    result = run_anomaly_task(task)
    assert result.codes == ["PAYMENT_ID_MISMATCH"]
    assert result.conclusion == "divergence"


def test_provedor_indisponivel_mantem_o_resumo_da_evidencia(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk_live_provider_key")

    def boom(*args, **kwargs):
        raise httpx.ConnectError("provider down")

    monkeypatch.setattr("model_client.httpx.post", boom)
    artifact = run_domain_task("payments", "get_processing", _task(), CountingGateway("allowed"))
    assert artifact.status == "completed"
    assert "pay_1" in artifact.summary
    assert "SUCCESS" in artifact.summary
    assert "sk_live_provider_key" not in artifact.summary


def test_falha_e_pendencia_com_o_mesmo_valor_nao_divergem() -> None:
    task = AnomalyTask(
        task_id="a",
        trace_id="t",
        customer_id="C-4821",
        business_date=date(2026, 9, 30),
        payments=_artifact("payments", _payment_data(status="FAILED", amount_cents=80_000)),
        reconciliation=_artifact(
            "reconciliation",
            _reconciliation_data(status="PENDING", expected_amount_cents=80_000, settled_amount_cents=None),
        ),
    )
    result = run_anomaly_task(task)
    assert result.conclusion == "no_divergence"
    assert result.codes == []


def _artifact(agent_id: str, data: dict | None, status: str = "completed") -> SpecialistArtifact:
    return SpecialistArtifact(
        task_id="t",
        trace_id="t",
        agent_id=agent_id,
        status=status,
        source="replica" if status == "completed" else None,
        customer_id="C-4821",
        business_date=date(2026, 9, 30),
        summary="ok",
        data=data,
    )


def _payment_data(status: str, amount_cents: int, payment_id: str = "pay_1") -> dict:
    return {
        "found": True,
        "customer_id": "C-4821",
        "business_date": "2026-09-30",
        "payment": {"status": status, "payment_id": payment_id, "amount_cents": amount_cents},
    }


def _reconciliation_data(
    status: str,
    expected_amount_cents: int = 100_000,
    settled_amount_cents: int | None = 100_000,
    payment_id: str = "pay_1",
    anomaly_code: str | None = None,
) -> dict:
    return {
        "found": True,
        "customer_id": "C-4821",
        "business_date": "2026-09-30",
        "reconciliation": {
            "status": status,
            "payment_id": payment_id,
            "expected_amount_cents": expected_amount_cents,
            "settled_amount_cents": settled_amount_cents,
            "anomaly_code": anomaly_code,
        },
    }
