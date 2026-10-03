# app-reconciliation

## Responsibility

Answer one question for the `reconciliation` domain: for this customer and business date, is reconciliation updated, pending, or in error. The answer comes from the replica `var/reconciliation.sqlite`.

The agent `reconciliation` and the MCP server are outside this folder. The tool name is `get_reconciliation`. The skill id is `lookup_customer_reconciliation`.

## What this is

A read API on port `8102` plus the writer for this replica.

| File | Role |
| --- | --- |
| `main.py` | Starts the API. Adds `src/` to the Python path, opens `RECONCILIATION_DB` (default `var/reconciliation.sqlite`), and listens on `127.0.0.1:8102`. `PORT` changes the port. |
| `load.py` | `write_reconciliation` deletes the previous SQLite file, creates `meta` and `reconciliation`, and inserts one row per customer in the manifest it receives. |
| `__init__.py` | Adds `src/` to the Python path so imports work from this folder. |

The HTTP route is `create_read_app(..., "reconciliation")` in `src/replica/api.py`. `main.py` chooses the database, the domain, and the port.

## How it works

This folder does not draw customer ids. `app-payments/load.py` builds one manifest and calls `write_reconciliation` with it, so both replicas share the same customers. `CARGA_SEED` fixes that draw. Each run deletes the SQLite file and writes it again. The default business date is `2026-09-30`. The replica timestamp is stored in `meta`.

| Scenario | reconciliation_id | status | expected_amount_cents | settled_amount_cents | anomaly_code |
| --- | --- | --- | --- | --- | --- |
| `aligned` | `rec_` + the customer's digits | `UPDATED` | `100000` | `100000` | empty |
| `divergence` | `rec_` + the customer's digits | `ERROR` | `150000` | `149999` | `AMOUNT_MISMATCH` |
| `failed` | `rec_` + the customer's digits | `PENDING` | `80000` | empty | empty |

The `divergence` row stores a hostile sentence with a fake token in `detail`. The gateway redacts that token before the audit log and before the model. Each row's `payment_id` repeats the payments id (`pay_` plus the same digits).

After the file exists, `GET /health` returns `source: replica` and `domain: reconciliation`.

`GET /v1/customers/{customer_id}/reconciliation?business_date=YYYY-MM-DD` reads one row. The body includes `source: replica`, `as_of`, `found`, and, when a row exists, `reconciliation_id`, `payment_id`, `status`, `expected_amount_cents`, `settled_amount_cents`, `anomaly_code`, and `detail`.

A customer id that is not `C-` plus four digits, or an invalid date, returns `400`. A customer missing from the replica returns `200` with `found: false`. There is no list route and no write route.

In the full example the MCP server calls this API through `get_reconciliation`. The gateway stands in front of that call. The `reconciliation` agent does not open this database.

## How to run

From the repository root, once the replica file exists:

```bash
.venv/bin/python app-reconciliation/main.py
```

The file is written by `load_session` in `app-payments`, which `commands/serve.py` and `commands/run_demo.py` call before starting this process.

```bash
curl -s "http://127.0.0.1:8102/v1/customers/C-6468/reconciliation?business_date=2026-09-30"
```

`C-6468` is the divergence customer only when `CARGA_SEED=7`.
