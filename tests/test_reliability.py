"""Regressão da etapa de confiabilidade do parecer. Sem provedor real e sem espera longa."""

import json
from datetime import date
from pathlib import Path

import httpx

from agents.runtime import GatewayClient, run_anomaly_task, run_domain_task
from contracts.models import AnomalyArtifact, AnomalyTask, DomainTask, DomainTaskInput, GatewayCall, GatewayResult, SpecialistArtifact
from orchestrator.loop import HttpAgentDirectory, answer_question
from settings import app_load

load_session = app_load("app-payments").load_session

DAY = date(2026, 9, 30)
CUSTOMER = "C-4821"
FOREIGN = "C-9999"
LEAK = "pay_foreign"


class Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now
        self.reads = 0

    def __call__(self) -> float:
        self.reads += 1
        if self.reads == 1:
            return 0.0
        return self.now


def _task(agent_skill: str = "lookup_customer_processing") -> DomainTask:
    return DomainTask(
        task_id="task-1",
        trace_id="trace-1",
        skill=agent_skill,
        input=DomainTaskInput(customer_id=CUSTOMER, business_date=DAY, question="foi processado?"),
    )


def _payment(
    status: str = "SUCCESS",
    amount_cents: int = 100_000,
    payment_id: str = "pay_1",
    found: bool = True,
    customer_id: str = CUSTOMER,
    business_date: str = "2026-09-30",
    include_record: bool = True,
) -> dict:
    data = {
        "source": "replica",
        "as_of": "2026-09-30T21:00:00Z",
        "found": found,
        "customer_id": customer_id,
        "business_date": business_date,
    }
    if include_record:
        data["payment"] = {"status": status, "payment_id": payment_id, "amount_cents": amount_cents}
    return data


def _reconciliation(
    status: str = "UPDATED",
    expected_amount_cents: int | None = 100_000,
    settled_amount_cents: int | None = 100_000,
    payment_id: str = "pay_1",
    found: bool = True,
    customer_id: str = CUSTOMER,
    business_date: str = "2026-09-30",
    include_record: bool = True,
) -> dict:
    data = {
        "source": "replica",
        "as_of": "2026-09-30T21:00:00Z",
        "found": found,
        "customer_id": customer_id,
        "business_date": business_date,
    }
    if include_record:
        data["reconciliation"] = {
            "reconciliation_id": "rec_1",
            "status": status,
            "payment_id": payment_id,
            "expected_amount_cents": expected_amount_cents,
            "settled_amount_cents": settled_amount_cents,
        }
    return data


def _artifact(
    agent_id: str,
    data: dict | None,
    status: str = "completed",
    summary: str = "ok",
    task=None,
) -> SpecialistArtifact:
    return SpecialistArtifact(
        task_id=task.task_id if task is not None else "t",
        trace_id=task.trace_id if task is not None else "t",
        agent_id=agent_id,
        status=status,
        source="replica" if status == "completed" else None,
        customer_id=task.input.customer_id if task is not None else CUSTOMER,
        business_date=task.input.business_date if task is not None else DAY,
        summary=summary,
        data=data,
    )


def _anomaly(payments: SpecialistArtifact, reconciliation: SpecialistArtifact) -> AnomalyTask:
    return AnomalyTask(
        task_id="a",
        trace_id="t",
        customer_id=CUSTOMER,
        business_date=DAY,
        payments=payments,
        reconciliation=reconciliation,
    )


def _manifest(tmp_path: Path):
    return load_session(tmp_path / "p.sqlite", tmp_path / "r.sqlite", tmp_path / "m.json", seed=7)


class _Gateway:
    def __init__(self, data: dict) -> None:
        self.data = data

    def call(self, body: GatewayCall):
        return 200, GatewayResult(decision="allowed", data=self.data)


def test_evidencia_de_outro_cliente_nao_vai_ao_modelo(monkeypatch) -> None:
    prompts: list[str] = []

    def spy(model, system, user, timeout=None):
        prompts.append(user)
        return "não usar"

    monkeypatch.setattr("agents.runtime.complete", spy)
    data = _payment(customer_id=FOREIGN, payment_id=LEAK, status="FAILED")
    data["payment"]["error_detail"] = "token=sk_live_other"
    artifact = run_domain_task("payments", "get_processing", _task(), _Gateway(data))
    assert artifact.status == "invalid"
    assert artifact.data is None
    assert prompts == []
    assert FOREIGN not in artifact.summary
    assert LEAK not in artifact.summary
    assert "sk_live" not in artifact.summary
    assert "FAILED" not in artifact.summary


def test_evidencia_de_outra_data_e_invalida(monkeypatch) -> None:
    monkeypatch.setattr("agents.runtime.complete", lambda *args, **kwargs: "não usar")
    artifact = run_domain_task(
        "payments",
        "get_processing",
        _task(),
        _Gateway(_payment(business_date="2026-01-01")),
    )
    assert artifact.status == "invalid"
    assert artifact.data is None


def test_envelope_de_outro_dominio_nao_gera_resumo(monkeypatch) -> None:
    prompts: list[str] = []

    def spy(model, system, user, timeout=None):
        prompts.append(user)
        return None

    monkeypatch.setattr("agents.runtime.complete", spy)
    artifact = run_domain_task("reconciliation", "get_reconciliation", _task(), _Gateway(_payment()))
    assert artifact.status == "invalid"
    assert prompts == []
    assert "SUCCESS" not in artifact.summary


def test_outro_cliente_nao_aparece_no_parecer(tmp_path: Path, monkeypatch) -> None:
    prompts: list[str] = []

    def spy(model, system, user, timeout=None):
        prompts.append(user)
        return None

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setattr("agents.runtime.complete", spy)
    monkeypatch.setattr("orchestrator.loop.complete", spy)
    manifest = _manifest(tmp_path)
    customer_id = manifest.customers[0].customer_id
    leaked = _payment(customer_id=FOREIGN, payment_id=LEAK, status="SUCCESS")

    class Agents:
        def payments(self, task):
            return _artifact(
                "payments",
                leaked,
                summary=f"Pagamento {LEAK} do customer {FOREIGN} com status SUCCESS.",
            )

        def reconciliation(self, task):
            data = _reconciliation()
            data["customer_id"] = task.input.customer_id
            data["business_date"] = task.input.business_date.isoformat()
            return _artifact("reconciliation", data, summary="Conciliação atualizada na réplica.", task=task)

        def anomaly(self, task):
            body = task.model_dump_json()
            assert FOREIGN not in body
            assert LEAK not in body
            result = run_anomaly_task(task)
            return result.model_copy(update={"explanation": f"vazou {FOREIGN} {LEAK} SUCCESS"})

    result = answer_question(f"O customer {customer_id} foi processado?", manifest, Agents())
    parecer = result["parecer"]
    blob = json.dumps(parecer)
    assert FOREIGN not in blob
    assert LEAK not in blob
    assert "SUCCESS" not in blob
    assert "sk-test-key" not in blob
    assert all(FOREIGN not in prompt and LEAK not in prompt for prompt in prompts)
    assert parecer["payments_outcome"] == "invalid"
    assert parecer["reconciliation_outcome"] == "completed"
    assert parecer["conclusion"] == "inconclusive"
    assert parecer["payments_status"] is None
    assert "Conciliação atualizada na réplica." in parecer["answer"]
    assert [source["domain"] for source in parecer["sources"]] == ["reconciliation"]


def test_found_coagido_nao_vira_ausencia() -> None:
    recon = _artifact("reconciliation", _reconciliation())
    for found in (0, "false", 1):
        data = _payment()
        data["found"] = found
        result = run_anomaly_task(_anomaly(_artifact("payments", data), recon))
        assert result.payments_outcome == "invalid"
        assert result.codes == []
        assert result.conclusion == "inconclusive"


def test_valor_coagido_nao_entra_na_regra(monkeypatch) -> None:
    prompts: list[str] = []

    def spy(model, system, user, timeout=None):
        prompts.append(user)
        return None

    monkeypatch.setattr("agents.runtime.complete", spy)
    text = _payment()
    text["payment"]["amount_cents"] = "80000"
    flag = _payment()
    flag["payment"]["amount_cents"] = True
    for payload in (text, flag):
        result = run_anomaly_task(
            _anomaly(_artifact("payments", payload), _artifact("reconciliation", _reconciliation()))
        )
        assert result.payments_outcome == "invalid"
        assert result.codes == []
    assert "80000" not in " ".join(prompts)


def test_modelo_lento_cabe_no_prazo_do_especialista(monkeypatch) -> None:
    monkeypatch.setattr("settings.MODEL_TIMEOUT_SECONDS", 20.0)
    monkeypatch.setattr("settings.AGENT_CALL_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr("settings.GATEWAY_CALL_TIMEOUT_SECONDS", 2.0)
    from settings import tool_and_model_budget

    gateway, model = tool_and_model_budget(15, includes_gateway=True)
    assert gateway + model + 0.25 <= 5
    assert model < 20


def test_found_ausente_nao_vira_pagamento_inexistente() -> None:
    data = _payment()
    del data["found"]
    result = run_anomaly_task(_anomaly(_artifact("payments", data), _artifact("reconciliation", _reconciliation())))
    assert result.payments_outcome == "invalid"
    assert result.conclusion == "inconclusive"
    assert result.codes == []


def test_found_true_sem_registro_e_invalido() -> None:
    result = run_anomaly_task(
        _anomaly(
            _artifact("payments", _payment(include_record=False)),
            _artifact("reconciliation", _reconciliation()),
        )
    )
    assert result.payments_outcome == "invalid"
    assert "PAYMENT_MISSING" not in result.codes


def test_found_false_com_registro_e_invalido() -> None:
    result = run_anomaly_task(
        _anomaly(
            _artifact("payments", _payment(found=False)),
            _artifact("reconciliation", _reconciliation()),
        )
    )
    assert result.payments_outcome == "invalid"
    assert result.codes == []


def test_found_false_sem_registro_continua_sendo_ausencia() -> None:
    result = run_anomaly_task(
        _anomaly(
            _artifact("payments", _payment(found=False, include_record=False)),
            _artifact("reconciliation", _reconciliation()),
        )
    )
    assert result.payments_outcome == "completed"
    assert result.codes == ["PAYMENT_MISSING"]
    assert result.conclusion == "divergence"


def test_status_e_valor_invalidos_nao_viram_divergencia(monkeypatch) -> None:
    prompts: list[str] = []

    def spy(model, system, user, timeout=None):
        prompts.append(user)
        return None

    monkeypatch.setattr("agents.runtime.complete", spy)
    unknown = _payment()
    unknown["payment"]["status"] = "DONE"
    negative = _payment(amount_cents=-1)
    for payload in (unknown, negative):
        result = run_anomaly_task(
            _anomaly(_artifact("payments", payload), _artifact("reconciliation", _reconciliation()))
        )
        assert result.payments_outcome == "invalid"
        assert result.conclusion == "inconclusive"
        assert result.codes == []
    joined = " ".join(prompts)
    assert "DONE" not in joined
    assert "-1" not in joined


def test_found_ausente_nao_entra_no_parecer(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    customer_id = manifest.customers[0].customer_id
    leaked = _payment(payment_id=LEAK)
    del leaked["found"]

    class Agents:
        def payments(self, task):
            return _artifact("payments", leaked, summary=f"Pagamento {LEAK} com status SUCCESS.")

        def reconciliation(self, task):
            data = _reconciliation()
            data["customer_id"] = task.input.customer_id
            data["business_date"] = task.input.business_date.isoformat()
            return _artifact("reconciliation", data, summary="Conciliação atualizada na réplica.", task=task)

        def anomaly(self, task):
            return run_anomaly_task(task)

    parecer = answer_question(f"O customer {customer_id} foi processado?", manifest, Agents())["parecer"]
    assert "PAYMENT_MISSING" not in parecer["answer"]
    assert LEAK not in json.dumps(parecer)
    assert parecer["payments_outcome"] == "invalid"
    assert parecer["conclusion"] == "inconclusive"


def test_gateway_fora_do_ar_nao_levanta_excecao() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = GatewayClient("http://gateway.test", "dev-payments", transport=httpx.MockTransport(handler))
    artifact = run_domain_task("payments", "get_processing", _task(), client)
    assert artifact.status == "unavailable"
    assert artifact.data is None
    assert "connection refused" not in artifact.summary


def test_resposta_nao_json_do_gateway_e_invalida() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"not-json {FOREIGN} {LEAK}")

    client = GatewayClient("http://gateway.test", "dev-payments", transport=httpx.MockTransport(handler))
    artifact = run_domain_task("payments", "get_processing", _task(), client)
    assert artifact.status == "invalid"
    assert artifact.data is None
    assert FOREIGN not in artifact.summary
    assert LEAK not in artifact.summary


def test_erro_http_do_gateway_nao_copia_o_corpo() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="token=sk_live_upstream")

    client = GatewayClient("http://gateway.test", "dev-payments", transport=httpx.MockTransport(handler))
    artifact = run_domain_task("payments", "get_processing", _task(), client)
    assert artifact.status == "unavailable"
    assert "sk_live" not in artifact.summary


def test_recusa_http_nao_copia_o_segredo() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Bearer sk_live_token"})

    client = GatewayClient("http://gateway.test", "dev-payments", transport=httpx.MockTransport(handler))
    artifact = run_domain_task("payments", "get_processing", _task(), client)
    assert artifact.status == "refused"
    assert "sk_live" not in artifact.summary


def test_especialista_indisponivel_na_entrada_do_orquestrador(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    customer_id = manifest.customers[0].customer_id

    class Agents:
        def payments(self, task):
            raise httpx.ConnectError("specialist down")

        def reconciliation(self, task):
            data = _reconciliation()
            data["customer_id"] = task.input.customer_id
            data["business_date"] = task.input.business_date.isoformat()
            return _artifact("reconciliation", data, summary="Conciliação atualizada na réplica.", task=task)

        def anomaly(self, task):
            return run_anomaly_task(task)

    parecer = answer_question(f"O customer {customer_id} foi processado?", manifest, Agents())["parecer"]
    assert parecer["payments_outcome"] == "unavailable"
    assert parecer["conclusion"] == "inconclusive"
    assert parecer["codes"] == []
    assert "specialist down" not in parecer["answer"]
    assert "Conciliação atualizada na réplica." in parecer["answer"]


def test_falha_de_cartao_e_contrato_na_entrada(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    customer_id = manifest.customers[0].customer_id

    def card_down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"card down {FOREIGN}")

    directory = HttpAgentDirectory(
        "http://payments.test",
        "http://reconciliation.test",
        "http://anomaly.test",
        transport=httpx.MockTransport(card_down),
    )
    parecer = answer_question(f"O customer {customer_id} foi processado?", manifest, directory)["parecer"]
    assert parecer["conclusion"] == "inconclusive"
    assert parecer["payments_outcome"] == "unavailable"
    assert FOREIGN not in json.dumps(parecer)
    directory.close()

    def card_text(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"not-json {LEAK}")

    directory = HttpAgentDirectory(
        "http://payments.test",
        "http://reconciliation.test",
        "http://anomaly.test",
        transport=httpx.MockTransport(card_text),
    )
    parecer = answer_question(f"O customer {customer_id} foi processado?", manifest, directory)["parecer"]
    assert parecer["payments_outcome"] == "invalid"
    assert LEAK not in json.dumps(parecer)
    directory.close()

    def bad_contract(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("agent-card.json"):
            skill = {
                "payments.test": "lookup_customer_processing",
                "reconciliation.test": "lookup_customer_reconciliation",
                "anomaly.test": "detect_anomaly",
            }[request.url.host]
            return httpx.Response(200, json={"skills": [{"id": skill}]})
        return httpx.Response(200, json={"customer_id": FOREIGN, "payment_id": LEAK})

    directory = HttpAgentDirectory(
        "http://payments.test",
        "http://reconciliation.test",
        "http://anomaly.test",
        transport=httpx.MockTransport(bad_contract),
    )
    parecer = answer_question(f"O customer {customer_id} foi processado?", manifest, directory)["parecer"]
    assert parecer["conclusion"] == "inconclusive"
    assert FOREIGN not in json.dumps(parecer)
    assert LEAK not in json.dumps(parecer)
    directory.close()


def test_anomalia_indisponivel_nao_inventa_conclusao(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    customer_id = manifest.customers[0].customer_id

    class Agents:
        def payments(self, task):
            data = _payment(status="SUCCESS", amount_cents=150_000)
            data["customer_id"] = task.input.customer_id
            data["business_date"] = task.input.business_date.isoformat()
            return _artifact("payments", data, summary="Pagamento com sucesso na réplica.", task=task)

        def reconciliation(self, task):
            data = _reconciliation(status="ERROR", expected_amount_cents=150_000, settled_amount_cents=149_999)
            data["customer_id"] = task.input.customer_id
            data["business_date"] = task.input.business_date.isoformat()
            return _artifact("reconciliation", data, summary="Conciliação em erro na réplica.", task=task)

        def anomaly(self, task):
            raise httpx.ConnectError("anomaly down")

    parecer = answer_question(f"O customer {customer_id} foi processado?", manifest, Agents())["parecer"]
    assert parecer["conclusion"] == "inconclusive"
    assert parecer["anomaly"] is False
    assert parecer["codes"] == []
    assert parecer["payments_outcome"] == "completed"
    assert parecer["reconciliation_outcome"] == "completed"
    assert "Pagamento com sucesso na réplica." in parecer["answer"]
    assert "Conciliação em erro na réplica." in parecer["answer"]
    assert "anomaly down" not in parecer["answer"]


def test_modelo_lento_cai_no_texto_deterministico(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    seen: list[float] = []

    def slow(*args, **kwargs):
        seen.append(kwargs["timeout"])
        raise httpx.TimeoutException("slow model")

    monkeypatch.setattr("model_client.httpx.post", slow)
    artifact = run_domain_task("payments", "get_processing", _task(), _Gateway(_payment()))
    assert artifact.status == "completed"
    assert "pay_1" in artifact.summary
    assert "SUCCESS" in artifact.summary
    assert seen
    assert seen[0] <= 2
    assert "sk-test-key" not in artifact.summary


def test_modelo_fora_do_prazo_nao_e_chamado(tmp_path: Path, monkeypatch) -> None:
    called = False

    def spy(model, system, user, timeout=None):
        nonlocal called
        called = True
        return "não usar"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setattr("orchestrator.loop.complete", spy)
    manifest = _manifest(tmp_path)
    customer_id = manifest.customers[0].customer_id
    clock = Clock()

    class Agents:
        def payments(self, task):
            data = _payment()
            data["customer_id"] = task.input.customer_id
            data["business_date"] = task.input.business_date.isoformat()
            return _artifact("payments", data, summary="Pagamento com sucesso na réplica.", task=task)

        def reconciliation(self, task):
            data = _reconciliation()
            data["customer_id"] = task.input.customer_id
            data["business_date"] = task.input.business_date.isoformat()
            return _artifact("reconciliation", data, summary="Conciliação atualizada na réplica.", task=task)

        def anomaly(self, task):
            clock.now = 1_000.0
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
                explanation="Os dois domínios não divergem.",
            )

    parecer = answer_question(
        f"O customer {customer_id} foi processado?",
        manifest,
        Agents(),
        clock=clock,
    )["parecer"]
    assert called is False
    assert "Pagamento com sucesso na réplica." in parecer["answer"]
    assert "Os dois domínios não divergem." in parecer["answer"]
    assert "réplica" in parecer["answer"]


def test_sem_orcamento_nao_inicia_chamada(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    customer_id = manifest.customers[0].customer_id
    clock = Clock(now=1_000.0)

    class Agents:
        def payments(self, task):
            raise AssertionError("pagamentos não deve ser chamado")

        def reconciliation(self, task):
            raise AssertionError("conciliação não deve ser chamada")

        def anomaly(self, task):
            raise AssertionError("anomalia não deve ser chamada")

    parecer = answer_question(
        f"O customer {customer_id} foi processado?",
        manifest,
        Agents(),
        clock=clock,
    )["parecer"]
    assert parecer["conclusion"] == "inconclusive"
    assert parecer["payments_outcome"] == "unavailable"
    assert parecer["codes"] == []


def test_falha_pendente_liquidacao_zero_nao_diverge() -> None:
    result = run_anomaly_task(
        _anomaly(
            _artifact("payments", _payment(status="FAILED", amount_cents=80_000)),
            _artifact(
                "reconciliation",
                _reconciliation(status="PENDING", expected_amount_cents=80_000, settled_amount_cents=0),
            ),
        )
    )
    assert result.conclusion == "no_divergence"
    assert result.codes == []


def test_falha_pendente_liquidacao_positiva_igual_fica_inconclusiva() -> None:
    result = run_anomaly_task(
        _anomaly(
            _artifact("payments", _payment(status="FAILED", amount_cents=80_000)),
            _artifact(
                "reconciliation",
                _reconciliation(status="PENDING", expected_amount_cents=80_000, settled_amount_cents=80_000),
            ),
        )
    )
    assert result.conclusion == "inconclusive"
    assert result.codes == []
    assert "não divergem" not in result.explanation


def test_falha_pendente_liquidacao_positiva_diferente_diverge() -> None:
    result = run_anomaly_task(
        _anomaly(
            _artifact("payments", _payment(status="FAILED", amount_cents=80_000)),
            _artifact(
                "reconciliation",
                _reconciliation(status="PENDING", expected_amount_cents=80_000, settled_amount_cents=100),
            ),
        )
    )
    assert result.conclusion == "divergence"
    assert result.codes == ["AMOUNT_MISMATCH"]
