from pathlib import Path

from carga.load import load_session
from contracts.models import BUSINESS_DATE


def test_carga_gera_tres_customers_e_catalogo_sem_dominio(tmp_path: Path) -> None:
    manifest = load_session(
        tmp_path / "payments.sqlite",
        tmp_path / "reconciliation.sqlite",
        tmp_path / "session.json",
        seed=7,
    )
    assert manifest.business_date == BUSINESS_DATE
    assert len(manifest.customers) == 3
    assert {item.scenario for item in manifest.customers} == {"aligned", "divergence", "failed"}
    public = manifest.public().model_dump()
    assert len(public["customers"]) == 3
    assert set(public["customers"][0]) == {"customer_id"}
    blob = str(public)
    for hidden in ("SUCCESS", "ERROR", "amount", "detail", "scenario", "token"):
        assert hidden not in blob


def test_semente_fixa_repete_os_ids(tmp_path: Path) -> None:
    first = load_session(tmp_path / "a.sqlite", tmp_path / "b.sqlite", tmp_path / "m1.json", seed=7)
    second = load_session(tmp_path / "c.sqlite", tmp_path / "d.sqlite", tmp_path / "m2.json", seed=7)
    assert [item.customer_id for item in first.customers] == [item.customer_id for item in second.customers]
