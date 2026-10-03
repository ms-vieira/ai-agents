# commands

## Responsibility

Run the example from the repository root. These commands load the session, start processes, or send a question. They do not read the SQLite files themselves and they are not a domain.

## What this is

Three entry points.

| File | Role |
| --- | --- |
| `serve.py` | Calls `load_session`, starts the nine processes, and stays up until `Ctrl+C`. Logs go to `var/serve.log`. When the orchestrator answers `GET /health`, it prints `up at http://127.0.0.1:8500`. |
| `ask.py` | Reads one line at a time and posts it to `http://127.0.0.1:8500/v1/perguntar`. Prints `answer` for a catalog or a refusal, and `parecer.answer` for a customer lookup. An empty line or `Ctrl+D` exits. It does not start or stop processes. |
| `run_demo.py` | Calls `load_session`, starts the same nine processes, asks two fixed questions, prints `var/audit.jsonl`, and shuts everything down. |

`CARGA_SEED` fixes the three customer ids. Without it, each run draws new ids.

## How it works

`serve.py` and `run_demo.py` call `load_session` in `app-payments/load.py`. That draws the three customers, writes `var/payments.sqlite`, asks `app-reconciliation/load.py` to write `var/reconciliation.sqlite`, and saves `var/session.json`. They then wait until these ports accept connections:

| Port | Process |
| --- | --- |
| 8101 | `app-payments/main.py` |
| 8102 | `app-reconciliation/main.py` |
| 8201 | `mcp_servers.payments` |
| 8202 | `mcp_servers.reconciliation` |
| 8300 | `gateway.app` |
| 8401 | `agents.payments` |
| 8402 | `agents.reconciliation` |
| 8403 | `agents.anomaly` |
| 8500 | `orchestrator.app` |

Questions go only to port `8500`. A question with no `C-` plus four digits returns the catalog and does not call the agents. A customer from the load runs `payments`, `reconciliation`, and `anomaly`. A customer outside the load is refused. `Ctrl+C` in the `serve.py` terminal stops the nine processes.

`run_demo.py` asks `Quais customers posso consultar?` and then asks about the customer assigned to the `divergence` scenario. Its process logs stay on the terminal. The audit print includes lines from earlier runs, because `var/audit.jsonl` is appended.

Without `OPENAI_API_KEY`, the answer text is filled from the replica JSON. With a key, `ORCHESTRATOR_MODEL` rewrites the orchestrator prose and `SPECIALIST_MODEL` rewrites the specialist summaries. Status fields still come from the replica. Variable names are in `.env.example`.

## How to run

From the repository root, with the virtualenv from the [root README](../README.md).

Leave the stack up in one terminal:

```bash
CARGA_SEED=7 .venv/bin/python commands/serve.py
```

Ask from a second terminal after the first prints `up at http://127.0.0.1:8500`:

```bash
.venv/bin/python commands/ask.py
```

```text
question> Quais customers posso consultar?
question> O customer C-6468 foi processado com sucesso hoje? Tem alguma anomalia?
```

Closed demo, which exits by itself:

```bash
CARGA_SEED=7 .venv/bin/python commands/run_demo.py
```
