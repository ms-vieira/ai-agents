"""API de leitura da réplica. Não abre o banco transacional e não lista customers."""

import sqlite3
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException

from contracts.models import (
    CUSTOMER_ID_PATTERN,
    PaymentReadResponse,
    PaymentRecord,
    ReconciliationReadResponse,
    ReconciliationRecord,
)
import re

CUSTOMER_RE = re.compile(CUSTOMER_ID_PATTERN)


def create_read_app(db_path: Path, domain: str) -> FastAPI:
    app = FastAPI(title=f"Leitura da réplica de {domain}")
    app.state.db_path = db_path
    app.state.domain = domain

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "source": "replica", "domain": domain}

    if domain == "payments":

        @app.get("/v1/customers/{customer_id}/processing", response_model=PaymentReadResponse)
        def processing(customer_id: str, business_date: str) -> PaymentReadResponse:
            day = _parse_query(customer_id, business_date)
            row, as_of = _lookup(db_path, "processing", customer_id, day)
            payment = None
            if row is not None:
                payment = PaymentRecord(
                    payment_id=row["payment_id"],
                    status=row["status"],
                    amount_cents=row["amount_cents"],
                    currency=row["currency"],
                    error_code=row["error_code"],
                    error_detail=row["error_detail"],
                    processed_at=_parse_dt(row["processed_at"]),
                )
                as_of = _parse_dt(row["replicated_at"]) or as_of
            return PaymentReadResponse(
                as_of=as_of,
                customer_id=customer_id,
                business_date=day,
                found=row is not None,
                payment=payment,
            )

    else:

        @app.get(
            "/v1/customers/{customer_id}/reconciliation",
            response_model=ReconciliationReadResponse,
        )
        def reconciliation(customer_id: str, business_date: str) -> ReconciliationReadResponse:
            day = _parse_query(customer_id, business_date)
            row, as_of = _lookup(db_path, "reconciliation", customer_id, day)
            record = None
            if row is not None:
                record = ReconciliationRecord(
                    reconciliation_id=row["reconciliation_id"],
                    payment_id=row["payment_id"],
                    status=row["status"],
                    expected_amount_cents=row["expected_amount_cents"],
                    settled_amount_cents=row["settled_amount_cents"],
                    anomaly_code=row["anomaly_code"],
                    detail=row["detail"],
                )
                as_of = _parse_dt(row["replicated_at"]) or as_of
            return ReconciliationReadResponse(
                as_of=as_of,
                customer_id=customer_id,
                business_date=day,
                found=row is not None,
                reconciliation=record,
            )

    return app


def _parse_query(customer_id: str, business_date: str) -> date:
    if not CUSTOMER_RE.match(customer_id):
        raise HTTPException(status_code=400, detail="customer_id invalido")
    try:
        return date.fromisoformat(business_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="business_date invalida") from exc


def _lookup(db_path: Path, table: str, customer_id: str, day: date) -> tuple[sqlite3.Row | None, datetime]:
    if table not in {"processing", "reconciliation"}:
        raise HTTPException(status_code=500, detail="tabela desconhecida")
    query = {
        "processing": "SELECT * FROM processing WHERE customer_id = ? AND business_date = ?",
        "reconciliation": "SELECT * FROM reconciliation WHERE customer_id = ? AND business_date = ?",
    }[table]
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        meta = connection.execute("SELECT replicated_at FROM meta").fetchone()
        if meta is None:
            raise HTTPException(status_code=503, detail="replica sem carimbo")
        as_of = _parse_dt(meta["replicated_at"])
        if as_of is None:
            raise HTTPException(status_code=503, detail="replica sem carimbo")
        row = connection.execute(query, (customer_id, day.isoformat())).fetchone()
    return row, as_of


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)
