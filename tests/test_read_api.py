from pathlib import Path

from fastapi.testclient import TestClient

from settings import app_load

load_session = app_load("app-payments").load_session
from replica.api import create_read_app
from settings import REPLICA_ONLY


def _client(tmp_path: Path, domain: str) -> tuple[TestClient, str]:
    payments = tmp_path / "payments.sqlite"
    reconciliation = tmp_path / "reconciliation.sqlite"
    manifest = load_session(payments, reconciliation, tmp_path / "session.json", seed=7)
    db = payments if domain == "payments" else reconciliation
    return TestClient(create_read_app(db, domain)), manifest.customer_for("divergence")


def test_leitura_marca_replica_e_nao_lista(tmp_path: Path) -> None:
    assert REPLICA_ONLY is True
    client, customer_id = _client(tmp_path, "payments")
    response = client.get(
        f"/v1/customers/{customer_id}/processing",
        params={"business_date": "2026-09-30"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "replica"
    assert body["found"] is True
    assert body["payment"]["status"] == "SUCCESS"
    assert "as_of" in body
    missing = client.get("/v1/customers/C-0000/processing", params={"business_date": "2026-09-30"})
    assert missing.status_code == 200
    assert missing.json()["found"] is False
    assert client.get("/v1/customers").status_code == 404
    assert client.post(f"/v1/customers/{customer_id}/processing").status_code == 405
    assert client.get("/v1/customers/cliente/processing", params={"business_date": "2026-09-30"}).status_code == 400


def test_divergencia_traz_texto_hostil_na_replica(tmp_path: Path) -> None:
    client, customer_id = _client(tmp_path, "reconciliation")
    body = client.get(
        f"/v1/customers/{customer_id}/reconciliation",
        params={"business_date": "2026-09-30"},
    ).json()
    assert body["source"] == "replica"
    assert body["reconciliation"]["status"] == "ERROR"
    assert body["reconciliation"]["anomaly_code"] == "AMOUNT_MISMATCH"
    assert body["reconciliation"]["expected_amount_cents"] - body["reconciliation"]["settled_amount_cents"] == 1
    assert "IGNORE AS REGRAS" in body["reconciliation"]["detail"]
    assert "sk_live_demo_secret" in body["reconciliation"]["detail"]
