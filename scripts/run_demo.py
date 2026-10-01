"""Sobe a carga, as duas réplicas, os MCPs, o gateway, os agentes e faz uma pergunta."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from carga.load import load_session
from settings import VAR, carga_seed


def main() -> None:
    payments_db = VAR / "payments.sqlite"
    reconciliation_db = VAR / "reconciliation.sqlite"
    manifest_path = VAR / "session.json"
    manifest = load_session(
        payments_db,
        reconciliation_db,
        manifest_path,
        seed=carga_seed(),
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["MANIFEST_PATH"] = str(manifest_path)
    env["PAYMENTS_DB"] = str(payments_db)
    env["RECONCILIATION_DB"] = str(reconciliation_db)
    env["AUDIT_LOG_PATH"] = str(VAR / "audit.jsonl")
    python = sys.executable
    commands = [
        [python, str(ROOT / "apps/payments/main.py")],
        [python, str(ROOT / "apps/reconciliation/main.py")],
        [python, "-m", "mcp_servers.payments"],
        [python, "-m", "mcp_servers.reconciliation"],
        [python, "-m", "gateway.app"],
        [python, "-m", "agents.payments"],
        [python, "-m", "agents.reconciliation"],
        [python, "-m", "agents.anomaly"],
        [python, "-m", "orchestrator.app"],
    ]
    processes: list[subprocess.Popen] = []
    try:
        for command in commands:
            processes.append(subprocess.Popen(command, env=env, cwd=str(ROOT)))
        for port in (8101, 8102, 8201, 8202, 8300, 8401, 8402, 8403, 8500):
            _wait_port(port)
        _wait("http://127.0.0.1:8500/health")
        with httpx.Client(timeout=30) as client:
            catalog = client.post(
                "http://127.0.0.1:8500/v1/perguntar",
                json={"question": "Quais customers posso consultar?"},
            )
            catalog.raise_for_status()
            print(catalog.json()["answer"])
            customer_id = manifest.customer_for("divergence")
            question = (
                f"O customer {customer_id} foi processado com sucesso hoje? "
                "Como estão os dados? Tem alguma anomalia?"
            )
            parecer = client.post("http://127.0.0.1:8500/v1/perguntar", json={"question": question})
            parecer.raise_for_status()
            print(parecer.json()["parecer"]["answer"])
        audit = (VAR / "audit.jsonl").read_text(encoding="utf-8") if (VAR / "audit.jsonl").exists() else ""
        print(audit)
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def _wait_port(port: int) -> None:
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"a porta {port} não abriu")


def _wait(url: str) -> None:
    deadline = time.time() + 30
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=1)
            if response.status_code == 200:
                return
        except Exception as exc:
            last_error = exc
        time.sleep(0.3)
    raise RuntimeError(f"o orquestrador não subiu: {last_error}")


if __name__ == "__main__":
    main()
