"""Load the replicas, start the processes, and stay up until Ctrl+C."""

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

from settings import VAR, app_load, carga_seed

PORTS = (8101, 8102, 8201, 8202, 8300, 8401, 8402, 8403, 8500)


def main() -> None:
    payments_db = VAR / "payments.sqlite"
    reconciliation_db = VAR / "reconciliation.sqlite"
    manifest_path = VAR / "session.json"
    VAR.mkdir(parents=True, exist_ok=True)
    app_load("app-payments").load_session(
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
        [python, str(ROOT / "app-payments/main.py")],
        [python, str(ROOT / "app-reconciliation/main.py")],
        [python, "-m", "mcp_servers.payments"],
        [python, "-m", "mcp_servers.reconciliation"],
        [python, "-m", "gateway.app"],
        [python, "-m", "agents.payments"],
        [python, "-m", "agents.reconciliation"],
        [python, "-m", "agents.anomaly"],
        [python, "-m", "orchestrator.app"],
    ]
    log_path = VAR / "serve.log"
    processes: list[subprocess.Popen] = []
    log = log_path.open("a", encoding="utf-8")
    try:
        for command in commands:
            processes.append(
                subprocess.Popen(command, env=env, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
            )
        for port in PORTS:
            _wait_port(port)
        _wait("http://127.0.0.1:8500/health")
        print("up at http://127.0.0.1:8500", flush=True)
        print("in another terminal: .venv/bin/python commands/ask.py", flush=True)
        print("Ctrl+C stops", flush=True)
        while True:
            failed = [process for process in processes if process.poll() is not None]
            if failed:
                raise RuntimeError(f"a process exited; see {log_path}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        log.close()


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
