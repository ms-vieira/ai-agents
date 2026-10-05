"""Envelope identity and per-investigation deadlines. No real model calls and no sleeps."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from agents.runtime import run_anomaly_task
from contracts.models import (
    AnomalyArtifact,
    DomainTask,
    DomainTaskInput,
    SpecialistArtifact,
)
from orchestrator.loop import (
    RETURN_MARGIN_SECONDS,
    Deadline,
    HttpAgentDirectory,
    answer_question,
)
from settings import AGENT_CALL_TIMEOUT_SECONDS, app_load

load_session = app_load("app-payments").load_session

DAY = date(2026, 9, 30)
FOREIGN = "C-9999"
LEAK = "pay_foreign"
OTHER_TASK = "task-de-outra-investigacao"


class ManualClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _manifest(tmp_path: Path):
    return load_session(tmp_path / "p.sqlite", tmp_path / "r.sqlite", tmp_path / "m.json", seed=7)


def _domain_task(task_id: str = "task-payments", trace_id: str = "trace-1") -> DomainTask:
    return DomainTask(
        task_id=task_id,
        trace_id=trace_id,
        skill="lookup_customer_processing",
        input=DomainTaskInput(customer_id="C-4821", business_date=DAY, question="foi processado?"),
    )


def _payment_data(customer_id: str = "C-4821", business_date: str = "2026-09-30", payment_id: str = "pay_1") -> dict:
    return {
        "source": "replica",
        "as_of": "2026-09-30T21:00:00Z",
        "found": True,
        "customer_id": customer_id,
        "business_date": business_date,
        "payment": {"payment_id": payment_id, "status": "SUCCESS", "amount_cents": 100_000},
    }


def _reconciliation_data(customer_id: str, business_date: str) -> dict:
    return {
        "source": "replica",
        "as_of": "2026-09-30T21:00:00Z",
        "found": True,
        "customer_id": customer_id,
        "business_date": business_date,
        "reconciliation": {
            "reconciliation_id": "rec_1",
            "status": "UPDATED",
            "payment_id": "pay_1",
            "expected_amount_cents": 100_000,
            "settled_amount_cents": 100_000,
        },
    }


def _payment_artifact(task: DomainTask, **changes) -> SpecialistArtifact:
    data = _payment_data(task.input.customer_id, task.input.business_date.isoformat())
    artifact = SpecialistArtifact(
        task_id=task.task_id,
        trace_id=task.trace_id,
        agent_id="payments",
        status="completed",
        source="replica",
        customer_id=task.input.customer_id,
        business_date=task.input.business_date,
        summary="Pagamento pay_1 com status SUCCESS na réplica.",
        data=data,
    )
    return artifact.model_copy(update=changes)


def _reconciliation_artifact(task: DomainTask) -> SpecialistArtifact:
    return SpecialistArtifact(
        task_id=task.task_id,
        trace_id=task.trace_id,
        agent_id="reconciliation",
        status="completed",
        source="replica",
        customer_id=task.input.customer_id,
        business_date=task.input.business_date,
        summary="Conciliação atualizada na réplica.",
        data=_reconciliation_data(task.input.customer_id, task.input.business_date.isoformat()),
    )


def _spy(monkeypatch):
    prompts: list[str] = []

    def spy(model, system, user, timeout=None):
        prompts.append(user)
        return None

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr("orchestrator.loop.complete", spy)
    monkeypatch.setattr("agents.runtime.complete", spy)
    return prompts


def _ask(tmp_path: Path, agents, monkeypatch):
    prompts = _spy(monkeypatch)
    manifest = _manifest(tmp_path)
    customer_id = manifest.customers[0].customer_id
    parecer = answer_question(f"O customer {customer_id} foi processado?", manifest, agents)["parecer"]
    return parecer, prompts, customer_id


def test_suite_nao_ve_a_chave_real():
    import os

    assert "OPENAI_API_KEY" not in os.environ


@pytest.mark.parametrize(
    "change",
    [
        {"task_id": OTHER_TASK},
        {"trace_id": "trace-de-outra-investigacao"},
        {"agent_id": "reconciliation"},
        {"customer_id": FOREIGN},
        {"business_date": date(2026, 1, 1)},
    ],
)
def test_envelope_de_outra_tarefa_nao_entra_no_parecer(tmp_path: Path, monkeypatch, change) -> None:
    prompts_box = _spy(monkeypatch)
    leaked = _payment_data(FOREIGN, "2026-09-30", LEAK)

    class Agents:
        def payments(self, task):
            artifact = _payment_artifact(
                task,
                summary=f"Pagamento {LEAK} do customer {FOREIGN}.",
                data=leaked,
            )
            return artifact.model_copy(update=change)

        def reconciliation(self, task):
            return _reconciliation_artifact(task)

        def anomaly(self, task):
            return run_anomaly_task(task)

    manifest = _manifest(tmp_path)
    customer_id = manifest.customers[0].customer_id
    parecer = answer_question(f"O customer {customer_id} foi processado?", manifest, Agents())["parecer"]
    blob = json.dumps(parecer)
    assert parecer["payments_outcome"] == "invalid"
    assert parecer["conclusion"] == "inconclusive"
    assert parecer["reconciliation_outcome"] == "completed"
    assert LEAK not in blob
    assert FOREIGN not in blob
    assert OTHER_TASK not in blob
    assert "Conciliação atualizada na réplica." in parecer["answer"]
    assert all(LEAK not in prompt and FOREIGN not in prompt for prompt in prompts_box)


def test_dados_internos_diferentes_do_envelope_sao_descartados(tmp_path: Path, monkeypatch) -> None:
    prompts = _spy(monkeypatch)

    class Agents:
        def payments(self, task):
            data = _payment_data(FOREIGN, task.input.business_date.isoformat(), LEAK)
            return _payment_artifact(task, data=data, summary=f"Pagamento {LEAK} do customer {FOREIGN}.")

        def reconciliation(self, task):
            return _reconciliation_artifact(task)

        def anomaly(self, task):
            return run_anomaly_task(task)

    parecer, _, _ = _ask_with(tmp_path, Agents(), prompts)
    blob = json.dumps(parecer)
    assert parecer["payments_outcome"] == "invalid"
    assert LEAK not in blob and FOREIGN not in blob
    assert all(LEAK not in item and FOREIGN not in item for item in prompts)


def test_anomalia_de_outra_investigacao_nao_conclui(tmp_path: Path, monkeypatch) -> None:
    prompts = _spy(monkeypatch)

    class Agents:
        def payments(self, task):
            return _payment_artifact(task)

        def reconciliation(self, task):
            return _reconciliation_artifact(task)

        def anomaly(self, task):
            return AnomalyArtifact(
                task_id=OTHER_TASK,
                trace_id=task.trace_id,
                customer_id=task.customer_id,
                business_date=task.business_date,
                payments_outcome="completed",
                reconciliation_outcome="completed",
                conclusion="no_divergence",
                anomaly=False,
                codes=[],
                explanation="NAO-DESTA-INVESTIGACAO os dominios nao divergem.",
            )

    parecer, _, _ = _ask_with(tmp_path, Agents(), prompts)
    blob = json.dumps(parecer)
    assert parecer["conclusion"] == "inconclusive"
    assert parecer["anomaly"] is False
    assert parecer["codes"] == []
    assert "NAO-DESTA-INVESTIGACAO" not in blob
    assert "não divergem" not in parecer["answer"]
    assert "Pagamento pay_1" in parecer["answer"]
    assert all("NAO-DESTA-INVESTIGACAO" not in item for item in prompts)


def test_conclusao_contraditoria_e_descartada(tmp_path: Path, monkeypatch) -> None:
    prompts = _spy(monkeypatch)

    class Agents:
        def payments(self, task):
            return _payment_artifact(task)

        def reconciliation(self, task):
            return _reconciliation_artifact(task)

        def anomaly(self, task):
            return AnomalyArtifact(
                task_id=task.task_id,
                trace_id=task.trace_id,
                customer_id=task.customer_id,
                business_date=task.business_date,
                payments_outcome="completed",
                reconciliation_outcome="completed",
                conclusion="no_divergence",
                anomaly=True,
                codes=["CODIGO_FALSO"],
                explanation="CODIGO_FALSO contradiz a conclusao.",
            )

    parecer, _, _ = _ask_with(tmp_path, Agents(), prompts)
    blob = json.dumps(parecer)
    assert parecer["conclusion"] == "inconclusive"
    assert parecer["codes"] == []
    assert "CODIGO_FALSO" not in blob
    assert all("CODIGO_FALSO" not in item for item in prompts)


def test_resultado_tecnico_mentiroso_nao_substitui_as_evidencias(tmp_path: Path, monkeypatch) -> None:
    prompts = _spy(monkeypatch)

    class Agents:
        def payments(self, task):
            return SpecialistArtifact(
                task_id=task.task_id,
                trace_id=task.trace_id,
                agent_id="payments",
                status="refused",
                customer_id=task.input.customer_id,
                business_date=task.input.business_date,
                summary="A consulta foi recusada.",
                data=None,
            )

        def reconciliation(self, task):
            return _reconciliation_artifact(task)

        def anomaly(self, task):
            return AnomalyArtifact(
                task_id=task.task_id,
                trace_id=task.trace_id,
                customer_id=task.customer_id,
                business_date=task.business_date,
                payments_outcome="completed",
                reconciliation_outcome="completed",
                conclusion="no_divergence",
                anomaly=False,
                codes=[],
                explanation="TUDO-CERTO sem divergencia.",
            )

    parecer, _, _ = _ask_with(tmp_path, Agents(), prompts)
    assert parecer["payments_outcome"] == "refused"
    assert parecer["conclusion"] == "inconclusive"
    assert "TUDO-CERTO" not in json.dumps(parecer)
    assert "Conciliação atualizada na réplica." in parecer["answer"]
    assert all("TUDO-CERTO" not in item for item in prompts)


def _ask_with(tmp_path, agents, prompts):
    manifest = _manifest(tmp_path)
    customer_id = manifest.customers[0].customer_id
    parecer = answer_question(f"O customer {customer_id} foi processado?", manifest, agents)["parecer"]
    return parecer, prompts, customer_id


def _directory(handler) -> HttpAgentDirectory:
    return HttpAgentDirectory(
        "http://payments.test",
        "http://reconciliation.test",
        "http://anomaly.test",
        transport=httpx.MockTransport(handler),
    )


def _skill(host: str) -> str:
    return {
        "payments.test": "lookup_customer_processing",
        "reconciliation.test": "lookup_customer_reconciliation",
        "anomaly.test": "detect_anomaly",
    }[host]


def _specialist_body(body: dict, agent_id: str) -> dict:
    customer_id = body.get("customer_id") or body["input"]["customer_id"]
    business_date = body.get("business_date") or body["input"]["business_date"]
    return {
        "task_id": body["task_id"],
        "trace_id": body["trace_id"],
        "agent_id": agent_id,
        "status": "unavailable",
        "customer_id": customer_id,
        "business_date": business_date,
        "summary": "sem evidencia",
        "data": None,
    }


def test_investigacoes_intercaladas_preservam_o_proprio_prazo() -> None:
    clock_a = ManualClock()
    clock_b = ManualClock()
    deadline_a = Deadline(30, 1, clock_a)
    deadline_b = Deadline(4, 1, clock_b)
    posts: list[tuple[str, float]] = []
    holder: dict = {}
    started = {"nested": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("agent-card.json"):
            if request.url.host == "payments.test" and not started["nested"]:
                started["nested"] = True
                holder["directory"].reconciliation(_domain_task("task-b", "trace-b"), deadline_b)
            return httpx.Response(200, json={"skills": [{"id": _skill(request.url.host)}]})
        body = json.loads(request.content)
        posts.append((request.url.host, body["timeout_seconds"]))
        agent_id = "reconciliation" if request.url.host == "reconciliation.test" else "payments"
        return httpx.Response(200, json=_specialist_body(body, agent_id))

    directory = _directory(handler)
    holder["directory"] = directory
    directory.payments(_domain_task("task-a", "trace-a"), deadline_a)
    directory.payments(_domain_task("task-a2", "trace-a"), deadline_a)
    directory.close()

    large = AGENT_CALL_TIMEOUT_SECONDS - RETURN_MARGIN_SECONDS
    small = 3 - RETURN_MARGIN_SECONDS
    assert posts[0][0] == "reconciliation.test"
    assert posts[0][1] == pytest.approx(small)
    assert posts[1][1] == pytest.approx(large)
    assert posts[2][1] == pytest.approx(large)
    assert not hasattr(directory, "_deadline")


def test_descoberta_rapida_encurta_o_post_sem_renovar_a_janela() -> None:
    clock = ManualClock()
    deadline = Deadline(30, 1, clock)
    posts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("agent-card.json"):
            clock.now += 0.2
            return httpx.Response(200, json={"skills": [{"id": "lookup_customer_processing"}]})
        body = json.loads(request.content)
        posts.append(body["timeout_seconds"])
        return httpx.Response(200, json=_specialist_body(body, "payments"))

    directory = _directory(handler)
    directory.payments(_domain_task(), deadline)
    directory.close()
    assert posts == [pytest.approx(AGENT_CALL_TIMEOUT_SECONDS - 0.2 - RETURN_MARGIN_SECONDS)]


def test_descoberta_que_consome_a_janela_nao_inicia_o_post() -> None:
    clock = ManualClock()
    deadline = Deadline(30, 1, clock)
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("agent-card.json"):
            clock.now += AGENT_CALL_TIMEOUT_SECONDS - 0.4
            return httpx.Response(200, json={"skills": [{"id": "lookup_customer_processing"}]})
        posts.append(request.url.path)
        return httpx.Response(200, json={})

    directory = _directory(handler)
    artifact = directory.payments(_domain_task(), deadline)
    directory.close()
    assert posts == []
    assert artifact.status == "unavailable"
    assert artifact.data is None


def test_descoberta_que_consome_a_investigacao_nao_inicia_o_post(tmp_path: Path) -> None:
    clock = ManualClock()
    manifest = _manifest(tmp_path)
    customer_id = manifest.customers[0].customer_id
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("agent-card.json"):
            clock.now += 2.6
            return httpx.Response(200, json={"skills": [{"id": _skill(request.url.host)}]})
        posts.append(request.url.path)
        return httpx.Response(200, json={})

    directory = _directory(handler)
    parecer = answer_question(
        f"O customer {customer_id} foi processado?",
        manifest,
        directory,
        clock=clock,
        deadline_seconds=4,
    )["parecer"]
    directory.close()
    assert posts == []
    assert parecer["conclusion"] == "inconclusive"
    assert parecer["payments_outcome"] == "unavailable"


def test_parecer_deterministico_quando_a_ia_nao_cabe(tmp_path: Path, monkeypatch) -> None:
    called = False

    def spy(model, system, user, timeout=None):
        nonlocal called
        called = True
        return "nao usar"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr("agents.runtime.complete", lambda *args, **kwargs: None)
    monkeypatch.setattr("orchestrator.loop.complete", spy)
    clock = ManualClock()
    manifest = _manifest(tmp_path)
    customer_id = manifest.customers[0].customer_id

    class Agents:
        def payments(self, task):
            return _payment_artifact(task)

        def reconciliation(self, task):
            return _reconciliation_artifact(task)

        def anomaly(self, task):
            clock.now = 1_000.0
            return run_anomaly_task(task)

    parecer = answer_question(
        f"O customer {customer_id} foi processado?",
        manifest,
        Agents(),
        clock=clock,
        deadline_seconds=8,
    )["parecer"]
    assert called is False
    assert "Pagamento pay_1" in parecer["answer"]
    assert "réplica" in parecer["answer"]
