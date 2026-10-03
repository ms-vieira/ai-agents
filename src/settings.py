import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VAR = ROOT / "var"
BUSINESS_DATE = date.fromisoformat(os.environ.get("BUSINESS_DATE", "2026-09-30"))

# A sustentação lê a réplica. Este processo não tem connection string de banco transacional.
REPLICA_ONLY = True

PAYMENTS_API_PORT = 8101
RECONCILIATION_API_PORT = 8102
PAYMENTS_MCP_PORT = 8201
RECONCILIATION_MCP_PORT = 8202
GATEWAY_PORT = 8300
PAYMENTS_AGENT_PORT = 8401
RECONCILIATION_AGENT_PORT = 8402
ANOMALY_AGENT_PORT = 8403
ORCHESTRATOR_PORT = 8500

READ_TIMEOUT_SECONDS = 2.0
MAX_TOOL_CALLS_PER_TASK = 3
RATE_LIMIT_CAPACITY = 5
RATE_LIMIT_WINDOW_SECONDS = 60

AGENT_TOKENS = {
    os.environ.get("PAYMENTS_AGENT_TOKEN", "dev-payments"): "payments",
    os.environ.get("RECONCILIATION_AGENT_TOKEN", "dev-reconciliation"): "reconciliation",
    os.environ.get("ANOMALY_AGENT_TOKEN", "dev-anomaly"): "anomaly",
}

ALLOWLIST = {
    "payments": frozenset({"get_processing"}),
    "reconciliation": frozenset({"get_reconciliation"}),
    "anomaly": frozenset(),
}


def app_load(folder: str):
    import importlib.util

    path = ROOT / folder / "load.py"
    spec = importlib.util.spec_from_file_location(folder.replace("-", "_") + "_load", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def carga_seed() -> int | None:
    raw = os.environ.get("CARGA_SEED", "").strip()
    if raw == "":
        return None
    return int(raw)
