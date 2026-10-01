from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from agents.payments import create_app as create_payments_agent
from carga.load import load_session
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
            data={"found": True, "source": "replica", "payment": {"status": "SUCCESS", "payment_id": "pay_1"}},
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
                "found": True,
                "source": "replica",
                "reconciliation": {"status": "ERROR", "anomaly_code": "AMOUNT_MISMATCH", "reconciliation_id": "rec_1"},
            },
        )

    def anomaly(self, task) -> AnomalyArtifact:
        return AnomalyArtifact(
            task_id=task.task_id,
            trace_id=task.trace_id,
            customer_id=task.customer_id,
            business_date=task.business_date,
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
    assert parecer["payments_status"] == "SUCCESS"
    assert parecer["reconciliation_status"] == "ERROR"
    assert "réplica" in parecer["answer"]
    assert all(source["source"] == "replica" for source in parecer["sources"])


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
