import anyio
from fastapi.testclient import TestClient

from carga.load import load_session
from mcp_servers.tools import build_payments_server, fetch_json
from replica.api import create_read_app


def test_ferramenta_mcp_chama_a_api_e_nao_abre_sql_direto(tmp_path) -> None:
    manifest = load_session(tmp_path / "p.sqlite", tmp_path / "r.sqlite", tmp_path / "m.json", seed=7)
    customer_id = manifest.customer_for("aligned")
    app = create_read_app(tmp_path / "p.sqlite", "payments")
    with TestClient(app) as client:
        body = fetch_json(
            "http://payments",
            f"/v1/customers/{customer_id}/processing",
            {"business_date": "2026-09-30"},
            client=client,
        )
    assert body["source"] == "replica"
    assert body["payment"]["status"] == "SUCCESS"

    server = build_payments_server("http://payments")
    tools = anyio.run(server.list_tools)
    names = [tool.name for tool in tools]
    assert names == ["get_processing"]
