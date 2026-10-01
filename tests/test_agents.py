from datetime import date

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
        payments=_artifact("payments", {"found": True, "payment": {"status": "SUCCESS"}}),
        reconciliation=_artifact(
            "reconciliation",
            {
                "found": True,
                "reconciliation": {"status": "ERROR", "anomaly_code": "AMOUNT_MISMATCH"},
            },
        ),
    )
    result = run_anomaly_task(task)
    assert result.anomaly is True
    assert result.codes == ["PAYMENT_SUCCESS_RECONCILIATION_ERROR", "AMOUNT_MISMATCH"]


def _artifact(agent_id: str, data: dict) -> SpecialistArtifact:
    return SpecialistArtifact(
        task_id="t",
        trace_id="t",
        agent_id=agent_id,
        status="completed",
        source="replica",
        customer_id="C-4821",
        business_date=date(2026, 9, 30),
        summary="ok",
        data=data,
    )
