# app-payments

## Responsibility

Answer one question for the `payments` domain: did this customer have payment processing on a business date, and did it succeed or fail. The answer comes from the replica `var/payments.sqlite`. This folder also draws the three customers of the session and asks `app-reconciliation` to write the matching replica.

The agent `payments` and the MCP server are outside this folder. The tool name is `get_processing`. The skill id is `lookup_customer_processing`.

## What this is

A read API on port `8101` plus the session load.

| File | Role |
| --- | --- |
| `main.py` | Starts the API. Adds `src/` to the Python path, opens `PAYMENTS_DB` (default `var/payments.sqlite`), and listens on `127.0.0.1:8101`. `PORT` changes the port. |
| `load.py` | `build_manifest` draws three `C-` plus four-digit ids and assigns `aligned`, `divergence`, and `failed`. `load_session` writes this replica, calls `app-reconciliation/load.py`, and saves `var/session.json`. `write_payments` recreates the SQLite file. |
| `__init__.py` | Adds `src/` to the Python path so imports work from this folder. |

The HTTP route is `create_read_app(..., "payments")` in `src/replica/api.py`. `main.py` chooses the database, the domain, and the port.

## How it works

`load_session` draws the customers once, so both replicas share the same ids. `CARGA_SEED` fixes the draw. Each run deletes the SQLite file and writes it again. The default business date is `2026-09-30`. The replica timestamp is `2026-09-30T21:00:00Z`, stored in `meta`. The processing time on each row is `2026-09-30T14:05:00+00:00`.

| Scenario | payment_id | status | amount_cents | error |
| --- | --- | --- | --- | --- |
| `aligned` | `pay_` + the customer's digits | `SUCCESS` | `100000` | empty |
| `divergence` | `pay_` + the customer's digits | `SUCCESS` | `150000` | empty |
| `failed` | `pay_` + the customer's digits | `FAILED` | `80000` | `INSUFFICIENT_FUNDS` |

After the file exists, `GET /health` returns `source: replica` and `domain: payments`.

`GET /v1/customers/{customer_id}/processing?business_date=YYYY-MM-DD` reads one row. The body includes `source: replica`, `as_of`, `found`, and, when a row exists, `payment_id`, `status`, `amount_cents`, `currency`, `error_code`, `error_detail`, and `processed_at`.

A customer id that is not `C-` plus four digits, or an invalid date, returns `400`. A customer missing from the replica returns `200` with `found: false`. There is no list route and no write route.

In the full example the MCP server calls this API through `get_processing`. The gateway stands in front of that call. The `payments` agent does not open this database. The orchestrator and the gateway read `var/session.json` through `src/contracts/manifest.py`.

## How to run

From the repository root, once the replica file exists:

```bash
.venv/bin/python app-payments/main.py
```

To write the replica first, with the reconciliation replica and the manifest:

```bash
CARGA_SEED=7 .venv/bin/python -c "
from pathlib import Path
from settings import app_load, carga_seed
app_load('app-payments').load_session(
    Path('var/payments.sqlite'),
    Path('var/reconciliation.sqlite'),
    Path('var/session.json'),
    seed=carga_seed(),
)
"
```

`PYTHONPATH=src` is required for that one-liner. `commands/serve.py` and `commands/run_demo.py` set it and call `load_session` before starting `main.py`.

```bash
curl -s "http://127.0.0.1:8101/v1/customers/C-6468/processing?business_date=2026-09-30"
```

`C-6468` is the divergence customer only when `CARGA_SEED=7`.
