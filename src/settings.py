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
INVESTIGATION_DEADLINE_SECONDS = float(os.environ.get("INVESTIGATION_DEADLINE_SECONDS", "16"))
PARECER_RESERVE_SECONDS = float(os.environ.get("PARECER_RESERVE_SECONDS", "1"))
AGENT_CALL_TIMEOUT_SECONDS = float(os.environ.get("AGENT_CALL_TIMEOUT_SECONDS", "5"))
GATEWAY_CALL_TIMEOUT_SECONDS = float(os.environ.get("GATEWAY_CALL_TIMEOUT_SECONDS", "2"))
MODEL_TIMEOUT_SECONDS = float(os.environ.get("MODEL_TIMEOUT_SECONDS", "2"))
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


def call_window(remaining: float | None) -> float:
    """The specialist is cut off at its own HTTP deadline, even if the investigation has more time left."""
    window = AGENT_CALL_TIMEOUT_SECONDS if remaining is None else remaining
    return max(0.0, min(window, AGENT_CALL_TIMEOUT_SECONDS))


def tool_and_model_budget(remaining: float | None, includes_gateway: bool) -> tuple[float, float]:
    """Split one specialist call into a tool wait and a model wait that both fit inside it."""
    window = call_window(remaining)
    if not includes_gateway:
        return 0.0, min(MODEL_TIMEOUT_SECONDS, max(0.0, window - 0.25))
    gateway = min(GATEWAY_CALL_TIMEOUT_SECONDS, max(0.0, window - 0.5))
    model = min(MODEL_TIMEOUT_SECONDS, max(0.0, window - gateway - 0.25))
    return gateway, model


def carga_seed() -> int | None:
    raw = os.environ.get("CARGA_SEED", "").strip()
    if raw == "":
        return None
    return int(raw)
