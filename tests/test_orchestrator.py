from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from agents.payments import create_app as create_payments_agent
from agents.runtime import run_anomaly_task
from settings import app_load

load_session = app_load("app-payments").load_session
from contracts.models import AnomalyArtifact, SpecialistArtifact
from orchestrator.loop import answer_question


class ExplodingAgents:
    def payments(self, task):
        raise AssertionError("pagamentos não deve ser chamado")

    def reconciliation(self, task):
        raise AssertionError("conciliação não deve ser chamada")

    def anomaly(self, task):
        raise AssertionError("anomalia não deve ser chamada")


class ScriptedAgents:
    def payments(self, task) -> SpecialistArtifact:
        return SpecialistArtifact(
            task_id=task.task_id,
            trace_id=task.trace_id,
            agent_id="payments",
            status="completed",
            source="replica",
            as_of=task.input.business_date,
            customer_id=task.input.customer_id,
            business_date=task.input.business_date,
            summary="Pagamento com sucesso na réplica.",
            data={
                "source": "replica",
                "as_of": "2026-09-30T21:00:00Z",
                "found": True,
                "customer_id": task.input.customer_id,
                "business_date": task.input.business_date.isoformat(),
                "payment": {"status": "SUCCESS", "payment_id": "pay_1", "amount_cents": 100_000},
            },
        )

    def reconciliation(self, task) -> SpecialistArtifact:
        return SpecialistArtifact(
            task_id=task.task_id,
            trace_id=task.trace_id,
            agent_id="reconciliation",
            status="completed",
            source="replica",
            customer_id=task.input.customer_id,
            business_date=task.input.business_date,
            summary="Conciliação em erro na réplica.",
            data={
                "source": "replica",
                "as_of": "2026-09-30T21:00:00Z",
                "found": True,
                "customer_id": task.input.customer_id,
                "business_date": task.input.business_date.isoformat(),
                "reconciliation": {
                    "status": "ERROR",
                    "anomaly_code": "AMOUNT_MISMATCH",
                    "reconciliation_id": "rec_1",
                    "payment_id": "pay_1",
                    "expected_amount_cents": 100_000,
                    "settled_amount_cents": 99_999,
                },
            },
        )

    def anomaly(self, task) -> AnomalyArtifact:
        return AnomalyArtifact(
            task_id=task.task_id,
            trace_id=task.trace_id,
            customer_id=task.customer_id,
            business_date=task.business_date,
            payments_outcome="completed",
            reconciliation_outcome="completed",
            conclusion="divergence",
            anomaly=True,
            codes=["PAYMENT_SUCCESS_RECONCILIATION_ERROR", "AMOUNT_MISMATCH"],
            explanation="Pagamento com sucesso e conciliação em erro.",
        )


def test_catalogo_nao_consulta_dominio(tmp_path: Path) -> None:
    manifest = load_session(tmp_path / "p.sqlite", tmp_path / "r.sqlite", tmp_path / "m.json", seed=7)
    result = answer_question("Quais customers posso consultar?", manifest, ExplodingAgents())
    assert result["kind"] == "catalog"
    ids = [item["customer_id"] for item in result["catalog"]["customers"]]
    assert ids == [item.customer_id for item in manifest.customers]
    assert "SUCCESS" not in result["answer"]
    assert "amount" not in result["answer"]


def test_customer_desconhecido_nao_chama_agentes(tmp_path: Path) -> None:
    manifest = load_session(tmp_path / "p.sqlite", tmp_path / "r.sqlite", tmp_path / "m.json", seed=7)
    result = answer_question("O customer C-0000 foi processado?", manifest, ExplodingAgents())
    assert result["kind"] == "recusa"
    assert "C-0000" in result["answer"]


def test_parecer_cita_replica(tmp_path: Path) -> None:
    manifest = load_session(tmp_path / "p.sqlite", tmp_path / "r.sqlite", tmp_path / "m.json", seed=7)
    customer_id = manifest.customers[0].customer_id
    result = answer_question(
        f"O customer {customer_id} foi processado com sucesso hoje?",
        manifest,
        ScriptedAgents(),
    )
    parecer = result["parecer"]
    assert result["kind"] == "parecer"
    assert parecer["anomaly"] is True
    assert parecer["conclusion"] == "divergence"
    assert parecer["codes"] == ["PAYMENT_SUCCESS_RECONCILIATION_ERROR", "AMOUNT_MISMATCH"]
    assert parecer["payments_status"] == "SUCCESS"
    assert parecer["reconciliation_status"] == "ERROR"
    assert "réplica" in parecer["answer"]
    assert all(source["source"] == "replica" for source in parecer["sources"])


def test_parecer_inconclusivo_preserva_codigos_quando_ha_divergencia(tmp_path: Path) -> None:
    manifest = load_session(tmp_path / "p.sqlite", tmp_path / "r.sqlite", tmp_path / "m.json", seed=7)
    customer_id = manifest.customers[0].customer_id

    class LiveAnomaly:
        def payments(self, task) -> SpecialistArtifact:
            return SpecialistArtifact(
                task_id=task.task_id,
                trace_id=task.trace_id,
                agent_id="payments",
                status="refused",
                customer_id=task.input.customer_id,
                business_date=task.input.business_date,
                summary="A consulta foi recusada pelo gateway.",
                data=None,
            )

        def reconciliation(self, task) -> SpecialistArtifact:
            return SpecialistArtifact(
                task_id=task.task_id,
                trace_id=task.trace_id,
                agent_id="reconciliation",
                status="completed",
                source="replica",
                customer_id=task.input.customer_id,
                business_date=task.input.business_date,
                summary="Conciliação atualizada na réplica.",
                data={
                    "source": "replica",
                    "as_of": "2026-09-30T21:00:00Z",
                    "found": True,
                    "customer_id": task.input.customer_id,
                    "business_date": task.input.business_date.isoformat(),
                    "reconciliation": {
                        "reconciliation_id": "rec_1",
                        "status": "UPDATED",
                        "payment_id": "pay_1",
                        "expected_amount_cents": 100_000,
                        "settled_amount_cents": 100_000,
                    },
                },
            )

        def anomaly(self, task):
            return run_anomaly_task(task)

    result = answer_question(f"O customer {customer_id} foi processado?", manifest, LiveAnomaly())
    parecer = result["parecer"]
    assert parecer["conclusion"] == "inconclusive"
    assert parecer["anomaly"] is False
    assert parecer["payments_outcome"] == "refused"
    assert parecer["reconciliation_outcome"] == "completed"
    assert "não divergem" not in parecer["answer"]
    assert "Conciliação atualizada na réplica." in parecer["answer"]
    assert parecer["codes"] == []
    assert [source["domain"] for source in parecer["sources"]] == ["reconciliation"]


def test_cartao_do_agente_de_pagamentos() -> None:
    client = TestClient(create_payments_agent())
    card = client.get("/.well-known/agent-card.json")
    alias = client.get("/.well-known/agent.json")
    assert card.status_code == 200
    assert alias.status_code == 200
    body = card.json()
    assert body["name"] == "Payments"
    assert body["skills"][0]["id"] == "lookup_customer_processing"
    assert "application/json" in body["defaultInputModes"]
