"""Payments replica load. Draws the three session customers and writes processing."""

import importlib.util
import random
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from types import ModuleType

from contracts.models import BUSINESS_DATE, ScenarioName, SessionCustomer, SessionManifest

PROCESSED_AT = datetime(2026, 9, 30, 14, 5, tzinfo=timezone.utc)
REPLICATED_AT = datetime(2026, 9, 30, 21, 0, tzinfo=timezone.utc)
SCENARIOS: tuple[ScenarioName, ...] = ("aligned", "divergence", "failed")


def load_session(
    payments_db: Path,
    reconciliation_db: Path,
    manifest_path: Path,
    *,
    seed: int | None = None,
    business_date: date = BUSINESS_DATE,
) -> SessionManifest:
    manifest = build_manifest(seed=seed, business_date=business_date)
    write_payments(payments_db, manifest)
    _reconciliation().write_reconciliation(reconciliation_db, manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest


def build_manifest(*, seed: int | None = None, business_date: date = BUSINESS_DATE) -> SessionManifest:
    rng = random.Random(seed if seed is not None else random.SystemRandom().randrange(2**31))
    customer_ids = _unique_ids(rng)
    scenarios = list(SCENARIOS)
    rng.shuffle(scenarios)
    customers = [
        SessionCustomer(customer_id=customer_id, scenario=scenario)
        for customer_id, scenario in zip(customer_ids, scenarios, strict=True)
    ]
    return SessionManifest(
        business_date=business_date,
        replicated_at=REPLICATED_AT,
        customers=customers,
    )


def write_payments(path: Path, manifest: SessionManifest) -> None:
    _reset(path)
    with _connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE meta (
                replicated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE processing (
                customer_id TEXT NOT NULL,
                business_date TEXT NOT NULL,
                payment_id TEXT NOT NULL,
                status TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                currency TEXT NOT NULL,
                error_code TEXT,
                error_detail TEXT,
                processed_at TEXT,
                replicated_at TEXT NOT NULL,
                PRIMARY KEY (customer_id, business_date)
            )
            """
        )
        connection.execute(
            "INSERT INTO meta (replicated_at) VALUES (?)",
            (manifest.replicated_at.isoformat(),),
        )
        for customer in manifest.customers:
            connection.execute(
                """
                INSERT INTO processing (
                    customer_id, business_date, payment_id, status, amount_cents,
                    currency, error_code, error_detail, processed_at, replicated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _payment_row(customer, manifest),
            )


def _payment_row(customer: SessionCustomer, manifest: SessionManifest) -> tuple:
    digits = customer.customer_id.removeprefix("C-")
    replicated = manifest.replicated_at.isoformat()
    day = manifest.business_date.isoformat()
    if customer.scenario == "aligned":
        return (
            customer.customer_id,
            day,
            f"pay_{digits}",
            "SUCCESS",
            100_000,
            "BRL",
            None,
            None,
            PROCESSED_AT.isoformat(),
            replicated,
        )
    if customer.scenario == "divergence":
        return (
            customer.customer_id,
            day,
            f"pay_{digits}",
            "SUCCESS",
            150_000,
            "BRL",
            None,
            None,
            PROCESSED_AT.isoformat(),
            replicated,
        )
    return (
        customer.customer_id,
        day,
        f"pay_{digits}",
        "FAILED",
        80_000,
        "BRL",
        "INSUFFICIENT_FUNDS",
        "Saldo insuficiente para o processamento.",
        PROCESSED_AT.isoformat(),
        replicated,
    )


def _unique_ids(rng: random.Random) -> list[str]:
    chosen: set[str] = set()
    while len(chosen) < 3:
        chosen.add(f"C-{rng.randint(0, 9999):04d}")
    return list(chosen)


def _reconciliation() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "app-reconciliation" / "load.py"
    spec = importlib.util.spec_from_file_location("app_reconciliation_load", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reset(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection
